"""Unit tests for FixRepository.

These tests use an in-memory SQLite database to test the repository
pattern without requiring a PostgreSQL connection.
"""

from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from repotoire.db.models.base import Base
from repotoire.db.models.fix import Fix, FixComment, FixConfidence, FixStatus, FixType
from repotoire.db.models.user import User
from repotoire.db.models.organization import Organization, PlanTier
from repotoire.db.models.repository import Repository
from repotoire.db.models.analysis import AnalysisRun, AnalysisStatus
from repotoire.db.repositories.fix import (
    FixNotFoundError,
    FixRepository,
    InvalidStatusTransitionError,
    VALID_STATUS_TRANSITIONS,
)


# =============================================================================
# Fixtures
# =============================================================================


def _create_sqlite_compatible_schema() -> MetaData:
    """Create a SQLite-compatible metadata for testing.

    SQLite doesn't support JSONB or PostgreSQL-specific types,
    so we create a simplified schema that works for testing.
    """
    metadata = MetaData()

    # Users table (all columns from ORM model)
    Table(
        "users",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("clerk_user_id", String(255), nullable=False, unique=True),
        Column("email", String(255), nullable=False, unique=True),
        Column("name", String(255), nullable=True),
        Column("avatar_url", String(2048), nullable=True),
        Column("deleted_at", DateTime(timezone=True), nullable=True),
        Column("anonymized_at", DateTime(timezone=True), nullable=True),
        Column("deletion_requested_at", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    )

    # Organizations table (all columns from ORM model)
    Table(
        "organizations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("name", String(255), nullable=False),
        Column("slug", String(255), nullable=False, unique=True),
        Column("clerk_org_id", String(255), nullable=True),
        Column("stripe_customer_id", String(255), nullable=True),
        Column("stripe_subscription_id", String(255), nullable=True),
        Column("plan_tier", String(50), nullable=False, default="free"),
        Column("plan_expires_at", DateTime(timezone=True), nullable=True),
        Column("graph_database_name", String(100), nullable=True),
        Column("graph_backend", String(50), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    )

    # Repositories table (all columns from ORM model)
    Table(
        "repositories",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
        Column("github_repo_id", Integer, nullable=False),
        Column("github_installation_id", Integer, nullable=False),
        Column("full_name", String(255), nullable=False),
        Column("default_branch", String(255), nullable=False, default="main"),
        Column("is_active", Integer, nullable=False, default=1),  # SQLite uses integer for bool
        Column("last_analyzed_at", DateTime(timezone=True), nullable=True),
        Column("health_score", Integer, nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    )

    # Analysis runs table (all columns from ORM model)
    Table(
        "analysis_runs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("repository_id", String(36), ForeignKey("repositories.id"), nullable=False),
        Column("commit_sha", String(40), nullable=False),
        Column("branch", String(255), nullable=False),
        Column("status", String(50), nullable=False, default="queued"),
        Column("health_score", Integer, nullable=True),
        Column("structure_score", Integer, nullable=True),
        Column("quality_score", Integer, nullable=True),
        Column("architecture_score", Integer, nullable=True),
        Column("score_delta", Integer, nullable=True),
        Column("findings_count", Integer, nullable=False, default=0),
        Column("files_analyzed", Integer, nullable=False, default=0),
        Column("progress_percent", Integer, nullable=False, default=0),
        Column("current_step", String(255), nullable=True),
        Column("triggered_by_id", String(36), ForeignKey("users.id"), nullable=True),
        Column("started_at", DateTime(timezone=True), nullable=True),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("error_message", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    )

    # Findings table (simplified for FK reference)
    Table(
        "findings",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("analysis_run_id", String(36), ForeignKey("analysis_runs.id"), nullable=False),
        Column("detector", String(100), nullable=False),
        Column("severity", String(50), nullable=False),
        Column("title", String(500), nullable=False),
        Column("description", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )

    # Fixes table (using JSON instead of JSONB for SQLite)
    Table(
        "fixes",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("analysis_run_id", String(36), ForeignKey("analysis_runs.id"), nullable=False),
        Column("finding_id", String(36), ForeignKey("findings.id"), nullable=True),
        Column("file_path", String(1024), nullable=False),
        Column("line_start", Integer, nullable=True),
        Column("line_end", Integer, nullable=True),
        Column("original_code", Text, nullable=False),
        Column("fixed_code", Text, nullable=False),
        Column("title", String(500), nullable=False),
        Column("description", Text, nullable=False),
        Column("explanation", Text, nullable=False),
        Column("fix_type", String(50), nullable=False),
        Column("confidence", String(50), nullable=False),
        Column("confidence_score", Float, nullable=False),
        Column("status", String(50), nullable=False, default="pending"),
        Column("evidence", JSON, nullable=True),  # JSON instead of JSONB
        Column("validation_data", JSON, nullable=True),  # JSON instead of JSONB
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        Column("applied_at", DateTime(timezone=True), nullable=True),
    )

    # Fix comments table
    Table(
        "fix_comments",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("fix_id", String(36), ForeignKey("fixes.id"), nullable=False),
        Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
        Column("content", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )

    return metadata


@pytest_asyncio.fixture
async def async_session():
    """Create an in-memory async SQLite session for testing."""
    # Use aiosqlite for async SQLite support
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create SQLite-compatible schema
    sqlite_metadata = _create_sqlite_compatible_schema()

    async with engine.begin() as conn:
        await conn.run_sync(sqlite_metadata.create_all)

    async_session_maker = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def sample_org(async_session: AsyncSession) -> Organization:
    """Create a sample organization."""
    org = Organization(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        plan_tier=PlanTier.PRO,
    )
    async_session.add(org)
    await async_session.commit()
    await async_session.refresh(org)
    return org


@pytest_asyncio.fixture
async def sample_user(async_session: AsyncSession) -> User:
    """Create a sample user."""
    user = User(
        id=uuid4(),
        clerk_user_id="user_test123",
        email="test@example.com",
        name="Test User",
    )
    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_repo(async_session: AsyncSession, sample_org: Organization) -> Repository:
    """Create a sample repository."""
    repo = Repository(
        id=uuid4(),
        organization_id=sample_org.id,
        github_repo_id=12345,
        github_installation_id=67890,
        full_name="test/test-repo",
        default_branch="main",
    )
    async_session.add(repo)
    await async_session.commit()
    await async_session.refresh(repo)
    return repo


@pytest_asyncio.fixture
async def sample_analysis_run(
    async_session: AsyncSession,
    sample_repo: Repository,
) -> AnalysisRun:
    """Create a sample analysis run."""
    run = AnalysisRun(
        id=uuid4(),
        repository_id=sample_repo.id,
        commit_sha="abc123def456",
        branch="main",
        status=AnalysisStatus.COMPLETED,
    )
    async_session.add(run)
    await async_session.commit()
    await async_session.refresh(run)
    return run


@pytest_asyncio.fixture
async def fix_repository(async_session: AsyncSession) -> FixRepository:
    """Create a FixRepository instance."""
    return FixRepository(async_session)


# =============================================================================
# Create Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_fix(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test creating a new fix."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="src/utils.py",
        original_code="def foo():\n    pass",
        fixed_code="def foo() -> None:\n    pass",
        title="Add type hint to foo",
        description="This fix adds a return type hint",
        explanation="Type hints improve code clarity",
        fix_type=FixType.TYPE_HINT,
        confidence=FixConfidence.HIGH,
        confidence_score=0.95,
    )

    assert fix.id is not None
    assert fix.analysis_run_id == sample_analysis_run.id
    assert fix.file_path == "src/utils.py"
    assert fix.status == FixStatus.PENDING
    assert fix.confidence == FixConfidence.HIGH
    assert fix.confidence_score == 0.95
    assert fix.created_at is not None


@pytest.mark.asyncio
async def test_create_fix_with_all_fields(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test creating a fix with all optional fields."""
    evidence = {
        "similar_patterns": ["pattern1", "pattern2"],
        "documentation_refs": ["PEP-484"],
    }
    validation_data = {
        "syntax_valid": True,
        "import_valid": True,
    }

    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="src/module.py",
        line_start=10,
        line_end=15,
        original_code="old code",
        fixed_code="new code",
        title="Fix title",
        description="Fix description",
        explanation="Fix explanation",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.MEDIUM,
        confidence_score=0.75,
        finding_id=None,
        evidence=evidence,
        validation_data=validation_data,
    )

    assert fix.line_start == 10
    assert fix.line_end == 15
    assert fix.evidence == evidence
    assert fix.validation_data == validation_data


# =============================================================================
# Read Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_by_id(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test getting a fix by ID."""
    created = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.SIMPLIFY,
        confidence=FixConfidence.LOW,
        confidence_score=0.5,
    )

    retrieved = await fix_repository.get_by_id(created.id)

    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.file_path == "test.py"


@pytest.mark.asyncio
async def test_get_by_id_not_found(fix_repository: FixRepository):
    """Test getting a non-existent fix returns None."""
    result = await fix_repository.get_by_id(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_or_raise(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test get_by_id_or_raise with existing fix."""
    created = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REMOVE,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    retrieved = await fix_repository.get_by_id_or_raise(created.id)
    assert retrieved.id == created.id


@pytest.mark.asyncio
async def test_get_by_id_or_raise_not_found(fix_repository: FixRepository):
    """Test get_by_id_or_raise raises for non-existent fix."""
    with pytest.raises(FixNotFoundError) as exc_info:
        await fix_repository.get_by_id_or_raise(uuid4())

    assert "not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_by_analysis_run(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test getting fixes by analysis run."""
    # Create multiple fixes
    for i in range(3):
        await fix_repository.create(
            analysis_run_id=sample_analysis_run.id,
            file_path=f"file{i}.py",
            original_code="old",
            fixed_code="new",
            title=f"Fix {i}",
            description="Test",
            explanation="Test",
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.MEDIUM,
            confidence_score=0.7,
        )

    fixes = await fix_repository.get_by_analysis_run(sample_analysis_run.id)

    assert len(fixes) == 3


@pytest.mark.asyncio
async def test_get_by_analysis_run_with_status_filter(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test filtering fixes by status."""
    # Create fixes with different statuses
    fix1 = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file1.py",
        original_code="old",
        fixed_code="new",
        title="Fix 1",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file2.py",
        original_code="old",
        fixed_code="new",
        title="Fix 2",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    # Approve one fix
    await fix_repository.update_status(fix1.id, FixStatus.APPROVED)

    # Get only approved fixes
    approved = await fix_repository.get_by_analysis_run(
        sample_analysis_run.id,
        status=FixStatus.APPROVED,
    )

    assert len(approved) == 1
    assert approved[0].id == fix1.id


# =============================================================================
# Search Tests
# =============================================================================


@pytest.mark.asyncio
async def test_search_with_text_filter(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test searching fixes by text."""
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file1.py",
        original_code="old",
        fixed_code="new",
        title="Add type hints",
        description="Adds type annotations",
        explanation="Test",
        fix_type=FixType.TYPE_HINT,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file2.py",
        original_code="old",
        fixed_code="new",
        title="Remove dead code",
        description="Removes unused function",
        explanation="Test",
        fix_type=FixType.REMOVE,
        confidence=FixConfidence.MEDIUM,
        confidence_score=0.7,
    )

    # Search for "type"
    fixes, total = await fix_repository.search(search_text="type")
    assert len(fixes) == 1
    assert "type" in fixes[0].title.lower()


@pytest.mark.asyncio
async def test_search_with_confidence_filter(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test filtering fixes by confidence."""
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file1.py",
        original_code="old",
        fixed_code="new",
        title="High confidence fix",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.95,
    )
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file2.py",
        original_code="old",
        fixed_code="new",
        title="Low confidence fix",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.LOW,
        confidence_score=0.5,
    )

    # Filter by high confidence
    fixes, total = await fix_repository.search(confidence=[FixConfidence.HIGH])
    assert len(fixes) == 1
    assert fixes[0].confidence == FixConfidence.HIGH


@pytest.mark.asyncio
async def test_search_pagination(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test pagination in search results."""
    # Create 10 fixes
    for i in range(10):
        await fix_repository.create(
            analysis_run_id=sample_analysis_run.id,
            file_path=f"file{i}.py",
            original_code="old",
            fixed_code="new",
            title=f"Fix {i}",
            description="Test",
            explanation="Test",
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.MEDIUM,
            confidence_score=0.7,
        )

    # Get first page
    page1, total = await fix_repository.search(limit=5, offset=0)
    assert len(page1) == 5
    assert total == 10

    # Get second page
    page2, _ = await fix_repository.search(limit=5, offset=5)
    assert len(page2) == 5

    # Verify no overlap
    page1_ids = {f.id for f in page1}
    page2_ids = {f.id for f in page2}
    assert page1_ids.isdisjoint(page2_ids)


# =============================================================================
# Status Update Tests
# =============================================================================


@pytest.mark.asyncio
async def test_update_status_pending_to_approved(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test approving a pending fix."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    updated = await fix_repository.update_status(fix.id, FixStatus.APPROVED)

    assert updated.status == FixStatus.APPROVED


@pytest.mark.asyncio
async def test_update_status_approved_to_applied(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test applying an approved fix sets applied_at."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    await fix_repository.update_status(fix.id, FixStatus.APPROVED)
    updated = await fix_repository.update_status(fix.id, FixStatus.APPLIED)

    assert updated.status == FixStatus.APPLIED
    assert updated.applied_at is not None


@pytest.mark.asyncio
async def test_update_status_invalid_transition(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test that invalid status transitions raise an error."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    # Try to apply a pending fix (should fail)
    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        await fix_repository.update_status(fix.id, FixStatus.APPLIED)

    assert exc_info.value.current_status == FixStatus.PENDING
    assert exc_info.value.new_status == FixStatus.APPLIED


@pytest.mark.asyncio
async def test_valid_status_transitions():
    """Test all valid status transitions are defined."""
    # Verify PENDING can go to APPROVED or REJECTED
    assert FixStatus.APPROVED in VALID_STATUS_TRANSITIONS[FixStatus.PENDING]
    assert FixStatus.REJECTED in VALID_STATUS_TRANSITIONS[FixStatus.PENDING]

    # Verify APPROVED can go to APPLIED, REJECTED, or FAILED
    assert FixStatus.APPLIED in VALID_STATUS_TRANSITIONS[FixStatus.APPROVED]
    assert FixStatus.REJECTED in VALID_STATUS_TRANSITIONS[FixStatus.APPROVED]
    assert FixStatus.FAILED in VALID_STATUS_TRANSITIONS[FixStatus.APPROVED]

    # Verify REJECTED can go back to PENDING
    assert FixStatus.PENDING in VALID_STATUS_TRANSITIONS[FixStatus.REJECTED]

    # Verify APPLIED is terminal
    assert len(VALID_STATUS_TRANSITIONS[FixStatus.APPLIED]) == 0


# =============================================================================
# Update Tests
# =============================================================================


@pytest.mark.asyncio
async def test_update_fix_fields(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test updating fix fields."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Original Title",
        description="Original Description",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.MEDIUM,
        confidence_score=0.7,
    )

    updated = await fix_repository.update(
        fix.id,
        title="Updated Title",
        description="Updated Description",
    )

    assert updated.title == "Updated Title"
    assert updated.description == "Updated Description"
    # Fixed code should remain unchanged
    assert updated.fixed_code == "new"


@pytest.mark.asyncio
async def test_update_validation_data(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test updating validation data."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.MEDIUM,
        confidence_score=0.7,
    )

    validation_data = {
        "syntax_valid": True,
        "import_valid": True,
        "type_valid": False,
        "errors": [{"type": "type", "message": "Missing type annotation"}],
    }

    updated = await fix_repository.update(
        fix.id,
        validation_data=validation_data,
    )

    assert updated.validation_data == validation_data


# =============================================================================
# Delete Tests
# =============================================================================


@pytest.mark.asyncio
async def test_delete_fix(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test deleting a fix."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REMOVE,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    result = await fix_repository.delete(fix.id)

    assert result is True

    # Verify it's gone
    retrieved = await fix_repository.get_by_id(fix.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_delete_nonexistent_fix(fix_repository: FixRepository):
    """Test deleting a non-existent fix returns False."""
    result = await fix_repository.delete(uuid4())
    assert result is False


# =============================================================================
# Batch Operations Tests
# =============================================================================


@pytest.mark.asyncio
async def test_batch_update_status(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test batch status update."""
    # Create multiple fixes
    fix_ids = []
    for i in range(3):
        fix = await fix_repository.create(
            analysis_run_id=sample_analysis_run.id,
            file_path=f"file{i}.py",
            original_code="old",
            fixed_code="new",
            title=f"Fix {i}",
            description="Test",
            explanation="Test",
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            confidence_score=0.9,
        )
        fix_ids.append(fix.id)

    # Batch approve
    processed, errors = await fix_repository.batch_update_status(
        fix_ids,
        FixStatus.APPROVED,
    )

    assert processed == 3
    assert len(errors) == 0

    # Verify all were approved
    for fix_id in fix_ids:
        fix = await fix_repository.get_by_id(fix_id)
        assert fix.status == FixStatus.APPROVED


@pytest.mark.asyncio
async def test_batch_update_status_with_invalid(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test batch update with some invalid fixes."""
    # Create a fix
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    # Include a non-existent ID
    fix_ids = [fix.id, uuid4()]

    processed, errors = await fix_repository.batch_update_status(
        fix_ids,
        FixStatus.APPROVED,
    )

    assert processed == 1
    assert len(errors) == 1
    assert "not found" in errors[0]


# =============================================================================
# Comment Tests
# =============================================================================


@pytest.mark.asyncio
async def test_add_comment(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
    sample_user: User,
):
    """Test adding a comment to a fix."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    comment = await fix_repository.add_comment(
        fix_id=fix.id,
        user_id=sample_user.id,
        content="This looks good!",
    )

    assert comment.id is not None
    assert comment.fix_id == fix.id
    assert comment.user_id == sample_user.id
    assert comment.content == "This looks good!"
    assert comment.created_at is not None


@pytest.mark.asyncio
async def test_add_comment_to_nonexistent_fix(
    fix_repository: FixRepository,
    sample_user: User,
):
    """Test adding a comment to a non-existent fix raises error."""
    with pytest.raises(FixNotFoundError):
        await fix_repository.add_comment(
            fix_id=uuid4(),
            user_id=sample_user.id,
            content="Test comment",
        )


@pytest.mark.asyncio
async def test_get_comments(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
    sample_user: User,
):
    """Test getting comments for a fix."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    # Add multiple comments
    for i in range(3):
        await fix_repository.add_comment(
            fix_id=fix.id,
            user_id=sample_user.id,
            content=f"Comment {i}",
        )

    comments = await fix_repository.get_comments(fix.id)

    assert len(comments) == 3


@pytest.mark.asyncio
async def test_delete_comment(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
    sample_user: User,
):
    """Test deleting a comment."""
    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    comment = await fix_repository.add_comment(
        fix_id=fix.id,
        user_id=sample_user.id,
        content="To be deleted",
    )

    result = await fix_repository.delete_comment(comment.id, user_id=sample_user.id)

    assert result is True

    # Verify it's gone
    comments = await fix_repository.get_comments(fix.id)
    assert len(comments) == 0


@pytest.mark.asyncio
async def test_delete_comment_wrong_user(
    fix_repository: FixRepository,
    async_session: AsyncSession,
    sample_analysis_run: AnalysisRun,
    sample_user: User,
):
    """Test that users can only delete their own comments."""
    # Create another user
    other_user = User(
        id=uuid4(),
        clerk_user_id="user_other",
        email="other@example.com",
        name="Other User",
    )
    async_session.add(other_user)
    await async_session.commit()

    fix = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="test.py",
        original_code="old",
        fixed_code="new",
        title="Test",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )

    comment = await fix_repository.add_comment(
        fix_id=fix.id,
        user_id=sample_user.id,
        content="Sample user's comment",
    )

    # Try to delete as other user
    result = await fix_repository.delete_comment(comment.id, user_id=other_user.id)

    assert result is False

    # Comment should still exist
    comments = await fix_repository.get_comments(fix.id)
    assert len(comments) == 1


# =============================================================================
# Statistics Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_stats_by_analysis_run(
    fix_repository: FixRepository,
    sample_analysis_run: AnalysisRun,
):
    """Test getting fix statistics."""
    # Create fixes with different statuses and confidences
    fix1 = await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file1.py",
        original_code="old",
        fixed_code="new",
        title="Fix 1",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        confidence_score=0.9,
    )
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file2.py",
        original_code="old",
        fixed_code="new",
        title="Fix 2",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.MEDIUM,
        confidence_score=0.7,
    )
    await fix_repository.create(
        analysis_run_id=sample_analysis_run.id,
        file_path="file3.py",
        original_code="old",
        fixed_code="new",
        title="Fix 3",
        description="Test",
        explanation="Test",
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.LOW,
        confidence_score=0.5,
    )

    # Approve one fix
    await fix_repository.update_status(fix1.id, FixStatus.APPROVED)

    stats = await fix_repository.get_stats_by_analysis_run(sample_analysis_run.id)

    assert stats["total"] == 3
    assert stats["by_status"]["pending"] == 2
    assert stats["by_status"]["approved"] == 1
    assert stats["by_confidence"]["high"] == 1
    assert stats["by_confidence"]["medium"] == 1
    assert stats["by_confidence"]["low"] == 1
