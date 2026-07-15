"""Integration tests for the multi-file driver, with every external service
(Claude, DeepSeek, E2B, GitHub) faked - same approach as test_integration.py,
extended to cover what's specific to running several files through the
existing single-file graph in sequence.
"""

import re
import sqlite3

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from repograte.orchestration import nodes
from repograte.orchestration.codebase import (
    CodebaseRunSummary,
    FileTask,
    publish_codebase_pr,
    run_codebase_migration,
    run_single_file,
)
from repograte.orchestration.graph import build_graph
from repograte.orchestration.migration_spec import load_migration_spec
from repograte.orchestration.schemas import (
    EngineerDiffOutput,
    QAValidationResult,
    SearchReplaceBlock,
)
from repograte.sandbox.e2b_runner import SandboxRunResult

SPEC = load_migration_spec("react-class-to-hooks")


def _marker(messages) -> str:
    text = messages[-1].content
    m = re.search(r"<div>(\w+)</div>", text)
    assert m, f"no marker found in prompt: {text[:200]}"
    return m.group(1)


class _FakeInvoker:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class _DynamicEngineerInvoker:
    """Builds a diff whose search/replace text is derived from whichever
    file's content is actually in the prompt, so the same fake model works
    for any number of distinct synthetic files in one test."""

    def invoke(self, messages):
        marker = _marker(messages)
        return EngineerDiffOutput(
            reasoning=f"Converted {marker} to hooks.",
            blocks=[
                SearchReplaceBlock(
                    search_block=f"return <div>{marker}</div>;",
                    replace_block=f"return <div>{marker}, hooks</div>;",
                )
            ],
        )


class FakeChatModel:
    def invoke(self, messages):
        return AIMessage(content="Plan: migrate to hooks.")

    def with_structured_output(self, schema, method=None):
        if schema is EngineerDiffOutput:
            return _DynamicEngineerInvoker()
        if schema is QAValidationResult:
            return _FakeInvoker(QAValidationResult(is_valid=True, feedback=""))
        raise AssertionError(f"Unexpected schema: {schema}")


def _make_file(marker: str) -> str:
    return f"class {marker} extends React.Component {{\n  render() {{\n    return <div>{marker}</div>;\n  }}\n}}\n"


@pytest.fixture
def sandbox_calls(monkeypatch):
    """Patches everything external and returns the list of `files` dicts
    passed to run_verification, in call order - lets tests assert the
    overlay actually reached the sandbox step for later files."""
    calls: list[dict] = []
    monkeypatch.setattr(nodes, "_get_claude_model", lambda: FakeChatModel())
    monkeypatch.setattr(nodes, "_get_deepseek_model", lambda: FakeChatModel())
    monkeypatch.setattr(nodes, "gather_repo_context", lambda **kwargs: "")

    def _fake_run_verification(self, **kwargs):
        calls.append(kwargs["files"])
        return SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step="")

    monkeypatch.setattr(nodes.E2BRunner, "run_verification", _fake_run_verification)
    return calls


def _approve_all(payload: dict) -> dict:
    return {"approved": True}


def test_overlay_propagates_to_later_files_sandbox_check(sandbox_calls):
    """Regression test: file N's sandbox verification must include every
    previously-approved file's patch, not just its own - otherwise
    cross-file breakage between migrated files would go undetected."""
    leaf = FileTask(file_path="src/Leaf.tsx", original_code=_make_file("Leaf"))
    app = FileTask(file_path="src/App.tsx", original_code=_make_file("App"), dependencies=["./Leaf"])

    graph = build_graph(checkpointer=MemorySaver())
    summary = run_codebase_migration(
        graph=graph,
        repo_url="https://github.com/example/example.git",
        branch="main",
        spec=SPEC,
        tasks=[leaf, app],  # deliberately not pre-ordered - driver doesn't reorder itself
        review_fn=_approve_all,
        thread_id_prefix="test-overlay",
    )

    assert set(summary.converged) == {"src/Leaf.tsx", "src/App.tsx"}
    assert summary.patched_files["src/Leaf.tsx"].strip() != leaf.original_code.strip()

    # Two sandbox calls: Leaf's (overlay empty) and App's (overlay has Leaf's patch).
    assert len(sandbox_calls) == 2
    leaf_call, app_call = sandbox_calls
    assert set(leaf_call.keys()) == {"src/Leaf.tsx"}
    assert set(app_call.keys()) == {"src/Leaf.tsx", "src/App.tsx"}
    assert app_call["src/Leaf.tsx"] == summary.patched_files["src/Leaf.tsx"]


