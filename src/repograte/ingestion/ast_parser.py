import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Query, QueryCursor, Node
from pydantic import BaseModel, Field
from typing import List


# Data Models
class ASTMethod(BaseModel):
    name: str
    code_snippet: str
    start_line: int
    end_line: int


class ASTComponent(BaseModel):
    name: str
    type: str = "class_component"
    methods: List[ASTMethod]
    dependencies: List[str] = Field(default_factory=list)
    raw_code: str


# Parser Implementation
class TSXParser:
    def __init__(self):
        self.language = Language(tsts.language_tsx())
        self.parser = Parser(self.language)

        # Compiled once; reused for every parse_file() call.
        self.class_query = Query(
            self.language,
            """
            (class_declaration
                name: (type_identifier) @class_name
                body: (class_body) @body
            ) @full_class
            """,
        )

    @staticmethod
    def _node_text(node: Node) -> str:
        """Safely decode the text of a Tree-sitter node."""
        text = node.text
        if text is None:
            raise ValueError("Tree-sitter node has no text.")
        return text.decode("utf-8")

    def parse_file(self, file_path: str, file_content: bytes) -> List[ASTComponent]:
        tree = self.parser.parse(file_content)

        cursor = QueryCursor(self.class_query)
        matches = cursor.matches(tree.root_node)

        components: List[ASTComponent] = []

        for _, match_captures in matches:
            class_name = self._node_text(match_captures["class_name"][0])
            body_node = match_captures["body"][0]
            full_node = match_captures["full_class"][0]

            components.append(
                ASTComponent(
                    name=class_name,
                    methods=self._extract_methods(body_node),
                    raw_code=self._node_text(full_node),
                )
            )

        return components

    def _extract_methods(self, class_body_node: Node) -> List[ASTMethod]:
        methods: List[ASTMethod] = []

        for child in class_body_node.named_children:
            if child.type != "method_definition":
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


if __name__ == "__main__":
    sample = b"""
    class Foo extends React.Component {
        render() {
            return <div>foo</div>;
        }

        handleClick() {
            console.log("click");
        }
    }

    class FooBar extends React.Component {
        render() {
            return <span>bar</span>;
        }
    }
    """

    parser = TSXParser()
    result = parser.parse_file("sample.tsx", sample)

    for component in result:
        print(component.name, "->", [method.name for method in component.methods])
