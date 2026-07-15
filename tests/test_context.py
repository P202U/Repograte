from pathlib import Path

from repograte.ingestion.context import (
    _collection_name_for,
    apply_overlay,
    find_candidate_files,
    format_context,
    parse_components,
)


def _write(root: Path, rel_path: str, content: str) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


TSX_EXTENSIONS = [".tsx", ".jsx", ".ts", ".js"]


def test_find_candidate_files_skips_ignored_dirs_and_excluded_file(tmp_path):
    _write(tmp_path, "node_modules/react/index.js", "module.exports = {}")
    _write(
        tmp_path,
        "src/Button.tsx",
        'import React from "react";\nclass Button extends React.Component { render() { return null; } }',
    )
    target = _write(tmp_path, "src/Target.tsx", "class Target extends React.Component {}")

    files = find_candidate_files(tmp_path, extensions=TSX_EXTENSIONS, exclude=target)
    relative = sorted(str(f.relative_to(tmp_path)) for f in files)
    assert relative == ["src/Button.tsx"]


def test_find_candidate_files_respects_max_files(tmp_path):
    for i in range(5):
        _write(tmp_path, f"src/Comp{i}.tsx", "class C extends React.Component {}")
    files = find_candidate_files(tmp_path, extensions=TSX_EXTENSIONS, max_files=2)
    assert len(files) == 2


def test_find_candidate_files_includes_jsx_and_js(tmp_path):
    _write(tmp_path, "src/A.jsx", "class A extends React.Component { render(){return null;} }")
    _write(tmp_path, "src/util.js", "export const x = 1;")
    files = find_candidate_files(tmp_path, extensions=TSX_EXTENSIONS)
    names = {f.name for f in files}
    assert {"A.jsx", "util.js"}.issubset(names)


def test_find_candidate_files_respects_extensions_filter(tmp_path):
    _write(tmp_path, "src/Comp.tsx", "class C extends React.Component {}")
    _write(tmp_path, "script.py", "def f(): pass")
    files = find_candidate_files(tmp_path, extensions=[".py"])
    assert [f.name for f in files] == ["script.py"]


def test_parse_components_skips_unparseable_files_without_raising(tmp_path):
    good = _write(
        tmp_path,
        "src/Good.tsx",
        'import React from "react";\nclass Good extends React.Component { render() { return null; } }',
    )
    # Not actually invalid syntax to tree-sitter (it error-recovers), but an
    # empty/binary-ish file exercises the try/except path regardless.
    weird = _write(tmp_path, "src/Weird.tsx", "\x00\x01not really source")

    results = parse_components([good, weird])
    names = [c.name for _, c in results]
    assert "Good" in names


def test_parse_components_dispatches_by_extension(tmp_path):
    """Regression test: parse_components previously always used TSXParser
    regardless of file type. A .py file must go through the Python adapter."""
    py_file = _write(tmp_path, "worker.py", "class Worker(object):\n    def run(self):\n        pass\n")
    tsx_file = _write(
        tmp_path, "Button.tsx",
        'import React from "react";\nclass Button extends React.Component { render() { return null; } }',
    )
    results = parse_components([py_file, tsx_file])
    names = {c.name for _, c in results}
    assert names == {"Worker", "Button"}


def test_parse_components_falls_back_to_generic_for_unknown_extension(tmp_path):
    rb_file = _write(tmp_path, "script.rb", "def hello\n  puts 'hi'\nend\n")
    results = parse_components([rb_file])
    assert len(results) == 1
    _, component = results[0]
    assert component.type == "file"
    assert component.name == "script"


def test_apply_overlay_writes_files_into_root(tmp_path):
    _write(tmp_path, "src/A.tsx", "old content")
    apply_overlay(tmp_path, {"src/A.tsx": "new content", "src/B.tsx": "brand new file"})
    assert (tmp_path / "src/A.tsx").read_text() == "new content"
    assert (tmp_path / "src/B.tsx").read_text() == "brand new file"



    assert format_context([]) == ""


def test_format_context_includes_component_name_and_location():
    matches = [{"component_name": "Button", "file_path": "src/Button.tsx", "code": "class Button {}"}]
    result = format_context(matches)
    assert "Button" in result
    assert "src/Button.tsx" in result
    assert "class Button {}" in result


def test_collection_name_for_is_stable_and_repo_scoped():
    a = _collection_name_for("https://github.com/owner/repo.git")
    b = _collection_name_for("https://github.com/owner/repo.git")
    c = _collection_name_for("https://github.com/owner/other-repo.git")
    assert a == b
    assert a != c
