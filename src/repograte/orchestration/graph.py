from langgraph.graph import StateGraph, END
from .state import RepoPilotState
from .nodes import architect_node, engineer_node, qa_node


def build_graph():
    workflow = StateGraph(RepoPilotState)

    workflow.add_node("architect", architect_node)
    workflow.add_node("engineer", engineer_node)
    workflow.add_node("qa_validator", qa_node)

    # Stub the Sandbox and Debugger nodes for Phase 3 integration
    workflow.add_node("sandbox", lambda state: state)  # Stub
    workflow.add_node("debugger", lambda state: state)  # Stub

    # Entry Point
    workflow.set_entry_point("architect")

    workflow.add_edge("architect", "engineer")
    workflow.add_edge("engineer", "qa_validator")

    # Conditional Edges
    def route_after_qa(state: RepoPilotState) -> str:
        if state["status"] == "qa_failed":
            return "engineer"
        return "sandbox"

    workflow.add_conditional_edges(
        "qa_validator", route_after_qa, {"engineer": "engineer", "sandbox": "sandbox"}
    )

    # Failsafe routing after Sandbox compilation
    def route_after_sandbox(state: RepoPilotState) -> str:
        if state["status"] == "success":
            return END
        elif state["loop_count"] >= 4:
            return END
        return "debugger"

    workflow.add_conditional_edges(
        "sandbox", route_after_sandbox, {END: END, "debugger": "debugger"}
    )

    workflow.add_edge("debugger", "engineer")

    return workflow.compile()
