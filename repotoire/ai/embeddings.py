"""Code-aware embedding generation with backend flexibility.

Supports:
- OpenAI: High quality embeddings via API ($0.02/1M tokens)
- DeepInfra: Cheap, high-quality Qwen3 embeddings (~$0.01/1M tokens)
- Local: Free, high-quality embeddings via Qwen3-Embedding-0.6B

Environment variables:
- OPENAI_API_KEY: Required for 'openai' backend
- DEEPINFRA_API_KEY: Required for 'deepinfra' backend
- No key needed for 'local' backend
"""

import os
from typing import List, Optional, Literal
from dataclasses import dataclass

from repotoire.models import Entity, FunctionEntity, ClassEntity, FileEntity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for embedding backends
EmbeddingBackend = Literal["openai", "local", "deepinfra"]

# Backend configurations with defaults
BACKEND_CONFIGS = {
    "openai": {"dimensions": 1536, "model": "text-embedding-3-small"},
    "local": {
        "dimensions": 1024,
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "fallback_model": "all-MiniLM-L6-v2",
        "fallback_dimensions": 384,
    },
    "deepinfra": {
        "dimensions": 4096,
        "model": "Qwen/Qwen3-Embedding-8B",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "env_key": "DEEPINFRA_API_KEY",
    },
}


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation."""

    backend: EmbeddingBackend = "openai"
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

    Supports three backends:
    - OpenAI (default): High quality embeddings via API ($0.02/1M tokens)
    - DeepInfra: Cheap, high-quality Qwen3 embeddings (~$0.01/1M tokens)
    - Local: Free, high-quality embeddings via Qwen3-Embedding-0.6B (MTEB-Code #1)

    Example:
        >>> # OpenAI backend (default)
        >>> embedder = CodeEmbedder()
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1536

        >>> # DeepInfra backend (cheap API)
        >>> embedder = CodeEmbedder(backend="deepinfra")
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1024

        >>> # Local backend (free, no API key required)
        >>> embedder = CodeEmbedder(backend="local")
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1024
    """

    def __init__(
        self,
        config: Optional[EmbeddingConfig] = None,
        backend: EmbeddingBackend = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize code embedder.

        Args:
            config: Embedding configuration (uses defaults if not provided)
            backend: Backend to use ("openai", "local", or "deepinfra"), ignored if config provided
            model: Model name override, ignored if config provided
            api_key: API key for OpenAI/DeepInfra (uses env vars if not provided)
        """
        # Build config from parameters if not provided
        if config is None:
            config = EmbeddingConfig(backend=backend, model=model)
        self.config = config
        self._api_key = api_key

        # Store dimensions for external access
        self.dimensions = self.config.dimensions

        # Initialize the appropriate backend
        if self.config.backend == "local":
            self._init_local()
        elif self.config.backend == "deepinfra":
            self._init_deepinfra()
        else:
            self._init_openai(api_key)

        logger.info(
            f"Initialized CodeEmbedder with backend={self.config.backend}, "
            f"model={self.config.effective_model}, dimensions={self.dimensions}"
        )

    def _init_local(self) -> None:
        """Initialize local sentence-transformers model with fallback support."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers required for local backend. "
                "Install with: pip install repotoire[local-embeddings]"
            )

        model_name = self.config.effective_model
        config = BACKEND_CONFIGS["local"]
        fallback_model = config.get("fallback_model")
        fallback_dimensions = config.get("fallback_dimensions")

        logger.info(f"Loading local model: {model_name}")

        try:
            self._model = SentenceTransformer(model_name)
        except Exception as e:
            # Fallback to MiniLM for low-memory systems or download issues
            if fallback_model and model_name != fallback_model:
                logger.warning(
                    f"Failed to load {model_name}, falling back to {fallback_model}: {e}"
                )
                self._model = SentenceTransformer(fallback_model)
                if fallback_dimensions:
                    self.dimensions = fallback_dimensions
            else:
                raise

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

    def _init_deepinfra(self) -> None:
        """Initialize DeepInfra embeddings via OpenAI-compatible API."""
        config = BACKEND_CONFIGS["deepinfra"]
        env_key = config["env_key"]

        api_key = self._api_key or os.getenv(env_key)
        if not api_key:
            raise ValueError(
                f"{env_key} environment variable required for deepinfra backend. "
                f"Get your API key at https://deepinfra.com"
            )

        # Store for later use in embed methods
        self._deepinfra_api_key = api_key
        self._deepinfra_base_url = config["base_url"]

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
        elif self.config.backend == "deepinfra":
            return self._embed_deepinfra([query])[0]
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
        elif self.config.backend == "deepinfra":
            return self._embed_deepinfra(texts)
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

    def _embed_deepinfra(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using DeepInfra's OpenAI-compatible API.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        from openai import OpenAI

        client = OpenAI(
            api_key=self._deepinfra_api_key,
            base_url=self._deepinfra_base_url,
        )

        response = client.embeddings.create(
            model=self.config.effective_model,
            input=texts,
        )

        return [e.embedding for e in response.data]

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
    backend: EmbeddingBackend = "openai",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CodeEmbedder:
    """Factory function to create a configured CodeEmbedder.

    Args:
        backend: Backend to use ("openai", "local", or "deepinfra")
        model: Model name override (uses backend default if not provided)
        api_key: Optional API key for OpenAI/DeepInfra (uses env var if not provided)

    Returns:
        Configured CodeEmbedder instance
    """
    config = EmbeddingConfig(backend=backend, model=model)
    return CodeEmbedder(config=config, api_key=api_key)


def get_embedding_dimensions(backend: EmbeddingBackend = "openai") -> int:
    """Get the embedding dimensions for a backend.

    Useful for schema creation before embedder is instantiated.

    Args:
        backend: Backend to get dimensions for

    Returns:
        Embedding dimensions (1536 for OpenAI, 1024 for local/DeepInfra Qwen3)
    """
    return BACKEND_CONFIGS[backend]["dimensions"]
