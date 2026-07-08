from typing import (
    TypedDict,
    Annotated,
    List,
    Optional,
    NotRequired,
)
import operator
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from .schemas import EngineerDiffOutput


class RepoPilotState(TypedDict):
    # Inputs
    file_path: str
    original_code: str
    repo_url: str
    branch: Optional[str]
    loop_count: int

    # LLM Generated Context
    architect_plan: NotRequired[str]
    current_diff: NotRequired[Optional[EngineerDiffOutput]]
    diff_history: Annotated[List[EngineerDiffOutput], operator.add]

    # Sandbox Execution
    sandbox_logs: NotRequired[str]
    errors: Annotated[List[str], operator.add]

    # Core LangGraph message history
    messages: Annotated[list[BaseMessage], add_messages]

    # Graph Control
    status: NotRequired[str]
