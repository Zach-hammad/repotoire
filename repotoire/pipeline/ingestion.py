"""Main ingestion pipeline for processing codebases."""

import time
from pathlib import Path
from typing import Dict, List, Optional, Callable

from repotoire.graph import Neo4jClient, GraphSchema
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
        neo4j_client: Neo4jClient,
        follow_symlinks: bool = DEFAULT_FOLLOW_SYMLINKS,
        max_file_size_mb: float = MAX_FILE_SIZE_MB,
        batch_size: int = DEFAULT_BATCH_SIZE,
        secrets_policy: SecretsPolicy = SecretsPolicy.REDACT,
        generate_clues: bool = False
    ):
        """Initialize ingestion pipeline with security validation.

        Args:
            repo_path: Path to repository root
            neo4j_client: Neo4j database client
            follow_symlinks: Whether to follow symbolic links (default: False for security)
            max_file_size_mb: Maximum file size in MB to process (default: 10MB)
            batch_size: Number of entities to batch before loading to graph (default: 100)
            secrets_policy: Policy for handling detected secrets (default: REDACT)
            generate_clues: Whether to generate AI semantic clues (default: False)

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
        self.parsers: Dict[str, CodeParser] = {}
        self.follow_symlinks = follow_symlinks
        self.max_file_size_mb = max_file_size_mb
        self.batch_size = batch_size
        self.secrets_policy = secrets_policy
        self.generate_clues = generate_clues

        # Track skipped files for reporting
        self.skipped_files: List[Dict[str, str]] = []

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

    def register_parser(self, language: str, parser: CodeParser) -> None:
        """Register a language parser.

        Args:
            language: Language identifier (e.g., 'python', 'typescript')
            parser: Parser instance
        """
        self.parsers[language] = parser
        logger.info(f"Registered parser for {language}")

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

        Args:
            patterns: List of glob patterns to match (default: ['**/*.py'])

        Returns:
            List of validated file paths
        """
        if patterns is None:
            patterns = ["**/*.py"]  # Default to Python files

        files = []
        for pattern in patterns:
            files.extend(self.repo_path.glob(pattern))

        # Filter out common directories to ignore
        ignored_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}
        files = [
            f for f in files
            if f.is_file()
            and not any(ignored in f.parts for ignored in ignored_dirs)
            and not self._should_skip_file(f)
        ]

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
                    # Need to compute current hash
                    import hashlib
                    with open(file_path, "rb") as f:
                        current_hash = hashlib.md5(f.read()).hexdigest()

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

        logger.info("Ingestion complete", extra=log_extra)

        # Report skipped files if any
        if self.skipped_files:
            self._report_skipped_files()

    def _report_skipped_files(self) -> None:
        """Report skipped files summary."""
        if not self.skipped_files:
            return

        logger.warning(f"\n{'='*60}")
        logger.warning(f"SKIPPED FILES SUMMARY: {len(self.skipped_files)} files were skipped")
        logger.warning(f"{'='*60}")

        # Group by reason
        reasons: Dict[str, List[str]] = {}
        for item in self.skipped_files:
            reason = item["reason"]
            if reason not in reasons:
                reasons[reason] = []
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
