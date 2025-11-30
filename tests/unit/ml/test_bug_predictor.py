"""Tests for bug prediction model."""

import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.ml.bug_predictor import (
    BugPredictor,
    BugPredictorConfig,
    FeatureExtractor,
    PredictionResult,
    ModelMetrics,
)
from repotoire.ml.training_data import TrainingDataset, TrainingExample


class TestFeatureExtractor:
    """Tests for FeatureExtractor."""

    def test_extract_features_returns_correct_shape(self):
        """Test feature vector has correct dimensions (128 embedding + 10 metrics)."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{
            "embedding": [0.1] * 128,
            "complexity": 10,
            "loc": 50,
            "fan_in": 3,
            "fan_out": 5,
            "churn": 2,
            "age_days": 100,
            "num_authors": 2,
            "has_tests": 1,
        }]

        extractor = FeatureExtractor(mock_client)
        features = extractor.extract_features("test.module.function")

        assert features is not None
        assert len(features) == 138  # 128 embedding + 10 metrics

    def test_extract_features_returns_none_when_no_embedding(self):
        """Test returns None when embedding is missing."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{"embedding": None}]

        extractor = FeatureExtractor(mock_client)
        features = extractor.extract_features("test.function")

        assert features is None

    def test_extract_features_returns_none_when_no_result(self):
        """Test returns None when function not found."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        extractor = FeatureExtractor(mock_client)
        features = extractor.extract_features("nonexistent.function")

        assert features is None

    def test_extract_features_handles_missing_metrics(self):
        """Test handles missing metrics gracefully with defaults."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{
            "embedding": [0.5] * 128,
            "complexity": None,  # Missing
            "loc": None,  # Missing
            "fan_in": 0,
            "fan_out": 0,
            "churn": None,
            "age_days": None,
            "num_authors": None,
            "has_tests": None,
        }]

        extractor = FeatureExtractor(mock_client)
        features = extractor.extract_features("test.function")

        assert features is not None
        # Check defaults are applied
        assert features[128] == 1  # complexity default
        assert features[129] == 10  # loc default

    def test_extract_batch_features(self):
        """Test batch feature extraction."""
        mock_client = MagicMock()

        def mock_query(query, **kwargs):
            name = kwargs.get("qualified_name", "")
            if name == "valid.function":
                return [{
                    "embedding": [0.1] * 128,
                    "complexity": 5,
                    "loc": 20,
                    "fan_in": 2,
                    "fan_out": 3,
                    "churn": 1,
                    "age_days": 50,
                    "num_authors": 1,
                    "has_tests": 1,
                }]
            return []

        mock_client.execute_query.side_effect = mock_query

        extractor = FeatureExtractor(mock_client)
        X, valid_names = extractor.extract_batch_features([
            "valid.function",
            "invalid.function",
        ])

        assert len(X) == 1
        assert valid_names == ["valid.function"]

    def test_extract_metrics_only(self):
        """Test extracting only code metrics (no embedding)."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{
            "complexity": 15,
            "loc": 100,
            "fan_in": 5,
            "fan_out": 10,
            "churn": 3,
            "age_days": 200,
            "num_authors": 4,
            "has_tests": 1,
        }]

        extractor = FeatureExtractor(mock_client)
        metrics = extractor.extract_metrics_only("test.function")

        assert metrics is not None
        assert len(metrics) == 10
        assert metrics[0] == 15  # complexity
        assert metrics[1] == 100  # loc


class TestPredictionResult:
    """Tests for PredictionResult dataclass."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = PredictionResult(
            qualified_name="module.MyClass.method",
            file_path="module.py",
            bug_probability=0.85,
            is_high_risk=True,
            contributing_factors=["complexity=25", "fan_out=12"],
            similar_buggy_functions=["module.past_bug"],
        )

        d = result.to_dict()

        assert d["qualified_name"] == "module.MyClass.method"
        assert d["bug_probability"] == 0.85
        assert d["is_high_risk"] is True
        assert len(d["contributing_factors"]) == 2


class TestModelMetrics:
    """Tests for ModelMetrics dataclass."""

    def test_to_dict(self):
        """Test ModelMetrics serialization."""
        metrics = ModelMetrics(
            accuracy=0.85,
            precision=0.82,
            recall=0.88,
            f1_score=0.85,
            auc_roc=0.90,
            cv_scores=[0.88, 0.90, 0.87, 0.91, 0.89],
        )

        d = metrics.to_dict()

        assert d["accuracy"] == 0.85
        assert d["auc_roc"] == 0.9
        assert "cv_mean" in d
        assert "cv_std" in d
        assert d["cv_mean"] == 0.89  # mean of cv_scores

    def test_to_dict_empty_cv_scores(self):
        """Test serialization with empty CV scores."""
        metrics = ModelMetrics(
            accuracy=0.8,
            precision=0.75,
            recall=0.85,
            f1_score=0.8,
            auc_roc=0.85,
            cv_scores=[],
        )

        d = metrics.to_dict()

        assert d["cv_mean"] == 0.0
        assert d["cv_std"] == 0.0


