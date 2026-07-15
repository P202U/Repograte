from unittest.mock import Mock

from repograte.config import settings
from repograte.orchestration import nodes
from repograte.orchestration.migration_spec import load_migration_spec
from repograte.orchestration.schemas import (
    EngineerDiffOutput,
    QAValidationResult,
    SearchReplaceBlock,
)
from repograte.sandbox.e2b_runner import SandboxRunResult

ORIGINAL_CODE = "class Foo extends React.Component {\n  render() { return 1; }\n}\n"
VALID_DIFF = EngineerDiffOutput(
    reasoning="test",
    blocks=[SearchReplaceBlock(search_block="return 1;", replace_block="return 2;")],
)
SPEC = load_migration_spec("react-class-to-hooks").model_dump()


def _base_state(**overrides):
    state = {
        "file_path": "src/Foo.tsx",
        "original_code": ORIGINAL_CODE,
        "repo_url": "https://github.com/example/example.git",
        "branch": "main",
        "loop_count": 0,
        "errors": [],
        "diff_history": [],
        "messages": [],
        "current_diff": VALID_DIFF,
        "migration_spec": SPEC,
    }
    state.update(overrides)
    return state


def test_engineer_node_increments_loop_count(monkeypatch):
    fake = Mock()
    fake.with_structured_output.return_value.invoke.return_value = VALID_DIFF
    monkeypatch.setattr(nodes, "_get_claude_model", lambda: fake)

    result = nodes.engineer_node(_base_state(loop_count=2))
    assert result["loop_count"] == 3


def test_qa_node_fails_normally_under_the_cap(monkeypatch):
    fake = Mock()
    fake.with_structured_output.return_value.invoke.return_value = QAValidationResult(
        is_valid=False, feedback="not idiomatic hooks usage"
    )
    monkeypatch.setattr(nodes, "_get_deepseek_model", lambda: fake)

    state = _base_state(loop_count=1)  # well under settings.max_correction_loops
    result = nodes.qa_node(state)
    assert result["status"] == "qa_failed"
    assert "not idiomatic hooks usage" in result["errors"][-1]


def test_qa_node_marks_failed_wip_once_cap_reached(monkeypatch):
    """Regression test for the previously-unbounded QA retry loop: once
    loop_count has reached the cap, a QA failure must stop the retry loop
    instead of looping engineer<->qa_validator forever."""
    fake = Mock()
    fake.with_structured_output.return_value.invoke.return_value = QAValidationResult(
        is_valid=False, feedback="still wrong"
    )
    monkeypatch.setattr(nodes, "_get_deepseek_model", lambda: fake)

    state = _base_state(loop_count=settings.max_correction_loops)
    result = nodes.qa_node(state)
    assert result["status"] == "failed_wip"


def test_qa_node_mechanical_failure_also_respects_the_cap(monkeypatch):
    bad_diff = EngineerDiffOutput(
        reasoning="test",
        blocks=[SearchReplaceBlock(search_block="not in the source", replace_block="x")],
    )
    state = _base_state(loop_count=settings.max_correction_loops, current_diff=bad_diff)
    result = nodes.qa_node(state)
    assert result["status"] == "failed_wip"
    assert "Mechanical diff check failed" in result["errors"][-1]


def test_sandbox_node_does_not_re_increment_loop_count(monkeypatch):
    monkeypatch.setattr(
        nodes.E2BRunner,
        "run_verification",
        lambda self, **kwargs: SandboxRunResult(
            success=False, stdout="", stderr="type error", exit_code=1, step="test"
        ),
    )
    state = _base_state(loop_count=2)
    result = nodes.sandbox_node(state)
    # sandbox_node must not touch loop_count at all - it was already set by
    # engineer_node for this attempt.
    assert "loop_count" not in result
    assert result["status"] == "sandbox_failed"


def test_sandbox_node_marks_failed_wip_once_cap_reached(monkeypatch):
    monkeypatch.setattr(
        nodes.E2BRunner,
        "run_verification",
        lambda self, **kwargs: SandboxRunResult(
            success=False, stdout="", stderr="type error", exit_code=1, step="test"
        ),
    )
    state = _base_state(loop_count=settings.max_correction_loops)
    result = nodes.sandbox_node(state)
    assert result["status"] == "failed_wip"


def test_sandbox_node_passes_configured_commands_through(monkeypatch):
    captured = {}

    def _fake_run_verification(self, **kwargs):
        captured.update(kwargs)
        return SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step="")

    monkeypatch.setattr(nodes.E2BRunner, "run_verification", _fake_run_verification)

    state = _base_state(install_cmd="pnpm install", test_cmd="pnpm test")
    nodes.sandbox_node(state)
    assert captured["install_cmd"] == "pnpm install"
    assert captured["test_cmd"] == "pnpm test"


def test_sandbox_node_falls_back_to_spec_when_no_override_given(monkeypatch):
    """Precedence is state override > migration_spec > settings. The
    react-class-to-hooks spec's commands happen to match the global settings
    defaults, but a distinctly-configured spec must still win - proven in
    test_sandbox_node_uses_python_spec_commands below."""
    captured = {}

    def _fake_run_verification(self, **kwargs):
        captured.update(kwargs)
        return SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step="")

    monkeypatch.setattr(nodes.E2BRunner, "run_verification", _fake_run_verification)

    nodes.sandbox_node(_base_state())
    assert captured["install_cmd"] == SPEC["sandbox_install_cmd"]
    assert captured["test_cmd"] == SPEC["sandbox_test_cmd"]


def test_sandbox_node_uses_python_spec_commands_when_no_override_given(monkeypatch):
    """Regression test: sandbox_node must read its commands from the run's
    own migration_spec, not always fall back to the global settings."""
    captured = {}

    def _fake_run_verification(self, **kwargs):
        captured.update(kwargs)
        return SandboxRunResult(success=True, stdout="ok", stderr="", exit_code=0, step="")

    monkeypatch.setattr(nodes.E2BRunner, "run_verification", _fake_run_verification)

    python_spec = load_migration_spec("python2-to-3").model_dump()
    state = _base_state(migration_spec=python_spec)
    nodes.sandbox_node(state)
    assert captured["install_cmd"] == python_spec["sandbox_install_cmd"]
    assert captured["test_cmd"] == python_spec["sandbox_test_cmd"]
    assert captured["test_cmd"] != settings.sandbox_test_cmd
