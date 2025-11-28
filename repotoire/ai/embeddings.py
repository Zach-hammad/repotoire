"""Code-aware embedding generation with backend flexibility.

Supports OpenAI (high quality, paid) and local sentence-transformers (free, faster).
"""

from typing import List, Optional, Literal
from dataclasses import dataclass

from repotoire.models import Entity, FunctionEntity, ClassEntity, FileEntity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# Backend configurations with defaults
BACKEND_CONFIGS = {
    "openai": {"dimensions": 1536, "model": "text-embedding-3-small"},
    "local": {"dimensions": 384, "model": "all-MiniLM-L6-v2"},
}


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation."""

    backend: Literal["openai", "local"] = "openai"
    model: Optional[str] = None  # Uses backend default if not specified
    batch_size: int = 100
    include_context: bool = True  # Include surrounding code context
    max_code_length: int = 2000  # Max characters of code to embed

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions for the configured backend."""
        return BACKEND_CONFIGS[self.backend]["dimensions"]

    @property
    def effective_model(self) -> str:
        """Get the effective model name (user-specified or backend default)."""
        return self.model or BACKEND_CONFIGS[self.backend]["model"]


class CodeEmbedder:
    """Generate semantic embeddings for code entities.

    Supports two backends:
    - OpenAI (default): High quality embeddings via API ($0.13/1M tokens)
    - Local: Free, fast embeddings via sentence-transformers (~85-90% quality)

    Example:
        >>> # OpenAI backend (default)
        >>> embedder = CodeEmbedder()
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1536

        >>> # Local backend (free, no API key required)
        >>> embedder = CodeEmbedder(backend="local")
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        384
    """

    def __init__(
        self,
        config: Optional[EmbeddingConfig] = None,
        backend: Literal["openai", "local"] = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize code embedder.

        Args:
            config: Embedding configuration (uses defaults if not provided)
            backend: Backend to use ("openai" or "local"), ignored if config provided
            model: Model name override, ignored if config provided
            api_key: OpenAI API key (uses OPENAI_API_KEY env var if not provided)
        """
        # Build config from parameters if not provided
        if config is None:
            config = EmbeddingConfig(backend=backend, model=model)
        self.config = config

        # Store dimensions for external access
        self.dimensions = self.config.dimensions

        # Initialize the appropriate backend
        if self.config.backend == "local":
            self._init_local()
        else:
            self._init_openai(api_key)

        logger.info(
            f"Initialized CodeEmbedder with backend={self.config.backend}, "
            f"model={self.config.effective_model}, dimensions={self.dimensions}"
        )

    def _init_local(self) -> None:
        """Initialize local sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers required for local backend. "
                "Install with: pip install repotoire[local-embeddings]"
            )

        model_name = self.config.effective_model
        logger.info(f"Loading local model: {model_name}")
        self._model = SentenceTransformer(model_name)

        # Update dimensions from actual model (may differ from config default)
        actual_dims = self._model.get_sentence_embedding_dimension()
        if actual_dims != self.dimensions:
            logger.info(f"Updating dimensions from {self.dimensions} to {actual_dims}")
            self.dimensions = actual_dims

    def _init_openai(self, api_key: Optional[str]) -> None:
        """Initialize OpenAI embeddings via neo4j-graphrag."""
        from neo4j_graphrag.embeddings import OpenAIEmbeddings

        self._embeddings = OpenAIEmbeddings(
            model=self.config.effective_model,
            api_key=api_key,
        )

    def embed_entity(self, entity: Entity) -> List[float]:
        """Generate embedding for a single code entity.

        Creates a rich text representation of the entity including:
        - Entity type and name
        - Docstring/documentation
        - Code signature (for functions/classes)
        - Contextual information

        Args:
            entity: Entity to embed

        Returns:
            Embedding vector (dimensions depend on backend)
        """
        text = self._entity_to_text(entity)
        return self.embed_query(text)

    def embed_entities_batch(
        self,
        entities: List[Entity]
    ) -> List[List[float]]:
        """Generate embeddings for multiple entities efficiently.

        Uses batch processing for better performance with many entities.

        Args:
            entities: List of entities to embed

        Returns:
            List of embedding vectors (one per entity)
        """
        # Convert entities to text representations
        texts = [self._entity_to_text(entity) for entity in entities]

        # Use batch embedding
        embeddings = self.embed_batch(texts)

        logger.info(f"Generated embeddings for {len(entities)} entities")
        return embeddings

    def embed_query(self, query: str) -> List[float]:
        """Embed a natural language query for semantic search.

        Args:
            query: Natural language question about code

        Returns:
            Embedding vector (dimensions depend on backend)
        """
        if self.config.backend == "local":
            return self._embed_local([query])[0]
        else:
            return self._embeddings.embed_query(query)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts efficiently.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if self.config.backend == "local":
            return self._embed_local(texts)
        else:
            # neo4j-graphrag doesn't have native batch, so we iterate
            return [self._embeddings.embed_query(text) for text in texts]

    def _embed_local(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using local sentence-transformers model.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def _entity_to_text(self, entity: Entity) -> str:
        """Convert entity to rich text representation for embedding.

        Different entity types get different text representations to
        capture their semantic meaning accurately.

        Args:
            entity: Entity to convert

        Returns:
            Text representation suitable for embedding
        """
        parts = []

        # Add entity type
        if entity.node_type:
            parts.append(f"Type: {entity.node_type.value}")

        # Add name
        parts.append(f"Name: {entity.name}")

        # Add type-specific information
        if isinstance(entity, FunctionEntity):
            parts.extend(self._function_context(entity))
        elif isinstance(entity, ClassEntity):
            parts.extend(self._class_context(entity))
        elif isinstance(entity, FileEntity):
            parts.extend(self._file_context(entity))

        # Add docstring if present
        if entity.docstring:
            parts.append(f"Documentation: {entity.docstring}")

        # Add file path for context
        parts.append(f"Location: {entity.file_path}")

        # Join all parts
        text = "\n".join(parts)

        # Truncate if too long
        if len(text) > self.config.max_code_length:
            text = text[: self.config.max_code_length] + "..."

        return text

    def _function_context(self, func: FunctionEntity) -> List[str]:
        """Extract function-specific context for embedding.

        Args:
            func: Function entity

        Returns:
            List of context strings
        """
        parts = []

        # Signature
        params_str = ", ".join(func.parameters)
        signature = f"def {func.name}({params_str})"
        if func.return_type:
            signature += f" -> {func.return_type}"
        parts.append(f"Signature: {signature}")

        # Function characteristics
        characteristics = []
        if func.is_async:
            characteristics.append("async")
        if func.is_static:
            characteristics.append("staticmethod")
        if func.is_classmethod:
            characteristics.append("classmethod")
        if func.is_property:
            characteristics.append("property")
        if func.is_method:
            characteristics.append("method")
        else:
            characteristics.append("function")

        if characteristics:
            parts.append(f"Characteristics: {', '.join(characteristics)}")

        # Decorators
        if func.decorators:
            parts.append(f"Decorators: {', '.join(func.decorators)}")

        # Complexity hint
        if func.complexity > 10:
            parts.append(f"Complexity: {func.complexity} (complex)")
        elif func.complexity > 5:
            parts.append(f"Complexity: {func.complexity} (moderate)")

        return parts

    def _class_context(self, cls: ClassEntity) -> List[str]:
        """Extract class-specific context for embedding.

        Args:
            cls: Class entity

        Returns:
            List of context strings
        """
        parts = []

        # Note: Base class information is stored in graph relationships (INHERITS),
        # not as a property. To include inheritance info, would need graph query.

        # Class characteristics
        characteristics = []
        if cls.is_abstract:
            characteristics.append("abstract")
        if cls.is_dataclass:
            characteristics.append("dataclass")
        if cls.is_exception:
            characteristics.append("exception")

        if characteristics:
            parts.append(f"Class type: {', '.join(characteristics)}")

        # Decorators
        if cls.decorators:
            parts.append(f"Decorators: {', '.join(cls.decorators)}")

        return parts

    def _file_context(self, file: FileEntity) -> List[str]:
        """Extract file-specific context for embedding.

        Args:
            file: File entity

        Returns:
            List of context strings
        """
        parts = []

        # Language
        parts.append(f"Language: {file.language}")

        # Size hint
        if file.loc:
            if file.loc > 500:
                parts.append(f"Size: {file.loc} LOC (large file)")
            elif file.loc > 100:
                parts.append(f"Size: {file.loc} LOC (medium file)")
            else:
                parts.append(f"Size: {file.loc} LOC (small file)")

        # Exports
        if file.exports:
            parts.append(f"Exports: {', '.join(file.exports[:10])}")  # First 10

        return parts


def create_embedder(
    backend: Literal["openai", "local"] = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CodeEmbedder:
    """Factory function to create a configured CodeEmbedder.

    Args:
        backend: Backend to use ("openai" or "local")
        model: Model name override (uses backend default if not provided)
        api_key: Optional OpenAI API key (uses env var if not provided)

    Returns:
        Configured CodeEmbedder instance
    """
    config = EmbeddingConfig(backend=backend, model=model)
    return CodeEmbedder(config=config, api_key=api_key)


def get_embedding_dimensions(backend: Literal["openai", "local"] = "openai") -> int:
    """Get the embedding dimensions for a backend.

    Useful for schema creation before embedder is instantiated.

    Args:
        backend: Backend to get dimensions for

    Returns:
        Embedding dimensions (1536 for OpenAI, 384 for local)
    """
    return BACKEND_CONFIGS[backend]["dimensions"]
