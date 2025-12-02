"""Unit tests for hybrid search (REPO-243)."""

import pytest

from repotoire.ai.hybrid import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
    linear_fusion,
    fuse_results,
    escape_lucene_query,
)


class TestHybridSearchConfig:
    """Tests for HybridSearchConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = HybridSearchConfig()

        assert config.enabled is True
        assert config.alpha == 0.7
        assert config.dense_top_k == 100
        assert config.bm25_top_k == 100
        assert config.fusion_method == "rrf"
        assert config.rrf_k == 60

    def test_custom_values(self):
        """Test custom configuration values."""
        config = HybridSearchConfig(
            enabled=False,
            alpha=0.5,
            dense_top_k=50,
            bm25_top_k=50,
            fusion_method="linear",
            rrf_k=30,
        )

        assert config.enabled is False
        assert config.alpha == 0.5
        assert config.dense_top_k == 50
        assert config.bm25_top_k == 50
        assert config.fusion_method == "linear"
        assert config.rrf_k == 30


class TestReciprocalRankFusion:
    """Tests for RRF fusion algorithm."""

    def test_rrf_combines_results(self):
        """Test RRF combines results from both lists."""
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "b", "score": 5.0}]

        fused = reciprocal_rank_fusion(dense, bm25)

        assert len(fused) == 2
        assert all("score" in r for r in fused)

    def test_rrf_boosts_overlap(self):
        """Test RRF boosts results appearing in both lists."""
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "a", "score": 5.0}]

        fused = reciprocal_rank_fusion(dense, bm25)

        assert len(fused) == 1
        # Score should be higher than single-list score (1/(k+1))
        # With k=60, single list score = 1/61 ≈ 0.0164
        # Overlap should be 2/61 ≈ 0.0328
        assert fused[0]["score"] > 1 / 61

    def test_rrf_preserves_node_data(self):
        """Test RRF preserves original node data."""
        dense = [
            {
                "qualified_name": "func1",
                "name": "func1",
                "file_path": "test.py",
                "score": 0.9,
            }
        ]
        bm25 = []

        fused = reciprocal_rank_fusion(dense, bm25)

        assert len(fused) == 1
        assert fused[0]["node"]["name"] == "func1"
        assert fused[0]["node"]["file_path"] == "test.py"

    def test_rrf_handles_nested_node(self):
        """Test RRF handles nested node structure."""
        dense = [
            {
                "node": {"qualified_name": "func1", "name": "func1"},
                "score": 0.9,
            }
        ]
        bm25 = [
            {
                "node": {"qualified_name": "func2", "name": "func2"},
                "score": 5.0,
            }
        ]

        fused = reciprocal_rank_fusion(dense, bm25)

        assert len(fused) == 2
        node_names = {r["node"]["name"] for r in fused}
        assert node_names == {"func1", "func2"}

    def test_rrf_sorts_by_score(self):
        """Test RRF results are sorted by score descending."""
        dense = [
            {"qualified_name": f"func{i}", "score": 0.9 - i * 0.1}
            for i in range(5)
        ]
        bm25 = []

        fused = reciprocal_rank_fusion(dense, bm25)

        scores = [r["score"] for r in fused]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_custom_k(self):
        """Test RRF with custom k parameter."""
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = []

        # Lower k = steeper score decay
        fused_k30 = reciprocal_rank_fusion(dense, bm25, k=30)
        fused_k90 = reciprocal_rank_fusion(dense, bm25, k=90)

        # k=30: score = 1/31, k=90: score = 1/91
        assert fused_k30[0]["score"] > fused_k90[0]["score"]

    def test_rrf_empty_inputs(self):
        """Test RRF handles empty inputs."""
        fused = reciprocal_rank_fusion([], [])
        assert fused == []

    def test_rrf_handles_missing_id(self):
        """Test RRF skips results without qualified_name."""
        dense = [
            {"qualified_name": "a", "score": 0.9},
            {"name": "no_id", "score": 0.8},  # Missing qualified_name
        ]
        bm25 = []

        fused = reciprocal_rank_fusion(dense, bm25)

        # Only result with qualified_name should be included
        assert len(fused) == 1
        assert fused[0]["node"]["qualified_name"] == "a"


class TestLinearFusion:
    """Tests for linear interpolation fusion."""

    def test_linear_combines_results(self):
        """Test linear fusion combines results from both lists."""
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "b", "score": 5.0}]

        fused = linear_fusion(dense, bm25, alpha=0.7)

        assert len(fused) == 2

    def test_linear_respects_alpha(self):
        """Test linear fusion respects alpha weighting."""
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "b", "score": 5.0}]

        # alpha=1.0: only dense counts
        fused_dense_only = linear_fusion(dense, bm25, alpha=1.0)
        # alpha=0.0: only bm25 counts
        fused_bm25_only = linear_fusion(dense, bm25, alpha=0.0)

        # Dense-only: a has normalized score 1.0 * 1.0 = 1.0, b has 0
        dense_a = next(r for r in fused_dense_only if r["node"]["qualified_name"] == "a")
        dense_b = next(r for r in fused_dense_only if r["node"]["qualified_name"] == "b")
        assert dense_a["score"] > dense_b["score"]

        # BM25-only: b has normalized score 1.0 * 1.0 = 1.0, a has 0
        bm25_a = next(r for r in fused_bm25_only if r["node"]["qualified_name"] == "a")
        bm25_b = next(r for r in fused_bm25_only if r["node"]["qualified_name"] == "b")
        assert bm25_b["score"] > bm25_a["score"]

    def test_linear_normalizes_scores(self):
        """Test linear fusion normalizes scores before combining."""
        dense = [
            {"qualified_name": "a", "score": 0.9},
            {"qualified_name": "b", "score": 0.3},
        ]
        bm25 = []

        fused = linear_fusion(dense, bm25, alpha=1.0)

        # Top dense score should be 1.0 after normalization
        top_result = fused[0]
        assert top_result["score"] == pytest.approx(1.0 * 1.0, rel=0.01)

    def test_linear_empty_inputs(self):
        """Test linear fusion handles empty inputs."""
        fused = linear_fusion([], [])
        assert fused == []


class TestFuseResults:
    """Tests for fuse_results dispatcher."""

    def test_fuse_results_rrf(self):
        """Test fuse_results uses RRF when configured."""
        config = HybridSearchConfig(fusion_method="rrf")
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "b", "score": 5.0}]

        fused = fuse_results(dense, bm25, config)

        assert len(fused) == 2

    def test_fuse_results_linear(self):
        """Test fuse_results uses linear fusion when configured."""
        config = HybridSearchConfig(fusion_method="linear")
        dense = [{"qualified_name": "a", "score": 0.9}]
        bm25 = [{"qualified_name": "b", "score": 5.0}]

        fused = fuse_results(dense, bm25, config)

        assert len(fused) == 2

    def test_fuse_results_invalid_method(self):
        """Test fuse_results raises error for invalid method."""
        config = HybridSearchConfig()
        # Manually set invalid method (bypassing type checking)
        config.fusion_method = "invalid"  # type: ignore

        with pytest.raises(ValueError, match="Unknown fusion method"):
            fuse_results([], [], config)


class TestLuceneEscaping:
    """Tests for Lucene query escaping."""

    def test_escape_special_chars(self):
        """Test escaping Lucene special characters."""
        query = "foo:bar && baz"
        escaped = escape_lucene_query(query)

        # The colon and ampersands should be escaped with backslashes
        assert "\\:" in escaped
        assert "foo" in escaped
        assert "bar" in escaped
        assert "baz" in escaped

    def test_escape_all_special_chars(self):
        """Test all special characters are escaped."""
        special_chars = r'+-&|!(){}[]^"~*?:\/'
        escaped = escape_lucene_query(special_chars)

        # Each special char should have a backslash prepended
        # The result will have \\ in the string representation
        for char in special_chars:
            # Check that each special char is preceded by a backslash
            assert f"\\{char}" in escaped

    def test_escape_preserves_regular_text(self):
        """Test regular text is preserved."""
        query = "authentication function"
        escaped = escape_lucene_query(query)

        assert escaped == "authentication function"

    def test_escape_mixed_content(self):
        """Test mixed special and regular content."""
        query = "class:User && method:login"
        escaped = escape_lucene_query(query)

        # Special characters should be escaped
        assert "\\:" in escaped
        assert "\\&" in escaped
        # Regular text should be preserved
        assert "class" in escaped
        assert "User" in escaped
        assert "method" in escaped
        assert "login" in escaped
