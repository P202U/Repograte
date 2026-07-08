import uuid
from typing import cast, Any

from pydantic import SecretStr
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt

from ..config import settings
from ..sandbox.e2b_runner import E2BRunner
from ..vcs.github_utils import build_pr_body, create_pull_request
from .state import RepoPilotState
from .schemas import EngineerDiffOutput, QAValidationResult

# Initialize Models
claude_model = ChatAnthropic(
    model_name="claude-4-5-sonnet",
    temperature=0.2,
    timeout=None,
    stop=None,
    api_key=SecretStr(settings.anthropic_api_key),
)

# DeepSeek for fast, cheap QA and JSON enforcement
deepseek_model = ChatOpenAI(
    model="deepseek-chat",
    api_key=SecretStr(settings.deepseek_api_key),
    base_url=settings.deepseek_base_url,
    temperature=0,
)


def apply_diff(original_code: str, diff: EngineerDiffOutput) -> str:
    """
    Deterministically applies each search/replace block.
    Raises ValueError with a precise, actionable message if a search_block
    doesn't match the source exactly once - the classic failure mode of
    Aider-style diffs, and far cheaper to catch here (a string check) than
    after burning a full sandbox boot + npm install to discover it.
    """
    patched = original_code
    for i, block in enumerate(diff.blocks):
        occurrences = patched.count(block.search_block)
        if occurrences == 0:
            raise ValueError(
                f"Block {i}: search_block not found verbatim in source. "
                "The engineer must copy the existing code exactly, including whitespace."
            )
        if occurrences > 1:
            raise ValueError(
                f"Block {i}: search_block matches {occurrences} locations; "
                "it must uniquely identify one location. Include more surrounding context."
            )
        patched = patched.replace(block.search_block, block.replace_block, 1)
    return patched


def architect_node(state: RepoPilotState) -> dict[str, Any]:
    """Queries context and writes the migration blueprint."""
    system_prompt = "You are a Principal AI Architect. Write a step-by-step plan to migrate this React Class Component to Hooks."
    message = HumanMessage(
        content=f"File: {state['file_path']}\nCode:\n{state['original_code']}"
    )
    response = claude_model.invoke([SystemMessage(content=system_prompt), message])
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
    system_prompt = "You are a Senior Engineer. Execute the architect's plan strictly using the Search/Replace schema."
    structured_engineer = claude_model.with_structured_output(
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
            [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
        ),
    )
    diff_history = list(state.get("diff_history", []))
    diff_history.append(response)

    return {
        "current_diff": response,
        "diff_history": diff_history,
        "status": "validating",
    }


def qa_node(state: RepoPilotState) -> dict[str, Any]:
    """
    DeepSeek validates the diff before sending it to the Sandbox.
    Mechanical matching is done in code, logically sound judgement by DeepSeek.
    """
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("QA node executed without a current diff.")

    try:
        apply_diff(state["original_code"], diff)
    except ValueError as e:
        current_errors = list(state.get("errors", []))
        current_errors.append(f"Mechanical diff check failed: {e}")
        return {"status": "qa_failed", "errors": current_errors}

    system_prompt = (
        "You are a QA Agent. The search/replace blocks have already been "
        "verified to exist verbatim in the source. Judge only whether the "
        "replacement code is logically sound React/Hooks code."
    )
    structured_qa = deepseek_model.with_structured_output(
        QAValidationResult, method="json_schema"
    )
    prompt = f"Original:\n{state.get('original_code')}\n\nDiff Proposed:\n{diff.model_dump_json()}"

    validation = cast(
        QAValidationResult,
        structured_qa.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
        ),
    )

    if validation.is_valid:
        return {"status": "compiling"}

    current_errors = list(state.get("errors", []))
    if validation.feedback:
        current_errors.append(validation.feedback)

    return {"status": "qa_failed", "errors": current_errors}


def _sandbox_failure(
    error_message: str, logs: str, next_loop_count: int
) -> dict[str, Any]:
    # After 4 failed loops, stop retrying and fall through to a WIP PR
    status = "failed_wip" if next_loop_count >= 4 else "sandbox_failed"
    return {
        "status": status,
        "loop_count": next_loop_count,
        "errors": [error_message],
        "sandbox_logs": logs,
    }


def sandbox_node(state: RepoPilotState) -> dict[str, Any]:
    """Applies the current diff and verifies it for real inside an E2B microVM."""
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Sandbox node executed without a current diff.")

    next_loop_count = state.get("loop_count", 0) + 1

    try:
        patched_code = apply_diff(state["original_code"], diff)
    except ValueError as e:
        return _sandbox_failure(f"Diff could not be applied: {e}", "", next_loop_count)

    runner = E2BRunner()

    repo_url = state.get("repo_url")
    if not repo_url:
        raise ValueError("Sandbox node executed without a repo_url in state.")

    file_path = state.get("file_path")
    if file_path is None:
        raise ValueError("file_path is missing")

    result = runner.run_verification(
        repo_url=repo_url,
        branch=state.get("branch"),
        file_path=file_path,
        file_content=patched_code,
    )

    if result.success:
        return {
            "status": "success",
            "loop_count": next_loop_count,
            "sandbox_logs": result.stdout,
        }

    failure_summary = (
        f"[{result.step or 'test'} failed, exit code {result.exit_code}]\n"
        f"{result.stderr or result.stdout}"
    )

    return _sandbox_failure(
        failure_summary, result.stdout + "\n" + result.stderr, next_loop_count
    )


def debugger_node(state: RepoPilotState) -> dict[str, Any]:
    """Turns raw sandbox failure output into a short, focused correction instruction for the Engineer."""
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Debugger node executed without a current diff.")

    system_prompt = (
        "You are a Debugging Agent. Given a failed code change and its "
        "sandbox output, identify the root cause in 3-5 sentences and state "
        "precisely what the engineer should change. Do not restate the full logs."
    )
    # Safely get logs/errors from state and cast to string to prevent slicing errors on null types
    logs = str(state.get("sandbox_logs", ""))
    errors = state.get("errors", [""])

    prompt = (
        f"Original code:\n{state.get('original_code')}\n\n"
        f"Diff that was applied:\n{diff.model_dump_json()}\n\n"
        f"Sandbox output (may be truncated):\n{logs[-4000:]}\n\n"
        f"Last recorded error:\n{errors[-1]}"
    )

    response = claude_model.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
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
    """Applies the approved diff to a fresh clone and opens the PR."""
    diff = state.get("current_diff")
    if diff is None:
        raise ValueError("Publish PR node executed without a current diff.")

    patched_code = apply_diff(state["original_code"], diff)
    is_wip = state.get("status") == "failed_wip"

    file_path = state.get("file_path", "")
    slug = file_path.rsplit("/", 1)[-1].split(".")[0].lower()
    branch_name = f"repo-pilot/{slug}-{uuid.uuid4().hex[:8]}"
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
        file_path=file_path,
        patched_code=patched_code,
        branch_name=branch_name,
        commit_message=f"{title_prefix}Repo-Pilot: migrate {file_path} to hooks",
        pr_title=f"{title_prefix}Migrate {file_path} to Hooks",
        pr_body=pr_body,
        draft=is_wip,
    )

    return {"pr_url": pr_url, "status": "pr_opened"}
