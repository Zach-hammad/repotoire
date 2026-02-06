"""API routes for monorepo analysis.

This module provides endpoints for:
- Package detection in monorepos
- Per-package health analysis
- Affected packages detection
- Dependency graph visualization

REPO-435: Monorepo support for web UI.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.models import Organization, Repository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.monorepo import (
    AffectedPackagesDetector,
    CrossPackageAnalyzer,
    Package,
    PackageAnalyzer,
    PackageDetector,
    PackageHealth,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/monorepo", tags=["monorepo"])


# =============================================================================
# Request/Response Models
# =============================================================================


class PackageMetadataResponse(BaseModel):
    """Package metadata from configuration files."""

    name: str = Field(description="Package name")
    version: Optional[str] = Field(description="Package version")
    description: Optional[str] = Field(description="Package description")
    package_type: str = Field(description="Package type (npm, poetry, cargo, etc.)")
    config_file: str = Field(description="Path to configuration file")
    dependencies: List[str] = Field(default_factory=list, description="Package dependencies")
    dev_dependencies: List[str] = Field(default_factory=list, description="Development dependencies")
    language: Optional[str] = Field(description="Primary language (python, typescript, etc.)")
    framework: Optional[str] = Field(description="Framework (react, fastapi, etc.)")


class PackageResponse(BaseModel):
    """A package detected in the monorepo."""

    path: str = Field(description="Path to package directory")
    name: str = Field(description="Package name")
    metadata: PackageMetadataResponse = Field(description="Package metadata")
    file_count: int = Field(description="Number of files in package")
    loc: int = Field(description="Lines of code")
    has_tests: bool = Field(description="Whether package has tests")
    test_count: int = Field(description="Number of test files")
    imports_packages: List[str] = Field(default_factory=list, description="Packages this imports")
    imported_by_packages: List[str] = Field(default_factory=list, description="Packages that import this")


class PackageHealthResponse(BaseModel):
    """Health analysis for a single package."""

    package_path: str = Field(description="Path to package")
    package_name: str = Field(description="Package name")
    overall_score: float = Field(description="Overall health score (0-100)")
    grade: str = Field(description="Health grade (A-F)")
    coupling_score: float = Field(description="Coupling score (0-100, higher is better)")
    independence_score: float = Field(description="Independence score (0-100)")
    test_coverage: float = Field(description="Test coverage percentage")
    build_time_estimate: float = Field(description="Estimated build time in seconds")
    affected_by_changes: List[str] = Field(default_factory=list, description="Packages affected by changes here")


class ListPackagesResponse(BaseModel):
    """Response from listing packages."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    scanned_at: datetime = Field(description="When packages were detected")
    package_count: int = Field(description="Total number of packages")
    workspace_type: Optional[str] = Field(description="Detected workspace type (nx, turborepo, etc.)")
    packages: List[PackageResponse] = Field(description="List of detected packages")


class MonorepoHealthResponse(BaseModel):
    """Overall monorepo health analysis."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    analyzed_at: datetime = Field(description="When analysis was performed")
    overall_score: float = Field(description="Overall health score (0-100)")
    grade: str = Field(description="Overall grade (A-F)")
    avg_package_score: float = Field(description="Average package health score")
    package_count: int = Field(description="Total number of packages")
    cross_package_issues: int = Field(description="Number of cross-package issues")
    circular_dependencies: int = Field(description="Number of circular package dependencies")
    duplicate_code_percentage: float = Field(description="Code duplication across packages")
    packages: List[PackageHealthResponse] = Field(description="Per-package health scores")


class AffectedPackagesResponse(BaseModel):
    """Packages affected by changes."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    since: str = Field(description="Git reference compared against")
    detected_at: datetime = Field(description="When detection was performed")
    changed_files: int = Field(description="Number of changed files")
    changed_packages: List[str] = Field(description="Packages with direct changes")
    affected_packages: List[str] = Field(description="Packages affected by dependencies")
    all_packages: List[str] = Field(description="All packages that need testing/building")
    build_commands: List[str] = Field(default_factory=list, description="Suggested build commands")


