import argparse
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from repograte.config import settings
from repograte.orchestration.graph import build_graph
from repograte.orchestration.migration_spec import (
    list_builtin_specs,
    load_migration_spec,
)
from repograte.orchestration.state import RepoPilotState


def _pending_interrupt(result: dict):
    """Extracts an interrupt payload from a graph.invoke() result dict, or None."""
    interrupts = result.get("__interrupt__")
    return interrupts[0].value if interrupts else None


def _print_review_prompt(payload: dict) -> None:
    print("\n=== Human review requested ===")
    print(f"File:   {payload['file_path']}")
    print(f"Status: {payload['status']}  (loop {payload['loop_count']})")
    if payload.get("architect_plan"):
        print(f"\nArchitect plan:\n{payload['architect_plan']}")
    print(f"\nReasoning:\n{payload['reasoning']}")
    print("\nProposed changes:")
    for block in payload["diff"]:
        print("--- search ---")
        print(block["search_block"])
        print("--- replace ---")
        print(block["replace_block"])


def main():
    parser = argparse.ArgumentParser(description="Run Repograte on a single file.")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--file", required=True, dest="file_path")
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--code-file",
        default=None,
        help=(
            "Local path to the file's current contents. Required to start a "
            "new run; not needed when resuming an interrupted one."
        ),
    )
    parser.add_argument(
        "--migration",
        default=settings.default_migration,
        help=f"Built-in: {', '.join(list_builtin_specs())}. Or a path to your own YAML file.",
    )
    parser.add_argument(
        "--install-cmd",
        default=None,
        help="Override the migration spec's sandbox install command for this run only.",
    )
    parser.add_argument(
        "--test-cmd",
        default=None,
        help="Override the migration spec's sandbox test command for this run only. "
        'Use this for non-TypeScript projects, e.g. --test-cmd "npm run lint".',
    )
    args = parser.parse_args()

    graph = build_graph()
    spec = load_migration_spec(args.migration)

    thread_id = f"{args.repo_url}:{args.file_path}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)
    pending = None

    if snapshot.next and snapshot.tasks and snapshot.tasks[0].interrupts:
        print(f"\nResuming an interrupted run for {args.file_path} ...")
        pending = snapshot.tasks[0].interrupts[0].value
        result: dict = {}
    else:
        if not args.code_file:
            parser.error(
                "--code-file is required to start a new run "
                "(no interrupted run found for this --repo-url/--file)."
            )
        with open(args.code_file) as f:
            original_code = f.read()

        state: RepoPilotState = {
            "file_path": args.file_path,
            "original_code": original_code,
            "repo_url": args.repo_url,
            "branch": args.branch,
            "loop_count": 0,
            "errors": [],
            "diff_history": [],
            "messages": [],
            "install_cmd": args.install_cmd,
            "test_cmd": args.test_cmd,
            "migration_spec": spec.model_dump(),
            "repo_overlay": {},
        }
        result = graph.invoke(state, config=config)
        pending = _pending_interrupt(result)

    while pending is not None:
        _print_review_prompt(pending)

        answer = input("\nApprove and open PR? [y/n]: ").strip().lower()
        if answer == "y":
            resume = {"approved": True}
        else:
            feedback = input("Feedback for another attempt (Enter to skip): ").strip()
            resume = {"approved": False, "feedback": feedback}

        result = graph.invoke(Command(resume=resume), config=config)
        pending = _pending_interrupt(result)

    if result.get("pr_url"):
        print(f"\n✅ PR opened: {result['pr_url']}")
    else:
        print(f"\nFinished with status: {result.get('status', 'unknown')}")


if __name__ == "__main__":
    main()
