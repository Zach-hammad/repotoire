"""Analysis engine that orchestrates all detectors."""

import heapq
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Any

from repotoire.graph import DatabaseClient

# Try to import Rust path cache for O(1) reachability queries (REPO-416)
try:
    from repotoire_fast import PyPathCache
    _HAS_PATH_CACHE = True
except ImportError:
    _HAS_PATH_CACHE = False
    PyPathCache = None  # type: ignore
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import (
    Finding,
    FindingsSummary,
    CodebaseHealth,
    MetricsBreakdown,
    Severity,
)

# Insights engine for ML and graph enrichment (REPO-501)
try:
    from repotoire.insights import InsightsEngine, InsightsConfig
    _HAS_INSIGHTS = True
except ImportError:
    _HAS_INSIGHTS = False
    InsightsEngine = None  # type: ignore
    InsightsConfig = None  # type: ignore
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

# Rust-based graph detectors (REPO-433)
from repotoire.detectors.rust_graph_detectors import (
    PackageStabilityDetector,
    TechnicalDebtHotspotDetector,
    LayeredArchitectureDetector,
    CallChainDepthDetector,
    HubDependencyDetector,
    ChangeCouplingDetector,
)

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

# TypeScript/JavaScript detectors
from repotoire.detectors.tsc_detector import TscDetector
from repotoire.detectors.eslint_detector import ESLintDetector
from repotoire.detectors.npm_audit_detector import NpmAuditDetector
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


