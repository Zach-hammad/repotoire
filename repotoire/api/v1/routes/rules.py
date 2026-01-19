"""Custom rules API routes (REPO-431).

This module provides API endpoints for managing organization-level
custom code quality rules stored in FalkorDB.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.models import (
    MemberRole,
    Organization,
    OrganizationMembership,
    User,
)
from repotoire.db.session import get_db
from repotoire.graph.factory import create_client
from repotoire.logging_config import get_logger
from repotoire.models import Rule, Severity
from repotoire.rules.engine import RuleEngine
from repotoire.rules.validator import RuleValidator

logger = get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["rules"])


# =============================================================================
# Request/Response Models
# =============================================================================


class RuleResponse(BaseModel):
    """Response with rule details."""

    id: str = Field(description="Unique rule identifier")
    name: str = Field(description="Human-readable rule name")
    description: str = Field(description="Detailed explanation")
    pattern: str = Field(description="Cypher query pattern")
    severity: str = Field(description="Severity level (critical, high, medium, low, info)")
    enabled: bool = Field(description="Whether the rule is active")
    user_priority: int = Field(description="User-defined priority (0-1000)")
    access_count: int = Field(description="Number of times executed")
    last_used: Optional[str] = Field(default=None, description="Last execution timestamp")
    auto_fix: Optional[str] = Field(default=None, description="Suggested fix description")
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last modification timestamp")
    priority_score: Optional[float] = Field(default=None, description="Calculated priority score")

    @classmethod
    def from_rule(cls, rule: Rule, include_priority: bool = False) -> "RuleResponse":
        """Create response from Rule model."""
        return cls(
            id=rule.id,
            name=rule.name,
            description=rule.description,
            pattern=rule.pattern,
            severity=rule.severity.value,
            enabled=rule.enabled,
            user_priority=rule.userPriority,
            access_count=rule.accessCount,
            last_used=rule.lastUsed.isoformat() if rule.lastUsed else None,
            auto_fix=rule.autoFix,
            tags=rule.tags or [],
            created_at=rule.createdAt.isoformat() if rule.createdAt else "",
            updated_at=rule.updatedAt.isoformat() if rule.updatedAt else "",
            priority_score=rule.calculate_priority() if include_priority else None,
        )


class RuleListResponse(BaseModel):
    """Response listing rules."""

    rules: List[RuleResponse] = Field(description="List of rules")
    total: int = Field(description="Total number of rules")


class RuleCreate(BaseModel):
    """Request to create a new rule."""

    id: str = Field(description="Unique rule identifier (e.g., 'no-god-classes')")
    name: str = Field(description="Human-readable rule name")
    description: str = Field(description="Detailed explanation of what the rule detects")
    pattern: str = Field(description="Cypher query pattern to detect violations")
    severity: str = Field(
        default="medium",
        description="Severity level (critical, high, medium, low, info)",
    )
    enabled: bool = Field(default=True, description="Whether the rule is active")
    user_priority: int = Field(default=50, description="User-defined priority (0-1000)")
    auto_fix: Optional[str] = Field(
        default=None, description="Suggested fix description"
    )
    tags: List[str] = Field(default_factory=list, description="Categorization tags")


class RuleUpdate(BaseModel):
    """Request to update a rule."""

    name: Optional[str] = Field(default=None, description="New rule name")
    description: Optional[str] = Field(default=None, description="New description")
    pattern: Optional[str] = Field(default=None, description="New Cypher pattern")
    severity: Optional[str] = Field(default=None, description="New severity level")
    enabled: Optional[bool] = Field(default=None, description="Enable/disable rule")
    user_priority: Optional[int] = Field(default=None, description="New priority")
    auto_fix: Optional[str] = Field(default=None, description="New fix suggestion")
    tags: Optional[List[str]] = Field(default=None, description="New tags")


class RuleTestRequest(BaseModel):
    """Request to test a rule."""

    scope: Optional[List[str]] = Field(
        default=None, description="Optional file paths to limit scope"
    )


class RuleTestResponse(BaseModel):
    """Response from rule test execution."""

    rule_id: str = Field(description="Rule that was tested")
    findings_count: int = Field(description="Number of violations found")
    findings: List[Dict[str, Any]] = Field(description="Finding details (limited)")
    execution_time_ms: float = Field(description="Execution time in milliseconds")


class ValidatePatternRequest(BaseModel):
    """Request to validate a Cypher pattern."""

    pattern: str = Field(description="Cypher query pattern to validate")


class ValidatePatternResponse(BaseModel):
    """Response from pattern validation."""

    valid: bool = Field(description="Whether the pattern is valid")
    error: Optional[str] = Field(default=None, description="Error message if invalid")
    warnings: List[str] = Field(
        default_factory=list, description="Performance warnings"
    )


class RuleStatsResponse(BaseModel):
    """Response with rule statistics."""

    total_rules: int = Field(description="Total number of rules")
    enabled_rules: int = Field(description="Number of enabled rules")
    avg_access_count: float = Field(description="Average access count")
    max_access_count: int = Field(description="Maximum access count")
    total_executions: int = Field(description="Total rule executions")


# =============================================================================
# Helper Functions
# =============================================================================


async def get_org_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    """Get organization by slug."""
    result = await session.execute(
        select(Organization).where(Organization.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_user_membership(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> OrganizationMembership | None:
    """Get user's membership in an organization."""
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()
    if not user_record:
        return None

    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_record.id,
            OrganizationMembership.organization_id == org.id,
        )
    )
    return result.scalar_one_or_none()


