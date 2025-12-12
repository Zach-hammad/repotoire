"""Webhook payload documentation schemas for OpenAPI.

These schemas document the webhook payloads sent to customer-configured
endpoints. They are not used for request validation - they serve as
documentation in the OpenAPI spec.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WebhookPayloadBase(BaseModel):
    """Base webhook payload with common fields.

    All webhook payloads include these fields for consistent handling.
    """

    event: str = Field(..., description="Event type identifier")
    timestamp: datetime = Field(..., description="When the event occurred (ISO 8601)")
    webhook_id: str = Field(..., description="Unique ID for this webhook delivery")
    organization_id: str = Field(..., description="Organization that triggered the event")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "analysis.completed",
                "timestamp": "2025-01-15T10:35:00Z",
                "webhook_id": "whd_abc123def456",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
            }
        }
    }


class AnalysisStartedData(BaseModel):
    """Data payload for analysis.started event."""

    analysis_run_id: str = Field(..., description="Unique ID of the analysis run")
    repository_id: str = Field(..., description="Repository being analyzed")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    commit_sha: str = Field(..., description="Git commit SHA being analyzed")
    branch: str = Field(..., description="Git branch name")
    triggered_by: str = Field(..., description="How analysis was triggered: 'push', 'pr', 'manual', 'schedule'")
    triggered_by_user: Optional[str] = Field(None, description="User who triggered (if manual)")


class AnalysisStartedPayload(WebhookPayloadBase):
    """Payload for analysis.started webhook event.

    Sent when an analysis job begins processing.
    """

    event: Literal["analysis.started"] = "analysis.started"
    data: AnalysisStartedData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "analysis.started",
                "timestamp": "2025-01-15T10:30:00Z",
                "webhook_id": "whd_abc123def456",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "commit_sha": "abc123def456789",
                    "branch": "main",
                    "triggered_by": "push",
                    "triggered_by_user": None,
                },
            }
        }
    }


class AnalysisCompletedData(BaseModel):
    """Data payload for analysis.completed event."""

    analysis_run_id: str = Field(..., description="Unique ID of the analysis run")
    repository_id: str = Field(..., description="Repository that was analyzed")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    commit_sha: str = Field(..., description="Git commit SHA that was analyzed")
    branch: str = Field(..., description="Git branch name")
    health_score: int = Field(..., description="Overall health score (0-100)", ge=0, le=100)
    structure_score: int = Field(..., description="Code structure score (0-100)", ge=0, le=100)
    quality_score: int = Field(..., description="Code quality score (0-100)", ge=0, le=100)
    architecture_score: int = Field(..., description="Architecture score (0-100)", ge=0, le=100)
    findings_count: int = Field(..., description="Total findings detected", ge=0)
    critical_count: int = Field(..., description="Critical severity findings", ge=0)
    high_count: int = Field(..., description="High severity findings", ge=0)
    files_analyzed: int = Field(..., description="Number of files processed", ge=0)
    duration_seconds: int = Field(..., description="Analysis duration in seconds", ge=0)
    dashboard_url: str = Field(..., description="URL to view results in dashboard")


class AnalysisCompletedPayload(WebhookPayloadBase):
    """Payload for analysis.completed webhook event.

    Sent when an analysis finishes successfully.
    """

    event: Literal["analysis.completed"] = "analysis.completed"
    data: AnalysisCompletedData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "analysis.completed",
                "timestamp": "2025-01-15T10:35:00Z",
                "webhook_id": "whd_def456ghi789",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "commit_sha": "abc123def456789",
                    "branch": "main",
                    "health_score": 78,
                    "structure_score": 82,
                    "quality_score": 75,
                    "architecture_score": 77,
                    "findings_count": 42,
                    "critical_count": 2,
                    "high_count": 8,
                    "files_analyzed": 156,
                    "duration_seconds": 285,
                    "dashboard_url": "https://app.repotoire.io/org/acme/repo/backend/analysis/550e8400",
                },
            }
        }
    }


class AnalysisFailedData(BaseModel):
    """Data payload for analysis.failed event."""

    analysis_run_id: str = Field(..., description="Unique ID of the analysis run")
    repository_id: str = Field(..., description="Repository that was being analyzed")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    commit_sha: str = Field(..., description="Git commit SHA that was being analyzed")
    branch: str = Field(..., description="Git branch name")
    error_code: str = Field(..., description="Machine-readable error code")
    error_message: str = Field(..., description="Human-readable error description")
    failed_at_step: Optional[str] = Field(None, description="Which processing step failed")


class AnalysisFailedPayload(WebhookPayloadBase):
    """Payload for analysis.failed webhook event.

    Sent when an analysis encounters an error and cannot complete.
    """

    event: Literal["analysis.failed"] = "analysis.failed"
    data: AnalysisFailedData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "analysis.failed",
                "timestamp": "2025-01-15T10:32:00Z",
                "webhook_id": "whd_ghi789jkl012",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "commit_sha": "abc123def456789",
                    "branch": "main",
                    "error_code": "CLONE_FAILED",
                    "error_message": "Failed to clone repository: authentication required",
                    "failed_at_step": "repository_clone",
                },
            }
        }
    }


class HealthScoreChangedData(BaseModel):
    """Data payload for health_score.changed event."""

    repository_id: str = Field(..., description="Repository whose score changed")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    previous_score: int = Field(..., description="Previous health score", ge=0, le=100)
    new_score: int = Field(..., description="New health score", ge=0, le=100)
    change: int = Field(..., description="Score change (positive = improvement)")
    analysis_run_id: str = Field(..., description="Analysis that caused the change")


class HealthScoreChangedPayload(WebhookPayloadBase):
    """Payload for health_score.changed webhook event.

    Sent when a repository's health score changes significantly (>= 5 points).
    """

    event: Literal["health_score.changed"] = "health_score.changed"
    data: HealthScoreChangedData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "health_score.changed",
                "timestamp": "2025-01-15T10:35:00Z",
                "webhook_id": "whd_jkl012mno345",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "previous_score": 72,
                    "new_score": 78,
                    "change": 6,
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                },
            }
        }
    }


class FindingNewData(BaseModel):
    """Data payload for finding.new event."""

    finding_id: str = Field(..., description="Unique ID of the new finding")
    analysis_run_id: str = Field(..., description="Analysis that detected the finding")
    repository_id: str = Field(..., description="Repository containing the finding")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    detector: str = Field(..., description="Detector that found the issue")
    severity: str = Field(..., description="Severity level: critical, high, medium, low, info")
    title: str = Field(..., description="Short description of the finding")
    file_path: str = Field(..., description="Primary affected file")
    line_start: Optional[int] = Field(None, description="Starting line number")
    line_end: Optional[int] = Field(None, description="Ending line number")
    dashboard_url: str = Field(..., description="URL to view finding in dashboard")


class FindingNewPayload(WebhookPayloadBase):
    """Payload for finding.new webhook event.

    Sent when a new finding is detected that wasn't present in previous analysis.
    Only sent for high and critical severity findings by default.
    """

    event: Literal["finding.new"] = "finding.new"
    data: FindingNewData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "finding.new",
                "timestamp": "2025-01-15T10:35:00Z",
                "webhook_id": "whd_mno345pqr678",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "detector": "bandit",
                    "severity": "high",
                    "title": "Hardcoded password detected",
                    "file_path": "src/config.py",
                    "line_start": 42,
                    "line_end": 42,
                    "dashboard_url": "https://app.repotoire.io/org/acme/repo/backend/findings/770e8400",
                },
            }
        }
    }


class FindingResolvedData(BaseModel):
    """Data payload for finding.resolved event."""

    finding_id: str = Field(..., description="Unique ID of the resolved finding")
    analysis_run_id: str = Field(..., description="Analysis that confirmed resolution")
    repository_id: str = Field(..., description="Repository that contained the finding")
    repository_name: str = Field(..., description="Full repository name (owner/repo)")
    detector: str = Field(..., description="Detector that originally found the issue")
    severity: str = Field(..., description="Severity level of the resolved finding")
    title: str = Field(..., description="Short description of the finding")
    resolved_by: Optional[str] = Field(None, description="Commit SHA that resolved the finding")


class FindingResolvedPayload(WebhookPayloadBase):
    """Payload for finding.resolved webhook event.

    Sent when a previously detected finding is no longer present in the codebase.
    """

    event: Literal["finding.resolved"] = "finding.resolved"
    data: FindingResolvedData = Field(..., description="Event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "finding.resolved",
                "timestamp": "2025-01-15T10:35:00Z",
                "webhook_id": "whd_pqr678stu901",
                "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
                "data": {
                    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
                    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                    "repository_name": "acme/backend",
                    "detector": "bandit",
                    "severity": "high",
                    "title": "Hardcoded password detected",
                    "resolved_by": "def789ghi012345",
                },
            }
        }
    }
