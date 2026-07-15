import pytest

from repograte.orchestration.migration_spec import list_builtin_specs, load_migration_spec


def test_lists_both_builtin_specs():
    assert set(list_builtin_specs()) == {"react-class-to-hooks", "python2-to-3"}


def test_loads_react_spec():
    spec = load_migration_spec("react-class-to-hooks")
    assert spec.file_extensions == [".tsx", ".jsx", ".ts", ".js"]
    assert spec.language_adapter == "tsx"
    assert "Hooks" in spec.architect_prompt


def test_loads_python_spec():
    spec = load_migration_spec("python2-to-3")
    assert spec.file_extensions == [".py"]
    assert spec.language_adapter == "python"
    assert "{files}" in spec.sandbox_test_cmd


def test_matches_checks_extension():
    spec = load_migration_spec("python2-to-3")
    assert spec.matches("src/legacy.py")
    assert not spec.matches("src/App.tsx")


def test_unknown_name_raises_with_helpful_message():
    with pytest.raises(FileNotFoundError, match="react-class-to-hooks"):
        load_migration_spec("not-a-real-spec")


def test_loads_from_arbitrary_path(tmp_path):
    custom = tmp_path / "my-migration.yaml"
    custom.write_text(
        """
        name: my-migration
        file_extensions: [".rb"]
        language_adapter: generic
        architect_prompt: "plan it"
        engineer_prompt: "do it"
        qa_prompt: "check it"
        debugger_prompt: "fix it"
        sandbox_install_cmd: "bundle install"
        sandbox_test_cmd: "ruby -c {files}"
        """
    )
    spec = load_migration_spec(str(custom))
    assert spec.name == "my-migration"
    assert spec.file_extensions == [".rb"]