async def require_member(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> None:
    """Verify user is a member of the organization."""
    membership = await get_user_membership(session, user, org)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )


async def require_admin_or_owner(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> None:
    """Verify user is admin or owner of the organization."""
    membership = await get_user_membership(session, user, org)
    if not membership or membership.role not in [MemberRole.OWNER, MemberRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required to manage rules",
        )


def get_rule_engine(org: Organization) -> RuleEngine:
    """Get RuleEngine for the organization's graph."""
    client = create_client(org_id=org.id, org_slug=org.slug)
    return RuleEngine(client)


def get_rule_validator(org: Organization) -> RuleValidator:
    """Get RuleValidator for the organization's graph."""
    client = create_client(org_id=org.id, org_slug=org.slug)
    return RuleValidator(client)


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/{slug}/rules",
    response_model=RuleListResponse,
    summary="List custom rules",
    description="Get all custom code quality rules for the organization.",
)
async def list_rules(
    slug: str,
    enabled_only: bool = False,
    tags: Optional[str] = None,
    limit: Optional[int] = None,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleListResponse:
    """List all custom rules for an organization.

    Returns rules sorted by priority (highest first).
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    engine = get_rule_engine(org)
    tag_list = tags.split(",") if tags else None

    rules = engine.list_rules(
        enabled_only=enabled_only,
        tags=tag_list,
        limit=limit,
    )

    return RuleListResponse(
        rules=[RuleResponse.from_rule(r, include_priority=True) for r in rules],
        total=len(rules),
    )


@router.post(
    "/{slug}/rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom rule",
    description="Create a new custom code quality rule.",
)
async def create_rule(
    slug: str,
    request: RuleCreate,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Create a new custom rule.

    The Cypher pattern will be validated before creation.
    Requires admin or owner role.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    await require_admin_or_owner(session, user, org)

    # Validate pattern
    validator = get_rule_validator(org)
    is_valid, error = validator.validate_pattern(request.pattern)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid pattern: {error}",
        )

    # Validate severity
    try:
        severity = Severity(request.severity.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid severity. Must be one of: critical, high, medium, low, info",
        )

    # Create rule
    engine = get_rule_engine(org)
    rule = Rule(
        id=request.id,
        name=request.name,
        description=request.description,
        pattern=request.pattern,
        severity=severity,
        enabled=request.enabled,
        userPriority=request.user_priority,
        autoFix=request.auto_fix,
        tags=request.tags or [],
    )

    try:
        created = engine.create_rule(rule)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    logger.info(
        f"Created rule '{request.id}' for org {slug}",
        extra={
            "organization_slug": slug,
            "rule_id": request.id,
            "user_id": user.user_id,
        },
    )

    return RuleResponse.from_rule(created)


@router.get(
    "/{slug}/rules/{rule_id}",
    response_model=RuleResponse,
    summary="Get a custom rule",
    description="Get details of a specific custom rule.",
)
async def get_rule(
    slug: str,
    rule_id: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Get a specific rule by ID."""
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    engine = get_rule_engine(org)
    rule = engine.get_rule(rule_id)

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )

    return RuleResponse.from_rule(rule, include_priority=True)


@router.put(
    "/{slug}/rules/{rule_id}",
    response_model=RuleResponse,
    summary="Update a custom rule",
    description="Update an existing custom rule.",
)
async def update_rule(
    slug: str,
    rule_id: str,
    request: RuleUpdate,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Update a custom rule.

    Only provided fields will be updated. Requires admin or owner role.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    await require_admin_or_owner(session, user, org)

    engine = get_rule_engine(org)

    # Check rule exists
    existing = engine.get_rule(rule_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )

    # Validate new pattern if provided
    if request.pattern is not None:
        validator = get_rule_validator(org)
        is_valid, error = validator.validate_pattern(request.pattern)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid pattern: {error}",
            )

    # Validate severity if provided
    if request.severity is not None:
        try:
            Severity(request.severity.lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid severity. Must be one of: critical, high, medium, low, info",
            )

    # Build update dict
    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.pattern is not None:
        updates["pattern"] = request.pattern
    if request.severity is not None:
        updates["severity"] = request.severity.lower()
    if request.enabled is not None:
        updates["enabled"] = request.enabled
    if request.user_priority is not None:
        updates["userPriority"] = request.user_priority
    if request.auto_fix is not None:
        updates["autoFix"] = request.auto_fix
    if request.tags is not None:
        updates["tags"] = request.tags

    updated = engine.update_rule(rule_id, **updates)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update rule",
        )

    logger.info(
        f"Updated rule '{rule_id}' for org {slug}",
        extra={
            "organization_slug": slug,
            "rule_id": rule_id,
            "user_id": user.user_id,
        },
    )

    return RuleResponse.from_rule(updated, include_priority=True)


