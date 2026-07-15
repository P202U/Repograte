from repograte.orchestration.codebase import (
    FileTask,
    _resolve_local_import,
    order_by_dependencies,
)


def _task(path, deps=None):
    return FileTask(file_path=path, original_code=f"// {path}", dependencies=deps or [])


def test_resolve_relative_import_to_sibling_file():
    targets = {"src/App.tsx", "src/Button.tsx"}
    assert _resolve_local_import("src/App.tsx", "./Button", targets) == "src/Button.tsx"


def test_resolve_relative_import_up_a_directory():
    targets = {"src/utils/helpers.ts", "src/App.tsx"}
    assert (
        _resolve_local_import("src/App.tsx", "./utils/helpers", targets)
        == "src/utils/helpers.ts"
    )


def test_resolve_index_import():
    targets = {"src/components/index.tsx", "src/App.tsx"}
    assert (
        _resolve_local_import("src/App.tsx", "./components", targets)
        == "src/components/index.tsx"
    )


def test_bare_package_import_is_not_resolved():
    assert _resolve_local_import("src/App.tsx", "react", {"src/Button.tsx"}) is None


def test_relative_import_outside_target_set_is_not_resolved():
    assert _resolve_local_import("src/App.tsx", "./NotMigrated", {"src/Button.tsx"}) is None


def test_order_puts_dependency_before_dependent():
    # App imports Button -> Button must come first.
    app = _task("src/App.tsx", deps=["./Button"])
    button = _task("src/Button.tsx")
    ordered = order_by_dependencies([app, button])
    assert [t.file_path for t in ordered] == ["src/Button.tsx", "src/App.tsx"]


def test_order_is_stable_and_alphabetical_for_independent_files():
    c = _task("src/C.tsx")
    a = _task("src/A.tsx")
    b = _task("src/B.tsx")
    ordered = order_by_dependencies([c, a, b])
    assert [t.file_path for t in ordered] == ["src/A.tsx", "src/B.tsx", "src/C.tsx"]


def test_order_handles_a_chain():
    # A -> B -> C (A imports B, B imports C): C, then B, then A.
    a = _task("src/A.tsx", deps=["./B"])
    b = _task("src/B.tsx", deps=["./C"])
    c = _task("src/C.tsx")
    ordered = order_by_dependencies([a, b, c])
    assert [t.file_path for t in ordered] == ["src/C.tsx", "src/B.tsx", "src/A.tsx"]


def test_order_breaks_cycles_without_hanging():
    # A imports B, B imports A - a real (if messy) pattern in JS/TS.
    a = _task("src/A.tsx", deps=["./B"])
    b = _task("src/B.tsx", deps=["./A"])
    ordered = order_by_dependencies([a, b])
    # No infinite loop, and both files still show up exactly once.
    assert sorted(t.file_path for t in ordered) == ["src/A.tsx", "src/B.tsx"]
    assert len(ordered) == 2


def test_order_ignores_bare_package_imports():
    a = _task("src/A.tsx", deps=["react", "react-dom"])
    b = _task("src/B.tsx")
    ordered = order_by_dependencies([a, b])
    assert [t.file_path for t in ordered] == ["src/A.tsx", "src/B.tsx"]
