"""Neo4j database client."""

from typing import Any, Dict, List, Optional
from neo4j import GraphDatabase, Driver, Result
import logging

from falkor.models import Entity, Relationship

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Client for interacting with Neo4j graph database."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        username: str = "neo4j",
        password: str = "password",
    ):
        """Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI
            username: Database username
            password: Database password
        """
        self.driver: Driver = GraphDatabase.driver(uri, auth=(username, password))
        logger.info(f"Connected to Neo4j at {uri}")

    def close(self) -> None:
        """Close database connection."""
        self.driver.close()
        logger.info("Closed Neo4j connection")

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def execute_query(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """Execute a Cypher query and return results.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dictionaries
        """
        with self.driver.session() as session:
            result: Result = session.run(query, parameters or {})
            return [dict(record) for record in result]

    def create_node(self, entity: Entity) -> str:
        """Create a node in the graph.

        Args:
            entity: Entity to create

        Returns:
            Node ID
        """
        query = f"""
        CREATE (n:{entity.node_type.value} {{
            name: $name,
            qualifiedName: $qualified_name,
            filePath: $file_path,
            lineStart: $line_start,
            lineEnd: $line_end,
            docstring: $docstring
        }})
        RETURN elementId(n) as id
        """

        result = self.execute_query(
            query,
            {
                "name": entity.name,
                "qualified_name": entity.qualified_name,
                "file_path": entity.file_path,
                "line_start": entity.line_start,
                "line_end": entity.line_end,
                "docstring": entity.docstring,
            },
        )

        return result[0]["id"]

    def create_relationship(self, rel: Relationship) -> None:
        """Create a relationship between nodes.

        Args:
            rel: Relationship to create
        """
        query = f"""
        MATCH (source), (target)
        WHERE elementId(source) = $source_id AND elementId(target) = $target_id
        CREATE (source)-[r:{rel.rel_type}]->(target)
        SET r = $properties
        """

        self.execute_query(
            query,
            {
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "properties": rel.properties,
            },
        )

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes in a single transaction.

        Args:
            entities: List of entities to create

        Returns:
            Dict mapping qualified_name to elementId
        """
        # Group by type for efficient batch creation
        by_type: Dict[str, List[Entity]] = {}
        for entity in entities:
            type_name = entity.node_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(entity)

        id_mapping: Dict[str, str] = {}

        for node_type, entities_of_type in by_type.items():
            query = f"""
            UNWIND $entities AS entity
            CREATE (n:{node_type})
            SET n = entity
            RETURN elementId(n) as id, entity.qualifiedName as qualifiedName
            """

            entity_dicts = [
                {
                    "name": e.name,
                    "qualifiedName": e.qualified_name,
                    "filePath": e.file_path,
                    "lineStart": e.line_start,
                    "lineEnd": e.line_end,
                    "docstring": e.docstring,
                }
                for e in entities_of_type
            ]

            results = self.execute_query(query, {"entities": entity_dicts})
            for r in results:
                id_mapping[r["qualifiedName"]] = r["id"]

        logger.info(f"Created {len(id_mapping)} nodes")
        return id_mapping

    def clear_graph(self) -> None:
        """Delete all nodes and relationships. Use with caution!"""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_query(query)
        logger.warning("Cleared all nodes from graph")

    def create_indexes(self) -> None:
        """Create indexes for better query performance."""
        indexes = [
            "CREATE INDEX file_path IF NOT EXISTS FOR (f:File) ON (f.filePath)",
            "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.qualifiedName)",
            "CREATE INDEX function_name IF NOT EXISTS FOR (f:Function) ON (f.qualifiedName)",
        ]

        for index_query in indexes:
            self.execute_query(index_query)

        logger.info("Created graph indexes")

    def get_context(self, entity_id: str, depth: int = 1) -> Dict:
        """Get graph context around an entity.

        Args:
            entity_id: Node ID
            depth: Traversal depth

        Returns:
            Context dictionary with connected nodes
        """
        query = """
        MATCH (n)
        WHERE elementId(n) = $entity_id
        CALL apoc.path.subgraphAll(n, {
            maxLevel: $depth,
            relationshipFilter: 'CALLS>|USES>|IMPORTS>'
        })
        YIELD nodes, relationships
        RETURN nodes, relationships
        """

        result = self.execute_query(query, {"entity_id": entity_id, "depth": depth})

        if not result:
            return {}

        return {
            "nodes": [dict(node) for node in result[0].get("nodes", [])],
            "relationships": [dict(rel) for rel in result[0].get("relationships", [])],
        }

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics.

        Returns:
            Dictionary with node/relationship counts
        """
        queries = {
            "total_nodes": "MATCH (n) RETURN count(n) as count",
            "total_files": "MATCH (f:File) RETURN count(f) as count",
            "total_classes": "MATCH (c:Class) RETURN count(c) as count",
            "total_functions": "MATCH (f:Function) RETURN count(f) as count",
            "total_relationships": "MATCH ()-[r]->() RETURN count(r) as count",
        }

        stats = {}
        for key, query in queries.items():
            result = self.execute_query(query)
            stats[key] = result[0]["count"] if result else 0

        return stats
