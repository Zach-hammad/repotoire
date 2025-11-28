"""Graph-aware retrieval for code Q&A using hybrid vector + graph search."""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

from repotoire.graph.base import DatabaseClient
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RetrievalResult:
    """Retrieved code context for RAG.

    Represents a code entity retrieved from the knowledge graph,
    enriched with semantic similarity scores and related entities.

    Attributes:
        entity_type: Type of entity (function, class, file)
        qualified_name: Fully qualified unique name
        name: Simple entity name
        code: Source code snippet
        docstring: Documentation string
        similarity_score: Vector similarity score (0-1)
        relationships: Related entities via graph traversal
        file_path: Source file location
        line_start: Starting line number
        line_end: Ending line number
        metadata: Additional context (decorators, complexity, etc.)
    """

    entity_type: str
    qualified_name: str
    name: str
    code: str
    docstring: str
    similarity_score: float
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class GraphRAGRetriever:
    """Hybrid retrieval combining vector search + graph traversal.

    This retriever is code-aware and leverages Repotoire's existing
    code knowledge graph structure (IMPORTS, CALLS, INHERITS, etc.)
    combined with semantic vector search for optimal results.

    Example:
        >>> retriever = GraphRAGRetriever(neo4j_client, embedder)
        >>> results = retriever.retrieve("How does authentication work?", top_k=10)
        >>> for result in results:
        ...     print(f"{result.qualified_name}: {result.similarity_score}")
    """

    def __init__(
        self,
        client: DatabaseClient,
        embedder: CodeEmbedder,
        context_lines: int = 5
    ):
        """Initialize retriever.

        Args:
            client: Connected database client (Neo4j or FalkorDB)
            embedder: Code embedder for query encoding
            context_lines: Lines of context to include before/after code
        """
        self.client = client
        self.embedder = embedder
        self.context_lines = context_lines
        # Detect if we're using FalkorDB
        self.is_falkordb = type(client).__name__ == "FalkorDBClient"

        logger.info(f"Initialized GraphRAGRetriever (backend: {'FalkorDB' if self.is_falkordb else 'Neo4j'})")

    def get_hot_rules_context(self, top_k: int = 10) -> str:
        """Get context about hot custom rules for RAG prompts.

        Fetches the most relevant custom quality rules based on usage
        patterns and formats them for inclusion in the RAG system prompt.
        This helps the AI assistant suggest relevant code improvements.

        Args:
            top_k: Number of hot rules to include (default: 10)

        Returns:
            Formatted string with rule context for RAG prompts
        """
        from repotoire.rules.engine import RuleEngine

        try:
            engine = RuleEngine(self.client)
            hot_rules = engine.get_hot_rules(top_k=top_k)

            if not hot_rules:
                return ""

            # Format rules for prompt
            context_parts = [
                "## Active Code Quality Rules",
                "",
                "The codebase is governed by the following custom quality rules "
                "(ordered by priority and recent usage):",
                ""
            ]

            for i, rule in enumerate(hot_rules, 1):
                priority = rule.calculate_priority()
                context_parts.extend([
                    f"### {i}. {rule.name}",
                    f"**ID**: {rule.id}",
                    f"**Severity**: {rule.severity.value.upper()}",
                    f"**Priority**: {priority:.1f} (accessed {rule.accessCount} times)",
                    f"**Description**: {rule.description}",
                    ""
                ])

                if rule.autoFix:
                    context_parts.append(f"**Suggested Fix**: {rule.autoFix}")
                    context_parts.append("")

                if rule.tags:
                    context_parts.append(f"**Tags**: {', '.join(rule.tags)}")
                    context_parts.append("")

            context_parts.extend([
                "",
                "When answering questions or making suggestions, consider these rules "
                "and recommend fixes that align with them.",
                ""
            ])

            return "\n".join(context_parts)

        except Exception as e:
            logger.warning(f"Could not fetch hot rules context: {e}")
            return ""

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        entity_types: Optional[List[str]] = None,
        include_related: bool = True
    ) -> List[RetrievalResult]:
        """Retrieve relevant code using hybrid vector + graph search.

        Combines:
        1. Vector similarity search for semantic matching
        2. Graph traversal for structural context
        3. Code snippet extraction from source files

        Args:
            query: Natural language question
            top_k: Number of results to return
            entity_types: Filter by types (e.g., ["Function", "Class"])
            include_related: Whether to fetch related entities via graph

        Returns:
            List of retrieval results ordered by relevance
        """
        logger.info(f"Retrieving for query: {query[:100]}...")

        # Step 1: Encode query as vector
        query_embedding = self.embedder.embed_query(query)

        # Step 2: Vector similarity search
        vector_results = self._vector_search(
            query_embedding,
            top_k=top_k,
            entity_types=entity_types
        )

        # Step 3: Enrich with graph context
        enriched_results = []
        for result in vector_results:
            # Get related entities if requested
            if include_related:
                relationships = self._get_related_entities(result["element_id"])
            else:
                relationships = []

            # Fetch actual source code
            code = self._fetch_code(
                result["file_path"],
                result["line_start"],
                result["line_end"]
            )

            enriched_results.append(
                RetrievalResult(
                    entity_type=result["entity_type"],
                    qualified_name=result["qualified_name"],
                    name=result["name"],
                    code=code,
                    docstring=result.get("docstring", ""),
                    similarity_score=result["score"],
                    relationships=relationships,
                    file_path=result["file_path"],
                    line_start=result["line_start"],
                    line_end=result["line_end"],
                    metadata=result.get("metadata", {})
                )
            )

        logger.info(f"Retrieved {len(enriched_results)} results")
        return enriched_results

    def retrieve_by_path(
        self,
        start_entity: str,
        relationship_types: List[str],
        max_hops: int = 3,
        limit: int = 20
    ) -> List[RetrievalResult]:
        """Retrieve code by following graph relationships.

        Uses pure graph traversal without vector search.
        Useful for queries like "Find all functions that call X".

        Args:
            start_entity: Qualified name of starting entity
            relationship_types: Relationships to follow (e.g., ["CALLS", "USES"])
            max_hops: Maximum traversal depth
            limit: Maximum results to return

        Returns:
            List of retrieval results
        """
        logger.info(
            f"Graph traversal from {start_entity} "
            f"via {relationship_types} (max {max_hops} hops)"
        )

        # Build Cypher query for graph traversal
        rel_pattern = "|".join(relationship_types)
        # FalkorDB uses id() while Neo4j uses elementId()
        id_func = "id" if self.is_falkordb else "elementId"

        query = f"""
        MATCH (start {{qualifiedName: $start_qname}})
        MATCH path = (start)-[:{rel_pattern}*1..{max_hops}]-(target)
        WHERE target.qualifiedName IS NOT NULL
        RETURN DISTINCT
            {id_func}(target) as element_id,
            target.qualifiedName as qualified_name,
            target.name as name,
            labels(target)[0] as entity_type,
            target.docstring as docstring,
            target.filePath as file_path,
            target.lineStart as line_start,
            target.lineEnd as line_end,
            length(path) as distance
        ORDER BY distance ASC
        LIMIT $limit
        """

        results = self.client.execute_query(
            query,
            {"start_qname": start_entity, "limit": limit}
        )

        enriched_results = []
        for result in results:
            # Fetch code and relationships
            code = self._fetch_code(
                result["file_path"],
                result["line_start"],
                result["line_end"]
            )
            relationships = self._get_related_entities(result["element_id"])

            enriched_results.append(
                RetrievalResult(
                    entity_type=result["entity_type"],
                    qualified_name=result["qualified_name"],
                    name=result["name"],
                    code=code,
                    docstring=result.get("docstring", ""),
                    # Closer entities get higher scores
                    similarity_score=1.0 / (result["distance"] + 1),
                    relationships=relationships,
                    file_path=result["file_path"],
                    line_start=result["line_start"],
                    line_end=result["line_end"]
                )
            )

        logger.info(f"Graph traversal returned {len(enriched_results)} results")
        return enriched_results

    def _vector_search(
        self,
        query_embedding: List[float],
        top_k: int,
        entity_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Perform vector similarity search across entity types.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            entity_types: Optional filter by entity types

        Returns:
            List of matching entities with scores
        """
        # Search across all entity types or filtered subset
        search_types = entity_types or ["Function", "Class", "File"]
        all_results = []

        for entity_type in search_types:
            if self.is_falkordb:
                # FalkorDB vector search query
                # Uses db.idx.vector.queryNodes with vecf32() wrapper
                query = f"""
                CALL db.idx.vector.queryNodes(
                    '{entity_type}',
                    'embedding',
                    $top_k,
                    vecf32($embedding)
                ) YIELD node, score
                RETURN
                    id(node) as element_id,
                    node.qualifiedName as qualified_name,
                    node.name as name,
                    '{entity_type}' as entity_type,
                    node.docstring as docstring,
                    node.filePath as file_path,
                    node.lineStart as line_start,
                    node.lineEnd as line_end,
                    score
                ORDER BY score DESC
                """
                params = {
                    "top_k": top_k,
                    "embedding": query_embedding
                }
            else:
                # Neo4j vector search query
                index_name = f"{entity_type.lower()}_embeddings"
                query = """
                CALL db.index.vector.queryNodes(
                    $index_name,
                    $top_k,
                    $embedding
                ) YIELD node, score
                RETURN
                    elementId(node) as element_id,
                    node.qualifiedName as qualified_name,
                    node.name as name,
                    $entity_type as entity_type,
                    node.docstring as docstring,
                    node.filePath as file_path,
                    node.lineStart as line_start,
                    node.lineEnd as line_end,
                    score
                ORDER BY score DESC
                """
                params = {
                    "index_name": index_name,
                    "top_k": top_k,
                    "embedding": query_embedding,
                    "entity_type": entity_type
                }

            try:
                results = self.client.execute_query(query, params)
                all_results.extend(results)
            except Exception as e:
                # Index might not exist yet
                logger.warning(f"Could not search {entity_type} embeddings: {e}")

        # Sort by score and return top_k
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    def _get_related_entities(
        self,
        entity_id: str,
        max_relationships: int = 20
    ) -> List[Dict[str, str]]:
        """Get related entities via graph traversal.

        Fetches entities within 1-2 hops that are connected via
        code relationships (CALLS, IMPORTS, INHERITS, USES, CONTAINS).

        Args:
            entity_id: Database element ID of entity
            max_relationships: Maximum relationships to return

        Returns:
            List of relationship dicts with entity and type
        """
        # FalkorDB uses id() while Neo4j uses elementId()
        id_func = "id" if self.is_falkordb else "elementId"

        query = f"""
        MATCH (start)
        WHERE {id_func}(start) = $id

        // Get direct relationships (1 hop)
        OPTIONAL MATCH (start)-[r1:CALLS|USES|INHERITS|IMPORTS]-(related1)
        WHERE related1.qualifiedName IS NOT NULL

        // Get container relationships (class contains methods)
        OPTIONAL MATCH (start)-[r2:CONTAINS]-(related2)
        WHERE related2.qualifiedName IS NOT NULL

        WITH collect(DISTINCT {{
            entity: related1.qualifiedName,
            relationship: type(r1),
            distance: 1
        }}) + collect(DISTINCT {{
            entity: related2.qualifiedName,
            relationship: type(r2),
            distance: 1
        }}) as relationships

        UNWIND relationships as rel
        RETURN rel.entity as entity,
               rel.relationship as relationship,
               rel.distance as distance
        ORDER BY rel.distance ASC
        LIMIT $max_relationships
        """

        try:
            results = self.client.execute_query(
                query,
                {"id": entity_id, "max_relationships": max_relationships}
            )

            return [
                {
                    "entity": r["entity"],
                    "relationship": r["relationship"]
                }
                for r in results
                if r["entity"]  # Filter out None values
            ]
        except Exception as e:
            logger.warning(f"Could not fetch relationships: {e}")
            return []

    def _fetch_code(
        self,
        file_path: str,
        line_start: int,
        line_end: int
    ) -> str:
        """Fetch actual source code from file.

        Includes extra context lines before and after the entity
        for better understanding.

        Args:
            file_path: Path to source file
            line_start: Starting line (1-indexed)
            line_end: Ending line (1-indexed)

        Returns:
            Source code string with context
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Add context lines
            start_idx = max(0, line_start - self.context_lines - 1)
            end_idx = min(len(lines), line_end + self.context_lines)

            # Join lines and add line numbers for reference
            code_lines = []
            for i in range(start_idx, end_idx):
                line_num = i + 1
                # Highlight the actual entity lines
                if line_start <= line_num <= line_end:
                    prefix = ">>> "
                else:
                    prefix = "    "
                code_lines.append(f"{prefix}{line_num:4d} | {lines[i]}")

            return ''.join(code_lines)

        except Exception as e:
            logger.warning(f"Could not fetch code from {file_path}: {e}")
            return f"# Could not fetch code: {e}"


def create_retriever(
    client: DatabaseClient,
    embedder: CodeEmbedder,
    context_lines: int = 5
) -> GraphRAGRetriever:
    """Factory function to create a configured retriever.

    Args:
        client: Connected database client (Neo4j or FalkorDB)
        embedder: Code embedder instance
        context_lines: Lines of context around code snippets

    Returns:
        Configured GraphRAGRetriever
    """
    return GraphRAGRetriever(
        client=client,
        embedder=embedder,
        context_lines=context_lines
    )
