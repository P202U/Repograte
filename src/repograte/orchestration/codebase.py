import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Any

import git
from langgraph.types import Command

from .migration_spec import MigrationSpec
from ..ingestion.context import find_candidate_files, parse_components
from ..vcs.github_utils import build_codebase_pr_body, create_pull_request

ReviewFn = Callable[[dict], dict]


@dataclass
class FileTask:
    file_path: str  # relative to repo root
    original_code: str
    dependencies: list[str] = field(default_factory=list)  # raw import specifiers


@dataclass
class FileResult:
    file_path: str
    outcome: str  # "converged" | "wip" | "skipped"
    reasoning: str = ""
    patched_code: Optional[str] = None


@dataclass
class CodebaseRunSummary:
    converged: dict[str, str] = field(default_factory=dict)  # file_path -> reasoning
    wip: dict[str, str] = field(default_factory=dict)  # file_path -> reasoning
    skipped: list[str] = field(default_factory=list)
    patched_files: dict[str, str] = field(
        default_factory=dict
    )  # file_path -> final content
    pr_url: Optional[str] = None


# Discovery & ordering (pure-ish; only plan_migration touches the network)


def _resolve_local_import(
    importer_rel_path: str, specifier: str, target_paths: set[str]
) -> Optional[str]:
    """Resolution of a relative import specifier (e.g. "./Button"
    imported from src/App.tsx) to one of the other target file paths (e.g.
    "src/Button.tsx"). Returns None for bare/package imports (e.g. "react")
    or anything that doesn't match a file actually in this migration's scope
    - those simply don't constrain ordering."""
    if not specifier.startswith("."):
        return None
    importer_dir = PurePosixPath(importer_rel_path).parent
    base = str(PurePosixPath(str(importer_dir / specifier)))
    candidates = [f"{base}{ext}" for ext in (".tsx", ".jsx", ".ts", ".js", ".py")]
    candidates += [f"{base}/index{ext}" for ext in (".tsx", ".jsx", ".ts", ".js")]
    for c in candidates:
        if c in target_paths:
            return c
    return None


def order_by_dependencies(tasks: list[FileTask]) -> list[FileTask]:
    """Topologically sorts tasks so a file is migrated only after every
    other target file it locally imports - later files can then be written
    consistent with already-migrated ones. Cycles (mutual imports, common
    enough in real code) are broken by falling back to alphabetical order
    for whatever's left once no more progress can be made.
    """
    target_paths = {t.file_path for t in tasks}
    by_path = {t.file_path: t for t in tasks}

    depends_on: dict[str, set[str]] = {}
    for t in tasks:
        local_deps = set()
        for specifier in t.dependencies:
            resolved = _resolve_local_import(t.file_path, specifier, target_paths)
            if resolved and resolved != t.file_path:
                local_deps.add(resolved)
        depends_on[t.file_path] = local_deps

    remaining = set(target_paths)
    ordered: list[FileTask] = []
    while remaining:
        ready = sorted(p for p in remaining if not (depends_on[p] & remaining))
        if not ready:
            ready = [sorted(remaining)[0]]
        for p in ready:
            ordered.append(by_path[p])
            remaining.discard(p)
    return ordered


def plan_migration(
    repo_url: str, branch: Optional[str], spec: MigrationSpec, max_files: int
) -> list[FileTask]:
    """Shallow-clones repo_url, finds every file matching spec that actually
    contains at least one component the migration's language adapter can
    see, and returns them in dependency order."""
    tmp_dir = tempfile.mkdtemp(prefix="repograte-plan-")
    try:
        clone_kwargs: dict[str, Any] = {"depth": 1}
        if branch:
            clone_kwargs["branch"] = branch
        git.Repo.clone_from(repo_url, tmp_dir, **clone_kwargs)
        root = Path(tmp_dir)

        # Cast a wider net than max_files since not every candidate file will
        # actually contain a migratable component - parse_components filters
        # that down to the real target set.
        candidates = find_candidate_files(
            root, extensions=spec.file_extensions, max_files=max_files * 3
        )
        parsed = parse_components(candidates)

        tasks_by_path: dict[str, FileTask] = {}
        for file_path, component in parsed:
            rel_path = str(Path(file_path).relative_to(root))
            if rel_path in tasks_by_path or len(tasks_by_path) >= max_files:
                continue
            tasks_by_path[rel_path] = FileTask(
                file_path=rel_path,
                original_code=(root / rel_path).read_text(),
                dependencies=list(component.dependencies),
            )

        return order_by_dependencies(list(tasks_by_path.values()))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Driving the existing single-file graph, one file at a time


def _pending_interrupt(result: dict):
    interrupts = result.get("__interrupt__")
    return interrupts[0].value if interrupts else None


