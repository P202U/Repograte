import pytest

from repograte.config import settings
from repograte.sandbox.e2b_runner import E2BRunner


class _FakeCommands:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, cmd, cwd=None, timeout=None):
        self.calls.append(cmd)
        from types import SimpleNamespace

        return SimpleNamespace(exit_code=0, stdout="ok", stderr="")


class _FakeFiles:
    def __init__(self):
        self.written: dict[str, str] = {}

    def write(self, path, content):
        self.written[path] = content


class _FakeSandbox:
    def __init__(self):
        self.commands = _FakeCommands()
        self.files = _FakeFiles()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSandboxClass:
    """Stands in for e2b.Sandbox: .create(...) returns a fresh fake sandbox."""

    last_instance: _FakeSandbox | None = None

    @staticmethod
    def create(**kwargs):
        _FakeSandboxClass.last_instance = _FakeSandbox()
        return _FakeSandboxClass.last_instance


@pytest.fixture(autouse=True)
def _fake_sandbox(monkeypatch):
    monkeypatch.setattr("repograte.sandbox.e2b_runner.Sandbox", _FakeSandboxClass)
    yield


def test_files_template_is_substituted_with_repo_paths():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"src/legacy.py": "print('hi')\n"},
        test_cmd="python3 -m py_compile {files}",
    )
    sandbox = _FakeSandboxClass.last_instance
    test_call = sandbox.commands.calls[-1]
    assert test_call == "python3 -m py_compile /repo/src/legacy.py"


def test_files_template_joins_multiple_paths():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"a.py": "x = 1\n", "b.py": "y = 2\n"},
        test_cmd="python3 -m py_compile {files}",
    )
    sandbox = _FakeSandboxClass.last_instance
    test_call = sandbox.commands.calls[-1]
    assert test_call == "python3 -m py_compile /repo/a.py /repo/b.py"


def test_test_cmd_without_files_placeholder_is_unchanged():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"src/App.tsx": "..."},
        test_cmd="npx tsc --noEmit",
    )
    sandbox = _FakeSandboxClass.last_instance
    assert sandbox.commands.calls[-1] == "npx tsc --noEmit"


def test_all_files_are_written_into_the_sandbox():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"src/A.tsx": "content A", "src/B.tsx": "content B"},
    )
    sandbox = _FakeSandboxClass.last_instance
    assert sandbox.files.written == {
        "/repo/src/A.tsx": "content A",
        "/repo/src/B.tsx": "content B",
    }


def test_no_files_raises():
    runner = E2BRunner()
    with pytest.raises(ValueError, match="no files"):
        runner.run_verification(repo_url="https://github.com/example/example.git", files={})


def test_explicit_commands_override_settings_defaults():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"a.py": "x"},
        install_cmd="pip install foo",
        test_cmd="pytest",
    )
    sandbox = _FakeSandboxClass.last_instance
    assert sandbox.commands.calls[-2] == "pip install foo"
    assert sandbox.commands.calls[-1] == "pytest"


def test_falls_back_to_settings_when_no_override_given():
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"a.tsx": "x"},
    )
    sandbox = _FakeSandboxClass.last_instance
    assert sandbox.commands.calls[-2] == settings.sandbox_install_cmd
    assert sandbox.commands.calls[-1] == settings.sandbox_test_cmd


def test_setup_cmds_run_before_install(monkeypatch):
    runner = E2BRunner()
    runner.run_verification(
        repo_url="https://github.com/example/example.git",
        files={"a.py": "x"},
        setup_cmds=["echo setup1", "echo setup2"],
        install_cmd="pip install -r requirements.txt",
        test_cmd="pytest",
    )
    sandbox = _FakeSandboxClass.last_instance
    assert sandbox.commands.calls[-4:] == [
        "echo setup1",
        "echo setup2",
        "pip install -r requirements.txt",
        "pytest",
    ]
