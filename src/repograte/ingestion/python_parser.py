import tree_sitter_python as tspy
from tree_sitter import Language, Parser, Query, QueryCursor, Node

from .ast_parser import ASTComponent, ASTMethod

SUPPORTED_EXTENSIONS = (".py",)


class PythonParser:
    """Extracts top-level classes (with their methods) and top-level
    functions from Python source, plus the file's imports as dependencies.
    """

    def __init__(self):
        self.language = Language(tspy.language())
        self.parser = Parser(self.language)

        self.class_query = Query(
            self.language,
            """
            (class_definition
                name: (identifier) @class_name
                body: (block) @body
            ) @full_class
            """,
        )
        self.import_query = Query(
            self.language,
            """
            [
              (import_statement (dotted_name) @mod)
              (import_statement (aliased_import (dotted_name) @mod))
              (import_from_statement module_name: (dotted_name) @mod)
            ]
            """,
        )

    @staticmethod
    def _node_text(node: Node) -> str:
        text = node.text
        if text is None:
            raise ValueError("Tree-sitter node has no text.")
        return text.decode("utf-8")

    def parse_file(self, file_path: str, file_content: bytes) -> list[ASTComponent]:
        tree = self.parser.parse(file_content)
        dependencies = self._extract_dependencies(tree.root_node)

        components: list[ASTComponent] = []

        cursor = QueryCursor(self.class_query)
        for _, caps in cursor.matches(tree.root_node):
            name = self._node_text(caps["class_name"][0])
            body_node = caps["body"][0]
            full_node = caps["full_class"][0]
            components.append(
                ASTComponent(
                    name=name,
                    type="class",
                    methods=self._extract_methods(body_node),
                    dependencies=dependencies,
                    raw_code=self._node_text(full_node),
                )
            )

        for child in tree.root_node.named_children:
            if child.type != "function_definition":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            components.append(
                ASTComponent(
                    name=self._node_text(name_node),
                    type="function",
                    methods=[],
                    dependencies=dependencies,
                    raw_code=self._node_text(child),
                )
            )

        return components

    def _extract_dependencies(self, root_node: Node) -> list[str]:
        cursor = QueryCursor(self.import_query)
        seen: dict[str, None] = {}
        for _, caps in cursor.matches(root_node):
            seen.setdefault(self._node_text(caps["mod"][0]), None)
        return list(seen.keys())

    def _extract_methods(self, class_body_node: Node) -> list[ASTMethod]:
        """Only direct children of the class body - a closure defined
        inside a method is not itself a method."""
        methods: list[ASTMethod] = []
        for child in class_body_node.named_children:
            if child.type != "function_definition":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            methods.append(
                ASTMethod(
                    name=self._node_text(name_node),
                    code_snippet=self._node_text(child),
                    start_line=child.start_point[0],
                    end_line=child.end_point[0],
                )
            )
        return methods
