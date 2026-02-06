"""
Unified finding grouping logic for deduplication and voting.

Ensures consistent grouping across all systems to prevent duplicates
from escaping, whether voting or deduplication runs.

Part of REPO-152 (Deduplication) and REPO-156 (Voting Engine).
"""

from dataclasses import dataclass
from typing import List, Optional

from repotoire.logging_config import get_logger
from repotoire.models import Finding

logger = get_logger(__name__)


# Category mapping (shared between VotingEngine and FindingDeduplicator)
ISSUE_CATEGORY_MAP = {
    # Structural issues (can corroborate each other)
    "GodClassDetector": "structural_complexity",
    "RadonDetector": "structural_complexity",

    # Coupling issues
    "CircularDependencyDetector": "coupling",
    "ShotgunSurgeryDetector": "coupling",
    "InappropriateIntimacyDetector": "coupling",
    "FeatureEnvyDetector": "coupling",

    # Dead/unused code
    "DeadCodeDetector": "dead_code",
    "VultureDetector": "dead_code",

    # Import issues
    "RuffImportDetector": "imports",

    # Linting/style
    "RuffLintDetector": "linting",
    "PylintDetector": "linting",

    # Type issues
    "MypyDetector": "type_errors",

    # Security
    "BanditDetector": "security",
    "SemgrepDetector": "security",

    # Duplication
    "JscpdDetector": "duplication",

    # Architecture
    "ArchitecturalBottleneckDetector": "architecture",
    "MiddleManDetector": "architecture",
}

# Default line bucketing threshold (groups findings within 5 lines)
DEFAULT_LINE_PROXIMITY_THRESHOLD = 5


@dataclass(frozen=True)
class FindingGroupKey:
    """
    Immutable key for grouping findings.

    Two findings should be considered duplicates/similar if and only if
    ALL components of their keys match.

    Components:
    1. issue_category: Type of issue (structural_complexity, dead_code, etc.)
    2. issue_type_hint: What specifically is being reported
    3. affected_entities: Sorted tuple of affected node qualified names
    4. affected_files: Sorted tuple of affected file paths
    5. location_bucket: Bucketed line number (groups nearby findings)
    """
    issue_category: str
    issue_type_hint: str
    affected_entities: tuple
    affected_files: tuple
    location_bucket: Optional[int]

    def __str__(self) -> str:
        """String representation for use as dictionary key."""
        return (
            f"{self.issue_category}|"
            f"{self.issue_type_hint}|"
            f"{self.affected_entities}|"
            f"{self.affected_files}|"
            f"L{self.location_bucket}"
        )


def get_issue_category(finding: Finding) -> str:
    """
    Determine the category/type of issue for grouping.

    Only findings in the same category can be merged.
    This prevents merging unrelated issues just because they're
    in the same location.

    Args:
        finding: Finding to categorize

    Returns:
        Category string (e.g., "structural_complexity", "dead_code")
    """
    detector = finding.detector

    # Check for known detector
    if detector in ISSUE_CATEGORY_MAP:
        return ISSUE_CATEGORY_MAP[detector]

    # Handle merged/consensus detector names
    if detector.startswith("Consensus[") or detector.startswith("Merged["):
        # Extract first detector name from merged name
        inner = detector.split("[")[1].split("]")[0]
        first_detector = inner.split("+")[0]
        if first_detector in ISSUE_CATEGORY_MAP:
            return ISSUE_CATEGORY_MAP[first_detector]

    # Check collaboration metadata tags for category hints
    if finding.collaboration_metadata:
        tags = finding.get_collaboration_tags()
        if "security" in tags:
            return "security"
        if "complexity" in tags or "god_class" in tags:
            return "structural_complexity"
        if "coupling" in tags:
            return "coupling"
        if "dead_code" in tags or "unused" in tags:
            return "dead_code"

    # Default: use detector name as category (no merging with others)
    return f"detector_{detector}"


