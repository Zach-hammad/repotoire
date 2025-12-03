"""GitHub App integration routes.

This module provides API endpoints for GitHub App installation management,
webhook handling, and repository configuration.
"""

from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.auth import ClerkUser, require_org
from repotoire.api.services.encryption import TokenEncryption, get_token_encryption
from repotoire.api.services.github import GitHubAppClient, get_github_client
from repotoire.db.models import GitHubInstallation, GitHubRepository, Organization
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


# =============================================================================
# Request/Response Models
# =============================================================================


class GitHubRepoResponse(BaseModel):
    """Response model for a GitHub repository."""

    id: UUID
    repo_id: int = Field(..., description="GitHub's repository ID")
    full_name: str = Field(..., description="Full repository name (owner/repo)")
    default_branch: str = Field(..., description="Default branch name")
    enabled: bool = Field(..., description="Whether analysis is enabled")
    last_analyzed_at: Optional[datetime] = Field(
        None, description="When the repository was last analyzed"
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GitHubInstallationResponse(BaseModel):
    """Response model for a GitHub App installation."""

    id: UUID
    installation_id: int = Field(..., description="GitHub App installation ID")
    account_login: str = Field(..., description="GitHub account/org name")
    account_type: str = Field(..., description="Organization or User")
    created_at: datetime
    updated_at: datetime
    repo_count: int = Field(0, description="Number of repositories")

    model_config = ConfigDict(from_attributes=True)


class UpdateReposRequest(BaseModel):
    """Request model for updating repository enabled status."""

    repo_ids: list[int] = Field(..., description="List of GitHub repository IDs")
    enabled: bool = Field(..., description="Enable or disable analysis")


class WebhookEvent(BaseModel):
    """GitHub webhook event payload."""

    action: str
    installation: Optional[dict] = None
    repositories: Optional[list[dict]] = None
    repositories_added: Optional[list[dict]] = None
    repositories_removed: Optional[list[dict]] = None
    sender: Optional[dict] = None


# =============================================================================
# Helper Functions
# =============================================================================


async def get_org_by_clerk_id(
    db: AsyncSession, org_id: str, org_slug: Optional[str] = None
) -> Optional[Organization]:
    """Get organization by Clerk organization ID.

    Looks up by clerk_org_id first, then falls back to slug.
    If not found and org_slug is provided, auto-creates the organization.
    """
    # First try to find by clerk_org_id
    result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == org_id)
    )
    org = result.scalar_one_or_none()
    if org:
        return org

    # Fall back to slug lookup
    result = await db.execute(
        select(Organization).where(Organization.slug == org_id)
    )
    org = result.scalar_one_or_none()
    if org:
        # Update the org with clerk_org_id for future lookups
        org.clerk_org_id = org_id
        await db.commit()
        return org

    # Auto-create organization if slug is provided
    if org_slug:
        org = Organization(
            name=org_slug,
            slug=org_slug,
            clerk_org_id=org_id,
        )
        db.add(org)
        await db.commit()
        await db.refresh(org)
        logger.info(f"Auto-created organization {org_slug} for Clerk org {org_id}")
        return org

    return None


async def ensure_token_fresh(
    db: AsyncSession,
    installation: GitHubInstallation,
    github: GitHubAppClient,
    encryption: TokenEncryption,
) -> str:
    """Ensure the installation token is fresh, refreshing if needed.

    Args:
        db: Database session
        installation: GitHub installation record
        github: GitHub API client
        encryption: Token encryption service

    Returns:
        Fresh access token
    """
    # Check if token is expiring soon (within 5 minutes)
    if github.is_token_expiring_soon(installation.token_expires_at):
        logger.info(
            f"Refreshing token for installation {installation.installation_id}"
        )
        new_token, expires_at = await github.get_installation_token(
            installation.installation_id
        )
        installation.access_token_encrypted = encryption.encrypt(new_token)
        installation.token_expires_at = expires_at
        await db.commit()
        return new_token

    return encryption.decrypt(installation.access_token_encrypted)


# =============================================================================
# Routes
# =============================================================================


@router.get("/callback")
async def github_callback(
    installation_id: int,
    setup_action: str,
) -> RedirectResponse:
    """Handle GitHub App installation callback.

    Called by GitHub after a user installs the GitHub App.
    Redirects to the frontend which will complete the installation
    with proper authentication.

    Args:
        installation_id: GitHub App installation ID
        setup_action: One of "install", "update", "delete"

    Returns:
        Redirect to frontend settings page
    """
    import os
    logger.info(
        f"GitHub callback: installation={installation_id}, action={setup_action}"
    )

    # Redirect to frontend to complete installation with auth
    frontend_url = os.getenv("FRONTEND_URL", "https://www.repotoire.com")
    redirect_url = f"{frontend_url}/dashboard/settings/github?installation_id={installation_id}&setup_action={setup_action}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/complete-installation")
