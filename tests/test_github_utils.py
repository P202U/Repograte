import subprocess

import pytest

from repograte.config import settings
from repograte.vcs.github_utils import (
    _parse_owner_repo,
    build_codebase_pr_body,
    build_pr_body,
    create_pull_request,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
    ],
)
def test_parse_owner_repo(url, expected):
    assert _parse_owner_repo(url) == expected


def test_parse_owner_repo_raises_on_garbage():
    with pytest.raises(ValueError):
        _parse_owner_repo("not a github url")


def test_build_pr_body_converged_run_is_just_the_reasoning():
    body = build_pr_body(
        reasoning="Converted class component to hooks.",
        diff_history_summaries=["attempt 1"],
        final_status="pr_opened",
        sandbox_logs_excerpt="tsc: no errors",
    )
    assert body == "Converted class component to hooks."


def test_build_pr_body_failed_wip_includes_full_history():
    body = build_pr_body(
        reasoning="final attempt reasoning",
        diff_history_summaries=["attempt 1 reasoning", "attempt 2 reasoning"],
        final_status="failed_wip",
        sandbox_logs_excerpt="tsc: error TS2322",
    )
    assert "attempt 1 reasoning" in body
    assert "attempt 2 reasoning" in body
    assert "tsc: error TS2322" in body
    assert "did not converge after 2 attempt(s)" in body


def test_create_pull_request_fails_fast_without_token(monkeypatch):
    """Regression test: previously the token was only checked *after*
    cloning, committing, and attempting an unauthenticated push - which fails
    on essentially every real GitHub repo with a raw GitPython error instead
    of this message. Assert no clone is even attempted now."""
    monkeypatch.setattr(settings, "github_token", "")

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("clone_from should not be called without a token")

    monkeypatch.setattr("git.Repo.clone_from", _should_not_be_called)

    with pytest.raises(RuntimeError, match="GITHUB_TOKEN is not set"):
        create_pull_request(
            repo_url="https://github.com/owner/repo.git",
            base_branch="main",
            files={"src/Foo.tsx": "..."},
            branch_name="repograte/foo-1234",
            commit_message="Repograte: migrate Foo",
            pr_title="Migrate Foo",
            pr_body="body",
        )


def _init_bare_remote(tmp_path) -> str:
    """Sets up a local bare repo (acting as 'GitHub') with one commit on
    main, and returns its path - usable as a repo_url since git supports
    local file paths."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    (seed / "src").mkdir()
    (seed / "src" / "A.tsx").write_text("class A extends React.Component {}\n")
    (seed / "src" / "B.tsx").write_text("class B extends React.Component {}\n")
    subprocess.run(["git", "add", "."], cwd=seed, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=seed, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)
    return str(bare)


def test_create_pull_request_writes_and_pushes_all_files(tmp_path, monkeypatch):
    """Real git clone/commit/push against a local bare repo (no network) -
    only the GitHub API call itself (opening the PR) is faked."""
    monkeypatch.setattr(settings, "github_token", "dummy-token-for-local-git")

    fake_pr = type("FakePR", (), {"html_url": "https://github.com/example/example/pull/7"})()
    fake_repo = type("FakeRepo", (), {"create_pull": lambda self, **kwargs: fake_pr})()
    fake_gh = type("FakeGithub", (), {"get_repo": lambda self, slug: fake_repo})()
    monkeypatch.setattr(
        "repograte.vcs.github_utils.Github", lambda auth: fake_gh
    )
    monkeypatch.setattr(
        "repograte.vcs.github_utils._parse_owner_repo", lambda repo_url: "owner/repo"
    )

    remote = _init_bare_remote(tmp_path)

    pr_url = create_pull_request(
        repo_url=remote,
        base_branch="main",
        files={
            "src/A.tsx": "function A() { return null; }\n",
            "src/B.tsx": "function B() { return null; }\n",
        },
        branch_name="repograte/batch-1234",
        commit_message="Repograte: migrate 2 files to hooks",
        pr_title="Migrate to hooks",
        pr_body="body",
    )

    assert pr_url == "https://github.com/example/example/pull/7"

    # Verify both files actually landed on the pushed branch in the remote.
    check = tmp_path / "check"
    subprocess.run(
        ["git", "clone", "--branch", "repograte/batch-1234", remote, str(check)],
        check=True, capture_output=True,
    )
    assert (check / "src" / "A.tsx").read_text() == "function A() { return null; }\n"
    assert (check / "src" / "B.tsx").read_text() == "function B() { return null; }\n"


def test_build_codebase_pr_body_lists_converged_and_wip_files():
    body = build_codebase_pr_body(
        migration_name="react-class-to-hooks",
        converged={"src/A.tsx": "Converted A to hooks."},
        wip={"src/B.tsx": "Still failing tsc after 4 attempts."},
    )
    assert "src/A.tsx" in body
    assert "Converted A to hooks." in body
    assert "src/B.tsx" in body
    assert "Still failing tsc after 4 attempts." in body
    assert "1 file(s) converged; 1 needs manual finishing" in body