class TestBugPredictor:
    """Tests for BugPredictor."""

    @pytest.fixture
    def mock_dataset(self):
        """Create mock training dataset."""
        examples = []
        for i in range(100):
            examples.append(TrainingExample(
                qualified_name=f"module.func_{i}",
                file_path="module.py",
                label="buggy" if i < 50 else "clean",
            ))

        return TrainingDataset(
            examples=examples,
            repository="/path/to/repo",
            extracted_at="2024-01-01T00:00:00",
            date_range=("2020-01-01", "2024-01-01"),
            statistics={"total": 100, "buggy": 50, "clean": 50, "buggy_pct": 50.0},
        )

    def test_config_defaults(self):
        """Test default configuration values."""
        config = BugPredictorConfig()

        assert config.n_estimators == 100
        assert config.max_depth == 10
        assert config.min_samples_split == 20
        assert config.class_weight == "balanced"
        assert config.test_split == 0.2
        assert config.cv_folds == 5

    def test_predict_raises_when_not_trained(self):
        """Test predict raises error when model not trained."""
        mock_client = MagicMock()
        predictor = BugPredictor(mock_client)

        with pytest.raises(RuntimeError, match="Model not trained"):
            predictor.predict("test.function")

    def test_save_raises_when_not_trained(self):
        """Test save raises error when model not trained."""
        mock_client = MagicMock()
        predictor = BugPredictor(mock_client)

        with pytest.raises(RuntimeError, match="Model not trained"):
            predictor.save(Path("/tmp/model.pkl"))

    def test_train_insufficient_data(self, mock_dataset):
        """Test training fails with insufficient data."""
        mock_client = MagicMock()
        # Return no features (simulating missing embeddings)
        mock_client.execute_query.return_value = []

        predictor = BugPredictor(mock_client)

        with pytest.raises(ValueError, match="Insufficient training data"):
            predictor.train(mock_dataset)

    def test_get_feature_importance_report(self):
        """Test feature importance report generation."""
        mock_client = MagicMock()
        predictor = BugPredictor(mock_client)

        # Simulate trained model with realistic importances
        predictor._is_trained = True
        predictor._feature_importances = np.concatenate([
            np.ones(128) * 0.005,  # Embedding features (128 * 0.005 = 0.64 total)
            np.array([0.1, 0.05, 0.03, 0.02, 0.01, 0.005, 0.005, 0.005, 0.005, 0.005]),  # Metrics
        ])

        report = predictor.get_feature_importance_report()

        assert "embedding_total" in report
        # All metrics should be in the report
        assert "complexity" in report
        assert "loc" in report
        assert report["embedding_total"] > 0
        assert report["complexity"] == 0.1  # Should match input value

    def test_get_feature_importance_report_untrained(self):
        """Test feature importance returns empty dict when not trained."""
        mock_client = MagicMock()
        predictor = BugPredictor(mock_client)

        report = predictor.get_feature_importance_report()

        assert report == {}


class TestNode2VecConfig:
    """Tests for Node2VecConfig."""

    def test_config_defaults(self):
        """Test default configuration values."""
        from repotoire.ml.node2vec_embeddings import Node2VecConfig

        config = Node2VecConfig()

        assert config.embedding_dimension == 128
        assert config.walk_length == 80
        assert config.walks_per_node == 10
        assert config.window_size == 10
        assert config.return_factor == 1.0
        assert config.in_out_factor == 1.0
        assert config.write_property == "node2vec_embedding"

    def test_custom_config(self):
        """Test custom configuration values."""
        from repotoire.ml.node2vec_embeddings import Node2VecConfig

        config = Node2VecConfig(
            embedding_dimension=256,
            walk_length=100,
            return_factor=0.5,
            in_out_factor=2.0,
        )

        assert config.embedding_dimension == 256
        assert config.walk_length == 100
        assert config.return_factor == 0.5
        assert config.in_out_factor == 2.0


