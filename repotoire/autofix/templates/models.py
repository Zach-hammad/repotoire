"""Pydantic models for fix templates."""

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class PatternType(str, Enum):
    """Type of pattern matching to use."""

    REGEX = "regex"
    LITERAL = "literal"
    AST = "ast"  # Reserved for future AST-based matching


class TemplateEvidence(BaseModel):
    """Evidence supporting a template fix."""

    documentation_refs: List[str] = Field(
        default_factory=list,
        description="References to documentation, PEPs, style guides",
    )
    best_practices: List[str] = Field(
        default_factory=list,
        description="Best practice justifications",
    )


class FixTemplate(BaseModel):
    """A template for automatic code fixes."""

    name: str = Field(description="Unique template name")
    description: Optional[str] = Field(
        default=None,
        description="Human-readable description of what this template fixes",
    )
    pattern: str = Field(description="Pattern to match (regex or literal)")
    pattern_type: PatternType = Field(
        default=PatternType.REGEX,
        description="Type of pattern matching",
    )
    replacement: str = Field(
        description="Replacement string with $1, $2 capture groups",
    )
    confidence: str = Field(
        default="HIGH",
        description="Confidence level (HIGH, MEDIUM, LOW)",
    )
    fix_type: str = Field(
        default="refactor",
        description="Type of fix (refactor, security, simplify, etc.)",
    )
    languages: List[str] = Field(
        default_factory=lambda: ["python"],
        description="Languages this template applies to",
    )
    evidence: TemplateEvidence = Field(
        default_factory=TemplateEvidence,
        description="Evidence and documentation supporting this fix",
    )
    file_pattern: Optional[str] = Field(
        default=None,
        description="Glob pattern to filter files (e.g., '**/models.py')",
    )
    priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Priority for matching (higher = checked first)",
    )

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: str) -> str:
        """Validate confidence is a valid level."""
        valid_levels = {"HIGH", "MEDIUM", "LOW"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"confidence must be one of {valid_levels}, got '{v}'")
        return upper

    @field_validator("pattern")
    @classmethod
    def validate_pattern_not_empty(cls, v: str) -> str:
        """Validate pattern is not empty."""
        if not v or not v.strip():
            raise ValueError("pattern cannot be empty")
        return v

    @field_validator("replacement")
    @classmethod
    def validate_replacement_not_empty(cls, v: str) -> str:
        """Validate replacement is not empty."""
        if v is None:
            raise ValueError("replacement cannot be None")
        return v


class TemplateMatch(BaseModel):
    """Result of matching a template against code."""

    template: FixTemplate = Field(description="The matched template")
    original_code: str = Field(description="Original code that matched")
    fixed_code: str = Field(description="Code after applying the fix")
    match_start: int = Field(description="Start position of match in original")
    match_end: int = Field(description="End position of match in original")
    capture_groups: Dict[str, str] = Field(
        default_factory=dict,
        description="Captured groups from regex match",
    )

    model_config = {"arbitrary_types_allowed": True}


class TemplateFile(BaseModel):
    """A YAML file containing multiple templates."""

    templates: List[FixTemplate] = Field(
        default_factory=list,
        description="List of fix templates",
    )
