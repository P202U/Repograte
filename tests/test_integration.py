"""End-to-end test of the compiled graph with every external service (Claude,
DeepSeek, E2B, GitHub) replaced by a fake, plus a regression test for the
cross-process resume behavior that run_cli.py depends on.
"""

import sqlite3

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from repograte.orchestration import nodes
from repograte.orchestration.graph import build_graph
from repograte.orchestration.migration_spec import load_migration_spec
from repograte.orchestration.schemas import (
    EngineerDiffOutput,
    QAValidationResult,
    SearchReplaceBlock,
)
from repograte.sandbox.e2b_runner import SandboxRunResult

SPEC = load_migration_spec("react-class-to-hooks").model_dump()

ORIGINAL_CODE = """class Greeting extends React.Component {
  render() {
    return <div>Hello</div>;
  }
}
"""
SEARCH_BLOCK = "    return <div>Hello</div>;"
REPLACE_BLOCK = "    return <div>Hello, hooks</div>;"


class _FakeInvoker:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class FakeChatModel:
    """Stands in for both the Claude and DeepSeek clients."""

    def invoke(self, messages):
        return AIMessage(content="Plan: migrate to hooks.")

    def with_structured_output(self, schema, method=None):
        if schema is EngineerDiffOutput:
            return _FakeInvoker(
                EngineerDiffOutput(
                    reasoning="Converted class component to a function component.",
                    blocks=[
                        SearchReplaceBlock(
                            search_block=SEARCH_BLOCK, replace_block=REPLACE_BLOCK
                        )
                    ],
                )
            )
        if schema is QAValidationResult:
            return _FakeInvoker(QAValidationResult(is_valid=True, feedback=""))
        raise AssertionError(f"Unexpected structured-output schema: {schema}")


@pytest.fixture(autouse=True)
def _patch_external_services(monkeypatch):
    """Every node function still runs for real; only the network/API edges
    (LLM calls, the E2B sandbox, repo-context retrieval, and GitHub) are
    replaced so the test is hermetic and fast."""
    monkeypatch.setattr(nodes, "_get_claude_model", lambda: FakeChatModel())
    monkeypatch.setattr(nodes, "_get_deepseek_model", lambda: FakeChatModel())
    monkeypatch.setattr(nodes, "gather_repo_context", lambda **kwargs: "")
    monkeypatch.setattr(
        nodes.E2BRunner,
        "run_verification",
        lambda self, **kwargs: SandboxRunResult(
            success=True, stdout="tsc: no errors", stderr="", exit_code=0, step=""
        ),
    )
    monkeypatch.setattr(
        nodes,
        "create_pull_request",
        lambda **kwargs: "https://github.com/example/example/pull/1",
    )


def _initial_state(**overrides):
    state = {
        "file_path": "src/Greeting.tsx",
        "original_code": ORIGINAL_CODE,
        "repo_url": "https://github.com/example/example.git",
        "branch": "main",
        "loop_count": 0,
        "errors": [],
        "diff_history": [],
        "messages": [],
        "migration_spec": SPEC,
    }
    state.update(overrides)
    return state


def test_full_run_approved_opens_pr():
    """architect -> engineer -> qa -> sandbox -> human_review -> publish_pr,
    with the human approving on the first pass."""
    graph = build_graph(checkpointer=MemorySaver())  # isolated from disk
    config = {"configurable": {"thread_id": "test-thread-1"}}

    result = graph.invoke(_initial_state(), config=config)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["status"] == "success"
    assert payload["architect_plan"]  # now actually surfaced to the reviewer

    final = graph.invoke(Command(resume={"approved": True}), config=config)
    assert final["status"] == "pr_opened"
    assert final["pr_url"] == "https://github.com/example/example/pull/1"


def test_resume_survives_process_restart(tmp_path):
    """Regression test for the cross-process resume bug: a plain
    graph.invoke() with the original state on a thread that already has a
    pending interrupt must NOT silently restart the run from scratch."""
    db_path = tmp_path / "checkpoints.sqlite"
    thread_id = "test-thread-2"
    config = {"configurable": {"thread_id": thread_id}}

    # "Process 1": run until the human_review interrupt, then drop everything
    # (simulating the CLI process being killed).
    conn1 = sqlite3.connect(str(db_path), check_same_thread=False)
    graph1 = build_graph(checkpointer=SqliteSaver(conn1))
    result1 = graph1.invoke(_initial_state(), config=config)
    assert "__interrupt__" in result1
    conn1.close()
    del graph1, conn1

    # "Process 2": fresh graph/checkpointer pointed at the same file, exactly
    # like a brand-new `python run_cli.py` invocation.
    conn2 = sqlite3.connect(str(db_path), check_same_thread=False)
    graph2 = build_graph(checkpointer=SqliteSaver(conn2))
    snapshot = graph2.get_state(config)

    # This is the check run_cli.py performs before deciding whether to start
    # a fresh run or resume.
    assert snapshot.next == ("human_review",)
    assert snapshot.tasks[0].interrupts

    final = graph2.invoke(Command(resume={"approved": True}), config=config)
    assert final["status"] == "pr_opened"
    assert final["pr_url"] == "https://github.com/example/example/pull/1"


def test_human_rejection_loops_back_to_engineer_with_feedback():
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-thread-3"}}

    result = graph.invoke(_initial_state(), config=config)
    assert "__interrupt__" in result

    result = graph.invoke(
        Command(resume={"approved": False, "feedback": "Use useCallback too."}),
        config=config,
    )
    # Rejection routes back through engineer -> qa -> sandbox -> human_review again.
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["status"] == "success"


def test_same_graph_runs_a_completely_different_migration_spec():
    """Proves the prompts/scope/sandbox-cmds are genuinely configurable, not
    just theoretically pluggable: the exact same compiled graph, unmodified,
    runs the python2-to-3 spec end to end - a different language, different
    prompts, and a different sandbox command template ({files})."""
    from repograte.orchestration.migration_spec import load_migration_spec

    python_original = 'class Worker(object):\n    def run(self):\n        print "hello"\n'
    python_spec = load_migration_spec("python2-to-3")

    class PythonFakeChatModel(FakeChatModel):
        def with_structured_output(self, schema, method=None):
            if schema is EngineerDiffOutput:
                return _FakeInvoker(
                    EngineerDiffOutput(
                        reasoning="Converted print statement to a function call.",
                        blocks=[
                            SearchReplaceBlock(
                                search_block='print "hello"',
                                replace_block='print("hello")',
                            )
                        ],
                    )
                )
            return super().with_structured_output(schema, method)

    captured_test_cmd = {}

    def _fake_run_verification(self, **kwargs):
        captured_test_cmd["value"] = kwargs.get("test_cmd")
        return SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step="")

    import unittest.mock as mock

    with mock.patch.object(nodes, "_get_claude_model", lambda: PythonFakeChatModel()), \
         mock.patch.object(nodes, "_get_deepseek_model", lambda: PythonFakeChatModel()), \
         mock.patch.object(nodes.E2BRunner, "run_verification", _fake_run_verification):
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-thread-python"}}
        state = _initial_state(
            file_path="legacy/worker.py",
            original_code=python_original,
            migration_spec=python_spec.model_dump(),
        )
        result = graph.invoke(state, config=config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["status"] == "success"
    assert "print" in payload["reasoning"].lower() or "hello" in payload["reasoning"].lower()
    # The Python spec's sandbox_test_cmd ("python3 -m py_compile {files}") was
    # actually used - not silently defaulted back to the React spec's tsc command.
    assert captured_test_cmd["value"] == python_spec.sandbox_test_cmd
