"""Data models for Falkor."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class Severity(str, Enum):
    """Finding severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class NodeType(str, Enum):
    """Graph node types."""
    FILE = "File"
    MODULE = "Module"
    CLASS = "Class"
    FUNCTION = "Function"
    CONCEPT = "Concept"
    IMPORT = "Import"


class RelationshipType(str, Enum):
    """Graph relationship types."""
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    CONTAINS = "CONTAINS"
    INHERITS = "INHERITS"
    USES = "USES"
    OVERRIDES = "OVERRIDES"
    DECORATES = "DECORATES"
    DEFINES = "DEFINES"
    DESCRIBES = "DESCRIBES"
    MENTIONS = "MENTIONS"
    PARENT_OF = "PARENT_OF"
    MODIFIED = "MODIFIED"
    VERSION_AT = "VERSION_AT"
    RELATED_TO = "RELATED_TO"


@dataclass
class Entity:
    """Base entity extracted from code."""
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    node_type: Optional[NodeType] = None
    docstring: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class FileEntity(Entity):
    """File node."""
    language: str = "python"
    loc: int = 0
    hash: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.FILE


@dataclass
class ModuleEntity(Entity):
    """Module/Package node."""
    is_external: bool = True  # True if from external package, False if in codebase
    package: Optional[str] = None  # Parent package (e.g., "os" for "os.path")

    def __post_init__(self) -> None:
        self.node_type = NodeType.MODULE


@dataclass
class ClassEntity(Entity):
    """Class node."""
    is_abstract: bool = False
    complexity: int = 0

    def __post_init__(self) -> None:
        self.node_type = NodeType.CLASS


@dataclass
class FunctionEntity(Entity):
    """Function/method node."""
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    complexity: int = 0
    is_async: bool = False

    def __post_init__(self) -> None:
        self.node_type = NodeType.FUNCTION


@dataclass
class Concept:
    """Semantic concept extracted from code."""
    name: str
    description: str
    confidence: float = 0.5
    embedding: Optional[List[float]] = None


@dataclass
class Relationship:
    """Graph relationship between entities."""
    source_id: str
    target_id: str
    rel_type: RelationshipType
    properties: Dict = field(default_factory=dict)


@dataclass
class Finding:
    """Code smell or issue detected by analysis."""
    id: str
    detector: str
    severity: Severity
    title: str
    description: str
    affected_nodes: List[str]
    affected_files: List[str]
    graph_context: Dict = field(default_factory=dict)
    suggested_fix: Optional[str] = None
    estimated_effort: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class FixSuggestion:
    """AI-generated refactoring suggestion."""
    explanation: str
    approach: str
    files_to_modify: List[str]
    estimated_effort: str
    code_diff: Optional[str] = None
    confidence: float = 0.0


@dataclass
class MetricsBreakdown:
    """Detailed code health metrics."""
    # Graph structure metrics (40% weight)
    modularity: float = 0.0
    avg_coupling: float = 0.0
    circular_dependencies: int = 0
    bottleneck_count: int = 0

    # Code quality metrics (30% weight)
    dead_code_percentage: float = 0.0
    duplication_percentage: float = 0.0
    god_class_count: int = 0

    # Architecture metrics (30% weight)
    layer_violations: int = 0
    boundary_violations: int = 0
    abstraction_ratio: float = 0.0

    # Summary stats
    total_files: int = 0
    total_classes: int = 0
    total_functions: int = 0
    total_loc: int = 0


@dataclass
class FindingsSummary:
    """Summary of findings by severity."""
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        """Total number of findings."""
        return self.critical + self.high + self.medium + self.low + self.info


@dataclass
class CodebaseHealth:
    """Overall codebase health report."""
    grade: str  # A, B, C, D, F
    overall_score: float  # 0-100

    # Category scores
    structure_score: float
    quality_score: float
    architecture_score: float

    # Detailed metrics
    metrics: MetricsBreakdown
    findings_summary: FindingsSummary

    # Detailed findings list
    findings: List[Finding] = field(default_factory=list)

    # Timestamp
    analyzed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "grade": self.grade,
            "overall_score": self.overall_score,
            "structure_score": self.structure_score,
            "quality_score": self.quality_score,
            "architecture_score": self.architecture_score,
            "findings_summary": {
                "critical": self.findings_summary.critical,
                "high": self.findings_summary.high,
                "medium": self.findings_summary.medium,
                "low": self.findings_summary.low,
                "total": self.findings_summary.total,
            },
            "findings": [
                {
                    "id": f.id,
                    "detector": f.detector,
                    "severity": f.severity.value,
                    "title": f.title,
                    "description": f.description,
                    "affected_files": f.affected_files,
                    "affected_nodes": f.affected_nodes,
                    "graph_context": f.graph_context,
                    "suggested_fix": f.suggested_fix,
                    "estimated_effort": f.estimated_effort,
                }
                for f in self.findings
            ],
            "analyzed_at": self.analyzed_at.isoformat(),
        }
