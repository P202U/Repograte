from repograte.config import settings
from repograte.orchestration.routing import (
    route_after_human_review,
    route_after_qa,
    route_after_sandbox,
)


def test_route_after_qa_success_goes_to_sandbox():
    assert route_after_qa({"status": "compiling"}) == "sandbox"


def test_route_after_qa_failure_loops_to_engineer():
    assert route_after_qa({"status": "qa_failed"}) == "engineer"


def test_route_after_qa_cap_hit_goes_to_human_review():
    """Regression test: previously there was no cap on qa_failed retries at
    all, so this state (which qa_node now produces once the loop cap is
    reached) would have had no route defined for it."""
    assert route_after_qa({"status": "failed_wip"}) == "human_review"


def test_route_after_sandbox_success_and_failed_wip_go_to_human_review():
    assert route_after_sandbox({"status": "success", "loop_count": 1}) == "human_review"
    assert route_after_sandbox({"status": "failed_wip", "loop_count": 4}) == "human_review"


def test_route_after_sandbox_failure_under_cap_goes_to_debugger():
    assert route_after_sandbox({"status": "sandbox_failed", "loop_count": 1}) == "debugger"


def test_route_after_sandbox_defensive_fallback_at_cap():
    over_cap = settings.max_correction_loops
    assert (
        route_after_sandbox({"status": "sandbox_failed", "loop_count": over_cap})
        == "human_review"
    )


def test_route_after_human_review_approved_publishes():
    assert route_after_human_review({"human_approved": True}) == "publish_pr"


def test_route_after_human_review_rejected_loops_back():
    assert route_after_human_review({"human_approved": False}) == "engineer"
    assert route_after_human_review({}) == "engineer"
