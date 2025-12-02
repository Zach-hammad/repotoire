"""Code-aware embedding generation with backend flexibility.

Supports:
- Auto: Automatically selects the best available backend based on API keys
- OpenAI: High quality embeddings via API ($0.02/1M tokens)
- DeepInfra: Cheap, high-quality Qwen3 embeddings (~$0.01/1M tokens)
- Local: Free, high-quality embeddings via Qwen3-Embedding-0.6B
- Voyage: Code-optimized embeddings, Anthropic-recommended ($0.18/1M tokens)

Environment variables:
- OPENAI_API_KEY: Required for 'openai' backend
- DEEPINFRA_API_KEY: Required for 'deepinfra' backend
- VOYAGE_API_KEY: Required for 'voyage' backend
- No key needed for 'local' backend
"""

import os
from typing import List, Optional, Literal, Tuple
from dataclasses import dataclass

from repotoire.models import Entity, FunctionEntity, ClassEntity, FileEntity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for embedding backends (includes "auto" for automatic selection)
EmbeddingBackend = Literal["auto", "openai", "local", "deepinfra", "voyage"]

# Priority order for auto-selection (best quality/code-fit first)
BACKEND_PRIORITY = [
    "voyage",      # Best for code (purpose-built, Anthropic-recommended)
    "openai",      # High quality, widely used
    "deepinfra",   # Cheap Qwen3 access
    "local",       # Free fallback (always available)
]

# Backend configurations with defaults
BACKEND_CONFIGS = {
    "openai": {
        "dimensions": 1536,
        "model": "text-embedding-3-small",
        "env_key": "OPENAI_API_KEY",
        "description": "OpenAI embeddings (high quality)",
    },
    "local": {
        "dimensions": 1024,
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "fallback_model": "all-MiniLM-L6-v2",
        "fallback_dimensions": 384,
        "env_key": None,  # No API key needed
        "description": "Local Qwen3 embeddings (free, ~4GB RAM)",
    },
    "deepinfra": {
        "dimensions": 4096,
        "model": "Qwen/Qwen3-Embedding-8B",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "env_key": "DEEPINFRA_API_KEY",
        "description": "DeepInfra Qwen3-8B (cheap, high quality)",
    },
    "voyage": {
        "dimensions": 1024,
        "model": "voyage-code-3",
        "env_key": "VOYAGE_API_KEY",
        "description": "Voyage AI code embeddings (best for code)",
        "models": {
            "voyage-code-3": {"dimensions": 1024, "price": 0.18},  # Best for code
            "voyage-3.5": {"dimensions": 1024, "price": 0.06},     # General purpose
            "voyage-3.5-lite": {"dimensions": 512, "price": 0.02},  # Budget option
        },
    },
}


def detect_available_backends() -> List[str]:
    """Detect which backends are available based on environment.

    Checks for API keys in environment variables for each backend.
    Local backend is always available as it requires no API key.

    Returns:
        List of available backend names in priority order.
    """
    available = []

    for backend in BACKEND_PRIORITY:
        config = BACKEND_CONFIGS[backend]
        env_key = config.get("env_key")

        if env_key is None:
            # No API key needed (e.g., local)
            available.append(backend)
        elif os.getenv(env_key):
            # API key is set
            available.append(backend)

    return available


def select_best_backend() -> Tuple[str, str]:
    """Select the best available backend based on environment.

    Prioritizes backends in this order:
    1. Voyage (best for code, purpose-built)
    2. OpenAI (high quality, widely used)
    3. DeepInfra (cheap Qwen3 access)
    4. Local (free, always available)

    Returns:
        Tuple of (backend_name, reason_string)
    """
    available = detect_available_backends()

    if not available:
        # Should never happen since local is always available
        return "local", "No API keys configured, using local embeddings"

    # Return first available in priority order
    selected = available[0]
    config = BACKEND_CONFIGS[selected]

    if selected == "local":
        reason = "No API keys configured, using local Qwen3-0.6B embeddings (free)"
    else:
        reason = f"Using {selected}: {config['description']}"

    return selected, reason


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation."""

    backend: EmbeddingBackend = "auto"  # Auto-selects best available backend
    model: Optional[str] = None  # Uses backend default if not specified
    batch_size: int = 100
    include_context: bool = True  # Include surrounding code context
    max_code_length: int = 2000  # Max characters of code to embed

    def resolve_backend(self) -> Tuple[str, str]:
        """Resolve 'auto' to an actual backend.

        If backend is 'auto', selects the best available backend based on
        configured API keys. Otherwise returns the explicitly configured backend.

        Returns:
            Tuple of (resolved_backend, reason_string)
        """
        if self.backend == "auto":
            return select_best_backend()
        return self.backend, f"Explicitly configured: {self.backend}"

    def _get_resolved_backend(self) -> str:
        """Get the resolved backend name (internal helper)."""
        if self.backend == "auto":
            backend, _ = select_best_backend()
            return backend
        return self.backend

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions for the configured backend."""
        backend = self._get_resolved_backend()
        return BACKEND_CONFIGS[backend]["dimensions"]

    @property
    def effective_model(self) -> str:
        """Get the effective model name (user-specified or backend default)."""
        backend = self._get_resolved_backend()
        return self.model or BACKEND_CONFIGS[backend]["model"]


