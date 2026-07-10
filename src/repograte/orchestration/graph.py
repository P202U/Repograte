import sqlite3
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ..config import settings
from .state import RepoPilotState
from .routing import route_after_qa, route_after_sandbox, route_after_human_review
from .nodes import (
    architect_node,
    engineer_node,
    qa_node,
    sandbox_node,
    debugger_node,
    human_review_node,
    publish_pr_node,
)

_ALLOWED_MSGPACK_MODULES = [("repograte.orchestration.schemas", "EngineerDiffOutput")]


def _build_default_checkpointer():
    """
    settings.checkpoint_path set (the default) -> SQLite-backed, so a run
    interrupted at human_review - or killed mid-sandbox-run - can actually be
    resumed by re-running the same command later, since the thread_id
    (f"{repo_url}:{file_path}", set in run_cli.py) is looked up against a
    file on disk rather than a dict that only exists inside one process.
    """
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)

    if not settings.checkpoint_path:
        return MemorySaver(serde=serde)

    path = Path(settings.checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn, serde=serde)


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
            "human_review": "human_review",
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

    if checkpointer is None:
        checkpointer = _build_default_checkpointer()

    return workflow.compile(checkpointer=checkpointer)
