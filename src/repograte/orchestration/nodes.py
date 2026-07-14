import functools
import logging
import uuid
from typing import cast, Any

from pydantic import SecretStr
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt

from ..config import settings
from ..ingestion.context import gather_repo_context
from ..sandbox.e2b_runner import E2BRunner
from ..vcs.github_utils import build_pr_body, create_pull_request
from .diffing import apply_diff
from .migration_spec import MigrationSpec
from .state import RepoPilotState
from .schemas import EngineerDiffOutput, QAValidationResult

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _get_claude_model() -> ChatAnthropic:
    return ChatAnthropic(
        model_name=settings.anthropic_model,
        temperature=0.2,
        timeout=None,
        stop=None,
        api_key=SecretStr(settings.anthropic_api_key),
    )


@functools.lru_cache(maxsize=1)
def _get_deepseek_model() -> ChatOpenAI:
    # Fast, cheap JSON-schema-enforcing QA judge.
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=SecretStr(settings.deepseek_api_key),
        base_url=settings.deepseek_base_url,
        temperature=0,
    )


def _get_spec(state: RepoPilotState) -> MigrationSpec:
    """Every node's prompts/scope/sandbox-cmds come from here instead of
    being hardcoded, so a different MigrationSpec (a YAML file, see
    orchestration/migration_spec.py) targets a different migration entirely
    without touching this module. Stored in state as a plain dict so it
    round-trips through the checkpointer without a serde allowlist entry."""
    return MigrationSpec.model_validate(state["migration_spec"])


def architect_node(state: RepoPilotState) -> dict[str, Any]:
    """Queries context and writes the migration blueprint."""
    spec = _get_spec(state)
    prompt = f"File: {state['file_path']}\nCode:\n{state['original_code']}"

    if settings.enable_rag_context:
        # Best-effort: never let a context-retrieval failure take down the run.
        try:
            extra_context = gather_repo_context(
                repo_url=state["repo_url"],
                branch=state.get("branch"),
                current_file_path=state["file_path"],
                current_code=state["original_code"],
                file_extensions=spec.file_extensions,
                overlay=state.get("repo_overlay"),
                max_files=settings.rag_max_files,
            )
            if extra_context:
                prompt += f"\n\n{extra_context}"
        except Exception:
            logger.warning(
                "gather_repo_context failed; continuing without it.", exc_info=True
            )

    message = HumanMessage(content=prompt)
    response = _get_claude_model().invoke(
        [SystemMessage(content=spec.architect_prompt), message]
    )
    architect_plan = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    return {
        "architect_plan": architect_plan,
        "status": "engineering",
        "messages": [response],
    }


def engineer_node(state: RepoPilotState) -> dict[str, Any]:
    """Takes the Architect's plan (or QA's/Debugger's feedback) and generates the Search/Replace Diff."""
    spec = _get_spec(state)
    structured_engineer = _get_claude_model().with_structured_output(
        EngineerDiffOutput, method="json_schema"
    )
    prompt = f"Plan:\n{state.get('architect_plan')}\n\nOriginal Code:\n{state.get('original_code')}"

    if state.get("status") in (
        "qa_failed",
        "sandbox_failed",
        "human_rejected",
    ) and state.get("errors"):
        prompt += f"\n\nFeedback from the last attempt - fix these issues:\n{state['errors'][-1]}"

    response = cast(
        EngineerDiffOutput,
        structured_engineer.invoke(
            [SystemMessage(content=spec.engineer_prompt), HumanMessage(content=prompt)]
        ),
    )
    diff_history = list(state.get("diff_history", []))
    diff_history.append(response)

    # This is "attempt N" - qa_node/sandbox_node read it (without incrementing
    # again) to decide whether the correction-loop cap has been hit.
    attempt = state.get("loop_count", 0) + 1

    return {
        "current_diff": response,
        "diff_history": diff_history,
        "status": "validating",
        "loop_count": attempt,
    }


def qa_node(state: RepoPilotState) -> dict[str, Any]:
    """
    DeepSeek validates the diff before sending it to the Sandbox.
    Mechanical matching is done in code, logically sound judgement by DeepSeek.
    """
    spec = _get_spec(state)
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("QA node executed without a current diff.")

    loop_count = state.get("loop_count", 0)

    def _qa_failed(message: str) -> dict[str, Any]:
        current_errors = list(state.get("errors", []))
        if message:
            current_errors.append(message)
        # Once the correction-loop cap is hit, stop retrying (even though we
        # never reached the sandbox) and fall through to human review instead
        # of looping engineer<->qa_validator forever.
        status = (
            "failed_wip" if loop_count >= settings.max_correction_loops else "qa_failed"
        )
        return {"status": status, "errors": current_errors}

    try:
        apply_diff(state["original_code"], diff)
    except ValueError as e:
        return _qa_failed(f"Mechanical diff check failed: {e}")

    structured_qa = _get_deepseek_model().with_structured_output(
        QAValidationResult, method="json_schema"
    )
    prompt = f"Original:\n{state.get('original_code')}\n\nDiff Proposed:\n{diff.model_dump_json()}"

    validation = cast(
        QAValidationResult,
        structured_qa.invoke(
            [SystemMessage(content=spec.qa_prompt), HumanMessage(content=prompt)]
        ),
    )

    if validation.is_valid:
        return {"status": "compiling"}

    return _qa_failed(validation.feedback)


def _sandbox_failure(error_message: str, logs: str, loop_count: int) -> dict[str, Any]:
    # After settings.max_correction_loops failed loops, stop retrying and fall
    # through to a WIP PR instead of looping forever.
    status = (
        "failed_wip"
        if loop_count >= settings.max_correction_loops
        else "sandbox_failed"
    )
    return {
        "status": status,
        "errors": [error_message],
        "sandbox_logs": logs,
    }


