"""Unit tests for Factory Boy factories.

These tests verify that all factories create valid model instances
with proper default values and traits.
"""

import pytest
from datetime import datetime, timezone
from uuid import UUID


class TestUserFactory:
    """Tests for UserFactory."""

    def test_creates_valid_user(self):
        """UserFactory should create a valid User instance."""
        from tests.factories import UserFactory

        user = UserFactory.build()

        assert user.clerk_user_id is not None
        assert user.email is not None
        assert "@" in user.email
        assert user.name is not None
        assert user.deleted_at is None
        assert user.anonymized_at is None

    def test_pending_deletion_trait(self):
        """pending_deletion trait should set deletion_requested_at."""
        from tests.factories import UserFactory

        user = UserFactory.build(pending_deletion=True)

        assert user.deletion_requested_at is not None
        assert user.deleted_at is None

    def test_anonymized_trait(self):
        """anonymized trait should anonymize user data."""
        from tests.factories import UserFactory

        user = UserFactory.build(anonymized=True)

        assert "anonymized" in user.email
        assert user.name == "Deleted User"
        assert user.avatar_url is None
        assert user.deleted_at is not None
        assert user.anonymized_at is not None

    def test_unique_clerk_ids(self):
        """Each user should have a unique clerk_user_id."""
        from tests.factories import UserFactory

        users = [UserFactory.build() for _ in range(10)]
        clerk_ids = [u.clerk_user_id for u in users]

        assert len(clerk_ids) == len(set(clerk_ids))


