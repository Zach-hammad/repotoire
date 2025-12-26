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
from typing import Any, Dict, List, Optional


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
        CLUE: AI-generated semantic summary or insight about code
        SESSION: Git commit snapshot representing code at a point in time
        IMPORT: Import statement
        VARIABLE: Variable or parameter
        ATTRIBUTE: Class or instance attribute
        BUILTIN_FUNCTION: Python builtin function (len, str, print, etc.)
        EXTERNAL_FUNCTION: External/third-party function reference
        EXTERNAL_CLASS: External/third-party class reference

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
    CLUE = "Clue"
    SESSION = "Session"
    IMPORT = "Import"
    VARIABLE = "Variable"
    ATTRIBUTE = "Attribute"
    # External reference types (KG-1 fix: prevents unlabeled nodes)
    BUILTIN_FUNCTION = "BuiltinFunction"
    EXTERNAL_FUNCTION = "ExternalFunction"
    EXTERNAL_CLASS = "ExternalClass"


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
        PARENT_OF: Parent-child relationship (e.g., session to session, class to method)
        CONTAINS_SNAPSHOT: Session contains a snapshot of an entity at a point in time
        MODIFIED: Entity was modified in a commit/session
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
    CONTAINS_SNAPSHOT = "CONTAINS_SNAPSHOT"
    MODIFIED = "MODIFIED"
    VERSION_AT = "VERSION_AT"
    RELATED_TO = "RELATED_TO"


class SecretsPolicy(str, Enum):
    """Policy for handling detected secrets during code ingestion.

    Determines how Falkor responds when secrets are detected in code.
    This is a critical security feature to prevent storing sensitive data
    in Neo4j or sending it to OpenAI APIs.

    Attributes:
        REDACT: Replace secrets with [REDACTED] placeholder (default, safe)
        BLOCK: Refuse to ingest entity containing secrets (strictest)
        WARN: Log warning but continue with original text (risky)
        FAIL: Abort entire ingestion process (for CI/CD enforcement)

    Example:
        >>> policy = SecretsPolicy.REDACT
        >>> if policy == SecretsPolicy.BLOCK:
        ...     raise ValueError("Secret detected, blocking ingestion")
    """
    REDACT = "redact"
    BLOCK = "block"
    WARN = "warn"
    FAIL = "fail"


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
        embedding: Optional vector embedding for semantic search (1536-dim for OpenAI embeddings)
        repo_id: Optional repository UUID for multi-tenant isolation (set by IngestionPipeline)
        repo_slug: Optional repository slug for human-readable identification

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
    embedding: Optional[List[float]] = None  # Vector embedding for RAG (1536-dim for OpenAI)
    repo_id: Optional[str] = None  # Repository UUID for multi-tenant isolation
    repo_slug: Optional[str] = None  # Repository slug (e.g., "owner/repo-name")


