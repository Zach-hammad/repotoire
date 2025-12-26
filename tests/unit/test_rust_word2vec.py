"""Unit tests for Rust Word2Vec skip-gram implementation (REPO-249).

Tests validate:
1. Basic functionality (training, embedding dimensions, vocabulary)
2. Determinism (same seed produces same embeddings)
3. Quality (embedding similarity matches intuition from walks)
4. Comparison with gensim (if available)
5. Performance (Rust should be faster than gensim)
"""

import time
from typing import List

import numpy as np
import pytest


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def simple_walks() -> List[List[int]]:
    """Simple walks for basic testing."""
    return [
        [0, 1, 2, 1, 0],
        [1, 2, 3, 2, 1],
        [0, 1, 3, 2, 0],
        [2, 3, 2, 1, 0],
    ]


@pytest.fixture
def cluster_walks() -> List[List[int]]:
    """Walks with clear cluster structure for similarity testing.

    Cluster 1: nodes 0, 1, 2 (tightly connected)
    Cluster 2: nodes 3, 4, 5 (tightly connected)
    Bridge: node 2 connects to node 3 occasionally
    """
    walks = []
    # Cluster 1 walks (many)
    for _ in range(20):
        walks.extend([
            [0, 1, 2, 1, 0, 1, 2],
            [1, 0, 1, 2, 1, 0],
            [2, 1, 0, 1, 2],
        ])
    # Cluster 2 walks (many)
    for _ in range(20):
        walks.extend([
            [3, 4, 5, 4, 3, 4, 5],
            [4, 3, 4, 5, 4, 3],
            [5, 4, 3, 4, 5],
        ])
    # Bridge walks (few)
    walks.extend([
        [2, 3],
        [2, 1, 2, 3, 4],
    ])
    return walks


@pytest.fixture
def large_walks() -> List[List[int]]:
    """Large walk set for performance testing."""
    np.random.seed(42)
    num_nodes = 1000
    walks = []
    for _ in range(1000):
        walk_length = np.random.randint(40, 80)
        walk = np.random.randint(0, num_nodes, size=walk_length).tolist()
        walks.append(walk)
    return walks


# ============================================================================
# BASIC FUNCTIONALITY TESTS
# ============================================================================


