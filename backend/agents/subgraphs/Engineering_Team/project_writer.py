from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .engineering_state import GeneratedFile, PackageResult
from .manifest_utils import validate_safe_path


SAFE_ROOT = Path("engineering_team_work/package")
MAX_TOTAL_BYTES = 10_000_000


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned[:80] or "generated-project"


def _unique_path(base: Path) -> Path:
    if not base.exists():
        return base

    counter = 2
    while True:
        candidate = base.with_name(f"{base.name}-{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def write_project(
    *,
    project_name: str,
    files: list[GeneratedFile],
    engineering_spec: str,
    roadmap: str,
    sandbox_summary: str,
) -> PackageResult:
    try:
        SAFE_ROOT.mkdir(parents=True, exist_ok=True)

        normalized: list[GeneratedFile] = []
        seen: set[str] = set()
        total_bytes = 0

        for item in files:
            path = validate_safe_path(item["path"])
            if path in seen:
                raise ValueError(f"Duplicate output path: {path}")

            content = str(item["content"])
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError("Generated project exceeds size limit.")

            seen.add(path)
            normalized.append({"path": path, "content": content})

        destination = _unique_path(SAFE_ROOT / _slug(project_name))
        staging = destination.with_name(destination.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        checksums: dict[str, str] = {}
        for item in normalized:
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
            checksums[item["path"]] = hashlib.sha256(
                item["content"].encode("utf-8")
            ).hexdigest()

        metadata = staging / ".engineering"
        metadata.mkdir()
        (metadata / "ENGINEERING_SPEC.md").write_text(
            engineering_spec,
            encoding="utf-8",
        )
        (metadata / "ROADMAP.md").write_text(
            roadmap,
            encoding="utf-8",
        )
        (metadata / "TEST_SUMMARY.md").write_text(
            sandbox_summary,
            encoding="utf-8",
        )
        (metadata / "manifest.json").write_text(
            json.dumps(checksums, indent=2),
            encoding="utf-8",
        )

        readme = (
            "# Generated Project\n\n"
            "This project was generated and sandbox-tested by "
            "Engineering Team v2.\n\n"
            "See `.engineering/ENGINEERING_SPEC.md`, "
            "`.engineering/ROADMAP.md`, and "
            "`.engineering/TEST_SUMMARY.md` for details.\n"
        )
        if not (staging / "README.md").exists():
            (staging / "README.md").write_text(
                readme,
                encoding="utf-8",
            )

        staging.replace(destination)
        archive = shutil.make_archive(
            str(destination),
            "zip",
            root_dir=destination,
        )

        written = sorted(
            str(path.relative_to(destination)).replace("\\", "/")
            for path in destination.rglob("*")
            if path.is_file()
        )

        return {
            "status": "COMPLETED",
            "project_directory": str(destination.resolve()),
            "archive_path": str(Path(archive).resolve()),
            "written_files": written,
            "error": "",
        }

    except Exception as exc:
        return {
            "status": "FAILED",
            "project_directory": "",
            "archive_path": "",
            "written_files": [],
            "error": f"{type(exc).__name__}: {exc}",
        }