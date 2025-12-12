"""Factory for AnalysisRun model."""

from datetime import datetime, timezone
import random
import secrets

import factory

from repotoire.db.models import AnalysisRun, AnalysisStatus

from .base import AsyncSQLAlchemyFactory


class AnalysisRunFactory(AsyncSQLAlchemyFactory):
    """Factory for creating AnalysisRun instances.

    Example:
        # Basic analysis run
        run = AnalysisRunFactory.build(repository_id=repo.id)

        # Completed analysis
        run = AnalysisRunFactory.build(
            repository_id=repo.id,
            completed=True
        )

        # Failed analysis
        run = AnalysisRunFactory.build(
            repository_id=repo.id,
            failed=True
        )
    """

    class Meta:
        model = AnalysisRun

    repository_id = None  # Must be provided

    commit_sha = factory.LazyFunction(lambda: secrets.token_hex(20))
    branch = "main"
    status = AnalysisStatus.QUEUED

    # Score fields - None until completed
    health_score = None
    structure_score = None
    quality_score = None
    architecture_score = None
    score_delta = None

    findings_count = 0
    files_analyzed = 0
    progress_percent = 0
    current_step = None

    triggered_by_id = None
    started_at = None
    completed_at = None
    error_message = None

    class Params:
        """Traits for analysis states."""

        # Running analysis
        running = factory.Trait(
            status=AnalysisStatus.RUNNING,
            started_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            progress_percent=factory.LazyFunction(lambda: random.randint(10, 90)),
            current_step="Analyzing code patterns...",
        )

        # Completed analysis with scores
        completed = factory.Trait(
            status=AnalysisStatus.COMPLETED,
            started_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            progress_percent=100,
            current_step=None,
            health_score=factory.LazyFunction(lambda: random.randint(60, 95)),
            structure_score=factory.LazyFunction(lambda: random.randint(60, 95)),
            quality_score=factory.LazyFunction(lambda: random.randint(60, 95)),
            architecture_score=factory.LazyFunction(lambda: random.randint(60, 95)),
            findings_count=factory.LazyFunction(lambda: random.randint(5, 50)),
            files_analyzed=factory.LazyFunction(lambda: random.randint(50, 500)),
        )

        # Failed analysis
        failed = factory.Trait(
            status=AnalysisStatus.FAILED,
            started_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            error_message="Analysis failed: unable to clone repository",
        )

        # Analysis with score improvement
        improved = factory.Trait(
            status=AnalysisStatus.COMPLETED,
            started_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            health_score=factory.LazyFunction(lambda: random.randint(75, 95)),
            score_delta=factory.LazyFunction(lambda: random.randint(1, 10)),
        )

        # Analysis with score regression
        regressed = factory.Trait(
            status=AnalysisStatus.COMPLETED,
            started_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            completed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            health_score=factory.LazyFunction(lambda: random.randint(40, 70)),
            score_delta=factory.LazyFunction(lambda: random.randint(-15, -1)),
        )

        # PR analysis (feature branch)
        pr_analysis = factory.Trait(
            branch=factory.LazyFunction(lambda: f"feature/test-{secrets.token_hex(4)}"),
        )
