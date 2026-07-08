import argparse
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from repograte.orchestration.graph import build_graph
from repograte.orchestration.state import RepoPilotState


def main():
    parser = argparse.ArgumentParser(description="Run Repo-Pilot on a single file.")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--file", required=True, dest="file_path")
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--code-file", required=True, help="Local path to the file's current contents."
    )
    args = parser.parse_args()

    with open(args.code_file) as f:
        original_code = f.read()

    graph = build_graph()

    # thread_id identifies this run across the pause/resume boundary
    thread_id = f"{args.repo_url}:{args.file_path}"

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    state: RepoPilotState = {
        "file_path": args.file_path,
        "original_code": original_code,
        "repo_url": args.repo_url,
        "branch": args.branch,
        "loop_count": 0,
        "errors": [],
        "diff_history": [],
        "messages": [],
    }

    result = graph.invoke(state, config=config)

    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print("\n=== Human review requested ===")
        print(f"File:   {payload['file_path']}")
        print(f"Status: {payload['status']}  (loop {payload['loop_count']})")
        print(f"\nReasoning:\n{payload['reasoning']}")
        print("\nProposed changes:")
        for block in payload["diff"]:
            print("--- search ---")
            print(block["search_block"])
            print("--- replace ---")
            print(block["replace_block"])

        answer = input("\nApprove and open PR? [y/n]: ").strip().lower()
        if answer == "y":
            resume = {"approved": True}
        else:
            feedback = input("Feedback for another attempt (Enter to skip): ").strip()
            resume = {"approved": False, "feedback": feedback}

        result = graph.invoke(Command(resume=resume), config=config)

    if result.get("pr_url"):
        print(f"\n✅ PR opened: {result['pr_url']}")
    else:
        print(f"\nFinished with status: {result['status']}")


if __name__ == "__main__":
    main()
