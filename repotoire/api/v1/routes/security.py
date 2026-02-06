"""API routes for security scanning features.

This module provides endpoints for:
- Triggering secrets scanning for repositories
- Retrieving secrets scan results
- SARIF export for CI/CD integration

REPO-434: Initial implementation for secrets scanning UI.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user_or_api_key
from repotoire.db.models import (
    Organization,
    Repository,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.security.secrets_scanner import SecretsScanner

logger = get_logger(__name__)

router = APIRouter(prefix="/security", tags=["security"])


# =============================================================================
# Request/Response Models
# =============================================================================


class SecretMatchResponse(BaseModel):
    """Detected secret in the codebase."""

    secret_type: str = Field(description="Type of secret detected (e.g., 'AWS Access Key')")
    file_path: str = Field(description="Path to the file containing the secret")
    line_number: int = Field(description="Line number where the secret was found")
    risk_level: str = Field(description="Risk level: critical, high, medium, or low")
    remediation: str = Field(description="Suggested remediation action")
    plugin_name: str = Field(description="Detection plugin that found the secret")


class ScanSecretsRequest(BaseModel):
    """Request to scan a repository for secrets."""

    repository_id: UUID = Field(
        ...,
        description="UUID of the repository to scan",
    )
    patterns: List[str] = Field(
        default=["**/*.py", "**/*.js", "**/*.ts", "**/*.json", "**/*.yml", "**/*.yaml", "**/*.env*"],
        description="Glob patterns for files to scan",
    )
    min_risk: str = Field(
        default="low",
        description="Minimum risk level to report: critical, high, medium, or low",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "repository_id": "550e8400-e29b-41d4-a716-446655440000",
                "patterns": ["**/*.py", "**/*.env*"],
                "min_risk": "medium",
            }
        }
    }


class ScanSecretsResponse(BaseModel):
    """Response from a secrets scan."""

    repository_id: UUID = Field(description="Repository that was scanned")
    repository_name: str = Field(description="Repository full name")
    scanned_at: datetime = Field(description="When the scan was performed")
    total_files_scanned: int = Field(description="Number of files scanned")
    total_secrets_found: int = Field(description="Total number of secrets found")
    by_risk_level: dict[str, int] = Field(description="Count of secrets by risk level")
    by_type: dict[str, int] = Field(description="Count of secrets by type")
    secrets: List[SecretMatchResponse] = Field(description="List of detected secrets")


class SecretsOverviewResponse(BaseModel):
    """Overview of secrets in a repository without full details."""

    repository_id: UUID = Field(description="Repository ID")
    repository_name: str = Field(description="Repository full name")
    total_secrets: int = Field(description="Total number of secrets found")
    by_risk_level: dict[str, int] = Field(description="Count by risk level")
    last_scanned_at: Optional[datetime] = Field(description="When last scanned")


# =============================================================================
# Helper Functions
# =============================================================================


def _filter_by_risk_level(secrets: List[SecretMatchResponse], min_risk: str) -> List[SecretMatchResponse]:
    """Filter secrets by minimum risk level."""
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    min_order = risk_order.get(min_risk.lower(), 3)
    return [s for s in secrets if risk_order.get(s.risk_level.lower(), 3) <= min_order]


def _generate_sarif(scan_result: ScanSecretsResponse) -> dict[str, Any]:
    """Generate SARIF format output from scan results.

    SARIF (Static Analysis Results Interchange Format) is a standard format
    for static analysis tool outputs, used by GitHub Code Scanning and other tools.

    See: https://sarifweb.azurewebsites.net/
    """
    rules = {}
    results = []

    for secret in scan_result.secrets:
        # Create rule if not exists
        rule_id = f"secrets/{secret.secret_type.lower().replace(' ', '-')}"
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": secret.secret_type,
                "shortDescription": {"text": f"Detected {secret.secret_type}"},
                "fullDescription": {"text": secret.remediation},
                "defaultConfiguration": {
                    "level": "error" if secret.risk_level in ["critical", "high"] else "warning"
                },
                "properties": {
                    "security-severity": {
                        "critical": "9.0",
                        "high": "7.0",
                        "medium": "5.0",
                        "low": "3.0",
                    }.get(secret.risk_level.lower(), "5.0"),
                    "tags": ["security", "secrets"],
                },
            }

        # Add result
        results.append({
            "ruleId": rule_id,
            "level": "error" if secret.risk_level in ["critical", "high"] else "warning",
            "message": {
                "text": f"{secret.secret_type} detected. {secret.remediation}",
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": secret.file_path,
                        },
                        "region": {
                            "startLine": secret.line_number,
                        },
                    },
                }
            ],
            "properties": {
                "riskLevel": secret.risk_level,
                "detectedBy": secret.plugin_name,
            },
        })

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Repotoire Secrets Scanner",
                        "version": "1.0.0",
                        "informationUri": "https://repotoire.io/docs/secrets-scanning",
                        "rules": list(rules.values()),
                    },
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "endTimeUtc": scan_result.scanned_at.isoformat(),
                    }
                ],
            }
        ],
    }


async def _verify_repository_access(
    repository_id: UUID,
    user: ClerkUser,
    session: AsyncSession,
) -> Repository:
    """Verify user has access to the repository and return it."""
    # Get user's organizations
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

        # Check repository belongs to user's org
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


async def _clone_and_scan_repository(
    repo: Repository,
    patterns: List[str],
) -> tuple[List[SecretMatchResponse], int, dict[str, int], dict[str, int]]:
    """Clone repository and scan for secrets.

    Returns tuple of (secrets, files_scanned, by_risk_level, by_type).
    """
    import fnmatch
    import os
    import subprocess

    from repotoire.integrations.github import get_installation_token

    secrets: List[SecretMatchResponse] = []
    files_scanned = 0
    by_risk_level: dict[str, int] = {}
    by_type: dict[str, int] = {}

    # Get GitHub installation token
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

    # Create temp directory and clone
    with tempfile.TemporaryDirectory(prefix="repotoire-secrets-") as temp_dir:
        clone_dir = Path(temp_dir) / "repo"

        # Clone repository with shallow depth for speed
        clone_url = f"https://x-access-token:{token}@github.com/{repo.full_name}.git"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, str(clone_dir)],
                check=True,
                capture_output=True,
                timeout=120,
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

        # Collect files matching patterns
        files_to_scan: List[Path] = []
        for root, dirs, files in os.walk(clone_dir):
            # Skip .git directory
            if ".git" in dirs:
                dirs.remove(".git")

            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(clone_dir)

                # Check if file matches any pattern
                for pattern in patterns:
                    if fnmatch.fnmatch(str(rel_path), pattern):
                        files_to_scan.append(file_path)
                        break

        # Scan files
        scanner = SecretsScanner(
            entropy_detection=True,
            cache_enabled=True,
        )

        # Scan in parallel for better performance
        results = await asyncio.to_thread(
            scanner.scan_files_parallel, files_to_scan
        )

        for file_path_str, result in results.items():
            files_scanned += 1

            if result.has_secrets:
                rel_path = Path(file_path_str).relative_to(clone_dir)

                for match in result.secrets_found:
                    secrets.append(
                        SecretMatchResponse(
                            secret_type=match.secret_type,
                            file_path=str(rel_path),
                            line_number=match.line_number,
                            risk_level=match.risk_level,
                            remediation=match.remediation,
                            plugin_name=match.plugin_name,
                        )
                    )

                    # Update stats
                    by_risk_level[match.risk_level] = by_risk_level.get(match.risk_level, 0) + 1
                    # Normalize entropy-based types for counting
                    type_key = "High Entropy String" if match.secret_type.startswith("High Entropy") else match.secret_type
                    by_type[type_key] = by_type.get(type_key, 0) + 1

    return secrets, files_scanned, by_risk_level, by_type


# =============================================================================
# API Endpoints
# =============================================================================


@router.post("/scan-secrets", response_model=ScanSecretsResponse)
async def scan_secrets(
    request: ScanSecretsRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    session: AsyncSession = Depends(get_db),
) -> ScanSecretsResponse:
    """Scan a repository for hardcoded secrets.

    Scans the repository for:
    - API keys (AWS, GCP, Azure, GitHub, OpenAI, Stripe, etc.)
    - Passwords and tokens
    - Private keys
    - Database connection strings
    - High-entropy strings (potential secrets)
    - Custom patterns

    **Rate Limits:** Subject to standard API rate limits.

    **Required Permissions:** Must have access to the repository through organization membership.
    """
    # Verify repository access
    repo = await _verify_repository_access(request.repository_id, user, session)

    # Clone and scan
    secrets, files_scanned, by_risk_level, by_type = await _clone_and_scan_repository(
        repo, request.patterns
    )

    # Filter by minimum risk level
    filtered_secrets = _filter_by_risk_level(secrets, request.min_risk)

    # Recalculate stats for filtered secrets
    filtered_by_risk: dict[str, int] = {}
    filtered_by_type: dict[str, int] = {}
    for s in filtered_secrets:
        filtered_by_risk[s.risk_level] = filtered_by_risk.get(s.risk_level, 0) + 1
        type_key = "High Entropy String" if s.secret_type.startswith("High Entropy") else s.secret_type
        filtered_by_type[type_key] = filtered_by_type.get(type_key, 0) + 1

    return ScanSecretsResponse(
        repository_id=repo.id,
        repository_name=repo.full_name,
        scanned_at=datetime.now(timezone.utc),
        total_files_scanned=files_scanned,
        total_secrets_found=len(filtered_secrets),
        by_risk_level=filtered_by_risk,
        by_type=filtered_by_type,
        secrets=filtered_secrets,
    )


@router.get("/secrets/{repository_id}", response_model=ScanSecretsResponse)
async def get_secrets(
    repository_id: UUID,
    min_risk: str = Query("low", description="Minimum risk level: critical, high, medium, or low"),
    format: str = Query("json", description="Output format: json or sarif"),
    user: ClerkUser = Depends(get_current_user_or_api_key),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Get secrets scan results for a repository.

    This endpoint performs a fresh scan of the repository. For cached results,
    use the secrets overview endpoint.

    **Formats:**
    - `json`: Standard JSON response
    - `sarif`: SARIF format for GitHub Code Scanning integration

    **Required Permissions:** Must have access to the repository through organization membership.
    """
    # Verify repository access
    repo = await _verify_repository_access(repository_id, user, session)

    # Default patterns for scanning
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.json", "**/*.yml", "**/*.yaml", "**/*.env*", "**/*.toml", "**/*.ini", "**/*.cfg"]

    # Clone and scan
    secrets, files_scanned, by_risk_level, by_type = await _clone_and_scan_repository(
        repo, patterns
    )

    # Filter by minimum risk level
    filtered_secrets = _filter_by_risk_level(secrets, min_risk)

    # Recalculate stats for filtered secrets
    filtered_by_risk: dict[str, int] = {}
    filtered_by_type: dict[str, int] = {}
    for s in filtered_secrets:
        filtered_by_risk[s.risk_level] = filtered_by_risk.get(s.risk_level, 0) + 1
        type_key = "High Entropy String" if s.secret_type.startswith("High Entropy") else s.secret_type
        filtered_by_type[type_key] = filtered_by_type.get(type_key, 0) + 1

    result = ScanSecretsResponse(
        repository_id=repo.id,
        repository_name=repo.full_name,
        scanned_at=datetime.now(timezone.utc),
        total_files_scanned=files_scanned,
        total_secrets_found=len(filtered_secrets),
        by_risk_level=filtered_by_risk,
        by_type=filtered_by_type,
        secrets=filtered_secrets,
    )

    if format.lower() == "sarif":
        sarif_output = _generate_sarif(result)
        return Response(
            content=json.dumps(sarif_output, indent=2),
            media_type="application/sarif+json",
            headers={
                "Content-Disposition": f"attachment; filename=secrets-{repository_id}.sarif.json"
            },
        )

    return JSONResponse(content=result.model_dump(mode="json"))


