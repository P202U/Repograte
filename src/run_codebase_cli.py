import argparse

from repograte.config import settings
from repograte.orchestration.codebase import (
    FileResult,
    plan_migration,
    publish_codebase_pr,
    run_codebase_migration,
)
from repograte.orchestration.graph import build_graph
from repograte.orchestration.migration_spec import (
    list_builtin_specs,
    load_migration_spec,
)

_OUTCOME_ICON = {
    "converged": "\u2705",
    "wip": "\u26a0\ufe0f ",
    "skipped": "\u23ed\ufe0f ",
}


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


def _make_review_fn(auto_approve: bool):
    def _review(payload: dict) -> dict:
        _print_review_prompt(payload)

        if auto_approve and payload.get("status") == "success":
            print("\nAuto-approved (passed QA + sandbox cleanly).")
            return {"approved": True}

        answer = (
            input("\n[a]pprove / [r]etry with feedback / [s]kip this file: ")
            .strip()
            .lower()
        )
        if answer == "a":
            return {"approved": True}
        if answer == "s":
            return {"skip": True}
        feedback = input("Feedback for another attempt (Enter to skip): ").strip()
        return {"approved": False, "feedback": feedback}

    return _review


def _print_progress(result: FileResult) -> None:
    icon = _OUTCOME_ICON.get(result.outcome, "?")
    print(f"{icon} {result.file_path}: {result.outcome}")


def main():
    parser = argparse.ArgumentParser(description="Run Repograte across an entire repo.")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--migration",
        default=settings.default_migration,
        help=f"Built-in: {', '.join(list_builtin_specs())}. Or a path to your own YAML file.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=settings.codebase_max_files,
        help="Hard cap on how many files this run will touch.",
    )
    parser.add_argument(
        "--install-cmd",
        default=None,
        help="Override the migration spec's sandbox install command.",
    )
    parser.add_argument(
        "--test-cmd",
        default=None,
        help="Override the migration spec's sandbox test command.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip manual review for files that pass QA + sandbox cleanly. Anything "
        "that hits the retry cap still needs a human - this never auto-ships "
        "unverified code, it just removes friction for the clean passes.",
    )
    args = parser.parse_args()

    spec = load_migration_spec(args.migration)
    if args.install_cmd:
        spec = spec.model_copy(update={"sandbox_install_cmd": args.install_cmd})
    if args.test_cmd:
        spec = spec.model_copy(update={"sandbox_test_cmd": args.test_cmd})

    print(f"Scanning {args.repo_url} for files matching '{spec.name}' ...")
    tasks = plan_migration(args.repo_url, args.branch, spec, max_files=args.max_files)
    if not tasks:
        print("No matching files found - nothing to do.")
        return

    print(f"Found {len(tasks)} file(s), migrating in this order:")
    for t in tasks:
        print(f"  - {t.file_path}")

    graph = build_graph()
    summary = run_codebase_migration(
        graph=graph,
        repo_url=args.repo_url,
        branch=args.branch,
        spec=spec,
        tasks=tasks,
        review_fn=_make_review_fn(args.auto_approve),
        on_file_done=_print_progress,
    )

    pr_url = publish_codebase_pr(args.repo_url, args.branch, spec, summary)

    print(
        f"\n{len(summary.converged)} converged, {len(summary.wip)} WIP, "
        f"{len(summary.skipped)} skipped."
    )
    if pr_url:
        print(f"\u2705 PR opened: {pr_url}")
    else:
        print("No files were approved - no PR opened.")


if __name__ == "__main__":
    main()