class TestNode2VecEmbedder:
    """Tests for Node2VecEmbedder."""

    def test_check_gds_available_success(self):
        """Test GDS availability check when successful."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{"version": "2.5.0"}]

        embedder = Node2VecEmbedder(mock_client)
        assert embedder.check_gds_available() is True

    def test_check_gds_available_failure(self):
        """Test GDS availability check when not available."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.side_effect = Exception("GDS not installed")

        embedder = Node2VecEmbedder(mock_client)
        assert embedder.check_gds_available() is False

    def test_create_projection_fails_without_gds(self):
        """Test projection fails when GDS not available."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.side_effect = Exception("GDS not installed")

        embedder = Node2VecEmbedder(mock_client)

        with pytest.raises(RuntimeError, match="GDS"):
            embedder.create_projection()

    def test_generate_embeddings_fails_without_projection(self):
        """Test embedding generation fails without projection."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        embedder = Node2VecEmbedder(mock_client)

        with pytest.raises(RuntimeError, match="projection does not exist"):
            embedder.generate_embeddings()

    def test_compute_embedding_statistics_empty(self):
        """Test statistics with no embeddings."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        embedder = Node2VecEmbedder(mock_client)
        stats = embedder.compute_embedding_statistics()

        assert stats["count"] == 0
        assert stats["mean_norm"] == 0.0

    def test_get_embedding_for_node(self):
        """Test retrieving embedding for specific node."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{
            "embedding": [0.1, 0.2, 0.3, 0.4, 0.5]
        }]

        embedder = Node2VecEmbedder(mock_client)
        embedding = embedder.get_embedding_for_node("test.function")

        assert embedding is not None
        assert len(embedding) == 5
        assert isinstance(embedding, np.ndarray)

    def test_get_embedding_for_node_not_found(self):
        """Test returns None when node not found."""
        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder

        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        embedder = Node2VecEmbedder(mock_client)
        embedding = embedder.get_embedding_for_node("nonexistent.function")

        assert embedding is None


class TestMLBugDetector:
    """Tests for MLBugDetector."""

    def test_detect_skips_without_model_path(self):
        """Test detector skips when no model path configured."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector

        mock_client = MagicMock()
        detector = MLBugDetector(mock_client, model_path=None)

        findings = detector.detect()

        assert findings == []

    def test_detect_skips_when_model_not_found(self):
        """Test detector skips when model file doesn't exist."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector

        mock_client = MagicMock()
        detector = MLBugDetector(
            mock_client,
            model_path=Path("/nonexistent/model.pkl"),
        )

        findings = detector.detect()

        assert findings == []

    def test_generate_recommendations_complexity(self):
        """Test recommendations include complexity advice."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector

        mock_client = MagicMock()
        detector = MLBugDetector(mock_client)

        mock_pred = MagicMock()
        mock_pred.contributing_factors = ["complexity=25 (importance: 0.15)"]

        recommendations = detector._generate_recommendations(mock_pred)

        assert any("complexity" in r.lower() for r in recommendations)

    def test_generate_recommendations_coupling(self):
        """Test recommendations include coupling advice."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector

        mock_client = MagicMock()
        detector = MLBugDetector(mock_client)

        mock_pred = MagicMock()
        mock_pred.contributing_factors = ["fan_out=15 (importance: 0.10)"]

        recommendations = detector._generate_recommendations(mock_pred)

        assert any("depend" in r.lower() for r in recommendations)

    def test_prediction_to_finding_severity_critical(self):
        """Test finding severity is CRITICAL for >= 90% probability."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector
        from repotoire.models import Severity

        mock_client = MagicMock()
        detector = MLBugDetector(mock_client)

        mock_pred = MagicMock()
        mock_pred.qualified_name = "test.function"
        mock_pred.file_path = "test.py"
        mock_pred.bug_probability = 0.95
        mock_pred.contributing_factors = []
        mock_pred.similar_buggy_functions = []

        finding = detector._prediction_to_finding(mock_pred)

        assert finding.severity == Severity.CRITICAL
        assert "test.function" in finding.affected_nodes
        assert "test.py" in finding.affected_files
        assert finding.graph_context["bug_probability"] == 0.95

    def test_prediction_to_finding_severity_high(self):
        """Test finding severity is HIGH for >= 80% probability."""
        from repotoire.detectors.ml_bug_detector import MLBugDetector
        from repotoire.models import Severity

        mock_client = MagicMock()
        detector = MLBugDetector(mock_client)

        mock_pred = MagicMock()
        mock_pred.qualified_name = "test.function"
        mock_pred.file_path = "test.py"
        mock_pred.bug_probability = 0.85
        mock_pred.contributing_factors = []
        mock_pred.similar_buggy_functions = []

        finding = detector._prediction_to_finding(mock_pred)

        assert finding.severity == Severity.HIGH
