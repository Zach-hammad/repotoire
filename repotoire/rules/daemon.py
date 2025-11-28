"""Background daemon for rule priority management (REPO-125 Phase 3)."""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Union
from repotoire.graph.base import DatabaseClient
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class RuleRefreshDaemon:
    """Background daemon that automatically manages rule priorities.

    This daemon runs as an asyncio task and performs:
    1. Auto-decay: Reduces priority of stale rules (>7 days unused)
    2. Cleanup: Optionally archives very old rules (>90 days unused)
    3. Statistics: Logs usage patterns for monitoring

    The daemon complements the TIME REFRESHER by handling the negative
    side - decaying stale rules so they don't clutter the hot rules list.

    Example:
        >>> daemon = RuleRefreshDaemon(client, refresh_interval=3600)
        >>> await daemon.start()  # Runs in background
        >>> # ... later ...
        >>> await daemon.stop()
    """

    def __init__(
        self,
        client: Union[Neo4jClient, DatabaseClient],
        refresh_interval: int = 3600,  # 1 hour
        decay_threshold_days: int = 7,
        decay_factor: float = 0.9,  # Reduce priority by 10%
        archive_threshold_days: int = 90,
        auto_archive: bool = False,
    ):
        """Initialize daemon.

        Args:
            client: Database client instance (Neo4j or FalkorDB)
            refresh_interval: Seconds between refresh cycles
            decay_threshold_days: Days before starting decay
            decay_factor: Multiplier for priority decay (0.9 = 10% reduction)
            archive_threshold_days: Days before auto-archiving
            auto_archive: Whether to auto-archive very old rules
        """
        self.client = client
        self.refresh_interval = refresh_interval
        self.decay_threshold_days = decay_threshold_days
        self.decay_factor = decay_factor
        self.archive_threshold_days = archive_threshold_days
        self.auto_archive = auto_archive
        self._is_falkordb = getattr(client, 'is_falkordb', False)

        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the background daemon."""
        if self._running:
            logger.warning("Daemon already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Rule refresh daemon started "
            f"(interval={self.refresh_interval}s, decay_threshold={self.decay_threshold_days}d)"
        )

    async def stop(self) -> None:
        """Stop the background daemon."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Rule refresh daemon stopped")

    async def _run_loop(self) -> None:
        """Main daemon loop."""
        while self._running:
            try:
                await self._refresh_cycle()
            except Exception as e:
                logger.error(f"Error in refresh cycle: {e}", exc_info=True)

            # Wait for next cycle
            await asyncio.sleep(self.refresh_interval)

    async def _refresh_cycle(self) -> None:
        """Perform one refresh cycle."""
        logger.debug("Starting refresh cycle")

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()

        # Decay stale rules
        decayed = await loop.run_in_executor(None, self._decay_stale_rules)
        if decayed > 0:
            logger.info(f"Decayed {decayed} stale rules")

        # Archive very old rules (if enabled)
        if self.auto_archive:
            archived = await loop.run_in_executor(None, self._archive_old_rules)
            if archived > 0:
                logger.info(f"Archived {archived} very old rules")

        # Log statistics
        stats = await loop.run_in_executor(None, self._get_daemon_stats)
        logger.debug(
            f"Daemon stats: {stats.get('active_rules', 0)} active, "
            f"{stats.get('stale_rules', 0)} stale, "
            f"avg_age={stats.get('avg_days_since_use', 0):.1f}d"
        )

    def _decay_stale_rules(self) -> int:
        """Decay priority of stale rules.

        Returns:
            Number of rules decayed
        """
        decay_date = datetime.now(timezone.utc) - timedelta(days=self.decay_threshold_days)

        # FalkorDB doesn't support datetime() - use UNIX timestamps
        if self._is_falkordb:
            decay_timestamp = int(decay_date.timestamp())
            current_timestamp = int(time.time())
            query = """
            MATCH (r:Rule)
            WHERE r.enabled = true
              AND r.lastUsed < $decay_timestamp
              AND r.userPriority > 10
            SET r.userPriority = toInteger(r.userPriority * $decay_factor),
                r.updatedAt = $current_timestamp
            RETURN count(r) as decayed
            """
            params = {
                "decay_timestamp": decay_timestamp,
                "current_timestamp": current_timestamp,
                "decay_factor": self.decay_factor,
            }
        else:
            query = """
            MATCH (r:Rule)
            WHERE r.enabled = true
              AND r.lastUsed < datetime($decay_date)
              AND r.userPriority > 10
            SET r.userPriority = toInteger(r.userPriority * $decay_factor),
                r.updatedAt = datetime()
            RETURN count(r) as decayed
            """
            params = {
                "decay_date": decay_date.isoformat(),
                "decay_factor": self.decay_factor,
            }

        results = self.client.execute_query(query, params)
        return results[0]["decayed"] if results else 0

    def _archive_old_rules(self) -> int:
        """Archive very old unused rules.

        Returns:
            Number of rules archived
        """
        archive_date = datetime.now(timezone.utc) - timedelta(days=self.archive_threshold_days)

        # FalkorDB doesn't support datetime() - use UNIX timestamps
        if self._is_falkordb:
            archive_timestamp = int(archive_date.timestamp())
            current_timestamp = int(time.time())
            query = """
            MATCH (r:Rule)
            WHERE r.enabled = true
              AND (r.lastUsed < $archive_timestamp OR r.lastUsed IS NULL)
              AND r.accessCount = 0
            SET r.enabled = false,
                r.updatedAt = $current_timestamp
            RETURN count(r) as archived
            """
            params = {
                "archive_timestamp": archive_timestamp,
                "current_timestamp": current_timestamp,
            }
        else:
            query = """
            MATCH (r:Rule)
            WHERE r.enabled = true
              AND (r.lastUsed < datetime($archive_date) OR r.lastUsed IS NULL)
              AND r.accessCount = 0
            SET r.enabled = false,
                r.updatedAt = datetime()
            RETURN count(r) as archived
            """
            params = {"archive_date": archive_date.isoformat()}

        results = self.client.execute_query(query, params)
        return results[0]["archived"] if results else 0

    def _get_daemon_stats(self) -> dict:
        """Get statistics for monitoring.

        Returns:
            Dictionary with daemon statistics
        """
        # Simpler query without duration.between() to avoid type issues
        now = datetime.now(timezone.utc)
        decay_cutoff = now - timedelta(days=self.decay_threshold_days)

        # FalkorDB doesn't support datetime() - use UNIX timestamps
        if self._is_falkordb:
            decay_timestamp = int(decay_cutoff.timestamp())
            query = """
            MATCH (r:Rule)
            RETURN
                count(CASE WHEN r.enabled THEN 1 END) as active_rules,
                count(CASE WHEN NOT r.enabled THEN 1 END) as archived_rules,
                count(CASE WHEN r.enabled AND (r.lastUsed IS NULL OR r.lastUsed < $decay_timestamp) THEN 1 END) as stale_rules
            """
            params = {"decay_timestamp": decay_timestamp}
        else:
            query = """
            MATCH (r:Rule)
            RETURN
                count(CASE WHEN r.enabled THEN 1 END) as active_rules,
                count(CASE WHEN NOT r.enabled THEN 1 END) as archived_rules,
                count(CASE WHEN r.enabled AND (r.lastUsed IS NULL OR r.lastUsed < datetime($decay_cutoff)) THEN 1 END) as stale_rules
            """
            params = {"decay_cutoff": decay_cutoff.isoformat()}

        results = self.client.execute_query(query, params)
        if not results:
            return {}

        stats = dict(results[0])
        # Add avg_days_since_use as 0 for now (would need separate calculation)
        stats['avg_days_since_use'] = 0.0
        # Convert Decimal/float to regular types
        return {k: float(v) if v is not None else 0 for k, v in stats.items()}

    def force_refresh(self) -> dict:
        """Force an immediate refresh cycle (synchronous).

        Returns:
            Dictionary with refresh results
        """
        decayed = self._decay_stale_rules()
        archived = 0
        if self.auto_archive:
            archived = self._archive_old_rules()

        stats = self._get_daemon_stats()

        return {
            "decayed": decayed,
            "archived": archived,
            "stats": stats,
        }


# Singleton daemon instance for CLI usage
_daemon_instance: Optional[RuleRefreshDaemon] = None


def get_daemon(client: Union[Neo4jClient, DatabaseClient], **kwargs) -> RuleRefreshDaemon:
    """Get or create singleton daemon instance.

    Args:
        client: Database client (Neo4j or FalkorDB)
        **kwargs: Daemon configuration options

    Returns:
        RuleRefreshDaemon instance
    """
    global _daemon_instance

    if _daemon_instance is None:
        _daemon_instance = RuleRefreshDaemon(client, **kwargs)

    return _daemon_instance
