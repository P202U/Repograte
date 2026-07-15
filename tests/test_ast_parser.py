from repograte.ingestion.ast_parser import TSXParser


def test_extracts_class_components_and_methods():
    parser = TSXParser()
    sample = b"""
    class Foo extends React.Component {
        render() { return <div>foo</div>; }
        handleClick() { console.log("click"); }
    }
    """
    components = parser.parse_file("Foo.tsx", sample)
    assert len(components) == 1
    assert components[0].name == "Foo"
    assert {m.name for m in components[0].methods} == {"render", "handleClick"}


def test_dependencies_are_extracted_from_imports():
    """Regression test: ASTComponent.dependencies previously always defaulted
    to [] and was never populated anywhere."""
    parser = TSXParser()
    sample = b"""
    import React from "react";
    import PropTypes from "prop-types";
    import { connect } from "react-redux";

    class Foo extends React.Component {
        render() { return <div />; }
    }
    """
    components = parser.parse_file("Foo.tsx", sample)
    assert components[0].dependencies == ["react", "prop-types", "react-redux"]


def test_duplicate_imports_are_deduplicated():
    parser = TSXParser()
    sample = b"""
    import React from "react";
    import { useState } from "react";
    class Foo extends React.Component { render() { return null; } }
    """
    components = parser.parse_file("Foo.tsx", sample)
    assert components[0].dependencies == ["react"]


def test_parses_plain_jsx_without_type_annotations():
    """Regression test: the parser was previously undocumented as handling
    anything but .tsx; the TSX grammar is a superset of plain JS/JSX though,
    so a .jsx file with no TypeScript syntax should parse identically."""
    parser = TSXParser()
    sample = b"""
    import React from "react";
    class Card extends React.Component {
        render() { return <div className="card">hi</div>; }
    }
    """
    components = parser.parse_file("Card.jsx", sample)
    assert len(components) == 1
    assert components[0].name == "Card"


def test_multiple_components_in_one_file_share_the_files_dependencies():
    parser = TSXParser()
    sample = b"""
    import React from "react";
    class A extends React.Component { render() { return null; } }
    class B extends React.Component { render() { return null; } }
    """
    components = parser.parse_file("multi.tsx", sample)
    assert [c.name for c in components] == ["A", "B"]
    assert all(c.dependencies == ["react"] for c in components)


def test_no_components_found_returns_empty_list():
    parser = TSXParser()
    sample = b"""
    import React from "react";
    function Foo() { return <div />; }
    """
    assert parser.parse_file("Foo.tsx", sample) == []
