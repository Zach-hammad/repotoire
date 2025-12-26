"""Main ingestion pipeline for processing codebases."""

import functools
import hashlib
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple


# Memoized helper functions for string parsing - avoid repeated splits
@functools.lru_cache(maxsize=10000)
def _parse_qualified_name(qn: str) -> Tuple[str, str, str]:
    """Parse qualified name into (file_path, entity_part, line).

    Format: /path/to/file.py::Class.method:line
    Returns: (file_path, entity_part_without_line_numbers, "")
    """
    if "::" not in qn:
        return (qn, "", "")
    file_part, rest = qn.split("::", 1)
    # Entity part is like "ClassName:140.method_name:177" - strip line number at end
    entity_part = rest.rsplit(":", 1)[0] if ":" in rest else rest
    # Remove line numbers embedded in class names (e.g., "ClassName:140.method" -> "ClassName.method")
    entity_part = re.sub(r":(\d+)", "", entity_part)
    return (file_part, entity_part, "")


@functools.lru_cache(maxsize=10000)
def _get_module_prefix(qn: str) -> str:
    """Extract module prefix (everything except last component).

    e.g., "repotoire.mcp.models.Class" -> "repotoire.mcp.models"
    """
    if "." in qn:
        return ".".join(qn.split(".")[:-1])
    return ""


@functools.lru_cache(maxsize=10000)
def _split_path_components(path: str, sep: str = os.sep) -> Tuple[str, ...]:
    """Split path into components (cached for repeated use)."""
    return tuple(path.split(sep))

from repotoire_fast import scan_files as rust_scan_files

from repotoire.graph import Neo4jClient, GraphSchema
from repotoire.graph.base import DatabaseClient
from repotoire.parsers import CodeParser, PythonParser
from repotoire.models import Entity, Relationship, SecretsPolicy, RelationshipType
from repotoire.logging_config import get_logger, LogContext, log_operation

logger = get_logger(__name__)


class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


