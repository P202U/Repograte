from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from .state import RepoPilotState
from .nodes import (
    architect_node,
    engineer_node,
    qa_node,
    sandbox_node,
    debugger_node,
    human_review_node,
    publish_pr_node,
)

# Conditional Routing Functions


def route_after_qa(state: RepoPilotState) -> str:
    """Routes back to the engineer on failure, or advances to sandbox validation."""
    if state.get("status") == "qa_failed":
        return "engineer"
    return "sandbox"


def route_after_sandbox(state: RepoPilotState) -> str:
    """
    Failsafe routing after Sandbox execution. Both successful runs and
    exhausted loops ('failed_wip') are forwarded to human review.
    """
    if state.get("status") in ("success", "failed_wip"):
        return "human_review"
    if state.get("loop_count", 0) >= 4:
        return "human_review"  # Defensive fallback
    return "debugger"


def route_after_human_review(state: RepoPilotState) -> str:
    """Routes to PR publication if approved, or loops back to engineering with feedback."""
    if state.get("human_approved"):
        return "publish_pr"
    return "engineer"


# Graph Construction


def build_graph(checkpointer=None):
    workflow = StateGraph(RepoPilotState)

    # Add Nodes
    workflow.add_node("architect", architect_node)
    workflow.add_node("engineer", engineer_node)
    workflow.add_node("qa_validator", qa_node)
    workflow.add_node("sandbox", sandbox_node)
    workflow.add_node("debugger", debugger_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("publish_pr", publish_pr_node)

    # Define the Entry Point
    workflow.set_entry_point("architect")

    # Define Standard Edges
    workflow.add_edge("architect", "engineer")
    workflow.add_edge("engineer", "qa_validator")
    workflow.add_edge("debugger", "engineer")
    workflow.add_edge("publish_pr", END)

    # Define Conditional Edges
    workflow.add_conditional_edges(
        "qa_validator",
        route_after_qa,
        {
            "engineer": "engineer",
            "sandbox": "sandbox",
        },
    )

    workflow.add_conditional_edges(
        "sandbox",
        route_after_sandbox,
        {
            "human_review": "human_review",
            "debugger": "debugger",
        },
    )

    workflow.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "publish_pr": "publish_pr",
            "engineer": "engineer",
        },
    )

    # Checkpointer & State Persistence Config
    if checkpointer is None:
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("repo_pilot.orchestration.schemas", "EngineerDiffOutput")
            ]
        )
        checkpointer = MemorySaver(serde=serde)

    return workflow.compile(checkpointer=checkpointer)