def extract_issue_type_hint(finding: Finding) -> str:
    """
    Extract semantic issue type hint from finding.

    Prevents merging genuinely different issues in the same location.
    For example, don't merge a "missing docstring" finding with a
    "high cyclomatic complexity" finding just because they're in
    the same method.

    Args:
        finding: Finding to extract type hint from

    Returns:
        Issue type hint (semantic identifier of the problem)
    """
    # Try to extract from title (most specific)
    title = (finding.title or "").lower()

    # Dead code patterns
    if any(w in title for w in ["unused", "dead code", "unreachable"]):
        return "unused_code"

    # Complexity patterns
    if any(w in title for w in ["complexity", "cyclomatic", "cognitive", "large"]):
        return "high_complexity"

    # Docstring patterns
    if any(w in title for w in ["docstring", "documentation", "missing doc"]):
        return "missing_documentation"

    # Circular dependency patterns
    if any(w in title for w in ["circular", "cycle", "cyclic"]):
        return "circular_dependency"

    # Type error patterns
    if any(w in title for w in ["type", "annotation", "hint"]):
        return "type_error"

    # Security patterns
    if any(w in title for w in ["security", "injection", "overflow", "vulnerability"]):
        return "security_issue"

    # Duplication patterns
    if any(w in title for w in ["duplicate", "duplication", "copy-paste"]):
        return "code_duplication"

    # Coupling/dependency patterns
    if any(w in title for w in ["coupling", "dependency", "import", "depend"]):
        return "high_coupling"

    # God class pattern
    if any(w in title for w in ["god class", "too many", "responsibilities"]):
        return "god_class"

    # Fall back to using the category as type hint
    category = get_issue_category(finding)
    return category


def get_finding_group_key(
    finding: Finding,
    line_proximity_threshold: int = DEFAULT_LINE_PROXIMITY_THRESHOLD
) -> FindingGroupKey:
    """
    Generate deterministic grouping key for a finding.

    Two findings will be grouped together (considered duplicates/similar)
    if and only if their keys are identical.

    This ensures:
    1. Same-category grouping (don't merge structural issues with dead code)
    2. Same issue type (don't merge different problems in same location)
    3. Same affected entities (same method, class, etc.)
    4. Same file context (same files affected)
    5. Location proximity (within threshold line range)

    Args:
        finding: Finding to generate key for
        line_proximity_threshold: Max line distance for grouping (default: 5)

    Returns:
        FindingGroupKey for deterministic grouping
    """
    # Component 1: Issue category (prevents cross-category merges)
    issue_category = get_issue_category(finding)

    # Component 2: Issue type hint (prevents different issue merges)
    issue_type_hint = extract_issue_type_hint(finding)

    # Component 3: Affected entities (sorted for determinism)
    affected_entities = tuple(sorted(finding.affected_nodes or []))

    # Component 4: Affected files (sorted for determinism)
    affected_files = tuple(sorted(finding.affected_files or []))

    # Component 5: Location bucket (groups nearby findings)
    if finding.line_start is not None:
        location_bucket = (finding.line_start // line_proximity_threshold) * line_proximity_threshold
    else:
        location_bucket = None

    return FindingGroupKey(
        issue_category=issue_category,
        issue_type_hint=issue_type_hint,
        affected_entities=affected_entities,
        affected_files=affected_files,
        location_bucket=location_bucket
    )


def validate_group_consistency(findings: List[Finding]) -> bool:
    """
    SAFETY CHECK: Validate that all findings in a group are compatible.

    Ensures no cross-category merges occurred. Should never fail with
    unified grouping, but useful for debugging and defensive programming.

    Args:
        findings: List of findings supposedly in same group

    Returns:
        True if all findings have same category, False otherwise
    """
    if not findings:
        return True

    first_category = get_issue_category(findings[0])
    return all(get_issue_category(f) == first_category for f in findings)
