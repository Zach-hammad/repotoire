"""Celery tasks for repository and PR analysis.

Build: 2025-12-04T19:30:00Z

This module contains the main analysis tasks:
- analyze_repository: Full repository analysis with progress tracking
- analyze_pr: PR-specific analysis for changed files only
- analyze_repository_priority: High-priority analysis for enterprise tier

These tasks use the IngestionPipeline and AnalysisEngine to:
1. Clone the repository
2. Build/update the knowledge graph
3. Run code health detectors
4. Store results and trigger notifications
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update

from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    Organization,
    Repository,
)
from repotoire.db.models.finding import Finding as FindingDB
from repotoire.db.models.finding import FindingSeverity
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app
from repotoire.workers.limits import with_concurrency_limit
from repotoire.workers.progress import ProgressTracker

if TYPE_CHECKING:
    from repotoire.models import CodebaseHealth

logger = get_logger(__name__)

# Clone directory for temporary repository checkouts
CLONE_BASE_DIR = Path(os.getenv("REPOTOIRE_CLONE_DIR", "/tmp/repotoire-clones"))


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_repository",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
@with_concurrency_limit
def analyze_repository(
    self,
    analysis_run_id: str,
    repo_id: str,
    commit_sha: str,
    incremental: bool = True,
) -> dict[str, Any]:
    """Full repository analysis task.

    Performs complete code health analysis including:
    - Cloning the repository
    - Building/updating the knowledge graph
    - Running all code health detectors
    - Calculating scores and storing results
    - Triggering post-analysis notifications

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA to analyze.
        incremental: Whether to use incremental analysis (faster for re-analysis).

    Returns:
        dict with status, health_score, findings_count, and files_analyzed.
    """
    progress = ProgressTracker(self, analysis_run_id)
    clone_dir: Path | None = None

    try:
        # ============================================================
        # PHASE 1: Load data and update status (short DB session)
        # ============================================================
        # Use short-lived sessions to avoid Neon connection timeouts
        # during long-running operations (clone, ingestion, analysis)
        with get_sync_session() as session:
            # Load repository and organization
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                raise ValueError(f"Repository {repo_id} not found")

            # Extract values we need outside the session
            # (avoids lazy loading issues after session closes)
            repo_full_name = repo.full_name
            repo_github_installation_id = repo.github_installation_id
            org_id = repo.organization_id

            # Get org slug for multi-tenant graph naming
            org = repo.organization
            org_slug = org.slug if org else None

            # Update status to running
            progress.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=5,
                current_step="Cloning repository",
                started_at=datetime.now(timezone.utc),
            )

        # Session is now closed - safe for long operations

        # Trigger analysis.started webhook
        from repotoire.workers.hooks import _trigger_analysis_started_webhook

        _trigger_analysis_started_webhook(
            org_id=org_id,
            analysis_run_id=UUID(analysis_run_id),
            repo_id=UUID(repo_id),
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            triggered_by="push",  # Default to push, could be enhanced later
        )

        # ============================================================
        # PHASE 2: Clone repository (may take 30+ seconds)
        # ============================================================
        clone_dir = _clone_repository_by_values(
            full_name=repo_full_name,
            github_installation_id=repo_github_installation_id,
            commit_sha=commit_sha,
        )

        progress.update(
            progress_percent=20,
            current_step="Building knowledge graph",
        )

        # ============================================================
        # PHASE 3: Build knowledge graph (may take minutes)
        # ============================================================
        # Get graph client (multi-tenant: isolated graph per org)
        graph_client = _get_graph_client_for_org(org_id, org_slug)

        # Import here to avoid circular imports
        from repotoire.pipeline.ingestion import IngestionPipeline

        # Run ingestion pipeline with repo context for multi-tenant isolation
        pipeline = IngestionPipeline(
            repo_path=str(clone_dir),
            neo4j_client=graph_client,
            repo_id=repo_id,  # Pass repo UUID for node tagging
            repo_slug=repo_full_name,  # Pass full name (owner/repo)
        )

        def ingestion_progress(pct: float) -> None:
            progress.update(
                progress_percent=20 + int(pct * 0.4),  # 20-60%
            )

        ingest_result = pipeline.ingest(incremental=incremental)

        progress.update(
            progress_percent=60,
            current_step="Analyzing code health",
        )

        # ============================================================
        # PHASE 4: Run analysis (may take minutes)
        # ============================================================
        from repotoire.detectors.engine import AnalysisEngine

        engine = AnalysisEngine(neo4j_client=graph_client)

        def analysis_progress(pct: float) -> None:
            progress.update(
                progress_percent=60 + int(pct * 0.3),  # 60-90%
            )

        health = engine.analyze()

        progress.update(
            progress_percent=90,
            current_step="Saving results",
        )

        # ============================================================
        # PHASE 5: Save results (short DB session)
        # ============================================================
        with get_sync_session() as session:
            _save_analysis_results(
                session=session,
                analysis_run_id=analysis_run_id,
                health=health,
                files_analyzed=getattr(ingest_result, "files_processed", 0),
            )
        # Session is now closed

        # Trigger post-analysis hooks (outside the session)
        from repotoire.workers.hooks import on_analysis_complete

        on_analysis_complete.delay(analysis_run_id)

        return {
            "status": "completed",
            "health_score": health.overall_score,
            "findings_count": len(health.findings),
            "files_analyzed": getattr(ingest_result, "files_processed", 0),
        }

    except SoftTimeLimitExceeded:
        logger.warning(
            "Analysis timed out",
            extra={"analysis_run_id": analysis_run_id, "repo_id": repo_id},
        )
        progress.update(
            status=AnalysisStatus.FAILED,
            error_message="Analysis timed out after 30 minutes",
        )
        raise

    except Exception as e:
        logger.exception(
            "Analysis failed",
            extra={"analysis_run_id": analysis_run_id, "repo_id": repo_id, "error": str(e)},
        )

        progress.update(
            status=AnalysisStatus.FAILED,
            error_message=str(e)[:1000],
        )

        # Re-raise for Celery retry logic
        if self.request.retries < self.max_retries:
            raise

        # Final failure - send alert
        from repotoire.workers.hooks import on_analysis_failed

        on_analysis_failed.delay(analysis_run_id, str(e))

        return {
            "status": "failed",
            "error": str(e),
        }

    finally:
        # Cleanup clone directory
        if clone_dir and clone_dir.exists():
            try:
                shutil.rmtree(clone_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup clone dir: {e}")

        # Close progress tracker
        progress.close()


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_pr",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def analyze_pr(
    self,
    analysis_run_id: str,
    repo_id: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> dict[str, Any]:
    """PR-specific analysis (changed files only).

    Faster than full analysis - only analyzes files changed in the PR
    and calculates delta scores.

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        pr_number: Pull request number.
        base_sha: Base commit SHA (PR target).
        head_sha: Head commit SHA (PR source).

    Returns:
        dict with status, health_score, score_delta, and findings_count.
    """
    progress = ProgressTracker(self, analysis_run_id)
    clone_dir: Path | None = None

    # Set pending commit status at start
    from repotoire.workers.hooks import set_commit_status_pending

    set_commit_status_pending.delay(
        repo_id=repo_id,
        commit_sha=head_sha,
        analysis_run_id=analysis_run_id,
    )

    try:
        with get_sync_session() as session:
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                raise ValueError(f"Repository {repo_id} not found")

            org = repo.organization
            org_id = org.id
            repo_full_name = repo.full_name

            progress.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=5,
                current_step="Cloning repository",
                started_at=datetime.now(timezone.utc),
            )

            # Trigger analysis.started webhook for PR analysis
            from repotoire.workers.hooks import _trigger_analysis_started_webhook

            _trigger_analysis_started_webhook(
                org_id=org_id,
                analysis_run_id=UUID(analysis_run_id),
                repo_id=UUID(repo_id),
                repo_full_name=repo_full_name,
                commit_sha=head_sha,
                triggered_by="pr",
            )

            # Clone and get changed files
            clone_dir = _clone_repository(
                repo=repo,
                org=org,
                commit_sha=head_sha,
            )

            # Get list of changed files
            changed_files = _get_changed_files(clone_dir, base_sha, head_sha)

            if not changed_files:
                progress.update(
                    status=AnalysisStatus.COMPLETED,
                    progress_percent=100,
                    current_step="No analyzable files changed",
                )
                return {"status": "completed", "findings_count": 0}

            progress.update(
                progress_percent=20,
                current_step=f"Analyzing {len(changed_files)} changed files",
            )

            # Get graph client (multi-tenant: isolated graph per org)
            graph_client = _get_graph_client_for_org(org_id, org.slug if org else None)

            # Import here to avoid circular imports
            from repotoire.pipeline.ingestion import IngestionPipeline

            # Run incremental ingestion on changed files only
            pipeline = IngestionPipeline(
                repo_path=str(clone_dir),
                neo4j_client=graph_client,
                repo_id=repo_id,  # Pass repo UUID for node tagging
                repo_slug=repo_full_name,  # Pass full name (owner/repo)
            )

            # Ingest only changed files
            pipeline.ingest(incremental=True)

            progress.update(
                progress_percent=60,
                current_step="Analyzing changed code",
            )

            # Run analysis scoped to changed files
            from repotoire.detectors.engine import AnalysisEngine

            engine = AnalysisEngine(neo4j_client=graph_client)
            health = engine.analyze()

            # Get previous score for delta calculation
            base_score = _get_score_at_commit(session, repo_id, base_sha)
            head_score = health.overall_score
            score_delta = head_score - base_score if base_score is not None else None

            progress.update(
                progress_percent=90,
                current_step="Saving results",
            )

            # Update AnalysisRun
            run_id = UUID(analysis_run_id)
            session.execute(
                update(AnalysisRun)
                .where(AnalysisRun.id == run_id)
                .values(
                    status=AnalysisStatus.COMPLETED,
                    health_score=head_score,
                    structure_score=health.structure_score,
                    quality_score=health.quality_score,
                    architecture_score=health.architecture_score,
                    score_delta=score_delta,
                    findings_count=len(health.findings),
                    files_analyzed=len(changed_files),
                    completed_at=datetime.now(timezone.utc),
                    progress_percent=100,
                    current_step="Complete",
                )
            )

            # Persist individual findings for PR comment
            if health.findings:
                logger.info(
                    f"Persisting {len(health.findings)} findings for PR analysis {analysis_run_id}"
                )
                severity_map = {
                    "CRITICAL": FindingSeverity.CRITICAL,
                    "HIGH": FindingSeverity.HIGH,
                    "MEDIUM": FindingSeverity.MEDIUM,
                    "LOW": FindingSeverity.LOW,
                    "INFO": FindingSeverity.INFO,
                }
                for finding in health.findings:
                    severity = severity_map.get(
                        finding.severity.name, FindingSeverity.INFO
                    )
                    db_finding = FindingDB(
                        analysis_run_id=run_id,
                        detector=finding.detector,
                        severity=severity,
                        title=finding.title[:500],
                        description=finding.description,
                        affected_files=finding.affected_files or [],
                        affected_nodes=finding.affected_nodes or [],
                        line_start=finding.line_start,
                        line_end=finding.line_end,
                        suggested_fix=finding.suggested_fix,
                        estimated_effort=finding.estimated_effort,
                        graph_context=finding.graph_context,
                    )
                    session.add(db_finding)

            session.commit()

        # Post PR comment with analysis results
        from repotoire.workers.hooks import post_pr_comment, set_commit_status_result

        post_pr_comment.delay(
            repo_id=repo_id,
            pr_number=pr_number,
            analysis_run_id=analysis_run_id,
            base_sha=base_sha,
        )

        # Set final commit status based on quality gates
        set_commit_status_result.delay(
            repo_id=repo_id,
            commit_sha=head_sha,
            analysis_run_id=analysis_run_id,
            base_sha=base_sha,
        )

        return {
            "status": "completed",
            "health_score": head_score,
            "score_delta": score_delta,
            "findings_count": len(health.findings),
            "files_analyzed": len(changed_files),
        }

    except Exception as e:
        logger.exception(
            "PR analysis failed",
            extra={
                "analysis_run_id": analysis_run_id,
                "repo_id": repo_id,
                "pr_number": pr_number,
                "error": str(e),
            },
        )
        progress.update(
            status=AnalysisStatus.FAILED,
            error_message=str(e)[:1000],
        )

        # Set error commit status
        try:
            from repotoire.github.pr_commenter import get_installation_token_for_repo
            from repotoire.services.github_status import (
                CommitState,
                build_analysis_url,
                set_commit_status,
            )

            with get_sync_session() as session:
                repo = session.get(Repository, UUID(repo_id))
                if repo:
                    token = get_installation_token_for_repo(repo.id)
                    if token:
                        target_url = build_analysis_url(analysis_run_id, repo_id)
                        error_desc = f"Analysis failed: {str(e)[:100]}"
                        set_commit_status(
                            installation_token=token,
                            repo_full_name=repo.full_name,
                            sha=head_sha,
                            state=CommitState.ERROR,
                            description=error_desc,
                            target_url=target_url,
                        )
        except Exception as status_error:
            logger.warning(f"Failed to set error commit status: {status_error}")

        raise

    finally:
        if clone_dir and clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        progress.close()


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_repository_priority",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def analyze_repository_priority(
    self,
    analysis_run_id: str,
    repo_id: str,
    commit_sha: str,
    incremental: bool = True,
) -> dict[str, Any]:
    """High-priority repository analysis for enterprise tier.

    Same as analyze_repository but runs on the priority queue
    with faster retry settings.

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA to analyze.
        incremental: Whether to use incremental analysis.

    Returns:
        dict with status, health_score, findings_count, and files_analyzed.
    """
    # Delegate to the regular analyze_repository task
    return analyze_repository(
        self,
        analysis_run_id=analysis_run_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        incremental=incremental,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _clone_repository_by_values(
    full_name: str,
    github_installation_id: int | None,
    commit_sha: str,
) -> Path:
    """Clone repository to a temporary directory using primitive values.

    This version takes primitive values instead of ORM objects, allowing
    it to be called outside a database session context.

    Args:
        full_name: Repository full name (e.g., "owner/repo").
        github_installation_id: GitHub App installation ID for auth.
        commit_sha: Git commit SHA to checkout.

    Returns:
        Path to the cloned repository.
    """
    CLONE_BASE_DIR.mkdir(parents=True, exist_ok=True)

    # Create unique clone directory
    clone_dir = CLONE_BASE_DIR / f"{full_name.replace('/', '_')}_{commit_sha[:8]}"

    if clone_dir.exists():
        # Already cloned, just checkout the commit
        subprocess.run(
            ["git", "checkout", commit_sha],
            cwd=clone_dir,
            check=True,
            capture_output=True,
        )
        return clone_dir

    # Get GitHub token for authenticated clone using installation ID
    token = _get_github_token_by_installation_id(full_name, github_installation_id)
    clone_url = f"https://github.com/{full_name}.git"

    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{full_name}.git"

    # Clone with depth 1 for speed
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            clone_url,
            str(clone_dir),
        ],
        check=True,
        capture_output=True,
    )

    # Fetch the specific commit
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    return clone_dir


def _clone_repository(
    repo: Repository,
    org: Organization,
    commit_sha: str,
) -> Path:
    """Clone repository to a temporary directory.

    Args:
        repo: Repository model instance.
        org: Organization model instance.
        commit_sha: Git commit SHA to checkout.

    Returns:
        Path to the cloned repository.
    """
    return _clone_repository_by_values(
        full_name=repo.full_name,
        github_installation_id=repo.github_installation_id,
        commit_sha=commit_sha,
    )


def _get_github_token_by_installation_id(
    full_name: str,
    github_installation_id: int | None,
) -> str | None:
    """Get GitHub installation token using primitive values.

    Uses the stored installation token if valid, or refreshes it
    via the GitHub App API if expired.

    Args:
        full_name: Repository full name for logging.
        github_installation_id: GitHub App installation ID.

    Returns:
        Installation access token or None if unavailable.
    """
    import asyncio

    from repotoire.api.services.encryption import TokenEncryption
    from repotoire.api.services.github import GitHubAppClient
    from repotoire.db.models import GitHubInstallation

    # Fall back to env var if no installation ID
    if not github_installation_id:
        logger.warning(
            f"No github_installation_id for repo {full_name}, using env token"
        )
        return os.environ.get("GITHUB_TOKEN")

    try:
        with get_sync_session() as session:
            # Find the GitHubInstallation by installation_id
            result = session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == github_installation_id
                )
            )
            installation = result.scalar_one_or_none()

            if not installation:
                logger.warning(
                    f"GitHubInstallation not found for installation_id={github_installation_id}"
                )
                return os.environ.get("GITHUB_TOKEN")

            # Decrypt the token
            encryption = TokenEncryption()
            github_client = GitHubAppClient()

            # Check if token is expiring soon (within 5 minutes)
            if github_client.is_token_expiring_soon(installation.token_expires_at):
                logger.info(
                    f"Refreshing expired token for installation {installation.installation_id}"
                )
                # Refresh the token using asyncio.run() since we're in sync context
                new_token, expires_at = asyncio.run(
                    github_client.get_installation_token(installation.installation_id)
                )
                installation.access_token_encrypted = encryption.encrypt(new_token)
                installation.token_expires_at = expires_at
                session.commit()
                return new_token

            # Return the current valid token
            return encryption.decrypt(installation.access_token_encrypted)

    except Exception as e:
        logger.error(f"Failed to get GitHub token: {e}")
        # Fall back to environment variable
        return os.environ.get("GITHUB_TOKEN")


def _get_github_token(repo: Repository) -> str | None:
    """Get GitHub installation token for repository.

    Uses the stored installation token if valid, or refreshes it
    via the GitHub App API if expired.

    Args:
        repo: Repository model instance with github_installation_id.

    Returns:
        Installation access token or None if unavailable.
    """
    return _get_github_token_by_installation_id(
        full_name=repo.full_name,
        github_installation_id=repo.github_installation_id,
    )


def _get_graph_client_for_org(org_id: UUID | None, org_slug: str | None = None):
    """Get graph database client for organization.

    In multi-tenant mode (REPOTOIRE_MULTITENANT=true), each organization gets
    its own isolated graph via the GraphClientFactory. In single-tenant mode,
    returns a shared graph client.

    Args:
        org_id: Organization UUID for multi-tenant isolation.
        org_slug: Organization slug for human-readable graph names.

    Returns:
        DatabaseClient instance (Neo4j or FalkorDB depending on config).
    """
    is_multitenant = os.environ.get("REPOTOIRE_MULTITENANT", "").lower() in (
        "true", "1", "yes"
    )

    if is_multitenant and org_id:
        # Multi-tenant mode: use GraphClientFactory for isolated graphs
        from repotoire.graph.tenant_factory import GraphClientFactory

        factory = GraphClientFactory()
        return factory.get_client(org_id, org_slug)

    # Single-tenant mode: use shared graph client
    from repotoire.graph.factory import create_client

    return create_client()


def _get_neo4j_client_for_org(org: Organization | None):
    """Get Neo4j client for organization.

    DEPRECATED: Use _get_graph_client_for_org instead.

    In a multi-tenant setup, each organization could have its own
    Neo4j database or namespace.

    Args:
        org: Organization model instance.

    Returns:
        Neo4jClient instance.
    """
    # Extract org_id and slug if org is provided
    if org:
        return _get_graph_client_for_org(org.id, org.slug)

    return _get_graph_client_for_org(None)


def _get_changed_files(
    repo_path: Path,
    base_sha: str,
    head_sha: str,
) -> list[Path]:
    """Get list of changed Python files between two commits.

    Args:
        repo_path: Path to the repository.
        base_sha: Base commit SHA.
        head_sha: Head commit SHA.

    Returns:
        List of paths to changed files.
    """
    # Fetch base commit for diff
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", base_sha],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base_sha, head_sha],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Filter for Python files (extend for other languages)
        if line.endswith(".py"):
            file_path = repo_path / line
            if file_path.exists():
                files.append(file_path)

    return files


def _get_score_at_commit(
    session,
    repo_id: str,
    commit_sha: str,
) -> int | None:
    """Get health score from a previous analysis at a specific commit.

    Args:
        session: SQLAlchemy session.
        repo_id: Repository UUID.
        commit_sha: Git commit SHA.

    Returns:
        Health score or None if no analysis exists.
    """
    result = session.execute(
        select(AnalysisRun.health_score)
        .where(AnalysisRun.repository_id == UUID(repo_id))
        .where(AnalysisRun.commit_sha == commit_sha)
        .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


def _save_analysis_results(
    session,
    analysis_run_id: str,
    health: "CodebaseHealth",
    files_analyzed: int,
) -> None:
    """Save analysis results to the database.

    Args:
        session: SQLAlchemy session.
        analysis_run_id: UUID of the AnalysisRun.
        health: CodebaseHealth result from analysis.
        files_analyzed: Number of files processed.
    """
    run_id = UUID(analysis_run_id)

    # Update AnalysisRun with scores
    session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .values(
            status=AnalysisStatus.COMPLETED,
            health_score=health.overall_score,
            structure_score=health.structure_score,
            quality_score=health.quality_score,
            architecture_score=health.architecture_score,
            findings_count=len(health.findings),
            files_analyzed=files_analyzed,
            completed_at=datetime.now(timezone.utc),
            progress_percent=100,
            current_step="Complete",
        )
    )

    # Persist individual findings
    if health.findings:
        logger.info(
            f"Persisting {len(health.findings)} findings for analysis {analysis_run_id}"
        )
        for finding in health.findings:
            # Map Severity enum to FindingSeverity
            severity_map = {
                "CRITICAL": FindingSeverity.CRITICAL,
                "HIGH": FindingSeverity.HIGH,
                "MEDIUM": FindingSeverity.MEDIUM,
                "LOW": FindingSeverity.LOW,
                "INFO": FindingSeverity.INFO,
            }
            severity = severity_map.get(
                finding.severity.name, FindingSeverity.INFO
            )

            db_finding = FindingDB(
                analysis_run_id=run_id,
                detector=finding.detector,
                severity=severity,
                title=finding.title[:500],  # Truncate to column limit
                description=finding.description,
                affected_files=finding.affected_files or [],
                affected_nodes=finding.affected_nodes or [],
                line_start=finding.line_start,
                line_end=finding.line_end,
                suggested_fix=finding.suggested_fix,
                estimated_effort=finding.estimated_effort,
                graph_context=finding.graph_context,
            )
            session.add(db_finding)

    session.commit()
