from repograte.ingestion.ast_parser import TSXParser
from repograte.ingestion.languages import (
    GenericTextAdapter,
    get_adapter,
    get_adapter_for_extension,
)
from repograte.ingestion.python_parser import PythonParser


def test_get_adapter_by_spec_key():
    assert isinstance(get_adapter("tsx"), TSXParser)
    assert isinstance(get_adapter("python"), PythonParser)
    assert isinstance(get_adapter("generic"), GenericTextAdapter)


def test_get_adapter_unknown_key_falls_back_to_generic():
    assert isinstance(get_adapter("cobol"), GenericTextAdapter)


def test_get_adapter_for_extension():
    assert isinstance(get_adapter_for_extension(".tsx"), TSXParser)
    assert isinstance(get_adapter_for_extension(".py"), PythonParser)
    assert isinstance(get_adapter_for_extension(".rb"), GenericTextAdapter)


def test_generic_adapter_treats_whole_file_as_one_component():
    adapter = GenericTextAdapter()
    components = adapter.parse_file("script.rb", b"def hello\n  puts 'hi'\nend\n")
    assert len(components) == 1
    assert components[0].name == "script"
    assert components[0].type == "file"
    assert "puts" in components[0].raw_code


def test_generic_adapter_skips_empty_and_undecodable_files():
    adapter = GenericTextAdapter()
    assert adapter.parse_file("empty.rb", b"") == []
    assert adapter.parse_file("binary.dat", b"\xff\xfe\x00\x01") == []
