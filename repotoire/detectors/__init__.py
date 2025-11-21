"""Code smell detectors and analysis engine."""

from repotoire.detectors.engine import AnalysisEngine
from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.circular_dependency import CircularDependencyDetector
from repotoire.detectors.dead_code import DeadCodeDetector
from repotoire.detectors.god_class import GodClassDetector
from repotoire.detectors.architectural_bottleneck import ArchitecturalBottleneckDetector

# Graph-unique detectors (FAL-115: Graph-Enhanced Linting Strategy)
from repotoire.detectors.feature_envy import FeatureEnvyDetector
from repotoire.detectors.shotgun_surgery import ShotgunSurgeryDetector
from repotoire.detectors.middle_man import MiddleManDetector
from repotoire.detectors.inappropriate_intimacy import InappropriateIntimacyDetector
from repotoire.detectors.truly_unused_imports import TrulyUnusedImportsDetector

# Hybrid detectors (external tool + graph)
from repotoire.detectors.ruff_import_detector import RuffImportDetector
from repotoire.detectors.ruff_lint_detector import RuffLintDetector
from repotoire.detectors.mypy_detector import MypyDetector
from repotoire.detectors.pylint_detector import PylintDetector
from repotoire.detectors.bandit_detector import BanditDetector
from repotoire.detectors.radon_detector import RadonDetector
from repotoire.detectors.jscpd_detector import JscpdDetector

__all__ = [
    "AnalysisEngine",
    "CodeSmellDetector",
    "CircularDependencyDetector",
    "DeadCodeDetector",
    "GodClassDetector",
    "ArchitecturalBottleneckDetector",
    # Graph-unique detectors
    "FeatureEnvyDetector",
    "ShotgunSurgeryDetector",
    "MiddleManDetector",
    "InappropriateIntimacyDetector",
    "TrulyUnusedImportsDetector",
    # Hybrid detectors
    "RuffImportDetector",
    "RuffLintDetector",
    "MypyDetector",
    "PylintDetector",
    "BanditDetector",
    "RadonDetector",
    "JscpdDetector",
]
