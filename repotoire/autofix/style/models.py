"""Data models for style analysis and enforcement."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class StyleRule(BaseModel):
    """A detected style rule with confidence metrics."""

    name: str = Field(description="Name of the style rule (e.g., 'function_naming')")
    value: str = Field(description="The detected convention (e.g., 'snake_case')")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score (0.0-1.0) - percentage of codebase following this rule"
    )
    sample_count: int = Field(
        ge=0, description="Number of samples analyzed to determine this rule"
    )
    examples: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 example names/snippets demonstrating the convention",
    )

    def is_high_confidence(self, threshold: float = 0.6) -> bool:
        """Check if rule meets confidence threshold for enforcement.

        Args:
            threshold: Minimum confidence to consider high confidence

        Returns:
            True if confidence >= threshold
        """
        return self.confidence >= threshold


class StyleProfile(BaseModel):
    """Complete style profile for a repository."""

    repository: str = Field(description="Path to the analyzed repository")
    analyzed_at: datetime = Field(
        default_factory=datetime.utcnow, description="When the analysis was performed"
    )
    file_count: int = Field(ge=0, description="Number of Python files analyzed")

    # Core naming conventions
    function_naming: StyleRule = Field(description="Function naming convention")
    class_naming: StyleRule = Field(description="Class naming convention")
    variable_naming: StyleRule = Field(description="Variable naming convention")
    constant_naming: Optional[StyleRule] = Field(
        default=None, description="Constant naming convention (SCREAMING_SNAKE_CASE)"
    )

    # Documentation style
    docstring_style: StyleRule = Field(description="Docstring style (google, numpy, sphinx, none)")

    # Formatting
    max_line_length: StyleRule = Field(
        description="Maximum line length (80, 88, 100, 120)"
    )

    # Type hints
    type_hint_coverage: float = Field(
        ge=0.0,
        le=1.0,
        description="Percentage of functions with type annotations",
    )

    # Import organization
    import_style: Optional[StyleRule] = Field(
        default=None, description="Import organization style (grouped, ungrouped)"
    )

    # Extension point for custom rules
    custom_rules: Dict[str, StyleRule] = Field(
        default_factory=dict,
        description="Additional custom style rules detected",
    )

    def get_high_confidence_rules(self, threshold: float = 0.6) -> List[StyleRule]:
        """Get all rules that meet the confidence threshold.

        Args:
            threshold: Minimum confidence to include

        Returns:
            List of StyleRule objects meeting threshold
        """
        rules = []
        core_rules = [
            self.function_naming,
            self.class_naming,
            self.variable_naming,
            self.docstring_style,
            self.max_line_length,
        ]

        # Add optional rules if present
        if self.constant_naming:
            core_rules.append(self.constant_naming)
        if self.import_style:
            core_rules.append(self.import_style)

        for rule in core_rules:
            if rule.is_high_confidence(threshold):
                rules.append(rule)

        # Add high-confidence custom rules
        for rule in self.custom_rules.values():
            if rule.is_high_confidence(threshold):
                rules.append(rule)

        return rules

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "repository": self.repository,
            "analyzed_at": self.analyzed_at.isoformat(),
            "file_count": self.file_count,
            "function_naming": self.function_naming.model_dump(),
            "class_naming": self.class_naming.model_dump(),
            "variable_naming": self.variable_naming.model_dump(),
            "constant_naming": self.constant_naming.model_dump() if self.constant_naming else None,
            "docstring_style": self.docstring_style.model_dump(),
            "max_line_length": self.max_line_length.model_dump(),
            "type_hint_coverage": self.type_hint_coverage,
            "import_style": self.import_style.model_dump() if self.import_style else None,
            "custom_rules": {k: v.model_dump() for k, v in self.custom_rules.items()},
        }