@dataclass
class FileEntity(Entity):
    """Source file node in the knowledge graph.

    Represents a single source code file with metadata about its language,
    size, content hash, and exported symbols.

    Attributes:
        language: Programming language (e.g., "python", "javascript")
        loc: Lines of code (non-blank, non-comment)
        hash: MD5 hash of file contents for change detection
        last_modified: Last modification timestamp for incremental ingestion
        exports: List of symbols exported via __all__ or similar
        module_path: Python module path (e.g., "falkor.parsers.python_parser")
        is_test: True if this is a test file

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
        ...     last_modified=datetime.now(),
        ...     exports=["helper_function", "UtilityClass"],
        ...     module_path="src.utils",
        ...     is_test=False
        ... )
    """
    language: str = "python"
    loc: int = 0
    hash: str = ""
    last_modified: Optional[datetime] = None
    exports: List[str] = field(default_factory=list)
    module_path: Optional[str] = None
    is_test: bool = False

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
        is_dataclass: True if @dataclass decorator applied
        is_exception: True if inherits from Exception
        nesting_level: Depth of nesting (0 for top-level classes)

    Example:
        >>> cls = ClassEntity(
        ...     name="MyClass",
        ...     qualified_name="module.py::MyClass",
        ...     file_path="src/module.py",
        ...     line_start=10,
        ...     line_end=50,
        ...     is_abstract=False,
        ...     complexity=25,
        ...     decorators=["dataclass"],
        ...     is_dataclass=True,
        ...     is_exception=False,
        ...     nesting_level=0
        ... )
    """
    is_abstract: bool = False
    complexity: int = 0
    decorators: List[str] = field(default_factory=list)
    is_dataclass: bool = False
    is_exception: bool = False
    nesting_level: int = 0

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
        is_method: True if this is a class method (not a standalone function)
        is_static: True if @staticmethod decorator present
        is_classmethod: True if @classmethod decorator present
        is_property: True if @property decorator present
        has_return: True if function has return statement
        has_yield: True if function has yield statement (generator)

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
        ...     decorators=["lru_cache"],
        ...     is_method=False,
        ...     is_static=False,
        ...     is_classmethod=False,
        ...     is_property=False,
        ...     has_return=True,
        ...     has_yield=False
        ... )
    """
    parameters: List[str] = field(default_factory=list)
    parameter_types: dict = field(default_factory=dict)  # Maps param name -> type annotation
    return_type: Optional[str] = None
    complexity: int = 0
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)
    is_method: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    has_return: bool = False
    has_yield: bool = False

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
class ClueEntity(Entity):
    """AI-generated semantic summary or insight about code.

    Represents a semantic "clue" that helps understand code intent, purpose,
    or relationships. Generated by NLP models (spaCy) or LLMs (GPT-4).

    Attributes:
        clue_type: Type of clue (summary, purpose, concept, insight, pattern)
        summary: Brief summary text of the clue
        detailed_explanation: Optional longer explanation
        confidence: Confidence score 0.0-1.0 (higher = more confident)
        embedding: Optional vector embedding for semantic search
        generated_by: Method used to generate (spacy, gpt-4, etc.)
        generated_at: Timestamp when clue was generated
        keywords: List of extracted keywords
        target_entity: Qualified name of the entity this clue describes

    Example:
        >>> clue = ClueEntity(
        ...     name="authentication_clue",
        ...     qualified_name="clue::auth_module::purpose",
        ...     file_path="src/auth.py",
        ...     line_start=1,
        ...     line_end=50,
        ...     clue_type="purpose",
        ...     summary="Handles user authentication and session management",
        ...     detailed_explanation="This module implements JWT-based auth...",
        ...     confidence=0.85,
        ...     generated_by="spacy",
        ...     generated_at=datetime.now(),
        ...     keywords=["authentication", "jwt", "session"],
        ...     target_entity="src/auth.py::AuthModule"
        ... )
    """
    clue_type: str = "summary"  # summary, purpose, concept, insight, pattern
    summary: str = ""
    detailed_explanation: Optional[str] = None
    confidence: float = 0.0  # 0.0-1.0
    embedding: Optional[List[float]] = None
    generated_by: str = "spacy"  # spacy, gpt-4, gpt-4o, etc.
    generated_at: Optional[datetime] = None
    keywords: List[str] = field(default_factory=list)
    target_entity: Optional[str] = None  # Qualified name of entity this describes

    def __post_init__(self) -> None:
        self.node_type = NodeType.CLUE
        if self.generated_at is None:
            self.generated_at = datetime.utcnow()


