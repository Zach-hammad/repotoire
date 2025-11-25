"""Unit tests for BottleneckRiskAnalyzer (REPO-154).

Tests cross-detector risk amplification where bottlenecks combined with
high complexity and security vulnerabilities escalate to CRITICAL severity.
"""

from typing import List
from unittest.mock import Mock

import pytest

from repotoire.detectors.risk_analyzer import (
    BottleneckRiskAnalyzer,
    RiskAssessment,
    RiskFactor,
    analyze_compound_risks,
)
from repotoire.models import CollaborationMetadata, Finding, Severity


@pytest.fixture
def bottleneck_finding() -> Finding:
    """Create a basic bottleneck finding."""
    finding = Finding(
        id="bottleneck-001",
        detector="ArchitecturalBottleneckDetector",
        severity=Severity.MEDIUM,
        title="Architectural bottleneck: mymodule.CriticalClass.process",
        description="High betweenness centrality detected",
        affected_nodes=["mymodule.CriticalClass.process"],
        affected_files=["/mymodule.py"],
        graph_context={
            "betweenness_score": 0.85,
            "complexity": 10,
            "percentile": 95.0,
            "confidence": 0.9,
        },
    )
    return finding


@pytest.fixture
def complexity_finding() -> Finding:
    """Create a radon complexity finding for the same entity."""
    return Finding(
        id="complexity-001",
        detector="RadonDetector",
        severity=Severity.HIGH,
        title="High complexity: mymodule.CriticalClass.process",
        description="Cyclomatic complexity is too high",
        affected_nodes=["mymodule.CriticalClass.process"],
        affected_files=["/mymodule.py"],
        graph_context={
            "complexity": 25,
            "rank": "C",
            "line": 42,
        },
    )


@pytest.fixture
def security_finding() -> Finding:
    """Create a bandit security finding for the same entity."""
    return Finding(
        id="security-001",
        detector="BanditDetector",
        severity=Severity.HIGH,
        title="SQL Injection vulnerability",
        description="Possible SQL injection via string formatting",
        affected_nodes=["mymodule.CriticalClass.process"],
        affected_files=["/mymodule.py"],
        graph_context={
            "test_id": "B608",
            "issue_text": "Possible SQL injection via string formatting",
            "confidence": 0.85,
        },
    )


class TestRiskFactor:
    """Tests for RiskFactor dataclass."""

    def test_create_risk_factor(self):
        """Test creating a risk factor."""
        factor = RiskFactor(
            factor_type="bottleneck",
            detector="ArchitecturalBottleneckDetector",
            severity=Severity.MEDIUM,
            confidence=0.9,
            evidence=["high_betweenness"],
        )
        assert factor.factor_type == "bottleneck"
        assert factor.confidence == 0.9
        assert "high_betweenness" in factor.evidence

    def test_risk_factor_with_finding_id(self):
        """Test risk factor with related finding ID."""
        factor = RiskFactor(
            factor_type="security_vulnerability",
            detector="BanditDetector",
            severity=Severity.HIGH,
            confidence=0.85,
            evidence=["B608"],
            finding_id="security-001",
        )
        assert factor.finding_id == "security-001"


class TestRiskAssessment:
    """Tests for RiskAssessment dataclass."""

    def test_create_assessment(self):
        """Test creating a risk assessment."""
        assessment = RiskAssessment(
            entity="mymodule.CriticalClass.process",
            original_severity=Severity.MEDIUM,
            escalated_severity=Severity.HIGH,
            risk_score=0.75,
        )
        assert assessment.entity == "mymodule.CriticalClass.process"
        assert assessment.risk_score == 0.75

    def test_is_critical_risk_true(self):
        """Test is_critical_risk when multiple factors present."""
        assessment = RiskAssessment(
            entity="test",
            original_severity=Severity.MEDIUM,
            escalated_severity=Severity.CRITICAL,
            risk_factors=[
                RiskFactor("bottleneck", "Det1", Severity.MEDIUM, 0.9, []),
                RiskFactor("high_complexity", "Det2", Severity.HIGH, 0.9, []),
            ],
        )
        assert assessment.is_critical_risk is True

    def test_is_critical_risk_false_single_factor(self):
        """Test is_critical_risk with single factor."""
        assessment = RiskAssessment(
            entity="test",
            original_severity=Severity.MEDIUM,
            escalated_severity=Severity.MEDIUM,
            risk_factors=[
                RiskFactor("bottleneck", "Det1", Severity.MEDIUM, 0.9, []),
            ],
        )
        assert assessment.is_critical_risk is False

    def test_factor_types(self):
        """Test getting unique factor types."""
        assessment = RiskAssessment(
            entity="test",
            risk_factors=[
                RiskFactor("bottleneck", "Det1", Severity.MEDIUM, 0.9, []),
                RiskFactor("high_complexity", "Det2", Severity.HIGH, 0.9, []),
                RiskFactor("security_vulnerability", "Det3", Severity.HIGH, 0.8, []),
            ],
        )
        factor_types = assessment.factor_types
        assert "bottleneck" in factor_types
        assert "high_complexity" in factor_types
        assert "security_vulnerability" in factor_types


