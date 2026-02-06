"""Kuzu Cypher Query Adapter.

Transforms FalkorDB/Neo4j Cypher queries to Kuzu-compatible Cypher.

Key differences handled:
1. Function names: toFloat() → CAST AS DOUBLE, elementId() → id()
2. Unsupported syntax: shortestPath, ORDER BY id(), pattern comprehensions
3. Slice syntax: [0..5] → not supported (flag for rewrite)
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Note: Property names and relationship types are handled by the KuzuClient schema
# which uses camelCase names to match existing queries.


class KuzuQueryAdapter:
    """Adapts FalkorDB Cypher queries to Kuzu Cypher."""

    def __init__(self, strict: bool = False):
        """Initialize adapter.
        
        Args:
            strict: If True, raise on unsupported syntax. If False, return None.
        """
        self.strict = strict

    def adapt(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        """Adapt a Cypher query for Kuzu compatibility.
        
        Args:
            query: FalkorDB/Neo4j Cypher query
            
        Returns:
            Tuple of (adapted_query, error_reason)
            - If successful: (query, None)
            - If unsupported: (None, reason)
        """
        # Remove comments (Kuzu doesn't support all comment styles)
        query = self._remove_comments(query)

        # Check for unsupported features first
        unsupported = self._check_unsupported(query)
        if unsupported:
            logger.debug(f"Query uses unsupported Kuzu feature: {unsupported}")
            return None, unsupported

        # Apply transformations
        query = self._fix_functions(query)
        query = self._fix_syntax(query)

        return query, None

    def adapt_or_raise(self, query: str) -> str:
        """Adapt query or raise error if unsupported."""
        adapted, reason = self.adapt(query)
        if adapted is None:
            raise RuntimeError(f"Kuzu compatibility: {reason}")
        return adapted

    def _remove_comments(self, query: str) -> str:
        """Remove // and -- comments."""
        lines = []
        for line in query.split('\n'):
            # Remove line comments
            if '//' in line:
                line = line.split('//')[0]
            if '--' in line and not line.strip().startswith('--'):
                # Be careful not to remove -- in strings
                line = re.sub(r'--(?![^"]*"[^"]*$).*$', '', line)
            lines.append(line)
        return '\n'.join(lines)

    def _check_unsupported(self, query: str) -> Optional[str]:
        """Check for unsupported Kuzu features.
        
        Returns description of unsupported feature, or None if OK.
        """
        # shortestPath not supported
        if re.search(r'shortestPath', query, re.IGNORECASE):
            return "shortestPath()"

        # ORDER BY id(n) / INTERNAL_ID not supported
        if re.search(r'ORDER\s+BY\s+\w*id\s*\(', query, re.IGNORECASE):
            return "ORDER BY id()"
        if 'ORDER BY neo_id' in query or 'ORDER BY INTERNAL_ID' in query:
            return "ORDER BY internal id"

        # Pattern comprehensions with WHERE not fully supported
        if re.search(r'size\s*\(\s*\[\s*\([^]]+WHERE', query):
            return "size([pattern WHERE ...])"

        # Slice syntax is now auto-converted in _fix_syntax()
        # [0..5] → [0:5], [..-1] → [:-1]

        # Empty map literal {} is now auto-converted to map([],[]) in _fix_syntax()

        return None

    def _fix_functions(self, query: str) -> str:
        """Fix function differences between FalkorDB/Neo4j and Kuzu."""
        # toFloat(x) → CAST(x AS DOUBLE)
        query = re.sub(
            r'\btoFloat\s*\(\s*([^)]+)\s*\)',
            r'CAST(\1 AS DOUBLE)',
            query,
            flags=re.IGNORECASE
        )

        # elementId(x) → id(x)
        # Note: Kuzu id() returns internal ID, different format but works for comparisons
        query = re.sub(
            r'\belementId\s*\(',
            'id(',
            query,
            flags=re.IGNORECASE
        )

        # toInteger(x) → CAST(x AS INT64)
        query = re.sub(
            r'\btoInteger\s*\(\s*([^)]+)\s*\)',
            r'CAST(\1 AS INT64)',
            query,
            flags=re.IGNORECASE
        )

        # toString(x) → CAST(x AS STRING)
        query = re.sub(
            r'\btoString\s*\(\s*([^)]+)\s*\)',
            r'CAST(\1 AS STRING)',
            query,
            flags=re.IGNORECASE
        )

        # split(str, delim) → STRING_SPLIT(str, delim)
        query = re.sub(
            r'\bsplit\s*\(',
            'STRING_SPLIT(',
            query,
            flags=re.IGNORECASE
        )

        # reduce() for sum: reduce(sum = 0.0, d IN list | sum + d) → list_sum(list)
        # Matches: reduce(acc = init, var IN list | acc + var)
        query = re.sub(
            r'\breduce\s*\(\s*\w+\s*=\s*[\d.]+\s*,\s*\w+\s+IN\s+(\w+)\s*\|\s*\w+\s*\+\s*\w+\s*\)',
            r'list_sum(\1)',
            query,
            flags=re.IGNORECASE
        )

        # reduce() for max: reduce(max = 0, d IN list | CASE WHEN d > max THEN d ELSE max END)
        # → list_sort(list, "DESC")[1]
        query = re.sub(
            r'\breduce\s*\(\s*max\s*=\s*[\d.]+\s*,\s*\w+\s+IN\s+(\w+)\s*\|\s*CASE\s+WHEN.*?END\s*\)',
            r'list_sort(\1, "DESC")[1]',
            query,
            flags=re.IGNORECASE | re.DOTALL
        )

        # reduce() for string concat: reduce(s='', p IN list | s + '/' + p)
        # → list_to_string('/', list) (Kuzu uses reversed arg order)
        query = re.sub(
            r"\breduce\s*\(\s*\w+\s*=\s*['\"]'*['\"]?\s*,\s*\w+\s+IN\s+(\w+)\s*\|\s*\w+\s*\+\s*['\"]([^'\"]+)['\"]\s*\+\s*\w+\s*\)",
            r"list_to_string('\2', \1)",
            query,
            flags=re.IGNORECASE
        )

        return query

    def _fix_syntax(self, query: str) -> str:
        """Fix general syntax differences."""
        # Convert Cypher slice [0..5] to Kuzu slice [0:5]
        # Match patterns like var[0..5] or expr[start..end]
        query = re.sub(
            r'\[(\d+)\.\.(\d+)\]',
            r'[\1:\2]',
            query
        )

        # Convert [..-1] to [:-1] (slice to end minus 1)
        query = re.sub(
            r'\[\.\.(-?\d+)\]',
            r'[:\1]',
            query
        )

        # Convert empty map literal {} to map([],[])
        # Match COALESCE(x, {}) or standalone {}
        query = re.sub(
            r'\{\s*\}',
            'map([],[])',
            query
        )

        return query


def adapt_query(query: str) -> Optional[str]:
    """Convenience function to adapt a query.
    
    Returns adapted query or None if unsupported.
    """
    adapter = KuzuQueryAdapter(strict=False)
    return adapter.adapt(query)


def can_adapt_query(query: str) -> bool:
    """Check if a query can be adapted for Kuzu."""
    return adapt_query(query) is not None
