"""Code smell detectors and analysis engine.

Lazy-loading module to avoid importing all detectors at import time.
AnalysisEngine loads detectors dynamically when needed.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repotoire.detectors.base import CodeSmellDetector
    from repotoire.detectors.engine import AnalysisEngine

# Registry of detector names to module paths for lazy loading
_DETECTOR_REGISTRY = {
    # Core infrastructure
    "AnalysisEngine": ("repotoire.detectors.engine", "AnalysisEngine"),
    "CodeSmellDetector": ("repotoire.detectors.base", "CodeSmellDetector"),
    "IncrementalCache": ("repotoire.detectors.incremental_cache", "IncrementalCache"),
    # AI-generated code detectors
    "AIDuplicateBlockDetector": ("repotoire.detectors.ai_duplicate_block", "AIDuplicateBlockDetector"),
    "AIMissingTestsDetector": ("repotoire.detectors.ai_missing_tests", "AIMissingTestsDetector"),
    # Graph detectors
    "AIBoilerplateDetector": ("repotoire.detectors.ai_boilerplate", "AIBoilerplateDetector"),
    "ArchitecturalBottleneckDetector": ("repotoire.detectors.architectural_bottleneck", "ArchitecturalBottleneckDetector"),
    "ArgumentMismatchDetector": ("repotoire.detectors.argument_mismatch_detector", "ArgumentMismatchDetector"),
    "CircularDependencyDetector": ("repotoire.detectors.circular_dependency", "CircularDependencyDetector"),
    "CoreUtilityDetector": ("repotoire.detectors.core_utility", "CoreUtilityDetector"),
    "DeadCodeDetector": ("repotoire.detectors.dead_code", "DeadCodeDetector"),
    "DegreeCentralityDetector": ("repotoire.detectors.degree_centrality", "DegreeCentralityDetector"),
    "FeatureEnvyDetector": ("repotoire.detectors.feature_envy", "FeatureEnvyDetector"),
    "GodClassDetector": ("repotoire.detectors.god_class", "GodClassDetector"),
    "InappropriateIntimacyDetector": ("repotoire.detectors.inappropriate_intimacy", "InappropriateIntimacyDetector"),
    "InfluentialCodeDetector": ("repotoire.detectors.influential_code", "InfluentialCodeDetector"),
    "LazyClassDetector": ("repotoire.detectors.lazy_class", "LazyClassDetector"),
    "MiddleManDetector": ("repotoire.detectors.middle_man", "MiddleManDetector"),
    "ModuleCohesionDetector": ("repotoire.detectors.module_cohesion", "ModuleCohesionDetector"),
    "RefusedBequestDetector": ("repotoire.detectors.refused_bequest", "RefusedBequestDetector"),
    "ShotgunSurgeryDetector": ("repotoire.detectors.shotgun_surgery", "ShotgunSurgeryDetector"),
    "TrulyUnusedImportsDetector": ("repotoire.detectors.truly_unused_imports", "TrulyUnusedImportsDetector"),
    "UnusedImportsDetector": ("repotoire.detectors.unused_imports_detector", "UnusedImportsDetector"),
    # Hybrid detectors (external tool + graph)
    "BanditDetector": ("repotoire.detectors.bandit_detector", "BanditDetector"),
    "ESLintDetector": ("repotoire.detectors.eslint_detector", "ESLintDetector"),
    "JscpdDetector": ("repotoire.detectors.jscpd_detector", "JscpdDetector"),
    "MypyDetector": ("repotoire.detectors.mypy_detector", "MypyDetector"),
    "NpmAuditDetector": ("repotoire.detectors.npm_audit_detector", "NpmAuditDetector"),
    "PylintDetector": ("repotoire.detectors.pylint_detector", "PylintDetector"),
    "RadonDetector": ("repotoire.detectors.radon_detector", "RadonDetector"),
    "RuffImportDetector": ("repotoire.detectors.ruff_import_detector", "RuffImportDetector"),
    "RuffLintDetector": ("repotoire.detectors.ruff_lint_detector", "RuffLintDetector"),
    "SATDDetector": ("repotoire.detectors.satd_detector", "SATDDetector"),
    "SemgrepDetector": ("repotoire.detectors.semgrep_detector", "SemgrepDetector"),
    "TscDetector": ("repotoire.detectors.tsc_detector", "TscDetector"),
    "VultureDetector": ("repotoire.detectors.vulture_detector", "VultureDetector"),
    # AI pattern detectors
    "AIComplexitySpikeDetector": ("repotoire.detectors.ai_complexity_spike", "AIComplexitySpikeDetector"),
    # Rust-based detectors
    "InfiniteLoopDetector": ("repotoire.detectors.infinite_loop_detector", "InfiniteLoopDetector"),
    "CallChainDepthDetector": ("repotoire.detectors.rust_graph_detectors", "CallChainDepthDetector"),
    "ChangeCouplingDetector": ("repotoire.detectors.rust_graph_detectors", "ChangeCouplingDetector"),
    "HubDependencyDetector": ("repotoire.detectors.rust_graph_detectors", "HubDependencyDetector"),
    "LayeredArchitectureDetector": ("repotoire.detectors.rust_graph_detectors", "LayeredArchitectureDetector"),
    "PackageStabilityDetector": ("repotoire.detectors.rust_graph_detectors", "PackageStabilityDetector"),
    "TechnicalDebtHotspotDetector": ("repotoire.detectors.rust_graph_detectors", "TechnicalDebtHotspotDetector"),
    # AI code quality detectors
    "AIChurnDetector": ("repotoire.detectors.ai_churn_detector", "AIChurnDetector"),
    "AINamingPatternDetector": ("repotoire.detectors.ai_naming_pattern", "AINamingPatternDetector"),
    # Security detectors
    "SQLInjectionDetector": ("repotoire.detectors.sql_injection_detector", "SQLInjectionDetector"),
    "PickleDeserializationDetector": ("repotoire.detectors.pickle_detector", "PickleDeserializationDetector"),
    "TaintDetector": ("repotoire.detectors.taint_detector", "TaintDetector"),
}


def __getattr__(name: str):
    """Lazy import detectors on first access."""
    if name in _DETECTOR_REGISTRY:
        module_path, class_name = _DETECTOR_REGISTRY[name]
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    raise AttributeError(f"module 'repotoire.detectors' has no attribute {name!r}")


def get_all_detector_classes():
    """Get all available detector classes (loads them lazily).
    
    Returns:
        dict: Mapping of detector names to their classes
    """
    result = {}
    for name in _DETECTOR_REGISTRY:
        if name not in ("AnalysisEngine", "CodeSmellDetector"):
            result[name] = __getattr__(name)
    return result


__all__ = [
    "AnalysisEngine",
    "CodeSmellDetector",
    "IncrementalCache",
    "get_all_detector_classes",
    # All detector names from registry
    *[k for k in _DETECTOR_REGISTRY.keys() if k not in ("AnalysisEngine", "CodeSmellDetector", "IncrementalCache")],
]
