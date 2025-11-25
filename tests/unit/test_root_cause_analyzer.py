"""Unit tests for RootCauseAnalyzer (REPO-155).

Tests for root cause identification and cascading issue detection.
"""

import pytest

from repotoire.detectors.root_cause_analyzer import RootCauseAnalyzer, RootCauseAnalysis
from repotoire.models import Finding, Severity, CollaborationMetadata


@pytest.fixture
def analyzer():
    """Create a RootCauseAnalyzer instance."""
    return RootCauseAnalyzer()


def create_finding(
    detector: str,
    severity: Severity = Severity.MEDIUM,
    affected_files: list = None,
    affected_nodes: list = None,
    graph_context: dict = None,
    finding_id: str = None,
) -> Finding:
    """Helper to create test findings."""
    return Finding(
        id=finding_id or f"{detector}_{affected_files[0] if affected_files else 'test'}",
        detector=detector,
        severity=severity,
        title=f"Test {detector} finding",
        description=f"Test description for {detector}",
        affected_files=affected_files or [],
        affected_nodes=affected_nodes or [],
        graph_context=graph_context or {},
    )


class TestGodClassCascade:
    """Test god class cascade detection (REPO-155)."""

    def test_god_class_causes_circular_dependency(self, analyzer):
        """Test detecting god class that causes circular dependency."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/god_class.py"],
            affected_nodes=["module.GodClass"],
            graph_context={"name": "GodClass", "method_count": 30},
        )

        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/god_class.py", "src/other.py"],
            affected_nodes=["module.GodClass", "module.Other"],
        )

        findings = [god_class, circ_dep]
        enriched = analyzer.analyze(findings)

        # God class should be identified as root cause
        god_class_finding = next(f for f in enriched if f.detector == "GodClassDetector")
        assert god_class_finding.graph_context.get("is_root_cause") is True
        assert god_class_finding.graph_context.get("root_cause_type") == "god_class"
        assert god_class_finding.graph_context.get("cascading_count") >= 1

    def test_god_class_causes_multiple_issues(self, analyzer):
        """Test god class causing multiple cascading issues."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/manager.py"],
            affected_nodes=["module.Manager"],
            graph_context={"name": "Manager", "method_count": 50},
        )

        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/manager.py", "src/service.py"],
            affected_nodes=["module.Manager", "module.Service"],
        )

        shotgun = create_finding(
            detector="ShotgunSurgeryDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/manager.py"],
            affected_nodes=["module.Manager"],
        )

        intimacy = create_finding(
            detector="InappropriateIntimacyDetector",
            severity=Severity.LOW,
            affected_files=["src/manager.py"],
            affected_nodes=["module.Manager", "module.Helper"],
        )

        findings = [god_class, circ_dep, shotgun, intimacy]
        enriched = analyzer.analyze(findings)

        god_class_finding = next(f for f in enriched if f.detector == "GodClassDetector")
        assert god_class_finding.graph_context.get("is_root_cause") is True
        assert god_class_finding.graph_context.get("cascading_count") >= 2

    def test_cascading_issue_marked(self, analyzer):
        """Test that cascading issues reference their root cause."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/god.py"],
            affected_nodes=["module.God"],
            graph_context={"name": "God"},
        )

        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/god.py", "src/dep.py"],
        )

        findings = [god_class, circ_dep]
        enriched = analyzer.analyze(findings)

        circ_dep_finding = next(f for f in enriched if f.detector == "CircularDependencyDetector")
        assert circ_dep_finding.graph_context.get("caused_by_root_cause") is True
        assert circ_dep_finding.graph_context.get("root_cause_detector") == "GodClassDetector"
        assert "ROOT CAUSE" in circ_dep_finding.description


class TestCircularDependencyRootCause:
    """Test circular dependency as root cause."""

    def test_circular_dep_causes_intimacy(self, analyzer):
        """Test circular dependency causing inappropriate intimacy."""
        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.HIGH,
            affected_files=["src/a.py", "src/b.py"],
            affected_nodes=["module.A", "module.B"],
            graph_context={"cycle_length": 2},
        )

        intimacy = create_finding(
            detector="InappropriateIntimacyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/a.py", "src/b.py"],
            affected_nodes=["module.A", "module.B"],
        )

        findings = [circ_dep, intimacy]
        enriched = analyzer.analyze(findings)

        circ_finding = next(f for f in enriched if f.detector == "CircularDependencyDetector")
        assert circ_finding.graph_context.get("is_root_cause") is True
        assert circ_finding.graph_context.get("root_cause_type") == "circular_dependency"


class TestImpactScoring:
    """Test impact score calculation."""

    def test_critical_severity_high_impact(self, analyzer):
        """Test that critical findings have higher impact."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.CRITICAL,
            affected_files=["src/core.py"],
            affected_nodes=["module.Core"],
            graph_context={"name": "Core"},
        )

        cascading = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.HIGH,
            affected_files=["src/core.py"],
        )

        findings = [god_class, cascading]
        analyzer.analyze(findings)

        analyses = analyzer.get_analyses()
        assert len(analyses) == 1
        assert analyses[0].impact_score >= 4.0  # Critical base score

    def test_more_cascading_higher_impact(self, analyzer):
        """Test that more cascading issues increase impact."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/big.py"],
            affected_nodes=["module.Big"],
            graph_context={"name": "Big"},
        )

        cascading1 = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/big.py"],
        )

        cascading2 = create_finding(
            detector="ShotgunSurgeryDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/big.py"],
            affected_nodes=["module.Big"],
        )

        cascading3 = create_finding(
            detector="InappropriateIntimacyDetector",
            severity=Severity.LOW,
            affected_files=["src/big.py"],
            affected_nodes=["module.Big"],
        )

        findings = [god_class, cascading1, cascading2, cascading3]
        analyzer.analyze(findings)

        analyses = analyzer.get_analyses()
        assert len(analyses) == 1
        # More cascading issues = higher count bonus
        assert analyses[0].estimated_resolved_count >= 3


class TestPriorityCalculation:
    """Test refactoring priority calculation."""

    def test_critical_finding_critical_priority(self, analyzer):
        """Test that critical findings get critical priority."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.CRITICAL,
            affected_files=["src/main.py"],
            affected_nodes=["module.Main"],
            graph_context={"name": "Main"},
        )

        cascading = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/main.py"],
        )

        findings = [god_class, cascading]
        analyzer.analyze(findings)

        analyses = analyzer.get_analyses()
        assert analyses[0].refactoring_priority == "CRITICAL"

    def test_many_cascading_high_priority(self, analyzer):
        """Test that many cascading issues result in high priority."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/util.py"],
            affected_nodes=["module.Util"],
            graph_context={"name": "Util"},
        )

        # 3+ cascading issues should trigger HIGH priority
        cascading = [
            create_finding(
                detector="CircularDependencyDetector",
                severity=Severity.LOW,
                affected_files=["src/util.py"],
                finding_id=f"circ_{i}",
            )
            for i in range(3)
        ]

        findings = [god_class] + cascading
        analyzer.analyze(findings)

        analyses = analyzer.get_analyses()
        assert analyses[0].refactoring_priority in ("HIGH", "CRITICAL")


class TestSuggestedApproach:
    """Test refactoring suggestion generation."""

    def test_god_class_refactoring_suggestion(self, analyzer):
        """Test god class refactoring suggestions."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/big.py"],
            affected_nodes=["module.BigClass"],
            graph_context={"name": "BigClass", "method_count": 30},
        )

        cascading = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/big.py"],
        )

        findings = [god_class, cascading]
        enriched = analyzer.analyze(findings)

        god_finding = next(f for f in enriched if f.detector == "GodClassDetector")
        assert "ROOT CAUSE" in god_finding.suggested_fix
        assert "BigClass" in god_finding.suggested_fix
        assert "Split" in god_finding.suggested_fix or "Extract" in god_finding.suggested_fix

    def test_circular_dep_refactoring_suggestion(self, analyzer):
        """Test circular dependency refactoring suggestions."""
        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.HIGH,
            affected_files=["src/a.py", "src/b.py"],
            graph_context={"cycle_length": 2},
        )

        intimacy = create_finding(
            detector="InappropriateIntimacyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/a.py", "src/b.py"],
        )

        findings = [circ_dep, intimacy]
        enriched = analyzer.analyze(findings)

        circ_finding = next(f for f in enriched if f.detector == "CircularDependencyDetector")
        assert "ROOT CAUSE" in circ_finding.suggested_fix
        # Short cycles suggest merging or extracting shared types
        assert "merge" in circ_finding.suggested_fix.lower() or "extract" in circ_finding.suggested_fix.lower()


