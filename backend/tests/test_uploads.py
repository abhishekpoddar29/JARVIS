from __future__ import annotations

import io

import pytest
from fastapi import HTTPException

from config import get_settings
from security.uploads import artifact_download_headers, store_upload


class FakeUploadFile:
    def __init__(
        self,
        filename: str,
        content: bytes,
        content_type: str = "text/csv",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._stream = io.BytesIO(content)
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch):
    monkeypatch.setenv("UPLOAD_ALLOWED_EXTENSIONS", ".csv,.json")
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "16")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_store_upload_renames_and_sanitizes_file(tmp_path):
    upload = FakeUploadFile(
        filename="../../bad:name.csv",
        content=b"a,b\n1,2\n",
    )

    stored = await store_upload(upload, tmp_path)

    assert upload.closed is True
    assert stored.path.is_file()
    assert stored.path.parent == tmp_path.resolve()
    assert stored.original_filename == "bad_name.csv"
    assert stored.stored_filename.endswith(".csv")
    assert stored.size_bytes == 8
    assert stored.path.read_bytes() == b"a,b\n1,2\n"


@pytest.mark.asyncio
async def test_store_upload_rejects_disallowed_extension(tmp_path):
    upload = FakeUploadFile(
        filename="payload.exe",
        content=b"not allowed",
    )

    with pytest.raises(HTTPException) as exc_info:
        await store_upload(upload, tmp_path)

    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_store_upload_rejects_oversized_file(tmp_path):
    upload = FakeUploadFile(
        filename="large.csv",
        content=b"x" * 17,
    )

    with pytest.raises(HTTPException) as exc_info:
        await store_upload(upload, tmp_path)

    assert exc_info.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_artifact_download_headers_force_safe_download():
    headers = artifact_download_headers("../../report.html")

    assert headers["Content-Disposition"] == 'attachment; filename="report.html"'
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Security-Policy"] == "sandbox"
