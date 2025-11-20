"""Graph algorithm utilities using Neo4j GDS.

This module provides wrappers for Neo4j Graph Data Science algorithms
to analyze code graph structure and identify architectural patterns.
"""

from typing import Dict, Any, Optional, List
from falkor.graph.client import Neo4jClient
from falkor.logging_config import get_logger
from falkor.validation import validate_identifier, ValidationError

logger = get_logger(__name__)


class GraphAlgorithms:
    """Wrapper for Neo4j GDS graph algorithms."""

    def __init__(self, client: Neo4jClient):
        """Initialize graph algorithms.

        Args:
            client: Neo4j client instance
        """
        self.client = client

    def check_gds_available(self) -> bool:
        """Check if Neo4j GDS plugin is available.

        Returns:
            True if GDS is available, False otherwise
        """
        try:
            query = "RETURN gds.version() as version"
            result = self.client.execute_query(query)
            if result:
                logger.info(f"Neo4j GDS version: {result[0]['version']}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Neo4j GDS not available: {e}")
            return False

    def create_call_graph_projection(self, projection_name: str = "calls-graph") -> bool:
        """Create in-memory graph projection for call graph analysis.

        Args:
            projection_name: Name for the graph projection

        Returns:
            True if projection created successfully
        """
        try:
            # Validate projection name to prevent Cypher injection
            # GDS procedure calls cannot use parameters for procedure names,
            # so we validate the input instead
            validated_name = validate_identifier(projection_name, "projection name")

            # Drop existing projection if it exists
            drop_query = f"""
            CALL gds.graph.exists('{validated_name}')
            YIELD exists
            WHERE exists = true
            CALL gds.graph.drop('{validated_name}')
            YIELD graphName
            RETURN graphName
            """
            try:
                self.client.execute_query(drop_query)
            except Exception:
                pass  # Projection doesn't exist, that's fine

            # Create new projection
            create_query = f"""
            CALL gds.graph.project(
                '{validated_name}',
                'Function',
                'CALLS'
            )
            YIELD graphName, nodeCount, relationshipCount
            RETURN graphName, nodeCount, relationshipCount
            """
            result = self.client.execute_query(create_query)

            if result:
                logger.info(
                    f"Created graph projection '{projection_name}': "
                    f"{result[0]['nodeCount']} nodes, "
                    f"{result[0]['relationshipCount']} relationships"
                )
                return True
            return False

        except ValidationError:
            # Re-raise validation errors (security-related)
            raise
        except Exception as e:
            logger.error(f"Failed to create graph projection: {e}")
            return False

    def calculate_betweenness_centrality(
        self,
        projection_name: str = "calls-graph",
        write_property: str = "betweenness_score"
    ) -> Optional[Dict[str, Any]]:
        """Calculate betweenness centrality for all functions in the call graph.

        Betweenness centrality measures how often a node appears on shortest
        paths between other nodes. High betweenness indicates architectural
        bottlenecks - functions that many execution paths flow through.

        Args:
            projection_name: Name of the graph projection to use
            write_property: Property name to store betweenness scores

        Returns:
            Dictionary with computation results, or None if failed
        """
        try:
            # Validate inputs to prevent Cypher injection
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.betweenness.write('{validated_name}', {{
                writeProperty: '{validated_property}'
            }})
            YIELD nodePropertiesWritten, computeMillis
            RETURN nodePropertiesWritten, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"Calculated betweenness centrality: "
                    f"{result[0]['nodePropertiesWritten']} nodes updated in "
                    f"{result[0]['computeMillis']}ms"
                )
                return result[0]
            return None

        except ValidationError:
            # Re-raise validation errors (security-related)
            raise
        except Exception as e:
            logger.error(f"Failed to calculate betweenness centrality: {e}")
            return None

    def get_high_betweenness_functions(
        self,
        threshold: float = 0.0,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get functions with high betweenness centrality scores.

        Args:
            threshold: Minimum betweenness score (0.0 = all functions)
            limit: Maximum number of results

        Returns:
            List of function data with betweenness scores
        """
        # Use parameterized query to prevent injection
        query = """
        MATCH (f:Function)
        WHERE f.betweenness_score IS NOT NULL
          AND f.betweenness_score > $threshold
        RETURN
            f.qualifiedName as qualified_name,
            f.betweenness_score as betweenness,
            f.complexity as complexity,
            f.loc as loc,
            f.filePath as file_path,
            f.line_start as line_number
        ORDER BY f.betweenness_score DESC
        LIMIT $limit
        """
        return self.client.execute_query(query, parameters={
            "threshold": threshold,
            "limit": limit
        })

    def get_betweenness_statistics(self) -> Optional[Dict[str, float]]:
        """Get statistical summary of betweenness scores.

        Returns:
            Dictionary with min, max, avg, stdev of betweenness scores
        """
        query = """
        MATCH (f:Function)
        WHERE f.betweenness_score IS NOT NULL
        RETURN
            min(f.betweenness_score) as min_betweenness,
            max(f.betweenness_score) as max_betweenness,
            avg(f.betweenness_score) as avg_betweenness,
            stdev(f.betweenness_score) as stdev_betweenness,
            count(f) as total_functions
        """
        result = self.client.execute_query(query)
        return result[0] if result else None

    def cleanup_projection(self, projection_name: str = "calls-graph") -> bool:
        """Drop graph projection to free memory.

        Args:
            projection_name: Name of projection to drop

        Returns:
            True if successfully dropped
        """
        try:
            # Validate projection name to prevent Cypher injection
            validated_name = validate_identifier(projection_name, "projection name")

            query = f"""
            CALL gds.graph.drop('{validated_name}')
            YIELD graphName
            RETURN graphName
            """
            self.client.execute_query(query)
            logger.info(f"Dropped graph projection '{projection_name}'")
            return True
        except ValidationError:
            # Re-raise validation errors (security-related)
            raise
        except Exception as e:
            logger.warning(f"Failed to drop projection '{projection_name}': {e}")
            return False

    def find_entry_points(self) -> List[Dict[str, Any]]:
        """Find entry point functions (not called by any other function in codebase).

        These are potential starting points for execution flow analysis.

        Returns:
            List of entry point function data
        """
        query = """
        MATCH (f:Function)
        WHERE NOT (:Function)-[:CALLS]->(f)
        RETURN
            f.qualifiedName as qualified_name,
            f.name as name,
            f.filePath as file_path,
            f.line_start as line_number
        ORDER BY f.qualifiedName
        """
        return self.client.execute_query(query)