class TestSummary:
    """Test summary statistics."""

    def test_summary_counts(self, analyzer):
        """Test summary statistics are accurate."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/g.py"],
            affected_nodes=["module.G"],
            graph_context={"name": "G"},
        )

        cascading = [
            create_finding(
                detector="CircularDependencyDetector",
                severity=Severity.MEDIUM,
                affected_files=["src/g.py"],
                finding_id=f"circ_{i}",
            )
            for i in range(2)
        ]

        findings = [god_class] + cascading
        analyzer.analyze(findings)

        summary = analyzer.get_summary()
        assert summary["total_root_causes"] == 1
        assert summary["total_cascading_issues"] >= 1
        assert summary["root_causes_by_type"]["god_class"] == 1

    def test_empty_findings(self, analyzer):
        """Test with no findings."""
        enriched = analyzer.analyze([])

        assert enriched == []
        summary = analyzer.get_summary()
        assert summary["total_root_causes"] == 0
        assert summary["total_cascading_issues"] == 0


class TestNoFalsePositives:
    """Test that unrelated findings are not linked."""

    def test_unrelated_god_class_and_circ_dep(self, analyzer):
        """Test that god class and circular dep in different files are not linked."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/module_a/god.py"],
            affected_nodes=["module_a.God"],
            graph_context={"name": "God"},
        )

        circ_dep = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/module_b/x.py", "src/module_b/y.py"],
            affected_nodes=["module_b.X", "module_b.Y"],
        )

        findings = [god_class, circ_dep]
        enriched = analyzer.analyze(findings)

        god_finding = next(f for f in enriched if f.detector == "GodClassDetector")
        circ_finding = next(f for f in enriched if f.detector == "CircularDependencyDetector")

        # God class should NOT be marked as root cause for unrelated circular dep
        assert god_finding.graph_context.get("cascading_count", 0) == 0 or god_finding.graph_context.get("is_root_cause") is not True
        assert circ_finding.graph_context.get("caused_by_root_cause") is not True

    def test_standalone_findings(self, analyzer):
        """Test findings without cascading relationships."""
        standalone_god = create_finding(
            detector="GodClassDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/alone.py"],
            affected_nodes=["module.Alone"],
            graph_context={"name": "Alone"},
        )

        findings = [standalone_god]
        enriched = analyzer.analyze(findings)

        assert len(enriched) == 1
        assert enriched[0].graph_context.get("is_root_cause") is not True


class TestCollaborationMetadata:
    """Test collaboration metadata updates."""

    def test_root_cause_gets_tag(self, analyzer):
        """Test that root cause findings get appropriate tags."""
        god_class = create_finding(
            detector="GodClassDetector",
            severity=Severity.HIGH,
            affected_files=["src/core.py"],
            affected_nodes=["module.Core"],
            graph_context={"name": "Core"},
        )
        god_class.collaboration_metadata = CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["high_method_count"],
            tags=["god_class"],
        )

        cascading = create_finding(
            detector="CircularDependencyDetector",
            severity=Severity.MEDIUM,
            affected_files=["src/core.py"],
        )
        cascading.collaboration_metadata = CollaborationMetadata(
            detector="CircularDependencyDetector",
            confidence=0.8,
            evidence=["cycle_detected"],
            tags=["circular"],
        )

        findings = [god_class, cascading]
        enriched = analyzer.analyze(findings)

        god_finding = next(f for f in enriched if f.detector == "GodClassDetector")
        assert "root_cause" in god_finding.collaboration_metadata.tags

        circ_finding = next(f for f in enriched if f.detector == "CircularDependencyDetector")
        assert "cascading_issue" in circ_finding.collaboration_metadata.tags
