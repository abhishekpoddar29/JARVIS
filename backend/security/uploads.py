from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from config import get_settings


_SAFE_DISPLAY_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True)
class StoredUpload:
    upload_id: str
    original_filename: str
    stored_filename: str
    path: Path
    size_bytes: int
    content_type: str


def _display_name(filename: str | None) -> str:
    value = Path(filename or "upload").name.strip()
    value = _SAFE_DISPLAY_NAME.sub("_", value).strip(" .")
    return value[:160] or "upload"


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


async def store_upload(file: UploadFile, upload_dir: Path) -> StoredUpload:
    settings = get_settings()
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_filename = _display_name(file.filename)
    extension = _extension(original_filename)
    allowed = settings.upload_extension_set
    if extension not in allowed:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported upload type: {extension or '(none)'}.",
        )

    upload_id = uuid4().hex
    stored_filename = f"{upload_id}{extension}"
    destination = (upload_dir / stored_filename).resolve()
    if upload_dir.resolve() not in destination.parents:
        raise HTTPException(status_code=400, detail="Invalid upload path.")

    total = 0
    max_bytes = settings.upload_max_bytes
    try:
        with destination.open("wb") as buffer:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    buffer.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds the {max_bytes}-byte limit.",
                    )
                buffer.write(chunk)
    finally:
        await file.close()

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return StoredUpload(
        upload_id=upload_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        path=destination,
        size_bytes=total,
        content_type=(file.content_type or "application/octet-stream").split(";", 1)[0],
    )


def artifact_download_headers(filename: str) -> dict[str, str]:
    safe_name = _display_name(filename)
    return {
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox",
    }