class DependencyGraphResponse(BaseModel):
    """Dependency graph between packages."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    generated_at: datetime = Field(description="When graph was generated")
    nodes: List[dict] = Field(description="Graph nodes (packages)")
    edges: List[dict] = Field(description="Graph edges (dependencies)")


class CrossPackageIssueResponse(BaseModel):
    """A cross-package issue detected."""

    id: str = Field(description="Issue ID")
    severity: str = Field(description="Issue severity (critical, high, medium, low, info)")
    title: str = Field(description="Issue title")
    description: str = Field(description="Issue description")
    suggested_fix: Optional[str] = Field(description="Suggested fix")
    packages_involved: List[str] = Field(default_factory=list, description="Packages involved")


class CrossPackageAnalysisResponse(BaseModel):
    """Response from cross-package analysis."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    analyzed_at: datetime = Field(description="When analysis was performed")
    total_issues: int = Field(description="Total number of issues")
    by_severity: dict[str, int] = Field(description="Issues by severity")
    issues: List[CrossPackageIssueResponse] = Field(description="List of issues")


# =============================================================================
# Helper Functions
# =============================================================================


async def _verify_repository_access(
    repository_id: UUID,
    user: ClerkUser,
    session: AsyncSession,
) -> Repository:
    """Verify user has access to the repository and return it."""
    if user.org_id:
        org_result = await session.execute(
            select(Organization).where(Organization.clerk_org_id == user.org_id)
        )
        org = org_result.scalar_one_or_none()
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization not found",
            )

        repo_result = await session.execute(
            select(Repository).where(
                Repository.id == repository_id,
                Repository.organization_id == org.id,
            )
        )
        repo = repo_result.scalar_one_or_none()
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository not found or not accessible",
            )
        return repo
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization context required",
        )


async def _clone_repository(repo: Repository) -> Path:
    """Clone repository to a temporary directory.

    Returns the path to the cloned repository.
    Caller is responsible for cleanup.
    """
    from repotoire.integrations.github import get_installation_token

    try:
        token = await asyncio.to_thread(
            get_installation_token, repo.github_installation_id
        )
    except Exception as e:
        logger.error(f"Failed to get installation token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to authenticate with GitHub",
        )

    temp_dir = tempfile.mkdtemp(prefix="repotoire-monorepo-")
    clone_dir = Path(temp_dir) / "repo"

    clone_url = f"https://x-access-token:{token}@github.com/{repo.full_name}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(clone_dir)],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Repository clone timed out",
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Git clone failed: {e.stderr.decode() if e.stderr else e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clone repository",
        )

    return clone_dir


def _package_to_response(package: Package) -> PackageResponse:
    """Convert Package to response model."""
    return PackageResponse(
        path=package.path,
        name=package.name,
        metadata=PackageMetadataResponse(
            name=package.metadata.name,
            version=package.metadata.version,
            description=package.metadata.description,
            package_type=package.metadata.package_type,
            config_file=package.metadata.config_file,
            dependencies=package.metadata.dependencies,
            dev_dependencies=package.metadata.dev_dependencies,
            language=package.metadata.language,
            framework=package.metadata.framework,
        ),
        file_count=len(package.files),
        loc=package.loc,
        has_tests=package.has_tests,
        test_count=package.test_count,
        imports_packages=list(package.imports_packages),
        imported_by_packages=list(package.imported_by_packages),
    )


def _package_health_to_response(ph: PackageHealth) -> PackageHealthResponse:
    """Convert PackageHealth to response model."""
    return PackageHealthResponse(
        package_path=ph.package_path,
        package_name=ph.package_name,
        overall_score=ph.overall_score,
        grade=ph.grade,
        coupling_score=ph.coupling_score,
        independence_score=ph.independence_score,
        test_coverage=ph.test_coverage,
        build_time_estimate=ph.build_time_estimate,
        affected_by_changes=ph.affected_by_changes,
    )


# =============================================================================
# API Endpoints
# =============================================================================