async def complete_installation(
    installation_id: int,
    setup_action: str,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
    github: GitHubAppClient = Depends(get_github_client),
    encryption: TokenEncryption = Depends(get_token_encryption),
) -> dict:
    """Complete GitHub App installation after frontend redirect.

    Called by the frontend after receiving the GitHub callback redirect.
    Stores the installation and syncs available repositories.

    Args:
        installation_id: GitHub App installation ID
        setup_action: One of "install", "update", "delete"
        user: Authenticated user (must be in an organization)
        db: Database session
        github: GitHub API client
        encryption: Token encryption service

    Returns:
        Status message and installation info
    """
    logger.info(
        f"Completing installation: installation={installation_id}, action={setup_action}"
    )

    if setup_action == "delete":
        # Handle uninstallation
        result = await db.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.commit()
        return {"status": "deleted", "installation_id": installation_id}

    # Get organization
    org = await get_org_by_clerk_id(db, user.org_id, user.org_slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found for Clerk org_id: {user.org_id}",
        )

    # Get installation info from GitHub
    installation_info = await github.get_installation(installation_id)
    account = installation_info.get("account", {})

    # Get access token
    access_token, token_expires_at = await github.get_installation_token(
        installation_id
    )

    # Check if installation already exists by installation_id
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    installation = result.scalar_one_or_none()

    account_login = account.get("login", "")

    if installation:
        # Update existing installation
        installation.access_token_encrypted = encryption.encrypt(access_token)
        installation.token_expires_at = token_expires_at
        installation.account_login = account_login
        installation.account_type = account.get("type", "Organization")
    else:
        # Check if there's an existing installation for the same account (reinstall case)
        # This handles when user uninstalls and reinstalls - GitHub gives new installation_id
        result = await db.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.organization_id == org.id,
                GitHubInstallation.account_login == account_login,
            )
        )
        existing_for_account = result.scalars().all()

        if existing_for_account:
            # Delete old installations for this account (they have stale tokens)
            for old_install in existing_for_account:
                logger.info(f"Removing old installation {old_install.installation_id} for {account_login}")
                await db.delete(old_install)

        # Create new installation
        installation = GitHubInstallation(
            organization_id=org.id,
            installation_id=installation_id,
            account_login=account_login,
            account_type=account.get("type", "Organization"),
            access_token_encrypted=encryption.encrypt(access_token),
            token_expires_at=token_expires_at,
        )
        db.add(installation)

    await db.commit()
    await db.refresh(installation)

    # Sync repositories
    repos = await github.list_all_installation_repos(access_token)
    for repo_data in repos:
        result = await db.execute(
            select(GitHubRepository).where(
                GitHubRepository.installation_id == installation.id,
                GitHubRepository.repo_id == repo_data["id"],
            )
        )
        existing_repo = result.scalar_one_or_none()

        if not existing_repo:
            new_repo = GitHubRepository(
                installation_id=installation.id,
                repo_id=repo_data["id"],
                full_name=repo_data["full_name"],
                default_branch=repo_data.get("default_branch", "main"),
                enabled=False,  # Disabled by default
            )
            db.add(new_repo)

    await db.commit()

    return {
        "status": "success",
        "action": setup_action,
        "installation_id": installation_id,
        "account": account.get("login"),
        "repos_synced": len(repos),
    }