class TestBottleneckRiskAnalyzer:
    """Tests for BottleneckRiskAnalyzer."""

    def test_init_default_thresholds(self):
        """Test default threshold initialization."""
        analyzer = BottleneckRiskAnalyzer()
        assert analyzer.complexity_threshold == 15
        assert analyzer.security_severity_threshold == Severity.MEDIUM

    def test_init_custom_thresholds(self):
        """Test custom threshold initialization."""
        analyzer = BottleneckRiskAnalyzer(
            complexity_threshold=20,
            security_severity_threshold=Severity.HIGH,
        )
        assert analyzer.complexity_threshold == 20
        assert analyzer.security_severity_threshold == Severity.HIGH

    def test_analyze_bottleneck_only(self, bottleneck_finding: Finding):
        """Test analysis with bottleneck only - no escalation."""
        analyzer = BottleneckRiskAnalyzer()
        findings, assessments = analyzer.analyze([bottleneck_finding])

        assert len(findings) == 1
        assert len(assessments) == 1
        # No escalation when no other factors
        assert findings[0].severity == Severity.MEDIUM
        assert assessments[0].escalated_severity == Severity.MEDIUM

    def test_analyze_bottleneck_plus_complexity(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
    ):
        """Test severity escalation with bottleneck + complexity."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
        )

        assert len(findings) == 1
        assert len(assessments) == 1
        # Should escalate by 1 level (MEDIUM -> HIGH)
        assert findings[0].severity == Severity.HIGH
        assert assessments[0].escalated_severity == Severity.HIGH
        assert "high_complexity" in assessments[0].factor_types

    def test_analyze_bottleneck_plus_security(
        self,
        bottleneck_finding: Finding,
        security_finding: Finding,
    ):
        """Test severity escalation with bottleneck + security."""
        analyzer = BottleneckRiskAnalyzer()
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            bandit_findings=[security_finding],
        )

        assert len(findings) == 1
        # Should escalate by 1 level (MEDIUM -> HIGH)
        assert findings[0].severity == Severity.HIGH
        assert "security_vulnerability" in assessments[0].factor_types

    def test_analyze_compound_risk_critical(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
        security_finding: Finding,
    ):
        """Test CRITICAL escalation with bottleneck + complexity + security."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
            bandit_findings=[security_finding],
        )

        assert len(findings) == 1
        assert len(assessments) == 1
        # Should escalate to CRITICAL with 3 factors
        assert findings[0].severity == Severity.CRITICAL
        assert assessments[0].is_critical_risk is True
        assert len(assessments[0].factor_types) == 3

    def test_analyze_adds_collaboration_metadata(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
    ):
        """Test that collaboration metadata is added from contributing detectors."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, _ = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
        )

        assert len(findings) == 1
        # Should have metadata from RadonDetector
        metadata_detectors = [m.detector for m in findings[0].collaboration_metadata]
        assert "RadonDetector" in metadata_detectors

    def test_analyze_updates_description_for_critical(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
        security_finding: Finding,
    ):
        """Test that description is updated for critical compound risks."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, _ = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
            bandit_findings=[security_finding],
        )

        assert "CRITICAL COMPOUND RISK" in findings[0].description
        assert "Risk factors:" in findings[0].description

    def test_analyze_generates_mitigation_plan(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
        security_finding: Finding,
    ):
        """Test that mitigation plan is generated."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
            bandit_findings=[security_finding],
        )

        # Should have mitigation plan in suggested_fix
        assert "URGENT" in findings[0].suggested_fix  # Security first
        assert "coupling" in findings[0].suggested_fix.lower()  # Bottleneck
        assert "complexity" in findings[0].suggested_fix.lower()  # Complexity

        # Assessment should also have plan
        assert len(assessments[0].mitigation_plan) > 0

    def test_analyze_updates_graph_context(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
    ):
        """Test that graph_context is updated with risk info."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, _ = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
        )

        ctx = findings[0].graph_context
        assert "risk_score" in ctx
        assert "risk_factors" in ctx
        assert "is_compound_risk" in ctx
        assert ctx["is_compound_risk"] is True

    def test_analyze_empty_findings(self):
        """Test analysis with no findings."""
        analyzer = BottleneckRiskAnalyzer()
        findings, assessments = analyzer.analyze([])

        assert len(findings) == 0
        assert len(assessments) == 0

    def test_analyze_no_matching_entities(
        self,
        bottleneck_finding: Finding,
    ):
        """Test when complexity/security findings don't match bottleneck."""
        # Create findings for different entity
        other_complexity = Finding(
            id="complexity-other",
            detector="RadonDetector",
            severity=Severity.HIGH,
            title="High complexity: other.OtherClass.method",
            description="Complexity issue",
            affected_nodes=["other.OtherClass.method"],
            affected_files=["/other.py"],
            graph_context={"complexity": 30},
        )

        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[other_complexity],
        )

        # Should not escalate since entities don't match
        assert findings[0].severity == Severity.MEDIUM
        assert len(assessments[0].factor_types) == 1  # Only bottleneck

    def test_analyze_matches_by_file(self, bottleneck_finding: Finding):
        """Test that findings match by file path as well as node."""
        # Create complexity finding matching by file only
        file_complexity = Finding(
            id="complexity-file",
            detector="RadonDetector",
            severity=Severity.HIGH,
            title="High complexity",
            description="Complexity issue",
            affected_nodes=["mymodule.OtherClass.method"],  # Different node
            affected_files=["/mymodule.py"],  # Same file
            graph_context={"complexity": 30},
        )

        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[file_complexity],
        )

        # Should match by file and escalate
        assert findings[0].severity == Severity.HIGH
        assert "high_complexity" in assessments[0].factor_types

    def test_complexity_below_threshold_not_counted(
        self,
        bottleneck_finding: Finding,
    ):
        """Test that complexity below threshold is not counted as risk factor."""
        low_complexity = Finding(
            id="complexity-low",
            detector="RadonDetector",
            severity=Severity.LOW,
            title="Moderate complexity",
            description="Acceptable complexity",
            affected_nodes=["mymodule.CriticalClass.process"],
            affected_files=["/mymodule.py"],
            graph_context={"complexity": 10},  # Below threshold
        )

        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[low_complexity],
        )

        # Should not escalate since complexity below threshold
        assert findings[0].severity == Severity.MEDIUM
        assert "high_complexity" not in assessments[0].factor_types

    def test_security_below_threshold_not_counted(
        self,
        bottleneck_finding: Finding,
    ):
        """Test that low-severity security issues are not counted."""
        low_security = Finding(
            id="security-low",
            detector="BanditDetector",
            severity=Severity.LOW,  # Below MEDIUM threshold
            title="Minor security issue",
            description="Low severity issue",
            affected_nodes=["mymodule.CriticalClass.process"],
            affected_files=["/mymodule.py"],
            graph_context={"test_id": "B101"},
        )

        analyzer = BottleneckRiskAnalyzer(security_severity_threshold=Severity.MEDIUM)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            bandit_findings=[low_security],
        )

        # Should not escalate since security severity below threshold
        assert findings[0].severity == Severity.MEDIUM
        assert "security_vulnerability" not in assessments[0].factor_types

    def test_risk_score_calculation(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
        security_finding: Finding,
    ):
        """Test that risk score is calculated properly."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        _, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
            bandit_findings=[security_finding],
        )

        # Risk score should be between 0 and 1
        assert 0.0 <= assessments[0].risk_score <= 1.0
        # With 3 high-severity factors, score should be substantial
        assert assessments[0].risk_score > 0.5

    def test_multiple_bottlenecks(self):
        """Test analysis with multiple bottleneck findings."""
        bottleneck1 = Finding(
            id="bottleneck-001",
            detector="ArchitecturalBottleneckDetector",
            severity=Severity.MEDIUM,
            title="Bottleneck 1",
            description="First bottleneck",
            affected_nodes=["module1.Class1.method"],
            affected_files=["/module1.py"],
            graph_context={"confidence": 0.9},
        )
        bottleneck2 = Finding(
            id="bottleneck-002",
            detector="ArchitecturalBottleneckDetector",
            severity=Severity.HIGH,
            title="Bottleneck 2",
            description="Second bottleneck",
            affected_nodes=["module2.Class2.method"],
            affected_files=["/module2.py"],
            graph_context={"confidence": 0.85},
        )
        complexity1 = Finding(
            id="complexity-001",
            detector="RadonDetector",
            severity=Severity.HIGH,
            title="Complexity 1",
            description="First complexity",
            affected_nodes=["module1.Class1.method"],
            affected_files=["/module1.py"],
            graph_context={"complexity": 25},
        )

        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck1, bottleneck2],
            radon_findings=[complexity1],
        )

        assert len(findings) == 2
        assert len(assessments) == 2
        # First bottleneck should be escalated (matching complexity)
        assert findings[0].severity == Severity.HIGH
        # Second bottleneck should not be escalated (no matching factors)
        assert findings[1].severity == Severity.HIGH  # Original was HIGH


class TestAnalyzeCompoundRisks:
    """Tests for the convenience function."""

    def test_separates_findings_by_detector(self):
        """Test that findings are properly separated by detector type."""
        bottleneck = Finding(
            id="b1",
            detector="ArchitecturalBottleneckDetector",
            severity=Severity.MEDIUM,
            title="Bottleneck",
            description="",
            affected_nodes=["test"],
            affected_files=["/test.py"],
            graph_context={"confidence": 0.9},
        )
        complexity = Finding(
            id="c1",
            detector="RadonDetector",
            severity=Severity.HIGH,
            title="Complexity",
            description="",
            affected_nodes=["test"],
            affected_files=["/test.py"],
            graph_context={"complexity": 25},
        )
        security = Finding(
            id="s1",
            detector="BanditDetector",
            severity=Severity.HIGH,
            title="Security",
            description="",
            affected_nodes=["test"],
            affected_files=["/test.py"],
            graph_context={},
        )

        findings, assessments = analyze_compound_risks(
            [bottleneck, complexity, security],
            complexity_threshold=15,
        )

        # Should have analyzed bottleneck with others as context
        assert len(findings) == 1
        assert len(assessments) == 1
        assert assessments[0].escalated_severity == Severity.CRITICAL

    def test_handles_empty_list(self):
        """Test handling of empty findings list."""
        findings, assessments = analyze_compound_risks([])
        assert len(findings) == 0
        assert len(assessments) == 0


class TestMitigationPlanGeneration:
    """Tests for mitigation plan generation."""

    def test_security_first_priority(
        self,
        bottleneck_finding: Finding,
        security_finding: Finding,
    ):
        """Test that security issues are prioritized in mitigation plan."""
        analyzer = BottleneckRiskAnalyzer()
        _, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            bandit_findings=[security_finding],
        )

        plan = assessments[0].mitigation_plan
        # Security should be first (excluding critical warning)
        security_idx = next(i for i, p in enumerate(plan) if "security" in p.lower())
        coupling_idx = next(i for i, p in enumerate(plan) if "coupling" in p.lower())
        assert security_idx < coupling_idx

    def test_critical_warning_included(
        self,
        bottleneck_finding: Finding,
        complexity_finding: Finding,
        security_finding: Finding,
    ):
        """Test that critical compound risk warning is included."""
        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        _, assessments = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity_finding],
            bandit_findings=[security_finding],
        )

        plan = assessments[0].mitigation_plan
        # Critical warning should be first
        assert "CRITICAL" in plan[0]
        assert "compound" in plan[0].lower()


class TestSeverityEscalation:
    """Tests for severity escalation logic."""

    def test_severity_order(self):
        """Test that severity order is correct."""
        analyzer = BottleneckRiskAnalyzer()
        order = analyzer.SEVERITY_ORDER
        assert order.index(Severity.INFO) < order.index(Severity.LOW)
        assert order.index(Severity.LOW) < order.index(Severity.MEDIUM)
        assert order.index(Severity.MEDIUM) < order.index(Severity.HIGH)
        assert order.index(Severity.HIGH) < order.index(Severity.CRITICAL)

    def test_escalation_caps_at_critical(self, bottleneck_finding: Finding):
        """Test that escalation doesn't go beyond CRITICAL."""
        # Start with HIGH severity
        bottleneck_finding.severity = Severity.HIGH

        complexity = Finding(
            id="c1",
            detector="RadonDetector",
            severity=Severity.HIGH,
            title="Complexity",
            description="",
            affected_nodes=["mymodule.CriticalClass.process"],
            affected_files=["/mymodule.py"],
            graph_context={"complexity": 30},
        )
        security = Finding(
            id="s1",
            detector="BanditDetector",
            severity=Severity.HIGH,
            title="Security",
            description="",
            affected_nodes=["mymodule.CriticalClass.process"],
            affected_files=["/mymodule.py"],
            graph_context={},
        )

        analyzer = BottleneckRiskAnalyzer(complexity_threshold=15)
        findings, _ = analyzer.analyze(
            bottleneck_findings=[bottleneck_finding],
            radon_findings=[complexity],
            bandit_findings=[security],
        )

        # Should be CRITICAL, not beyond
        assert findings[0].severity == Severity.CRITICAL