# REPO-500: Global max findings limit to prevent memory exhaustion
# Very large codebases could generate 100k+ findings, consuming GBs of RAM
# Priority-sorted findings are kept up to this limit
MAX_FINDINGS_LIMIT = int(os.environ.get("REPOTOIRE_MAX_FINDINGS", "10000"))


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
    # Issues category penalizes based on finding severity counts
    WEIGHTS = {"structure": 0.30, "quality": 0.25, "architecture": 0.25, "issues": 0.20}

    def __init__(
        self,
        graph_client: DatabaseClient,
        detector_config: Dict = None,
        repository_path: str = ".",
        repo_id: Optional[str] = None,
        keep_metadata: bool = False,
        enable_voting: bool = True,
        voting_strategy: str = "weighted",
        confidence_threshold: float = 0.6,
        parallel: bool = True,
        max_workers: int = 4,
        changed_files: Optional[List[str]] = None,
        path_cache: Optional["PyPathCache"] = None,
        enable_insights: bool = True,
        insights_config: Optional[Dict] = None,
    ):
        """Initialize analysis engine.

        Args:
            graph_client: Graph database client (FalkorDB)
            detector_config: Optional detector configuration dict
            repository_path: Path to repository root (for hybrid detectors)
            repo_id: Repository UUID for filtering graph queries (multi-tenant isolation)
            keep_metadata: If True, don't cleanup detector metadata after analysis (enables hotspot queries)
            enable_voting: Enable voting engine for multi-detector consensus (REPO-156)
            voting_strategy: Voting strategy ("majority", "weighted", "threshold", "unanimous")
            confidence_threshold: Minimum confidence to include finding (0.0-1.0)
            parallel: Run independent detectors in parallel (REPO-217)
            max_workers: Maximum thread pool workers for parallel execution (default: 4)
            changed_files: List of relative file paths that changed (for incremental hybrid detector analysis)
            path_cache: Optional prebuilt path expression cache for O(1) reachability queries (REPO-416)
            enable_insights: Enable insights engine for ML enrichment and graph metrics (REPO-501)
            insights_config: Optional insights engine configuration dict
        """
        self.db = graph_client
        self.repository_path = repository_path
        self.repo_id = repo_id
        self.keep_metadata = keep_metadata
        self.enable_voting = enable_voting
        self.enable_insights = enable_insights and _HAS_INSIGHTS
        self.insights_config = insights_config or {}
        self.parallel = parallel
        self.max_workers = max_workers
        # Check if using FalkorDB (no GDS support)
        self.is_falkordb = getattr(graph_client, "is_falkordb", False) or type(graph_client).__name__ == "FalkorDBClient"
        # Check if using Kuzu (limited Cypher support - external tools only)
        self.is_kuzu = getattr(graph_client, "is_kuzu", False) or type(graph_client).__name__ == "KuzuClient"
        config = detector_config or {}

        # Path expression cache for O(1) reachability queries (REPO-416)
        # Build from graph if not provided (e.g., when running analyze without ingest)
        self.path_cache = path_cache
        if self.path_cache is None and _HAS_PATH_CACHE:
            self.path_cache = self._build_path_cache()

        # Log path cache status for debugging
        if self.path_cache is not None:
            logger.info("Path cache enabled - detectors will use O(1) reachability queries")
        else:
            logger.warning("Path cache NOT enabled - detectors will use slower Cypher queries")

        # REPO-522: Prefetch node data to reduce HTTP round-trips in cloud mode
        self.node_data_cache: Dict[str, Dict[str, Any]] = {}
        self._prefetch_node_data()

        # Initialize GraphEnricher for cross-detector collaboration (REPO-151 Phase 2)
        self.enricher = GraphEnricher(graph_client)

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

        # Helper to merge detector-specific config with repo_id for multi-tenant filtering
        def with_repo_id(specific_config: Optional[Dict] = None) -> Dict:
            """Merge detector-specific config with repo_id and path_cache for graph query filtering."""
            base = {"repo_id": repo_id} if repo_id else {}
            # Include path cache for O(1) reachability queries (REPO-416)
            if self.path_cache is not None:
                base["path_cache"] = self.path_cache
            # Include node data cache for O(1) property lookups (REPO-522)
            if self.node_data_cache:
                base["node_data_cache"] = self.node_data_cache
            if specific_config:
                base.update(specific_config)
            return base

        # Helper for hybrid detector config (repository_path + changed_files for incremental analysis)
        def hybrid_config(specific_config: Optional[Dict] = None) -> Dict:
            """Build config for hybrid detectors with repository_path and changed_files."""
            base = {"repository_path": repository_path}
            if changed_files:
                base["changed_files"] = changed_files
            if specific_config:
                base.update(specific_config)
            return base

        # Register all detectors (all graph detectors receive repo_id for filtering)
        self.detectors = [
            CircularDependencyDetector(graph_client, detector_config=with_repo_id(), enricher=self.enricher),
            DeadCodeDetector(graph_client, detector_config=with_repo_id(), enricher=self.enricher),
            GodClassDetector(graph_client, detector_config=with_repo_id(config.get("god_class")), enricher=self.enricher),
            ArchitecturalBottleneckDetector(graph_client, detector_config=with_repo_id(), enricher=self.enricher),
            # GDS-based graph detectors (REPO-172, REPO-173)
            ModuleCohesionDetector(graph_client, detector_config=with_repo_id()),
            CoreUtilityDetector(graph_client, detector_config=with_repo_id()),
            # GDS-based detectors (REPO-169, REPO-170, REPO-171)
            InfluentialCodeDetector(graph_client, detector_config=with_repo_id()),
            DegreeCentralityDetector(graph_client, detector_config=with_repo_id()),
            # Graph-unique detectors (FAL-115: Graph-Enhanced Linting Strategy)
            FeatureEnvyDetector(graph_client, detector_config=with_repo_id(config.get("feature_envy")), enricher=self.enricher),
            ShotgunSurgeryDetector(graph_client, detector_config=with_repo_id(config.get("shotgun_surgery")), enricher=self.enricher),
            MiddleManDetector(graph_client, detector_config=with_repo_id(config.get("middle_man")), enricher=self.enricher),
            InappropriateIntimacyDetector(graph_client, detector_config=with_repo_id(config.get("inappropriate_intimacy")), enricher=self.enricher),
            # Data clumps detector (REPO-216)
            DataClumpsDetector(graph_client, detector_config=with_repo_id(config.get("data_clumps")), enricher=self.enricher),
            # New graph-based detectors (REPO-228, REPO-229, REPO-231)
            AsyncAntipatternDetector(graph_client, detector_config=with_repo_id(config.get("async_antipattern")), enricher=self.enricher),
            TypeHintCoverageDetector(graph_client, detector_config=with_repo_id(config.get("type_hint_coverage")), enricher=self.enricher),
            LongParameterListDetector(graph_client, detector_config=with_repo_id(config.get("long_parameter_list")), enricher=self.enricher),
            # Additional graph-based detectors (REPO-221, REPO-223, REPO-232)
            MessageChainDetector(graph_client, detector_config=with_repo_id(config.get("message_chain")), enricher=self.enricher),
            TestSmellDetector(graph_client, detector_config=with_repo_id(config.get("test_smell")), enricher=self.enricher),
            GeneratorMisuseDetector(graph_client, detector_config=with_repo_id(config.get("generator_misuse")), enricher=self.enricher),
            # Design smell detectors (REPO-222, REPO-230)
            LazyClassDetector(graph_client, detector_config=with_repo_id(config.get("lazy_class")), enricher=self.enricher),
            RefusedBequestDetector(graph_client, detector_config=with_repo_id(config.get("refused_bequest")), enricher=self.enricher),
            # Rust-based graph detectors (REPO-433) - high-performance Rust implementations
            PackageStabilityDetector(graph_client, detector_config=with_repo_id(config.get("package_stability")), enricher=self.enricher),
            TechnicalDebtHotspotDetector(graph_client, detector_config=with_repo_id(config.get("technical_debt_hotspot")), enricher=self.enricher),
            LayeredArchitectureDetector(graph_client, detector_config=with_repo_id(config.get("layered_architecture")), enricher=self.enricher),
            CallChainDepthDetector(graph_client, detector_config=with_repo_id(config.get("call_chain_depth")), enricher=self.enricher),
            HubDependencyDetector(graph_client, detector_config=with_repo_id(config.get("hub_dependency")), enricher=self.enricher),
            ChangeCouplingDetector(graph_client, detector_config=with_repo_id(config.get("change_coupling")), enricher=self.enricher),
            # TrulyUnusedImportsDetector has high false positive rate - replaced by RuffImportDetector
            # TrulyUnusedImportsDetector(graph_client, detector_config=config.get("truly_unused_imports")),
            # Hybrid detectors (external tool + graph)
            # All hybrid detectors receive changed_files for incremental analysis (10-100x faster)
            RuffImportDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            RuffLintDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            MypyDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # PylintDetector in selective mode: only checks that Ruff doesn't cover (the 10%)
            # Uses parallel processing for optimal performance on multi-core systems
            # Note: R0801 (duplicate-code) removed - too slow (O(n²)), use RadonDetector instead
            PylintDetector(
                graph_client,
                detector_config=hybrid_config({
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
                }),
                enricher=self.enricher  # Enable graph enrichment
            ),
            BanditDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            RadonDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Duplicate code detection (fast, replaces slow Pylint R0801)
            JscpdDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Advanced unused code detection (more accurate than graph-based DeadCodeDetector)
            VultureDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Advanced security patterns (more powerful than Bandit)
            SemgrepDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # SATD (Self-Admitted Technical Debt) detector (REPO-410)
            # Scans TODO, FIXME, HACK, XXX, KLUDGE, REFACTOR, TEMP, BUG comments
            SATDDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # Taint tracking detector (REPO-411)
            # Traces data from untrusted sources to dangerous sinks
            # Detects SQL injection, command injection, XSS, etc.
            TaintDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher  # Enable graph enrichment
            ),
            # TypeScript/JavaScript detectors
            # TypeScript compiler type checking (like Mypy for Python)
            TscDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher
            ),
            # ESLint code quality checking
            ESLintDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher
            ),
            # npm audit security vulnerability scanning
            NpmAuditDetector(
                graph_client,
                detector_config=hybrid_config(),
                enricher=self.enricher
            ),
        ]

        # Lazy import to avoid circular dependency (security -> detectors.base -> detectors -> engine)
        from repotoire.security.dependency_scanner import DependencyScanner

        # Dependency vulnerability scanner (REPO-413)
        # Scans for vulnerable dependencies using pip-audit (with safety fallback)
        self.detectors.append(
            DependencyScanner(
                graph_client,
                detector_config=hybrid_config({
                    "max_findings": config.get("dependency_scanner", {}).get("max_findings", 50),
                    "ignore_packages": config.get("dependency_scanner", {}).get("ignore_packages", []),
                    "check_outdated": config.get("dependency_scanner", {}).get("check_outdated", False),
                }),
            )
        )

        # Filter detectors based on enabled/disabled configuration
        self.detectors = self._filter_detectors(config)

    def _filter_detectors(self, config: Dict) -> List:
        """Filter detectors based on enabled/disabled configuration.

        Args:
            config: Detector configuration dict with optional keys:
                - enabled_detectors: List of detector names to enable (None = all enabled)
                - disabled_detectors: List of detector names to disable

        Returns:
            Filtered list of detector instances
        """
        enabled = config.get("enabled_detectors")  # None means all enabled
        disabled = config.get("disabled_detectors", [])

        # Normalize detector names (remove "Detector" suffix, lowercase for case-insensitive matching)
        def normalize_name(name: str) -> str:
            return name.replace("Detector", "").replace("-", "").replace("_", "").lower()

        # Build set of disabled detector names (normalized)
        disabled_set = {normalize_name(d) for d in disabled}

        # Build set of enabled detector names (normalized), if specified
        enabled_set = None
        if enabled is not None:
            enabled_set = {normalize_name(e) for e in enabled}

        # Kuzu mode: disable graph-dependent detectors (Cypher compatibility issues)
        # These detectors require features not available in Kuzu (shortestPath, ORDER BY id, etc.)
        kuzu_disabled_detectors = {
            "circulardependency",      # Uses shortestPath
            "godclass",                # Complex pattern comprehensions
            "modulecohesion",          # Uses ORDER BY id() for Rust algorithms
            "coreutility",             # Uses ORDER BY id() for harmonic centrality
            "influentialcode",         # Uses ORDER BY id() for PageRank
            "degreecentrality",        # SET operations
            "shotgunsurgery",          # Slice syntax [0..5]
            "middleman",               # Pattern comprehensions
            # "inappropriateintimacy", # elementId() - now handled by adapter
            "dataclumps",              # CONTAINS relationship issues
            "asyncantipattern",        # Relationship properties
            "typehintcoverage",        # COALESCE with empty map
            # "lazyclass",             # toFloat() - now handled by adapter
            # "refusedbequest",        # toFloat() - now handled by adapter
            "packagestability",        # Slice syntax
            "technicaldebthotspot",    # Property name issues
            "layeredarchitecture",     # Property name issues
            "hubdependency",           # Property name issues
            "changecoupling",          # Commit table
            "deadcode",                # CONTAINS relationship
            "architecturalbottleneck", # ORDER BY id() for betweenness
            "featureenvy",             # labels() function issues
        }

        filtered = []
        skipped = []
        kuzu_skipped = []
        for detector in self.detectors:
            name = detector.__class__.__name__
            normalized = normalize_name(name)

            # Kuzu mode: skip graph-dependent detectors
            if self.is_kuzu and normalized in kuzu_disabled_detectors:
                kuzu_skipped.append(name)
                continue

            # Check if explicitly disabled
            if normalized in disabled_set or name in disabled_set:
                skipped.append(name)
                continue

            # Check if enabled list is specified and this detector is not in it
            if enabled_set is not None:
                if normalized not in enabled_set and name not in enabled_set:
                    skipped.append(name)
                    continue

            filtered.append(detector)

        if kuzu_skipped:
            logger.info(f"Kuzu mode: disabled {len(kuzu_skipped)} graph detectors (external tools active)")
        if skipped:
            logger.info(f"Disabled {len(skipped)} detectors: {', '.join(skipped)}")

        logger.info(f"Active detectors: {len(filtered)}/{len(self.detectors)}")
        return filtered

    def _get_cache_path(self) -> Optional[Path]:
        """Get path for cached graph data file (REPO-524)."""
        if not self.repository_path:
            return None
        cache_dir = Path(self.repository_path) / ".repotoire"
        cache_dir.mkdir(exist_ok=True)
        # Include repo_id in cache name for multi-tenant isolation
        suffix = f"_{self.repo_id[:8]}" if self.repo_id else ""
        return cache_dir / f"graph_cache{suffix}.json"

    def _load_cached_graph_data(self) -> Optional[Dict]:
        """Load cached graph data if fresh (REPO-524).
        
        Returns cached nodes/edges if cache exists and is <1 hour old.
        """
        import json
        import time as time_module
        
        cache_path = self._get_cache_path()
        if not cache_path or not cache_path.exists():
            return None
        
        try:
            # Check cache age (1 hour max)
            cache_age = time_module.time() - cache_path.stat().st_mtime
            if cache_age > 3600:  # 1 hour
                logger.debug(f"Graph cache expired ({cache_age:.0f}s old)")
                return None
            
            with open(cache_path) as f:
                data = json.load(f)
            
            logger.info(f"Loaded graph cache ({cache_age:.0f}s old, {len(data.get('nodes', []))} nodes)")
            return data
        except Exception as e:
            logger.debug(f"Failed to load graph cache: {e}")
            return None

    def _save_graph_cache(self, nodes: list, edges_by_type: Dict[str, list]) -> None:
        """Save graph data to cache file (REPO-524)."""
        import json
        
        cache_path = self._get_cache_path()
        if not cache_path:
            return
        
        try:
            data = {
                "nodes": nodes,
                "edges": edges_by_type,
                "repo_id": self.repo_id,
            }
            with open(cache_path, "w") as f:
                json.dump(data, f)
            logger.debug(f"Saved graph cache: {len(nodes)} nodes")
        except Exception as e:
            logger.debug(f"Failed to save graph cache: {e}")

    def _build_cache_from_data(self, data: Dict) -> Optional["PyPathCache"]:
        """Build path cache from cached data without API queries (REPO-524)."""
        import time as time_module
        start_time = time_module.time()
        
        if not _HAS_PATH_CACHE:
            return None
        
        try:
            from repotoire_fast import PyPathCache
            cache = PyPathCache()
            
            node_names = data.get("nodes", [])
            edges_by_type = data.get("edges", {})
            
            if not node_names:
                return None
            
            # Register nodes
            node_tuples = [(i, name) for i, name in enumerate(node_names)]
            cache.register_nodes(node_tuples)
            num_nodes = len(node_names)
            
            # Build caches for each relationship type
            total_edges = 0
            for rel_type, raw_edges in edges_by_type.items():
                edges = []
                for src_name, dst_name in raw_edges:
                    src_id = cache.get_id(src_name)
                    dst_id = cache.get_id(dst_name)
                    if src_id is not None and dst_id is not None:
                        edges.append((src_id, dst_id))
                
                if edges:
                    cache.build_cache(rel_type, edges, num_nodes)
                    total_edges += len(edges)
            
            elapsed = time_module.time() - start_time
            logger.info(f"Built path cache from local cache: {num_nodes} nodes, {total_edges} edges in {elapsed:.2f}s")
            return cache
            
        except Exception as e:
            logger.warning(f"Failed to build cache from data: {e}")
            return None

    def _build_path_cache(self) -> Optional["PyPathCache"]:
        """Build transitive closure cache for O(1) reachability queries (REPO-416).

        This cache precomputes all reachability relationships for CALLS, IMPORTS,
        and INHERITS edges, enabling O(1) lookup instead of O(V+E) traversal.

        REPO-500: Added query timeouts, memory limits, and repo_id filtering.
        REPO-524: Added local caching to skip API queries on repeated runs.

        Returns:
            PyPathCache instance with precomputed caches, or None if building fails.
        """
        import time as time_module
        start_time = time_module.time()
        
        # REPO-524: Try to load from cache first
        cached_data = self._load_cached_graph_data()
        if cached_data:
            return self._build_cache_from_data(cached_data)
        
        logger.info("Building path cache for O(1) reachability queries...")

        if not _HAS_PATH_CACHE:
            logger.warning("Path cache not available (repotoire_fast not installed)")
            return None

        # REPO-500: Memory limit - skip path cache for very large graphs
        MAX_PATH_CACHE_NODES = 100000  # 100k nodes max to prevent OOM

        try:
            from repotoire_fast import PyPathCache

            cache = PyPathCache()

            # REPO-500: Build repo_id filter for multi-tenant isolation
            repo_filter = ""
            repo_params: Dict[str, Any] = {}
            if self.repo_id:
                repo_filter = "AND n.repoId = $repo_id"
                repo_params["repo_id"] = self.repo_id
                logger.info(f"Path cache filtering by repo_id: {self.repo_id}")

            # Query all nodes with qualified names
            # REPO-500: Added repo_id filter and explicit timeout (120s)
            nodes_query = f"""
            MATCH (n)
            WHERE n.qualifiedName IS NOT NULL {repo_filter}
            RETURN n.qualifiedName AS name
            ORDER BY name
            """
            query_start = time_module.time()
            # REPO-500: Explicit 120s timeout for nodes query (large graphs)
            nodes_result = self.db.execute_query(nodes_query, repo_params, timeout=120.0)
            query_time = time_module.time() - query_start
            node_names = [r["name"] for r in nodes_result if r.get("name")]

            logger.info(f"Path cache nodes query returned {len(node_names)} nodes in {query_time:.2f}s")

            if not node_names:
                logger.warning("No nodes found for path cache - graph may be empty")
                return None

            # REPO-500: Memory limit check
            if len(node_names) > MAX_PATH_CACHE_NODES:
                logger.warning(
                    f"Path cache skipped: {len(node_names)} nodes exceeds limit of {MAX_PATH_CACHE_NODES}. "
                    f"Using Cypher queries instead to prevent OOM."
                )
                return None

            # Register nodes (assigns integer IDs)
            node_tuples = [(i, name) for i, name in enumerate(node_names)]
            cache.register_nodes(node_tuples)
            num_nodes = len(node_names)
            logger.info(f"Registered {num_nodes} nodes for path cache")

            # Build cache for each relationship type
            total_edges = 0
            edges_by_type: Dict[str, list] = {}  # REPO-524: Collect for caching
            for rel_type in ["CALLS", "IMPORTS", "INHERITS"]:
                # REPO-500: Added repo_id filter for edges
                edges_query = f"""
                MATCH (a)-[:{rel_type}]->(b)
                WHERE a.qualifiedName IS NOT NULL AND b.qualifiedName IS NOT NULL
                {repo_filter.replace('n.', 'a.')}
                RETURN a.qualifiedName AS src, b.qualifiedName AS dst
                """
                rel_start = time_module.time()
                # REPO-500: Explicit 300s timeout for edges query (IMPORTS can be dense)
                edges_result = self.db.execute_query(edges_query, repo_params, timeout=300.0)
                rel_query_time = time_module.time() - rel_start

                # Convert to (src_id, dst_id) pairs
                edges = []
                for r in edges_result:
                    src_name = r.get("src")
                    dst_name = r.get("dst")
                    if src_name and dst_name:
                        src_id = cache.get_id(src_name)
                        dst_id = cache.get_id(dst_name)
                        if src_id is not None and dst_id is not None:
                            edges.append((src_id, dst_id))

                # REPO-524: Store raw edge names for caching
                raw_edges = [(r.get("src"), r.get("dst")) for r in edges_result if r.get("src") and r.get("dst")]
                edges_by_type[rel_type] = raw_edges
                
                if edges:
                    cache.build_cache(rel_type, edges, num_nodes)
                    total_edges += len(edges)
                    stats = cache.stats(rel_type)
                    # PyCacheStats is a Rust struct, use attribute access not .get()
                    density = getattr(stats, 'density', 0) if stats else 0
                    logger.info(
                        f"Built {rel_type} cache: {len(edges)} edges in {rel_query_time:.2f}s, "
                        f"density={density:.4f}"
                    )
                else:
                    logger.info(f"No {rel_type} edges found ({rel_query_time:.2f}s)")

            total_time = time_module.time() - start_time
            logger.info(f"Path cache complete: {num_nodes} nodes, {total_edges} edges in {total_time:.2f}s")
            
            # REPO-524: Save to cache for next run
            self._save_graph_cache(node_names, edges_by_type)
            
            return cache

        except TimeoutError as e:
            # REPO-500: Specific handling for timeout errors
            logger.warning(f"Path cache query timed out: {e}. Falling back to Cypher queries.")
            return None
        except MemoryError as e:
            # REPO-500: Specific handling for memory errors
            logger.warning(f"Path cache ran out of memory: {e}. Falling back to Cypher queries.")
            return None
        except Exception as e:
            import traceback
            error_str = str(e).lower()
            # REPO-500: Categorize error types for better diagnostics
            if "timeout" in error_str:
                logger.warning(f"Path cache query timed out: {e}. Falling back to Cypher queries.")
            elif "memory" in error_str or "oom" in error_str:
                logger.warning(f"Path cache ran out of memory: {e}. Falling back to Cypher queries.")
            else:
                logger.warning(f"Failed to build path cache: {e}")
                logger.debug(f"Path cache traceback: {traceback.format_exc()}")
            return None

    def _prefetch_node_data(self) -> None:
        """Prefetch Class and Function node data to reduce HTTP round-trips (REPO-522).
        
        In cloud mode, each detector query is an HTTP round-trip (~200ms).
        This prefetches all node properties in 2 queries, storing in memory
        for O(1) lookup by detectors.
        """
        import time as time_module
        start = time_module.time()
        
        # Build repo filter
        repo_filter = ""
        repo_params: Dict[str, Any] = {}
        if self.repo_id:
            repo_filter = "WHERE n.repoId = $repo_id"
            repo_params["repo_id"] = self.repo_id
        
        try:
            # Prefetch Class nodes with all properties detectors need
            class_query = f"""
            MATCH (n:Class)
            {repo_filter}
            RETURN n.qualifiedName AS name,
                   n.complexity AS complexity,
                   n.loc AS loc,
                   n.decorators AS decorators,
                   n.is_abstract AS is_abstract,
                   n.nesting_level AS nesting_level,
                   n.filePath AS file_path,
                   n.lineStart AS line_start,
                   n.lineEnd AS line_end
            """
            class_results = self.db.execute_query(class_query, repo_params, timeout=60.0)
            for r in class_results:
                name = r.get("name")
                if name:
                    self.node_data_cache[name] = {
                        "type": "Class",
                        "complexity": r.get("complexity", 0),
                        "loc": r.get("loc", 0),
                        "decorators": r.get("decorators", []),
                        "is_abstract": r.get("is_abstract", False),
                        "nesting_level": r.get("nesting_level", 0),
                        "file_path": r.get("file_path"),
                        "line_start": r.get("line_start"),
                        "line_end": r.get("line_end"),
                    }
            
            # Prefetch Function nodes
            func_query = f"""
            MATCH (n:Function)
            {repo_filter}
            RETURN n.qualifiedName AS name,
                   n.complexity AS complexity,
                   n.loc AS loc,
                   n.parameters AS parameters,
                   n.return_type AS return_type,
                   n.is_async AS is_async,
                   n.decorators AS decorators,
                   n.filePath AS file_path,
                   n.lineStart AS line_start,
                   n.lineEnd AS line_end
            """
            func_results = self.db.execute_query(func_query, repo_params, timeout=60.0)
            for r in func_results:
                name = r.get("name")
                if name:
                    self.node_data_cache[name] = {
                        "type": "Function",
                        "complexity": r.get("complexity", 0),
                        "loc": r.get("loc", 0),
                        "parameters": r.get("parameters", []),
                        "return_type": r.get("return_type"),
                        "is_async": r.get("is_async", False),
                        "decorators": r.get("decorators", []),
                        "file_path": r.get("file_path"),
                        "line_start": r.get("line_start"),
                        "line_end": r.get("line_end"),
                    }
            
            elapsed = time_module.time() - start
            logger.info(f"Prefetched {len(self.node_data_cache)} nodes in {elapsed:.2f}s (REPO-522)")
            
        except Exception as e:
            logger.warning(f"Node data prefetch failed: {e}. Detectors will query individually.")
            self.node_data_cache = {}

    def analyze(self, progress_callback=None) -> CodebaseHealth:
        """Run complete analysis and generate health report.

        Args:
            progress_callback: Optional callback function(detector_name, current_index, total_count, status)
                              Called before each detector starts and after it completes.
                              status is "starting" or "completed".

        Returns:
            CodebaseHealth report
        """
        start_time = time.time()

        # REPO-600: Log tenant context for audit trail
        from repotoire.tenant import get_tenant_context
        tenant_ctx = get_tenant_context()

        with LogContext(operation="analyze"):
            if tenant_ctx:
                logger.info(
                    "Starting codebase analysis",
                    extra={
                        "tenant_id": tenant_ctx.org_id_str,
                        "tenant_slug": tenant_ctx.org_slug,
                        "repository_path": self.repository_path,
                        "repo_id": self.repo_id,
                    },
                )
            else:
                logger.info("Starting codebase analysis")

            try:
                # Run all detectors with progress reporting
                findings = self._run_detectors(progress_callback=progress_callback)

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

                # Two-phase deduplication strategy:
                # Phase 1: Deduplicator removes same-detector duplicates
                # Phase 2: Voting engine builds cross-detector consensus
                original_count = len(findings)

                # Phase 1: Always run deduplicator first
                # Uses unified grouping (category-aware) to prevent cross-category merges
                findings, dedup_stats = self.deduplicator.merge_duplicates(findings)
                self.dedup_stats = dedup_stats
                after_dedup_count = len(findings)

                if dedup_stats.get("duplicate_count", 0) > 0:
                    logger.debug(
                        f"Deduplication: removed {dedup_stats['duplicate_count']} duplicates "
                        f"({original_count} -> {after_dedup_count})"
                    )

                # Phase 2: Optionally run voting for multi-detector consensus
                if self.enable_voting:
                    findings, voting_stats = self.voting_engine.vote(findings)
                    self.voting_stats = voting_stats

                    if voting_stats.get("boosted_by_consensus", 0) > 0:
                        logger.info(
                            f"Voting engine: {voting_stats['boosted_by_consensus']} findings "
                            f"boosted by multi-detector consensus"
                        )
                else:
                    self.voting_stats = None

                deduplicated_count = len(findings)
                if original_count != deduplicated_count:
                    logger.debug(
                        f"Processed {original_count} findings to {deduplicated_count} "
                        f"({original_count - deduplicated_count} filtered/merged)"
                    )

                # REPO-500: Apply global findings limit to prevent memory exhaustion
                if len(findings) > MAX_FINDINGS_LIMIT:
                    # Use heapq.nsmallest for O(N log K) instead of O(N log N) full sort
                    severity_order = {
                        Severity.CRITICAL: 0,
                        Severity.HIGH: 1,
                        Severity.MEDIUM: 2,
                        Severity.LOW: 3,
                        Severity.INFO: 4,
                    }
                    findings = heapq.nsmallest(
                        MAX_FINDINGS_LIMIT,
                        findings,
                        key=lambda f: severity_order.get(f.severity, 5)
                    )
                    logger.warning(
                        f"Truncated findings from {deduplicated_count} to {MAX_FINDINGS_LIMIT} "
                        f"(prioritized by severity)"
                    )

                # REPO-501: Run insights engine for ML enrichment and graph metrics
                self.codebase_insights = None
                if self.enable_insights:
                    try:
                        insights_cfg = InsightsConfig(**self.insights_config) if self.insights_config else InsightsConfig()
                        insights_engine = InsightsEngine(self.db, insights_cfg)
                        findings, self.codebase_insights = insights_engine.enrich(findings)
                    except Exception as e:
                        logger.warning(f"Insights engine failed (non-fatal): {e}")

                # Calculate metrics (incorporating detector findings)
                metrics = self._calculate_metrics(findings)

                # Summarize findings by severity (needed for issues score)
                findings_summary = self._summarize_findings(findings)

                # Calculate scores
                structure_score = self._score_structure(metrics)
                quality_score = self._score_quality(metrics)
                architecture_score = self._score_architecture(metrics)
                issues_score = self._score_issues(findings_summary)

                overall_score = (
                    structure_score * self.WEIGHTS["structure"]
                    + quality_score * self.WEIGHTS["quality"]
                    + architecture_score * self.WEIGHTS["architecture"]
                    + issues_score * self.WEIGHTS["issues"]
                )

                grade = self._score_to_grade(overall_score)

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
                    issues_score=issues_score,
                    metrics=metrics,
                    findings_summary=findings_summary,
                    findings=findings,
                    dedup_stats=getattr(self, 'dedup_stats', None),
                    root_cause_summary=getattr(self, 'root_cause_summary', None),
                    voting_stats=getattr(self, 'voting_stats', None),
                    insights=getattr(self, 'codebase_insights', None),
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

    def _run_detectors(self, progress_callback=None) -> List[Finding]:
        """Run all registered detectors with two-phase parallel execution.

        REPO-217: Implements parallel execution for improved performance.

        Phase 1: Run all independent detectors (needs_previous_findings=False)
                 in parallel using ThreadPoolExecutor.
        Phase 2: Run dependent detectors (needs_previous_findings=True)
                 sequentially, passing accumulated findings.

        Args:
            progress_callback: Optional callback(detector_name, current, total, status)

        Returns:
            Combined list of all findings
        """
        # Classify detectors based on whether they need previous findings
        independent_detectors = [d for d in self.detectors if not d.needs_previous_findings]
        dependent_detectors = [d for d in self.detectors if d.needs_previous_findings]

        total_detectors = len(self.detectors)

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
            phase1_findings = self._run_detectors_parallel(
                independent_detectors,
                progress_callback=progress_callback,
                start_index=0,
                total=total_detectors
            )
        else:
            mode = "sequentially" if not self.parallel else "sequentially (single detector)"
            logger.info(f"Phase 1: Running {len(independent_detectors)} independent detectors {mode}")
            phase1_findings = self._run_detectors_sequential(
                independent_detectors,
                progress_callback=progress_callback,
                start_index=0,
                total=total_detectors
            )

        all_findings.extend(phase1_findings)
        logger.info(f"Phase 1 complete: {len(phase1_findings)} findings from independent detectors")

        # Phase 2: Run dependent detectors (can also be parallel since they depend
        # on Phase 1 detectors, not on each other)
        if dependent_detectors:
            phase2_start = len(independent_detectors)
            if self.parallel and len(dependent_detectors) > 1:
                logger.info(
                    f"Phase 2: Running {len(dependent_detectors)} dependent detectors "
                    f"in parallel (workers={self.max_workers})"
                )
                phase2_findings = self._run_detectors_parallel_with_findings(
                    dependent_detectors,
                    previous_findings=all_findings,
                    progress_callback=progress_callback,
                    start_index=phase2_start,
                    total=total_detectors
                )
            else:
                logger.info(f"Phase 2: Running {len(dependent_detectors)} dependent detectors sequentially")
                phase2_findings = self._run_detectors_sequential(
                    dependent_detectors,
                    previous_findings=all_findings,
                    progress_callback=progress_callback,
                    start_index=phase2_start,
                    total=total_detectors
                )
            all_findings.extend(phase2_findings)
            logger.info(f"Phase 2 complete: {len(phase2_findings)} findings from dependent detectors")

        logger.info("All detectors complete", extra={
            "total_findings": len(all_findings),
            "detectors_run": len(self.detectors),
            "parallel_mode": self.parallel
        })

        return all_findings

    def _run_detectors_parallel(
        self,
        detectors: list,
        progress_callback=None,
        start_index: int = 0,
        total: int = 0
    ) -> List[Finding]:
        """Run detectors in parallel using ThreadPoolExecutor.

        Args:
            detectors: List of detector instances to run
            progress_callback: Optional callback(detector_name, current, total, status)
            start_index: Starting index for progress tracking
            total: Total number of detectors (for progress percentage)

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []
        completed_count = 0
        # Thread-safe lock for findings aggregation and counter
        findings_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all detectors
            future_to_detector = {
                executor.submit(self._run_single_detector, d): d
                for d in detectors
            }

            # Track which detectors are starting
            if progress_callback:
                for detector in detectors:
                    detector_name = detector.__class__.__name__
                    # Remove "Detector" suffix for cleaner display
                    display_name = detector_name.replace("Detector", "")
                    progress_callback(display_name, start_index, total, "starting")

            # Collect results as they complete
            for future in as_completed(future_to_detector):
                detector = future_to_detector[future]
                detector_name = detector.__class__.__name__
                display_name = detector_name.replace("Detector", "")

                try:
                    findings = future.result()
                    # Thread-safe update of findings and counter
                    with findings_lock:
                        all_findings.extend(findings)
                        completed_count += 1
                        current_count = completed_count

                    if progress_callback:
                        progress_callback(
                            display_name,
                            start_index + current_count,
                            total,
                            "completed"
                        )
                except Exception as e:
                    # Thread-safe update of counter on failure
                    with findings_lock:
                        completed_count += 1
                        current_count = completed_count
                    logger.error(
                        f"Detector failed in parallel execution: {detector_name}",
                        extra={"error": str(e)},
                        exc_info=True
                    )
                    if progress_callback:
                        progress_callback(display_name, start_index + current_count, total, "failed")

        return all_findings

    def _run_detectors_parallel_with_findings(
        self,
        detectors: list,
        previous_findings: List[Finding],
        progress_callback=None,
        start_index: int = 0,
        total: int = 0
    ) -> List[Finding]:
        """Run dependent detectors in parallel, passing previous_findings to each.

        Since dependent detectors only depend on Phase 1 findings (not on each other),
        they can safely run in parallel with a shared read-only view of previous_findings.

        Args:
            detectors: List of detector instances that need previous_findings
            previous_findings: Findings from Phase 1 (read-only, shared across threads)
            progress_callback: Optional callback(detector_name, current, total, status)
            start_index: Starting index for progress tracking
            total: Total number of detectors (for progress percentage)

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []
        completed_count = 0
        # Thread-safe lock for findings aggregation and counter
        findings_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all detectors with previous_findings
            future_to_detector = {
                executor.submit(
                    self._run_single_detector_with_findings, d, previous_findings
                ): d
                for d in detectors
            }

            # Track which detectors are starting
            if progress_callback:
                for detector in detectors:
                    detector_name = detector.__class__.__name__
                    display_name = detector_name.replace("Detector", "")
                    progress_callback(display_name, start_index, total, "starting")

            # Collect results as they complete
            for future in as_completed(future_to_detector):
                detector = future_to_detector[future]
                detector_name = detector.__class__.__name__
                display_name = detector_name.replace("Detector", "")

                try:
                    findings = future.result()
                    # Thread-safe update of findings and counter
                    with findings_lock:
                        all_findings.extend(findings)
                        completed_count += 1
                        current_count = completed_count

                    if progress_callback:
                        progress_callback(
                            display_name,
                            start_index + current_count,
                            total,
                            "completed"
                        )
                except Exception as e:
                    # Thread-safe update of counter on failure
                    with findings_lock:
                        completed_count += 1
                        current_count = completed_count
                    logger.error(
                        f"Detector failed in parallel execution: {detector_name}",
                        extra={"error": str(e)},
                        exc_info=True
                    )
                    if progress_callback:
                        progress_callback(display_name, start_index + current_count, total, "failed")

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
        previous_findings: List[Finding] = None,
        progress_callback=None,
        start_index: int = 0,
        total: int = 0
    ) -> List[Finding]:
        """Run detectors sequentially with optional previous findings.

        Args:
            detectors: List of detector instances to run
            previous_findings: Optional findings to pass to detectors that need them
            progress_callback: Optional callback(detector_name, current, total, status)
            start_index: Starting index for progress tracking
            total: Total number of detectors (for progress percentage)

        Returns:
            Combined list of findings from all detectors
        """
        all_findings: List[Finding] = []
        accumulated_findings = list(previous_findings) if previous_findings else []

        for idx, detector in enumerate(detectors):
            detector_name = detector.__class__.__name__
            display_name = detector_name.replace("Detector", "")
            current_index = start_index + idx

            # Report detector starting
            if progress_callback:
                progress_callback(display_name, current_index, total, "starting")

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

                    # Report detector completed
                    if progress_callback:
                        progress_callback(display_name, current_index + 1, total, "completed")

                except Exception as e:
                    duration = time.time() - start_time
                    logger.error(
                        f"Detector failed: {detector_name}",
                        extra={"error": str(e), "duration_seconds": round(duration, 3)},
                        exc_info=True
                    )
                    # Report detector failed
                    if progress_callback:
                        progress_callback(display_name, current_index + 1, total, "failed")

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

    def _score_issues(self, summary: FindingsSummary) -> float:
        """Score based on finding severity counts.

        Penalizes based on the number and severity of findings detected.
        This ensures findings directly impact the health score.

        Penalties per finding:
        - Critical: 1.5 points each (max 25 point penalty)
        - High: 0.15 points each (max 25 point penalty)
        - Medium: 0.05 points each (max 15 point penalty)
        - Low: 0.01 points each (max 10 point penalty)

        Args:
            summary: FindingsSummary with severity counts

        Returns:
            Score from 0-100 (100 = no issues, 0 = many severe issues)
        """
        score = 100.0

        # Critical findings: heavy penalty, max 25 points
        critical_penalty = min(25, summary.critical * 1.5)
        score -= critical_penalty

        # High findings: moderate penalty, max 25 points
        high_penalty = min(25, summary.high * 0.15)
        score -= high_penalty

        # Medium findings: small penalty, max 15 points
        medium_penalty = min(15, summary.medium * 0.05)
        score -= medium_penalty

        # Low findings: minimal penalty, max 10 points
        low_penalty = min(10, summary.low * 0.01)
        score -= low_penalty

        return max(0, score)  # Ensure score doesn't go negative

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
