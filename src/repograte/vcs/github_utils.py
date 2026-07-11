import re
import shutil
import tempfile
from typing import List

import git
from github import Github, Auth

from ..config import settings


def _parse_owner_repo(repo_url: str) -> str:
    """'https://github.com/owner/repo.git' -> 'owner/repo'."""
    match = re.search(r"github\.com[:/](.+?)(\.git)?/?$", repo_url.strip())
    if not match:
        raise ValueError(f"Could not parse an owner/repo from {repo_url!r}")
    return match.group(1)


def build_pr_body(
    reasoning: str,
    diff_history_summaries: List[str],
    final_status: str,
    sandbox_logs_excerpt: str,
) -> str:
    """
    A converged migration (final_status != 'failed_wip') just gets the
    engineer's own reasoning as the PR body. A graceful-degradation run
    gets a full markdown summary of every attempt plus the final sandbox
    output, so a human can pick up exactly where the loop left off instead
    of re-deriving the history themselves.
    """
    if final_status != "failed_wip":
        return reasoning

    lines = [
        "# Repograte: automated migration attempt",
        "",
        f"This did not converge after {len(diff_history_summaries)} attempt(s) and needs a human to finish it.",
        "",
        "## Attempts",
    ]
    for i, summary in enumerate(diff_history_summaries, start=1):
        lines.append(f"\n### Attempt {i}")
        lines.append(summary)

    lines.append("\n## Final sandbox output")
    lines.append(f"```\n{sandbox_logs_excerpt[-2000:]}\n```")
    return "\n".join(lines)


def create_pull_request(
    repo_url: str,
    base_branch: str,
    file_path: str,
    patched_code: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    draft: bool = False,
) -> str:
    """
    Clones `repo_url` at `base_branch`, writes `patched_code` over
    `file_path` on `branch_name`, commits, pushes, and opens a PR.
    Returns the PR's URL.

    Raises RuntimeError immediately (before cloning anything) if GITHUB_TOKEN
    isn't set.
    """
    if not settings.github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. A token with 'repo' scope is required to "
            "push a branch and open a pull request - set it and re-run; the "
            "approved diff hasn't been touched, so nothing is lost by retrying."
        )

    clone_url = repo_url
    if clone_url.startswith("https://"):
        clone_url = clone_url.replace(
            "https://", f"https://x-access-token:{settings.github_token}@", 1
        )

    workdir = tempfile.mkdtemp(prefix="repograte_")
    try:
        repo = git.Repo.clone_from(clone_url, workdir, branch=base_branch, depth=1)

        new_branch = repo.create_head(branch_name)
        new_branch.checkout()

        full_path = f"{workdir}/{file_path.lstrip('/')}"
        with open(full_path, "w") as f:
            f.write(patched_code)

        repo.index.add([file_path])
        repo.index.commit(commit_message)
        repo.remote("origin").push(branch_name)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    gh = Github(auth=Auth.Token(settings.github_token))
    gh_repo = gh.get_repo(_parse_owner_repo(repo_url))
    pr = gh_repo.create_pull(
        base=base_branch, head=branch_name, title=pr_title, body=pr_body, draft=draft
    )

    return pr.html_url
