"""Cypher query builder utilities for safe, composable query construction.

This module provides a fluent API for building Cypher queries with automatic
parameter binding to prevent injection attacks.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class QueryBuilder:
    """Fluent API for building Cypher queries with parameterization.

    Example:
        >>> builder = QueryBuilder()
        >>> query, params = (builder
        ...     .match("(n:File)")
        ...     .where("n.language = $lang")
        ...     .return_("n.filePath AS path, n.loc AS lines")
        ...     .order_by("lines DESC")
        ...     .limit(10)
        ...     .build({"lang": "python"}))
    """

    _match_clauses: List[str] = field(default_factory=list)
    _optional_match_clauses: List[str] = field(default_factory=list)
    _where_clauses: List[str] = field(default_factory=list)
    _with_clauses: List[str] = field(default_factory=list)
    _return_clause: Optional[str] = None
    _order_by_clause: Optional[str] = None
    _limit_clause: Optional[int] = None
    _skip_clause: Optional[int] = None

    def match(self, pattern: str) -> "QueryBuilder":
        """Add a MATCH clause.

        Args:
            pattern: Cypher pattern (e.g., "(n:File)-[:IMPORTS]->(m:Module)")

        Returns:
            Self for method chaining
        """
        self._match_clauses.append(pattern)
        return self

    def optional_match(self, pattern: str) -> "QueryBuilder":
        """Add an OPTIONAL MATCH clause.

        Args:
            pattern: Cypher pattern

        Returns:
            Self for method chaining
        """
        self._optional_match_clauses.append(pattern)
        return self

    def where(self, condition: str) -> "QueryBuilder":
        """Add a WHERE condition.

        Multiple where() calls are AND-ed together.

        Args:
            condition: WHERE condition (e.g., "n.loc > $min_loc")

        Returns:
            Self for method chaining
        """
        self._where_clauses.append(condition)
        return self

    def with_(self, expression: str) -> "QueryBuilder":
        """Add a WITH clause.

        Args:
            expression: WITH expression (e.g., "n, count(m) AS imports")

        Returns:
            Self for method chaining
        """
        self._with_clauses.append(expression)
        return self

    def return_(self, expression: str) -> "QueryBuilder":
        """Set the RETURN clause.

        Args:
            expression: RETURN expression (e.g., "n.name, n.loc")

        Returns:
            Self for method chaining
        """
        self._return_clause = expression
        return self

    def order_by(self, expression: str) -> "QueryBuilder":
        """Set ORDER BY clause.

        Args:
            expression: ORDER BY expression (e.g., "n.loc DESC, n.name ASC")

        Returns:
            Self for method chaining
        """
        self._order_by_clause = expression
        return self

    def limit(self, count: int) -> "QueryBuilder":
        """Set LIMIT clause.

        Args:
            count: Maximum number of results

        Returns:
            Self for method chaining
        """
        self._limit_clause = count
        return self

    def skip(self, count: int) -> "QueryBuilder":
        """Set SKIP clause.

        Args:
            count: Number of results to skip

        Returns:
            Self for method chaining
        """
        self._skip_clause = count
        return self

    def build(self, parameters: Optional[Dict[str, Any]] = None) -> tuple[str, Dict[str, Any]]:
        """Build the final query string and parameters.

        Args:
            parameters: Query parameters for $-prefixed placeholders

        Returns:
            Tuple of (query_string, parameters_dict)

        Example:
            >>> builder = QueryBuilder().match("(n:File)").where("n.language = $lang").return_("n.name")
            >>> query, params = builder.build({"lang": "python"})
        """
        query_parts = []

        # MATCH clauses
        for pattern in self._match_clauses:
            query_parts.append(f"MATCH {pattern}")

        # OPTIONAL MATCH clauses
        for pattern in self._optional_match_clauses:
            query_parts.append(f"OPTIONAL MATCH {pattern}")

        # WHERE clause
        if self._where_clauses:
            where_expr = " AND ".join(f"({cond})" for cond in self._where_clauses)
            query_parts.append(f"WHERE {where_expr}")

        # WITH clauses
        for with_expr in self._with_clauses:
            query_parts.append(f"WITH {with_expr}")

        # RETURN clause
        if self._return_clause:
            query_parts.append(f"RETURN {self._return_clause}")

        # ORDER BY
        if self._order_by_clause:
            query_parts.append(f"ORDER BY {self._order_by_clause}")

        # SKIP
        if self._skip_clause is not None:
            query_parts.append(f"SKIP {self._skip_clause}")

        # LIMIT
        if self._limit_clause is not None:
            query_parts.append(f"LIMIT {self._limit_clause}")

        query = "\n".join(query_parts)
        return query, parameters or {}


class DetectorQueryBuilder:
    """Specialized query builder for common detector patterns."""

    @staticmethod
    def find_nodes_with_relationship_count(
        node_label: str,
        relationship_type: str,
        direction: str = "OUTGOING",
        min_count: Optional[int] = None,
        max_count: Optional[int] = None,
        limit: int = 100,
    ) -> tuple[str, Dict[str, Any]]:
        """Build query to find nodes by relationship count.

        Args:
            node_label: Node label to query
            relationship_type: Relationship type to count
            direction: "OUTGOING", "INCOMING", or "BOTH"
            min_count: Minimum relationship count (optional)
            max_count: Maximum relationship count (optional)
            limit: Result limit

        Returns:
            Tuple of (query, parameters)

        Example:
            >>> query, params = DetectorQueryBuilder.find_nodes_with_relationship_count(
            ...     "File", "IMPORTS", "OUTGOING", min_count=10
            ... )
        """
        if direction == "OUTGOING":
            rel_pattern = f"-[:{relationship_type}]->"
        elif direction == "INCOMING":
            rel_pattern = f"<-[:{relationship_type}]-"
        else:  # BOTH
            rel_pattern = f"-[:{relationship_type}]-"

        builder = QueryBuilder()
        builder.match(f"(n:{node_label})")
        builder.optional_match(f"(n){rel_pattern}(connected)")
        builder.with_("n, count(connected) AS rel_count")

        # Add count filters
        where_conditions = []
        params = {}
        if min_count is not None:
            where_conditions.append("rel_count >= $min_count")
            params["min_count"] = min_count
        if max_count is not None:
            where_conditions.append("rel_count <= $max_count")
            params["max_count"] = max_count

        if where_conditions:
            builder.where(" AND ".join(where_conditions))

        builder.return_("elementId(n) AS node_id, n.name AS name, n.filePath AS file_path, rel_count")
        builder.order_by("rel_count DESC")
        builder.limit(limit)

        return builder.build(params)

    @staticmethod
    def find_nodes_by_property(
        node_label: str,
        property_name: str,
        operator: str,
        value: Any,
        limit: int = 100,
    ) -> tuple[str, Dict[str, Any]]:
        """Build query to find nodes by property value.

        Args:
            node_label: Node label to query
            property_name: Property name
            operator: Comparison operator (=, >, <, >=, <=, <>)
            value: Value to compare against
            limit: Result limit

        Returns:
            Tuple of (query, parameters)

        Example:
            >>> query, params = DetectorQueryBuilder.find_nodes_by_property(
            ...     "Function", "complexity", ">=", 20
            ... )
        """
        builder = QueryBuilder()
        builder.match(f"(n:{node_label})")
        builder.where(f"n.{property_name} {operator} $value")
        builder.return_(f"elementId(n) AS node_id, n.name AS name, n.{property_name} AS property_value")
        builder.order_by(f"n.{property_name} DESC")
        builder.limit(limit)

        return builder.build({"value": value})

    @staticmethod
    def find_nodes_without_relationship(
        node_label: str,
        relationship_type: str,
        direction: str = "INCOMING",
        limit: int = 100,
    ) -> tuple[str, Dict[str, Any]]:
        """Build query to find nodes without a specific relationship (e.g., dead code).

        Args:
            node_label: Node label to query
            relationship_type: Relationship type to check
            direction: "INCOMING", "OUTGOING", or "BOTH"
            limit: Result limit

        Returns:
            Tuple of (query, parameters)

        Example:
            >>> query, params = DetectorQueryBuilder.find_nodes_without_relationship(
            ...     "Function", "CALLS", "INCOMING"
            ... )
        """
        if direction == "OUTGOING":
            rel_pattern = f"-[:{relationship_type}]->"
        elif direction == "INCOMING":
            rel_pattern = f"<-[:{relationship_type}]-"
        else:  # BOTH
            rel_pattern = f"-[:{relationship_type}]-"

        builder = QueryBuilder()
        builder.match(f"(n:{node_label})")
        builder.where(f"NOT (n){rel_pattern}()")
        builder.return_("elementId(n) AS node_id, n.name AS name, n.filePath AS file_path")
        builder.limit(limit)

        return builder.build()

    @staticmethod
    def aggregate_by_property(
        node_label: str,
        group_by_property: str,
        aggregate_property: str,
        aggregate_function: str = "count",
        limit: int = 100,
    ) -> tuple[str, Dict[str, Any]]:
        """Build query to aggregate nodes by property.

        Args:
            node_label: Node label to query
            group_by_property: Property to group by
            aggregate_property: Property to aggregate
            aggregate_function: Aggregation function (count, sum, avg, min, max)
            limit: Result limit

        Returns:
            Tuple of (query, parameters)

        Example:
            >>> query, params = DetectorQueryBuilder.aggregate_by_property(
            ...     "File", "language", "loc", "sum"
            ... )
        """
        builder = QueryBuilder()
        builder.match(f"(n:{node_label})")
        builder.with_(f"n.{group_by_property} AS group_key, {aggregate_function}(n.{aggregate_property}) AS agg_value")
        builder.return_("group_key, agg_value")
        builder.order_by("agg_value DESC")
        builder.limit(limit)

        return builder.build()