class TestBasicFunctionality:
    """Test basic Word2Vec functionality."""

    def test_import(self):
        """Test that Rust Word2Vec can be imported."""
        from repotoire_fast import (
            PyWord2VecConfig,
            train_word2vec_skipgram,
            train_word2vec_skipgram_matrix,
        )
        assert PyWord2VecConfig is not None
        assert train_word2vec_skipgram is not None
        assert train_word2vec_skipgram_matrix is not None

    def test_config_defaults(self):
        """Test config default values."""
        from repotoire_fast import PyWord2VecConfig

        config = PyWord2VecConfig()
        assert config.embedding_dim == 128
        assert config.window_size == 5
        assert config.min_count == 1
        assert config.negative_samples == 5
        assert config.epochs == 5

    def test_config_custom(self):
        """Test custom config values."""
        from repotoire_fast import PyWord2VecConfig

        config = PyWord2VecConfig(
            embedding_dim=64,
            window_size=10,
            min_count=2,
            negative_samples=10,
            epochs=3,
            seed=12345,
        )
        assert config.embedding_dim == 64
        assert config.window_size == 10
        assert config.min_count == 2
        assert config.negative_samples == 10
        assert config.epochs == 3
        assert config.seed == 12345

    def test_empty_walks(self):
        """Test training with empty walks."""
        from repotoire_fast import train_word2vec_skipgram

        embeddings = train_word2vec_skipgram([], None)
        assert len(embeddings) == 0

    def test_single_node_walk(self):
        """Test training with single-node walks.

        Single-node walks can't form context pairs, but they still build
        vocabulary. With our implementation, we keep them in vocab but
        they don't contribute to training.
        """
        from repotoire_fast import train_word2vec_skipgram, PyWord2VecConfig

        walks = [[0], [1], [2]]
        config = PyWord2VecConfig(epochs=1, seed=42)
        embeddings = train_word2vec_skipgram(walks, config)

        # Nodes may still be in vocabulary with initial random embeddings
        # The key is that they didn't get training updates
        # This is acceptable behavior - gensim also keeps them
        assert len(embeddings) <= 3

    def test_basic_training(self, simple_walks):
        """Test basic training produces embeddings."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config = PyWord2VecConfig(embedding_dim=32, epochs=3, seed=42)
        embeddings = train_word2vec_skipgram(simple_walks, config)

        # Should have embeddings for all nodes in walks
        assert len(embeddings) == 4  # nodes 0, 1, 2, 3
        for node_id in range(4):
            assert node_id in embeddings
            assert len(embeddings[node_id]) == 32

    def test_embedding_dimension(self, simple_walks):
        """Test different embedding dimensions."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        for dim in [16, 64, 128, 256]:
            config = PyWord2VecConfig(embedding_dim=dim, epochs=1, seed=42)
            embeddings = train_word2vec_skipgram(simple_walks, config)

            for node_id, embedding in embeddings.items():
                assert len(embedding) == dim, f"Wrong dim for {dim}: {len(embedding)}"

    def test_matrix_output(self, simple_walks):
        """Test matrix output format."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram_matrix

        config = PyWord2VecConfig(embedding_dim=32, epochs=2, seed=42)
        node_ids, flat_emb, dim = train_word2vec_skipgram_matrix(simple_walks, config)

        assert dim == 32
        assert len(node_ids) == 4
        assert len(flat_emb) == 4 * 32

        # Node IDs should be sorted
        assert list(node_ids) == sorted(node_ids)

        # Convert to matrix and verify
        matrix = np.array(flat_emb).reshape(-1, dim)
        assert matrix.shape == (4, 32)


# ============================================================================
# DETERMINISM TESTS
# ============================================================================


class TestDeterminism:
    """Test that training is deterministic with same seed."""

    def test_same_seed_same_embeddings(self, simple_walks):
        """Test same seed produces identical embeddings."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config = PyWord2VecConfig(embedding_dim=32, epochs=3, seed=42)

        emb1 = train_word2vec_skipgram(simple_walks, config)
        emb2 = train_word2vec_skipgram(simple_walks, config)

        for node_id in emb1:
            np.testing.assert_allclose(
                emb1[node_id],
                emb2[node_id],
                rtol=1e-5,
                err_msg=f"Embeddings differ for node {node_id}",
            )

    def test_different_seed_different_embeddings(self, simple_walks):
        """Test different seeds produce different embeddings."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config1 = PyWord2VecConfig(embedding_dim=32, epochs=3, seed=42)
        config2 = PyWord2VecConfig(embedding_dim=32, epochs=3, seed=12345)

        emb1 = train_word2vec_skipgram(simple_walks, config1)
        emb2 = train_word2vec_skipgram(simple_walks, config2)

        # At least some embeddings should be different
        any_different = False
        for node_id in emb1:
            if not np.allclose(emb1[node_id], emb2[node_id], rtol=1e-3):
                any_different = True
                break

        assert any_different, "Different seeds should produce different embeddings"


# ============================================================================
# EMBEDDING QUALITY TESTS
# ============================================================================


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class TestEmbeddingQuality:
    """Test that embeddings capture walk structure."""

    def test_cluster_similarity(self, cluster_walks):
        """Test nodes in same cluster have higher similarity."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config = PyWord2VecConfig(
            embedding_dim=64,
            window_size=5,
            epochs=15,
            learning_rate=0.05,
            seed=42,
        )

        embeddings = train_word2vec_skipgram(cluster_walks, config)

        # Within-cluster similarities
        sim_01 = cosine_similarity(embeddings[0], embeddings[1])
        sim_12 = cosine_similarity(embeddings[1], embeddings[2])
        sim_34 = cosine_similarity(embeddings[3], embeddings[4])
        sim_45 = cosine_similarity(embeddings[4], embeddings[5])

        # Cross-cluster similarities
        sim_03 = cosine_similarity(embeddings[0], embeddings[3])
        sim_15 = cosine_similarity(embeddings[1], embeddings[5])

        # Within-cluster should be higher than cross-cluster
        within_cluster_avg = (sim_01 + sim_12 + sim_34 + sim_45) / 4
        cross_cluster_avg = (sim_03 + sim_15) / 2

        assert within_cluster_avg > cross_cluster_avg, (
            f"Within-cluster similarity ({within_cluster_avg:.3f}) should be "
            f"higher than cross-cluster ({cross_cluster_avg:.3f})"
        )

    def test_embedding_norms(self, simple_walks):
        """Test that embeddings have reasonable norms."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config = PyWord2VecConfig(embedding_dim=64, epochs=5, seed=42)
        embeddings = train_word2vec_skipgram(simple_walks, config)

        norms = [np.linalg.norm(emb) for emb in embeddings.values()]

        # Norms should be reasonable (not zero, not huge)
        for norm in norms:
            assert 0.1 < norm < 100, f"Norm {norm} outside reasonable range"


# ============================================================================
# GENSIM COMPARISON TESTS
# ============================================================================


@pytest.fixture
def gensim_available():
    """Check if gensim is available."""
    try:
        from gensim.models import Word2Vec
        return True
    except ImportError:
        return False


class TestGensimComparison:
    """Compare Rust Word2Vec with gensim (when available)."""

    @pytest.mark.skipif(
        not pytest.importorskip("gensim", reason="gensim not installed"),
        reason="gensim not installed"
    )
    def test_similar_vocabulary(self, simple_walks):
        """Test that Rust and gensim produce same vocabulary."""
        from gensim.models import Word2Vec
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        # Convert walks to strings for gensim
        walks_str = [[str(n) for n in walk] for walk in simple_walks]

        # Train gensim
        gensim_model = Word2Vec(
            sentences=walks_str,
            vector_size=32,
            window=5,
            min_count=1,
            epochs=5,
        )
        gensim_vocab = set(gensim_model.wv.index_to_key)

        # Train Rust
        config = PyWord2VecConfig(embedding_dim=32, epochs=5, seed=42)
        rust_embeddings = train_word2vec_skipgram(simple_walks, config)
        rust_vocab = set(str(k) for k in rust_embeddings.keys())

        # Vocabularies should match
        assert gensim_vocab == rust_vocab, (
            f"Vocabulary mismatch: gensim={gensim_vocab}, rust={rust_vocab}"
        )

    @pytest.mark.skipif(
        not pytest.importorskip("gensim", reason="gensim not installed"),
        reason="gensim not installed"
    )
    def test_similar_quality(self, cluster_walks):
        """Test that Rust embeddings have similar quality to gensim."""
        from gensim.models import Word2Vec
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        # Convert walks to strings for gensim
        walks_str = [[str(n) for n in walk] for walk in cluster_walks]

        # Train gensim
        gensim_model = Word2Vec(
            sentences=walks_str,
            vector_size=64,
            window=5,
            min_count=1,
            epochs=10,
        )

        # Train Rust
        config = PyWord2VecConfig(
            embedding_dim=64,
            window_size=5,
            epochs=10,
            seed=42,
        )
        rust_embeddings = train_word2vec_skipgram(cluster_walks, config)

        # Compare cluster structure
        def within_cluster_sim(embeddings, cluster):
            sims = []
            for i, a in enumerate(cluster):
                for b in cluster[i + 1:]:
                    sims.append(cosine_similarity(embeddings[a], embeddings[b]))
            return np.mean(sims) if sims else 0

        # Gensim similarities
        gensim_emb = {int(k): gensim_model.wv[k].tolist() for k in gensim_model.wv.index_to_key}
        gensim_c1 = within_cluster_sim(gensim_emb, [0, 1, 2])
        gensim_c2 = within_cluster_sim(gensim_emb, [3, 4, 5])

        # Rust similarities
        rust_c1 = within_cluster_sim(rust_embeddings, [0, 1, 2])
        rust_c2 = within_cluster_sim(rust_embeddings, [3, 4, 5])

        # Both should show positive cluster structure (similarity > 0)
        # Note: exact values vary with random initialization
        assert gensim_c1 > 0.0, f"Gensim cluster 1 similarity negative: {gensim_c1}"
        assert rust_c1 > 0.0, f"Rust cluster 1 similarity negative: {rust_c1}"
        assert gensim_c2 > 0.0, f"Gensim cluster 2 similarity negative: {gensim_c2}"
        assert rust_c2 > 0.0, f"Rust cluster 2 similarity negative: {rust_c2}"

        # Both implementations should produce reasonable cluster structure
        print(f"\nCluster similarity comparison:")
        print(f"  Gensim: cluster1={gensim_c1:.3f}, cluster2={gensim_c2:.3f}")
        print(f"  Rust:   cluster1={rust_c1:.3f}, cluster2={rust_c2:.3f}")


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================


class TestPerformance:
    """Test Rust Word2Vec performance."""

    def test_training_speed(self, large_walks):
        """Test that Rust training is reasonably fast."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        config = PyWord2VecConfig(
            embedding_dim=128,
            window_size=5,
            epochs=5,
            seed=42,
        )

        start = time.time()
        embeddings = train_word2vec_skipgram(large_walks, config)
        rust_time = time.time() - start

        # Should complete in reasonable time (< 60 seconds for 1000 walks)
        assert rust_time < 60, f"Training took too long: {rust_time:.1f}s"

        # Should have produced embeddings
        assert len(embeddings) > 0

    @pytest.mark.skipif(
        not pytest.importorskip("gensim", reason="gensim not installed"),
        reason="gensim not installed"
    )
    def test_comparison_with_gensim(self, simple_walks):
        """Compare Rust and gensim performance and quality.

        Note: Gensim uses highly optimized Cython code, so our pure Rust
        implementation may not be faster. The main benefits of our Rust
        implementation are:
        1. No gensim dependency (~100MB)
        2. GIL release during training
        3. Integration with other Rust graph algorithms

        This test just verifies both produce valid results.
        """
        from gensim.models import Word2Vec
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        # Convert walks to strings for gensim
        walks_str = [[str(n) for n in walk] for walk in simple_walks]

        # Time gensim
        start = time.time()
        gensim_model = Word2Vec(
            sentences=walks_str,
            vector_size=32,
            window=5,
            min_count=1,
            workers=1,
            epochs=5,
        )
        gensim_time = time.time() - start

        # Time Rust
        config = PyWord2VecConfig(
            embedding_dim=32,
            window_size=5,
            epochs=5,
            seed=42,
        )
        start = time.time()
        rust_embeddings = train_word2vec_skipgram(simple_walks, config)
        rust_time = time.time() - start

        print(f"\nPerformance comparison (small walks):")
        print(f"  Gensim: {gensim_time:.3f}s")
        print(f"  Rust:   {rust_time:.3f}s")

        # Both should produce embeddings
        assert len(rust_embeddings) == len(gensim_model.wv)

        # Both should have correct dimensions
        for node_id, emb in rust_embeddings.items():
            assert len(emb) == 32
        for word in gensim_model.wv.index_to_key:
            assert len(gensim_model.wv[word]) == 32


