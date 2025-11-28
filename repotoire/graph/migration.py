"""Graph data migration utilities for Neo4j <-> FalkorDB.

Provides export, import, and validation for migrating graph data
between different database backends.
"""

import json
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict

from repotoire.graph.base import DatabaseClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class MigrationStats:
    """Statistics from a migration operation."""
    nodes_exported: int = 0
    relationships_exported: int = 0
    nodes_imported: int = 0
    relationships_imported: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


@dataclass
class ValidationResult:
    """Result of schema validation."""
    valid: bool
    node_count_match: bool
    relationship_count_match: bool
    source_stats: Dict[str, int]
    target_stats: Dict[str, int]
    issues: List[str]


class GraphMigration:
    """Handles export, import, and validation of graph data.

    Supports migration between Neo4j and FalkorDB with automatic
    handling of compatibility differences (temporal types, etc.).
    """

    # Node labels to export
    NODE_LABELS = [
        "File", "Module", "Class", "Function", "Variable",
        "Attribute", "Concept", "Rule"
    ]

    # Relationship types to export
    RELATIONSHIP_TYPES = [
        "IMPORTS", "CALLS", "CONTAINS", "INHERITS", "USES",
        "DEFINES", "DESCRIBES", "MODIFIED"
    ]

    def __init__(self, client: DatabaseClient):
        """Initialize migration handler.

        Args:
            client: Database client for operations
        """
        self.client = client
        self._is_falkordb = getattr(client, 'is_falkordb', False)

    def export_graph(self, output_path: Path, compress: bool = True) -> MigrationStats:
        """Export graph data to JSON file.

        Args:
            output_path: Path to output file
            compress: Whether to gzip compress the output

        Returns:
            Migration statistics
        """
        stats = MigrationStats()
        export_data = {
            "metadata": {
                "exported_at": datetime.utcnow().isoformat(),
                "source_backend": "falkordb" if self._is_falkordb else "neo4j",
                "version": "1.0"
            },
            "nodes": [],
            "relationships": []
        }

        # Export nodes by label
        for label in self.NODE_LABELS:
            try:
                nodes = self._export_nodes_by_label(label)
                export_data["nodes"].extend(nodes)
                stats.nodes_exported += len(nodes)
                logger.info(f"Exported {len(nodes)} {label} nodes")
            except Exception as e:
                error_msg = f"Failed to export {label} nodes: {e}"
                logger.error(error_msg)
                stats.errors.append(error_msg)

        # Export relationships by type
        for rel_type in self.RELATIONSHIP_TYPES:
            try:
                rels = self._export_relationships_by_type(rel_type)
                export_data["relationships"].extend(rels)
                stats.relationships_exported += len(rels)
                logger.info(f"Exported {len(rels)} {rel_type} relationships")
            except Exception as e:
                error_msg = f"Failed to export {rel_type} relationships: {e}"
                logger.error(error_msg)
                stats.errors.append(error_msg)

        # Write to file
        output_path = Path(output_path)
        if compress:
            if not str(output_path).endswith('.gz'):
                output_path = Path(str(output_path) + '.gz')
            with gzip.open(output_path, 'wt', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, default=str)
        else:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, default=str)

        logger.info(f"Export complete: {stats.nodes_exported} nodes, {stats.relationships_exported} relationships")
        return stats

    def _export_nodes_by_label(self, label: str) -> List[Dict]:
        """Export all nodes with a specific label."""
        query = f"""
        MATCH (n:{label})
        RETURN n, labels(n) as labels
        """

        results = self.client.execute_query(query)
        nodes = []

        for record in results:
            node_data = record.get("n", {})
            if isinstance(node_data, dict):
                # Already a dict from FalkorDB
                props = node_data
            else:
                # Neo4j node object
                props = dict(node_data) if hasattr(node_data, '__iter__') else {}

            # Convert temporal types to UNIX timestamps
            props = self._normalize_temporal_values(props)

            nodes.append({
                "labels": record.get("labels", [label]),
                "properties": props
            })

        return nodes

    def _export_relationships_by_type(self, rel_type: str) -> List[Dict]:
        """Export all relationships of a specific type."""
        query = f"""
        MATCH (a)-[r:{rel_type}]->(b)
        RETURN
            a.qualifiedName as source,
            b.qualifiedName as target,
            type(r) as type,
            properties(r) as properties
        """

        results = self.client.execute_query(query)
        relationships = []

        for record in results:
            props = record.get("properties", {}) or {}
            props = self._normalize_temporal_values(props)

            relationships.append({
                "source": record.get("source"),
                "target": record.get("target"),
                "type": record.get("type", rel_type),
                "properties": props
            })

        return relationships

    def _normalize_temporal_values(self, props: Dict) -> Dict:
        """Convert Neo4j temporal types to UNIX timestamps."""
        normalized = {}
        for key, value in props.items():
            if value is None:
                normalized[key] = None
            elif hasattr(value, 'timestamp'):  # datetime-like
                normalized[key] = int(value.timestamp())
            elif hasattr(value, 'isoformat'):  # date-like
                normalized[key] = value.isoformat()
            else:
                normalized[key] = value
        return normalized

    def import_graph(
        self,
        input_path: Path,
        clear_existing: bool = False,
        batch_size: int = 100
    ) -> MigrationStats:
        """Import graph data from JSON file.

        Args:
            input_path: Path to input file
            clear_existing: Whether to clear existing data first
            batch_size: Number of nodes/relationships per batch

        Returns:
            Migration statistics
        """
        stats = MigrationStats()

        # Load data
        input_path = Path(input_path)
        if str(input_path).endswith('.gz'):
            with gzip.open(input_path, 'rt', encoding='utf-8') as f:
                import_data = json.load(f)
        else:
            with open(input_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)

        logger.info(f"Loaded {len(import_data.get('nodes', []))} nodes, "
                   f"{len(import_data.get('relationships', []))} relationships")

        # Clear existing data if requested
        if clear_existing:
            logger.warning("Clearing existing graph data...")
            self.client.clear_graph()

        # Import nodes
        nodes = import_data.get("nodes", [])
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            imported = self._import_nodes_batch(batch)
            stats.nodes_imported += imported

        # Import relationships
        relationships = import_data.get("relationships", [])
        for i in range(0, len(relationships), batch_size):
            batch = relationships[i:i + batch_size]
            imported = self._import_relationships_batch(batch)
            stats.relationships_imported += imported

        logger.info(f"Import complete: {stats.nodes_imported} nodes, "
                   f"{stats.relationships_imported} relationships")
        return stats

    def _import_nodes_batch(self, nodes: List[Dict]) -> int:
        """Import a batch of nodes."""
        imported = 0

        for node in nodes:
            labels = node.get("labels", [])
            props = node.get("properties", {})

            if not labels or not props.get("qualifiedName"):
                continue

            label = labels[0] if isinstance(labels, list) else labels

            # Handle vector embeddings for FalkorDB
            if self._is_falkordb and "embedding" in props:
                embedding = props.pop("embedding")
                # Create node first, then add embedding
                query = f"""
                MERGE (n:{label} {{qualifiedName: $qualifiedName}})
                SET n += $props
                """
                self.client.execute_query(query, {
                    "qualifiedName": props["qualifiedName"],
                    "props": props
                })

                # Add embedding with vecf32()
                if embedding:
                    embed_query = f"""
                    MATCH (n:{label} {{qualifiedName: $qualifiedName}})
                    SET n.embedding = vecf32($embedding)
                    """
                    self.client.execute_query(embed_query, {
                        "qualifiedName": props["qualifiedName"],
                        "embedding": embedding
                    })
            else:
                query = f"""
                MERGE (n:{label} {{qualifiedName: $qualifiedName}})
                SET n += $props
                """
                self.client.execute_query(query, {
                    "qualifiedName": props["qualifiedName"],
                    "props": props
                })

            imported += 1

        return imported

    def _import_relationships_batch(self, relationships: List[Dict]) -> int:
        """Import a batch of relationships."""
        imported = 0

        for rel in relationships:
            source = rel.get("source")
            target = rel.get("target")
            rel_type = rel.get("type")
            props = rel.get("properties", {})

            if not source or not target or not rel_type:
                continue

            query = f"""
            MATCH (a {{qualifiedName: $source}})
            MATCH (b {{qualifiedName: $target}})
            MERGE (a)-[r:{rel_type}]->(b)
            SET r += $props
            """

            try:
                self.client.execute_query(query, {
                    "source": source,
                    "target": target,
                    "props": props
                })
                imported += 1
            except Exception as e:
                logger.warning(f"Failed to create relationship {source} -> {target}: {e}")

        return imported

    def validate(self, other_client: Optional[DatabaseClient] = None) -> ValidationResult:
        """Validate graph integrity or compare with another database.

        Args:
            other_client: Optional other database to compare against

        Returns:
            Validation result
        """
        source_stats = self._get_validation_stats()
        issues = []

        if other_client:
            # Compare with other database
            other_migration = GraphMigration(other_client)
            target_stats = other_migration._get_validation_stats()

            node_match = source_stats["total_nodes"] == target_stats["total_nodes"]
            rel_match = source_stats["total_relationships"] == target_stats["total_relationships"]

            if not node_match:
                issues.append(
                    f"Node count mismatch: source={source_stats['total_nodes']}, "
                    f"target={target_stats['total_nodes']}"
                )

            if not rel_match:
                issues.append(
                    f"Relationship count mismatch: source={source_stats['total_relationships']}, "
                    f"target={target_stats['total_relationships']}"
                )

            # Check label counts
            for label, count in source_stats.get("by_label", {}).items():
                target_count = target_stats.get("by_label", {}).get(label, 0)
                if count != target_count:
                    issues.append(f"{label} count mismatch: source={count}, target={target_count}")

            return ValidationResult(
                valid=len(issues) == 0,
                node_count_match=node_match,
                relationship_count_match=rel_match,
                source_stats=source_stats,
                target_stats=target_stats,
                issues=issues
            )
        else:
            # Just validate current database
            return ValidationResult(
                valid=True,
                node_count_match=True,
                relationship_count_match=True,
                source_stats=source_stats,
                target_stats={},
                issues=[]
            )

    def _get_validation_stats(self) -> Dict[str, Any]:
        """Get statistics for validation."""
        stats = {
            "total_nodes": 0,
            "total_relationships": 0,
            "by_label": {},
            "by_rel_type": {}
        }

        # Count nodes by label
        for label in self.NODE_LABELS:
            query = f"MATCH (n:{label}) RETURN count(n) as count"
            result = self.client.execute_query(query)
            count = result[0]["count"] if result else 0
            stats["by_label"][label] = count
            stats["total_nodes"] += count

        # Count relationships by type
        for rel_type in self.RELATIONSHIP_TYPES:
            query = f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count"
            result = self.client.execute_query(query)
            count = result[0]["count"] if result else 0
            stats["by_rel_type"][rel_type] = count
            stats["total_relationships"] += count

        return stats