@router.get(
    "/{repository_id}/packages",
    response_model=ListPackagesResponse,
    summary="Detect packages in monorepo",
    description="Scans a repository for packages (package.json, pyproject.toml, Cargo.toml, etc.) "
    "and returns a list of detected packages with their metadata.",
)
async def list_packages(
    repository_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ListPackagesResponse:
    """Detect and list packages in a monorepo."""
    repo = await _verify_repository_access(repository_id, user, session)

    import shutil

    clone_dir = None
    try:
        clone_dir = await _clone_repository(repo)

        # Detect packages
        detector = PackageDetector(clone_dir)
        packages = await asyncio.to_thread(detector.detect_packages)

        # Detect workspace type
        workspace_type = None
        if (clone_dir / "nx.json").exists():
            workspace_type = "nx"
        elif (clone_dir / "turbo.json").exists():
            workspace_type = "turborepo"
        elif (clone_dir / "lerna.json").exists():
            workspace_type = "lerna"
        elif (clone_dir / "pnpm-workspace.yaml").exists():
            workspace_type = "pnpm"

        return ListPackagesResponse(
            repository_id=repo.id,
            repository_name=repo.full_name,
            scanned_at=datetime.now(timezone.utc),
            package_count=len(packages),
            workspace_type=workspace_type,
            packages=[_package_to_response(p) for p in packages],
        )
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)


@router.get(
    "/{repository_id}/analyze",
    response_model=MonorepoHealthResponse,
    summary="Analyze monorepo health",
    description="Performs detailed health analysis of all packages in a monorepo, "
    "including per-package scores, coupling analysis, and cross-package issues.",
)
async def analyze_monorepo(
    repository_id: UUID,
    package: Optional[str] = Query(None, description="Analyze specific package by path or name"),
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MonorepoHealthResponse:
    """Analyze monorepo health with per-package scores."""
    from repotoire.graph.factory import create_client

    repo = await _verify_repository_access(repository_id, user, session)

    import shutil

    clone_dir = None
    try:
        clone_dir = await _clone_repository(repo)

        # Detect packages
        detector = PackageDetector(clone_dir)
        packages = await asyncio.to_thread(detector.detect_packages)

        if not packages:
            return MonorepoHealthResponse(
                repository_id=repo.id,
                repository_name=repo.full_name,
                analyzed_at=datetime.now(timezone.utc),
                overall_score=100.0,
                grade="A",
                avg_package_score=100.0,
                package_count=0,
                cross_package_issues=0,
                circular_dependencies=0,
                duplicate_code_percentage=0.0,
                packages=[],
            )

        # Analyze packages
        client = await asyncio.to_thread(create_client)
        analyzer = PackageAnalyzer(client, str(clone_dir))
        monorepo_health = await asyncio.to_thread(analyzer.analyze_monorepo, packages)

        return MonorepoHealthResponse(
            repository_id=repo.id,
            repository_name=repo.full_name,
            analyzed_at=datetime.now(timezone.utc),
            overall_score=monorepo_health.overall_score,
            grade=monorepo_health.grade,
            avg_package_score=monorepo_health.avg_package_score,
            package_count=monorepo_health.package_count,
            cross_package_issues=monorepo_health.cross_package_issues,
            circular_dependencies=monorepo_health.circular_package_dependencies,
            duplicate_code_percentage=monorepo_health.duplicate_code_across_packages,
            packages=[_package_health_to_response(ph) for ph in monorepo_health.package_health_scores],
        )
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)


@router.get(
    "/{repository_id}/affected",
    response_model=AffectedPackagesResponse,
    summary="Detect affected packages",
    description="Detects which packages have changes and which are affected by those changes "
    "(via dependencies). Useful for optimizing CI/CD to only build/test affected packages.",
)
async def get_affected_packages(
    repository_id: UUID,
    since: str = Query("origin/main", description="Git reference to compare against"),
    max_depth: int = Query(10, description="Maximum dependency traversal depth"),
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AffectedPackagesResponse:
    """Detect packages affected by changes since a git reference."""
    repo = await _verify_repository_access(repository_id, user, session)

    import shutil

    clone_dir = None
    try:
        clone_dir = await _clone_repository(repo)

        # Detect packages
        pkg_detector = PackageDetector(clone_dir)
        packages = await asyncio.to_thread(pkg_detector.detect_packages)

        if not packages:
            return AffectedPackagesResponse(
                repository_id=repo.id,
                repository_name=repo.full_name,
                since=since,
                detected_at=datetime.now(timezone.utc),
                changed_files=0,
                changed_packages=[],
                affected_packages=[],
                all_packages=[],
                build_commands=[],
            )

        # Detect affected packages
        affected_detector = AffectedPackagesDetector(clone_dir, packages)
        result = await asyncio.to_thread(
            affected_detector.detect_affected_since, since, max_depth=max_depth
        )

        # Generate build commands
        commands = affected_detector.generate_build_commands(result)

        return AffectedPackagesResponse(
            repository_id=repo.id,
            repository_name=repo.full_name,
            since=since,
            detected_at=datetime.now(timezone.utc),
            changed_files=result.get("stats", {}).get("changed_files", 0),
            changed_packages=result.get("changed", []),
            affected_packages=result.get("affected", []),
            all_packages=result.get("all", []),
            build_commands=commands,
        )
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)