@router.post("/webhook")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    github: GitHubAppClient = Depends(get_github_client),
    encryption: TokenEncryption = Depends(get_token_encryption),
) -> dict:
    """Handle GitHub webhook events.

    Receives and processes GitHub events like push, pull_request,
    and installation changes. Verifies webhook signature for security.

    Args:
        request: FastAPI request with raw body
        db: Database session
        github: GitHub API client
        encryption: Token encryption service

    Returns:
        Status message
    """
    # Get raw body for signature verification
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not github.verify_webhook_signature(body, signature):
        logger.warning("Invalid webhook signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    event_type = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    logger.info(f"Received webhook: {event_type}")

    if event_type == "installation":
        await handle_installation_event(db, payload, github, encryption)
    elif event_type == "installation_repositories":
        await handle_installation_repos_event(db, payload)
    elif event_type == "push":
        await handle_push_event(db, payload)
    elif event_type == "pull_request":
        await handle_pull_request_event(db, payload)

    return {"status": "ok", "event": event_type}


async def handle_installation_event(
    db: AsyncSession,
    payload: dict,
    github: GitHubAppClient,
    encryption: TokenEncryption,
) -> None:
    """Handle installation created/deleted events."""
    action = payload.get("action")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    if action == "deleted" or action == "suspend":
        result = await db.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if action == "deleted":
                await db.delete(existing)
            else:
                existing.suspended_at = datetime.now(timezone.utc)
            await db.commit()

    elif action == "unsuspend":
        result = await db.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.suspended_at = None
            await db.commit()


async def handle_installation_repos_event(
    db: AsyncSession,
    payload: dict,
) -> None:
    """Handle repositories added/removed from installation."""
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    db_installation = result.scalar_one_or_none()
    if not db_installation:
        return

    # Handle added repositories
    repos_added = payload.get("repositories_added", [])
    for repo_data in repos_added:
        result = await db.execute(
            select(GitHubRepository).where(
                GitHubRepository.installation_id == db_installation.id,
                GitHubRepository.repo_id == repo_data["id"],
            )
        )
        if not result.scalar_one_or_none():
            new_repo = GitHubRepository(
                installation_id=db_installation.id,
                repo_id=repo_data["id"],
                full_name=repo_data["full_name"],
                default_branch="main",  # Will be updated on sync
                enabled=False,
            )
            db.add(new_repo)

    # Handle removed repositories
    repos_removed = payload.get("repositories_removed", [])
    for repo_data in repos_removed:
        result = await db.execute(
            select(GitHubRepository).where(
                GitHubRepository.installation_id == db_installation.id,
                GitHubRepository.repo_id == repo_data["id"],
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await db.delete(existing)

    await db.commit()


async def handle_push_event(db: AsyncSession, payload: dict) -> None:
    """Handle push events - triggers analysis for enabled repos."""
    repo_data = payload.get("repository", {})
    repo_id = repo_data.get("id")

    # Find the repo if it's enabled for analysis
    result = await db.execute(
        select(GitHubRepository).where(
            GitHubRepository.repo_id == repo_id,
            GitHubRepository.enabled == True,
        )
    )
    repo = result.scalar_one_or_none()

    if repo:
        # TODO: Queue analysis job via Celery
        logger.info(f"Push event for enabled repo: {repo.full_name}")


async def handle_pull_request_event(db: AsyncSession, payload: dict) -> None:
    """Handle pull request events."""
    action = payload.get("action")
    repo_data = payload.get("repository", {})
    repo_id = repo_data.get("id")

    if action in ("opened", "synchronize"):
        # Find the repo if it's enabled for analysis
        result = await db.execute(
            select(GitHubRepository).where(
                GitHubRepository.repo_id == repo_id,
                GitHubRepository.enabled == True,
            )
        )
        repo = result.scalar_one_or_none()

        if repo:
            # TODO: Queue PR analysis job via Celery
            logger.info(f"PR event for enabled repo: {repo.full_name}")


@router.get("/installations")
async def list_installations(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> list[GitHubInstallationResponse]:
    """List GitHub App installations for the current organization.

    Args:
        user: Authenticated user (must be in an organization)
        db: Database session

    Returns:
        List of GitHub installations
    """
    org = await get_org_by_clerk_id(db, user.org_id, user.org_slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    result = await db.execute(
        select(GitHubInstallation)
        .where(GitHubInstallation.organization_id == org.id)
        .options(selectinload(GitHubInstallation.repositories))
    )
    installations = result.scalars().all()

    return [
        GitHubInstallationResponse(
            id=inst.id,
            installation_id=inst.installation_id,
            account_login=inst.account_login,
            account_type=inst.account_type,
            created_at=inst.created_at,
            updated_at=inst.updated_at,
            repo_count=len(inst.repositories),
        )
        for inst in installations
    ]


@router.get("/installations/{installation_id}/repos")
async def list_repos(
    installation_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
    github: GitHubAppClient = Depends(get_github_client),
    encryption: TokenEncryption = Depends(get_token_encryption),
) -> list[GitHubRepoResponse]:
    """List repositories for a GitHub App installation.

    Syncs with GitHub API to ensure repo list is current.

    Args:
        installation_id: UUID of the installation
        user: Authenticated user (must be in an organization)
        db: Database session
        github: GitHub API client
        encryption: Token encryption service

    Returns:
        List of repositories
    """
    org = await get_org_by_clerk_id(db, user.org_id, user.org_slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Get installation
    result = await db.execute(
        select(GitHubInstallation)
        .where(
            GitHubInstallation.id == installation_id,
            GitHubInstallation.organization_id == org.id,
        )
        .options(selectinload(GitHubInstallation.repositories))
    )
    installation = result.scalar_one_or_none()

    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installation not found",
        )

    # Ensure token is fresh
    access_token = await ensure_token_fresh(db, installation, github, encryption)

    # Sync repos from GitHub
    github_repos = await github.list_all_installation_repos(access_token)
    github_repo_ids = {repo["id"] for repo in github_repos}

    # Add new repos
    existing_repo_ids = {repo.repo_id for repo in installation.repositories}
    for repo_data in github_repos:
        if repo_data["id"] not in existing_repo_ids:
            new_repo = GitHubRepository(
                installation_id=installation.id,
                repo_id=repo_data["id"],
                full_name=repo_data["full_name"],
                default_branch=repo_data.get("default_branch", "main"),
                enabled=False,
            )
            db.add(new_repo)

    # Remove repos no longer in GitHub
    for repo in installation.repositories:
        if repo.repo_id not in github_repo_ids:
            await db.delete(repo)

    await db.commit()

    # Refresh to get updated list
    await db.refresh(installation)
    result = await db.execute(
        select(GitHubRepository)
        .where(GitHubRepository.installation_id == installation.id)
        .order_by(GitHubRepository.full_name)
    )
    repos = result.scalars().all()

    return [GitHubRepoResponse.model_validate(repo) for repo in repos]


@router.post("/installations/{installation_id}/repos")
async def update_repos(
    installation_id: UUID,
    request: UpdateReposRequest,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Enable or disable repositories for analysis.

    Args:
        installation_id: UUID of the installation
        request: Repository IDs and enabled status
        user: Authenticated user (must be in an organization)
        db: Database session

    Returns:
        Number of repositories updated
    """
    org = await get_org_by_clerk_id(db, user.org_id, user.org_slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify installation belongs to org
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.id == installation_id,
            GitHubInstallation.organization_id == org.id,
        )
    )
    installation = result.scalar_one_or_none()

    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installation not found",
        )

    # Update repos
    result = await db.execute(
        select(GitHubRepository).where(
            GitHubRepository.installation_id == installation_id,
            GitHubRepository.repo_id.in_(request.repo_ids),
        )
    )
    repos = result.scalars().all()

    for repo in repos:
        repo.enabled = request.enabled

    await db.commit()

    return {"updated": len(repos), "enabled": request.enabled}


@router.post("/installations/{installation_id}/sync")
async def sync_repos(
    installation_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
    github: GitHubAppClient = Depends(get_github_client),
    encryption: TokenEncryption = Depends(get_token_encryption),
) -> dict:
    """Force sync repositories from GitHub.

    Args:
        installation_id: UUID of the installation
        user: Authenticated user (must be in an organization)
        db: Database session
        github: GitHub API client
        encryption: Token encryption service

    Returns:
        Sync status
    """
    org = await get_org_by_clerk_id(db, user.org_id, user.org_slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    result = await db.execute(
        select(GitHubInstallation)
        .where(
            GitHubInstallation.id == installation_id,
            GitHubInstallation.organization_id == org.id,
        )
        .options(selectinload(GitHubInstallation.repositories))
    )
    installation = result.scalar_one_or_none()

    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installation not found",
        )

    # Get fresh token
    access_token = await ensure_token_fresh(db, installation, github, encryption)

    # Fetch repos from GitHub
    github_repos = await github.list_all_installation_repos(access_token)
    github_repo_ids = {repo["id"] for repo in github_repos}
    existing_repo_ids = {repo.repo_id for repo in installation.repositories}

    added = 0
    removed = 0

    # Add new repos
    for repo_data in github_repos:
        if repo_data["id"] not in existing_repo_ids:
            new_repo = GitHubRepository(
                installation_id=installation.id,
                repo_id=repo_data["id"],
                full_name=repo_data["full_name"],
                default_branch=repo_data.get("default_branch", "main"),
                enabled=False,
            )
            db.add(new_repo)
            added += 1

    # Remove repos no longer accessible
    for repo in installation.repositories:
        if repo.repo_id not in github_repo_ids:
            await db.delete(repo)
            removed += 1

    await db.commit()

    return {
        "synced": True,
        "total_repos": len(github_repos),
        "added": added,
        "removed": removed,
    }
