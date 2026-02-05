"""Kuzu Cypher Query Adapter.

Transforms FalkorDB/Neo4j Cypher queries to Kuzu-compatible Cypher.

Key differences handled:
1. Function names: toFloat() → CAST AS DOUBLE, elementId() → id()
2. Unsupported syntax: shortestPath, ORDER BY id(), pattern comprehensions
3. Slice syntax: [0..5] → not supported (flag for rewrite)
"""

import re
import logging
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
        
        # Slice syntax [..-1] not supported
        if re.search(r'\[\s*\.\.\s*-?\d+\s*\]', query):
            return "slice syntax [..-1]"
        if re.search(r'\[\s*\d+\s*\.\.\s*\d*\s*\]', query):
            return "slice syntax [0..5]"
        
        # Empty map literal {} in COALESCE
        if re.search(r'COALESCE\s*\([^,]+,\s*\{\s*\}\s*\)', query, re.IGNORECASE):
            return "COALESCE with empty map {}"
        
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
        
        return query

    def _fix_syntax(self, query: str) -> str:
        """Fix general syntax differences."""
        # 'X' IN labels(n) → Kuzu doesn't support labels() the same way
        # For now, leave it - specific queries may need manual fixes
        
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
