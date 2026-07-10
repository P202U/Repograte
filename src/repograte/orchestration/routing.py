from ..config import settings
from .state import RepoPilotState


def route_after_qa(state: RepoPilotState) -> str:
    """Advances to sandbox on success. On failure, loops back to the engineer
    unless the correction-loop cap has already been hit (qa_node sets
    status="failed_wip" once state["loop_count"] >= settings.max_correction_loops),
    in which case it falls through to human review instead.
    """
    status = state.get("status")
    if status == "failed_wip":
        return "human_review"
    if status == "qa_failed":
        return "engineer"
    return "sandbox"


def route_after_sandbox(state: RepoPilotState) -> str:
    """
    Failsafe routing after Sandbox execution. Both successful runs and
    exhausted loops ('failed_wip') are forwarded to human review.
    """
    if state.get("status") in ("success", "failed_wip"):
        return "human_review"
    if state.get("loop_count", 0) >= settings.max_correction_loops:
        return "human_review"  # Defensive fallback
    return "debugger"


def route_after_human_review(state: RepoPilotState) -> str:
    """Routes to PR publication if approved, or loops back to engineering with feedback."""
    if state.get("human_approved"):
        return "publish_pr"
    return "engineer"