def test_skipped_file_excluded_from_overlay_and_batch(sandbox_calls):
    leaf = FileTask(file_path="src/Leaf.tsx", original_code=_make_file("Leaf"))
    app = FileTask(file_path="src/App.tsx", original_code=_make_file("App"))

    def _skip_leaf_approve_rest(payload: dict) -> dict:
        return {"skip": True} if payload["file_path"] == "src/Leaf.tsx" else {"approved": True}

    graph = build_graph(checkpointer=MemorySaver())
    summary = run_codebase_migration(
        graph=graph,
        repo_url="https://github.com/example/example.git",
        branch="main",
        spec=SPEC,
        tasks=[leaf, app],
        review_fn=_skip_leaf_approve_rest,
        thread_id_prefix="test-skip",
    )

    assert summary.skipped == ["src/Leaf.tsx"]
    assert "src/Leaf.tsx" not in summary.patched_files
    assert set(summary.converged) == {"src/App.tsx"}
    # App's sandbox check must not include the skipped file.
    assert "src/Leaf.tsx" not in sandbox_calls[-1]


def test_publish_codebase_pr_batches_everything(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "repograte.orchestration.codebase.create_pull_request",
        lambda **kwargs: captured.update(kwargs) or "https://github.com/example/example/pull/9",
    )

    summary = CodebaseRunSummary(
        converged={"src/Leaf.tsx": "Converted Leaf."},
        wip={"src/App.tsx": "Still failing after retries."},
        patched_files={"src/Leaf.tsx": "new leaf content", "src/App.tsx": "new app content"},
    )

    url = publish_codebase_pr(
        repo_url="https://github.com/example/example.git",
        branch="main",
        spec=SPEC,
        summary=summary,
    )

    assert url == "https://github.com/example/example/pull/9"
    assert summary.pr_url == url
    assert captured["files"] == summary.patched_files
    assert captured["draft"] is True  # because there's a WIP file
    assert "[WIP]" in captured["pr_title"]


def test_publish_codebase_pr_no_files_opens_nothing():
    summary = CodebaseRunSummary()  # everything skipped
    assert publish_codebase_pr("url", "main", SPEC, summary) is None


def test_resume_reuses_already_completed_file_without_reinvoking(tmp_path, monkeypatch):
    """Regression test for the multi-file equivalent of the cross-process
    resume bug: a file already fully approved in an earlier (crashed) run of
    the same codebase-wide command must be reused, not silently re-migrated
    (which would waste API calls and could even produce a different diff the
    second time)."""
    call_count = {"n": 0}

    class _CountingEngineerInvoker(_DynamicEngineerInvoker):
        def invoke(self, messages):
            call_count["n"] += 1
            return super().invoke(messages)

    class _CountingFakeChatModel(FakeChatModel):
        def with_structured_output(self, schema, method=None):
            if schema is EngineerDiffOutput:
                return _CountingEngineerInvoker()
            return super().with_structured_output(schema, method)

    monkeypatch.setattr(nodes, "_get_claude_model", lambda: _CountingFakeChatModel())
    monkeypatch.setattr(nodes, "_get_deepseek_model", lambda: _CountingFakeChatModel())
    monkeypatch.setattr(nodes, "gather_repo_context", lambda **kwargs: "")
    monkeypatch.setattr(
        nodes.E2BRunner,
        "run_verification",
        lambda self, **kwargs: SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step=""),
    )

    task = FileTask(file_path="src/Leaf.tsx", original_code=_make_file("Leaf"))
    db_path = tmp_path / "checkpoints.sqlite"

    conn1 = sqlite3.connect(str(db_path), check_same_thread=False)
    graph1 = build_graph(checkpointer=SqliteSaver(conn1))
    result1 = run_single_file(
        graph=graph1, repo_url="https://github.com/example/example.git", branch="main",
        spec=SPEC, task=task, overlay={}, review_fn=_approve_all, thread_id_prefix="resume-test",
    )
    assert result1.outcome == "converged"
    assert call_count["n"] == 1
    conn1.close()
    del graph1, conn1

    # "Process 2": fresh graph/checkpointer on the same DB file, exactly like
    # re-running the codebase CLI after it was killed.
    conn2 = sqlite3.connect(str(db_path), check_same_thread=False)
    graph2 = build_graph(checkpointer=SqliteSaver(conn2))
    result2 = run_single_file(
        graph=graph2, repo_url="https://github.com/example/example.git", branch="main",
        spec=SPEC, task=task, overlay={}, review_fn=_approve_all, thread_id_prefix="resume-test",
    )

    assert result2.outcome == "converged"
    assert result2.patched_code == result1.patched_code
    assert call_count["n"] == 1  # NOT re-invoked