def run_single_file(
    graph,
    repo_url: str,
    branch: Optional[str],
    spec: MigrationSpec,
    task: FileTask,
    overlay: dict[str, str],
    review_fn: ReviewFn,
    thread_id_prefix: str,
) -> FileResult:
    """Runs one file through the existing single-file graph to completion
    (with defer_publish=True, so nothing gets pushed per-file), handling the
    human_review interrupt(s) via `review_fn`.

    `review_fn` receives the same interrupt payload run_cli.py prints, and
    must return one of:
        {"approved": True}
        {"approved": False, "feedback": "..."}   -> another attempt
        {"skip": True}                            -> abandon this file
    """
    thread_id = f"{thread_id_prefix}:{task.file_path}"
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)

    if not snapshot.next:
        # Either a brand-new thread, or one that already ran to completion
        # in an earlier (crashed) run of this same codebase-wide command -
        # reuse that result instead of silently re-migrating it.
        if snapshot.values.get("patched_code") is not None:
            status = snapshot.values.get("status")
            diff = snapshot.values.get("current_diff")
            return FileResult(
                file_path=task.file_path,
                outcome="wip" if status == "failed_wip" else "converged",
                reasoning=diff.reasoning if diff else "",
                patched_code=snapshot.values["patched_code"],
            )
        state = {
            "file_path": task.file_path,
            "original_code": task.original_code,
            "repo_url": repo_url,
            "branch": branch,
            "loop_count": 0,
            "errors": [],
            "diff_history": [],
            "messages": [],
            "migration_spec": spec.model_dump(),
            "repo_overlay": overlay,
            "defer_publish": True,
        }
        result = graph.invoke(state, config=config)
        pending = _pending_interrupt(result)
    elif snapshot.tasks and snapshot.tasks[0].interrupts:
        pending = snapshot.tasks[0].interrupts[0].value
    else:
        pending = None

    while pending is not None:
        decision = review_fn(pending)
        if decision.get("skip"):
            return FileResult(
                file_path=task.file_path,
                outcome="skipped",
                reasoning="Skipped by reviewer.",
            )
        result = graph.invoke(Command(resume=decision), config=config)
        pending = _pending_interrupt(result)

    final_values = graph.get_state(config).values
    patched_code = final_values.get("patched_code")
    if patched_code is None:
        return FileResult(
            file_path=task.file_path, outcome="skipped", reasoning="No patch produced."
        )

    status = final_values.get("status")
    diff = final_values.get("current_diff")
    return FileResult(
        file_path=task.file_path,
        outcome="wip" if status == "failed_wip" else "converged",
        reasoning=diff.reasoning if diff else "",
        patched_code=patched_code,
    )


def run_codebase_migration(
    graph,
    repo_url: str,
    branch: Optional[str],
    spec: MigrationSpec,
    tasks: list[FileTask],
    review_fn: ReviewFn,
    thread_id_prefix: Optional[str] = None,
    on_file_done: Optional[Callable[[FileResult], None]] = None,
) -> CodebaseRunSummary:
    """Drives `tasks` through run_single_file in order, feeding each
    approved/wip file's patch forward as the next file's repo_overlay.
    `on_file_done` (optional) lets the CLI print progress after each file."""
    thread_id_prefix = thread_id_prefix or repo_url
    summary = CodebaseRunSummary()
    overlay: dict[str, str] = {}

    for task in tasks:
        result = run_single_file(
            graph=graph,
            repo_url=repo_url,
            branch=branch,
            spec=spec,
            task=task,
            overlay=overlay,
            review_fn=review_fn,
            thread_id_prefix=thread_id_prefix,
        )

        if result.outcome == "skipped" or result.patched_code is None:
            summary.skipped.append(task.file_path)
        else:
            summary.patched_files[task.file_path] = result.patched_code
            overlay[task.file_path] = result.patched_code
            if result.outcome == "converged":
                summary.converged[task.file_path] = result.reasoning
            else:
                summary.wip[task.file_path] = result.reasoning

        if on_file_done:
            on_file_done(result)

    return summary


def publish_codebase_pr(
    repo_url: str, branch: str, spec: MigrationSpec, summary: CodebaseRunSummary
) -> Optional[str]:
    """Opens one PR covering every approved (converged or WIP) file. Returns
    None (and opens nothing) if every file was skipped."""
    if not summary.patched_files:
        return None

    is_wip = bool(summary.wip)
    branch_name = f"repograte/{spec.name}-{uuid.uuid4().hex[:8]}"
    pr_body = build_codebase_pr_body(
        migration_name=spec.name, converged=summary.converged, wip=summary.wip
    )
    n = len(summary.patched_files)
    pr_url = create_pull_request(
        repo_url=repo_url,
        base_branch=branch,
        files=summary.patched_files,
        branch_name=branch_name,
        commit_message=f"Repograte: {spec.name} across {n} file(s)",
        pr_title=f"{'[WIP] ' if is_wip else ''}Repograte: {spec.name} ({n} files)",
        pr_body=pr_body,
        draft=is_wip,
    )
    summary.pr_url = pr_url
    return pr_url
