"""Unit tests for Rust feature extraction functions (REPO-248).

Tests combine_features_batch and normalize_features_batch with:
- Normal operation
- Edge cases (empty, single row, constant columns)
- Equivalence with numpy implementations
- Performance comparison
"""

import pytest
import numpy as np
import time

# Try to import Rust extension
try:
    from repotoire_fast import combine_features_batch, normalize_features_batch
    HAS_RUST_EXT = True
except ImportError:
    HAS_RUST_EXT = False

skip_no_rust = pytest.mark.skipif(
    not HAS_RUST_EXT,
    reason="repotoire_fast Rust extension not available"
)


class TestCombineFeaturesBatch:
    """Tests for combine_features_batch function."""

    @skip_no_rust
    def test_basic_combination(self):
        """Test basic feature combination."""
        embeddings = np.array([
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
        ], dtype=np.float32)

        metrics = np.array([
            [1.0, 2.0],
            [3.0, 4.0],
        ], dtype=np.float32)

        result = combine_features_batch(embeddings, metrics)

        # Result is now a numpy array
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 5)  # 2 rows, 3 + 2 columns

        # Check values
        np.testing.assert_array_almost_equal(
            result[0], [0.1, 0.2, 0.3, 1.0, 2.0]
        )
        np.testing.assert_array_almost_equal(
            result[1], [0.4, 0.5, 0.6, 3.0, 4.0]
        )

    @skip_no_rust
    def test_realistic_dimensions(self):
        """Test with realistic embedding (128) and metric (10) dimensions."""
        n_samples = 100
        embeddings = np.random.rand(n_samples, 128).astype(np.float32)
        metrics = np.random.rand(n_samples, 10).astype(np.float32)

        result = combine_features_batch(embeddings, metrics)

        assert isinstance(result, np.ndarray)
        assert result.shape == (n_samples, 138)  # 128 + 10

        # Verify first row values match
        expected = np.concatenate([embeddings[0], metrics[0]])
        np.testing.assert_array_almost_equal(result[0], expected, decimal=5)

    @skip_no_rust
    def test_single_row(self):
        """Test with single row input."""
        embeddings = np.array([[0.1, 0.2, 0.3]], dtype=np.float32)
        metrics = np.array([[1.0, 2.0]], dtype=np.float32)

        result = combine_features_batch(embeddings, metrics)

        assert isinstance(result, np.ndarray)
        assert result.shape == (1, 5)
        np.testing.assert_array_almost_equal(
            result[0], [0.1, 0.2, 0.3, 1.0, 2.0]
        )

    @skip_no_rust
    def test_row_count_mismatch_error(self):
        """Test that mismatched row counts raise an error."""
        embeddings = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
        metrics = np.array([[1.0, 2.0]], dtype=np.float32)  # Only 1 row

        with pytest.raises(ValueError, match="Row count mismatch"):
            combine_features_batch(embeddings, metrics)

    @skip_no_rust
    def test_empty_array_error(self):
        """Test that empty arrays raise an error."""
        embeddings = np.array([], dtype=np.float32).reshape(0, 3)
        metrics = np.array([], dtype=np.float32).reshape(0, 2)

        with pytest.raises(ValueError, match="must not be empty"):
            combine_features_batch(embeddings, metrics)

    @skip_no_rust
    def test_matches_numpy_hstack(self):
        """Test that result matches numpy.hstack."""
        n_samples = 50
        embeddings = np.random.rand(n_samples, 128).astype(np.float32)
        metrics = np.random.rand(n_samples, 10).astype(np.float32)

        rust_result = combine_features_batch(embeddings, metrics)
        numpy_result = np.hstack([embeddings, metrics])

        np.testing.assert_array_almost_equal(rust_result, numpy_result, decimal=5)


