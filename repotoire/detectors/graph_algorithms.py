"""Graph algorithm utilities using Neo4j GDS.

This module provides wrappers for Neo4j Graph Data Science algorithms
to analyze code graph structure and identify architectural patterns.

REPO-152: Added community detection (Louvain) and PageRank for pattern recognition.
"""

from typing import Dict, Any, Optional, List
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger
from repotoire.validation import validate_identifier, ValidationError

logger = get_logger(__name__)

# Cache for community assignments to avoid recalculation
_community_cache: Dict[str, int] = {}
_pagerank_cache: Dict[str, float] = {}


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
            f.lineStart as line_number
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
            f.lineStart as line_number
        ORDER BY f.qualifiedName
        """
        return self.client.execute_query(query)

    # -------------------------------------------------------------------------
    # Community Detection (REPO-152)
    # -------------------------------------------------------------------------

    def create_community_projection(
        self,
        projection_name: str = "code-community-graph"
    ) -> bool:
        """Create in-memory graph projection for community detection.

        Projects Functions and their CALLS relationships plus Class-Function
        containment for community analysis.

        Args:
            projection_name: Name for the graph projection

        Returns:
            True if projection created successfully
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")

            # Drop existing projection if it exists
            try:
                drop_query = f"""
                CALL gds.graph.exists('{validated_name}')
                YIELD exists
                WHERE exists = true
                CALL gds.graph.drop('{validated_name}')
                YIELD graphName
                RETURN graphName
                """
                self.client.execute_query(drop_query)
            except Exception:
                pass  # Projection doesn't exist

            # Create projection with Functions and Classes
            # Use CALLS and USES relationships for community structure
            create_query = f"""
            CALL gds.graph.project(
                '{validated_name}',
                ['Function', 'Class'],
                {{
                    CALLS: {{
                        orientation: 'UNDIRECTED'
                    }},
                    CONTAINS: {{
                        orientation: 'UNDIRECTED'
                    }},
                    USES: {{
                        orientation: 'UNDIRECTED'
                    }}
                }}
            )
            YIELD graphName, nodeCount, relationshipCount
            RETURN graphName, nodeCount, relationshipCount
            """
            result = self.client.execute_query(create_query)

            if result:
                logger.info(
                    f"Created community projection '{projection_name}': "
                    f"{result[0]['nodeCount']} nodes, "
                    f"{result[0]['relationshipCount']} relationships"
                )
                return True
            return False

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to create community projection: {e}")
            return False

    def calculate_communities(
        self,
        projection_name: str = "code-community-graph",
        write_property: str = "communityId"
    ) -> Optional[Dict[str, Any]]:
        """Run Louvain community detection algorithm.

        Louvain is a hierarchical clustering algorithm that optimizes modularity.
        It identifies cohesive groups of nodes (code communities) that are
        densely connected internally but sparsely connected externally.

        Args:
            projection_name: Name of the graph projection
            write_property: Property name to store community IDs

        Returns:
            Dictionary with computation results, or None if failed
        """
        global _community_cache

        try:
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.louvain.write('{validated_name}', {{
                writeProperty: '{validated_property}'
            }})
            YIELD nodePropertiesWritten, communityCount, modularity, computeMillis
            RETURN nodePropertiesWritten, communityCount, modularity, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"Louvain community detection complete: "
                    f"{result[0]['communityCount']} communities found, "
                    f"modularity: {result[0]['modularity']:.3f}, "
                    f"computed in {result[0]['computeMillis']}ms"
                )

                # Clear cache since we've recalculated
                _community_cache.clear()

                return result[0]
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Failed to calculate communities (GDS may not be available): {e}")
            return None

    def get_class_community_span(self, qualified_name: str) -> int:
        """Get the number of distinct communities a class's methods span.

        A class with methods in 1-2 communities is cohesive (legitimate pattern).
        A class with methods spanning 3+ communities likely has multiple
        responsibilities (potential god class).

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Number of distinct communities the class's methods belong to
        """
        try:
            query = """
            MATCH (c:Class {qualifiedName: $qualified_name})-[:CONTAINS]->(m:Function)
            WHERE m.communityId IS NOT NULL
            WITH c, collect(DISTINCT m.communityId) AS communities
            RETURN size(communities) AS community_span
            """
            result = self.client.execute_query(query, {"qualified_name": qualified_name})

            if result and result[0].get("community_span") is not None:
                return result[0]["community_span"]

            # If communityId not set, try calculating on-the-fly
            return self._calculate_community_span_fallback(qualified_name)

        except Exception as e:
            logger.debug(f"Failed to get community span for {qualified_name}: {e}")
            return 1  # Default to cohesive

    def _calculate_community_span_fallback(self, qualified_name: str) -> int:
        """Calculate community span without pre-computed community IDs.

        Uses method-to-method call patterns to estimate community structure.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Estimated number of communities (based on method clustering)
        """
        try:
            # Count distinct method clusters based on shared call targets
            query = """
            MATCH (c:Class {qualifiedName: $qualified_name})-[:CONTAINS]->(m:Function)
            OPTIONAL MATCH (m)-[:CALLS]->(target:Function)
            WITH m, collect(DISTINCT target.qualifiedName) AS targets
            WITH collect({method: m.name, targets: targets}) AS method_patterns

            // Count methods that share no call targets with other methods
            // This approximates distinct responsibility clusters
            UNWIND method_patterns AS mp1
            WITH method_patterns, mp1
            WITH mp1,
                 size([mp2 IN method_patterns
                       WHERE mp2.method <> mp1.method
                       AND size([t IN mp1.targets WHERE t IN mp2.targets]) > 0]) AS shared_count
            WITH collect({method: mp1.method, isolated: shared_count = 0}) AS isolation_data

            // Estimate communities: isolated methods + 1 shared cluster
            RETURN size([d IN isolation_data WHERE d.isolated]) + 1 AS estimated_communities
            """
            result = self.client.execute_query(query, {"qualified_name": qualified_name})

            if result and result[0].get("estimated_communities") is not None:
                # Cap at reasonable maximum
                return min(result[0]["estimated_communities"], 10)

            return 1

        except Exception:
            return 1  # Default to cohesive on error

    def get_all_community_assignments(self) -> Dict[str, int]:
        """Get community assignments for all functions.

        Returns:
            Dictionary mapping qualified names to community IDs
        """
        global _community_cache

        if _community_cache:
            return _community_cache

        try:
            query = """
            MATCH (f:Function)
            WHERE f.communityId IS NOT NULL
            RETURN f.qualifiedName AS qualified_name, f.communityId AS community_id
            """
            result = self.client.execute_query(query)

            if result:
                _community_cache = {
                    r["qualified_name"]: r["community_id"]
                    for r in result
                }

            return _community_cache

        except Exception as e:
            logger.debug(f"Failed to get community assignments: {e}")
            return {}

    # -------------------------------------------------------------------------
    # PageRank Importance Scoring (REPO-152)
    # -------------------------------------------------------------------------

    def calculate_pagerank(
        self,
        projection_name: str = "calls-graph",
        write_property: str = "pagerank"
    ) -> Optional[Dict[str, Any]]:
        """Calculate PageRank importance scores for functions.

        PageRank measures the importance of a function based on how many
        other functions call it (and how important those callers are).

        High PageRank functions are core infrastructure - they may be large
        but serve many callers (legitimate). Low PageRank + large size
        suggests a true god class that should be refactored.

        Args:
            projection_name: Name of the graph projection
            write_property: Property name to store PageRank scores

        Returns:
            Dictionary with computation results, or None if failed
        """
        global _pagerank_cache

        try:
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.pageRank.write('{validated_name}', {{
                writeProperty: '{validated_property}',
                maxIterations: 20,
                dampingFactor: 0.85
            }})
            YIELD nodePropertiesWritten, ranIterations, computeMillis
            RETURN nodePropertiesWritten, ranIterations, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"PageRank calculation complete: "
                    f"{result[0]['nodePropertiesWritten']} nodes scored, "
                    f"{result[0]['ranIterations']} iterations in "
                    f"{result[0]['computeMillis']}ms"
                )

                # Clear cache since we've recalculated
                _pagerank_cache.clear()

                return result[0]
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Failed to calculate PageRank (GDS may not be available): {e}")
            return None

    def get_class_importance(self, qualified_name: str) -> float:
        """Get the importance score of a class based on its methods' PageRank.

        Uses the maximum PageRank among the class's methods as the class
        importance score. High importance suggests infrastructure code
        that many other parts depend on.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Importance score (0.0 to 1.0, normalized)
        """
        try:
            query = """
            MATCH (c:Class {qualifiedName: $qualified_name})-[:CONTAINS]->(m:Function)
            WHERE m.pagerank IS NOT NULL
            WITH max(m.pagerank) AS max_pagerank

            // Normalize against global max
            MATCH (f:Function)
            WHERE f.pagerank IS NOT NULL
            WITH max_pagerank, max(f.pagerank) AS global_max

            RETURN CASE WHEN global_max > 0
                        THEN max_pagerank / global_max
                        ELSE 0.0
                   END AS importance
            """
            result = self.client.execute_query(query, {"qualified_name": qualified_name})

            if result and result[0].get("importance") is not None:
                return float(result[0]["importance"])

            # Fallback: estimate importance from caller count
            return self._estimate_importance_fallback(qualified_name)

        except Exception as e:
            logger.debug(f"Failed to get class importance for {qualified_name}: {e}")
            return 0.5  # Neutral default

    def _estimate_importance_fallback(self, qualified_name: str) -> float:
        """Estimate class importance without pre-computed PageRank.

        Uses simple caller count as a proxy for importance.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Estimated importance (0.0 to 1.0)
        """
        try:
            query = """
            MATCH (c:Class {qualifiedName: $qualified_name})-[:CONTAINS]->(m:Function)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(m)
            WITH count(DISTINCT caller) AS caller_count

            // Normalize: 0 callers = 0, 50+ callers = 1.0
            RETURN CASE WHEN caller_count >= 50 THEN 1.0
                        ELSE toFloat(caller_count) / 50.0
                   END AS importance
            """
            result = self.client.execute_query(query, {"qualified_name": qualified_name})

            if result and result[0].get("importance") is not None:
                return float(result[0]["importance"])

            return 0.5

        except Exception:
            return 0.5

    def get_pagerank_statistics(self) -> Optional[Dict[str, float]]:
        """Get statistical summary of PageRank scores.

        Returns:
            Dictionary with min, max, avg, percentiles of PageRank scores
        """
        query = """
        MATCH (f:Function)
        WHERE f.pagerank IS NOT NULL
        RETURN
            min(f.pagerank) AS min_pagerank,
            max(f.pagerank) AS max_pagerank,
            avg(f.pagerank) AS avg_pagerank,
            percentileCont(f.pagerank, 0.5) AS median_pagerank,
            percentileCont(f.pagerank, 0.9) AS p90_pagerank,
            count(f) AS total_functions
        """
        result = self.client.execute_query(query)
        return result[0] if result else None

    # -------------------------------------------------------------------------
    # Combined Analysis (REPO-152)
    # -------------------------------------------------------------------------

    def run_full_analysis(self, projection_name: str = "code-analysis-graph") -> Dict[str, Any]:
        """Run complete graph analysis: communities + PageRank.

        This is the recommended entry point for analysis. Creates projections,
        runs algorithms, and returns combined results.

        Args:
            projection_name: Base name for graph projections

        Returns:
            Dictionary with analysis results and statistics
        """
        results = {
            "gds_available": False,
            "communities": None,
            "pagerank": None,
            "errors": []
        }

        # Check GDS availability
        if not self.check_gds_available():
            results["errors"].append("Neo4j GDS plugin not available")
            logger.warning("Graph algorithms require Neo4j GDS plugin")
            return results

        results["gds_available"] = True

        # Create projections and run algorithms
        try:
            # Community detection
            community_proj = f"{projection_name}-community"
            if self.create_community_projection(community_proj):
                results["communities"] = self.calculate_communities(community_proj)
                self.cleanup_projection(community_proj)
            else:
                results["errors"].append("Failed to create community projection")

            # PageRank (uses call graph)
            calls_proj = f"{projection_name}-calls"
            if self.create_call_graph_projection(calls_proj):
                results["pagerank"] = self.calculate_pagerank(calls_proj)
                self.cleanup_projection(calls_proj)
            else:
                results["errors"].append("Failed to create calls projection")

        except Exception as e:
            results["errors"].append(str(e))
            logger.error(f"Graph analysis failed: {e}")

        return results

    def clear_caches(self) -> None:
        """Clear all cached algorithm results."""
        global _community_cache, _pagerank_cache
        _community_cache.clear()
        _pagerank_cache.clear()
        logger.debug("Cleared graph algorithm caches")

    # -------------------------------------------------------------------------
    # Harmonic Centrality (REPO-173)
    # -------------------------------------------------------------------------

    def calculate_harmonic_centrality(
        self,
        projection_name: str = "calls-graph",
        write_property: str = "harmonic_score"
    ) -> Optional[Dict[str, Any]]:
        """Calculate harmonic centrality for all functions in the call graph.

        Harmonic centrality is a variant of closeness centrality that handles
        disconnected graphs gracefully. It measures how close a node is to all
        other nodes, with infinite distances contributing 0 instead of breaking.

        High harmonic centrality = central coordinator (can reach most functions quickly)
        Low harmonic centrality = isolated/peripheral code

        Args:
            projection_name: Name of the graph projection to use
            write_property: Property name to store harmonic scores

        Returns:
            Dictionary with computation results, or None if failed
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.closeness.harmonic.write('{validated_name}', {{
                writeProperty: '{validated_property}'
            }})
            YIELD nodePropertiesWritten, computeMillis
            RETURN nodePropertiesWritten, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"Calculated harmonic centrality: "
                    f"{result[0]['nodePropertiesWritten']} nodes updated in "
                    f"{result[0]['computeMillis']}ms"
                )
                return result[0]
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to calculate harmonic centrality: {e}")
            return None

    def get_harmonic_statistics(self) -> Optional[Dict[str, float]]:
        """Get statistical summary of harmonic centrality scores.

        Returns:
            Dictionary with min, max, avg, percentiles of harmonic scores
        """
        query = """
        MATCH (f:Function)
        WHERE f.harmonic_score IS NOT NULL
        RETURN
            min(f.harmonic_score) AS min_harmonic,
            max(f.harmonic_score) AS max_harmonic,
            avg(f.harmonic_score) AS avg_harmonic,
            stdev(f.harmonic_score) AS stdev_harmonic,
            percentileCont(f.harmonic_score, 0.05) AS p5_harmonic,
            percentileCont(f.harmonic_score, 0.10) AS p10_harmonic,
            percentileCont(f.harmonic_score, 0.90) AS p90_harmonic,
            percentileCont(f.harmonic_score, 0.95) AS p95_harmonic,
            count(f) AS total_functions
        """
        result = self.client.execute_query(query)
        return result[0] if result else None

    def get_high_harmonic_functions(
        self,
        threshold: float = 0.0,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get functions with high harmonic centrality (central coordinators).

        Args:
            threshold: Minimum harmonic score
            limit: Maximum number of results

        Returns:
            List of function data with harmonic scores
        """
        query = """
        MATCH (f:Function)
        WHERE f.harmonic_score IS NOT NULL
          AND f.harmonic_score > $threshold
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
        WITH f,
             count(DISTINCT caller) AS caller_count,
             count(DISTINCT callee) AS callee_count
        RETURN
            f.qualifiedName AS qualified_name,
            f.name AS name,
            f.harmonic_score AS harmonic_score,
            f.complexity AS complexity,
            f.loc AS loc,
            f.filePath AS file_path,
            f.lineStart AS line_number,
            caller_count,
            callee_count
        ORDER BY f.harmonic_score DESC
        LIMIT $limit
        """
        return self.client.execute_query(query, parameters={
            "threshold": threshold,
            "limit": limit
        })

    def get_low_harmonic_functions(
        self,
        threshold: float = 0.2,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get functions with low harmonic centrality (isolated code).

        Args:
            threshold: Maximum harmonic score to be considered isolated
            limit: Maximum number of results

        Returns:
            List of function data with harmonic scores and connection info
        """
        query = """
        MATCH (f:Function)
        WHERE f.harmonic_score IS NOT NULL
          AND f.harmonic_score < $threshold
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
        WITH f,
             count(DISTINCT caller) AS caller_count,
             count(DISTINCT callee) AS callee_count
        RETURN
            f.qualifiedName AS qualified_name,
            f.name AS name,
            f.harmonic_score AS harmonic_score,
            f.complexity AS complexity,
            f.loc AS loc,
            f.filePath AS file_path,
            f.lineStart AS line_number,
            caller_count,
            callee_count
        ORDER BY f.harmonic_score ASC
        LIMIT $limit
        """
        return self.client.execute_query(query, parameters={
            "threshold": threshold,
            "limit": limit
        })

    # -------------------------------------------------------------------------
    # Enhanced Louvain Analysis (REPO-172)
    # -------------------------------------------------------------------------

    def create_file_import_projection(
        self,
        projection_name: str = "file-import-graph"
    ) -> bool:
        """Create projection for file-level modularity analysis.

        Projects Files and their IMPORTS relationships for community detection
        at the module/package level.

        Args:
            projection_name: Name for the graph projection

        Returns:
            True if projection created successfully
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")

            # Drop existing projection
            try:
                drop_query = f"""
                CALL gds.graph.exists('{validated_name}')
                YIELD exists
                WHERE exists = true
                CALL gds.graph.drop('{validated_name}')
                YIELD graphName
                RETURN graphName
                """
                self.client.execute_query(drop_query)
            except Exception:
                pass

            # Create projection with Files and file-to-file import relationships
            # The graph has File -[IMPORTS]-> Module, so we need a Cypher projection
            # to create virtual edges: f1 imports module m which is contained in f2
            create_query = f"""
            CALL gds.graph.project.cypher(
                '{validated_name}',
                'MATCH (f:File) RETURN id(f) AS id',
                'MATCH (f1:File)-[:IMPORTS]->(m:Module)<-[:CONTAINS]-(f2:File)
                 WHERE f1 <> f2
                 RETURN id(f1) AS source, id(f2) AS target'
            )
            YIELD graphName, nodeCount, relationshipCount
            RETURN graphName, nodeCount, relationshipCount
            """
            result = self.client.execute_query(create_query)

            if result:
                logger.info(
                    f"Created file import projection '{projection_name}': "
                    f"{result[0]['nodeCount']} files, "
                    f"{result[0]['relationshipCount']} imports"
                )
                return True
            return False

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to create file import projection: {e}")
            return False

    def calculate_file_communities(
        self,
        projection_name: str = "file-import-graph",
        write_property: str = "community_id"
    ) -> Optional[Dict[str, Any]]:
        """Run Louvain on file-level graph for modularity analysis.

        Args:
            projection_name: Name of the graph projection
            write_property: Property name to store community IDs

        Returns:
            Dictionary with modularity score and community stats
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.louvain.write('{validated_name}', {{
                writeProperty: '{validated_property}',
                includeIntermediateCommunities: false
            }})
            YIELD nodePropertiesWritten, communityCount, modularity, computeMillis
            RETURN nodePropertiesWritten, communityCount, modularity, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"File community detection complete: "
                    f"{result[0]['communityCount']} communities, "
                    f"modularity: {result[0]['modularity']:.3f}"
                )
                return result[0]
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Failed to calculate file communities: {e}")
            return None

    def get_community_statistics(self) -> Optional[Dict[str, Any]]:
        """Get detailed statistics about detected communities.

        Returns:
            Dictionary with community sizes, distribution, coupling metrics
        """
        query = """
        MATCH (f:File)
        WHERE f.community_id IS NOT NULL
        WITH f.community_id AS community, collect(f) AS files
        WITH community, size(files) AS community_size, files
        ORDER BY community_size DESC
        WITH collect({
            community_id: community,
            size: community_size,
            files: [file IN files | file.qualifiedName]
        }) AS communities

        // Calculate overall stats
        UNWIND communities AS c
        WITH communities,
             count(c) AS total_communities,
             avg(c.size) AS avg_size,
             max(c.size) AS max_size,
             min(c.size) AS min_size

        RETURN
            total_communities,
            avg_size,
            max_size,
            min_size,
            communities[0..10] AS largest_communities
        """
        result = self.client.execute_query(query)
        return result[0] if result else None

    def get_inter_community_edges(self) -> List[Dict[str, Any]]:
        """Get edges that cross community boundaries.

        These represent coupling between modules and potential
        architectural issues.

        Returns:
            List of inter-community edges with source/target communities
        """
        query = """
        MATCH (f1:File)-[r:IMPORTS]->(f2:File)
        WHERE f1.community_id IS NOT NULL
          AND f2.community_id IS NOT NULL
          AND f1.community_id <> f2.community_id
        WITH f1.community_id AS source_community,
             f2.community_id AS target_community,
             count(r) AS edge_count,
             collect(DISTINCT f1.qualifiedName)[0..3] AS source_files,
             collect(DISTINCT f2.qualifiedName)[0..3] AS target_files
        RETURN source_community, target_community, edge_count, source_files, target_files
        ORDER BY edge_count DESC
        LIMIT 50
        """
        return self.client.execute_query(query)

    def get_misplaced_files(self) -> List[Dict[str, Any]]:
        """Find files that might be in the wrong directory.

        A file is considered potentially misplaced if most of its
        imports are from a different community than its own.

        Returns:
            List of potentially misplaced files with metrics
        """
        query = """
        MATCH (f:File)
        WHERE f.community_id IS NOT NULL
        OPTIONAL MATCH (f)-[:IMPORTS]->(imported:File)
        WHERE imported.community_id IS NOT NULL
        WITH f,
             f.community_id AS own_community,
             collect(imported.community_id) AS import_communities
        WHERE size(import_communities) > 0
        WITH f, own_community, import_communities,
             size([c IN import_communities WHERE c = own_community]) AS same_community_imports,
             size([c IN import_communities WHERE c <> own_community]) AS other_community_imports
        WHERE other_community_imports > same_community_imports
          AND other_community_imports >= 2
        WITH f, own_community, same_community_imports, other_community_imports,
             toFloat(other_community_imports) / size(import_communities) AS external_ratio
        ORDER BY external_ratio DESC
        LIMIT 50
        RETURN
            f.qualifiedName AS qualified_name,
            f.filePath AS file_path,
            own_community AS current_community,
            same_community_imports,
            other_community_imports,
            external_ratio
        """
        return self.client.execute_query(query)

    def get_god_modules(self, threshold_percent: float = 20.0) -> List[Dict[str, Any]]:
        """Find communities that are too large (god modules).

        Args:
            threshold_percent: Percentage of total files to be a god module

        Returns:
            List of oversized communities
        """
        query = """
        MATCH (f:File)
        WHERE f.community_id IS NOT NULL
        WITH count(f) AS total_files
        MATCH (f2:File)
        WHERE f2.community_id IS NOT NULL
        WITH total_files, f2.community_id AS community, count(f2) AS community_size
        WITH total_files, community, community_size,
             toFloat(community_size) / total_files * 100 AS percentage
        WHERE percentage >= $threshold
        RETURN
            community AS community_id,
            community_size,
            percentage,
            total_files
        ORDER BY community_size DESC
        """
        return self.client.execute_query(query, parameters={
            "threshold": threshold_percent
        })

    # -------------------------------------------------------------------------
    # Strongly Connected Components (SCC) - REPO-170
    # -------------------------------------------------------------------------

    def calculate_scc(
        self,
        projection_name: str = "imports-graph",
        write_property: str = "scc_component"
    ) -> Optional[Dict[str, Any]]:
        """Calculate Strongly Connected Components using Tarjan's algorithm.

        SCC finds cycles in directed graphs in O(V+E) time - 10-100x faster
        than pairwise path queries. Each node is assigned a component ID;
        components with size > 1 represent circular dependencies.

        Args:
            projection_name: Name of the graph projection
            write_property: Property name to store component IDs

        Returns:
            Dictionary with computation results, or None if failed
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")
            validated_property = validate_identifier(write_property, "property name")

            query = f"""
            CALL gds.scc.write('{validated_name}', {{
                writeProperty: '{validated_property}'
            }})
            YIELD componentCount, nodePropertiesWritten, computeMillis
            RETURN componentCount, nodePropertiesWritten, computeMillis
            """
            result = self.client.execute_query(query)

            if result:
                logger.info(
                    f"SCC calculation complete: "
                    f"{result[0]['componentCount']} components found, "
                    f"{result[0]['nodePropertiesWritten']} nodes labeled in "
                    f"{result[0]['computeMillis']}ms"
                )
                return result[0]
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Failed to calculate SCC (GDS may not be available): {e}")
            return None

    def get_scc_cycles(
        self,
        min_cycle_size: int = 2,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Get strongly connected components representing circular dependencies.

        Components with size > 1 are cycles. Returns cycle details including
        all files involved and the cycle size.

        Args:
            min_cycle_size: Minimum number of files in a cycle (default 2)
            max_results: Maximum cycles to return

        Returns:
            List of cycles with their member files
        """
        query = """
        MATCH (f:File)
        WHERE f.scc_component IS NOT NULL
        WITH f.scc_component AS component_id, collect(f) AS files
        WHERE size(files) >= $min_size
        UNWIND files AS f
        OPTIONAL MATCH (f)-[:IMPORTS]->(imported:File)
        WHERE imported.scc_component = component_id
        WITH component_id, files,
             collect(DISTINCT {from: f.qualifiedName, to: imported.qualifiedName}) AS cycle_edges
        RETURN
            component_id,
            size(files) AS cycle_size,
            [file IN files | file.qualifiedName] AS file_names,
            [file IN files | file.filePath] AS file_paths,
            [edge IN cycle_edges WHERE edge.to IS NOT NULL] AS edges
        ORDER BY cycle_size DESC
        LIMIT $limit
        """
        return self.client.execute_query(query, parameters={
            "min_size": min_cycle_size,
            "limit": max_results
        })

    # -------------------------------------------------------------------------
    # Degree Centrality - REPO-171
    # -------------------------------------------------------------------------

    def calculate_degree_centrality(
        self,
        projection_name: str = "imports-graph",
        relationship_orientation: str = "NATURAL"
    ) -> Optional[Dict[str, Any]]:
        """Calculate degree centrality for nodes in the graph.

        Degree centrality counts connections:
        - In-degree: How many files import this file (high = core/utility)
        - Out-degree: How many files this file imports (high = potential coupling)

        Args:
            projection_name: Name of the graph projection
            relationship_orientation: NATURAL, REVERSE, or UNDIRECTED

        Returns:
            Dictionary with computation results, or None if failed
        """
        try:
            validated_name = validate_identifier(projection_name, "projection name")

            # Calculate in-degree (who depends on me)
            in_degree_query = f"""
            CALL gds.degree.write('{validated_name}', {{
                writeProperty: 'in_degree',
                orientation: 'REVERSE'
            }})
            YIELD nodePropertiesWritten, computeMillis
            RETURN nodePropertiesWritten, computeMillis, 'in_degree' AS type
            """

            # Calculate out-degree (who do I depend on)
            out_degree_query = f"""
            CALL gds.degree.write('{validated_name}', {{
                writeProperty: 'out_degree',
                orientation: 'NATURAL'
            }})
            YIELD nodePropertiesWritten, computeMillis
            RETURN nodePropertiesWritten, computeMillis, 'out_degree' AS type
            """

            in_result = self.client.execute_query(in_degree_query)
            out_result = self.client.execute_query(out_degree_query)

            if in_result and out_result:
                logger.info(
                    f"Degree centrality complete: "
                    f"in-degree ({in_result[0]['computeMillis']}ms), "
                    f"out-degree ({out_result[0]['computeMillis']}ms)"
                )
                return {
                    "in_degree_nodes": in_result[0]["nodePropertiesWritten"],
                    "out_degree_nodes": out_result[0]["nodePropertiesWritten"],
                    "compute_millis": in_result[0]["computeMillis"] + out_result[0]["computeMillis"]
                }
            return None

        except ValidationError:
            raise
        except Exception as e:
            logger.warning(f"Failed to calculate degree centrality (GDS may not be available): {e}")
            return None

    def get_high_indegree_nodes(
        self,
        percentile: float = 95.0,
        min_degree: int = 5,
        node_label: str = "File"
    ) -> List[Dict[str, Any]]:
        """Get nodes with high in-degree (many dependents).

        High in-degree indicates core/utility code that many other files
        depend on. Combined with high complexity, suggests a god class
        or architectural bottleneck.

        Args:
            percentile: Percentile threshold (default 95th)
            min_degree: Minimum in-degree to consider
            node_label: Node label to filter (File, Class, Function)

        Returns:
            List of high in-degree nodes with metrics
        """
        validated_label = validate_identifier(node_label, "node label")

        query = f"""
        MATCH (n:{validated_label})
        WHERE n.in_degree IS NOT NULL AND n.in_degree >= $min_degree
        WITH n, n.in_degree AS degree
        ORDER BY degree DESC
        WITH collect({{node: n, degree: degree}}) AS all_nodes
        WITH all_nodes,
             toInteger(size(all_nodes) * (1 - $percentile / 100.0)) AS threshold_idx
        WITH all_nodes,
             CASE WHEN threshold_idx < size(all_nodes)
                  THEN all_nodes[threshold_idx].degree
                  ELSE 0 END AS threshold_degree
        UNWIND all_nodes AS item
        WITH item.node AS n, item.degree AS degree, threshold_degree
        WHERE degree >= threshold_degree
        RETURN
            n.qualifiedName AS qualified_name,
            n.filePath AS file_path,
            degree AS in_degree,
            n.out_degree AS out_degree,
            n.complexity AS complexity,
            n.line_count AS line_count,
            threshold_degree AS threshold
        ORDER BY degree DESC
        LIMIT 100
        """
        return self.client.execute_query(query, parameters={
            "percentile": percentile,
            "min_degree": min_degree
        })

    def get_high_outdegree_nodes(
        self,
        percentile: float = 95.0,
        min_degree: int = 10,
        node_label: str = "File"
    ) -> List[Dict[str, Any]]:
        """Get nodes with high out-degree (many dependencies).

        High out-degree indicates a file that depends on many other files.
        This can suggest:
        - Feature envy (reaching into other modules)
        - Tight coupling
        - Potential for cascading changes

        Args:
            percentile: Percentile threshold (default 95th)
            min_degree: Minimum out-degree to consider
            node_label: Node label to filter (File, Class, Function)

        Returns:
            List of high out-degree nodes with metrics
        """
        validated_label = validate_identifier(node_label, "node label")

        query = f"""
        MATCH (n:{validated_label})
        WHERE n.out_degree IS NOT NULL AND n.out_degree >= $min_degree
        WITH n, n.out_degree AS degree
        ORDER BY degree DESC
        WITH collect({{node: n, degree: degree}}) AS all_nodes
        WITH all_nodes,
             toInteger(size(all_nodes) * (1 - $percentile / 100.0)) AS threshold_idx
        WITH all_nodes,
             CASE WHEN threshold_idx < size(all_nodes)
                  THEN all_nodes[threshold_idx].degree
                  ELSE 0 END AS threshold_degree
        UNWIND all_nodes AS item
        WITH item.node AS n, item.degree AS degree, threshold_degree
        WHERE degree >= threshold_degree
        RETURN
            n.qualifiedName AS qualified_name,
            n.filePath AS file_path,
            degree AS out_degree,
            n.in_degree AS in_degree,
            n.complexity AS complexity,
            n.line_count AS line_count,
            threshold_degree AS threshold
        ORDER BY degree DESC
        LIMIT 100
        """
        return self.client.execute_query(query, parameters={
            "percentile": percentile,
            "min_degree": min_degree
        })

    def get_degree_statistics(self, node_label: str = "File") -> Dict[str, Any]:
        """Get degree distribution statistics for the graph.

        Useful for determining appropriate thresholds and understanding
        the overall dependency structure.

        Args:
            node_label: Node label to analyze

        Returns:
            Statistics including mean, max, percentiles for in/out degree
        """
        validated_label = validate_identifier(node_label, "node label")

        query = f"""
        MATCH (n:{validated_label})
        WHERE n.in_degree IS NOT NULL OR n.out_degree IS NOT NULL
        WITH
            collect(coalesce(n.in_degree, 0)) AS in_degrees,
            collect(coalesce(n.out_degree, 0)) AS out_degrees
        RETURN
            size(in_degrees) AS node_count,
            // In-degree stats
            reduce(sum = 0.0, d IN in_degrees | sum + d) / size(in_degrees) AS avg_in_degree,
            reduce(max = 0, d IN in_degrees | CASE WHEN d > max THEN d ELSE max END) AS max_in_degree,
            // Out-degree stats
            reduce(sum = 0.0, d IN out_degrees | sum + d) / size(out_degrees) AS avg_out_degree,
            reduce(max = 0, d IN out_degrees | CASE WHEN d > max THEN d ELSE max END) AS max_out_degree
        """
        result = self.client.execute_query(query)
        return result[0] if result else {}
