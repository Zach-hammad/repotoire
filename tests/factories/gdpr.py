"""Factories for GDPR models: DataExport and ConsentRecord."""

from datetime import datetime, timedelta, timezone

import factory

from repotoire.db.models import DataExport, ExportStatus, ConsentRecord, ConsentType

from .base import AsyncSQLAlchemyFactory, generate_uuid


class DataExportFactory(AsyncSQLAlchemyFactory):
    """Factory for creating DataExport instances.

    Example:
        # Pending export
        export = DataExportFactory.build(user_id=user.id)

        # Completed export with download URL
        export = DataExportFactory.build(
            user_id=user.id,
            completed=True
        )

        # Failed export
        export = DataExportFactory.build(
            user_id=user.id,
            failed=True
        )
    """

    class Meta:
        model = DataExport

    user_id = None  # Must be provided

    status = ExportStatus.PENDING
    download_url = None
    expires_at = factory.LazyFunction(
        lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )
    completed_at = None
    error_message = None
    file_size_bytes = None

    class Params:
        """Traits for export states."""

        # Processing export
        processing = factory.Trait(status=ExportStatus.PROCESSING)

        # Completed export
        completed = factory.Trait(
            status=ExportStatus.COMPLETED,
            download_url=factory.LazyFunction(
                lambda: f"https://storage.example.com/exports/{generate_uuid()}.json"
            ),
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            file_size_bytes=factory.LazyFunction(lambda: 1024 * 50),  # ~50KB
        )

        # Failed export
        failed = factory.Trait(
            status=ExportStatus.FAILED,
            error_message="Failed to generate export: database timeout",
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Expired export
        expired = factory.Trait(
            status=ExportStatus.EXPIRED,
            expires_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=1)
            ),
        )


class ConsentRecordFactory(AsyncSQLAlchemyFactory):
    """Factory for creating ConsentRecord instances.

    Example:
        # Analytics consent granted
        consent = ConsentRecordFactory.build(
            user_id=user.id,
            consent_type=ConsentType.ANALYTICS,
            granted=True
        )

        # Marketing consent revoked
        consent = ConsentRecordFactory.build(
            user_id=user.id,
            marketing_revoked=True
        )
    """

    class Meta:
        model = ConsentRecord

    user_id = None  # Must be provided

    consent_type = ConsentType.ANALYTICS
    granted = True
    ip_address = factory.Faker("ipv4")
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    class Params:
        """Traits for consent types and states."""

        # Essential consent (always required)
        essential = factory.Trait(
            consent_type=ConsentType.ESSENTIAL,
            granted=True,
        )

        # Analytics consent granted
        analytics_granted = factory.Trait(
            consent_type=ConsentType.ANALYTICS,
            granted=True,
        )

        # Analytics consent revoked
        analytics_revoked = factory.Trait(
            consent_type=ConsentType.ANALYTICS,
            granted=False,
        )

        # Marketing consent granted
        marketing_granted = factory.Trait(
            consent_type=ConsentType.MARKETING,
            granted=True,
        )

        # Marketing consent revoked
        marketing_revoked = factory.Trait(
            consent_type=ConsentType.MARKETING,
            granted=False,
        )

        # No tracking info (privacy-conscious)
        anonymous = factory.Trait(
            ip_address=None,
            user_agent=None,
        )
