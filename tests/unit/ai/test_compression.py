"""Unit tests for embedding compression module."""

import numpy as np
import pytest
from pathlib import Path
import tempfile

from repotoire.ai.compression import (
    EmbeddingCompressor,
    create_compressor,
    estimate_memory_savings,
    DEFAULT_TARGET_DIMS,
)


class TestEmbeddingCompressor:
    """Tests for EmbeddingCompressor class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        compressor = EmbeddingCompressor()
        assert compressor.target_dims == DEFAULT_TARGET_DIMS
        assert compressor.quantization_bits == 8
        assert not compressor.is_fitted

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=512,
                quantization_bits=8,
                model_path=model_path,
            )
            assert compressor.target_dims == 512
            assert compressor.model_path == model_path

    def test_fit_basic(self):
        """Test PCA fitting with random embeddings."""
        # Generate random embeddings (simulating 4096-dim vectors)
        # Need n_samples >= target_dims for PCA
        np.random.seed(42)
        source_dims = 4096
        n_samples = 2500  # More than target_dims (2048)
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=2048,
                model_path=model_path,
            )

            # Fit should not raise
            compressor.fit(embeddings, save=True)

            assert compressor.is_fitted
            assert compressor._source_dims == source_dims
            assert model_path.exists()

    def test_compress_and_decompress(self):
        """Test compression and decompression round-trip."""
        np.random.seed(42)
        source_dims = 4096
        target_dims = 2048
        n_samples = 2500  # More than target_dims for PCA
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            compressor.fit(embeddings, save=False)

            # Test single embedding compression
            test_embedding = embeddings[0]
            compressed = compressor.compress(test_embedding)

            # Compressed should be bytes of length target_dims (int8)
            assert isinstance(compressed, bytes)
            assert len(compressed) == target_dims

            # Decompress
            decompressed = compressor.decompress(compressed)
            assert len(decompressed) == source_dims

    def test_get_reduced_embedding(self):
        """Test PCA-only reduction (no quantization)."""
        np.random.seed(42)
        source_dims = 4096
        target_dims = 2048
        n_samples = 2500  # More than target_dims for PCA
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            compressor.fit(embeddings, save=False)

            # Get reduced embedding
            test_embedding = embeddings[0]
            reduced = compressor.get_reduced_embedding(test_embedding)

            assert len(reduced) == target_dims
            assert all(isinstance(x, float) for x in reduced)

    def test_batch_operations(self):
        """Test batch compression and reduction."""
        np.random.seed(42)
        source_dims = 4096
        target_dims = 2048
        n_samples = 2500  # More than target_dims for PCA
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            compressor.fit(embeddings, save=False)

            # Test batch
            batch = embeddings[:10]

            # Batch compression
            compressed_batch = compressor.compress_batch(batch)
            assert len(compressed_batch) == 10
            assert all(len(c) == target_dims for c in compressed_batch)

            # Batch reduction
            reduced_batch = compressor.get_reduced_embeddings_batch(batch)
            assert len(reduced_batch) == 10
            assert all(len(r) == target_dims for r in reduced_batch)

    def test_compression_ratio(self):
        """Test compression ratio calculation."""
        np.random.seed(42)
        source_dims = 4096
        target_dims = 2048
        n_samples = 2500  # More than target_dims for PCA
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"
            compressor = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            compressor.fit(embeddings, save=False)

            # Expected: (4096 * 4) / (2048 * 1) = 8x
            assert compressor.compression_ratio == 8.0

    def test_model_persistence(self):
        """Test saving and loading PCA model."""
        np.random.seed(42)
        source_dims = 4096
        target_dims = 2048
        n_samples = 2500  # More than target_dims for PCA
        embeddings = np.random.randn(n_samples, source_dims).tolist()

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.pkl"

            # Create and fit first compressor
            compressor1 = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            compressor1.fit(embeddings, save=True)

            # Get reduced embedding with first compressor
            test_embedding = embeddings[0]
            reduced1 = compressor1.get_reduced_embedding(test_embedding)

            # Create new compressor that loads from file
            compressor2 = EmbeddingCompressor(
                target_dims=target_dims,
                model_path=model_path,
            )
            assert compressor2.is_fitted

            # Should produce same results
            reduced2 = compressor2.get_reduced_embedding(test_embedding)
            np.testing.assert_array_almost_equal(reduced1, reduced2, decimal=5)

    def test_unfitted_raises_error(self):
        """Test that operations on unfitted compressor raise error."""
        compressor = EmbeddingCompressor()

        with pytest.raises(RuntimeError, match="not fitted"):
            compressor.compress([1.0] * 4096)

        with pytest.raises(RuntimeError, match="not fitted"):
            compressor.decompress(b"\x00" * 2048)

        with pytest.raises(RuntimeError, match="not fitted"):
            compressor.get_reduced_embedding([1.0] * 4096)


class TestEstimateMemorySavings:
    """Tests for memory savings estimation."""

    def test_basic_calculation(self):
        """Test basic memory savings calculation."""
        savings = estimate_memory_savings(
            num_entities=10000,
            source_dims=4096,
            target_dims=2048,
        )

        assert savings["num_entities"] == 10000
        assert savings["source_dims"] == 4096
        assert savings["target_dims"] == 2048

        # Original: 10000 * 4096 * 4 = 163,840,000 bytes ≈ 156.25 MB
        assert abs(savings["original_mb"] - 156.25) < 0.1

        # Compressed (int8): 10000 * 2048 * 1 = 20,480,000 bytes ≈ 19.53 MB
        assert abs(savings["compressed_mb"] - 19.53) < 0.1

        # Compression ratio: 163,840,000 / 20,480,000 = 8
        assert savings["compression_ratio"] == 8.0

    def test_pca_only_savings(self):
        """Test PCA-only savings (float32 output)."""
        savings = estimate_memory_savings(
            num_entities=10000,
            source_dims=4096,
            target_dims=2048,
        )

        # Reduced only (float32): 10000 * 2048 * 4 = 81,920,000 bytes ≈ 78.125 MB
        assert abs(savings["reduced_only_mb"] - 78.125) < 0.1


class TestCreateCompressor:
    """Tests for factory function."""

    def test_create_compressor_defaults(self):
        """Test creating compressor with defaults."""
        compressor = create_compressor()
        assert compressor.target_dims == DEFAULT_TARGET_DIMS
        assert not compressor.is_fitted

    def test_create_compressor_custom(self):
        """Test creating compressor with custom params."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "custom.pkl"
            compressor = create_compressor(
                target_dims=1024,
                model_path=model_path,
            )
            assert compressor.target_dims == 1024
            assert compressor.model_path == model_path
