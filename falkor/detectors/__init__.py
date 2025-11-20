"""Code smell detectors and analysis engine."""

from falkor.detectors.engine import AnalysisEngine
from falkor.detectors.base import CodeSmellDetector
from falkor.detectors.circular_dependency import CircularDependencyDetector
from falkor.detectors.dead_code import DeadCodeDetector
from falkor.detectors.god_class import GodClassDetector
from falkor.detectors.architectural_bottleneck import ArchitecturalBottleneckDetector

# Graph-unique detectors (FAL-115: Graph-Enhanced Linting Strategy)
from falkor.detectors.feature_envy import FeatureEnvyDetector
from falkor.detectors.shotgun_surgery import ShotgunSurgeryDetector
from falkor.detectors.middle_man import MiddleManDetector
from falkor.detectors.inappropriate_intimacy import InappropriateIntimacyDetector
from falkor.detectors.truly_unused_imports import TrulyUnusedImportsDetector

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
]