@dataclass
class SessionEntity(Entity):
    """Git commit snapshot representing code at a specific point in time.

    Represents a temporal snapshot of the codebase at a specific commit.
    Used to track code evolution, detect degradation patterns, and measure
    technical debt velocity over time.

    Attributes:
        commit_hash: Git commit SHA hash
        commit_message: Commit message text
        author: Author name
        author_email: Author email address
        committed_at: Timestamp when commit was created
        branch: Branch name where commit exists
        parent_hashes: List of parent commit hashes
        metrics_snapshot: Metrics calculated for this commit snapshot
        files_changed: Number of files changed in this commit
        insertions: Lines added in this commit
        deletions: Lines deleted in this commit

    Example:
        >>> session = SessionEntity(
        ...     name="c5ec541",
        ...     qualified_name="session::c5ec541abcd",
        ...     file_path=".",
        ...     line_start=0,
        ...     line_end=0,
        ...     commit_hash="c5ec541abcd1234567890",
        ...     commit_message="Add new feature",
        ...     author="John Doe",
        ...     author_email="john@example.com",
        ...     committed_at=datetime.now(),
        ...     branch="main",
        ...     parent_hashes=["abc123"],
        ...     files_changed=5,
        ...     insertions=150,
        ...     deletions=30
        ... )
    """
    commit_hash: str = ""
    commit_message: str = ""
    author: str = ""
    author_email: str = ""
    committed_at: Optional[datetime] = None
    branch: str = "main"
    parent_hashes: List[str] = field(default_factory=list)
    metrics_snapshot: Optional[Dict] = None
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    def __post_init__(self) -> None:
        self.node_type = NodeType.SESSION


@dataclass
class GitCommit:
    """Git commit information (data transfer object).

    Not a graph entity, just a DTO for passing commit data from
    GitRepository to the ingestion pipeline.

    Attributes:
        hash: Full commit SHA hash
        short_hash: Short version of commit hash (first 7 chars)
        message: Commit message
        author: Author name
        author_email: Author email address
        committed_at: Timestamp when committed
        parent_hashes: List of parent commit hashes
        branch: Branch name
        changed_files: List of file paths changed in this commit
        stats: Dict with insertions/deletions/files_changed counts

    Example:
        >>> commit = GitCommit(
        ...     hash="c5ec541abcd1234567890",
        ...     short_hash="c5ec541",
        ...     message="Add new feature",
        ...     author="John Doe",
        ...     author_email="john@example.com",
        ...     committed_at=datetime.now(),
        ...     parent_hashes=["abc123"],
        ...     branch="main",
        ...     changed_files=["src/file.py"],
        ...     stats={"insertions": 150, "deletions": 30, "files_changed": 5}
        ... )
    """
    hash: str
    short_hash: str
    message: str
    author: str
    author_email: str
    committed_at: datetime
    parent_hashes: List[str]
    branch: str = "main"
    changed_files: List[str] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)


@dataclass
class MetricTrend:
    """Temporal trend analysis for a specific metric.

    Represents how a metric changes over time with statistical analysis
    of the trend direction and velocity.

    Attributes:
        metric_name: Name of the metric being tracked
        values: List of metric values over time
        timestamps: Corresponding timestamps for each value
        trend_direction: Direction of trend (increasing, decreasing, stable)
        change_percentage: Percentage change from first to last value
        velocity: Average rate of change per day
        is_degrading: True if trend indicates degradation

    Example:
        >>> trend = MetricTrend(
        ...     metric_name="modularity",
        ...     values=[0.68, 0.65, 0.60, 0.52],
        ...     timestamps=[datetime(2024, 1, 1), datetime(2024, 2, 1), ...],
        ...     trend_direction="decreasing",
        ...     change_percentage=-23.5,
        ...     velocity=-0.002,
        ...     is_degrading=True
        ... )
    """
    metric_name: str
    values: List[float]
    timestamps: List[datetime]
    trend_direction: str  # "increasing", "decreasing", "stable"
    change_percentage: float
    velocity: float  # Change per day
    is_degrading: bool = False