class IngestionPipeline:
    """Pipeline for ingesting code into the knowledge graph."""

    # Security limits
    MAX_FILE_SIZE_MB = 10  # Maximum file size to process
    DEFAULT_FOLLOW_SYMLINKS = False  # Don't follow symlinks by default
    DEFAULT_BATCH_SIZE = 100  # Default batch size for loading entities

    def __init__(
        self,
        repo_path: str,
        neo4j_client: DatabaseClient,
        follow_symlinks: bool = DEFAULT_FOLLOW_SYMLINKS,
        max_file_size_mb: float = MAX_FILE_SIZE_MB,
        batch_size: int = DEFAULT_BATCH_SIZE,
        secrets_policy: SecretsPolicy = SecretsPolicy.REDACT,
        generate_clues: bool = False,
        generate_embeddings: bool = False,
        embedding_backend: str = "openai",
        embedding_model: Optional[str] = None,
        generate_contexts: bool = False,
        context_model: str = "claude-haiku-3-5-20241022",
        max_context_cost: Optional[float] = None,
        repo_id: Optional[str] = None,
        repo_slug: Optional[str] = None,
    ):
        """Initialize ingestion pipeline with security validation.

        Args:
            repo_path: Path to repository root
            neo4j_client: Database client (Neo4j or FalkorDB)
            follow_symlinks: Whether to follow symbolic links (default: False for security)
            max_file_size_mb: Maximum file size in MB to process (default: 10MB)
            batch_size: Number of entities to batch before loading to graph (default: 100)
            secrets_policy: Policy for handling detected secrets (default: REDACT)
            generate_clues: Whether to generate AI semantic clues (default: False)
            generate_embeddings: Whether to generate vector embeddings for RAG (default: False)
            embedding_backend: Backend for embeddings: 'openai' or 'local' (default: openai)
            embedding_model: Model name override for embeddings (default: uses backend default)
            generate_contexts: Whether to generate semantic contexts using Claude (default: False)
            context_model: Claude model for context generation (default: claude-haiku-3-5-20241022)
            max_context_cost: Maximum USD to spend on context generation (default: unlimited)
            repo_id: Optional repository UUID for multi-tenant isolation. When set, all
                entities created during ingestion will have this repo_id attached.
            repo_slug: Optional repository slug (e.g., "owner/repo-name") for human-readable
                identification. Used with repo_id for multi-tenant setups.

        Raises:
            ValueError: If repository path is invalid
            SecurityError: If path violates security constraints
        """
        # Check if path is a symlink BEFORE resolving (security)
        repo_path_obj = Path(repo_path)
        if repo_path_obj.is_symlink():
            raise SecurityError(
                f"Repository path cannot be a symbolic link: {repo_path}\n"
                f"Symlinks in the repository root are not allowed for security reasons."
            )

        # Resolve to absolute canonical path
        self.repo_path = repo_path_obj.resolve()

        # Validate repository path
        self._validate_repo_path()

        self.db = neo4j_client
        # Detect if we're using FalkorDB
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"
        self.parsers: Dict[str, CodeParser] = {}
        self.follow_symlinks = follow_symlinks
        self.max_file_size_mb = max_file_size_mb
        self.batch_size = batch_size
        self.secrets_policy = secrets_policy
        self.generate_clues = generate_clues
        self.generate_embeddings = generate_embeddings
        self.embedding_backend = embedding_backend
        self.embedding_model = embedding_model
        self.generate_contexts = generate_contexts
        self.context_model = context_model
        self.max_context_cost = max_context_cost
        self.repo_id = repo_id
        self.repo_slug = repo_slug

        # Track skipped files for reporting
        self.skipped_files: List[Dict[str, str]] = []

        # Cache for file content and hash to avoid redundant reads
        # Key: Path, Value: (content_bytes, md5_hash)
        self._file_cache: Dict[Path, Tuple[bytes, str]] = {}

        # Callbacks to run after ingestion completes (e.g., cache invalidation)
        self._on_ingest_complete_callbacks: List[Callable[[], None]] = []

        # Optional RAG retriever for automatic cache invalidation
        self._rag_retriever = None

        # Initialize clue generator if needed
        self.clue_generator = None
        if self.generate_clues:
            try:
                from repotoire.ai import SpacyClueGenerator
                self.clue_generator = SpacyClueGenerator()
                logger.info("Clue generation enabled (using spaCy)")
            except Exception as e:
                logger.warning(f"Could not initialize clue generator: {e}")
                logger.warning("Continuing without clue generation")
                self.generate_clues = False

        # Initialize embedder if needed
        self.embedder = None
        if self.generate_embeddings:
            try:
                from repotoire.ai import CodeEmbedder
                self.embedder = CodeEmbedder(
                    backend=self.embedding_backend,
                    model=self.embedding_model,
                )
                logger.info(
                    f"Embedding generation enabled (backend={self.embedding_backend}, "
                    f"model={self.embedder.config.effective_model}, "
                    f"dimensions={self.embedder.dimensions})"
                )
            except ImportError as e:
                logger.warning(f"Could not initialize embedder: {e}")
                if self.embedding_backend == "local":
                    logger.warning("Install with: pip install repotoire[local-embeddings]")
                logger.warning("Continuing without embedding generation")
                self.generate_embeddings = False
            except Exception as e:
                logger.warning(f"Could not initialize embedder: {e}")
                logger.warning("Continuing without embedding generation")
                self.generate_embeddings = False

        # Initialize context generator if needed (REPO-242)
        self.context_generator = None
        if self.generate_contexts:
            try:
                from repotoire.ai import ContextGenerator, ContextualRetrievalConfig
                config = ContextualRetrievalConfig(
                    enabled=True,
                    model=self.context_model,
                    max_cost_usd=self.max_context_cost,
                )
                self.context_generator = ContextGenerator(config)
                logger.info(
                    f"Context generation enabled (model={self.context_model}, "
                    f"max_cost=${self.max_context_cost or 'unlimited'})"
                )
            except ImportError as e:
                logger.warning(f"Could not initialize context generator: {e}")
                logger.warning("Install with: pip install anthropic")
                logger.warning("Continuing without context generation")
                self.generate_contexts = False
            except ValueError as e:
                logger.warning(f"Could not initialize context generator: {e}")
                logger.warning("Continuing without context generation")
                self.generate_contexts = False

        # Register default parsers with secrets policy
        self.register_parser("python", PythonParser(secrets_policy=secrets_policy))

    def _validate_repo_path(self) -> None:
        """Validate repository path for security.

        Raises:
            ValueError: If path doesn't exist or isn't a directory
        """
        if not self.repo_path.exists():
            raise ValueError(f"Repository does not exist: {self.repo_path}")

        if not self.repo_path.is_dir():
            raise ValueError(f"Repository must be a directory: {self.repo_path}")

        logger.info(f"Repository path validated: {self.repo_path}")

    def _get_file_content_and_hash(self, file_path: Path) -> Tuple[bytes, str]:
        """Read file once, cache content and hash.

        Reads the file in binary mode, computes MD5 hash, and caches both.
        Subsequent calls for the same path return cached values.

        Args:
            file_path: Path to the file to read

        Returns:
            Tuple of (content_bytes, md5_hash)

        Raises:
            OSError: If file cannot be read
        """
        # Normalize path for consistent cache keys
        normalized_path = file_path.resolve()

        if normalized_path in self._file_cache:
            return self._file_cache[normalized_path]

        with open(normalized_path, "rb") as f:
            content = f.read()
        file_hash = hashlib.md5(content).hexdigest()

        self._file_cache[normalized_path] = (content, file_hash)
        return content, file_hash

    def _clear_file_cache(self) -> None:
        """Clear the file content cache to free memory.

        Should be called after ingestion batch completes to prevent memory bloat.
        """
        cache_size = len(self._file_cache)
        self._file_cache.clear()
        if cache_size > 0:
            logger.debug(f"Cleared file cache ({cache_size} entries)")

    def register_parser(self, language: str, parser: CodeParser) -> None:
        """Register a language parser.

        Args:
            language: Language identifier (e.g., 'python', 'typescript')
            parser: Parser instance
        """
        self.parsers[language] = parser
        logger.info(f"Registered parser for {language}")

    def register_on_ingest_complete(self, callback: Callable[[], None]) -> None:
        """Register a callback to run after ingestion completes.

        Use this to register cache invalidation or other cleanup functions.

        Args:
            callback: Zero-argument callable to execute after ingestion

        Example:
            >>> pipeline.register_on_ingest_complete(retriever.invalidate_cache)
        """
        self._on_ingest_complete_callbacks.append(callback)
        logger.debug(f"Registered on_ingest_complete callback: {callback.__name__}")

    def set_rag_retriever(self, retriever) -> None:
        """Set the RAG retriever for automatic cache invalidation.

        When a retriever is set, its cache will be automatically invalidated
        after each ingestion completes.

        Args:
            retriever: GraphRAGRetriever instance with invalidate_cache method
        """
        self._rag_retriever = retriever
        logger.info("RAG retriever registered for automatic cache invalidation")

    def _run_on_ingest_complete_callbacks(self) -> None:
        """Execute all registered on_ingest_complete callbacks."""
        # Invalidate RAG cache if retriever is set
        if self._rag_retriever is not None:
            try:
                self._rag_retriever.invalidate_cache()
            except Exception as e:
                logger.warning(f"Failed to invalidate RAG cache: {e}")

        # Run other registered callbacks
        for callback in self._on_ingest_complete_callbacks:
            try:
                callback()
            except Exception as e:
                logger.warning(f"On-ingest-complete callback failed: {e}")

    def _validate_file_path(self, file_path: Path) -> None:
        """Validate file path is within repository boundary.

        Args:
            file_path: Path to validate

        Raises:
            SecurityError: If file is outside repository or violates security constraints
        """
        # Resolve to absolute path
        resolved_file = file_path.resolve()

        # Check if file is within repository boundary
        try:
            resolved_file.relative_to(self.repo_path)
        except ValueError:
            raise SecurityError(
                f"Security violation: File is outside repository boundary\n"
                f"File: {file_path}\n"
                f"Repository: {self.repo_path}\n"
                f"This could be a path traversal attack."
            )

    def _validate_file_size(self, file_path: Path) -> bool:
        """Validate file size is within limits.

        Args:
            file_path: Path to check

        Returns:
            True if file is within size limit, False otherwise
        """
        try:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            if size_mb > self.max_file_size_mb:
                logger.warning(
                    f"Skipping file {file_path}: size {size_mb:.1f}MB exceeds limit of {self.max_file_size_mb}MB"
                )
                self.skipped_files.append({
                    "file": str(file_path),
                    "reason": f"File too large: {size_mb:.1f}MB > {self.max_file_size_mb}MB"
                })
                return False
            return True
        except Exception as e:
            logger.warning(f"Could not check file size for {file_path}: {e}")
            return True  # Allow file if size check fails

    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped for security or other reasons.

        Args:
            file_path: Path to check

        Returns:
            True if file should be skipped
        """
        # Skip symlinks by default (security)
        if file_path.is_symlink() and not self.follow_symlinks:
            logger.warning(f"Skipping symlink: {file_path} (use --follow-symlinks to include)")
            self.skipped_files.append({
                "file": str(file_path),
                "reason": "Symbolic link (security)"
            })
            return True

        # Validate file size
        if not self._validate_file_size(file_path):
            return True

        # Validate path boundary
        try:
            self._validate_file_path(file_path)
        except SecurityError as e:
            logger.error(f"Security check failed for {file_path}: {e}")
            self.skipped_files.append({
                "file": str(file_path),
                "reason": "Outside repository boundary"
            })
            return True

        return False

    def scan(self, patterns: Optional[List[str]] = None) -> List[Path]:
        """Scan repository for source files with security validation.

        Uses Rust parallel scanner for 3-10x speedup over Python glob.

        Args:
            patterns: List of glob patterns to match (default: ['**/*.py'])

        Returns:
            List of validated file paths
        """
        if patterns is None:
            patterns = ["**/*.py"]

        ignored_dirs = [".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"]
        file_paths = rust_scan_files(str(self.repo_path), patterns, ignored_dirs)
        files = [Path(p) for p in file_paths if not self._should_skip_file(Path(p))]

        logger.info(f"Found {len(files)} source files (skipped {len(self.skipped_files)} files)")
        return files

    def _get_relative_path(self, file_path: Path) -> str:
        """Get relative path from repository root.

        Stores relative paths instead of absolute paths for security
        (avoids exposing full system paths in database).

        Args:
            file_path: Absolute file path

        Returns:
            Relative path string from repository root
        """
        try:
            return str(file_path.relative_to(self.repo_path))
        except ValueError:
            # Should not happen due to validation, but handle gracefully
            logger.warning(f"Could not make path relative: {file_path}")
            return str(file_path)

    def parse_and_extract(self, file_path: Path) -> tuple[List[Entity], List[Relationship]]:
        """Parse a file and extract entities/relationships with security validation.

        Args:
            file_path: Path to source file (must be within repository)

        Returns:
            Tuple of (entities, relationships)

        Note:
            All file paths stored in entities will be relative to repository root
            for security (avoids exposing system structure).
        """
        # Security validation
        try:
            self._validate_file_path(file_path)
        except SecurityError as e:
            logger.error(f"Security validation failed: {e}")
            self.skipped_files.append({
                "file": str(file_path),
                "reason": "Security validation failed"
            })
            return [], []

        # Determine language from extension
        language = self._detect_language(file_path)

        if language not in self.parsers:
            logger.warning(f"No parser for {language}, skipping {file_path}")
            return [], []

        parser = self.parsers[language]

        try:
            # Populate file cache if not already cached (for new files)
            # This ensures each file is read at most once during ingestion
            content, file_hash = self._get_file_content_and_hash(file_path)

            # Inject cached content into parser to avoid redundant reads
            if hasattr(parser, 'set_cached_content'):
                parser.set_cached_content(str(file_path), content, file_hash)

            entities, relationships = parser.process_file(str(file_path))

            # Convert all entity file paths to relative paths for security
            for entity in entities:
                if hasattr(entity, 'file_path') and entity.file_path:
                    # Store relative path instead of absolute
                    entity.file_path = self._get_relative_path(Path(entity.file_path))

            logger.debug(
                f"Extracted {len(entities)} entities and {len(relationships)} relationships from {file_path}"
            )
            return entities, relationships
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            self.skipped_files.append({
                "file": str(file_path),
                "reason": f"Parse error: {str(e)}"
            })
            return [], []

    def _generate_clues_for_entities(
        self, entities: List[Entity]
    ) -> tuple[List[Entity], List[Relationship]]:
        """Generate semantic clues for entities.

        Args:
            entities: List of entities to generate clues for

        Returns:
            Tuple of (clue_entities, describes_relationships)
        """
        if not self.generate_clues or not self.clue_generator:
            return [], []

        clue_entities = []
        describes_relationships = []

        for entity in entities:
            try:
                # Generate clues for this entity
                clues = self.clue_generator.generate_clues(entity)

                for clue in clues:
                    clue_entities.append(clue)

                    # Create DESCRIBES relationship from clue to target entity
                    describes_rel = Relationship(
                        from_node=clue.qualified_name,
                        to_node=entity.qualified_name,
                        rel_type=RelationshipType.DESCRIBES,
                        properties={
                            "clue_type": clue.clue_type,
                            "confidence": clue.confidence,
                            "generated_by": clue.generated_by
                        }
                    )
                    describes_relationships.append(describes_rel)

            except Exception as e:
                logger.warning(f"Failed to generate clues for {entity.qualified_name}: {e}")

        logger.debug(f"Generated {len(clue_entities)} clues for {len(entities)} entities")
        return clue_entities, describes_relationships

    def _generate_embeddings_for_all_entities(self) -> int:
        """Generate vector embeddings for all entities in the graph.

        Queries Neo4j for all Function, Class, and File nodes without embeddings,
        generates embeddings in batches, and updates the nodes.

        Returns:
            Number of entities that received embeddings
        """
        if not self.embedder:
            return 0

        from repotoire.models import FunctionEntity, ClassEntity, FileEntity

        entities_embedded = 0
        embedding_batch_size = 50  # Smaller batches for API rate limits

        # FalkorDB uses id() while Neo4j uses elementId()
        id_func = "id" if self.is_falkordb else "elementId"

        # Process each entity type
        for entity_type, entity_class in [
            ("Function", FunctionEntity),
            ("Class", ClassEntity),
            ("File", FileEntity)
        ]:
            logger.info(f"Generating embeddings for {entity_type} entities...")

            # Query entities without embeddings (include semantic_context for contextual retrieval)
            query = f"""
            MATCH (e:{entity_type})
            WHERE e.embedding IS NULL
            RETURN
                {id_func}(e) as id,
                e.name as name,
                e.qualifiedName as qualified_name,
                e.docstring as docstring,
                e.filePath as file_path,
                e.lineStart as line_start,
                e.lineEnd as line_end,
                e.semantic_context as semantic_context
            """

            entities = self.db.execute_query(query)

            if not entities:
                logger.debug(f"No {entity_type} entities need embeddings")
                continue

            logger.info(f"Found {len(entities)} {entity_type} entities to embed")

            # Process in batches
            for i in range(0, len(entities), embedding_batch_size):
                batch = entities[i:i + embedding_batch_size]

                # Convert to entity objects for embedder
                entity_objects = []
                semantic_contexts = []  # Track contexts for contextualized embedding
                for e in batch:
                    if entity_type == "Function":
                        entity_obj = FunctionEntity(
                            name=e["name"],
                            qualified_name=e["qualified_name"],
                            file_path=e["file_path"],
                            line_start=e["line_start"],
                            line_end=e["line_end"],
                            docstring=e.get("docstring"),
                            parameters=[],  # Not needed for embedding
                        )
                    elif entity_type == "Class":
                        entity_obj = ClassEntity(
                            name=e["name"],
                            qualified_name=e["qualified_name"],
                            file_path=e["file_path"],
                            line_start=e["line_start"],
                            line_end=e["line_end"],
                            docstring=e.get("docstring")
                        )
                    else:  # File
                        entity_obj = FileEntity(
                            name=e["name"],
                            qualified_name=e["qualified_name"],
                            file_path=e["file_path"],
                            line_start=e["line_start"],
                            line_end=e["line_end"],
                            language="python"  # Default, actual value not needed for embedding
                        )

                    entity_objects.append(entity_obj)
                    semantic_contexts.append(e.get("semantic_context"))

                try:
                    # Generate embeddings - use contextualized text if semantic_context available
                    texts = []
                    for entity_obj, context in zip(entity_objects, semantic_contexts):
                        if context and self.context_generator:
                            # Use contextualized text for better retrieval
                            text = self.context_generator.contextualize_text(entity_obj, context)
                        else:
                            # Fall back to standard entity text
                            text = self.embedder._entity_to_text(entity_obj)
                        texts.append(text)

                    embeddings = self.embedder.embed_batch(texts)

                    # Update database with embeddings
                    for entity, embedding in zip(batch, embeddings):
                        # FalkorDB requires vecf32() wrapper for vector storage
                        if self.is_falkordb:
                            update_query = f"""
                            MATCH (e:{entity_type})
                            WHERE {id_func}(e) = $id
                            SET e.embedding = vecf32($embedding)
                            """
                        else:
                            update_query = f"""
                            MATCH (e:{entity_type})
                            WHERE {id_func}(e) = $id
                            SET e.embedding = $embedding
                            """
                        self.db.execute_query(update_query, {
                            "id": entity["id"],
                            "embedding": embedding
                        })

                    entities_embedded += len(batch)
                    logger.debug(f"Embedded batch of {len(batch)} {entity_type} entities ({entities_embedded}/{len(entities)})")

                except Exception as e:
                    logger.error(f"Failed to generate embeddings for batch: {e}")
                    continue

        return entities_embedded

    async def _generate_contexts_for_all_entities(self) -> int:
        """Generate semantic contexts for all entities in the graph using Claude.

        Queries Neo4j for all Function, Class, and File nodes without contexts,
        generates contexts using Claude, and updates the nodes.

        Returns:
            Number of entities that received contexts
        """
        if not self.context_generator:
            return 0

        from repotoire.models import FunctionEntity, ClassEntity, FileEntity, NodeType
        from repotoire.ai import CostLimitExceeded

        entities_contextualized = 0
        context_batch_size = 100  # Process entities in batches

        # FalkorDB uses id() while Neo4j uses elementId()
        id_func = "id" if self.is_falkordb else "elementId"

        # Get entities without context from database
        entity_records = self.db.get_entities_without_context(limit=10000)

        if not entity_records:
            logger.info("No entities need context generation")
            return 0

        logger.info(f"Found {len(entity_records)} entities to generate contexts for")

        # Convert records to Entity objects for the context generator
        entities = []
        for record in entity_records:
            entity_type = record.get("entity_type", "Function")

            # Create appropriate entity type
            if entity_type == "Function":
                entity = FunctionEntity(
                    name=record["name"],
                    qualified_name=record["qualified_name"],
                    file_path=record.get("file_path", ""),
                    line_start=record.get("line_start", 0),
                    line_end=record.get("line_end", 0),
                    docstring=record.get("docstring"),
                    parameters=[],
                )
            elif entity_type == "Class":
                entity = ClassEntity(
                    name=record["name"],
                    qualified_name=record["qualified_name"],
                    file_path=record.get("file_path", ""),
                    line_start=record.get("line_start", 0),
                    line_end=record.get("line_end", 0),
                    docstring=record.get("docstring"),
                )
            else:  # File
                entity = FileEntity(
                    name=record["name"],
                    qualified_name=record["qualified_name"],
                    file_path=record.get("file_path", ""),
                    line_start=record.get("line_start", 0),
                    line_end=record.get("line_end", 0),
                    language="python",
                )

            entities.append(entity)

        # Process in batches
        for i in range(0, len(entities), context_batch_size):
            batch = entities[i:i + context_batch_size]

            try:
                # Progress callback for logging
                def on_progress(qn: str, count: int):
                    if count % 10 == 0:
                        logger.debug(f"Generated context for {count}/{len(batch)} entities in batch")

                # Generate contexts for batch
                contexts = await self.context_generator.generate_contexts_batch(
                    batch,
                    on_progress=on_progress,
                )

                # Store contexts in database
                if contexts:
                    updated = self.db.batch_set_contexts(contexts)
                    entities_contextualized += updated
                    logger.debug(f"Stored {updated} contexts (batch {i // context_batch_size + 1})")

            except CostLimitExceeded as e:
                logger.warning(f"Context generation stopped due to cost limit: {e}")
                break
            except Exception as e:
                logger.error(f"Failed to generate contexts for batch: {e}")
                continue

        return entities_contextualized

    def load_to_graph(
        self, entities: List[Entity], relationships: List[Relationship]
    ) -> None:
        """Load entities and relationships into Neo4j.

        Args:
            entities: List of entities to create
            relationships: List of relationships to create
        """
        if not entities:
            return

        # Set repo_id and repo_slug on all entities for multi-tenant isolation
        if self.repo_id:
            for entity in entities:
                entity.repo_id = self.repo_id
                entity.repo_slug = self.repo_slug

        # Batch create nodes
        try:
            id_mapping = self.db.batch_create_nodes(entities)
            logger.info(f"Created {len(id_mapping)} nodes")

            # Batch create all relationships
            # Note: batch_create_relationships now accepts qualified names directly
            if relationships:
                self.db.batch_create_relationships(relationships)
                logger.info(f"Created {len(relationships)} relationships")
            else:
                logger.warning("No relationships to create")

        except Exception as e:
            logger.error(f"Failed to load data to graph: {e}")

    def _find_dependent_files(self, changed_files: List[str], max_depth: int = 3) -> List[str]:
        """Find files that depend on changed files via import relationships.

        This enables dependency-aware incremental analysis: when a file changes,
        we also need to re-analyze files that import it.

        Args:
            changed_files: List of file paths that changed
            max_depth: Maximum depth to traverse import relationships (default: 3)

        Returns:
            List of file paths that transitively depend on changed files
        """
        if not changed_files:
            return []

        logger.debug(f"Finding files dependent on {len(changed_files)} changed files")

        # Query to find files that import the changed files (bidirectional)
        # Split into two queries to avoid Cypher aggregation issues
        query = """
        // Find files that import changed files (downstream impact)
        MATCH (f1:File)
        WHERE f1.filePath IN $changed_files
        OPTIONAL MATCH (f2:File)-[:IMPORTS*1..3]->(f1)
        WHERE f2.filePath IS NOT NULL

        RETURN DISTINCT f2.filePath as filePath

        UNION

        // Find files that changed files import (upstream dependencies)
        MATCH (f1:File)
        WHERE f1.filePath IN $changed_files
        OPTIONAL MATCH (f1)-[:IMPORTS*1..3]->(f3:File)
        WHERE f3.filePath IS NOT NULL

        RETURN DISTINCT f3.filePath as filePath
        """

        try:
            result = self.db.execute_query(query, {
                "changed_files": changed_files
            })

            dependent_files = [record["filePath"] for record in result]
            logger.info(f"Found {len(dependent_files)} dependent files for {len(changed_files)} changed files")
            return dependent_files

        except Exception as e:
            logger.warning(f"Could not find dependent files (graph may be empty): {e}")
            return []

    @log_operation("ingest")
    def ingest(
        self,
        incremental: bool = False,
        patterns: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> None:
        """Run the complete ingestion pipeline with security validation.

        Args:
            incremental: If True, only process changed files
            patterns: File patterns to match
            progress_callback: Optional callback function(current, total, filename) for progress tracking
        """
        start_time = time.time()

        # Reset skipped files tracking
        self.skipped_files = []

        # Initialize schema
        with LogContext(operation="init_schema"):
            schema = GraphSchema(self.db)
            # Pass vector dimensions when embeddings are enabled
            if self.generate_embeddings and self.embedder:
                schema.initialize(
                    enable_vector_search=True,
                    vector_dimensions=self.embedder.dimensions,
                )
            else:
                schema.initialize()
            logger.debug("Schema initialized")

        # Scan for files
        with LogContext(operation="scan_files"):
            files = self.scan(patterns)
            logger.info(f"Scanned repository", extra={
                "files_found": len(files),
                "patterns": patterns or ["**/*.py"]
            })

        if not files:
            logger.warning("No files found to process")
            self._clear_file_cache()  # Clear cache on early return
            if self.skipped_files:
                self._report_skipped_files()
            return

        # Incremental ingestion: filter files based on hash comparison
        files_to_process = []
        files_unchanged = 0
        files_changed = 0
        files_new = 0

        if incremental:
            logger.info("Running incremental ingestion (comparing file hashes)")
            for file_path in files:
                rel_path = self._get_relative_path(file_path)
                metadata = self.db.get_file_metadata(rel_path)

                if metadata is None:
                    # New file, need to ingest
                    files_to_process.append(file_path)
                    files_new += 1
                else:
                    # File exists in database, compare hashes
                    # Use cached hash (will read file once, cache for later use by parser)
                    _, current_hash = self._get_file_content_and_hash(file_path)

                    if current_hash == metadata["hash"]:
                        # File unchanged, skip
                        logger.debug(f"Skipping unchanged file: {rel_path}")
                        files_unchanged += 1
                    else:
                        # File changed, need to re-ingest
                        logger.debug(f"File changed (hash mismatch): {rel_path}")
                        # Delete old data first
                        self.db.delete_file_entities(rel_path)
                        files_to_process.append(file_path)
                        files_changed += 1

            logger.info(f"Incremental scan: {files_new} new, {files_changed} changed, {files_unchanged} unchanged")

            # Find dependent files (files that import changed/new files)
            changed_and_new_paths = [self._get_relative_path(f) for f in files_to_process]
            dependent_paths = self._find_dependent_files(changed_and_new_paths)

            # Add dependent files to processing list
            if dependent_paths:
                files_map = {self._get_relative_path(f): f for f in files}
                dependent_files = []

                for dep_path in dependent_paths:
                    # Skip if already in processing list
                    if dep_path in changed_and_new_paths:
                        continue

                    # Get Path object for dependent file
                    if dep_path in files_map:
                        dep_file = files_map[dep_path]
                        # Delete old entities for dependent file
                        self.db.delete_file_entities(dep_path)
                        dependent_files.append(dep_file)

                if dependent_files:
                    logger.info(f"Found {len(dependent_files)} dependent files that need re-analysis")
                    files_to_process.extend(dependent_files)

            # Clean up deleted files (files in DB but not on filesystem)
            all_scanned_paths = {self._get_relative_path(f) for f in files}
            all_db_paths = set(self.db.get_all_file_paths())
            deleted_paths = all_db_paths - all_scanned_paths

            if deleted_paths:
                logger.info(f"Cleaning up {len(deleted_paths)} deleted files from graph")
                for deleted_path in deleted_paths:
                    self.db.delete_file_entities(deleted_path)
        else:
            # Full ingestion: process all files
            files_to_process = files

        if not files_to_process:
            logger.info("No files to process (all files unchanged)")
            self._clear_file_cache()  # Clear cache on early return
            return

        # Process each file
        all_entities = []
        all_relationships = []
        files_processed = 0
        files_failed = 0

        for i, file_path in enumerate(files_to_process, 1):
            with LogContext(operation="parse_file", file=str(file_path), progress=f"{i}/{len(files_to_process)}"):
                logger.debug(f"Processing file {i}/{len(files_to_process)}: {file_path}")

                # Call progress callback if provided
                if progress_callback:
                    progress_callback(i, len(files_to_process), str(file_path))

                entities, relationships = self.parse_and_extract(file_path)

                if entities:
                    files_processed += 1
                    all_entities.extend(entities)
                    all_relationships.extend(relationships)

                    # Generate semantic clues if enabled
                    if self.generate_clues:
                        clue_entities, clue_relationships = self._generate_clues_for_entities(entities)
                        all_entities.extend(clue_entities)
                        all_relationships.extend(clue_relationships)
                else:
                    files_failed += 1

                # Batch load entities for better performance
                if len(all_entities) >= self.batch_size:
                    batch_start = time.time()
                    self.load_to_graph(all_entities, all_relationships)
                    batch_duration = time.time() - batch_start

                    logger.debug("Loaded batch", extra={
                        "entities": len(all_entities),
                        "relationships": len(all_relationships),
                        "duration_seconds": round(batch_duration, 3)
                    })

                    all_entities = []
                    all_relationships = []

        # Load remaining entities
        if all_entities:
            self.load_to_graph(all_entities, all_relationships)
            logger.debug("Loaded final batch", extra={
                "entities": len(all_entities),
                "relationships": len(all_relationships)
            })

        # Show stats
        stats = self.db.get_stats()
        total_duration = time.time() - start_time

        log_extra = {
            "stats": stats,
            "files_total": len(files),
            "files_processed": files_processed,
            "files_failed": files_failed,
            "files_skipped": len(self.skipped_files),
            "duration_seconds": round(total_duration, 2),
            "files_per_second": round(len(files_to_process) / total_duration, 2) if total_duration > 0 else 0
        }

        # Add incremental stats if applicable
        if incremental:
            log_extra["incremental"] = {
                "new": files_new,
                "changed": files_changed,
                "unchanged": files_unchanged,
            }

        # Generate semantic contexts using Claude if enabled (REPO-242)
        if self.generate_contexts and self.context_generator:
            import asyncio
            logger.info("Generating semantic contexts for entities...")
            context_start = time.time()
            contexts_generated = asyncio.run(self._generate_contexts_for_all_entities())
            context_duration = time.time() - context_start
            logger.info(f"Contexts generated for {contexts_generated} entities in {context_duration:.2f}s")

            # Log cost summary if tracking enabled
            if self.context_generator.cost_tracker:
                cost_summary = self.context_generator.cost_tracker.summary()
                logger.info(f"Context generation cost: ${cost_summary['total_cost_usd']:.4f}")
                log_extra["context_cost"] = cost_summary

        # Generate embeddings for all entities if enabled
        if self.generate_embeddings and self.embedder:
            logger.info("Generating embeddings for all entities...")
            embedding_start = time.time()
            entities_embedded = self._generate_embeddings_for_all_entities()
            embedding_duration = time.time() - embedding_start
            logger.info(f"Embeddings generated for {entities_embedded} entities in {embedding_duration:.2f}s")

        # Resolve internal calls (link CALLS relationships to internal functions)
        logger.info("Resolving internal function calls...")
        resolve_start = time.time()
        calls_resolved = self._resolve_internal_calls()
        resolve_duration = time.time() - resolve_start
        logger.info(f"Resolved {calls_resolved} internal calls in {resolve_duration:.2f}s")

        # Validate resolved calls using graph-based community detection
        logger.info("Validating call resolutions with graph structure...")
        validation_start = time.time()
        validation_stats = self._validate_calls_with_graph()
        validation_duration = time.time() - validation_start
        if validation_stats["high_confidence"] > 0:
            total_validated = sum(validation_stats.values())
            high_pct = validation_stats["high_confidence"] / total_validated * 100 if total_validated > 0 else 0
            logger.info(
                f"Call validation complete in {validation_duration:.2f}s: "
                f"{validation_stats['high_confidence']} high ({high_pct:.1f}%), "
                f"{validation_stats['medium_confidence']} medium, "
                f"{validation_stats['low_confidence']} low confidence"
            )

        logger.info("Ingestion complete", extra=log_extra)

        # Clear file content cache to free memory
        self._clear_file_cache()

        # Report skipped files if any
        if self.skipped_files:
            self._report_skipped_files()

        # Run post-ingestion callbacks (e.g., RAG cache invalidation)
        self._run_on_ingest_complete_callbacks()

    def _run_type_inference(self) -> Dict[str, Dict[str, str]]:
        """Run Rust-based type inference to build accurate call graph.

        Returns:
            Dict mapping caller_qn -> {target_simple_name -> resolved_callee_qn}
            This allows us to resolve "self.method()" to the actual method qualified name.
        """
        type_inferred_calls: Dict[str, Dict[str, str]] = defaultdict(dict)

        try:
            import repotoire_fast as rf
        except ImportError:
            logger.debug("repotoire_fast not available, skipping type inference")
            return dict(type_inferred_calls)  # Return regular dict

        try:
            # Step 1: Get all source files with their paths
            files_query = """
            MATCH (f:File)
            WHERE f.filePath IS NOT NULL AND f.filePath ENDS WITH '.py'
            RETURN f.filePath as path
            """
            files_result = self.db.execute_query(files_query)

            # Read source files
            files_to_process = []
            for f in files_result:
                file_path = f["path"]
                # Convert relative path to absolute
                abs_path = str(self.repo_path / file_path) if not file_path.startswith("/") else file_path
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fp:
                        source = fp.read()
                    files_to_process.append((abs_path, source))
                except Exception as e:
                    logger.debug(f"Could not read {abs_path} for type inference: {e}")

            if not files_to_process:
                return type_inferred_calls

            # Step 2: Run type inference
            logger.debug(f"Running type inference on {len(files_to_process)} files...")
            ti_result = rf.infer_types(files_to_process, 10)
            logger.debug(
                f"Type inference complete: {ti_result['num_definitions']} definitions, "
                f"{ti_result['num_classes']} classes, {ti_result['num_calls']} calls"
            )

            # Step 3: Build mapping from type inference namespace to graph qualified names
            # Type inference uses: module.Class.method
            # Graph uses: /path/to/file.py::Class.method:123

            # Get all Functions with their qualified names and simple names
            funcs_query = """
            MATCH (f:Function)
            WHERE f.qualifiedName IS NOT NULL AND f.qualifiedName CONTAINS '::'
            RETURN f.qualifiedName as qn, f.name as name
            """
            funcs = self.db.execute_query(funcs_query)

            # Pre-compute package directories from files_result (which has relative paths)
            # This matches how Rust detects packages
            package_dirs: set[str] = set()
            for f in files_result:
                fp = f["path"]
                # Normalize path for cross-platform compatibility
                fp_norm = os.path.normpath(fp)
                basename = os.path.basename(fp_norm)
                if basename == "__init__.py":
                    # Get the directory containing __init__.py
                    dir_path = os.path.dirname(fp_norm)
                    if dir_path:
                        package_dirs.add(dir_path)

            logger.debug(f"Detected {len(package_dirs)} package directories")

            # Pre-compute file lookup table for O(1) path matching - O(m) construction
            # Index by all suffix paths so we can match absolute paths against relative paths
            file_lookup: Dict[str, dict] = {}
            for f in files_result:
                fp = f["path"]
                fp_norm = os.path.normpath(fp)
                # Store normalized path on the dict for later use
                f["_normalized_path"] = fp_norm

                # Index by all suffixes: full path, then progressively shorter
                # e.g., "repotoire/models.py" → keys: "repotoire/models.py", "models.py"
                parts = fp_norm.split(os.sep)
                for i in range(len(parts)):
                    suffix = os.sep.join(parts[i:])
                    if suffix not in file_lookup:  # First match wins (matches original behavior)
                        file_lookup[suffix] = f

            logger.debug(f"Built file lookup table with {len(file_lookup)} suffix entries for {len(files_result)} files")

            # Build mapping: (file_module, entity_path) -> graph_qn
            # e.g., ("repotoire.models", "Finding.__init__") -> "/path/repotoire/models.py::Finding.__init__:45"
            ti_ns_to_graph_qn: Dict[str, str] = {}
            graph_qn_to_ti_ns: Dict[str, str] = {}

            for func in funcs:
                qn = func["qn"]
                # Use cached parsing for qualified names (significant speedup for large codebases)
                file_part, entity_part, _ = _parse_qualified_name(qn)
                if not entity_part:  # Parsing failed or no :: separator
                    continue

                # Convert file path to module path for matching
                # Must match Rust file_to_module_ns: uses package detection from __init__.py
                module_path = None

                # Find the relative path from files_result that matches this absolute path
                # Graph QN has absolute path, files_result has relative paths
                # Use pre-computed lookup table for O(1) matching instead of O(m) inner loop
                file_part_norm = os.path.normpath(file_part)

                # Try suffixes of file_part_norm until we find a match
                # e.g., "/home/user/project/repotoire/models.py" tries:
                #   "home/user/project/repotoire/models.py", "user/project/repotoire/models.py", ...
                #   until it matches "repotoire/models.py" in the lookup table
                relative_file_path = None
                parts = _split_path_components(file_part_norm)  # Cached split
                for i in range(len(parts)):
                    suffix = os.sep.join(parts[i:])
                    matched_file = file_lookup.get(suffix)
                    if matched_file:
                        relative_file_path = matched_file["_normalized_path"]
                        break

                if relative_file_path:
                    # Remove .py extension using os.path
                    path_without_ext, _ = os.path.splitext(relative_file_path)

                    # Get file directory
                    file_dir = os.path.dirname(path_without_ext)

                    # Find the topmost package that is an ancestor of this file
                    package_root = None
                    for pkg_dir in package_dirs:
                        pkg_dir_norm = os.path.normpath(pkg_dir)
                        # Check if pkg_dir is an ancestor of file_dir
                        if file_dir == pkg_dir_norm or file_dir.startswith(pkg_dir_norm + os.sep):
                            # Find topmost (shortest path)
                            if package_root is None or len(pkg_dir_norm) < len(package_root):
                                package_root = pkg_dir_norm

                    if package_root:
                        # Match Rust logic: strip parent of package root, then use relative path
                        # e.g., package_root = "repotoire", parent = "", result = whole path
                        # e.g., package_root = "tests/unit", parent = "tests", result = strip "tests/"
                        parent = os.path.dirname(package_root)
                        if not parent:
                            # Package is at the root level (e.g., "repotoire")
                            module_path = path_without_ext.replace(os.sep, ".")
                        elif path_without_ext.startswith(parent + os.sep):
                            # Strip the parent directory
                            rel_path = path_without_ext[len(parent) + 1:]  # +1 for separator
                            module_path = rel_path.replace(os.sep, ".")
                        else:
                            module_path = path_without_ext.replace(os.sep, ".")
                    else:
                        # Fallback: use whole relative path
                        module_path = path_without_ext.replace(os.sep, ".")
                else:
                    # Fallback for paths not in files_result: use last 4 components
                    path_without_ext, _ = os.path.splitext(file_part)
                    # Split by both forward and back slashes for cross-platform
                    parts = [p for p in path_without_ext.replace("\\", "/").split("/") if p]
                    if len(parts) > 4:
                        parts = parts[-4:]
                    module_path = ".".join(parts)

                if module_path:
                    # Build type inference namespace: module.Entity
                    ti_ns = f"{module_path}.{entity_part}"
                    ti_ns_to_graph_qn[ti_ns] = qn
                    graph_qn_to_ti_ns[qn] = ti_ns

            logger.debug(f"Built {len(ti_ns_to_graph_qn)} type inference -> graph mappings")

            # Step 4: Convert type inference call graph to graph-based resolution map
            call_graph = ti_result.get("call_graph", {})
            for caller_ti_ns, callees in call_graph.items():
                # Find the graph qualified name for this caller
                caller_qn = ti_ns_to_graph_qn.get(caller_ti_ns)
                if not caller_qn:
                    continue

                # For each callee, try to find its graph qualified name
                for callee_ti_ns in callees:
                    callee_qn = ti_ns_to_graph_qn.get(callee_ti_ns)
                    if callee_qn:
                        # Extract simple name from callee
                        simple_name = callee_ti_ns.split(".")[-1]
                        type_inferred_calls[caller_qn][simple_name] = callee_qn

            logger.info(
                f"Type inference resolved {sum(len(v) for v in type_inferred_calls.values())} "
                f"calls across {len(type_inferred_calls)} callers"
            )
            # Debug: log sample of type_inferred_calls keys
            if type_inferred_calls:
                sample_keys = list(type_inferred_calls.keys())[:3]
                logger.debug(f"Type inference sample callers: {sample_keys}")

        except Exception as e:
            logger.debug(f"Type inference failed (will use fallback resolution): {e}")

        return type_inferred_calls

    def _resolve_internal_calls(self) -> int:
        """Resolve CALLS relationships to internal functions/classes.

        During parsing, cross-file calls are created with simple names (e.g., "some_func")
        that don't match the full qualified names of internal Function nodes. This method
        finds those unresolved calls and links them to the actual internal entities.

        Resolution priority (uses multiple strategies for accuracy):
        0. Type inference: Rust-based PyCG-style analysis (highest accuracy)
        1. Same file: Entity defined in the same file as caller
        2. Imported: Entity from a file that the caller's file imports
        3. Class method: Entity that's a method on a class the caller uses
        4. Community-guided: Use Leiden community membership
        5. Fallback: First matching entity (rare)

        This enables graph analysis features like:
        - Function-to-function call graphs
        - PageRank/betweenness centrality on internal code
        - Circular dependency detection

        Returns:
            Number of calls resolved to internal entities
        """
        calls_resolved = 0
        type_inferred_count = 0

        # Run type inference first (highest accuracy)
        type_inferred_calls = self._run_type_inference()

        # Pre-compute community memberships for graph-aware fallback selection
        # Use File-level IMPORTS relationships (which exist before call resolution)
        # to compute communities, then propagate to all functions in each file
        qn_to_community: Dict[str, int] = {}
        try:
            import repotoire_fast as rf

            # Get all Files with their paths
            file_query = """
            MATCH (f:File)
            WHERE f.filePath IS NOT NULL
            RETURN f.filePath as path
            ORDER BY f.filePath
            """
            files = self.db.execute_query(file_query)
            if files:
                file_paths = [f["path"] for f in files]
                file_to_idx = {path: i for i, path in enumerate(file_paths)}

                # Get import relationships between files (via External* nodes)
                # File A imports module X, File B defines module X -> A imports B
                import_edges_query = """
                MATCH (importer:File)-[:IMPORTS]->(ext)
                WHERE ext:ExternalClass OR ext:ExternalFunction
                WITH importer, ext.qualifiedName as imported_qn
                // Find internal files that define this module
                MATCH (definer:File)-[:CONTAINS]->(entity)
                WHERE (entity:Function OR entity:Class)
                  AND entity.qualifiedName CONTAINS '::'
                  AND entity.qualifiedName ENDS WITH '::' + split(imported_qn, '.')[-1]
                RETURN DISTINCT importer.filePath as src, definer.filePath as dst
                """
                import_edges = self.db.execute_query(import_edges_query)
                edges = []
                for e in import_edges:
                    if e["src"] in file_to_idx and e["dst"] in file_to_idx:
                        edges.append((file_to_idx[e["src"]], file_to_idx[e["dst"]]))

                if edges:
                    file_communities = rf.graph_leiden_parallel(
                        edges, len(file_paths), resolution=1.0, max_iterations=10, parallel=True
                    )
                    file_to_community = {path: comm for path, comm in zip(file_paths, file_communities)}

                    # Now map functions to their file's community
                    func_file_query = """
                    MATCH (f:File)-[:CONTAINS]->(func:Function)
                    WHERE func.qualifiedName IS NOT NULL AND func.qualifiedName CONTAINS '::'
                    RETURN func.qualifiedName as qn, f.filePath as file_path
                    """
                    func_files = self.db.execute_query(func_file_query)
                    for ff in func_files:
                        if ff["file_path"] in file_to_community:
                            qn_to_community[ff["qn"]] = file_to_community[ff["file_path"]]

                    logger.debug(
                        f"Pre-computed {len(set(file_communities))} file communities, "
                        f"mapped to {len(qn_to_community)} functions"
                    )
        except ImportError:
            pass  # repotoire_fast not available
        except Exception as e:
            logger.debug(f"Community pre-computation failed (will use random fallback): {e}")

        try:
            # Step 1: Build a map of simple names to internal qualified names
            # Query all internal Functions and Classes
            internal_entities_query = """
            MATCH (e)
            WHERE (e:Function OR e:Class) AND e.qualifiedName IS NOT NULL
            RETURN e.name as name, e.qualifiedName as qualified_name, labels(e)[0] as label
            """
            internal_entities = self.db.execute_query(internal_entities_query)

            # Build name -> list of qualified names (multiple entities can have same name)
            name_to_qualified: Dict[str, List[Dict]] = defaultdict(list)
            for entity in internal_entities:
                name = entity["name"]
                name_to_qualified[name].append({
                    "qualified_name": entity["qualified_name"],
                    "label": entity["label"]
                })

            logger.debug(f"Built internal entity map with {len(name_to_qualified)} unique names")

            # Step 1b: Build import map using IMPORTS relationships in the graph
            # IMPORTS go from File -> External* nodes with module paths like "repotoire.mcp.models.Class"
            # We need to convert these to file paths and map to internal entities
            imports_query = """
            MATCH (f:File)-[:IMPORTS]->(imported)
            WHERE imported:ExternalClass OR imported:ExternalFunction
            RETURN f.filePath as importer_path, imported.qualifiedName as imported_qn
            """
            imports_result = self.db.execute_query(imports_query)

            # Map: file_path -> set of imported module prefixes (e.g., "repotoire.mcp.models")
            file_import_modules: Dict[str, set] = defaultdict(set)
            for imp in imports_result:
                importer = imp["importer_path"]
                imported_qn = imp["imported_qn"]
                if not importer or not imported_qn:
                    continue
                # Store the full imported qualified name and its module prefix
                file_import_modules[importer].add(imported_qn)
                # Also store module prefix (e.g., "repotoire.mcp.models" from "repotoire.mcp.models.Class")
                module_prefix = _get_module_prefix(imported_qn)  # Cached
                if module_prefix:
                    file_import_modules[importer].add(module_prefix)

            # Build mapping from module path to file path for internal files
            # e.g., "repotoire.mcp.models" -> "repotoire/mcp/models.py"
            module_to_file: Dict[str, str] = {}
            for entity in internal_entities:
                qn = entity["qualified_name"]
                if "::" in qn:
                    # Extract file path from qualified name: /full/path/file.py::Entity:line
                    file_path = qn.split("::")[0]
                    # Convert absolute path to module-like path
                    # /home/user/project/repotoire/mcp/models.py -> repotoire.mcp.models
                    try:
                        rel_path = str(Path(file_path).relative_to(self.repo_path))
                        if rel_path.endswith(".py"):
                            rel_path = rel_path[:-3]  # Remove .py
                        # rel_path is now like "repotoire/mcp/models" - convert to module path
                        module_path = rel_path.replace("/", ".")
                        module_to_file[module_path] = file_path
                    except ValueError:
                        # Path is not within repo_path, skip this entity
                        pass

            logger.debug(f"Built import map for {len(file_import_modules)} files, {len(module_to_file)} module mappings")

            # Step 2: Find CALLS relationships where target is External*
            external_calls_query = """
            MATCH (caller:Function)-[r:CALLS]->(target)
            WHERE (target:ExternalFunction OR target:ExternalClass OR target:BuiltinFunction)
              AND target.name IS NOT NULL
            RETURN
                caller.qualifiedName as caller_qn,
                target.name as target_name,
                target.qualifiedName as target_qn,
                labels(target)[0] as target_label,
                r.line as line,
                r.call_name as call_name,
                r.is_self_call as is_self_call
            """
            external_calls = self.db.execute_query(external_calls_query)
            logger.debug(f"Found {len(external_calls)} external calls to potentially resolve")

            # Step 3: For each external call, try to resolve to internal entity
            # Collect all resolutions for batch processing (performance optimization)
            resolutions = []  # List of dicts with resolution data
            fallback_count = 0
            community_guided_fallback = 0
            for call in external_calls:
                target_name = call["target_name"]
                caller_qn = call["caller_qn"]

                if target_name not in name_to_qualified:
                    continue  # No internal entity with this name

                # Find the best matching internal entity
                candidates = name_to_qualified[target_name]
                best_match = None

                # Extract caller's file path for context (both absolute and relative)
                caller_file_abs = caller_qn.split("::")[0] if "::" in caller_qn else None
                caller_file_rel = None
                if caller_file_abs:
                    # Convert absolute path to relative path (relative to repo root)
                    # File.filePath uses relative paths like "tests/unit/file.py" or "repotoire/models.py"
                    try:
                        caller_file_rel = str(Path(caller_file_abs).relative_to(self.repo_path))
                    except ValueError:
                        # Path is not within repo_path, leave as None
                        pass

                # Priority 0: Type inference resolution (highest accuracy)
                if caller_qn in type_inferred_calls:
                    if target_name in type_inferred_calls[caller_qn]:
                        resolved_qn = type_inferred_calls[caller_qn][target_name]
                        # Find the matching candidate
                        for candidate in candidates:
                            if candidate["qualified_name"] == resolved_qn:
                                best_match = candidate
                                type_inferred_count += 1
                                break
                    elif type_inferred_count < 3:  # Debug first few misses
                        logger.debug(f"Type inference miss: caller has {list(type_inferred_calls[caller_qn].keys())}, looking for '{target_name}'")
                elif type_inferred_count < 3 and target_name in name_to_qualified:  # Debug first few
                    logger.debug(f"Type inference miss: caller_qn={caller_qn[:80]} not in type_inferred_calls")

                # Priority 1: Same file match
                if not best_match:
                    for candidate in candidates:
                        cand_qn = candidate["qualified_name"]
                        cand_file = cand_qn.split("::")[0] if "::" in cand_qn else None
                        if caller_file_abs and cand_file == caller_file_abs:
                            best_match = candidate
                            break

                # Priority 2: Imported module match (use graph IMPORTS relationships)
                # Check if the candidate's module is imported by the caller's file
                # Debug: log first few mismatches to understand path format
                if not best_match and caller_file_rel and caller_file_rel not in file_import_modules:
                    if fallback_count < 3:  # Only log first 3
                        logger.debug(f"Import map miss: caller_file_rel={caller_file_rel}, available keys sample: {list(file_import_modules.keys())[:5]}")
                if not best_match and caller_file_rel and caller_file_rel in file_import_modules:
                    imported_modules = file_import_modules[caller_file_rel]
                    for candidate in candidates:
                        cand_qn = candidate["qualified_name"]
                        if "::" in cand_qn:
                            cand_file_abs = cand_qn.split("::")[0]
                            # Convert candidate's file to module path
                            # /home/user/project/repotoire/mcp/models.py -> repotoire.mcp.models
                            try:
                                cand_rel = str(Path(cand_file_abs).relative_to(self.repo_path))
                                if cand_rel.endswith(".py"):
                                    cand_rel = cand_rel[:-3]
                                # cand_rel is like "repotoire/mcp/models" - convert directly
                                cand_module = cand_rel.replace("/", ".")
                                # Check if this module (or entity) is imported
                                entity_name = candidate["qualified_name"].split("::")[-1].split(":")[0]
                                entity_full = cand_module + "." + entity_name
                                if cand_module in imported_modules or entity_full in imported_modules:
                                    best_match = candidate
                                    break
                            except ValueError:
                                # Path is not within repo_path, skip this candidate
                                continue

                # Priority 3: Check if it's a method call on a class we use (via USES relationship)
                # This handles cases like: obj = SomeClass(); obj.method()
                if not best_match and call.get("is_self_call"):
                    # For self calls, prefer methods from classes in same file
                    for candidate in candidates:
                        if candidate["label"] == "Function":
                            cand_qn = candidate["qualified_name"]
                            # Check if this is a method (has class in path)
                            if "::" in cand_qn and "." in cand_qn.split("::")[-1]:
                                best_match = candidate
                                break

                # Fallback: Use community-aware selection if available
                if not best_match and candidates:
                    if len(candidates) == 1:
                        best_match = candidates[0]
                    elif qn_to_community and caller_qn in qn_to_community:
                        # Use community membership to pick best candidate
                        caller_comm = qn_to_community[caller_qn]
                        scored_candidates = []
                        for candidate in candidates:
                            cand_qn = candidate["qualified_name"]
                            if cand_qn in qn_to_community:
                                cand_comm = qn_to_community[cand_qn]
                                # Score: 2 for same community, 1 for any community, 0 for no community
                                score = 2 if cand_comm == caller_comm else 1
                            else:
                                score = 0
                            scored_candidates.append((score, candidate))
                        # Pick highest scored candidate
                        scored_candidates.sort(key=lambda x: x[0], reverse=True)
                        best_match = scored_candidates[0][1]
                        community_guided_fallback += 1
                    else:
                        # No community info, use first candidate
                        best_match = candidates[0]
                    fallback_count += 1

                if best_match:
                    # Collect resolution data for batch processing
                    resolutions.append({
                        "caller_qn": caller_qn,
                        "old_target_qn": call["target_qn"],
                        "new_target_qn": best_match["qualified_name"],
                        "line": call.get("line"),
                        "call_name": call.get("call_name"),
                        "is_self_call": call.get("is_self_call", False),
                    })

            # Step 4: Execute batch resolution query
            # Using UNWIND for efficient bulk processing instead of per-call queries
            if resolutions:
                BATCH_SIZE = 10000  # Chunk to avoid memory issues with huge batches
                resolve_query = """
                UNWIND $resolutions AS res
                MATCH (caller:Function {qualifiedName: res.caller_qn})-[r:CALLS]->(old_target {qualifiedName: res.old_target_qn})
                MATCH (new_target {qualifiedName: res.new_target_qn})
                DELETE r
                MERGE (caller)-[r2:CALLS]->(new_target)
                ON CREATE SET r2.line = res.line, r2.call_name = res.call_name, r2.is_self_call = res.is_self_call, r2.resolved = true
                RETURN count(r2) as created
                """
                try:
                    for i in range(0, len(resolutions), BATCH_SIZE):
                        chunk = resolutions[i:i + BATCH_SIZE]
                        if len(resolutions) > BATCH_SIZE:
                            logger.debug(f"Processing resolution batch {i // BATCH_SIZE + 1}: {len(chunk)} calls")
                        result = self.db.execute_query(resolve_query, {"resolutions": chunk})
                        if result and result[0]["created"] > 0:
                            calls_resolved += result[0]["created"]
                    logger.info(f"Batch resolved {calls_resolved} calls to internal entities")
                except Exception as e:
                    logger.warning(f"Batch call resolution failed: {e}")
                    # Fall back to individual queries for debugging
                    logger.debug("Attempting individual call resolution for debugging...")
                    for res in resolutions[:10]:  # Try first 10 to diagnose
                        try:
                            single_query = """
                            MATCH (caller:Function {qualifiedName: $caller_qn})-[r:CALLS]->(old_target {qualifiedName: $old_target_qn})
                            MATCH (new_target {qualifiedName: $new_target_qn})
                            DELETE r
                            MERGE (caller)-[r2:CALLS]->(new_target)
                            ON CREATE SET r2.line = $line, r2.call_name = $call_name, r2.is_self_call = $is_self_call, r2.resolved = true
                            RETURN count(r2) as created
                            """
                            single_result = self.db.execute_query(single_query, res)
                            if single_result and single_result[0]["created"] > 0:
                                calls_resolved += 1
                        except Exception as inner_e:
                            logger.debug(f"Individual resolution failed for {res['caller_qn']}: {inner_e}")

            # Log resolution quality metrics
            if calls_resolved > 0:
                # Type inference is highest quality, then imports/same-file
                import_same_file_count = calls_resolved - fallback_count - type_inferred_count
                high_quality = type_inferred_count + import_same_file_count
                quality_pct = (high_quality / calls_resolved) * 100 if calls_resolved > 0 else 0
                random_fallback = fallback_count - community_guided_fallback
                logger.info(
                    f"Call resolution quality: {type_inferred_count} type-inferred, "
                    f"{import_same_file_count} via imports/same-file "
                    f"({quality_pct:.1f}% high-quality), "
                    f"{community_guided_fallback} community-guided, "
                    f"{random_fallback} random fallback"
                )

            # Step 5: Clean up orphaned external nodes (no incoming or outgoing relationships)
            cleanup_query = """
            MATCH (n)
            WHERE (n:ExternalFunction OR n:ExternalClass)
              AND NOT (n)-[]-()
            DELETE n
            RETURN count(n) as deleted
            """
            try:
                cleanup_result = self.db.execute_query(cleanup_query)
                if cleanup_result and cleanup_result[0]["deleted"] > 0:
                    logger.debug(f"Cleaned up {cleanup_result[0]['deleted']} orphaned external nodes")
            except Exception as e:
                logger.debug(f"Cleanup query failed (non-critical): {e}")

        except Exception as e:
            logger.warning(f"Failed to resolve internal calls: {e}")

        return calls_resolved

    def _validate_calls_with_graph(self) -> Dict[str, int]:
        """Validate resolved calls using graph-based community detection.

        Uses Rust-powered Leiden community detection and graph algorithms
        to score call resolutions by structural similarity.

        Returns:
            Dict with validation statistics:
            - high_confidence: Calls within same community
            - medium_confidence: Calls between adjacent communities
            - low_confidence: Calls between distant communities
        """
        stats = {"high_confidence": 0, "medium_confidence": 0, "low_confidence": 0}

        try:
            import repotoire_fast as rf
        except ImportError:
            logger.debug("repotoire_fast not available, skipping graph validation")
            return stats

        try:
            # Step 1: Get all internal Function nodes with indices
            nodes_query = """
            MATCH (f:Function)
            WHERE f.qualifiedName IS NOT NULL AND f.qualifiedName CONTAINS '::'
            RETURN f.qualifiedName as qn
            ORDER BY f.qualifiedName
            """
            nodes_result = self.db.execute_query(nodes_query)
            if not nodes_result:
                return stats

            # Build qualified name -> index mapping
            qn_to_idx: Dict[str, int] = {}
            for idx, row in enumerate(nodes_result):
                qn_to_idx[row["qn"]] = idx

            num_nodes = len(qn_to_idx)
            if num_nodes < 2:
                return stats

            # Step 2: Get all CALLS edges between internal Functions
            edges_query = """
            MATCH (caller:Function)-[:CALLS]->(callee:Function)
            WHERE caller.qualifiedName IS NOT NULL AND caller.qualifiedName CONTAINS '::'
              AND callee.qualifiedName IS NOT NULL AND callee.qualifiedName CONTAINS '::'
            RETURN caller.qualifiedName as caller_qn, callee.qualifiedName as callee_qn
            """
            edges_result = self.db.execute_query(edges_query)

            # Convert to (src_idx, dst_idx) tuples
            edges: List[tuple] = []
            calls: List[tuple] = []  # For validation
            for row in edges_result:
                src_idx = qn_to_idx.get(row["caller_qn"])
                dst_idx = qn_to_idx.get(row["callee_qn"])
                if src_idx is not None and dst_idx is not None:
                    edges.append((src_idx, dst_idx))
                    calls.append((src_idx, dst_idx))

            if not edges:
                return stats

            # Step 3: Run Leiden community detection
            logger.debug(f"Running Leiden community detection on {num_nodes} nodes, {len(edges)} edges")
            communities = rf.graph_leiden_parallel(edges, num_nodes, resolution=1.0, max_iterations=10, parallel=True)

            # Step 4: Validate calls by community membership
            confidences = rf.graph_validate_calls(calls, communities, edges, num_nodes)

            # Step 5: Count confidence levels
            for conf in confidences:
                if conf >= 0.9:
                    stats["high_confidence"] += 1
                elif conf >= 0.4:
                    stats["medium_confidence"] += 1
                else:
                    stats["low_confidence"] += 1

            # Log summary
            total = sum(stats.values())
            if total > 0:
                high_pct = 100 * stats["high_confidence"] / total
                med_pct = 100 * stats["medium_confidence"] / total
                low_pct = 100 * stats["low_confidence"] / total
                logger.info(
                    f"Graph-based call validation: {stats['high_confidence']} high ({high_pct:.1f}%), "
                    f"{stats['medium_confidence']} medium ({med_pct:.1f}%), "
                    f"{stats['low_confidence']} low ({low_pct:.1f}%)"
                )

                # Determine number of communities detected
                num_communities = len(set(communities))
                logger.debug(f"Detected {num_communities} code communities via Leiden algorithm")

        except Exception as e:
            logger.debug(f"Graph validation failed (non-critical): {e}")

        return stats

    def _report_skipped_files(self) -> None:
        """Report skipped files summary."""
        if not self.skipped_files:
            return

        logger.warning(f"\n{'='*60}")
        logger.warning(f"SKIPPED FILES SUMMARY: {len(self.skipped_files)} files were skipped")
        logger.warning(f"{'='*60}")

        # Group by reason
        reasons: Dict[str, List[str]] = defaultdict(list)
        for item in self.skipped_files:
            reason = item["reason"]
            reasons[reason].append(item["file"])

        for reason, files in reasons.items():
            logger.warning(f"\n{reason}: {len(files)} files")
            for file in files[:5]:  # Show first 5
                logger.warning(f"  - {file}")
            if len(files) > 5:
                logger.warning(f"  ... and {len(files) - 5} more")

        logger.warning(f"\n{'='*60}\n")

    def _detect_language(self, file_path: Path) -> str:
        """Detect programming language from file extension.

        Args:
            file_path: Path to file

        Returns:
            Language identifier
        """
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
        }

        return extension_map.get(file_path.suffix, "unknown")
