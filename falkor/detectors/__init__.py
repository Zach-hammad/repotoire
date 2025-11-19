"""Code smell detectors and analysis engine."""

from falkor.detectors.engine import AnalysisEngine
from falkor.detectors.base import CodeSmellDetector
from falkor.detectors.circular_dependency import CircularDependencyDetector
from falkor.detectors.dead_code import DeadCodeDetector
from falkor.detectors.god_class import GodClassDetector

__all__ = [
    "AnalysisEngine",
    "CodeSmellDetector",
    "CircularDependencyDetector",
    "DeadCodeDetector",
    "GodClassDetector",
]
