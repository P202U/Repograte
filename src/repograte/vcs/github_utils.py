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


def build_codebase_pr_body(
    migration_name: str,
    converged: dict[str, str],
    wip: dict[str, str],
) -> str:
    """PR body for a codebase-wide run: one PR covering every file that
    reached a result, converged or not. `converged`/`wip` map file_path ->
    a short summary (the Engineer's reasoning, or a failure note)."""
    lines = [
        f"# Repograte: {migration_name}",
        "",
        f"{len(converged)} file(s) converged; {len(wip)} needs manual finishing.",
    ]

    if converged:
        lines.append("\n## Converged")
        for path, summary in converged.items():
            lines.append(f"\n### `{path}`")
            lines.append(summary)

    if wip:
        lines.append("\n## Needs a human to finish (hit the retry cap)")
        for path, summary in wip.items():
            lines.append(f"\n### `{path}`")
            lines.append(summary)

    return "\n".join(lines)


def create_pull_request(
    repo_url: str,
    base_branch: str,
    files: dict[str, str],
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    draft: bool = False,
) -> str:
    """
    Clones `repo_url` at `base_branch`, writes every path in `files`
    (relative repo path -> new content) on `branch_name`, commits everything
    in one commit, pushes, and opens a PR. Returns the PR's URL.

    `files` holds one entry for a single-file run, or every approved file
    from a codebase-wide run - either way, one PR is opened for the whole
    batch rather than one per file, since that's how a human actually wants
    to review "migrate this codebase" (a single coherent PR, not N of them).

    Raises RuntimeError immediately (before cloning anything) if GITHUB_TOKEN
    isn't set.
    """
    if not files:
        raise ValueError("create_pull_request called with no files to write.")

    if not settings.github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. A token with 'repo' scope is required to "
            "push a branch and open a pull request - set it and re-run; the "
            "approved diff(s) haven't been touched, so nothing is lost by retrying."
        )

    clone_url = repo_url
    if clone_url.startswith("https://"):
        # Inject the token into the URL so the push is authenticated without mutating the machine's global git config.
        clone_url = clone_url.replace(
            "https://", f"https://x-access-token:{settings.github_token}@", 1
        )

    workdir = tempfile.mkdtemp(prefix="repograte_")
    try:
        repo = git.Repo.clone_from(clone_url, workdir, branch=base_branch, depth=1)

        new_branch = repo.create_head(branch_name)
        new_branch.checkout()

        for file_path, patched_code in files.items():
            full_path = f"{workdir}/{file_path.lstrip('/')}"
            with open(full_path, "w") as f:
                f.write(patched_code)

        repo.index.add(list(files.keys()))
        repo.index.commit(commit_message)
        repo.remote("origin").push(branch_name)
    finally:
        # Otherwise every PR created leaves a full clone behind in /tmp forever.
        shutil.rmtree(workdir, ignore_errors=True)

    gh = Github(auth=Auth.Token(settings.github_token))
    gh_repo = gh.get_repo(_parse_owner_repo(repo_url))
    pr = gh_repo.create_pull(
        base=base_branch, head=branch_name, title=pr_title, body=pr_body, draft=draft
    )

    return pr.html_url
