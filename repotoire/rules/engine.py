"""Rule execution engine with time-based priority refresh (REPO-125)."""

import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Union
from repotoire.graph.base import DatabaseClient
from repotoire.graph.client import Neo4jClient
from repotoire.models import Rule, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class RuleEngine:
    """Executes custom code quality rules stored in the graph.

    This engine manages the complete lifecycle of custom rules:
    - CRUD operations (create, read, update, delete)
    - Rule execution with automatic timestamp refresh
    - Priority calculation based on usage patterns
    - Hot rule queries for RAG integration

    Key Innovation: TIME REFRESHER
    Every rule execution automatically updates `lastUsed` and `accessCount`,
    making frequently-used rules "hot" so they appear first in RAG context.

    Example:
        >>> engine = RuleEngine(client)
        >>> rule = engine.get_rule("no-god-classes")
        >>> findings = engine.execute_rule(rule)  # Auto-refreshes timestamps!
        >>> hot_rules = engine.get_hot_rules(top_k=10)  # For RAG context
    """

    def __init__(self, client: Union[Neo4jClient, DatabaseClient]):
        """Initialize rule engine.

        Args:
            client: Database client instance (Neo4j or FalkorDB)
        """
        self.client = client
        self._is_falkordb = getattr(client, 'is_falkordb', False)

    # ========================================================================
    # CRUD Operations
    # ========================================================================

    def create_rule(self, rule: Rule) -> Rule:
        """Create a new rule in the graph.

        Args:
            rule: Rule to create

        Returns:
            Created rule with Neo4j properties

        Raises:
            ValueError: If rule with same ID already exists
        """
        query = """
        CREATE (r:Rule {
            id: $id,
            name: $name,
            description: $description,
            pattern: $pattern,
            severity: $severity,
            enabled: $enabled,
            userPriority: $userPriority,
            lastUsed: $lastUsed,
            accessCount: $accessCount,
            autoFix: $autoFix,
            tags: $tags,
            createdAt: $createdAt,
            updatedAt: $updatedAt
        })
        RETURN r
        """

        params = rule.to_dict()

        try:
            results = self.client.execute_query(query, params)
            logger.info(f"Created rule: {rule.id}")
            return rule
        except Exception as e:
            if "already exists" in str(e).lower() or "constraint" in str(e).lower():
                raise ValueError(f"Rule with ID '{rule.id}' already exists")
            raise

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        """Retrieve a rule by ID.

        Args:
            rule_id: Unique rule identifier

        Returns:
            Rule if found, None otherwise
        """
        query = """
        MATCH (r:Rule {id: $rule_id})
        RETURN r
        """

        results = self.client.execute_query(query, {"rule_id": rule_id})
        if not results:
            return None

        node = results[0]["r"]
        return Rule.from_dict(node)

    def update_rule(self, rule_id: str, **kwargs) -> Optional[Rule]:
        """Update rule properties.

        Args:
            rule_id: Rule to update
            **kwargs: Properties to update (name, description, pattern, etc.)

        Returns:
            Updated rule if found, None otherwise
        """
        # Build SET clause dynamically
        set_clauses = []
        params = {"rule_id": rule_id, "updatedAt": datetime.now(timezone.utc).isoformat()}

        for key, value in kwargs.items():
            if key in {"id", "createdAt"}:  # Don't allow updating these
                continue
            set_clauses.append(f"r.{key} = ${key}")
            params[key] = value

        if not set_clauses:
            return self.get_rule(rule_id)

        # Always update updatedAt
        set_clauses.append("r.updatedAt = $updatedAt")

        query = f"""
        MATCH (r:Rule {{id: $rule_id}})
        SET {', '.join(set_clauses)}
        RETURN r
        """

        results = self.client.execute_query(query, params)
        if not results:
            return None

        node = results[0]["r"]
        logger.info(f"Updated rule: {rule_id}")
        return Rule.from_dict(node)

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule from the graph.

        Args:
            rule_id: Rule to delete

        Returns:
            True if deleted, False if not found
        """
        query = """
        MATCH (r:Rule {id: $rule_id})
        DELETE r
        RETURN count(r) as deleted
        """

        results = self.client.execute_query(query, {"rule_id": rule_id})
        deleted = results[0]["deleted"] > 0

        if deleted:
            logger.info(f"Deleted rule: {rule_id}")

        return deleted

    def list_rules(
        self,
        enabled_only: bool = False,
        tags: Optional[List[str]] = None,
        limit: Optional[int] = None
    ) -> List[Rule]:
        """List all rules.

        Args:
            enabled_only: Only return enabled rules
            tags: Filter by tags (any match)
            limit: Maximum number of rules to return

        Returns:
            List of rules sorted by priority (highest first)
        """
        where_clauses = []
        params: Dict[str, Any] = {}

        if enabled_only:
            where_clauses.append("r.enabled = true")

        if tags:
            where_clauses.append("any(tag IN $tags WHERE tag IN r.tags)")
            params["tags"] = tags

        where_clause = ""
        if where_clauses:
            where_clause = "WHERE " + " AND ".join(where_clauses)

        limit_clause = ""
        if limit:
            limit_clause = f"LIMIT {limit}"

        query = f"""
        MATCH (r:Rule)
        {where_clause}
        RETURN r
        ORDER BY r.userPriority DESC, r.lastUsed DESC
        {limit_clause}
        """

        results = self.client.execute_query(query, params)
        return [Rule.from_dict(record["r"]) for record in results]

    # ========================================================================
    # Rule Execution with Time Refresh
    # ========================================================================

    def execute_rule(self, rule: Rule, scope: Optional[List[str]] = None) -> List[Finding]:
        """Execute a rule and return findings.

        KEY FEATURE: This automatically updates `lastUsed` and `accessCount`
        to keep frequently-used rules "hot" for RAG context.

        Args:
            rule: Rule to execute
            scope: Optional file paths to limit scope (for incremental analysis)

        Returns:
            List of findings (code smell violations)
        """
        # 1. Execute the Cypher pattern
        try:
            # Add scope filter if provided
            pattern = rule.pattern
            params = {}

            if scope:
                # Inject file path filter into query
                # This assumes the query has a File node that can be filtered
                if "MATCH" in pattern and "File" in pattern:
                    # Simple injection: add WHERE clause before RETURN
                    parts = pattern.split("RETURN")
                    if len(parts) == 2:
                        pattern = f"{parts[0]} WHERE f.filePath IN $scope RETURN {parts[1]}"
                        params["scope"] = scope

            results = self.client.execute_query(pattern, params)

        except Exception as e:
            logger.error(f"Rule execution failed for {rule.id}: {e}")
            return []

        # 2. Update timestamps (TIME REFRESHER!)
        self._refresh_rule_metadata(rule.id)

        # 3. Convert results to Finding objects
        findings = []
        for record in results:
            # Extract relevant data from query results
            # Common fields: file_path, class_name, function_name, metric_value
            file_path = record.get("file_path", record.get("filePath", "unknown"))
            affected_entity = record.get("class_name") or record.get("function_name") or ""
            metric_value = record.get("method_count") or record.get("loc") or record.get("complexity") or ""

            # Generate description
            description = f"{rule.description}"
            if metric_value:
                description += f" (value: {metric_value})"

            # Generate unique ID for finding
            import uuid
            finding_id = f"{rule.id}-{uuid.uuid4().hex[:8]}"

            finding = Finding(
                id=finding_id,
                detector=f"CustomRule:{rule.id}",
                severity=rule.severity,
                title=rule.name,
                description=description,
                affected_nodes=[affected_entity] if affected_entity else [],
                affected_files=[file_path] if file_path != "unknown" else [],
                suggested_fix=rule.autoFix,
                graph_context={
                    "rule_id": rule.id,
                    "rule_tags": rule.tags,
                    "query_result": dict(record),
                }
            )
            findings.append(finding)

        logger.info(f"Rule {rule.id} found {len(findings)} violations")
        return findings

    def _refresh_rule_metadata(self, rule_id: str) -> None:
        """Update lastUsed and accessCount (TIME REFRESHER).

        This is the core of the time-based priority system. Every rule
        execution increments the access count and updates the timestamp,
        making frequently-used rules automatically bubble to the top.

        Args:
            rule_id: Rule to refresh
        """
        # FalkorDB doesn't support datetime() - use UNIX timestamps
        if self._is_falkordb:
            current_timestamp = int(time.time())
            query = """
            MATCH (r:Rule {id: $rule_id})
            SET r.lastUsed = $timestamp,
                r.accessCount = r.accessCount + 1,
                r.updatedAt = $timestamp
            RETURN r.accessCount as new_count
            """
            params = {"rule_id": rule_id, "timestamp": current_timestamp}
        else:
            query = """
            MATCH (r:Rule {id: $rule_id})
            SET r.lastUsed = datetime(),
                r.accessCount = r.accessCount + 1,
                r.updatedAt = datetime()
            RETURN r.accessCount as new_count
            """
            params = {"rule_id": rule_id}

        results = self.client.execute_query(query, params)
        if results:
            new_count = results[0]["new_count"]
            logger.debug(f"Rule {rule_id} refreshed (accessCount: {new_count})")

    # ========================================================================
    # Hot Rules (for RAG integration)
    # ========================================================================

    def get_hot_rules(self, top_k: int = 10) -> List[Rule]:
        """Get top-k hot rules sorted by priority.

        This is used by the RAG system to include the most relevant rules
        in the context window. Rules are sorted by:
        1. Enabled status (enabled first)
        2. Recent usage (lastUsed DESC)
        3. User priority (userPriority DESC)

        Args:
            top_k: Number of hot rules to return

        Returns:
            List of hot rules
        """
        query = """
        MATCH (r:Rule)
        WHERE r.enabled = true
        RETURN r
        ORDER BY r.lastUsed DESC, r.userPriority DESC
        LIMIT $top_k
        """

        results = self.client.execute_query(query, {"top_k": top_k})
        return [Rule.from_dict(record["r"]) for record in results]

    def get_rules_by_priority(self, limit: int = 50) -> List[tuple[Rule, float]]:
        """Get rules with calculated priority scores.

        Returns rules sorted by dynamically calculated priority
        (user priority + recency + frequency).

        Args:
            limit: Maximum rules to return

        Returns:
            List of (rule, priority_score) tuples
        """
        rules = self.list_rules(enabled_only=True, limit=limit)

        # Calculate priority for each rule
        rules_with_priority = [
            (rule, rule.calculate_priority())
            for rule in rules
        ]

        # Sort by priority (highest first)
        rules_with_priority.sort(key=lambda x: x[1], reverse=True)

        return rules_with_priority

    # ========================================================================
    # Bulk Operations
    # ========================================================================

    def execute_all_rules(
        self,
        enabled_only: bool = True,
        tags: Optional[List[str]] = None
    ) -> List[Finding]:
        """Execute all rules and aggregate findings.

        Args:
            enabled_only: Only execute enabled rules
            tags: Filter rules by tags

        Returns:
            All findings from all rules
        """
        rules = self.list_rules(enabled_only=enabled_only, tags=tags)

        all_findings = []
        for rule in rules:
            findings = self.execute_rule(rule)
            all_findings.extend(findings)

        logger.info(f"Executed {len(rules)} rules, found {len(all_findings)} total violations")
        return all_findings

    def get_rule_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics about rules.

        Returns:
            Dictionary with rule statistics
        """
        query = """
        MATCH (r:Rule)
        RETURN
            count(r) as total_rules,
            sum(CASE WHEN r.enabled THEN 1 ELSE 0 END) as enabled_rules,
            avg(r.accessCount) as avg_access_count,
            max(r.accessCount) as max_access_count,
            sum(r.accessCount) as total_executions
        """

        results = self.client.execute_query(query)
        if not results:
            return {}

        return dict(results[0])
