"""Graph enrichment utility for cross-detector collaboration.

This module provides persistent storage of intermediate detector analysis
in Neo4j, enabling cross-session collaboration and finding deduplication.

Part of REPO-151: Phase 2 - Graph Enrichment
"""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class GraphEnricher:
    """Manages persistent detector metadata in Neo4j graph.

    Enables detectors to store intermediate analysis results in the graph
    for collaboration across runs and deduplication of findings.
    """

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize graph enricher.

        Args:
            neo4j_client: Neo4j database client
        """
        self.db = neo4j_client

    def flag_entity(
        self,
        entity_qualified_name: str,
        detector: str,
        severity: str,
        issues: List[str],
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Mark an entity as flagged by a detector.

        Creates a DetectorMetadata node and FLAGGED_BY relationship to track
        that a detector has flagged a specific entity during analysis.

        Args:
            entity_qualified_name: Qualified name of the entity (class, function, file)
            detector: Name of the detector flagging the entity
            severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW, INFO)
            issues: List of issue descriptions
            confidence: Confidence score 0.0-1.0
            metadata: Optional additional metadata dict

        Returns:
            ID of the created DetectorMetadata node

        Example:
            >>> enricher.flag_entity(
            ...     "module.py::MyClass",
            ...     "GodClassDetector",
            ...     "HIGH",
            ...     ["high_lcom", "many_methods"],
            ...     0.9
            ... )
            'detector-metadata-uuid-123'
        """
        metadata_id = f"detector-metadata-{uuid.uuid4()}"
        timestamp = datetime.now().isoformat()

        query = """
        // Find the entity (Class, Function, or File)
        MATCH (entity)
        WHERE entity.qualifiedName = $qualified_name OR entity.filePath = $qualified_name

        // Create metadata node
        CREATE (meta:DetectorMetadata {
            id: $metadata_id,
            detector: $detector,
            severity: $severity,
            issues: $issues,
            confidence: $confidence,
            timestamp: $timestamp,
            metadata: $metadata
        })

        // Create relationship
        CREATE (entity)-[r:FLAGGED_BY {
            severity: $severity,
            confidence: $confidence,
            timestamp: $timestamp
        }]->(meta)

        RETURN meta.id as metadata_id
        """

        try:
            # JSON-encode metadata for Neo4j compatibility (can't store dicts as properties)
            metadata_json = json.dumps(metadata or {})

            result = self.db.execute_query(query, {
                "qualified_name": entity_qualified_name,
                "metadata_id": metadata_id,
                "detector": detector,
                "severity": severity,
                "issues": issues,
                "confidence": confidence,
                "timestamp": timestamp,
                "metadata": metadata_json
            })

            if result:
                logger.debug(f"Flagged entity {entity_qualified_name} by {detector}")
                return result[0]["metadata_id"]
            else:
                logger.debug(f"Entity not found for flagging: {entity_qualified_name}")
                return metadata_id

        except Exception as e:
            logger.error(f"Failed to flag entity {entity_qualified_name}: {e}")
            return metadata_id

    def get_flagged_entities(
        self,
        detector: Optional[str] = None,
        severity: Optional[str] = None,
        min_confidence: float = 0.0
    ) -> List[Dict]:
        """Query flagged entities with optional filtering.

        Args:
            detector: Optional detector name to filter by
            severity: Optional severity level to filter by
            min_confidence: Minimum confidence score (0.0-1.0)

        Returns:
            List of dicts with entity info and metadata

        Example:
            >>> enricher.get_flagged_entities(detector="GodClassDetector", severity="HIGH")
            [{'entity': 'module.py::MyClass', 'detector': 'GodClassDetector', ...}]
        """
        query = """
        MATCH (entity)-[r:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE 1=1
        """

        params = {"min_confidence": min_confidence}

        if detector:
            query += " AND meta.detector = $detector"
            params["detector"] = detector

        if severity:
            query += " AND meta.severity = $severity"
            params["severity"] = severity

        query += """
        AND meta.confidence >= $min_confidence
        RETURN
            COALESCE(entity.qualifiedName, entity.filePath) as entity,
            labels(entity) as entity_types,
            meta.detector as detector,
            meta.severity as severity,
            meta.issues as issues,
            meta.confidence as confidence,
            meta.timestamp as timestamp,
            meta.metadata as metadata
        ORDER BY meta.timestamp DESC
        """

        try:
            results = self.db.execute_query(query, params)
            logger.debug(f"Retrieved {len(results)} flagged entities")
            return results
        except Exception as e:
            logger.error(f"Failed to get flagged entities: {e}")
            return []

    def get_entity_flags(self, entity_qualified_name: str) -> List[Dict]:
        """Get all flags for a specific entity.

        Args:
            entity_qualified_name: Qualified name of the entity

        Returns:
            List of flag metadata dicts

        Example:
            >>> flags = enricher.get_entity_flags("module.py::MyClass")
            >>> print(f"Entity flagged by {len(flags)} detectors")
        """
        query = """
        MATCH (entity)-[:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE entity.qualifiedName = $qualified_name OR entity.filePath = $qualified_name
        RETURN
            meta.detector as detector,
            meta.severity as severity,
            meta.issues as issues,
            meta.confidence as confidence,
            meta.timestamp as timestamp,
            meta.metadata as metadata
        ORDER BY meta.timestamp DESC
        """

        try:
            results = self.db.execute_query(query, {
                "qualified_name": entity_qualified_name
            })
            return results
        except Exception as e:
            logger.error(f"Failed to get entity flags for {entity_qualified_name}: {e}")
            return []

    def is_entity_flagged(
        self,
        entity_qualified_name: str,
        detector: Optional[str] = None
    ) -> bool:
        """Check if an entity has been flagged.

        Args:
            entity_qualified_name: Qualified name of the entity
            detector: Optional specific detector to check

        Returns:
            True if entity is flagged (by specified detector if provided)

        Example:
            >>> if enricher.is_entity_flagged("module.py::MyClass", "GodClassDetector"):
            ...     print("Already flagged as god class")
        """
        query = """
        MATCH (entity)-[:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE (entity.qualifiedName = $qualified_name OR entity.filePath = $qualified_name)
        """

        params = {"qualified_name": entity_qualified_name}

        if detector:
            query += " AND meta.detector = $detector"
            params["detector"] = detector

        query += " RETURN count(*) as flag_count"

        try:
            result = self.db.execute_query(query, params)
            return result[0]["flag_count"] > 0 if result else False
        except Exception as e:
            logger.error(f"Failed to check if entity flagged: {e}")
            return False

    def cleanup_metadata(self, detector: Optional[str] = None) -> int:
        """Remove detector metadata from graph.

        Should be called after analysis to clean up temporary collaboration data.

        Args:
            detector: Optional detector name to clean up (cleans all if not specified)

        Returns:
            Number of metadata nodes deleted

        Example:
            >>> enricher.cleanup_metadata()  # Clean all
            >>> enricher.cleanup_metadata("GodClassDetector")  # Clean specific detector
        """
        query = """
        MATCH (meta:DetectorMetadata)
        """

        params = {}
        if detector:
            query += " WHERE meta.detector = $detector"
            params["detector"] = detector

        query += """
        DETACH DELETE meta
        RETURN count(*) as deleted_count
        """

        try:
            result = self.db.execute_query(query, params)
            deleted_count = result[0]["deleted_count"] if result else 0
            logger.info(f"Cleaned up {deleted_count} detector metadata nodes")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to cleanup metadata: {e}")
            return 0

    def get_duplicate_findings(
        self,
        entity_qualified_name: str,
        min_detectors: int = 2
    ) -> List[Dict]:
        """Find entities flagged by multiple detectors (potential duplicates).

        Args:
            entity_qualified_name: Entity to check
            min_detectors: Minimum number of detectors that must agree

        Returns:
            List of detectors that flagged this entity

        Example:
            >>> dupes = enricher.get_duplicate_findings("module.py::MyClass", min_detectors=2)
            >>> if len(dupes) >= 2:
            ...     print(f"Multiple detectors agree: {[d['detector'] for d in dupes]}")
        """
        query = """
        MATCH (entity)-[:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE (entity.qualifiedName = $qualified_name OR entity.filePath = $qualified_name)
        WITH entity, collect(DISTINCT meta.detector) as detectors, collect(meta) as metadata
        WHERE size(detectors) >= $min_detectors
        UNWIND metadata as meta
        RETURN
            meta.detector as detector,
            meta.severity as severity,
            meta.confidence as confidence,
            detectors as all_detectors
        """

        try:
            results = self.db.execute_query(query, {
                "qualified_name": entity_qualified_name,
                "min_detectors": min_detectors
            })
            return results
        except Exception as e:
            logger.error(f"Failed to get duplicate findings: {e}")
            return []

    def find_hotspots(
        self,
        min_detectors: int = 2,
        min_confidence: float = 0.0,
        severity: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Find code hotspots flagged by multiple detectors.

        Args:
            min_detectors: Minimum number of detectors that must flag an entity
            min_confidence: Minimum average confidence score (0.0-1.0)
            severity: Optional severity filter (e.g., "HIGH", "MEDIUM")
            limit: Maximum results to return

        Returns:
            List of hotspot dictionaries with entity info and detector counts

        Example:
            >>> hotspots = enricher.find_hotspots(min_detectors=3, min_confidence=0.85)
            >>> for spot in hotspots:
            ...     print(f"{spot['entity']}: {spot['detector_count']} detectors")
        """
        severity_filter = "AND meta.severity = $severity" if severity else ""

        query = f"""
        MATCH (entity)-[r:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE meta.confidence >= $min_confidence
        {severity_filter}
        WITH entity,
             collect(DISTINCT meta.detector) as detectors,
             avg(meta.confidence) as avg_confidence,
             collect(DISTINCT meta.severity) as severities,
             collect(meta.issues) as all_issues
        WHERE size(detectors) >= $min_detectors
        RETURN
            coalesce(entity.qualifiedName, entity.filePath, entity.name) as entity,
            labels(entity)[0] as entity_type,
            size(detectors) as detector_count,
            detectors as detectors,
            avg_confidence as avg_confidence,
            severities[0] as severity,
            reduce(s = [], issues IN all_issues | s + issues) as issues
        ORDER BY detector_count DESC, avg_confidence DESC
        LIMIT $limit
        """

        try:
            results = self.db.execute_query(query, {
                "min_detectors": min_detectors,
                "min_confidence": min_confidence,
                "severity": severity,
                "limit": limit
            })
            return results
        except Exception as e:
            logger.error(f"Failed to find hotspots: {e}")
            return []

    def find_high_confidence_issues(
        self,
        min_confidence: float = 0.9,
        severity: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Find issues with high confidence scores.

        Args:
            min_confidence: Minimum confidence threshold (default: 0.9)
            severity: Optional severity filter
            limit: Maximum results to return

        Returns:
            List of high-confidence issue dictionaries

        Example:
            >>> issues = enricher.find_high_confidence_issues(min_confidence=0.95, severity="HIGH")
            >>> print(f"Found {len(issues)} high-confidence critical issues")
        """
        severity_filter = "AND meta.severity = $severity" if severity else ""

        query = f"""
        MATCH (entity)-[r:FLAGGED_BY]->(meta:DetectorMetadata)
        WHERE meta.confidence >= $min_confidence
        {severity_filter}
        RETURN
            coalesce(entity.qualifiedName, entity.filePath, entity.name) as entity,
            labels(entity)[0] as entity_type,
            meta.detector as detector,
            meta.confidence as confidence,
            meta.severity as severity,
            meta.issues as issues,
            meta.metadata as metadata
        ORDER BY meta.confidence DESC, meta.severity
        LIMIT $limit
        """

        try:
            results = self.db.execute_query(query, {
                "min_confidence": min_confidence,
                "severity": severity,
                "limit": limit
            })
            return results
        except Exception as e:
            logger.error(f"Failed to find high confidence issues: {e}")
            return []

    def get_file_hotspots(self, file_path: str) -> Dict[str, Any]:
        """Get hotspot analysis for a specific file.

        Args:
            file_path: Relative file path

        Returns:
            Dictionary with file hotspot statistics

        Example:
            >>> stats = enricher.get_file_hotspots("repotoire/models.py")
            >>> print(f"File has {stats['total_flags']} issues from {stats['detector_count']} detectors")
        """
        query = """
        MATCH (file:File {filePath: $file_path})-[:CONTAINS]->(entity)
        OPTIONAL MATCH (entity)-[r:FLAGGED_BY]->(meta:DetectorMetadata)
        WITH file,
             collect(DISTINCT meta.detector) as detectors,
             collect(meta) as all_metadata,
             count(DISTINCT meta) as flag_count
        RETURN
            file.filePath as file_path,
            file.loc as file_loc,
            size(detectors) as detector_count,
            detectors as detectors,
            flag_count as total_flags,
            [m IN all_metadata | {
                detector: m.detector,
                severity: m.severity,
                confidence: m.confidence,
                issues: m.issues
            }] as flags
        """

        try:
            results = self.db.execute_query(query, {"file_path": file_path})
            if results:
                return results[0]
            return {
                "file_path": file_path,
                "detector_count": 0,
                "total_flags": 0,
                "detectors": [],
                "flags": []
            }
        except Exception as e:
            logger.error(f"Failed to get file hotspots: {e}")
            return {
                "file_path": file_path,
                "detector_count": 0,
                "total_flags": 0,
                "error": str(e)
            }