@router.get("/secrets/{repository_id}/sarif")
async def export_secrets_sarif(
    repository_id: UUID,
    min_risk: str = Query("low", description="Minimum risk level: critical, high, medium, or low"),
    user: ClerkUser = Depends(get_current_user_or_api_key),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export secrets scan results in SARIF format.

    SARIF (Static Analysis Results Interchange Format) is a standard format
    for uploading security findings to GitHub Code Scanning.

    **Usage with GitHub Actions:**
    ```yaml
    - name: Scan secrets
      run: |
        curl -H "Authorization: Bearer $TOKEN" \\
          "https://api.repotoire.io/api/v1/security/secrets/$REPO_ID/sarif" \\
          -o secrets.sarif

    - name: Upload SARIF
      uses: github/codeql-action/upload-sarif@v2
      with:
        sarif_file: secrets.sarif
    ```
    """
    # Verify repository access
    repo = await _verify_repository_access(repository_id, user, session)

    # Default patterns for scanning
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.json", "**/*.yml", "**/*.yaml", "**/*.env*", "**/*.toml", "**/*.ini", "**/*.cfg"]

    # Clone and scan
    secrets, files_scanned, by_risk_level, by_type = await _clone_and_scan_repository(
        repo, patterns
    )

    # Filter by minimum risk level
    filtered_secrets = _filter_by_risk_level(secrets, min_risk)

    # Recalculate stats for filtered secrets
    filtered_by_risk: dict[str, int] = {}
    filtered_by_type: dict[str, int] = {}
    for s in filtered_secrets:
        filtered_by_risk[s.risk_level] = filtered_by_risk.get(s.risk_level, 0) + 1
        type_key = "High Entropy String" if s.secret_type.startswith("High Entropy") else s.secret_type
        filtered_by_type[type_key] = filtered_by_type.get(type_key, 0) + 1

    result = ScanSecretsResponse(
        repository_id=repo.id,
        repository_name=repo.full_name,
        scanned_at=datetime.now(timezone.utc),
        total_files_scanned=files_scanned,
        total_secrets_found=len(filtered_secrets),
        by_risk_level=filtered_by_risk,
        by_type=filtered_by_type,
        secrets=filtered_secrets,
    )

    sarif_output = _generate_sarif(result)
    return Response(
        content=json.dumps(sarif_output, indent=2),
        media_type="application/sarif+json",
        headers={
            "Content-Disposition": f"attachment; filename=secrets-{repository_id}.sarif.json"
        },
    )
