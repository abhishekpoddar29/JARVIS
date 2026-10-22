from __future__ import annotations

from langgraph.graph import END

from .engineering_state import EngineeringState


def after_pm(s: EngineeringState):
    if s["control"]["status"] in {
        "FAILED",
        "BUDGET_EXCEEDED",
        "CANCELLED",
    }:
        return END

    return (
        "requirement_architect"
        if s["pm_report"]
        else END
    )


def after_spec(s: EngineeringState):
    return (
        "roadmap"
        if (
            s["engineering_spec"]
            and s["selected_stack"] != "UNKNOWN"
            and s["control"]["status"] == "COMPLETED"
        )
        else END
    )


def after_roadmap(s: EngineeringState):
    return (
        "file_manifest"
        if (
            s["roadmap"]
            and s["control"]["status"] == "COMPLETED"
        )
        else END
    )


def after_manifest(s: EngineeringState):
    return (
        ["frontend_engineer", "backend_engineer"]
        if s["manifest"]["status"] == "COMPLETED"
        else END
    )


def after_test(s: EngineeringState):
    return (
        "sandbox_test"
        if s["test_work"]["status"] == "COMPLETED"
        else "review_and_repair"
    )


def after_sandbox(s: EngineeringState):
    return (
        "package_and_save"
        if s["sandbox_result"]["status"] == "COMPLETED"
        else "review_and_repair"
    )


def after_review(s: EngineeringState):
    return {
        "FRONTEND": "frontend_repair",
        "BACKEND": "backend_repair",
        "TEST": "test_repair",
        "MANIFEST": "file_manifest",
        "ROADMAP": "roadmap",
        "MULTIPLE": "multi_repair",
    }.get(s["review_result"]["target_owner"], END)


def after_owner_repair(s: EngineeringState):
    return (
        "test_repair"
        if s["review_result"]["rerun_tests"]
        else "sandbox_test"
    )