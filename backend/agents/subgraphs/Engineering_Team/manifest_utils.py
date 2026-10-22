from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .engineering_state import ManifestFile, Owner, Stack


FILE_LIMITS: dict[Stack, int] = {
    "STATIC_HTML_CSS_JS": 6,
    "REACT_VITE": 12,
    "FASTAPI_STATIC": 14,
    "FASTAPI_REACT": 20,
    "UNKNOWN": 0,
}

DISALLOWED_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".ico", ".avif", ".woff", ".woff2",
    ".ttf", ".otf",
}

VALID_OWNERS: set[str] = {"FRONTEND", "BACKEND", "TEST"}


def validate_safe_path(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    candidate = PurePosixPath(value)

    if not value or value.startswith("/") or "\x00" in value:
        raise ValueError(f"Unsafe file path: {path!r}")

    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Unsafe file path: {path!r}")

    if candidate.suffix.lower() in DISALLOWED_SUFFIXES:
        raise ValueError(f"Generated asset type is not allowed: {value}")

    return candidate.as_posix()


def normalize_manifest_file(raw: Any) -> ManifestFile | None:
    if not isinstance(raw, dict):
        return None

    try:
        path = validate_safe_path(raw.get("path", ""))
    except ValueError:
        return None

    owner = str(raw.get("owner", "")).upper().strip()
    if owner not in VALID_OWNERS:
        return None

    depends_on: list[str] = []
    for dependency in raw.get("depends_on", []) or []:
        try:
            normalized = validate_safe_path(dependency)
        except ValueError:
            continue
        if normalized not in depends_on:
            depends_on.append(normalized)

    return {
        "path": path,
        "owner": owner,  # type: ignore[typeddict-item]
        "purpose": str(raw.get("purpose", "")).strip(),
        "depends_on": depends_on,
        "required": bool(raw.get("required", True)),
    }


def validate_manifest(
    files: list[ManifestFile],
    selected_stack: SelectedStack,
) -> list[str]:
    errors: list[str] = []
    limit = FILE_LIMITS[selected_stack]

    if not files:
        return ["The manifest does not contain any files."]

    if len(files) > limit:
        errors.append(
            f"Manifest contains {len(files)} files; limit for "
            f"{selected_stack} is {limit}."
        )

    paths = [item["path"] for item in files]
    unique_paths = set(paths)

    if len(paths) != len(unique_paths):
        errors.append("Manifest contains duplicate file paths.")

    for item in files:
        for dependency in item["depends_on"]:
            if dependency == item["path"]:
                errors.append(f"{item['path']} depends on itself.")
            elif dependency not in unique_paths:
                errors.append(
                    f"{item['path']} depends on missing file {dependency}."
                )

    indegree = {path: 0 for path in unique_paths}
    edges: dict[str, list[str]] = {path: [] for path in unique_paths}

    for item in files:
        for dependency in item["depends_on"]:
            if dependency in unique_paths and dependency != item["path"]:
                edges[dependency].append(item["path"])
                indegree[item["path"]] += 1

    queue = [path for path, degree in indegree.items() if degree == 0]
    visited = 0

    while queue:
        current = queue.pop(0)
        visited += 1
        for dependent in edges[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if visited != len(unique_paths):
        errors.append("Manifest dependency graph contains a cycle.")

    return list(dict.fromkeys(errors))


def files_for_owner(
    files: list[ManifestFile],
    owner: Owner,
) -> list[ManifestFile]:
    return [item for item in files if item["owner"] == owner]


def validate_file_bundle(
    generated: list[dict[str, str]],
    assigned: list[ManifestFile],
) -> tuple[list[dict[str, str]], list[str]]:
    assigned_paths = {item["path"] for item in assigned}
    normalized: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()

    for raw in generated:
        if not isinstance(raw, dict):
            continue
        try:
            path = validate_safe_path(raw.get("path", ""))
        except ValueError as exc:
            errors.append(str(exc))
            continue

        content = str(raw.get("content", ""))
        if path not in assigned_paths:
            errors.append(f"Generated unassigned file: {path}")
            continue
        if path in seen:
            errors.append(f"Generated duplicate file: {path}")
            continue
        if not content.strip():
            errors.append(f"Generated empty file: {path}")
            continue

        seen.add(path)
        normalized.append({"path": path, "content": content})

    missing = sorted(assigned_paths - seen)
    if missing:
        errors.append("Missing assigned files: " + ", ".join(missing))

    return normalized, errors