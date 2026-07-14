from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "migrations"


class MigrationSpec(BaseModel):
    name: str
    description: str = ""
    file_extensions: list[str]
    # Key into ingestion.languages.ADAPTERS ("tsx", "python", or "generic").
    language_adapter: str = "generic"

    architect_prompt: str
    engineer_prompt: str
    qa_prompt: str
    debugger_prompt: str

    sandbox_install_cmd: str
    sandbox_test_cmd: str
    sandbox_setup_cmds: list[str] = Field(default_factory=list)

    def matches(self, file_path: str) -> bool:
        return any(file_path.endswith(ext) for ext in self.file_extensions)


def _builtin_path(name: str) -> Path:
    # "react-class-to-hooks" -> react_class_to_hooks.yaml
    return _BUILTIN_DIR / f"{name.replace('-', '_')}.yaml"


def list_builtin_specs() -> list[str]:
    return sorted(p.stem.replace("_", "-") for p in _BUILTIN_DIR.glob("*.yaml"))


def load_migration_spec(name_or_path: str) -> MigrationSpec:
    """Loads a built-in spec by name (e.g. "react-class-to-hooks") if one
    exists under src/repograte/migrations/, otherwise treats the argument as
    a filesystem path to a YAML file."""
    builtin = _builtin_path(name_or_path)
    path = builtin if builtin.exists() else Path(name_or_path)

    if not path.exists():
        available = ", ".join(list_builtin_specs())
        raise FileNotFoundError(
            f"No migration spec named or at {name_or_path!r}. "
            f"Built-in specs: {available}. Or pass a path to your own YAML file."
        )

    with open(path) as f:
        data = yaml.safe_load(f)
    return MigrationSpec.model_validate(data)
