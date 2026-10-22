from Engineering_Team.engineering_state import (
    build_initial_engineering_state,
)
from .manifest_utils import (
    normalize_manifest_file,
    validate_manifest,
)


def test_initial_state_is_small_and_complete() -> None:
    state = build_initial_engineering_state("Build a task manager")

    assert state["request"]["raw_prompt"] == "Build a task manager"
    assert state["control"]["repair_attempts"] == 0
    assert state["manifest"]["files"] == []
    assert state["frontend_work"]["files"] == []


def test_manifest_cycle_is_rejected() -> None:
    first = normalize_manifest_file({
        "path": "index.html",
        "owner": "FRONTEND",
        "purpose": "page",
        "depends_on": ["script.js"],
        "required": True,
    })
    second = normalize_manifest_file({
        "path": "script.js",
        "owner": "FRONTEND",
        "purpose": "behavior",
        "depends_on": ["index.html"],
        "required": True,
    })

    assert first is not None
    assert second is not None

    errors = validate_manifest(
        [first, second],
        "STATIC_HTML_CSS_JS",
    )

    assert "Manifest dependency graph contains a cycle." in errors


def test_generated_assets_are_rejected() -> None:
    assert normalize_manifest_file({
        "path": "assets/hero.svg",
        "owner": "FRONTEND",
        "purpose": "hero image",
        "depends_on": [],
        "required": True,
    }) is None