class TestOrganizationFactory:
    """Tests for OrganizationFactory."""

    def test_creates_valid_organization(self):
        """OrganizationFactory should create a valid Organization instance."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import PlanTier

        org = OrganizationFactory.build()

        assert org.name is not None
        assert org.slug is not None
        assert org.plan_tier == PlanTier.FREE
        assert org.stripe_customer_id is None

    def test_pro_trait(self):
        """pro trait should set Pro tier with Stripe."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import PlanTier

        org = OrganizationFactory.build(pro=True)

        assert org.plan_tier == PlanTier.PRO
        assert org.stripe_customer_id is not None
        assert org.stripe_subscription_id is not None

    def test_enterprise_trait(self):
        """enterprise trait should set Enterprise tier."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import PlanTier

        org = OrganizationFactory.build(enterprise=True)

        assert org.plan_tier == PlanTier.ENTERPRISE

    def test_unique_slugs(self):
        """Each organization should have a unique slug."""
        from tests.factories import OrganizationFactory

        orgs = [OrganizationFactory.build() for _ in range(10)]
        slugs = [o.slug for o in orgs]

        assert len(slugs) == len(set(slugs))


class TestRepositoryFactory:
    """Tests for RepositoryFactory."""

    def test_creates_valid_repository(self):
        """RepositoryFactory should create a valid Repository instance."""
        from tests.factories import RepositoryFactory
        from uuid import uuid4

        org_id = uuid4()
        repo = RepositoryFactory.build(organization_id=org_id)

        assert repo.organization_id == org_id
        assert repo.github_repo_id is not None
        assert repo.full_name is not None
        assert repo.default_branch == "main"
        assert repo.is_active is True

    def test_analyzed_trait(self):
        """analyzed trait should set analysis results."""
        from tests.factories import RepositoryFactory
        from uuid import uuid4

        repo = RepositoryFactory.build(organization_id=uuid4(), analyzed=True)

        assert repo.last_analyzed_at is not None
        assert repo.health_score is not None
        assert 0 <= repo.health_score <= 100

    def test_inactive_trait(self):
        """inactive trait should set is_active to False."""
        from tests.factories import RepositoryFactory
        from uuid import uuid4

        repo = RepositoryFactory.build(organization_id=uuid4(), inactive=True)

        assert repo.is_active is False


class TestAnalysisRunFactory:
    """Tests for AnalysisRunFactory."""

    def test_creates_valid_analysis_run(self):
        """AnalysisRunFactory should create a valid AnalysisRun instance."""
        from tests.factories import AnalysisRunFactory
        from repotoire.db.models import AnalysisStatus
        from uuid import uuid4

        repo_id = uuid4()
        run = AnalysisRunFactory.build(repository_id=repo_id)

        assert run.repository_id == repo_id
        assert run.commit_sha is not None
        assert len(run.commit_sha) == 40  # Git SHA length
        assert run.status == AnalysisStatus.QUEUED

    def test_completed_trait(self):
        """completed trait should set all score fields."""
        from tests.factories import AnalysisRunFactory
        from repotoire.db.models import AnalysisStatus
        from uuid import uuid4

        run = AnalysisRunFactory.build(repository_id=uuid4(), completed=True)

        assert run.status == AnalysisStatus.COMPLETED
        assert run.health_score is not None
        assert run.structure_score is not None
        assert run.quality_score is not None
        assert run.architecture_score is not None
        assert run.progress_percent == 100
        assert run.completed_at is not None

    def test_failed_trait(self):
        """failed trait should set error state."""
        from tests.factories import AnalysisRunFactory
        from repotoire.db.models import AnalysisStatus
        from uuid import uuid4

        run = AnalysisRunFactory.build(repository_id=uuid4(), failed=True)

        assert run.status == AnalysisStatus.FAILED
        assert run.error_message is not None

    def test_running_trait(self):
        """running trait should set in-progress state."""
        from tests.factories import AnalysisRunFactory
        from repotoire.db.models import AnalysisStatus
        from uuid import uuid4

        run = AnalysisRunFactory.build(repository_id=uuid4(), running=True)

        assert run.status == AnalysisStatus.RUNNING
        assert run.started_at is not None
        assert 0 < run.progress_percent < 100
        assert run.current_step is not None


class TestFindingFactory:
    """Tests for FindingFactory."""

    def test_creates_valid_finding(self):
        """FindingFactory should create a valid Finding instance."""
        from tests.factories import FindingFactory
        from repotoire.db.models import FindingSeverity
        from uuid import uuid4

        run_id = uuid4()
        finding = FindingFactory.build(analysis_run_id=run_id)

        assert finding.analysis_run_id == run_id
        assert finding.detector is not None
        assert finding.severity == FindingSeverity.MEDIUM
        assert finding.title is not None
        assert len(finding.affected_files) > 0

    def test_critical_trait(self):
        """critical trait should set critical severity."""
        from tests.factories import FindingFactory
        from repotoire.db.models import FindingSeverity
        from uuid import uuid4

        finding = FindingFactory.build(analysis_run_id=uuid4(), critical=True)

        assert finding.severity == FindingSeverity.CRITICAL
        assert "security" in finding.detector

    def test_circular_dependency_trait(self):
        """circular_dependency trait should create specific finding."""
        from tests.factories import FindingFactory
        from uuid import uuid4

        finding = FindingFactory.build(analysis_run_id=uuid4(), circular_dependency=True)

        assert finding.detector == "graph:circular_dependency"
        assert finding.graph_context is not None
        assert "cycle_path" in finding.graph_context


class TestFixFactory:
    """Tests for FixFactory."""

    def test_creates_valid_fix(self):
        """FixFactory should create a valid Fix instance."""
        from tests.factories import FixFactory
        from repotoire.db.models import FixStatus, FixConfidence
        from uuid import uuid4

        run_id = uuid4()
        fix = FixFactory.build(analysis_run_id=run_id)

        assert fix.analysis_run_id == run_id
        assert fix.file_path is not None
        assert fix.original_code is not None
        assert fix.fixed_code is not None
        assert fix.status == FixStatus.PENDING
        assert fix.confidence == FixConfidence.HIGH

    def test_applied_trait(self):
        """applied trait should set applied state."""
        from tests.factories import FixFactory
        from repotoire.db.models import FixStatus
        from uuid import uuid4

        fix = FixFactory.build(analysis_run_id=uuid4(), applied=True)

        assert fix.status == FixStatus.APPLIED
        assert fix.applied_at is not None

    def test_security_trait(self):
        """security trait should create security fix."""
        from tests.factories import FixFactory
        from repotoire.db.models import FixType
        from uuid import uuid4

        fix = FixFactory.build(analysis_run_id=uuid4(), security=True)

        assert fix.fix_type == FixType.SECURITY


class TestBillingFactories:
    """Tests for billing-related factories."""

    def test_subscription_factory(self):
        """SubscriptionFactory should create valid subscription."""
        from tests.factories import SubscriptionFactory
        from repotoire.db.models import SubscriptionStatus
        from uuid import uuid4

        org_id = uuid4()
        sub = SubscriptionFactory.build(organization_id=org_id)

        assert sub.organization_id == org_id
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.stripe_subscription_id is not None

    def test_subscription_trialing_trait(self):
        """trialing trait should set trial period."""
        from tests.factories import SubscriptionFactory
        from repotoire.db.models import SubscriptionStatus
        from uuid import uuid4

        sub = SubscriptionFactory.build(organization_id=uuid4(), trialing=True)

        assert sub.status == SubscriptionStatus.TRIALING
        assert sub.trial_start is not None
        assert sub.trial_end is not None

    def test_usage_record_factory(self):
        """UsageRecordFactory should create valid usage record."""
        from tests.factories import UsageRecordFactory
        from uuid import uuid4

        org_id = uuid4()
        usage = UsageRecordFactory.build(organization_id=org_id)

        assert usage.organization_id == org_id
        assert usage.repos_count >= 0
        assert usage.analyses_count >= 0


class TestGitHubFactories:
    """Tests for GitHub-related factories."""

    def test_installation_factory(self):
        """GitHubInstallationFactory should create valid installation."""
        from tests.factories import GitHubInstallationFactory
        from uuid import uuid4

        org_id = uuid4()
        installation = GitHubInstallationFactory.build(organization_id=org_id)

        assert installation.organization_id == org_id
        assert installation.installation_id is not None
        assert installation.account_login is not None
        assert installation.access_token_encrypted is not None

    def test_github_repository_factory(self):
        """GitHubRepositoryFactory should create valid github repo."""
        from tests.factories import GitHubRepositoryFactory
        from uuid import uuid4

        installation_id = uuid4()
        repo = GitHubRepositoryFactory.build(installation_id=installation_id)

        assert repo.installation_id == installation_id
        assert repo.repo_id is not None
        assert repo.full_name is not None

    def test_github_repository_with_quality_gates(self):
        """with_quality_gates trait should configure gates."""
        from tests.factories import GitHubRepositoryFactory
        from uuid import uuid4

        repo = GitHubRepositoryFactory.build(
            installation_id=uuid4(),
            with_quality_gates=True,
        )

        assert repo.quality_gates is not None
        assert repo.quality_gates["enabled"] is True
        assert "min_health_score" in repo.quality_gates


class TestGDPRFactories:
    """Tests for GDPR-related factories."""

    def test_data_export_factory(self):
        """DataExportFactory should create valid export."""
        from tests.factories import DataExportFactory
        from repotoire.db.models import ExportStatus
        from uuid import uuid4

        user_id = uuid4()
        export = DataExportFactory.build(user_id=user_id)

        assert export.user_id == user_id
        assert export.status == ExportStatus.PENDING
        assert export.expires_at is not None

    def test_data_export_completed_trait(self):
        """completed trait should set download URL."""
        from tests.factories import DataExportFactory
        from repotoire.db.models import ExportStatus
        from uuid import uuid4

        export = DataExportFactory.build(user_id=uuid4(), completed=True)

        assert export.status == ExportStatus.COMPLETED
        assert export.download_url is not None
        assert export.completed_at is not None

    def test_consent_record_factory(self):
        """ConsentRecordFactory should create valid consent record."""
        from tests.factories import ConsentRecordFactory
        from repotoire.db.models import ConsentType
        from uuid import uuid4

        user_id = uuid4()
        consent = ConsentRecordFactory.build(user_id=user_id)

        assert consent.user_id == user_id
        assert consent.consent_type == ConsentType.ANALYTICS
        assert consent.granted is True


class TestAuditLogFactory:
    """Tests for AuditLogFactory."""

    def test_creates_valid_audit_log(self):
        """AuditLogFactory should create valid audit log."""
        from tests.factories import AuditLogFactory
        from repotoire.db.models import EventSource, AuditStatus

        log = AuditLogFactory.build()

        assert log.timestamp is not None
        assert log.event_type is not None
        assert log.event_source == EventSource.APPLICATION
        assert log.status == AuditStatus.SUCCESS

    def test_login_event_trait(self):
        """login_event trait should create Clerk login event."""
        from tests.factories import AuditLogFactory
        from repotoire.db.models import EventSource

        log = AuditLogFactory.build(login_event=True)

        assert log.event_type == "user.login"
        assert log.event_source == EventSource.CLERK
        assert log.clerk_event_id is not None

    def test_repo_connected_trait(self):
        """repo_connected trait should create repository event."""
        from tests.factories import AuditLogFactory

        log = AuditLogFactory.build(repo_connected=True)

        assert log.event_type == "repository.connected"
        assert log.resource_type == "repository"
        assert log.event_metadata is not None


class TestWebhookFactories:
    """Tests for webhook-related factories."""

    def test_webhook_factory(self):
        """WebhookFactory should create valid webhook."""
        from tests.factories import WebhookFactory
        from uuid import uuid4

        org_id = uuid4()
        webhook = WebhookFactory.build(organization_id=org_id)

        assert webhook.organization_id == org_id
        assert webhook.url is not None
        assert webhook.secret is not None
        assert len(webhook.events) > 0
        assert webhook.is_active is True

    def test_webhook_all_events_trait(self):
        """all_events trait should subscribe to all events."""
        from tests.factories import WebhookFactory
        from repotoire.db.models import WebhookEvent
        from uuid import uuid4

        webhook = WebhookFactory.build(organization_id=uuid4(), all_events=True)

        assert len(webhook.events) == len(WebhookEvent)

    def test_webhook_delivery_factory(self):
        """WebhookDeliveryFactory should create valid delivery."""
        from tests.factories import WebhookDeliveryFactory
        from repotoire.db.models import DeliveryStatus
        from uuid import uuid4

        webhook_id = uuid4()
        delivery = WebhookDeliveryFactory.build(webhook_id=webhook_id)

        assert delivery.webhook_id == webhook_id
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.payload is not None

    def test_webhook_delivery_success_trait(self):
        """success trait should set successful delivery."""
        from tests.factories import WebhookDeliveryFactory
        from repotoire.db.models import DeliveryStatus
        from uuid import uuid4

        delivery = WebhookDeliveryFactory.build(webhook_id=uuid4(), success=True)

        assert delivery.status == DeliveryStatus.SUCCESS
        assert delivery.response_status_code == 200
        assert delivery.delivered_at is not None