@router.delete(
    "/{slug}/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom rule",
    description="Delete a custom rule from the organization.",
)
async def delete_rule(
    slug: str,
    rule_id: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a custom rule.

    Requires admin or owner role.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    await require_admin_or_owner(session, user, org)

    engine = get_rule_engine(org)
    deleted = engine.delete_rule(rule_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )

    logger.info(
        f"Deleted rule '{rule_id}' for org {slug}",
        extra={
            "organization_slug": slug,
            "rule_id": rule_id,
            "user_id": user.user_id,
        },
    )


@router.post(
    "/{slug}/rules/{rule_id}/test",
    response_model=RuleTestResponse,
    summary="Test a custom rule",
    description="Execute a rule and see what violations it would find.",
)
async def test_rule(
    slug: str,
    rule_id: str,
    request: Optional[RuleTestRequest] = None,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleTestResponse:
    """Test (dry-run) a rule to see what it would find.

    Returns up to 20 sample findings.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    engine = get_rule_engine(org)
    rule = engine.get_rule(rule_id)

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )

    import time
    start = time.time()

    scope = request.scope if request else None
    findings = engine.execute_rule(rule, scope=scope)

    elapsed_ms = (time.time() - start) * 1000

    # Convert findings to dicts (limit to 20)
    finding_dicts = []
    for f in findings[:20]:
        finding_dicts.append({
            "id": f.id,
            "title": f.title,
            "description": f.description,
            "severity": f.severity.value,
            "affected_files": f.affected_files,
            "affected_nodes": f.affected_nodes,
            "suggested_fix": f.suggested_fix,
        })

    logger.info(
        f"Tested rule '{rule_id}' for org {slug}: {len(findings)} findings",
        extra={
            "organization_slug": slug,
            "rule_id": rule_id,
            "findings_count": len(findings),
            "execution_time_ms": elapsed_ms,
        },
    )

    return RuleTestResponse(
        rule_id=rule_id,
        findings_count=len(findings),
        findings=finding_dicts,
        execution_time_ms=round(elapsed_ms, 2),
    )


@router.post(
    "/{slug}/rules/validate",
    response_model=ValidatePatternResponse,
    summary="Validate a Cypher pattern",
    description="Validate a Cypher query pattern before creating a rule.",
)
async def validate_pattern(
    slug: str,
    request: ValidatePatternRequest,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ValidatePatternResponse:
    """Validate a Cypher pattern.

    Checks for:
    - Syntax errors
    - Required MATCH and RETURN clauses
    - Dangerous operations (DELETE, DROP, etc.)
    - Performance warnings
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    validator = get_rule_validator(org)
    is_valid, error = validator.validate_pattern(request.pattern)

    # Get warnings even if valid
    warnings = validator._check_performance_issues(request.pattern) if is_valid else []

    return ValidatePatternResponse(
        valid=is_valid,
        error=error,
        warnings=warnings,
    )


@router.get(
    "/{slug}/rules/stats",
    response_model=RuleStatsResponse,
    summary="Get rule statistics",
    description="Get aggregate statistics about custom rules.",
)
async def get_rule_stats(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleStatsResponse:
    """Get aggregate statistics about rules."""
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    engine = get_rule_engine(org)
    stats = engine.get_rule_statistics()

    return RuleStatsResponse(
        total_rules=stats.get("total_rules", 0),
        enabled_rules=stats.get("enabled_rules", 0),
        avg_access_count=stats.get("avg_access_count", 0.0) or 0.0,
        max_access_count=stats.get("max_access_count", 0) or 0,
        total_executions=stats.get("total_executions", 0) or 0,
    )


@router.get(
    "/{slug}/rules/hot",
    response_model=RuleListResponse,
    summary="Get hot rules",
    description="Get the most frequently used rules for RAG context.",
)
async def get_hot_rules(
    slug: str,
    top_k: int = 10,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RuleListResponse:
    """Get top-k hot rules sorted by recent usage.

    Used by RAG system to include relevant rules in context.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    await require_member(session, user, org)

    engine = get_rule_engine(org)
    rules = engine.get_hot_rules(top_k=min(top_k, 50))

    return RuleListResponse(
        rules=[RuleResponse.from_rule(r, include_priority=True) for r in rules],
        total=len(rules),
    )
