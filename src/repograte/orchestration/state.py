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
    # Which MigrationSpec (prompts + scope + sandbox cmds) this run uses.
    # Stored as a plain dict (MigrationSpec.model_dump()) rather than the
    # pydantic model itself, so it round-trips through the checkpointer
    # without needing its own msgpack-serde allowlist entry - nodes.py
    # reconstructs a MigrationSpec from it via .model_validate() when needed.
    migration_spec: dict
    # Already-approved patches from earlier files in a codebase-wide run
    # (file_path -> new content). Empty for a single-file run. sandbox_node
    # and gather_repo_context apply this on top of a fresh clone before
    # doing anything else, so file N's verification/context reflects the
    # cumulative state of the migration so far, not the pristine original.
    repo_overlay: NotRequired[dict[str, str]]
    # Optional per-run overrides of the configured sandbox commands
    # (falls back to migration_spec's, then settings.sandbox_install_cmd /
    # settings.sandbox_test_cmd, when absent).
    install_cmd: NotRequired[Optional[str]]
    test_cmd: NotRequired[Optional[str]]

    # LLM Generated Context
    architect_plan: NotRequired[str]
    current_diff: NotRequired[Optional[EngineerDiffOutput]]
    diff_history: Annotated[List[EngineerDiffOutput], operator.add]

    # Sandbox Execution
    sandbox_logs: NotRequired[str]
    errors: Annotated[List[str], operator.add]

    # Core LangGraph message history. Not read back into any prompt today; kept as
    # a debug/observability trail (e.g. for LangSmith tracing) of what the Architect
    # and Debugger actually said.
    messages: Annotated[list[BaseMessage], add_messages]

    # Graph Control
    status: NotRequired[str]
    # Set by human_review_node; read by route_after_human_review. Declared here so the
    # state schema actually matches what the graph reads/writes (previously undeclared).
    human_approved: NotRequired[bool]
    # Set by publish_pr_node once a PR is opened (single-file run).
    pr_url: NotRequired[str]
    # Input: when True, publish_pr_node computes the final patched code but
    # does NOT open a PR - the codebase-wide driver (orchestration/codebase.py)
    # sets this so it can batch every approved file into one PR at the end
    # instead of one per file.
    defer_publish: NotRequired[bool]
    # Set by publish_pr_node when defer_publish is True; read by the
    # codebase-wide driver as this file's final result.
    patched_code: NotRequired[str]
