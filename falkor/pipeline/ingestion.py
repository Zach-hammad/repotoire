"""Main ingestion pipeline for processing codebases."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from falkor.graph import Neo4jClient, GraphSchema
from falkor.parsers import CodeParser, PythonParser
from falkor.models import Entity, Relationship

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Pipeline for ingesting code into the knowledge graph."""

    def __init__(self, repo_path: str, neo4j_client: Neo4jClient):
        """Initialize ingestion pipeline.

        Args:
            repo_path: Path to repository root
            neo4j_client: Neo4j database client
        """
        self.repo_path = Path(repo_path)
        self.db = neo4j_client
        self.parsers: Dict[str, CodeParser] = {}

        # Register default parsers
        self.register_parser("python", PythonParser())

    def register_parser(self, language: str, parser: CodeParser) -> None:
        """Register a language parser.

        Args:
            language: Language identifier (e.g., 'python', 'typescript')
            parser: Parser instance
        """
        self.parsers[language] = parser
        logger.info(f"Registered parser for {language}")

    def scan(self, patterns: Optional[List[str]] = None) -> List[Path]:
        """Scan repository for source files.

        Args:
            patterns: List of glob patterns to match (default: ['**/*.py'])

        Returns:
            List of file paths
        """
        if patterns is None:
            patterns = ["**/*.py"]  # Default to Python files

        files = []
        for pattern in patterns:
            files.extend(self.repo_path.glob(pattern))

        # Filter out common directories to ignore
        ignored_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}
        files = [
            f for f in files if not any(ignored in f.parts for ignored in ignored_dirs)
        ]

        logger.info(f"Found {len(files)} source files")
        return files

    def parse_and_extract(self, file_path: Path) -> tuple[List[Entity], List[Relationship]]:
        """Parse a file and extract entities/relationships.

        Args:
            file_path: Path to source file

        Returns:
            Tuple of (entities, relationships)
        """
        # Determine language from extension
        language = self._detect_language(file_path)

        if language not in self.parsers:
            logger.warning(f"No parser for {language}, skipping {file_path}")
            return [], []

        parser = self.parsers[language]

        try:
            entities, relationships = parser.process_file(str(file_path))
            logger.debug(
                f"Extracted {len(entities)} entities and {len(relationships)} relationships from {file_path}"
            )
            return entities, relationships
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return [], []

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

        # Batch create nodes and get mapping of qualified_name -> elementId
        try:
            id_mapping = self.db.batch_create_nodes(entities)
            logger.info(f"Created {len(id_mapping)} nodes")

            # Convert relationships to use elementId (create new objects to avoid mutation)
            resolved_rels = []
            logger.debug(f"Processing {len(relationships)} relationships")
            logger.debug(f"ID mapping has {len(id_mapping)} entries")

            for rel in relationships:
                # Map qualified_name to elementId
                source_id = id_mapping.get(rel.source_id, rel.source_id)
                target_id = id_mapping.get(rel.target_id, rel.target_id)

                logger.debug(f"Relationship: {rel.rel_type} from {rel.source_id[:50]}... to {rel.target_id[:50]}...")
                logger.debug(f"  Source mapped: {source_id[:50] if isinstance(source_id, str) else source_id}...")
                logger.debug(f"  Target mapped: {target_id[:50] if isinstance(target_id, str) else target_id}...")

                # Create new relationship with resolved IDs
                resolved_rel = Relationship(
                    source_id=source_id,
                    target_id=target_id,
                    rel_type=rel.rel_type,
                    properties=rel.properties,
                )
                resolved_rels.append(resolved_rel)

            # Batch create all relationships at once
            if resolved_rels:
                self.db.batch_create_relationships(resolved_rels)
                logger.info(f"Created {len(relationships)} relationships")
            else:
                logger.warning("No relationships to create")

        except Exception as e:
            logger.error(f"Failed to load data to graph: {e}")

    def ingest(self, incremental: bool = False, patterns: Optional[List[str]] = None) -> None:
        """Run the complete ingestion pipeline.

        Args:
            incremental: If True, only process changed files
            patterns: File patterns to match
        """
        logger.info(f"Starting ingestion of {self.repo_path}")

        # Initialize schema
        schema = GraphSchema(self.db)
        schema.initialize()

        # Scan for files
        files = self.scan(patterns)

        if not files:
            logger.warning("No files found to process")
            return

        # Process each file
        all_entities = []
        all_relationships = []

        for i, file_path in enumerate(files, 1):
            logger.info(f"Processing {i}/{len(files)}: {file_path}")

            entities, relationships = self.parse_and_extract(file_path)
            all_entities.extend(entities)
            all_relationships.extend(relationships)

            # Batch load every 10 files for better performance
            if len(all_entities) >= 100:
                self.load_to_graph(all_entities, all_relationships)
                all_entities = []
                all_relationships = []

        # Load remaining entities
        if all_entities:
            self.load_to_graph(all_entities, all_relationships)

        # Show stats
        stats = self.db.get_stats()
        logger.info(f"Ingestion complete! Stats: {stats}")

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
