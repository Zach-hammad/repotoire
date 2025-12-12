"""Factories for Fix and FixComment models."""

from datetime import datetime, timezone
import random

import factory

from repotoire.db.models import Fix, FixComment, FixStatus, FixConfidence, FixType

from .base import AsyncSQLAlchemyFactory, generate_uuid


class FixFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Fix instances.

    Example:
        # Basic fix proposal
        fix = FixFactory.build(analysis_run_id=run.id)

        # High-confidence fix
        fix = FixFactory.build(
            analysis_run_id=run.id,
            high_confidence=True
        )

        # Applied fix
        fix = FixFactory.build(
            analysis_run_id=run.id,
            applied=True
        )
    """

    class Meta:
        model = Fix

    analysis_run_id = None  # Must be provided
    finding_id = None  # Optional

    file_path = factory.LazyFunction(lambda: f"src/module_{generate_uuid()}.py")
    line_start = factory.LazyFunction(lambda: random.randint(1, 100))
    line_end = factory.LazyAttribute(lambda o: o.line_start + random.randint(5, 20))

    original_code = factory.LazyFunction(
        lambda: f"""def process_data(data):
    result = []
    for item in data:
        if item is not None:
            result.append(item)
    return result"""
    )

    fixed_code = factory.LazyFunction(
        lambda: f"""def process_data(data: list) -> list:
    \"\"\"Process data by filtering None values.\"\"\"
    return [item for item in data if item is not None]"""
    )

    title = "Simplify function using list comprehension"
    description = "Replace explicit loop with list comprehension for better readability and performance."
    explanation = (
        "List comprehensions are more Pythonic and often faster than explicit loops. "
        "This change also adds type hints for better code documentation."
    )

    fix_type = FixType.SIMPLIFY
    confidence = FixConfidence.HIGH
    confidence_score = factory.LazyFunction(lambda: round(random.uniform(0.85, 0.99), 2))
    status = FixStatus.PENDING

    evidence = None
    validation_data = None
    applied_at = None

    class Params:
        """Traits for fix states."""

        # High confidence fix
        high_confidence = factory.Trait(
            confidence=FixConfidence.HIGH,
            confidence_score=factory.LazyFunction(lambda: round(random.uniform(0.90, 0.99), 2)),
        )

        # Medium confidence fix
        medium_confidence = factory.Trait(
            confidence=FixConfidence.MEDIUM,
            confidence_score=factory.LazyFunction(lambda: round(random.uniform(0.70, 0.89), 2)),
        )

        # Low confidence fix (needs careful review)
        low_confidence = factory.Trait(
            confidence=FixConfidence.LOW,
            confidence_score=factory.LazyFunction(lambda: round(random.uniform(0.50, 0.69), 2)),
        )

        # Approved fix
        approved = factory.Trait(status=FixStatus.APPROVED)

        # Rejected fix
        rejected = factory.Trait(status=FixStatus.REJECTED)

        # Applied fix
        applied = factory.Trait(
            status=FixStatus.APPLIED,
            applied_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Failed fix
        failed = factory.Trait(status=FixStatus.FAILED)

        # Security fix
        security = factory.Trait(
            fix_type=FixType.SECURITY,
            title="Fix SQL injection vulnerability",
            description="Replace string formatting with parameterized query.",
            confidence=FixConfidence.HIGH,
        )

        # Refactor fix
        refactor = factory.Trait(
            fix_type=FixType.REFACTOR,
            title="Extract method for better modularity",
            description="Extract repeated logic into a reusable method.",
        )

        # Type hint fix
        type_hint = factory.Trait(
            fix_type=FixType.TYPE_HINT,
            title="Add type annotations",
            description="Add type hints to improve code documentation and enable static analysis.",
        )

        # With validation data
        validated = factory.Trait(
            validation_data=factory.LazyFunction(
                lambda: {
                    "syntax_valid": True,
                    "imports_valid": True,
                    "types_valid": True,
                    "tests_passed": True,
                }
            )
        )

        # With evidence
        with_evidence = factory.Trait(
            evidence=factory.LazyFunction(
                lambda: {
                    "similar_patterns": [
                        {"file": "src/utils.py", "line": 42},
                        {"file": "src/helpers.py", "line": 18},
                    ],
                    "documentation_links": [
                        "https://docs.python.org/3/tutorial/datastructures.html#list-comprehensions"
                    ],
                    "best_practices": ["PEP 8", "Google Python Style Guide"],
                }
            )
        )


class FixCommentFactory(AsyncSQLAlchemyFactory):
    """Factory for creating FixComment instances.

    Example:
        # Basic comment
        comment = FixCommentFactory.build(fix_id=fix.id, user_id=user.id)
    """

    class Meta:
        model = FixComment

    fix_id = None  # Must be provided
    user_id = None  # Must be provided

    content = factory.Faker("paragraph", nb_sentences=2)

    class Params:
        """Traits for comment types."""

        # Approval comment
        approval = factory.Trait(
            content="Looks good! This fix improves readability and follows our coding standards."
        )

        # Rejection comment
        rejection = factory.Trait(
            content="This change would break backwards compatibility. Please consider an alternative approach."
        )

        # Question comment
        question = factory.Trait(
            content="Can you explain why this approach is better than the current implementation?"
        )
