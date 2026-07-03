from typing import TypedDict, Annotated, List, Optional
import operator
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from .schemas import EngineerDiffOutput


class RepoPilotState(TypedDict):
    # Inputs
    file_path: str
    original_code: str

    # LLM Generated Context
    architect_plan: str
    current_diff: Optional[EngineerDiffOutput]
    diff_history: Annotated[List[EngineerDiffOutput], operator.add]

    # Sandbox Execution (Phase 3)
    sandbox_logs: str
    errors: Annotated[List[str], operator.add]
    loop_count: int

    # Core LangGraph message history
    messages: Annotated[list[BaseMessage], add_messages]

    # Graph Control
    status: str  # "planning", "engineering", "qa_failed", "compiling", "success", "failed_wip"
