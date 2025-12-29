"""Analysis engine that orchestrates all detectors."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from repotoire.graph import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import (
    Finding,
    FindingsSummary,
    CodebaseHealth,
    MetricsBreakdown,
    Severity,
)
from repotoire.detectors.circular_dependency import CircularDependencyDetector
from repotoire.detectors.dead_code import DeadCodeDetector
from repotoire.detectors.god_class import GodClassDetector
from repotoire.detectors.architectural_bottleneck import ArchitecturalBottleneckDetector

# GDS-based graph detectors (REPO-172, REPO-173)
from repotoire.detectors.module_cohesion import ModuleCohesionDetector
from repotoire.detectors.core_utility import CoreUtilityDetector

# GDS-based detectors (REPO-169, REPO-170, REPO-171)
from repotoire.detectors.influential_code import InfluentialCodeDetector
from repotoire.detectors.degree_centrality import DegreeCentralityDetector

# Graph-unique detectors (FAL-115)
from repotoire.detectors.feature_envy import FeatureEnvyDetector
from repotoire.detectors.shotgun_surgery import ShotgunSurgeryDetector
from repotoire.detectors.middle_man import MiddleManDetector
from repotoire.detectors.inappropriate_intimacy import InappropriateIntimacyDetector

# Data clumps detector (REPO-216)
from repotoire.detectors.data_clumps import DataClumpsDetector

# New graph-based detectors (REPO-228, REPO-229, REPO-231)
from repotoire.detectors.async_antipattern import AsyncAntipatternDetector
from repotoire.detectors.type_hint_coverage import TypeHintCoverageDetector
from repotoire.detectors.long_parameter_list import LongParameterListDetector

# Additional graph-based detectors (REPO-221, REPO-223, REPO-232)
from repotoire.detectors.message_chain import MessageChainDetector
from repotoire.detectors.test_smell import TestSmellDetector
from repotoire.detectors.generator_misuse import GeneratorMisuseDetector

# Design smell detectors (REPO-222, REPO-230)
from repotoire.detectors.lazy_class import LazyClassDetector
from repotoire.detectors.refused_bequest import RefusedBequestDetector

# Hybrid detectors (external tool + graph)
from repotoire.detectors.ruff_import_detector import RuffImportDetector
from repotoire.detectors.ruff_lint_detector import RuffLintDetector
from repotoire.detectors.mypy_detector import MypyDetector
from repotoire.detectors.pylint_detector import PylintDetector
from repotoire.detectors.bandit_detector import BanditDetector
from repotoire.detectors.radon_detector import RadonDetector
from repotoire.detectors.jscpd_detector import JscpdDetector
from repotoire.detectors.vulture_detector import VultureDetector
from repotoire.detectors.semgrep_detector import SemgrepDetector
from repotoire.detectors.satd_detector import SATDDetector
from repotoire.detectors.taint_detector import TaintDetector
from repotoire.detectors.deduplicator import FindingDeduplicator
from repotoire.detectors.root_cause_analyzer import RootCauseAnalyzer
from repotoire.detectors.voting_engine import VotingEngine, VotingStrategy, ConfidenceMethod

from repotoire.logging_config import get_logger, LogContext

logger = get_logger(__name__)

# Optional observability (REPO-224)
try:
    from repotoire.observability import (
        get_metrics,
        DETECTOR_DURATION,
        FINDINGS_TOTAL,
        HAS_PROMETHEUS,
    )
except ImportError:
    HAS_PROMETHEUS = False
    get_metrics = None  # type: ignore
    DETECTOR_DURATION = None  # type: ignore
    FINDINGS_TOTAL = None  # type: ignore


class AnalysisEngine:
    """Orchestrates code smell detection and health scoring."""

    # Grade thresholds (inclusive lower bound, exclusive upper bound except for A)
    GRADES = {
        "A": (90, 100),
        "B": (80, 90),
        "C": (70, 80),
        "D": (60, 70),
        "F": (0, 60),
    }

    # Category weights
    WEIGHTS = {"structure": 0.40, "quality": 0.30, "architecture": 0.30}

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Dict = None,
        repository_path: str = ".",
        keep_metadata: bool = False,
        enable_voting: bool = True,
        voting_strategy: str = "weighted",
        confidence_threshold: float = 0.6,
        parallel: bool = True,
        max_workers: int = 4,
    ):
        """Initialize analysis engine.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional detector configuration dict
            repository_path: Path to repository root (for hybrid detectors)
            keep_metadata: If True, don't cleanup detector metadata after analysis (enables hotspot queries)
            enable_voting: Enable voting engine for multi-detector consensus (REPO-156)
            voting_strategy: Voting strategy ("majority", "weighted", "threshold", "unanimous")
            confidence_threshold: Minimum confidence to include finding (0.0-1.0)
            parallel: Run independent detectors in parallel (REPO-217)
            max_workers: Maximum thread pool workers for parallel execution (default: 4)
        """
        self.db = neo4j_client
        self.repository_path = repository_path
        self.keep_metadata = keep_metadata
        self.enable_voting = enable_voting
        self.parallel = parallel
        self.max_workers = max_workers
        # Check if using FalkorDB (no GDS support)
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"
        config = detector_config or {}

        # Initialize GraphEnricher for cross-detector collaboration (REPO-151 Phase 2)
        self.enricher = GraphEnricher(neo4j_client)

        # Initialize FindingDeduplicator for reducing duplicate findings (REPO-152 Phase 3)
        self.deduplicator = FindingDeduplicator(line_proximity_threshold=5)

        # Initialize RootCauseAnalyzer for cross-detector pattern recognition (REPO-155)
        self.root_cause_analyzer = RootCauseAnalyzer()

        # Initialize VotingEngine for multi-detector consensus (REPO-156)
        strategy_map = {
            "majority": VotingStrategy.MAJORITY,
            "weighted": VotingStrategy.WEIGHTED,
            "threshold": VotingStrategy.THRESHOLD,
            "unanimous": VotingStrategy.UNANIMOUS,
        }
        self.voting_engine = VotingEngine(
            strategy=strategy_map.get(voting_strategy, VotingStrategy.WEIGHTED),
            confidence_method=ConfidenceMethod.WEIGHTED,
            confidence_threshold=confidence_threshold,
        )

        # Register all detectors
        self.detectors = [
            CircularDependencyDetector(neo4j_client, enricher=self.enricher),
            DeadCodeDetector(neo4j_client, enricher=self.enricher),
            GodClassDetector(neo4j_client, detector_config=detector_config, enricher=self.enricher),
            ArchitecturalBottleneckDetector(neo4j_client, enricher=self.enricher),
            # GDS-based graph detectors (REPO-172, REPO-173)
            ModuleCohesionDetector(neo4j_client),
            CoreUtilityDetector(neo4j_client),
            # GDS-based detectors (REPO-169, REPO-170, REPO-171)
            InfluentialCodeDetector(neo4j_client),
            DegreeCentralityDetector(neo4j_client),
            # Graph-unique detectors (FAL-115: Graph-Enhanced Linting Strategy)
            FeatureEnvyDetector(neo4j_client, detector_config=config.get("feature_envy"), enricher=self.enricher),
            ShotgunSurgeryDetector(neo4j_client, detector_config=config.get("shotgun_surgery"), enricher=self.enricher),
            MiddleManDetector(neo4j_client, detector_config=config.get("middle_man"), enricher=self.enricher),
            InappropriateIntimacyDetector(neo4j_client, detector_config=config.get("inappropriate_intimacy"), enricher=self.enricher),
            # Data clumps detector (REPO-216)
            DataClumpsDetector(neo4j_client, detector_config=config.get("data_clumps"), enricher=self.enricher),
            # New graph-based detectors (REPO-228, REPO-229, REPO-231)
            AsyncAntipatternDetector(neo4j_client, detector_config=config.get("async_antipattern"), enricher=self.enricher),
            TypeHintCoverageDetector(neo4j_client, detector_config=config.get("type_hint_coverage"), enricher=self.enricher),
            LongParameterListDetector(neo4j_client, detector_config=config.get("long_parameter_list"), enricher=self.enricher),
            # Additional graph-based detectors (REPO-221, REPO-223, REPO-232)
            MessageChainDetector(neo4j_client, detector_config=config.get("message_chain"), enricher=self.enricher),
            TestSmellDetector(neo4j_client, detector_config=config.get("test_smell"), enricher=self.enricher),
            GeneratorMisuseDetector(neo4j_client, detector_config=config.get("generator_misuse"), enricher=self.enricher),
            # Design smell detectors (REPO-222, REPO-230)
            LazyClassDetector(neo4j_client, detector_config=config.get("lazy_class"), enricher=self.enricher),
            RefusedBequestDetector(neo4j_client, detector_config=config.get("refused_bequest"), enricher=self.enricher),
            # TrulyUnusedImportsDetector has high false positive rate - replaced by RuffImportDetector
            # TrulyUnusedImportsDetector(neo4j_client, detector_config=config.get("truly_unused_imports")),
            # Hybrid detectors (external tool + graph)
            RuffImportDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            RuffLintDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            MypyDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # PylintDetector in selective mode: only checks that Ruff doesn't cover (the 10%)
            # Uses parallel processing for optimal performance on multi-core systems
            # Note: R0801 (duplicate-code) removed - too slow (O(n²)), use RadonDetector instead
            PylintDetector(
                neo4j_client,
                detector_config={
                    "repository_path": repository_path,
                    "enable_only": [
                        # Design checks (class/module structure)
                        "R0901",  # too-many-ancestors
                        "R0902",  # too-many-instance-attributes
                        "R0903",  # too-few-public-methods
                        "R0904",  # too-many-public-methods
                        "R0916",  # too-many-boolean-expressions
                        # Advanced refactoring
                        "R1710",  # inconsistent-return-statements
                        "R1711",  # useless-return
                        "R1703",  # simplifiable-if-statement
                        "C0206",  # consider-using-dict-items
                        # Import analysis
                        "R0401",  # import-self
                        "R0402",  # cyclic-import
                    ],
                    "max_findings": 50,  # Limit to keep it fast
                    "jobs": min(4, os.cpu_count() or 1)  # Use max 4 cores to avoid freezing
                },
                enricher=self.enricher  # Enable graph enrichment
            ),
            BanditDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            RadonDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Duplicate code detection (fast, replaces slow Pylint R0801)
            JscpdDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Advanced unused code detection (more accurate than graph-based DeadCodeDetector)
            VultureDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Advanced security patterns (more powerful than Bandit)
            SemgrepDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # SATD (Self-Admitted Technical Debt) detector (REPO-410)
            # Scans TODO, FIXME, HACK, XXX, KLUDGE, REFACTOR, TEMP, BUG comments
            SATDDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Taint tracking detector (REPO-411)
            # Traces data from untrusted sources to dangerous sinks
            # Detects SQL injection, command injection, XSS, etc.
            TaintDetector(
                neo4j_client,
                detector_config={"repository_path": repository_path},
                enricher=self.enricher  # Enable graph enrichment
            ),
        ]

        # Lazy import to avoid circular dependency (security -> detectors.base -> detectors -> engine)
        from repotoire.security.dependency_scanner import DependencyScanner

        # Dependency vulnerability scanner (REPO-413)
        # Scans for vulnerable dependencies using pip-audit (with safety fallback)
        self.detectors.append(
            DependencyScanner(
                neo4j_client,
                detector_config={
                    "repository_path": repository_path,
                    "max_findings": config.get("dependency_scanner", {}).get("max_findings", 50),
                    "ignore_packages": config.get("dependency_scanner", {}).get("ignore_packages", []),
                    "check_outdated": config.get("dependency_scanner", {}).get("check_outdated", False),
                },
            )
        )

    def analyze(self) -> CodebaseHealth:
        """Run complete analysis and generate health report.

        Returns:
            CodebaseHealth report
        """
        start_time = time.time()

        with LogContext(operation="analyze"):
            logger.info("Starting codebase analysis")

            try:
                # Run all detectors
                findings = self._run_detectors()

                # Run root cause analysis (REPO-155)
                # Identifies god classes that cause cascading issues
                findings = self.root_cause_analyzer.analyze(findings)
                root_cause_summary = self.root_cause_analyzer.get_summary()
                if root_cause_summary["total_root_causes"] > 0:
                    logger.info(
                        f"Root cause analysis: {root_cause_summary['total_root_causes']} root causes "
                        f"affecting {root_cause_summary['total_cascading_issues']} cascading issues"
                    )

                # Store root cause summary for reporting
                self.root_cause_summary = root_cause_summary

                # Apply voting engine for multi-detector consensus (REPO-156)
                original_count = len(findings)
                if self.enable_voting:
                    findings, voting_stats = self.voting_engine.vote(findings)
                    self.voting_stats = voting_stats

                    if voting_stats.get("boosted_by_consensus", 0) > 0:
                        logger.info(
                            f"Voting engine: {voting_stats['boosted_by_consensus']} findings "
                            f"boosted by multi-detector consensus"
                        )
                else:
                    # Fall back to simple deduplication (REPO-152 Phase 3)
                    findings, dedup_stats = self.deduplicator.merge_duplicates(findings)
                    self.voting_stats = None
                    self.dedup_stats = dedup_stats

                deduplicated_count = len(findings)
                if original_count != deduplicated_count:
                    logger.debug(
                        f"Processed {original_count} findings to {deduplicated_count} "
                        f"({original_count - deduplicated_count} filtered/merged)"
                    )

                # Calculate metrics (incorporating detector findings)
                metrics = self._calculate_metrics(findings)

                # Calculate scores
                structure_score = self._score_structure(metrics)
                quality_score = self._score_quality(metrics)
                architecture_score = self._score_architecture(metrics)

                overall_score = (
                    structure_score * self.WEIGHTS["structure"]
                    + quality_score * self.WEIGHTS["quality"]
                    + architecture_score * self.WEIGHTS["architecture"]
                )

                grade = self._score_to_grade(overall_score)

                findings_summary = self._summarize_findings(findings)

                duration = time.time() - start_time
                logger.info("Analysis complete", extra={
                    "grade": grade,
                    "overall_score": round(overall_score, 2),
                    "total_findings": len(findings),
                    "duration_seconds": round(duration, 3)
                })

                return CodebaseHealth(
                    grade=grade,
                    overall_score=overall_score,
                    structure_score=structure_score,
                    quality_score=quality_score,
                    architecture_score=architecture_score,
                    metrics=metrics,
                    findings_summary=findings_summary,
                    findings=findings,
                    dedup_stats=getattr(self, 'dedup_stats', None),
                    root_cause_summary=getattr(self, 'root_cause_summary', None),
                    voting_stats=getattr(self, 'voting_stats', None),
                )

            finally:
                # Clean up temporary detector metadata from graph (REPO-151 Phase 2)
                # This removes DetectorMetadata nodes and FLAGGED_BY relationships
                # after analysis is complete (unless --keep-metadata flag is set)
                if not self.keep_metadata:
                    try:
                        deleted_count = self.enricher.cleanup_metadata()
                        logger.debug(f"Cleaned up {deleted_count} detector metadata nodes from graph")
                    except Exception as e:
                        # Don't fail analysis if cleanup fails
                        logger.warning(f"Failed to clean up detector metadata: {e}")
                else:
                    logger.info("Keeping detector metadata in graph for hotspot queries (use 'repotoire hotspots' command)")

    def _run_detectors(self) -> List[Finding]:
        """Run all registered detectors with two-phase parallel execution.

        REPO-217: Implements parallel execution for improved performance.

        Phase 1: Run all independent detectors (needs_previous_findings=False)
                 in parallel using ThreadPoolExecutor.
        Phase 2: Run dependent detectors (needs_previous_findings=True)
                 sequentially, passing accumulated findings.

        Returns:
            Combined list of all findings
        """
        # Classify detectors based on whether they need previous findings
        independent_detectors = [d for d in self.detectors if not d.needs_previous_findings]
        dependent_detectors = [d for d in self.detectors if d.needs_previous_findings]

        logger.info(
            f"Detector classification: {len(independent_detectors)} independent, "
            f"{len(dependent_detectors)} dependent (need previous findings)"
        )

        all_findings: List[Finding] = []

        # Phase 1: Run independent detectors (optionally in parallel)
        if self.parallel and len(independent_detectors) > 1:
            logger.info(
                f"Phase 1: Running {len(independent_detectors)} independent detectors "
                f"in parallel (workers={self.max_workers})"
            )
            phase1_findings = self._run_detectors_parallel(independent_detectors)
        else:
            mode = "sequentially" if not self.parallel else "sequentially (single detector)"
            logger.info(f"Phase 1: Running {len(independent_detectors)} independent detectors {mode}")
            phase1_findings = self._run_detectors_sequential(independent_detectors)

        all_findings.extend(phase1_findings)
        logger.info(f"Phase 1 complete: {len(phase1_findings)} findings from independent detectors")

        # Phase 2: Run dependent detectors (can also be parallel since they depend
        # on Phase 1 detectors, not on each other)
        if dependent_detectors:
            if self.parallel and len(dependent_detectors) > 1:
                logger.info(
                    f"Phase 2: Running {len(dependent_detectors)} dependent detectors "
                    f"in parallel (workers={self.max_workers})"
                )
                phase2_findings = self._run_detectors_parallel_with_findings(
                    dependent_detectors,
                    previous_findings=all_findings
                )
            else:
                logger.info(f"Phase 2: Running {len(dependent_detectors)} dependent detectors sequentially")
                phase2_findings = self._run_detectors_sequential(
                    dependent_detectors,
                    previous_findings=all_findings
                )
            all_findings.extend(phase2_findings)
            logger.info(f"Phase 2 complete: {len(phase2_findings)} findings from dependent detectors")

        logger.info("All detectors complete", extra={
            "total_findings": len(all_findings),
            "detectors_run": len(self.detectors),
            "parallel_mode": self.parallel
        })

        return all_findings

    def _run_detectors_parallel(self, detectors: list) -> List[Finding]:
        """Run detectors in parallel using ThreadPoolExecutor.

        Args:
            detectors: List of detector instances to run

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all detectors
            future_to_detector = {
                executor.submit(self._run_single_detector, d): d
                for d in detectors
            }

            # Collect results as they complete
            for future in as_completed(future_to_detector):
                detector = future_to_detector[future]
                detector_name = detector.__class__.__name__

                try:
                    findings = future.result()
                    all_findings.extend(findings)
                except Exception as e:
                    logger.error(
                        f"Detector failed in parallel execution: {detector_name}",
                        extra={"error": str(e)},
                        exc_info=True
                    )

        return all_findings

    def _run_detectors_parallel_with_findings(
        self,
        detectors: list,
        previous_findings: List[Finding]
    ) -> List[Finding]:
        """Run dependent detectors in parallel, passing previous_findings to each.

        Since dependent detectors only depend on Phase 1 findings (not on each other),
        they can safely run in parallel with a shared read-only view of previous_findings.

        Args:
            detectors: List of detector instances that need previous_findings
            previous_findings: Findings from Phase 1 (read-only, shared across threads)

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all detectors with previous_findings
            future_to_detector = {
                executor.submit(
                    self._run_single_detector_with_findings, d, previous_findings
                ): d
                for d in detectors
            }

            # Collect results as they complete
            for future in as_completed(future_to_detector):
                detector = future_to_detector[future]
                detector_name = detector.__class__.__name__

                try:
                    findings = future.result()
                    all_findings.extend(findings)
                except Exception as e:
                    logger.error(
                        f"Detector failed in parallel execution: {detector_name}",
                        extra={"error": str(e)},
                        exc_info=True
                    )

        return all_findings

    def _run_single_detector_with_findings(
        self,
        detector,
        previous_findings: List[Finding]
    ) -> List[Finding]:
        """Run a single detector that needs previous_findings.

        Args:
            detector: Detector instance to run
            previous_findings: Findings from previous detectors

        Returns:
            List of findings (empty list on error)
        """
        detector_name = detector.__class__.__name__

        with LogContext(detector=detector_name):
            start_time = time.time()
            logger.info(f"Running detector: {detector_name}")

            try:
                findings = detector.detect(previous_findings=previous_findings)
                duration = time.time() - start_time

                logger.info(f"Detector complete: {detector_name}", extra={
                    "findings_count": len(findings),
                    "duration_seconds": round(duration, 3)
                })

                return findings

            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"Detector failed: {detector_name}",
                    extra={"error": str(e), "duration_seconds": round(duration, 3)},
                    exc_info=True
                )
                return []

    def _run_detectors_sequential(
        self,
        detectors: list,
        previous_findings: List[Finding] = None
    ) -> List[Finding]:
        """Run detectors sequentially with optional previous findings.

        Args:
            detectors: List of detector instances to run
            previous_findings: Optional findings to pass to detectors that need them

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []
        accumulated_findings = list(previous_findings) if previous_findings else []

        for detector in detectors:
            detector_name = detector.__class__.__name__

            with LogContext(detector=detector_name):
                start_time = time.time()
                logger.info(f"Running detector: {detector_name}")

                try:
                    # Pass previous findings if detector needs them
                    if detector.needs_previous_findings:
                        findings = detector.detect(previous_findings=accumulated_findings)
                        logger.debug(
                            f"{detector_name} received {len(accumulated_findings)} previous findings"
                        )
                    else:
                        findings = detector.detect()

                    duration = time.time() - start_time

                    logger.info(f"Detector complete: {detector_name}", extra={
                        "findings_count": len(findings),
                        "duration_seconds": round(duration, 3)
                    })

                    all_findings.extend(findings)
                    accumulated_findings.extend(findings)

                except Exception as e:
                    duration = time.time() - start_time
                    logger.error(
                        f"Detector failed: {detector_name}",
                        extra={"error": str(e), "duration_seconds": round(duration, 3)},
                        exc_info=True
                    )

        return all_findings

    def _run_single_detector(self, detector) -> List[Finding]:
        """Run a single detector with timing, error handling, and metrics.

        Args:
            detector: Detector instance to run

        Returns:
            List of findings (empty list on error)
        """
        detector_name = detector.__class__.__name__

        with LogContext(detector=detector_name):
            start_time = time.time()
            logger.info(f"Running detector: {detector_name}")

            try:
                findings = detector.detect()
                duration = time.time() - start_time

                logger.info(f"Detector complete: {detector_name}", extra={
                    "findings_count": len(findings),
                    "duration_seconds": round(duration, 3)
                })

                # Record observability metrics (REPO-224)
                if HAS_PROMETHEUS and DETECTOR_DURATION is not None:
                    DETECTOR_DURATION.labels(detector=detector_name).observe(duration)
                    # Record findings by severity
                    for finding in findings:
                        FINDINGS_TOTAL.labels(
                            detector=detector_name,
                            severity=finding.severity.value
                        ).inc()

                return findings

            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"Detector failed: {detector_name}",
                    extra={"error": str(e), "duration_seconds": round(duration, 3)},
                    exc_info=True
                )
                return []

    def _calculate_metrics(self, findings: List[Finding]) -> MetricsBreakdown:
        """Calculate detailed code metrics.

        Args:
            findings: List of findings from detectors

        Returns:
            MetricsBreakdown with all metrics
        """
        stats = self.db.get_stats()

        # Count findings by detector type
        circular_deps = sum(
            1 for f in findings if f.detector == "CircularDependencyDetector"
        )
        god_classes = sum(1 for f in findings if f.detector == "GodClassDetector")
        dead_code_items = sum(1 for f in findings if f.detector == "DeadCodeDetector")

        # Calculate dead code percentage
        total_nodes = stats.get("total_classes", 0) + stats.get("total_functions", 0)
        dead_code_pct = (dead_code_items / total_nodes) if total_nodes > 0 else 0.0

        # Calculate average coupling from graph
        coupling_query = """
        MATCH (c:Class)-[:CONTAINS]->(m:Function)-[:CALLS]->()
        WITH c, count(*) as calls
        RETURN avg(calls) as avg_coupling
        """
        coupling_result = self.db.execute_query(coupling_query)
        if coupling_result and coupling_result[0].get("avg_coupling") is not None:
            avg_coupling = float(coupling_result[0]["avg_coupling"])
        else:
            avg_coupling = 0.0

        # Calculate modularity using community detection
        modularity = self._calculate_modularity()

        return MetricsBreakdown(
            total_files=stats.get("total_files", 0),
            total_classes=stats.get("total_classes", 0),
            total_functions=stats.get("total_functions", 0),
            modularity=modularity,
            avg_coupling=avg_coupling,
            circular_dependencies=circular_deps,
            bottleneck_count=0,  # TODO: Implement bottleneck detection
            dead_code_percentage=dead_code_pct,
            duplication_percentage=0.0,  # TODO: Implement duplication detection
            god_class_count=god_classes,
            layer_violations=0,  # TODO: Implement layer violation detection
            boundary_violations=0,  # TODO: Implement boundary violation detection
            abstraction_ratio=0.5,  # TODO: Calculate from abstract classes
        )

    def _score_structure(self, m: MetricsBreakdown) -> float:
        """Score graph structure metrics."""
        modularity_score = m.modularity * 100
        avg_coupling = m.avg_coupling if m.avg_coupling is not None else 0.0
        coupling_score = max(0, 100 - (avg_coupling * 10))
        cycle_penalty = min(50, m.circular_dependencies * 10)
        cycle_score = 100 - cycle_penalty
        bottleneck_penalty = min(30, m.bottleneck_count * 5)
        bottleneck_score = 100 - bottleneck_penalty

        return (modularity_score + coupling_score + cycle_score + bottleneck_score) / 4

    def _score_quality(self, m: MetricsBreakdown) -> float:
        """Score code quality metrics."""
        dead_code_score = 100 - (m.dead_code_percentage * 100)
        duplication_score = 100 - (m.duplication_percentage * 100)
        god_class_penalty = min(40, m.god_class_count * 15)
        god_class_score = 100 - god_class_penalty

        return (dead_code_score + duplication_score + god_class_score) / 3

    def _score_architecture(self, m: MetricsBreakdown) -> float:
        """Score architecture health."""
        layer_penalty = min(50, m.layer_violations * 5)
        layer_score = 100 - layer_penalty

        boundary_penalty = min(40, m.boundary_violations * 3)
        boundary_score = 100 - boundary_penalty

        # Abstraction: 0.3-0.7 is ideal
        if 0.3 <= m.abstraction_ratio <= 0.7:
            abstraction_score = 100
        else:
            distance = min(
                abs(m.abstraction_ratio - 0.3), abs(m.abstraction_ratio - 0.7)
            )
            abstraction_score = max(50, 100 - (distance * 100))

        return (layer_score + boundary_score + abstraction_score) / 3

    def _score_to_grade(self, score: float) -> str:
        """Convert numeric score to letter grade.

        Uses inclusive lower bound and exclusive upper bound, except for grade A
        which includes the maximum score of 100.
        """
        for grade, (min_score, max_score) in self.GRADES.items():
            if grade == "A":
                # A grade: 90 <= score <= 100 (inclusive on both ends)
                if min_score <= score <= max_score:
                    return grade
            else:
                # Other grades: min <= score < max
                if min_score <= score < max_score:
                    return grade
        return "F"

    def _summarize_findings(self, findings: List[Finding]) -> FindingsSummary:
        """Summarize findings by severity."""
        summary = FindingsSummary()

        for finding in findings:
            if finding.severity == Severity.CRITICAL:
                summary.critical += 1
            elif finding.severity == Severity.HIGH:
                summary.high += 1
            elif finding.severity == Severity.MEDIUM:
                summary.medium += 1
            elif finding.severity == Severity.LOW:
                summary.low += 1
            else:
                summary.info += 1

        return summary

    def _calculate_modularity(self) -> float:
        """Calculate modularity score using graph-based community detection.

        Modularity measures how well the codebase is divided into modules.
        A score near 0 means poorly separated, while 0.3-0.7 is good.

        This uses a simplified algorithm based on import relationships.
        In production, this would use Louvain or Label Propagation via Neo4j GDS.

        Returns:
            Modularity score (0-1, typically 0.3-0.7 for well-modularized code)
        """
        try:
            # Skip GDS for FalkorDB (no GDS plugin support)
            if not self.is_falkordb:
                # Try using Neo4j GDS Louvain algorithm if available
                gds_query = """
                CALL gds.graph.exists('codeGraph') YIELD exists
                WHERE exists
                CALL gds.louvain.stream('codeGraph')
                YIELD nodeId, communityId
                WITH gds.util.asNode(nodeId) AS node, communityId
                RETURN count(DISTINCT communityId) AS num_communities,
                       count(node) AS num_nodes
                """

                try:
                    result = self.db.execute_query(gds_query)
                    if result and result[0].get("num_communities", 0) > 0:
                        # Calculate modularity from communities
                        # Simple approximation: more balanced communities = higher modularity
                        num_communities = result[0]["num_communities"]
                        num_nodes = result[0]["num_nodes"]

                        # Ideal: sqrt(n) communities for n nodes
                        import math
                        ideal_communities = math.sqrt(num_nodes) if num_nodes > 0 else 1
                        ratio = min(num_communities, ideal_communities) / max(num_communities, ideal_communities, 1)

                        return min(0.9, max(0.3, ratio * 0.7))
                except Exception:
                    # GDS not available or graph not created, fall back to simpler method
                    pass

            # Fallback: Calculate simple modularity based on file cohesion
            cohesion_query = """
            // Calculate ratio of internal vs external imports
            MATCH (f1:File)-[:CONTAINS]->(:Module)-[r:IMPORTS]->(:Module)<-[:CONTAINS]-(f2:File)
            WITH f1, f2, count(r) AS import_count
            WITH f1,
                 sum(CASE WHEN f1 = f2 THEN import_count ELSE 0 END) AS internal_imports,
                 sum(CASE WHEN f1 <> f2 THEN import_count ELSE 0 END) AS external_imports
            WITH avg(CASE
                WHEN (internal_imports + external_imports) > 0
                THEN toFloat(internal_imports) / (internal_imports + external_imports)
                ELSE 0.5
            END) AS avg_cohesion
            RETURN avg_cohesion
            """

            result = self.db.execute_query(cohesion_query)
            if result and result[0].get("avg_cohesion") is not None:
                avg_cohesion = result[0]["avg_cohesion"]
                # Scale cohesion (0-1) to modularity range (0.3-0.7)
                return 0.3 + (avg_cohesion * 0.4)

        except Exception as e:
            logger.warning(f"Failed to calculate modularity: {e}")

        # Default fallback for well-structured codebases
        return 0.65