def sandbox_node(state: RepoPilotState) -> dict[str, Any]:
    """Applies the current diff and verifies it for real inside an E2B microVM."""
    spec = _get_spec(state)
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Sandbox node executed without a current diff.")

    # Set by engineer_node for this attempt; not incremented again here.
    loop_count = state.get("loop_count", 0)

    try:
        patched_code = apply_diff(state["original_code"], diff)
    except ValueError as e:
        return _sandbox_failure(f"Diff could not be applied: {e}", "", loop_count)

    runner = E2BRunner()

    repo_url = state.get("repo_url")
    if not repo_url:
        raise ValueError("Sandbox node executed without a repo_url in state.")

    file_path = state.get("file_path")
    if file_path is None:
        raise ValueError("file_path is missing")

    # Verify against the cumulative state of the migration so far: every
    # previously-approved file in a codebase-wide run (empty for a
    # single-file run), plus this file's own new patch on top.
    files = {**state.get("repo_overlay", {}), file_path: patched_code}

    result = runner.run_verification(
        repo_url=repo_url,
        branch=state.get("branch"),
        files=files,
        install_cmd=state.get("install_cmd") or spec.sandbox_install_cmd,
        test_cmd=state.get("test_cmd") or spec.sandbox_test_cmd,
        setup_cmds=spec.sandbox_setup_cmds,
    )

    if result.success:
        return {
            "status": "success",
            "sandbox_logs": result.stdout,
        }

    failure_summary = (
        f"[{result.step or 'test'} failed, exit code {result.exit_code}]\n"
        f"{result.stderr or result.stdout}"
    )

    return _sandbox_failure(
        failure_summary, result.stdout + "\n" + result.stderr, loop_count
    )


def debugger_node(state: RepoPilotState) -> dict[str, Any]:
    """Turns raw sandbox failure output into a short, focused correction instruction for the Engineer."""
    spec = _get_spec(state)
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Debugger node executed without a current diff.")

    # Safely get logs/errors from state and cast to string to prevent slicing errors on null types
    logs = str(state.get("sandbox_logs", ""))
    errors = state.get("errors", [""])

    prompt = (
        f"Original code:\n{state.get('original_code')}\n\n"
        f"Diff that was applied:\n{diff.model_dump_json()}\n\n"
        f"Sandbox output (may be truncated):\n{logs[-4000:]}\n\n"
        f"Last recorded error:\n{errors[-1]}"
    )

    response = _get_claude_model().invoke(
        [SystemMessage(content=spec.debugger_prompt), HumanMessage(content=prompt)]
    )

    return {
        "errors": [response.content],
        "messages": [response],
    }


def human_review_node(state: RepoPilotState) -> dict[str, Any]:
    """
    Pauses the graph and waits for a human to approve, reject, or send
    feedback before anything gets pushed.
    """
    diff = state.get("current_diff")
    decision = interrupt(
        {
            "file_path": state.get("file_path"),
            "status": state.get("status"),
            "architect_plan": state.get("architect_plan", ""),
            "reasoning": diff.reasoning if diff else "",
            "diff": [b.model_dump() for b in diff.blocks] if diff else [],
            "loop_count": state.get("loop_count", 0),
            "sandbox_logs": str(state.get("sandbox_logs", ""))[-2000:],
        }
    )

    # Expected resume payload: {"approved": bool, "feedback": Optional[str]}
    if decision.get("approved"):
        return {"human_approved": True}

    feedback = (
        decision.get("feedback")
        or "Human rejected the diff without additional feedback."
    )
    current_errors = list(state.get("errors", []))
    current_errors.append(f"Human feedback: {feedback}")

    return {
        "human_approved": False,
        "status": "human_rejected",
        "errors": current_errors,
    }


def publish_pr_node(state: RepoPilotState) -> dict[str, Any]:
    """Applies the approved diff and either opens a PR immediately
    (single-file run) or, in a codebase-wide run (state["defer_publish"] is
    True), just hands the final patched code back to the driver, which
    batches every approved file into one PR at the end instead of opening
    one per file."""
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Publish PR node executed without a current diff.")

    patched_code = apply_diff(state["original_code"], diff)
    is_wip = state.get("status") == "failed_wip"
    file_path = state.get("file_path", "")

    if state.get("defer_publish"):
        return {
            "patched_code": patched_code,
            "status": "failed_wip" if is_wip else "approved",
        }

    slug = file_path.rsplit("/", 1)[-1].split(".")[0].lower()
    branch_name = f"repograte/{slug}-{uuid.uuid4().hex[:8]}"
    title_prefix = "[WIP] " if is_wip else ""

    diff_history_summaries = [d.reasoning for d in state.get("diff_history", [])]

    pr_body = build_pr_body(
        reasoning=diff.reasoning,
        diff_history_summaries=diff_history_summaries,
        final_status=state.get("status", ""),
        sandbox_logs_excerpt=state.get("sandbox_logs", ""),
    )

    pr_url = create_pull_request(
        repo_url=state["repo_url"],
        base_branch=state.get("branch") or "main",
        files={file_path: patched_code},
        branch_name=branch_name,
        commit_message=f"{title_prefix}Repograte: migrate {file_path} to hooks",
        pr_title=f"{title_prefix}Migrate {file_path} to Hooks",
        pr_body=pr_body,
        draft=is_wip,
    )

    return {"pr_url": pr_url, "status": "pr_opened"}
