from pathlib import Path
from typing import Protocol

from .ast_parser import ASTComponent, TSXParser
from .python_parser import PythonParser


class LanguageAdapter(Protocol):
    def parse_file(self, file_path: str, file_content: bytes) -> list[ASTComponent]: ...


class GenericTextAdapter:
    """No structural parsing: the whole file becomes one pseudo-component.
    Used for any extension without a dedicated adapter, so an unfamiliar
    language still gets *something* useful out of repo-context retrieval."""

    def parse_file(self, file_path: str, file_content: bytes) -> list[ASTComponent]:
        try:
            text = file_content.decode("utf-8")
        except UnicodeDecodeError:
            return []
        if not text.strip():
            return []
        return [
            ASTComponent(
                name=Path(file_path).stem,
                type="file",
                methods=[],
                dependencies=[],
                raw_code=text,
            )
        ]


_TSX = TSXParser()
_PYTHON = PythonParser()
_GENERIC = GenericTextAdapter()

ADAPTERS: dict[str, LanguageAdapter] = {
    "tsx": _TSX,
    "python": _PYTHON,
    "generic": _GENERIC,
}

_BY_EXTENSION: dict[str, LanguageAdapter] = {
    ".tsx": _TSX,
    ".jsx": _TSX,
    ".ts": _TSX,
    ".js": _TSX,
    ".py": _PYTHON,
}


def get_adapter(name: str) -> LanguageAdapter:
    """Looks up an adapter by MigrationSpec.language_adapter key."""
    return ADAPTERS.get(name, _GENERIC)


def get_adapter_for_extension(extension: str) -> LanguageAdapter:
    """Looks up an adapter by file extension, falling back to the generic
    text adapter for anything unrecognized."""
    return _BY_EXTENSION.get(extension, _GENERIC)