@dataclass
class CodeHotspot:
    """Code hotspot with high churn and increasing complexity.

    Represents a file that is frequently modified and accumulating
    technical debt, making it a candidate for refactoring.

    Attributes:
        file_path: Path to the file
        churn_count: Number of commits modifying this file
        complexity_velocity: Average complexity increase per commit
        coupling_velocity: Average coupling increase per commit
        risk_score: Combined risk score (churn * complexity_velocity)
        last_modified: Most recent modification timestamp
        top_authors: Authors who modified this file most frequently

    Example:
        >>> hotspot = CodeHotspot(
        ...     file_path="auth/session_manager.py",
        ...     churn_count=23,
        ...     complexity_velocity=1.4,
        ...     coupling_velocity=0.3,
        ...     risk_score=32.2,
        ...     last_modified=datetime.now(),
        ...     top_authors=["Jane Doe", "John Smith"]
        ... )
    """
    file_path: str
    churn_count: int
    complexity_velocity: float
    coupling_velocity: float
    risk_score: float
    last_modified: Optional[datetime] = None
    top_authors: List[str] = field(default_factory=list)


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
class SecretMatch:
    """Detected secret in code or documentation.

    Represents a potential secret (API key, password, token, etc.) detected
    during code ingestion. Used to redact or block sensitive data before
    storing in Neo4j or sending to AI services.

    Attributes:
        secret_type: Type of secret detected (e.g., "AWS Access Key", "Private Key")
        start_index: Character index where secret starts in the text
        end_index: Character index where secret ends in the text
        context: File path and line number where secret was found
        filename: File containing the secret
        line_number: Line number where secret appears
        plugin_name: Name of detect-secrets plugin that found it
        risk_level: Risk level (critical, high, medium, low)
        remediation: Suggested remediation action

    Example:
        >>> match = SecretMatch(
        ...     secret_type="AWS Access Key",
        ...     start_index=45,
        ...     end_index=65,
        ...     context="config.py:12",
        ...     filename="config.py",
        ...     line_number=12,
        ...     plugin_name="AWSKeyDetector",
        ...     risk_level="critical",
        ...     remediation="Rotate key via IAM console"
        ... )
        >>> print(f"Found {match.secret_type} ({match.risk_level}) at {match.context}")
        Found AWS Access Key (critical) at config.py:12
    """
    secret_type: str
    start_index: int
    end_index: int
    context: str
    filename: str
    line_number: int
    plugin_name: str
    risk_level: str = "medium"  # critical, high, medium, low
    remediation: str = ""  # Suggested remediation action


