"""API documentation schemas for OpenAPI spec."""

from repotoire.api.docs.webhooks import (
    AnalysisCompletedPayload,
    AnalysisFailedPayload,
    AnalysisStartedPayload,
    FindingNewPayload,
    FindingResolvedPayload,
    HealthScoreChangedPayload,
    WebhookPayloadBase,
)

__all__ = [
    "WebhookPayloadBase",
    "AnalysisStartedPayload",
    "AnalysisCompletedPayload",
    "AnalysisFailedPayload",
    "HealthScoreChangedPayload",
    "FindingNewPayload",
    "FindingResolvedPayload",
]