@router.get(
    "/{repository_id}/dependencies",
    response_model=DependencyGraphResponse,
    summary="Get dependency graph",
    description="Returns the dependency graph between packages as nodes and edges "
    "for visualization.",
)
async def get_dependency_graph(
    repository_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DependencyGraphResponse:
    """Get dependency graph between packages."""
    repo = await _verify_repository_access(repository_id, user, session)

    import shutil

    clone_dir = None
    try:
        clone_dir = await _clone_repository(repo)

        # Detect packages
        pkg_detector = PackageDetector(clone_dir)
        packages = await asyncio.to_thread(pkg_detector.detect_packages)

        # Build graph representation for visualization
        nodes = []
        edges = []
        package_by_path = {p.path: p for p in packages}

        for pkg in packages:
            nodes.append({
                "id": pkg.path,
                "label": pkg.name,
                "type": pkg.metadata.package_type,
                "language": pkg.metadata.language,
                "framework": pkg.metadata.framework,
                "loc": pkg.loc,
                "file_count": len(pkg.files),
            })

            for imported_path in pkg.imports_packages:
                if imported_path in package_by_path:
                    edges.append({
                        "source": pkg.path,
                        "target": imported_path,
                        "type": "imports",
                    })

        return DependencyGraphResponse(
            repository_id=repo.id,
            repository_name=repo.full_name,
            generated_at=datetime.now(timezone.utc),
            nodes=nodes,
            edges=edges,
        )
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)


@router.get(
    "/{repository_id}/cross-package",
    response_model=CrossPackageAnalysisResponse,
    summary="Analyze cross-package issues",
    description="Detects issues spanning multiple packages: circular dependencies, "
    "excessive coupling, boundary violations, and inconsistent dependency versions.",
)
async def analyze_cross_package_issues(
    repository_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> CrossPackageAnalysisResponse:
    """Analyze cross-package issues in the monorepo."""
    repo = await _verify_repository_access(repository_id, user, session)

    import shutil

    clone_dir = None
    try:
        clone_dir = await _clone_repository(repo)

        # Detect packages
        pkg_detector = PackageDetector(clone_dir)
        packages = await asyncio.to_thread(pkg_detector.detect_packages)

        if not packages:
            return CrossPackageAnalysisResponse(
                repository_id=repo.id,
                repository_name=repo.full_name,
                analyzed_at=datetime.now(timezone.utc),
                total_issues=0,
                by_severity={},
                issues=[],
            )

        # Analyze cross-package issues
        analyzer = CrossPackageAnalyzer(packages)
        findings = await asyncio.to_thread(analyzer.detect_cross_package_issues)

        # Group by severity
        by_severity: dict[str, int] = {}
        issues = []

        for finding in findings:
            severity = finding.severity.value.lower()
            by_severity[severity] = by_severity.get(severity, 0) + 1

            issues.append(CrossPackageIssueResponse(
                id=finding.id,
                severity=severity,
                title=finding.title,
                description=finding.description,
                suggested_fix=finding.suggested_fix,
                packages_involved=finding.graph_context.get("packages_involved", []) if finding.graph_context else [],
            ))

        return CrossPackageAnalysisResponse(
            repository_id=repo.id,
            repository_name=repo.full_name,
            analyzed_at=datetime.now(timezone.utc),
            total_issues=len(findings),
            by_severity=by_severity,
            issues=issues,
        )
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)
