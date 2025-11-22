"""Custom rule engine with time-based priority refresh (REPO-125).

This module implements a rule engine where rules are stored as graph nodes
and automatically prioritized based on usage patterns.

Key features:
- Rules as first-class Neo4j nodes
- Time-based priority refresh (lastUsed, accessCount)
- Dynamic priority calculation (user + recency + frequency)
- Integration with RAG context building
- YAML/JSON rule definitions
"""

from repotoire.rules.engine import RuleEngine
from repotoire.rules.validator import RuleValidator
from repotoire.rules.daemon import RuleRefreshDaemon, get_daemon

__all__ = [
    "RuleEngine",
    "RuleValidator",
    "RuleRefreshDaemon",
    "get_daemon",
]