class TestNormalizeFeaturesBatch:
    """Tests for normalize_features_batch function."""

    @skip_no_rust
    def test_basic_normalization(self):
        """Test basic Z-score normalization."""
        features = np.array([
            [1.0, 10.0],
            [2.0, 20.0],
            [3.0, 30.0],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # Result is now a numpy array
        assert isinstance(result, np.ndarray)

        # Check that each column has mean ≈ 0
        col_means = np.mean(result, axis=0)
        np.testing.assert_array_almost_equal(col_means, [0, 0], decimal=5)

        # Check that each column has std ≈ 1
        col_stds = np.std(result, axis=0)
        np.testing.assert_array_almost_equal(col_stds, [1, 1], decimal=5)

    @skip_no_rust
    def test_zscore_values(self):
        """Test specific Z-score values."""
        features = np.array([
            [1.0, 10.0],
            [2.0, 20.0],
            [3.0, 30.0],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # Column 0: mean=2.0, std=sqrt(2/3) ≈ 0.8165
        # (1-2)/0.8165 ≈ -1.2247
        # (2-2)/0.8165 = 0
        # (3-2)/0.8165 ≈ 1.2247
        expected_col0 = np.array([-1.2247, 0.0, 1.2247])
        np.testing.assert_array_almost_equal(result[:, 0], expected_col0, decimal=3)

    @skip_no_rust
    def test_single_row_returns_zeros(self):
        """Test that single row returns all zeros (std=0)."""
        features = np.array([[5.0, 10.0, 15.0]], dtype=np.float32)

        result = normalize_features_batch(features)

        assert isinstance(result, np.ndarray)
        assert result.shape == (1, 3)
        np.testing.assert_array_almost_equal(result[0], [0, 0, 0])

    @skip_no_rust
    def test_constant_column_returns_zeros(self):
        """Test that constant columns return zeros."""
        features = np.array([
            [1.0, 5.0],  # Column 1 is constant (5.0)
            [2.0, 5.0],
            [3.0, 5.0],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # Column 1 (constant) should be all zeros
        np.testing.assert_array_almost_equal(result[:, 1], [0, 0, 0])

        # Column 0 should be normalized
        assert abs(np.mean(result[:, 0])) < 1e-5
        assert abs(np.std(result[:, 0]) - 1.0) < 1e-5

    @skip_no_rust
    def test_empty_array_error(self):
        """Test that empty arrays raise an error."""
        features = np.array([], dtype=np.float32).reshape(0, 3)

        with pytest.raises(ValueError, match="must not be empty"):
            normalize_features_batch(features)

    @skip_no_rust
    def test_realistic_dimensions(self):
        """Test with realistic 138-dimensional features."""
        n_samples = 100
        features = np.random.rand(n_samples, 138).astype(np.float32) * 100

        result = normalize_features_batch(features)

        assert isinstance(result, np.ndarray)
        assert result.shape == (n_samples, 138)

        # Check normalization for each column
        for col in range(138):
            col_data = result[:, col]
            # Mean should be ~0
            assert abs(np.mean(col_data)) < 0.01
            # Std should be ~1 (unless constant)
            if np.std(features[:, col]) > 1e-5:
                assert abs(np.std(col_data) - 1.0) < 0.01

    @skip_no_rust
    def test_matches_numpy_zscore(self):
        """Test that result matches numpy Z-score implementation."""
        n_samples = 50
        features = np.random.rand(n_samples, 20).astype(np.float32) * 100

        rust_result = normalize_features_batch(features)

        # Numpy Z-score
        mean = np.mean(features, axis=0)
        std = np.std(features, axis=0)
        std = np.where(std < 1e-10, 1.0, std)
        numpy_result = (features - mean) / std

        np.testing.assert_array_almost_equal(rust_result, numpy_result, decimal=4)


class TestFeatureExtractorIntegration:
    """Integration tests for FeatureExtractor methods."""

    def test_combine_features_method(self):
        """Test FeatureExtractor.combine_features static method."""
        from repotoire.ml.bug_predictor import FeatureExtractor

        embeddings = np.random.rand(10, 128).astype(np.float32)
        metrics = np.random.rand(10, 10).astype(np.float32)

        result = FeatureExtractor.combine_features(embeddings, metrics)

        assert result.shape == (10, 138)
        # Verify values
        expected = np.hstack([embeddings, metrics])
        np.testing.assert_array_almost_equal(result, expected, decimal=4)

    def test_normalize_features_method(self):
        """Test FeatureExtractor.normalize_features static method."""
        from repotoire.ml.bug_predictor import FeatureExtractor

        features = np.random.rand(20, 138).astype(np.float32) * 100

        result = FeatureExtractor.normalize_features(features)

        assert result.shape == (20, 138)
        # Check normalization
        for col in range(138):
            assert abs(np.mean(result[:, col])) < 0.01

    def test_combine_then_normalize_pipeline(self):
        """Test combining then normalizing (full pipeline)."""
        from repotoire.ml.bug_predictor import FeatureExtractor

        # Simulate real data
        n_samples = 50
        embeddings = np.random.rand(n_samples, 128).astype(np.float32)
        metrics = np.random.rand(n_samples, 10).astype(np.float32) * np.array([
            10,    # complexity
            100,   # loc
            5,     # fan_in
            5,     # fan_out
            20,    # churn
            365,   # age_days
            3,     # num_authors
            1,     # has_tests
            10,    # total_coupling
            0.1,   # complexity_density
        ])

        # Combine
        combined = FeatureExtractor.combine_features(embeddings, metrics)
        assert combined.shape == (n_samples, 138)

        # Normalize
        normalized = FeatureExtractor.normalize_features(combined)
        assert normalized.shape == (n_samples, 138)

        # Verify normalized
        for col in range(138):
            assert abs(np.mean(normalized[:, col])) < 0.01


@skip_no_rust
class TestPerformance:
    """Performance benchmarks for Rust vs numpy."""

    def test_combine_performance(self):
        """Benchmark combine_features_batch vs numpy.hstack."""
        n_samples = 10000
        embeddings = np.random.rand(n_samples, 128).astype(np.float32)
        metrics = np.random.rand(n_samples, 10).astype(np.float32)

        # Warm up
        combine_features_batch(embeddings, metrics)
        np.hstack([embeddings, metrics])

        # Benchmark Rust
        start = time.perf_counter()
        for _ in range(10):
            rust_result = combine_features_batch(embeddings, metrics)
        rust_time = time.perf_counter() - start

        # Benchmark numpy
        start = time.perf_counter()
        for _ in range(10):
            numpy_result = np.hstack([embeddings, metrics])
        numpy_time = time.perf_counter() - start

        print(f"\nCombine features ({n_samples} samples):")
        print(f"  Rust:  {rust_time:.4f}s")
        print(f"  Numpy: {numpy_time:.4f}s")
        print(f"  Speedup: {numpy_time/rust_time:.2f}x")

        # Verify results match
        np.testing.assert_array_almost_equal(rust_result, numpy_result, decimal=5)

    def test_normalize_performance(self):
        """Benchmark normalize_features_batch vs numpy Z-score."""
        n_samples = 10000
        features = np.random.rand(n_samples, 138).astype(np.float32) * 100

        def numpy_zscore(x):
            mean = np.mean(x, axis=0)
            std = np.std(x, axis=0)
            std = np.where(std < 1e-10, 1.0, std)
            return (x - mean) / std

        # Warm up
        normalize_features_batch(features)
        numpy_zscore(features)

        # Benchmark Rust
        start = time.perf_counter()
        for _ in range(10):
            rust_result = normalize_features_batch(features)
        rust_time = time.perf_counter() - start

        # Benchmark numpy
        start = time.perf_counter()
        for _ in range(10):
            numpy_result = numpy_zscore(features)
        numpy_time = time.perf_counter() - start

        print(f"\nNormalize features ({n_samples} samples × 138 features):")
        print(f"  Rust:  {rust_time:.4f}s")
        print(f"  Numpy: {numpy_time:.4f}s")
        print(f"  Speedup: {numpy_time/rust_time:.2f}x")

        # Verify results match
        np.testing.assert_array_almost_equal(rust_result, numpy_result, decimal=4)

    def test_large_batch_performance(self):
        """Test performance with very large batches."""
        n_samples = 100000
        embeddings = np.random.rand(n_samples, 128).astype(np.float32)
        metrics = np.random.rand(n_samples, 10).astype(np.float32)

        # Just verify it completes in reasonable time
        start = time.perf_counter()
        result = combine_features_batch(embeddings, metrics)
        combine_time = time.perf_counter() - start

        print(f"\nLarge batch ({n_samples} samples):")
        print(f"  Combine time: {combine_time:.4f}s")

        assert len(result) == n_samples
        assert combine_time < 5.0  # Should complete in under 5 seconds

        # Test normalize
        features = np.random.rand(n_samples, 138).astype(np.float32)
        start = time.perf_counter()
        result = normalize_features_batch(features)
        normalize_time = time.perf_counter() - start

        print(f"  Normalize time: {normalize_time:.4f}s")

        assert len(result) == n_samples
        assert normalize_time < 10.0  # Should complete in under 10 seconds


class TestEdgeCases:
    """Test edge cases and error handling."""

    @skip_no_rust
    def test_very_small_values(self):
        """Test with very small values (near zero)."""
        features = np.array([
            [1e-10, 1e-10],
            [2e-10, 2e-10],
            [3e-10, 3e-10],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # Should still normalize correctly
        assert result.shape == (3, 2)
        # Check mean is ~0
        assert abs(np.mean(result[:, 0])) < 0.1

    @skip_no_rust
    def test_very_large_values(self):
        """Test with very large values."""
        features = np.array([
            [1e6, 1e8],
            [2e6, 2e8],
            [3e6, 3e8],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # Should still normalize correctly
        assert result.shape == (3, 2)
        assert abs(np.mean(result[:, 0])) < 0.01

    @skip_no_rust
    def test_mixed_scale_features(self):
        """Test with features at different scales (realistic scenario)."""
        # Simulate real bug predictor features:
        # - Embeddings: values 0-1
        # - Complexity: 1-50
        # - LOC: 10-1000
        # - Fan-in/out: 0-20
        # - Churn: 0-100
        features = np.array([
            [0.1, 0.2, 0.3, 5.0, 100.0, 2.0, 3.0, 10.0, 30.0, 0.05],
            [0.5, 0.6, 0.7, 15.0, 500.0, 5.0, 8.0, 50.0, 60.0, 0.03],
            [0.9, 0.8, 0.7, 25.0, 900.0, 10.0, 15.0, 90.0, 90.0, 0.02],
        ], dtype=np.float32)

        result = normalize_features_batch(features)

        # All columns should be normalized
        for col in range(features.shape[1]):
            assert abs(np.mean(result[:, col])) < 0.01
            assert abs(np.std(result[:, col]) - 1.0) < 0.01

    @skip_no_rust
    def test_negative_values(self):
        """Test with negative values."""
        embeddings = np.array([
            [-0.5, 0.5],
            [0.0, 0.0],
            [0.5, -0.5],
        ], dtype=np.float32)

        metrics = np.array([
            [-10.0, 10.0],
            [0.0, 0.0],
            [10.0, -10.0],
        ], dtype=np.float32)

        result = combine_features_batch(embeddings, metrics)

        assert len(result) == 3
        np.testing.assert_array_almost_equal(
            result[0], [-0.5, 0.5, -10.0, 10.0]
        )