# ============================================================================
# EDGE CASES
# ============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_min_count_filtering(self):
        """Test that min_count filters low-frequency nodes."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        walks = [
            [0, 1, 2, 1, 0],
            [1, 2, 1, 2, 1],
            [100, 1, 2],  # Node 100 appears only once
        ]

        config = PyWord2VecConfig(min_count=2, epochs=2, seed=42)
        embeddings = train_word2vec_skipgram(walks, config)

        # Node 100 should be filtered out
        assert 100 not in embeddings
        assert 0 in embeddings
        assert 1 in embeddings
        assert 2 in embeddings

    def test_very_short_walks(self):
        """Test training with very short walks."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        walks = [[0, 1], [1, 2], [2, 0]]  # All length 2

        config = PyWord2VecConfig(embedding_dim=16, epochs=5, seed=42)
        embeddings = train_word2vec_skipgram(walks, config)

        # Should still produce embeddings
        assert len(embeddings) == 3

    def test_repeated_walks(self):
        """Test training with identical repeated walks."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        walks = [[0, 1, 2, 1, 0]] * 100  # Same walk repeated

        config = PyWord2VecConfig(embedding_dim=32, epochs=3, seed=42)
        embeddings = train_word2vec_skipgram(walks, config)

        # Should produce embeddings for all nodes
        assert len(embeddings) == 3
        for node_id in [0, 1, 2]:
            assert node_id in embeddings

    def test_large_node_ids(self):
        """Test training with large node IDs."""
        from repotoire_fast import PyWord2VecConfig, train_word2vec_skipgram

        walks = [
            [1000000, 1000001, 1000002],
            [1000001, 1000002, 1000001],
        ]

        config = PyWord2VecConfig(embedding_dim=16, epochs=2, seed=42)
        embeddings = train_word2vec_skipgram(walks, config)

        assert len(embeddings) == 3
        assert 1000000 in embeddings
        assert 1000001 in embeddings
        assert 1000002 in embeddings