class CodeEmbedder:
    """Generate semantic embeddings for code entities.

    Supports four backends:
    - OpenAI (default): High quality embeddings via API ($0.02/1M tokens)
    - DeepInfra: Cheap, high-quality Qwen3 embeddings (~$0.01/1M tokens)
    - Local: Free, high-quality embeddings via Qwen3-Embedding-0.6B (MTEB-Code #1)
    - Voyage: Code-optimized embeddings, Anthropic-recommended ($0.18/1M tokens)

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
        4096

        >>> # Local backend (free, no API key required)
        >>> embedder = CodeEmbedder(backend="local")
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1024

        >>> # Voyage backend (code-optimized, Anthropic-recommended)
        >>> embedder = CodeEmbedder(backend="voyage")
        >>> embedding = embedder.embed_entity(function_entity)
        >>> len(embedding)
        1024
    """

    def __init__(
        self,
        config: Optional[EmbeddingConfig] = None,
        backend: EmbeddingBackend = "auto",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize code embedder.

        Args:
            config: Embedding configuration (uses defaults if not provided)
            backend: Backend to use ("auto", "openai", "local", "deepinfra", "voyage"),
                     ignored if config provided. "auto" selects best available.
            model: Model name override, ignored if config provided
            api_key: API key for OpenAI/DeepInfra/Voyage (uses env vars if not provided)
        """
        # Build config from parameters if not provided
        if config is None:
            config = EmbeddingConfig(backend=backend, model=model)
        self.config = config
        self._api_key = api_key

        # Resolve auto backend
        self.resolved_backend, self.backend_reason = config.resolve_backend()
        logger.info(f"Embedding backend: {self.backend_reason}")

        # Store dimensions for external access (uses resolved backend)
        self.dimensions = BACKEND_CONFIGS[self.resolved_backend]["dimensions"]

        # Initialize the resolved backend
        if self.resolved_backend == "local":
            self._init_local()
        elif self.resolved_backend == "deepinfra":
            self._init_deepinfra()
        elif self.resolved_backend == "voyage":
            self._init_voyage()
        else:
            self._init_openai(api_key)

        logger.info(
            f"Initialized CodeEmbedder with backend={self.resolved_backend}, "
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

    def _init_voyage(self) -> None:
        """Initialize Voyage AI embeddings.

        Voyage AI provides code-optimized embeddings recommended by Anthropic.
        Uses voyage-code-3 model by default, optimized for code search.
        """
        config = BACKEND_CONFIGS["voyage"]
        env_key = config["env_key"]

        api_key = self._api_key or os.getenv(env_key)
        if not api_key:
            raise ValueError(
                f"{env_key} environment variable required for voyage backend. "
                f"Get your API key at https://dash.voyageai.com"
            )

        # Store for later use in embed methods
        self._voyage_api_key = api_key

        # Update dimensions if using a non-default model
        model_name = self.config.effective_model
        if model_name in config.get("models", {}):
            model_config = config["models"][model_name]
            if model_config["dimensions"] != self.dimensions:
                self.dimensions = model_config["dimensions"]
                logger.info(f"Voyage model {model_name} uses {self.dimensions} dimensions")

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

        For Voyage backend, uses input_type="query" for optimal search performance.

        Args:
            query: Natural language question about code

        Returns:
            Embedding vector (dimensions depend on backend)
        """
        if self.resolved_backend == "local":
            return self._embed_local([query])[0]
        elif self.resolved_backend == "deepinfra":
            return self._embed_deepinfra([query])[0]
        elif self.resolved_backend == "voyage":
            return self._embed_voyage([query], input_type="query")[0]
        else:
            return self._embeddings.embed_query(query)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts efficiently.

        For Voyage backend, uses input_type="document" for indexing.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if self.resolved_backend == "local":
            return self._embed_local(texts)
        elif self.resolved_backend == "deepinfra":
            return self._embed_deepinfra(texts)
        elif self.resolved_backend == "voyage":
            return self._embed_voyage(texts, input_type="document")
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

    def _embed_voyage(
        self, texts: List[str], input_type: str = "document"
    ) -> List[List[float]]:
        """Generate embeddings using Voyage AI API.

        Voyage AI recommends using different input_type for queries vs documents:
        - "query": For search queries (shorter, question-like text)
        - "document": For documents being indexed (code, documentation)

        Args:
            texts: List of texts to embed
            input_type: "document" for indexing, "query" for search queries

        Returns:
            List of embedding vectors
        """
        try:
            import voyageai
        except ImportError:
            raise ImportError(
                "voyageai package required for voyage backend. "
                "Install with: pip install repotoire[voyage]"
            )

        client = voyageai.Client(api_key=self._voyage_api_key)

        result = client.embed(
            texts=texts,
            model=self.config.effective_model,
            input_type=input_type,
        )

        return result.embeddings

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
    backend: EmbeddingBackend = "auto",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CodeEmbedder:
    """Factory function to create a configured CodeEmbedder.

    Args:
        backend: Backend to use ("auto", "openai", "local", "deepinfra", or "voyage").
                 "auto" selects best available based on API keys.
        model: Model name override (uses backend default if not provided)
        api_key: Optional API key for OpenAI/DeepInfra/Voyage (uses env var if not provided)

    Returns:
        Configured CodeEmbedder instance
    """
    config = EmbeddingConfig(backend=backend, model=model)
    return CodeEmbedder(config=config, api_key=api_key)


def get_embedding_dimensions(backend: EmbeddingBackend = "auto") -> int:
    """Get the embedding dimensions for a backend.

    Useful for schema creation before embedder is instantiated.

    Args:
        backend: Backend to get dimensions for. "auto" resolves to best available.

    Returns:
        Embedding dimensions:
        - OpenAI: 1536
        - DeepInfra: 4096
        - Local: 1024
        - Voyage: 1024 (voyage-code-3), 512 (voyage-3.5-lite)
        - Auto: dimensions of resolved backend
    """
    if backend == "auto":
        resolved_backend, _ = select_best_backend()
        return BACKEND_CONFIGS[resolved_backend]["dimensions"]
    return BACKEND_CONFIGS[backend]["dimensions"]
