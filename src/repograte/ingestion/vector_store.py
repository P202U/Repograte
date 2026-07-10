import logging
import uuid
from qdrant_client import QdrantClient

from ..config import settings
from .ast_parser import ASTComponent

logger = logging.getLogger(__name__)


class CodeIndexer:
    def __init__(self, collection_name: str = "repograte_ast"):
        if settings.qdrant_url:
            self.client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
            )
        else:
            self.client = QdrantClient(":memory:")
        self.client.set_model("BAAI/bge-small-en-v1.5")
        self.collection_name = collection_name

        self._ensure_collection()

    def _ensure_collection(self):
        """Creates the Qdrant collection if it doesn't exist."""
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self.client.get_fastembed_vector_params(),
            )

    def index_component(self, file_path: str, component: ASTComponent):
        """Embeds and indexes a component and its methods into Qdrant."""
        documents = []
        metadata = []
        ids = []

        # 1. Indexes the class as a whole summary
        documents.append(f"Class: {component.name}\n{component.raw_code}")
        metadata.append(
            {
                "type": "class_summary",
                "file_path": file_path,
                "component_name": component.name,
                "code": component.raw_code,
                "dependencies": component.dependencies,
            }
        )
        ids.append(str(uuid.uuid4()))

        # 2. Indexes individual methods for hyper-specific context retrieval
        for method in component.methods:
            documents.append(
                f"Method: {method.name} in {component.name}\n{method.code_snippet}"
            )
            metadata.append(
                {
                    "type": "method",
                    "file_path": file_path,
                    "component_name": component.name,
                    "method_name": method.name,
                    "code": method.code_snippet,
                    "lines": f"{method.start_line}-{method.end_line}",
                }
            )
            ids.append(str(uuid.uuid4()))

        self.client.add(
            collection_name=self.collection_name,
            documents=documents,
            metadata=metadata,
            ids=ids,
        )
        logger.debug(
            "Indexed %s and %d methods.", component.name, len(component.methods)
        )

    def retrieve_context(self, query: str, limit: int = 3) -> list[dict]:
        """Allows the Architect Agent to search the codebase semantically."""
        search_result = self.client.query(
            collection_name=self.collection_name, query_text=query, limit=limit
        )
        return [hit.metadata for hit in search_result]
