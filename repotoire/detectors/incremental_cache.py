"""Incremental file fingerprinting and cache system for fast re-analysis.

This module provides caching of detector findings keyed by file content hash,
enabling incremental analysis that only re-runs detectors on changed files.

Uses xxhash for speed when available, falls back to md5.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Try xxhash for speed (~10x faster than md5)
try:
    import xxhash
    _HAS_XXHASH = True
except ImportError:
    _HAS_XXHASH = False

if TYPE_CHECKING:
    from repotoire.models import Finding

logger = logging.getLogger(__name__)

# Cache format version - bump when schema changes
CACHE_VERSION = 2

# Buffer size for hashing large files (64KB chunks)
HASH_BUFFER_SIZE = 65536


class IncrementalCache:
    """File fingerprinting and findings cache for incremental analysis.

    Stores file hashes and associated findings to avoid re-running detectors
    on unchanged files. Cache is persisted to disk as JSON.

    Attributes:
        cache_dir: Directory containing the cache file
        cache_file: Path to the JSON cache file
        _cache: In-memory cache data

    Example:
        >>> cache = IncrementalCache(Path("/repo/.repotoire/cache"))
        >>> changed = cache.get_changed_files(all_files)
        >>> for f in changed:
        ...     findings = run_detector(f)
        ...     cache.cache_findings(f, findings)
        >>> cache.save_cache()
    """

    __slots__ = ("cache_dir", "cache_file", "_cache", "_dirty")

    def __init__(self, cache_dir: Path) -> None:
        """Initialize the cache.

        Args:
            cache_dir: Directory to store cache file (created if needed)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "findings_cache.json"
        self._cache: dict[str, Any] = {
            "version": CACHE_VERSION,
            "files": {},
            "graph": {"hash": None, "detectors": {}},
        }
        self._dirty = False

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load existing cache
        self.load_cache()

    def get_file_hash(self, path: Path) -> str:
        """Compute fast content hash of a file.

        Uses xxhash if available (10x faster), otherwise md5.
        Reads in chunks to handle large files efficiently.

        Args:
            path: Path to the file to hash

        Returns:
            Hex digest of file contents

        Raises:
            OSError: If file cannot be read
        """
        if _HAS_XXHASH:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5(usedforsecurity=False)

        try:
            with open(path, "rb") as f:
                while chunk := f.read(HASH_BUFFER_SIZE):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            # Return unique hash for unreadable files
            return f"error:{path}"

    def load_cache(self) -> dict[str, Any]:
        """Load cache from disk.

        Handles missing files and corrupted data gracefully.

        Returns:
            The loaded cache dictionary
        """
        if not self.cache_file.exists():
            logger.debug("No cache file found at %s", self.cache_file)
            return self._cache

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Version check - rebuild if schema changed
            if data.get("version") != CACHE_VERSION:
                logger.info(
                    "Cache version mismatch (got %s, expected %s), rebuilding",
                    data.get("version"),
                    CACHE_VERSION,
                )
                self._invalidate_cache()
                return self._cache

            self._cache = data
            logger.debug(
                "Loaded cache with %d files", len(self._cache.get("files", {}))
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Cache corrupted (%s), rebuilding", e)
            self._invalidate_cache()

        return self._cache

    def save_cache(self) -> None:
        """Persist cache to disk.

        Only writes if cache has been modified (dirty flag).
        Uses atomic write pattern to prevent corruption.
        """
        if not self._dirty:
            return

        # Write to temp file first, then rename (atomic on POSIX)
        tmp_file = self.cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, separators=(",", ":"))

            # Atomic rename
            tmp_file.replace(self.cache_file)
            self._dirty = False
            logger.debug(
                "Saved cache with %d files", len(self._cache.get("files", {}))
            )

        except OSError as e:
            logger.error("Failed to save cache: %s", e)
            # Clean up temp file if it exists
            if tmp_file.exists():
                tmp_file.unlink()

    def is_file_changed(self, path: Path) -> bool:
        """Check if file has changed since last cache.

        Args:
            path: Path to check

        Returns:
            True if file is new or content hash differs from cached
        """
        path_key = self._path_key(path)
        cached = self._cache["files"].get(path_key)

        if cached is None:
            return True

        current_hash = self.get_file_hash(path)
        return cached.get("hash") != current_hash

    def get_cached_findings(self, path: Path) -> list["Finding"]:
        """Retrieve cached findings for a file.

        Args:
            path: Path to get findings for

        Returns:
            List of Finding objects, empty if not cached or file changed
        """
        from repotoire.models import Finding, Severity

        path_key = self._path_key(path)
        cached = self._cache["files"].get(path_key)

        if cached is None:
            return []

        # Check if file changed - if so, cached findings are stale
        current_hash = self.get_file_hash(path)
        if cached.get("hash") != current_hash:
            return []

        findings: list[Finding] = []
        for finding_data in cached.get("findings", []):
            try:
                finding = self._deserialize_finding(finding_data)
                findings.append(finding)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug("Failed to deserialize finding: %s", e)
                continue

        return findings

    def cache_findings(self, path: Path, findings: list["Finding"]) -> None:
        """Store findings for a file in the cache.

        Args:
            path: Path the findings are for
            findings: List of Finding objects to cache
        """
        path_key = self._path_key(path)
        file_hash = self.get_file_hash(path)

        serialized_findings = []
        for finding in findings:
            try:
                serialized = self._serialize_finding(finding)
                serialized_findings.append(serialized)
            except (TypeError, ValueError) as e:
                logger.debug("Failed to serialize finding: %s", e)
                continue

        self._cache["files"][path_key] = {
            "hash": file_hash,
            "findings": serialized_findings,
            "timestamp": int(time.time()),
        }
        self._dirty = True

    def get_changed_files(self, all_files: list[Path]) -> list[Path]:
        """Filter to only files that have changed since last cache.

        This is the main entry point for incremental analysis - call this
        to get the list of files that need re-analysis.

        Args:
            all_files: List of all files to potentially analyze

        Returns:
            List of files that are new or have changed content
        """
        changed: list[Path] = []
        cached_files = self._cache.get("files", {})

        for path in all_files:
            path_key = self._path_key(path)
            cached = cached_files.get(path_key)

            if cached is None:
                # New file
                changed.append(path)
                continue

            # Check content hash
            current_hash = self.get_file_hash(path)
            if cached.get("hash") != current_hash:
                changed.append(path)

        logger.debug(
            "Incremental analysis: %d/%d files changed",
            len(changed),
            len(all_files),
        )
        return changed

    def invalidate_file(self, path: Path) -> None:
        """Remove a file from the cache.

        Args:
            path: Path to invalidate
        """
        path_key = self._path_key(path)
        if path_key in self._cache["files"]:
            del self._cache["files"][path_key]
            self._dirty = True

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        self._cache = {
            "version": CACHE_VERSION,
            "files": {},
            "graph": {"hash": None, "detectors": {}},
        }
        self._dirty = True

    # -------------------------------------------------------------------------
    # Graph-level caching methods
    # -------------------------------------------------------------------------

    def get_graph_hash(self, db: Any) -> str:
        """Compute a fast fingerprint of the graph state.

        Uses node and edge counts by type as a lightweight hash.
        This is fast to compute and changes when the graph structure changes.

        Args:
            db: Database connection with execute() method

        Returns:
            Hex digest representing current graph state
        """
        if _HAS_XXHASH:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5(usedforsecurity=False)

        # Count nodes by type - handle both execute() and execute_query_safe()
        db_query = getattr(db, 'execute_query_safe', getattr(db, 'execute', None))
        if db_query is None:
            logger.warning("Database has no execute method, returning dummy hash")
            return "no-db-connection"
        
        node_types = ["File", "Class", "Function"]
        for node_type in node_types:
            try:
                result = db_query(f"MATCH (n:{node_type}) RETURN count(n) AS cnt")
                count = result[0]["cnt"] if result else 0
            except Exception:
                count = 0
            hasher.update(f"{node_type}:{count}|".encode())

        # Count edges by type
        edge_types = ["CALLS", "IMPORTS", "CONTAINS", "INHERITS"]
        for edge_type in edge_types:
            try:
                result = db_query(f"MATCH ()-[r:{edge_type}]->() RETURN count(r) AS cnt")
                count = result[0]["cnt"] if result else 0
            except Exception:
                count = 0
            hasher.update(f"{edge_type}:{count}|".encode())

        return hasher.hexdigest()

    def is_graph_changed(self, db: Any) -> bool:
        """Check if the graph has changed since last cache.

        Args:
            db: Database connection with execute() method

        Returns:
            True if graph structure has changed, False if unchanged
        """
        graph_data = self._cache.get("graph", {})
        cached_hash = graph_data.get("hash")

        if cached_hash is None:
            return True

        current_hash = self.get_graph_hash(db)
        return cached_hash != current_hash

    def cache_graph_findings(
        self, detector_name: str, findings: list["Finding"]
    ) -> None:
        """Store findings from a graph-level detector.

        Args:
            detector_name: Name of the detector (e.g., "GodClassDetector")
            findings: List of Finding objects to cache
        """
        serialized_findings = []
        for finding in findings:
            try:
                serialized = self._serialize_finding(finding)
                serialized_findings.append(serialized)
            except (TypeError, ValueError) as e:
                logger.debug("Failed to serialize graph finding: %s", e)
                continue

        # Ensure graph section exists
        if "graph" not in self._cache:
            self._cache["graph"] = {"hash": None, "detectors": {}}

        self._cache["graph"]["detectors"][detector_name] = serialized_findings
        self._dirty = True

    def get_cached_graph_findings(self, detector_name: str) -> list["Finding"]:
        """Retrieve cached findings for a specific graph detector.

        Args:
            detector_name: Name of the detector to get findings for

        Returns:
            List of Finding objects, empty if not cached
        """
        graph_data = self._cache.get("graph", {})
        detectors = graph_data.get("detectors", {})
        findings_data = detectors.get(detector_name, [])

        findings: list["Finding"] = []
        for finding_data in findings_data:
            try:
                # Copy to avoid mutating cache (deserialize modifies in-place)
                finding = self._deserialize_finding(finding_data.copy())
                findings.append(finding)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug("Failed to deserialize graph finding: %s", e)
                continue

        return findings

    def get_all_cached_graph_findings(self) -> list["Finding"]:
        """Retrieve all cached findings from all graph detectors.

        Returns:
            List of all Finding objects from graph detectors
        """
        graph_data = self._cache.get("graph", {})
        detectors = graph_data.get("detectors", {})

        all_findings: list["Finding"] = []
        for detector_name, findings_data in detectors.items():
            for finding_data in findings_data:
                try:
                    # Copy to avoid mutating cache (deserialize modifies in-place)
                    finding = self._deserialize_finding(finding_data.copy())
                    all_findings.append(finding)
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug(
                        "Failed to deserialize graph finding from %s: %s",
                        detector_name,
                        e,
                    )
                    continue

        return all_findings

    def update_graph_hash(self, db: Any) -> None:
        """Update the cached graph hash after running graph detectors.

        Call this after running all graph detectors to mark the current
        graph state as processed.

        Args:
            db: Database connection with execute() method
        """
        if "graph" not in self._cache:
            self._cache["graph"] = {"hash": None, "detectors": {}}

        self._cache["graph"]["hash"] = self.get_graph_hash(db)
        self._dirty = True

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats (file count, size, etc.)
        """
        files = self._cache.get("files", {})
        total_findings = sum(
            len(f.get("findings", [])) for f in files.values()
        )

        # Graph cache stats
        graph_data = self._cache.get("graph", {})
        graph_detectors = graph_data.get("detectors", {})
        graph_findings = sum(len(f) for f in graph_detectors.values())

        return {
            "cached_files": len(files),
            "total_findings": total_findings,
            "graph_hash": graph_data.get("hash"),
            "graph_detectors": len(graph_detectors),
            "graph_findings": graph_findings,
            "cache_version": self._cache.get("version"),
            "using_xxhash": _HAS_XXHASH,
        }

    def _path_key(self, path: Path) -> str:
        """Convert path to cache key.

        Uses relative paths when possible for portability.

        Args:
            path: Path to convert

        Returns:
            String key for the cache
        """
        # Try to make path relative to common roots for portability
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _invalidate_cache(self) -> None:
        """Delete corrupted cache file and reset in-memory cache."""
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except OSError:
                pass
        self._cache = {
            "version": CACHE_VERSION,
            "files": {},
            "graph": {"hash": None, "detectors": {}},
        }
        self._dirty = False

    def _serialize_finding(self, finding: "Finding") -> dict[str, Any]:
        """Serialize a Finding to JSON-compatible dict.

        Handles special types like Enum, datetime, Path.

        Args:
            finding: Finding object to serialize

        Returns:
            JSON-serializable dictionary
        """
        from repotoire.models import Severity

        # Use dataclasses.asdict for basic conversion
        data = asdict(finding)

        # Convert special types
        if "severity" in data and isinstance(data["severity"], Severity):
            data["severity"] = data["severity"].value
        elif "severity" in data:
            # Already a string from asdict
            data["severity"] = str(data["severity"])

        if "created_at" in data:
            if isinstance(data["created_at"], datetime):
                data["created_at"] = data["created_at"].isoformat()
            elif data["created_at"] is not None:
                data["created_at"] = str(data["created_at"])

        # Remove heavy/non-essential fields to keep cache small
        # These can be regenerated from the detector
        data.pop("graph_context", None)
        data.pop("collaboration_metadata", None)

        return data

    def _deserialize_finding(self, data: dict[str, Any]) -> "Finding":
        """Deserialize a dict back to Finding object.

        Args:
            data: Dictionary from cache

        Returns:
            Finding object

        Raises:
            KeyError: If required fields are missing
            ValueError: If data is malformed
        """
        from repotoire.models import CollaborationMetadata, Finding, Severity

        # Convert severity string back to enum
        if "severity" in data and isinstance(data["severity"], str):
            data["severity"] = Severity(data["severity"])

        # Convert ISO string back to datetime
        if "created_at" in data and isinstance(data["created_at"], str):
            try:
                data["created_at"] = datetime.fromisoformat(data["created_at"])
            except ValueError:
                data["created_at"] = datetime.now()

        # Restore defaults for stripped fields
        if "graph_context" not in data:
            data["graph_context"] = {}
        if "collaboration_metadata" not in data:
            data["collaboration_metadata"] = []
        if "merged_from" not in data:
            data["merged_from"] = []

        # Get valid field names for Finding
        valid_fields = {f.name for f in fields(Finding)}

        # Filter to only valid fields (in case schema evolved)
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return Finding(**filtered_data)

    def __enter__(self) -> "IncrementalCache":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - auto-save cache."""
        self.save_cache()
