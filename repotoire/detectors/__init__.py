"""Code smell detectors and analysis engine."""

from repotoire.detectors.engine import AnalysisEngine
from repotoire.detectors.base import CodeSmellDetector
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

# Graph-unique detectors (FAL-115: Graph-Enhanced Linting Strategy)
from repotoire.detectors.feature_envy import FeatureEnvyDetector
from repotoire.detectors.shotgun_surgery import ShotgunSurgeryDetector
from repotoire.detectors.middle_man import MiddleManDetector
from repotoire.detectors.inappropriate_intimacy import InappropriateIntimacyDetector
from repotoire.detectors.truly_unused_imports import TrulyUnusedImportsDetector

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

__all__ = [
    "AnalysisEngine",
    "CodeSmellDetector",
    "CircularDependencyDetector",
    "DeadCodeDetector",
    "GodClassDetector",
    "ArchitecturalBottleneckDetector",
    # GDS-based graph detectors
    "ModuleCohesionDetector",
    "CoreUtilityDetector",
    # GDS-based detectors (REPO-169, REPO-170, REPO-171)
    "InfluentialCodeDetector",
    "DegreeCentralityDetector",
    # Graph-unique detectors
    "FeatureEnvyDetector",
    "ShotgunSurgeryDetector",
    "MiddleManDetector",
    "InappropriateIntimacyDetector",
    "TrulyUnusedImportsDetector",
    # Design smell detectors
    "LazyClassDetector",
    "RefusedBequestDetector",
    # Hybrid detectors
    "RuffImportDetector",
    "RuffLintDetector",
    "MypyDetector",
    "PylintDetector",
    "BanditDetector",
    "RadonDetector",
    "JscpdDetector",
    "VultureDetector",
    "SemgrepDetector",
    "SATDDetector",
]
