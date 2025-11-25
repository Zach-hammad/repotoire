"""Unit tests for VotingEngine (REPO-156).

Tests for multi-detector voting and consensus calculation.
"""

import pytest

from repotoire.detectors.voting_engine import (
    VotingEngine,
    VotingStrategy,
    ConfidenceMethod,
    SeverityResolution,
    ConsensusResult,
)
from repotoire.models import Finding, Severity, CollaborationMetadata


def create_finding(
    detector: str,
    severity: Severity = Severity.MEDIUM,
    affected_files: list = None,
    affected_nodes: list = None,
    confidence: float = 0.8,
    line_start: int = None,
) -> Finding:
    """Helper to create test findings with collaboration metadata."""
    finding = Finding(
        id=f"{detector}_{affected_nodes[0] if affected_nodes else 'test'}",
        detector=detector,
        severity=severity,
        title=f"Test {detector} finding",
        description=f"Test description for {detector}",
        affected_files=affected_files or ["test.py"],
        affected_nodes=affected_nodes or ["module.TestClass"],
        line_start=line_start,
    )
    finding.collaboration_metadata = [
        CollaborationMetadata(
            detector=detector,
            confidence=confidence,
            evidence=["test_evidence"],
            tags=["test"],
        )
    ]
    return finding


class TestVotingStrategies:
    """Test different voting strategies."""

    def test_majority_strategy_consensus(self):
        """Test majority strategy achieves consensus with 2+ detectors."""
        engine = VotingEngine(strategy=VotingStrategy.MAJORITY)

        # Use same-category detectors (both security)
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        assert len(results) == 1
        assert stats["boosted_by_consensus"] == 1
        assert results[0].detector_agreement_count == 2

    def test_majority_strategy_no_consensus_single(self):
        """Test majority strategy with single detector."""
        engine = VotingEngine(strategy=VotingStrategy.MAJORITY)

        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        assert len(results) == 1
        assert stats["boosted_by_consensus"] == 0

    def test_weighted_strategy(self):
        """Test weighted strategy considers detector weights."""
        engine = VotingEngine(strategy=VotingStrategy.WEIGHTED)

        # Both linting category - RuffLintDetector has high weight (1.3), PylintDetector has 1.0
        findings = [
            create_finding("RuffLintDetector", affected_nodes=["module.Class"]),
            create_finding("PylintDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        assert len(results) == 1
        # Combined weight: 1.3 + 1.0 = 2.3 >= 2.0 threshold
        assert results[0].detector_agreement_count == 2

    def test_threshold_strategy(self):
        """Test threshold strategy filters low confidence."""
        engine = VotingEngine(
            strategy=VotingStrategy.THRESHOLD,
            confidence_threshold=0.8
        )

        findings = [
            create_finding("BanditDetector", confidence=0.9, affected_nodes=["module.A"]),
            create_finding("SemgrepDetector", confidence=0.5, affected_nodes=["module.B"]),
        ]

        results, stats = engine.vote(findings)

        # Only high confidence finding should pass
        assert len(results) == 1
        assert stats["rejected_low_confidence"] == 1

    def test_unanimous_strategy(self):
        """Test unanimous strategy requires all detectors to agree."""
        engine = VotingEngine(strategy=VotingStrategy.UNANIMOUS)

        # Same category (security), same entity - consensus
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        assert len(results) == 1
        assert results[0].detector_agreement_count == 2


class TestConfidenceMethods:
    """Test confidence calculation methods."""

    def test_average_confidence(self):
        """Test simple average confidence calculation."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.AVERAGE)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", confidence=0.8, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.6, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Average: (0.8 + 0.6) / 2 = 0.7, plus boost for 2 detectors
        assert results[0].aggregate_confidence >= 0.7

    def test_weighted_confidence(self):
        """Test weighted confidence calculation."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.WEIGHTED)

        # Same category (security) - BanditDetector (1.1) + SemgrepDetector (1.2) = 2.3 weight
        findings = [
            create_finding("BanditDetector", confidence=0.9, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.6, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # BanditDetector has weight 1.1, SemgrepDetector 1.2
        assert results[0].aggregate_confidence > 0.7

    def test_max_confidence(self):
        """Test max confidence calculation."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.MAX)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", confidence=0.9, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.5, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Max: 0.9, plus boost
        assert results[0].aggregate_confidence >= 0.9

    def test_min_confidence(self):
        """Test min (conservative) confidence calculation."""
        engine = VotingEngine(
            confidence_method=ConfidenceMethod.MIN,
            confidence_threshold=0.5  # Lower threshold to allow 0.5 confidence
        )

        # Same category (security)
        findings = [
            create_finding("BanditDetector", confidence=0.9, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.6, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Min: 0.6, plus boost for consensus
        assert 0.6 <= results[0].aggregate_confidence <= 0.8

    def test_bayesian_confidence(self):
        """Test Bayesian confidence calculation."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.BAYESIAN)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", confidence=0.8, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.8, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Bayesian should converge toward certainty with agreeing evidence
        assert results[0].aggregate_confidence >= 0.8


class TestSeverityResolution:
    """Test severity conflict resolution."""

    def test_highest_severity(self):
        """Test highest severity resolution."""
        engine = VotingEngine(severity_resolution=SeverityResolution.HIGHEST)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", severity=Severity.HIGH, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", severity=Severity.LOW, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        assert results[0].severity == Severity.HIGH

    def test_lowest_severity(self):
        """Test lowest (conservative) severity resolution."""
        engine = VotingEngine(severity_resolution=SeverityResolution.LOWEST)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", severity=Severity.HIGH, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", severity=Severity.LOW, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        assert results[0].severity == Severity.LOW

    def test_majority_severity(self):
        """Test majority severity resolution."""
        engine = VotingEngine(severity_resolution=SeverityResolution.MAJORITY)

        # Same category (coupling) - 3 detectors
        findings = [
            create_finding("ShotgunSurgeryDetector", severity=Severity.MEDIUM, affected_nodes=["module.Class"]),
            create_finding("FeatureEnvyDetector", severity=Severity.MEDIUM, affected_nodes=["module.Class"]),
            create_finding("InappropriateIntimacyDetector", severity=Severity.HIGH, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # MEDIUM appears twice, HIGH once
        assert results[0].severity == Severity.MEDIUM

    def test_weighted_severity(self):
        """Test weighted severity resolution."""
        engine = VotingEngine(severity_resolution=SeverityResolution.WEIGHTED)

        # Same category (security) - BanditDetector (1.1) + SemgrepDetector (1.2) = 2.3 weight
        findings = [
            create_finding("BanditDetector", severity=Severity.HIGH, confidence=0.95,
                          affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", severity=Severity.LOW, confidence=0.5,
                          affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # HIGH has higher weighted score
        assert results[0].severity == Severity.HIGH


class TestEntityGrouping:
    """Test entity grouping for consensus."""

    def test_same_category_same_entity_grouped(self):
        """Test findings of same category on same entity are grouped."""
        engine = VotingEngine()

        # Both security detectors on same entity - should merge
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        assert stats["multi_detector_findings"] == 1
        assert len(results) == 1

    def test_different_categories_stay_separate(self):
        """Test findings of different categories stay separate even on same entity."""
        engine = VotingEngine()

        # Different issue types on same entity - should NOT merge
        findings = [
            create_finding("GodClassDetector", affected_nodes=["module.Class"]),  # structural
            create_finding("BanditDetector", affected_nodes=["module.Class"]),    # security
        ]

        results, stats = engine.vote(findings)

        # Different categories = separate findings
        assert stats["single_detector_findings"] == 2
        assert len(results) == 2

    def test_different_entities_separate(self):
        """Test findings on different entities stay separate."""
        engine = VotingEngine()

        findings = [
            create_finding("BanditDetector", affected_nodes=["module.ClassA"]),
            create_finding("SemgrepDetector", affected_nodes=["module.ClassB"]),
        ]

        results, stats = engine.vote(findings)

        assert stats["single_detector_findings"] == 2
        assert len(results) == 2

    def test_line_proximity_same_category_grouped(self):
        """Test same-category findings within line proximity are grouped."""
        engine = VotingEngine()

        # Same category, same entity, nearby lines - should merge
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"], line_start=10),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"], line_start=15),
        ]

        results, stats = engine.vote(findings)

        # Both in line bucket 10-19, same category
        assert stats["multi_detector_findings"] == 1

    def test_distant_lines_separate(self):
        """Test findings on distant lines stay separate."""
        engine = VotingEngine()

        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"], line_start=10),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"], line_start=100),
        ]

        results, stats = engine.vote(findings)

        # Different line buckets
        assert stats["single_detector_findings"] == 2

    def test_coupling_detectors_can_merge(self):
        """Test coupling-related detectors can reach consensus."""
        engine = VotingEngine()

        findings = [
            create_finding("ShotgunSurgeryDetector", affected_nodes=["module.Class"]),
            create_finding("FeatureEnvyDetector", affected_nodes=["module.Class"]),
        ]

        results, stats = engine.vote(findings)

        # Both are coupling category
        assert stats["multi_detector_findings"] == 1


class TestConsensusBoost:
    """Test confidence boost from multi-detector agreement."""

    def test_two_detectors_boost(self):
        """Test confidence boost with 2 detectors."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.AVERAGE)

        # Same category (security)
        findings = [
            create_finding("BanditDetector", confidence=0.7, affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", confidence=0.7, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Base: 0.7, boost: +0.05 for 2 detectors
        assert results[0].aggregate_confidence >= 0.75

    def test_three_detectors_higher_boost(self):
        """Test higher boost with 3 detectors."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.AVERAGE)

        # Same category (coupling) - 3 coupling detectors
        findings = [
            create_finding("ShotgunSurgeryDetector", confidence=0.7, affected_nodes=["module.Class"]),
            create_finding("FeatureEnvyDetector", confidence=0.7, affected_nodes=["module.Class"]),
            create_finding("InappropriateIntimacyDetector", confidence=0.7, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Base: 0.7, boost: +0.10 for 3 detectors (allow floating point tolerance)
        assert results[0].aggregate_confidence >= 0.79

    def test_max_boost_cap(self):
        """Test boost is capped at 20%."""
        engine = VotingEngine(confidence_method=ConfidenceMethod.AVERAGE)

        # Use coupling detectors - we have 4 in that category
        # CircularDependencyDetector, ShotgunSurgeryDetector, InappropriateIntimacyDetector, FeatureEnvyDetector
        findings = [
            create_finding("CircularDependencyDetector", confidence=0.75, affected_nodes=["module.Class"]),
            create_finding("ShotgunSurgeryDetector", confidence=0.75, affected_nodes=["module.Class"]),
            create_finding("InappropriateIntimacyDetector", confidence=0.75, affected_nodes=["module.Class"]),
            create_finding("FeatureEnvyDetector", confidence=0.75, affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        # Base: 0.75, boost for 4 detectors: +0.15 = 0.90
        assert results[0].aggregate_confidence <= 0.95


class TestMergedFinding:
    """Test merged finding creation."""

    def test_merged_finding_has_consensus_info(self):
        """Test merged finding contains consensus metadata."""
        engine = VotingEngine()

        # Use same-category detectors (security)
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        merged = results[0]
        assert "Consensus" in merged.detector
        assert merged.detector_agreement_count == 2
        assert merged.aggregate_confidence > 0
        assert "BanditDetector" in merged.merged_from
        assert "SemgrepDetector" in merged.merged_from

    def test_merged_finding_graph_context(self):
        """Test merged finding has consensus in graph_context."""
        engine = VotingEngine()

        # Use same-category detectors (security)
        findings = [
            create_finding("BanditDetector", affected_nodes=["module.Class"]),
            create_finding("SemgrepDetector", affected_nodes=["module.Class"]),
        ]

        results, _ = engine.vote(findings)

        ctx = results[0].graph_context
        assert "consensus_confidence" in ctx
        assert "detector_agreement" in ctx
        assert "contributing_detectors" in ctx


class TestStatistics:
    """Test voting statistics."""

    def test_stats_counts(self):
        """Test statistics are accurate."""
        engine = VotingEngine(confidence_threshold=0.6)

        # Same-category detectors (security) on module.A - should merge
        # Low-confidence single detector on module.B - should be rejected
        findings = [
            create_finding("BanditDetector", confidence=0.9, affected_nodes=["module.A"]),
            create_finding("SemgrepDetector", confidence=0.9, affected_nodes=["module.A"]),
            create_finding("MypyDetector", confidence=0.3, affected_nodes=["module.B"]),
        ]

        results, stats = engine.vote(findings)

        assert stats["total_input"] == 3
        assert stats["multi_detector_findings"] == 1  # BanditDetector + SemgrepDetector merged
        assert stats["single_detector_findings"] == 1  # MypyDetector alone
        assert stats["boosted_by_consensus"] == 1
        assert stats["rejected_low_confidence"] == 1  # MypyDetector rejected (0.3 < 0.6)

    def test_empty_findings(self):
        """Test with empty findings list."""
        engine = VotingEngine()

        results, stats = engine.vote([])

        assert len(results) == 0
        assert stats["total_input"] == 0
        assert stats["total_output"] == 0


class TestDetectorWeights:
    """Test detector weight configuration."""

    def test_default_weights_applied(self):
        """Test default detector weights are used."""
        engine = VotingEngine()

        # MypyDetector should have weight 1.3
        weight = engine._get_detector_weight("MypyDetector")
        assert weight == 1.3

    def test_unknown_detector_default_weight(self):
        """Test unknown detectors get default weight."""
        engine = VotingEngine()

        weight = engine._get_detector_weight("UnknownDetector")
        assert weight == 1.0

    def test_custom_weights(self):
        """Test custom detector weights."""
        from repotoire.detectors.voting_engine import DetectorWeight

        custom_weights = {
            "CustomDetector": DetectorWeight("CustomDetector", 2.0, 0.95),
            "default": DetectorWeight("default", 0.5, 0.5),
        }
        engine = VotingEngine(detector_weights=custom_weights)

        assert engine._get_detector_weight("CustomDetector") == 2.0
        assert engine._get_detector_weight("Unknown") == 0.5
