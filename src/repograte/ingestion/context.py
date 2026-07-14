import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Any

import git

from .ast_parser import ASTComponent
from .languages import get_adapter_for_extension
from .vector_store import CodeIndexer

logger = logging.getLogger(__name__)

# Directories never worth walking into: dependency trees, build output, VCS internals.
IGNORED_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".git",
    ".next",
    "coverage",
    "out",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "egg-info",
}


def apply_overlay(root: Path, overlay: dict[str, str]) -> None:
    """Writes already-approved patched content (from earlier files in a
    codebase-wide run) into a freshly-cloned working tree, so anything that
    reads from `root` afterwards - context retrieval, sandbox verification -
    sees the cumulative state of the migration so far rather than the
    pristine original."""
    for rel_path, content in overlay.items():
        full_path = root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)


def find_candidate_files(
    root: Path,
    extensions: list[str],
    exclude: Optional[Path] = None,
    max_files: int = 40,
) -> list[Path]:
    """Returns up to `max_files` files under `root` matching `extensions`,
    skipping vendored/build directories and (if given) the file currently
    being migrated."""
    candidates: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(candidates) >= max_files:
            break
        if not path.is_file() or path.suffix not in extensions:
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        candidates.append(path)
    return candidates


def parse_components(files: list[Path]) -> list[tuple[str, ASTComponent]]:
    """Parses each file with whichever adapter matches its extension
    (falling back to the generic text adapter), skipping files that fail to
    parse rather than aborting the whole batch over one bad file."""
    results: list[tuple[str, ASTComponent]] = []
    for path in files:
        adapter = get_adapter_for_extension(path.suffix)
        try:
            content = path.read_bytes()
            for component in adapter.parse_file(str(path), content):
                results.append((str(path), component))
        except Exception:
            logger.warning("Skipping %s: failed to parse.", path, exc_info=True)
    return results


def format_context(matches: list[dict]) -> str:
    """Formats retrieved sibling-component snippets for the Architect prompt.
    Returns "" (not a header with no body) when there's nothing to show, so
    callers can just check truthiness."""
    if not matches:
        return ""
    parts = ["Related components already in this codebase (for consistency):"]
    for m in matches:
        label = m.get("component_name", "?")
        location = m.get("file_path", "?")
        code = (m.get("code") or "").strip()
        parts.append(f"\n- {label} ({location}):\n{code[:800]}")
    return "\n".join(parts)


def _collection_name_for(repo_url: str) -> str:
    """A stable, repo-scoped collection name so a persistent Qdrant Cloud
    deployment doesn't mix retrieval results across unrelated repos."""
    digest = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:12]
    return f"repograte-{digest}"


def gather_repo_context(
    repo_url: str,
    branch: Optional[str],
    current_file_path: str,
    current_code: str,
    file_extensions: list[str],
    overlay: Optional[dict[str, str]] = None,
    max_files: int = 40,
) -> str:
    """Shallow-clones `repo_url`, applies `overlay` (already-approved patches
    from earlier files in this run, if any) on top, indexes sibling
    components matching `file_extensions`, and returns formatted context
    describing the ones most similar to `current_code`. Returns "" on any
    failure or if nothing useful is found.
    """
    tmp_dir = tempfile.mkdtemp(prefix="repograte-context-")
    try:
        clone_kwargs: dict[str, Any] = {"depth": 1}
        if branch:
            clone_kwargs["branch"] = branch
        git.Repo.clone_from(repo_url, tmp_dir, **clone_kwargs)

        root = Path(tmp_dir)
        if overlay:
            apply_overlay(root, overlay)

        exclude = root / current_file_path
        files = find_candidate_files(
            root, extensions=file_extensions, exclude=exclude, max_files=max_files
        )
        components = parse_components(files)
        if not components:
            return ""

        # A fresh in-process instance per call; CodeIndexer itself decides
        # in-memory vs. Qdrant Cloud based on settings.qdrant_url.
        indexer = CodeIndexer(collection_name=_collection_name_for(repo_url))
        for file_path, component in components:
            indexer.index_component(file_path, component)

        matches = indexer.retrieve_context(current_code, limit=3)
        return format_context(matches)
    except Exception:
        logger.warning(
            "Repo context retrieval failed for %s; continuing without it.",
            repo_url,
            exc_info=True,
        )
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