@dataclass
class CollaborationMetadata:
    """Metadata for cross-detector collaboration.

    Enables detectors to share context and findings through structured metadata,
    reducing false positives and improving detection accuracy through multi-detector
    validation.

    Attributes:
        detector: Name of the detector providing this metadata
        confidence: Confidence score 0.0-1.0 (higher = more confident)
        evidence: List of evidence supporting the finding (e.g., ["high_lcom", "many_methods"])
        tags: Categorization tags (e.g., ["god_class", "complexity", "symptom"])
        related_findings: IDs of related findings from other detectors

    Example:
        >>> metadata = CollaborationMetadata(
        ...     detector="GodClassDetector",
        ...     confidence=0.9,
        ...     evidence=["high_lcom", "many_methods", "low_cohesion"],
        ...     tags=["god_class", "complexity"],
        ...     related_findings=["finding-123", "finding-456"]
        ... )
    """
    detector: str
    confidence: float
    evidence: List[str]
    tags: List[str]
    related_findings: List[str] = field(default_factory=list)


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
        line_start: Optional starting line number where issue occurs
        line_end: Optional ending line number where issue occurs
        graph_context: Additional graph data about the issue
        suggested_fix: Optional fix suggestion
        estimated_effort: Estimated effort to fix (e.g., "Small (2-4 hours)")
        created_at: When the finding was detected
        collaboration_metadata: List of collaboration metadata from multiple detectors

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
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    graph_context: Dict = field(default_factory=dict)
    suggested_fix: Optional[str] = None
    estimated_effort: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    collaboration_metadata: List[CollaborationMetadata] = field(default_factory=list)

    # Deduplication fields (REPO-152 Phase 3)
    is_duplicate: bool = False
    detector_agreement_count: int = 1
    aggregate_confidence: float = 0.0
    merged_from: List[str] = field(default_factory=list)

    def add_collaboration_metadata(self, metadata: CollaborationMetadata) -> None:
        """Add collaboration metadata from a detector.

        Args:
            metadata: CollaborationMetadata instance with detector context

        Example:
            >>> finding.add_collaboration_metadata(CollaborationMetadata(
            ...     detector="GodClassDetector",
            ...     confidence=0.9,
            ...     evidence=["high_lcom", "many_methods"],
            ...     tags=["god_class", "complexity"]
            ... ))
        """
        self.collaboration_metadata.append(metadata)

    def get_collaboration_tags(self) -> List[str]:
        """Get all unique tags from collaboration metadata.

        Returns:
            List of unique tags from all collaboration metadata

        Example:
            >>> tags = finding.get_collaboration_tags()
            >>> print(tags)
            ['god_class', 'complexity', 'high_coupling']
        """
        tags = set()
        for metadata in self.collaboration_metadata:
            tags.update(metadata.tags)
        return list(tags)

    def get_confidence_scores(self) -> Dict[str, float]:
        """Get confidence scores from all detectors.

        Returns:
            Dictionary mapping detector name to confidence score

        Example:
            >>> scores = finding.get_confidence_scores()
            >>> print(scores)
            {'GodClassDetector': 0.9, 'RadonDetector': 0.95}
        """
        return {
            metadata.detector: metadata.confidence
            for metadata in self.collaboration_metadata
        }

    def has_tag(self, tag: str) -> bool:
        """Check if finding has a specific tag.

        Args:
            tag: Tag name to check

        Returns:
            True if any collaboration metadata contains the tag

        Example:
            >>> if finding.has_tag("god_class"):
            ...     print("This is a god class issue")
        """
        return tag in self.get_collaboration_tags()

    def get_average_confidence(self) -> float:
        """Calculate average confidence across all detectors.

        Returns:
            Average confidence score, or 0.0 if no metadata

        Example:
            >>> avg = finding.get_average_confidence()
            >>> print(f"Average confidence: {avg:.2f}")
            Average confidence: 0.85
        """
        if not self.collaboration_metadata:
            return 0.0
        scores = self.get_confidence_scores()
        return sum(scores.values()) / len(scores)

    @property
    def priority_score(self) -> float:
        """Calculate composite priority score for this finding.

        Priority score is calculated from:
        - Severity weight (40%): How critical is the issue
        - Confidence weight (30%): How certain are we about the issue
        - Detector agreement weight (30%): How many detectors agree

        Returns:
            Priority score between 0.0-100.0 (higher = more priority)

        Example:
            >>> sorted_findings = sorted(findings, key=lambda f: f.priority_score, reverse=True)
            >>> top_priority = sorted_findings[0]
            >>> print(f"Top priority: {top_priority.title} (score: {top_priority.priority_score:.1f})")
        """
        # Severity weight (40%) - normalize to 0-1 scale
        severity_map = {
            Severity.CRITICAL: 1.0,
            Severity.HIGH: 0.8,
            Severity.MEDIUM: 0.6,
            Severity.LOW: 0.4,
            Severity.INFO: 0.2
        }
        severity_weight = severity_map.get(self.severity, 0.5) * 0.4

        # Confidence weight (30%) - use aggregate or average confidence
        confidence = self.aggregate_confidence if self.aggregate_confidence > 0 else self.get_average_confidence()
        confidence_weight = confidence * 0.3

        # Detector agreement weight (30%) - normalize detector count
        # 1 detector = 0.0, 2 detectors = 0.5, 3+ detectors = 1.0
        agreement_normalized = min(1.0, (self.detector_agreement_count - 1) / 2.0) if self.detector_agreement_count > 1 else 0.0
        agreement_weight = agreement_normalized * 0.3

        # Calculate final score (0-100 scale)
        return (severity_weight + confidence_weight + agreement_weight) * 100


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
class Rule:
    """Custom code quality rule stored as graph node (REPO-125).

    Represents a user-defined or system rule for detecting code smells.
    Rules have time-based priority that refreshes based on usage patterns.

    Attributes:
        id: Unique rule identifier (e.g., "no-god-classes")
        name: Human-readable rule name
        description: Detailed explanation of what the rule detects
        pattern: Cypher query pattern to detect violations
        severity: Issue severity level (CRITICAL, HIGH, MEDIUM, LOW, INFO)
        enabled: Whether the rule is active
        userPriority: User-defined base priority (0-1000)
        lastUsed: Timestamp of last execution (auto-updated)
        accessCount: Number of times rule has been executed
        autoFix: Optional suggestion for fixing violations
        tags: Optional categorization tags
        createdAt: Rule creation timestamp
        updatedAt: Last modification timestamp

    Priority Calculation:
        priority = userPriority + recency_score + frequency_score
        - recency_score: 100 * exp(-hours_since_use / 24)
        - frequency_score: log10(accessCount + 1) * 10

    Example:
        >>> rule = Rule(
        ...     id="no-god-classes",
        ...     name="Classes should have fewer than 20 methods",
        ...     description="Large classes violate SRP and are hard to maintain",
        ...     pattern="MATCH (c:Class)-[:CONTAINS]->(m:Function)...",
        ...     severity=Severity.HIGH,
        ...     userPriority=100
        ... )
    """
    id: str
    name: str
    description: str
    pattern: str  # Cypher query
    severity: Severity
    enabled: bool = True
    userPriority: int = 50  # Default priority (0-1000 scale)
    lastUsed: Optional[datetime] = None  # Auto-updated on execution
    accessCount: int = 0  # Auto-incremented on execution
    autoFix: Optional[str] = None  # Suggested fix description
    tags: List[str] = field(default_factory=list)  # e.g., ["complexity", "architecture"]
    createdAt: datetime = field(default_factory=datetime.now)
    updatedAt: datetime = field(default_factory=datetime.now)

    def calculate_priority(self) -> float:
        """Calculate dynamic priority based on usage patterns.

        Returns:
            Priority score combining user preference, recency, and frequency
        """
        import math
        from datetime import datetime, timezone

        # Base priority (0-1000)
        base = float(self.userPriority)

        # Recency bonus (exponential decay)
        if self.lastUsed:
            hours_since_use = (datetime.now(timezone.utc) - self.lastUsed).total_seconds() / 3600
            recency = 100 * math.exp(-hours_since_use / 24)  # Decays over days
        else:
            recency = 0.0

        # Frequency bonus (logarithmic scale)
        frequency = math.log10(self.accessCount + 1) * 10

        return base + recency + frequency

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Neo4j storage."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "pattern": self.pattern,
            "severity": self.severity.value,
            "enabled": self.enabled,
            "userPriority": self.userPriority,
            "lastUsed": self.lastUsed.isoformat() if self.lastUsed else None,
            "accessCount": self.accessCount,
            "autoFix": self.autoFix,
            "tags": self.tags,
            "createdAt": self.createdAt.isoformat(),
            "updatedAt": self.updatedAt.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rule":
        """Create Rule from Neo4j node properties."""
        from datetime import datetime

        def parse_datetime(value):
            """Parse datetime from Neo4j (can be string or datetime object)."""
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            # Handle Neo4j DateTime objects
            if hasattr(value, 'to_native'):
                return value.to_native()
            return value

        # Parse datetimes
        last_used = parse_datetime(data.get("lastUsed"))
        created_at = parse_datetime(data["createdAt"])
        updated_at = parse_datetime(data["updatedAt"])

        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            pattern=data["pattern"],
            severity=Severity(data["severity"]),
            enabled=data.get("enabled", True),
            userPriority=data.get("userPriority", 50),
            lastUsed=last_used,
            accessCount=data.get("accessCount", 0),
            autoFix=data.get("autoFix"),
            tags=data.get("tags", []),
            createdAt=created_at,
            updatedAt=updated_at,
        )


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

    # Deduplication statistics (optional)
    dedup_stats: Optional[Dict] = None

    # Root cause analysis summary (REPO-155)
    root_cause_summary: Optional[Dict] = None

    # Voting engine statistics (REPO-156)
    voting_stats: Optional[Dict] = None

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
