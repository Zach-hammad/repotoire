"""Data models for Falkor.

This module defines the core data structures used throughout Falkor:
- Entity hierarchy: Represents code elements (files, classes, functions, etc.)
- Relationships: Connections between entities in the knowledge graph
- Findings: Code smells and issues detected by analyzers
- Health metrics: Codebase health scoring and metrics

All models use dataclasses for immutability and type safety.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class Severity(str, Enum):
    """Finding severity levels ordered by impact.

    Used to prioritize findings from detectors. Higher severity issues
    should be addressed first.

    Attributes:
        CRITICAL: System-critical issues requiring immediate attention
        HIGH: Significant issues affecting code quality or maintainability
        MEDIUM: Moderate issues that should be addressed soon
        LOW: Minor issues or code style violations
        INFO: Informational findings for awareness

    Example:
        >>> finding.severity == Severity.CRITICAL
        True
        >>> Severity.HIGH.value
        'high'
    """
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class NodeType(str, Enum):
    """Types of nodes in the knowledge graph.

    Each node type represents a different code element or concept.
    Nodes are connected via relationships to form the complete graph.

    Attributes:
        FILE: Source code file
        MODULE: Python module or package (import target)
        CLASS: Class definition
        FUNCTION: Function or method definition
        CONCEPT: Semantic concept extracted by NLP/AI
        IMPORT: Import statement
        VARIABLE: Variable or parameter
        ATTRIBUTE: Class or instance attribute

    Example:
        >>> entity.node_type == NodeType.CLASS
        True
        >>> NodeType.FUNCTION.value
        'Function'
    """
    FILE = "File"
    MODULE = "Module"
    CLASS = "Class"
    FUNCTION = "Function"
    CONCEPT = "Concept"
    IMPORT = "Import"
    VARIABLE = "Variable"
    ATTRIBUTE = "Attribute"


class RelationshipType(str, Enum):
    """Types of relationships between nodes in the knowledge graph.

    Relationships capture how code elements interact and depend on each other.
    They form the edges of the knowledge graph.

    Attributes:
        IMPORTS: File or module imports another module
        CALLS: Function calls another function
        CONTAINS: File contains a class/function, or class contains a method
        INHERITS: Class inherits from another class
        USES: Function uses a variable/attribute
        OVERRIDES: Method overrides a parent class method
        DECORATES: Decorator applied to a function or class
        DEFINES: Entity defines a concept or type
        DESCRIBES: Documentation describes an entity
        MENTIONS: Documentation mentions an entity
        PARENT_OF: Parent-child relationship (e.g., class to method)
        MODIFIED: Entity was modified in a commit
        VERSION_AT: Entity exists at a specific version
        RELATED_TO: General semantic relationship

    Example:
        >>> rel.rel_type == RelationshipType.IMPORTS
        True
        >>> RelationshipType.CALLS.value
        'CALLS'
    """
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
    """Base entity extracted from code.

    Represents a code element (file, class, function, etc.) in the knowledge graph.
    All specific entity types (FileEntity, ClassEntity, etc.) inherit from this base.

    Attributes:
        name: Simple name of the entity (e.g., "my_function")
        qualified_name: Fully qualified unique name (e.g., "myfile.py::MyClass.my_function")
        file_path: Path to source file containing this entity
        line_start: Starting line number in the source file
        line_end: Ending line number in the source file
        node_type: Type of node in the graph (File, Class, Function, etc.)
        docstring: Extracted docstring or documentation
        metadata: Additional arbitrary metadata

    Example:
        >>> entity = Entity(
        ...     name="my_function",
        ...     qualified_name="module.py::my_function",
        ...     file_path="src/module.py",
        ...     line_start=10,
        ...     line_end=25,
        ...     node_type=NodeType.FUNCTION,
        ...     docstring="This function does something useful."
        ... )
    """
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
    """Source file node in the knowledge graph.

    Represents a single source code file with metadata about its language,
    size, content hash, and exported symbols.

    Attributes:
        language: Programming language (e.g., "python", "javascript")
        loc: Lines of code (non-blank, non-comment)
        hash: MD5 hash of file contents for change detection
        exports: List of symbols exported via __all__ or similar

    Example:
        >>> file = FileEntity(
        ...     name="utils.py",
        ...     qualified_name="src/utils.py",
        ...     file_path="src/utils.py",
        ...     line_start=1,
        ...     line_end=150,
        ...     language="python",
        ...     loc=120,
        ...     hash="a1b2c3d4e5f6",
        ...     exports=["helper_function", "UtilityClass"]
        ... )
    """
    language: str = "python"
    loc: int = 0
    hash: str = ""
    exports: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.FILE


@dataclass
class ModuleEntity(Entity):
    """Module or package node representing an import target.

    Represents a module that can be imported. Can be either external
    (from a package) or internal (from the codebase).

    Attributes:
        is_external: True if from external package, False if in codebase
        package: Parent package name (e.g., "os" for "os.path")
        is_dynamic_import: True if imported via importlib or __import__

    Example:
        >>> module = ModuleEntity(
        ...     name="path",
        ...     qualified_name="os.path",
        ...     file_path="src/main.py",  # File that imports it
        ...     line_start=5,
        ...     line_end=5,
        ...     is_external=True,
        ...     package="os"
        ... )
    """
    is_external: bool = True  # True if from external package, False if in codebase
    package: Optional[str] = None  # Parent package (e.g., "os" for "os.path")
    is_dynamic_import: bool = False  # True if imported via importlib or __import__

    def __post_init__(self) -> None:
        self.node_type = NodeType.MODULE


@dataclass
class ClassEntity(Entity):
    """Class definition node.

    Represents a class with metadata about its complexity, decorators,
    and whether it's abstract.

    Attributes:
        is_abstract: True if class inherits from ABC or has abstract methods
        complexity: Cyclomatic complexity of all methods combined
        decorators: List of decorator names applied to the class

    Example:
        >>> cls = ClassEntity(
        ...     name="MyClass",
        ...     qualified_name="module.py::MyClass",
        ...     file_path="src/module.py",
        ...     line_start=10,
        ...     line_end=50,
        ...     is_abstract=False,
        ...     complexity=25,
        ...     decorators=["dataclass"]
        ... )
    """
    is_abstract: bool = False
    complexity: int = 0
    decorators: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.CLASS


@dataclass
class FunctionEntity(Entity):
    """Function or method definition node.

    Represents a function or method with detailed type information,
    complexity metrics, and decorators.

    Attributes:
        parameters: List of parameter names
        parameter_types: Maps parameter name to type annotation string
        return_type: Return type annotation string
        complexity: Cyclomatic complexity score
        is_async: True if async function or coroutine
        decorators: List of decorator names applied to function

    Example:
        >>> func = FunctionEntity(
        ...     name="calculate_score",
        ...     qualified_name="module.py::calculate_score",
        ...     file_path="src/module.py",
        ...     line_start=10,
        ...     line_end=25,
        ...     parameters=["value", "threshold"],
        ...     parameter_types={"value": "float", "threshold": "float"},
        ...     return_type="int",
        ...     complexity=5,
        ...     is_async=False,
        ...     decorators=["lru_cache"]
        ... )
    """
    parameters: List[str] = field(default_factory=list)
    parameter_types: dict = field(default_factory=dict)  # Maps param name -> type annotation
    return_type: Optional[str] = None
    complexity: int = 0
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.FUNCTION


@dataclass
class VariableEntity(Entity):
    """Local variable or function parameter node.

    Represents a variable with optional type information.

    Attributes:
        variable_type: Type annotation string if available

    Example:
        >>> var = VariableEntity(
        ...     name="count",
        ...     qualified_name="module.py::my_function.count",
        ...     file_path="src/module.py",
        ...     line_start=15,
        ...     line_end=15,
        ...     variable_type="int"
        ... )
    """
    variable_type: Optional[str] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.VARIABLE


@dataclass
class AttributeEntity(Entity):
    """Class or instance attribute node.

    Represents an attribute (field) of a class with type information.

    Attributes:
        attribute_type: Type annotation string if available
        is_class_attribute: True if class attribute, False if instance attribute

    Example:
        >>> attr = AttributeEntity(
        ...     name="count",
        ...     qualified_name="module.py::MyClass.count",
        ...     file_path="src/module.py",
        ...     line_start=12,
        ...     line_end=12,
        ...     attribute_type="int",
        ...     is_class_attribute=True
        ... )
    """
    attribute_type: Optional[str] = None
    is_class_attribute: bool = False

    def __post_init__(self) -> None:
        self.node_type = NodeType.ATTRIBUTE


@dataclass
class Concept:
    """Semantic concept extracted from code using NLP/AI.

    Represents a domain concept or business logic pattern identified
    through semantic analysis of code and documentation.

    Attributes:
        name: Concept name (e.g., "authentication", "payment processing")
        description: Human-readable description of the concept
        confidence: Confidence score 0-1 (higher = more confident)
        embedding: Optional vector embedding for similarity search

    Example:
        >>> concept = Concept(
        ...     name="authentication",
        ...     description="User authentication and authorization logic",
        ...     confidence=0.85,
        ...     embedding=[0.1, 0.2, ...]  # 1536-dim vector
        ... )
    """
    name: str
    description: str
    confidence: float = 0.5
    embedding: Optional[List[float]] = None


@dataclass
class Relationship:
    """Directed relationship between two entities in the graph.

    Represents an edge connecting two nodes with optional properties.

    Attributes:
        source_id: Source entity qualified name or element ID
        target_id: Target entity qualified name or element ID
        rel_type: Type of relationship (IMPORTS, CALLS, etc.)
        properties: Additional metadata about the relationship

    Example:
        >>> rel = Relationship(
        ...     source_id="file.py::function_a",
        ...     target_id="file.py::function_b",
        ...     rel_type=RelationshipType.CALLS,
        ...     properties={"line": 15, "call_type": "direct"}
        ... )
    """
    source_id: str
    target_id: str
    rel_type: RelationshipType
    properties: Dict = field(default_factory=dict)


@dataclass
class Finding:
    """Code smell or issue detected by a detector.

    Represents a specific problem found during analysis with context,
    severity, and suggested fixes.

    Attributes:
        id: Unique identifier (UUID)
        detector: Name of detector that found this issue
        severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW, INFO)
        title: Short title describing the issue
        description: Detailed description with context
        affected_nodes: List of entity qualified names affected
        affected_files: List of file paths affected
        graph_context: Additional graph data about the issue
        suggested_fix: Optional fix suggestion
        estimated_effort: Estimated effort to fix (e.g., "Small (2-4 hours)")
        created_at: When the finding was detected

    Example:
        >>> finding = Finding(
        ...     id="abc-123-def-456",
        ...     detector="CircularDependencyDetector",
        ...     severity=Severity.HIGH,
        ...     title="Circular dependency between 3 files",
        ...     description="Found import cycle: a.py -> b.py -> c.py -> a.py",
        ...     affected_nodes=["src/a.py", "src/b.py", "src/c.py"],
        ...     affected_files=["src/a.py", "src/b.py", "src/c.py"],
        ...     graph_context={"cycle_length": 3},
        ...     suggested_fix="Extract shared interfaces to break the cycle",
        ...     estimated_effort="Medium (1-2 days)"
        ... )
    """
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
    """AI-generated refactoring suggestion with detailed guidance.

    Represents an AI-generated suggestion for fixing a code issue,
    including explanation, approach, and estimated effort.

    Attributes:
        explanation: Why this fix is needed
        approach: Step-by-step approach to implement the fix
        files_to_modify: List of files that need changes
        estimated_effort: Human-readable effort estimate
        code_diff: Optional unified diff showing specific changes
        confidence: AI confidence score 0-1

    Example:
        >>> suggestion = FixSuggestion(
        ...     explanation="The circular dependency makes code hard to test",
        ...     approach="1. Extract interface\n2. Apply dependency injection",
        ...     files_to_modify=["src/a.py", "src/b.py"],
        ...     estimated_effort="Medium (1-2 days)",
        ...     code_diff="--- a/src/a.py\n+++ b/src/a.py\n...",
        ...     confidence=0.85
        ... )
    """
    explanation: str
    approach: str
    files_to_modify: List[str]
    estimated_effort: str
    code_diff: Optional[str] = None
    confidence: float = 0.0


@dataclass
class MetricsBreakdown:
    """Detailed code health metrics across three categories.

    Comprehensive metrics used to calculate the overall health score.
    Metrics are grouped into three weighted categories:
    - Structure (40%): Graph topology and modularity
    - Quality (30%): Code quality and maintainability
    - Architecture (30%): Architectural patterns and design

    Attributes:
        modularity: Community structure score 0-1 (0.3-0.7 is good)
        avg_coupling: Average outgoing dependencies per class
        circular_dependencies: Count of import cycles
        bottleneck_count: Count of highly-connected nodes
        dead_code_percentage: Percentage of unused code 0-1
        duplication_percentage: Percentage of duplicated code 0-1
        god_class_count: Count of overly complex classes
        layer_violations: Count of improper layer crossings
        boundary_violations: Count of boundary violations
        abstraction_ratio: Ratio of abstract to concrete classes 0-1
        total_files: Total source files analyzed
        total_classes: Total class definitions
        total_functions: Total function/method definitions
        total_loc: Total lines of code

    Example:
        >>> metrics = MetricsBreakdown(
        ...     modularity=0.65,
        ...     avg_coupling=3.2,
        ...     circular_dependencies=2,
        ...     bottleneck_count=1,
        ...     dead_code_percentage=0.05,
        ...     duplication_percentage=0.03,
        ...     god_class_count=1,
        ...     layer_violations=0,
        ...     boundary_violations=0,
        ...     abstraction_ratio=0.4,
        ...     total_files=50,
        ...     total_classes=30,
        ...     total_functions=200,
        ...     total_loc=5000
        ... )
    """
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
    """Summary count of findings grouped by severity.

    Provides quick overview of issue distribution for reporting.

    Attributes:
        critical: Count of critical severity findings
        high: Count of high severity findings
        medium: Count of medium severity findings
        low: Count of low severity findings
        info: Count of informational findings

    Example:
        >>> summary = FindingsSummary(
        ...     critical=0,
        ...     high=2,
        ...     medium=5,
        ...     low=10,
        ...     info=3
        ... )
        >>> summary.total
        20
    """
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        """Total number of findings across all severities.

        Returns:
            Sum of all severity counts
        """
        return self.critical + self.high + self.medium + self.low + self.info


@dataclass
class CodebaseHealth:
    """Complete codebase health report with scores and findings.

    The primary output of Falkor's analysis engine. Contains overall
    health grade, category scores, detailed metrics, and all findings.

    Health scores are calculated as:
    - Structure (40% weight): Modularity, coupling, cycles
    - Quality (30% weight): Dead code, duplication, god classes
    - Architecture (30% weight): Layer violations, abstraction ratio

    Letter grades: A (90-100), B (80-89), C (70-79), D (60-69), F (0-59)

    Attributes:
        grade: Letter grade (A, B, C, D, F)
        overall_score: Weighted score 0-100
        structure_score: Structure category score 0-100
        quality_score: Quality category score 0-100
        architecture_score: Architecture category score 0-100
        metrics: Detailed metrics breakdown
        findings_summary: Summary counts by severity
        findings: Full list of all findings
        analyzed_at: Timestamp of analysis

    Example:
        >>> health = CodebaseHealth(
        ...     grade="B",
        ...     overall_score=82.5,
        ...     structure_score=85.0,
        ...     quality_score=78.0,
        ...     architecture_score=85.0,
        ...     metrics=MetricsBreakdown(...),
        ...     findings_summary=FindingsSummary(high=2, medium=5),
        ...     findings=[Finding(...), ...]
        ... )
    """
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
        """Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation suitable for JSON export
        """
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
