from langgraph.graph import StateGraph, END
from .state import RepoPilotState
from .nodes import architect_node, engineer_node, qa_node, sandbox_node, debugger_node


def route_after_qa(state: RepoPilotState) -> str:
    if state["status"] == "qa_failed":
        return "engineer"
    return "sandbox"


def route_after_sandbox(state: RepoPilotState) -> str:
    if state["status"] in ("success", "failed_wip"):
        return END
    if state["loop_count"] >= 4:
        return END
    return "debugger"


def build_graph():
    workflow = StateGraph(RepoPilotState)

    # Nodes
    workflow.add_node("architect", architect_node)
    workflow.add_node("engineer", engineer_node)
    workflow.add_node("qa_validator", qa_node)
    workflow.add_node("sandbox", sandbox_node)
    workflow.add_node("debugger", debugger_node)

    # Entry Point
    workflow.set_entry_point("architect")

    # Standard Edges
    workflow.add_edge("architect", "engineer")
    workflow.add_edge("engineer", "qa_validator")

    # onditional Edges

    workflow.add_conditional_edges(
        "qa_validator",
        route_after_qa,
        {
            "engineer": "engineer",
            "sandbox": "sandbox",
        },
    )

    # Failsafe routing after Sandbox execution

    workflow.add_conditional_edges(
        "sandbox",
        route_after_sandbox,
        {
            END: END,
            "debugger": "debugger",
        },
    )

    workflow.add_edge("debugger", "engineer")

    return workflow.compile()
