import os
from typing import cast, Any
from pydantic import SecretStr

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from .state import RepoPilotState
from .schemas import EngineerDiffOutput, QAValidationResult

claude_model = ChatAnthropic(
    model_name="claude-sonnet-4-5",
    temperature=0.2,
    timeout=None,
    stop=None,
)

deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
if not deepseek_api_key:
    raise RuntimeError("DEEPSEEK_API_KEY environment variable is not set.")

deepseek_model = ChatOpenAI(
    model="deepseek-chat",
    api_key=SecretStr(deepseek_api_key),
    base_url="https://api.deepseek.com/v1",
    temperature=0,
)


def architect_node(state: RepoPilotState) -> dict:
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
    """Takes the Architect's plan and generates the Search/Replace Diff."""
    system_prompt = "You are a Senior Engineer. Execute the architect's plan strictly using the Search/Replace schema."

    structured_engineer = claude_model.with_structured_output(
        EngineerDiffOutput, method="json_schema"
    )

    prompt = (
        f"Plan:\n{state['architect_plan']}\n\nOriginal Code:\n{state['original_code']}"
    )

    if state["status"] == "qa_failed" and state.get("errors"):
        prompt += f"\n\nQA Feedback: Fix these issues:\n{state['errors'][-1]}"

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


def qa_node(state: RepoPilotState) -> dict:
    """DeepSeek validates the diff before sending it to the Sandbox."""
    system_prompt = "You are a QA Agent. Validate this code diff. Ensure the 'search_block' exists in the original code."

    structured_qa = deepseek_model.with_structured_output(
        QAValidationResult, method="json_schema"
    )

    current_diff = state.get("current_diff")
    if current_diff is None:
        raise ValueError("QA Node executed but 'current_diff' is missing from state.")

    prompt = f"Original:\n{state['original_code']}\n\nDiff Proposed:\n{current_diff.model_dump_json()}"

    validation = cast(
        QAValidationResult,
        structured_qa.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
        ),
    )

    if validation.is_valid:
        return {"status": "compiling"}
    else:
        current_errors = list(state.get("errors", []))
        if validation.feedback:
            current_errors.append(validation.feedback)

        return {"status": "qa_failed", "errors": current_errors}
