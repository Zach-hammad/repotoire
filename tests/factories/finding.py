"""Factory for Finding model."""

import random

import factory

from repotoire.db.models import Finding, FindingSeverity

from .base import AsyncSQLAlchemyFactory, generate_uuid


class FindingFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Finding instances.

    Example:
        # Basic finding
        finding = FindingFactory.build(analysis_run_id=run.id)

        # Critical security finding
        finding = FindingFactory.build(
            analysis_run_id=run.id,
            critical=True
        )

        # Finding with fix suggestion
        finding = FindingFactory.build(
            analysis_run_id=run.id,
            with_fix=True
        )
    """

    class Meta:
        model = Finding

    analysis_run_id = None  # Must be provided

    detector = factory.LazyFunction(
        lambda: random.choice([
            # Hybrid detectors (external tools)
            "ruff",        # General linting
            "pylint",      # Specialized checks
            "mypy",        # Type checking
            "bandit",      # Security
            "radon",       # Complexity metrics
            "jscpd",       # Duplicate code
            "vulture",     # Dead code
            "semgrep",     # Advanced security (OWASP)
            # Graph-based detectors
            "graph:circular_dependency",
            "graph:modularity",
            "graph:coupling",
        ])
    )
    severity = FindingSeverity.MEDIUM
    title = factory.LazyFunction(
        lambda: f"Code issue detected in module {generate_uuid()}"
    )
    description = factory.Faker("paragraph", nb_sentences=3)
    affected_files = factory.LazyFunction(
        lambda: [f"src/module_{generate_uuid()}.py"]
    )
    affected_nodes = factory.LazyFunction(
        lambda: [f"module_{generate_uuid()}.ClassName.method_name"]
    )
    line_start = factory.LazyFunction(lambda: random.randint(1, 100))
    line_end = factory.LazyAttribute(lambda o: o.line_start + random.randint(1, 20))
    suggested_fix = None
    estimated_effort = None
    graph_context = None

    class Params:
        """Traits for finding states."""

        # Critical severity finding
        critical = factory.Trait(
            severity=FindingSeverity.CRITICAL,
            detector="security_vulnerability",
            title="Critical security vulnerability detected",
            description="A critical security vulnerability was found that requires immediate attention.",
            estimated_effort="Medium (4-8 hours)",
        )

        # High severity finding
        high = factory.Trait(
            severity=FindingSeverity.HIGH,
            estimated_effort="Medium (4-8 hours)",
        )

        # Low severity finding
        low = factory.Trait(
            severity=FindingSeverity.LOW,
            estimated_effort="Small (1-2 hours)",
        )

        # Info-level finding
        info = factory.Trait(
            severity=FindingSeverity.INFO,
            title="Style suggestion",
            estimated_effort="Trivial (<1 hour)",
        )

        # Finding with suggested fix
        with_fix = factory.Trait(
            suggested_fix=factory.Faker("paragraph", nb_sentences=2),
            estimated_effort="Small (2-4 hours)",
        )

        # Circular dependency finding (graph detector)
        circular_dependency = factory.Trait(
            detector="graph:circular_dependency",
            title="Circular import dependency detected",
            description="A circular import was detected between modules, which can cause import errors and makes the code harder to maintain.",
            affected_files=factory.LazyFunction(
                lambda: [
                    f"src/module_a_{generate_uuid()}.py",
                    f"src/module_b_{generate_uuid()}.py",
                ]
            ),
            graph_context=factory.LazyFunction(
                lambda: {
                    "cycle_path": ["module_a", "module_b", "module_a"],
                    "cycle_length": 2,
                }
            ),
        )

        # Dead code finding (vulture detector)
        dead_code = factory.Trait(
            detector="vulture",
            title="Unused function detected",
            description="This function is never called anywhere in the codebase and can be safely removed.",
            severity=FindingSeverity.LOW,
        )

        # Complex function finding (radon detector)
        complex_function = factory.Trait(
            detector="radon",
            title="High cyclomatic complexity",
            description="This function has a cyclomatic complexity of 25, which exceeds the recommended threshold of 10.",
            severity=FindingSeverity.MEDIUM,
            graph_context=factory.LazyFunction(
                lambda: {
                    "complexity": random.randint(15, 30),
                    "threshold": 10,
                }
            ),
        )

        # Ruff linting finding
        ruff = factory.Trait(
            detector="ruff",
            title="Linting issue detected",
            description="A code style or quality issue was detected by the ruff linter.",
            severity=FindingSeverity.LOW,
        )

        # Security finding (bandit detector)
        security = factory.Trait(
            detector="bandit",
            title="Security vulnerability detected",
            description="A potential security vulnerability was detected in the code.",
            severity=FindingSeverity.HIGH,
        )
