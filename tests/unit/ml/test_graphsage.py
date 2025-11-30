"""Tests for GraphSAGE zero-shot defect prediction.

These tests verify the GraphSAGE model, cross-project trainer, and detector.
Most tests mock PyTorch/PyTorch Geometric to allow running without GPU.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

import numpy as np


# Check for PyTorch Geometric availability
try:
    import torch
    from torch_geometric.data import Data
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


class TestGraphSAGEConfig:
    """Tests for GraphSAGEConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        from repotoire.ml.graphsage_predictor import GraphSAGEConfig

        config = GraphSAGEConfig()

        assert config.input_dim == 256
        assert config.hidden_dim == 128
        assert config.output_dim == 2
        assert config.num_layers == 2
        assert config.dropout == 0.5
        assert config.aggregator == "mean"

    def test_custom_config(self):
        """Test custom configuration values."""
        from repotoire.ml.graphsage_predictor import GraphSAGEConfig

        config = GraphSAGEConfig(
            input_dim=512,
            hidden_dim=256,
            num_layers=3,
            dropout=0.3,
            aggregator="max",
        )

        assert config.input_dim == 512
        assert config.hidden_dim == 256
        assert config.num_layers == 3
        assert config.dropout == 0.3
        assert config.aggregator == "max"


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not installed")
class TestGraphSAGEDefectPredictor:
    """Tests for GraphSAGE model."""

    @pytest.fixture
    def model(self):
        """Create model with default config."""
        from repotoire.ml.graphsage_predictor import (
            GraphSAGEDefectPredictor,
            GraphSAGEConfig,
        )
        return GraphSAGEDefectPredictor(GraphSAGEConfig())

    @pytest.fixture
    def sample_data(self):
        """Create sample graph data."""
        num_nodes = 100
        num_edges = 300
        input_dim = 256

        x = torch.randn(num_nodes, input_dim)
        edge_index = torch.randint(0, num_nodes, (2, num_edges))
        y = torch.randint(0, 2, (num_nodes,))

        return Data(x=x, edge_index=edge_index, y=y)

    def test_forward_output_shape(self, model, sample_data):
        """Test forward pass produces correct output shape."""
        out = model(sample_data.x, sample_data.edge_index)

        assert out.shape == (sample_data.x.size(0), 2)

    def test_get_embeddings_shape(self, model, sample_data):
        """Test embedding extraction shape."""
        embeddings = model.get_embeddings(sample_data.x, sample_data.edge_index)

        assert embeddings.shape == (sample_data.x.size(0), model.config.hidden_dim)

    def test_different_configs(self, sample_data):
        """Test model works with different configurations."""
        from repotoire.ml.graphsage_predictor import (
            GraphSAGEDefectPredictor,
            GraphSAGEConfig,
        )

        configs = [
            GraphSAGEConfig(hidden_dim=64, num_layers=1),
            GraphSAGEConfig(hidden_dim=256, num_layers=3),
            GraphSAGEConfig(hidden_dim=128, num_layers=2, dropout=0.3),
        ]

        for config in configs:
            model = GraphSAGEDefectPredictor(config)
            out = model(sample_data.x, sample_data.edge_index)
            assert out.shape == (sample_data.x.size(0), 2)

    def test_empty_graph(self, model):
        """Test model handles empty graph."""
        x = torch.randn(0, 256)
        edge_index = torch.zeros((2, 0), dtype=torch.long)

        out = model(x, edge_index)
        assert out.shape == (0, 2)

    def test_single_node_no_edges(self, model):
        """Test model handles single node with no edges."""
        x = torch.randn(1, 256)
        edge_index = torch.zeros((2, 0), dtype=torch.long)

        out = model(x, edge_index)
        assert out.shape == (1, 2)

    def test_output_is_valid_logits(self, model, sample_data):
        """Test output can be converted to probabilities."""
        out = model(sample_data.x, sample_data.edge_index)
        probs = torch.nn.functional.softmax(out, dim=1)

        assert probs.shape == out.shape
        # Probabilities should sum to 1
        assert torch.allclose(probs.sum(dim=1), torch.ones(probs.size(0)), atol=1e-5)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not installed")
class TestGraphSAGEWithAttention:
    """Tests for attention-based GraphSAGE."""

    def test_forward(self):
        """Test forward pass."""
        from repotoire.ml.graphsage_predictor import (
            GraphSAGEWithAttention,
            GraphSAGEConfig,
        )

        model = GraphSAGEWithAttention(GraphSAGEConfig())

        x = torch.randn(50, 256)
        edge_index = torch.randint(0, 50, (2, 100))

        out = model(x, edge_index)
        assert out.shape == (50, 2)

    def test_get_embeddings(self):
        """Test embedding extraction."""
        from repotoire.ml.graphsage_predictor import (
            GraphSAGEWithAttention,
            GraphSAGEConfig,
        )

        config = GraphSAGEConfig(hidden_dim=128)
        model = GraphSAGEWithAttention(config)

        x = torch.randn(50, 256)
        edge_index = torch.randint(0, 50, (2, 100))

        embeddings = model.get_embeddings(x, edge_index)
        assert embeddings.shape == (50, 128)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not installed")
class TestGraphFeatureExtractor:
    """Tests for GraphFeatureExtractor."""

    def test_extract_graph_data(self):
        """Test extracting graph data from Neo4j."""
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor

        mock_client = MagicMock()

        # Mock node query
        mock_client.execute_query.side_effect = [
            # First call: nodes
            [
                {
                    "qualified_name": "module.func1",
                    "embedding": [0.1] * 254,
                    "complexity": 5,
                    "loc": 20,
                },
                {
                    "qualified_name": "module.func2",
                    "embedding": [0.2] * 254,
                    "complexity": 10,
                    "loc": 50,
                },
            ],
            # Second call: edges
            [
                {"source": "module.func1", "target": "module.func2"},
            ],
        ]

        extractor = GraphFeatureExtractor(mock_client)
        data, node_mapping = extractor.extract_graph_data()

        assert data.x.size(0) == 2
        assert data.x.size(1) == 256  # 254 embedding + 2 metrics
        assert data.edge_index.size(1) == 1
        assert len(node_mapping) == 2
        assert "module.func1" in node_mapping
        assert "module.func2" in node_mapping

    def test_extract_graph_data_with_labels(self):
        """Test extracting with label mapping."""
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor

        mock_client = MagicMock()
        mock_client.execute_query.side_effect = [
            [
                {"qualified_name": "func1", "embedding": [0.1] * 254, "complexity": 1, "loc": 10},
                {"qualified_name": "func2", "embedding": [0.2] * 254, "complexity": 2, "loc": 20},
            ],
            [],
        ]

        labels = {"func1": 1, "func2": 0}

        extractor = GraphFeatureExtractor(mock_client)
        data, _ = extractor.extract_graph_data(labels=labels)

        assert data.y[0].item() == 1
        assert data.y[1].item() == 0

    def test_extract_empty_graph(self):
        """Test handling empty graph."""
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor

        mock_client = MagicMock()
        mock_client.execute_query.return_value = []

        extractor = GraphFeatureExtractor(mock_client)
        data, node_mapping = extractor.extract_graph_data()

        assert data.x.size(0) == 0
        assert len(node_mapping) == 0

    def test_create_train_test_masks(self):
        """Test train/test mask creation."""
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor

        mock_client = MagicMock()
        extractor = GraphFeatureExtractor(mock_client)

        # Create sample data
        data = Data(
            x=torch.randn(100, 256),
            y=torch.randint(0, 2, (100,)),
            edge_index=torch.zeros((2, 0), dtype=torch.long),
        )

        data = extractor.create_train_test_masks(data, train_ratio=0.8)

        assert hasattr(data, "train_mask")
        assert hasattr(data, "test_mask")
        assert data.train_mask.sum().item() == 80
        assert data.test_mask.sum().item() == 20
        # Masks should be mutually exclusive
        assert (data.train_mask & data.test_mask).sum().item() == 0


class TestCrossProjectTrainingConfig:
    """Tests for CrossProjectTrainingConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        from repotoire.ml.cross_project_trainer import CrossProjectTrainingConfig

        config = CrossProjectTrainingConfig()

        assert config.epochs == 100
        assert config.batch_size == 128
        assert config.learning_rate == 0.001
        assert config.weight_decay == 0.01
        assert config.num_neighbors == [10, 5]
        assert config.early_stopping_patience == 15


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not installed")
class TestCrossProjectTrainer:
    """Tests for cross-project training."""

    @pytest.fixture
    def sample_train_data(self):
        """Create sample training data."""
        num_nodes = 200
        x = torch.randn(num_nodes, 256)
        edge_index = torch.randint(0, num_nodes, (2, 500))
        y = torch.randint(0, 2, (num_nodes,))
        train_mask = torch.zeros(num_nodes, dtype=torch.bool)
        train_mask[:160] = True
        test_mask = ~train_mask

        return Data(x=x, edge_index=edge_index, y=y, train_mask=train_mask, test_mask=test_mask)

    def test_train_basic(self, sample_train_data):
        """Test basic training loop."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectTrainer,
            CrossProjectTrainingConfig,
        )

        config = CrossProjectTrainingConfig(epochs=2, batch_size=32)
        trainer = CrossProjectTrainer(training_config=config)

        history = trainer.train(sample_train_data)

        assert "train_loss" in history.to_dict()
        assert "train_acc" in history.to_dict()
        assert len(history.train_loss) == 2

    def test_predict_zero_shot(self, sample_train_data):
        """Test zero-shot prediction on new data."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectTrainer,
            CrossProjectTrainingConfig,
        )

        config = CrossProjectTrainingConfig(epochs=2, batch_size=32)
        trainer = CrossProjectTrainer(training_config=config)
        trainer.train(sample_train_data)

        # Create "new" unseen data
        new_data = Data(
            x=torch.randn(50, 256),
            edge_index=torch.randint(0, 50, (2, 100)),
        )

        predictions = trainer.predict_zero_shot(new_data)

        assert len(predictions) == 50
        assert all("buggy_probability" in p for p in predictions)
        assert all("prediction" in p for p in predictions)
        assert all(0 <= p["buggy_probability"] <= 1 for p in predictions)

    def test_predict_without_training_raises_error(self):
        """Test prediction before training raises error."""
        from repotoire.ml.cross_project_trainer import CrossProjectTrainer

        trainer = CrossProjectTrainer()

        new_data = Data(
            x=torch.randn(10, 256),
            edge_index=torch.zeros((2, 0), dtype=torch.long),
        )

        with pytest.raises(RuntimeError, match="not trained"):
            trainer.predict_zero_shot(new_data)

    def test_save_and_load(self, sample_train_data, tmp_path):
        """Test model save and load."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectTrainer,
            CrossProjectTrainingConfig,
        )

        config = CrossProjectTrainingConfig(epochs=2, batch_size=32)
        trainer = CrossProjectTrainer(training_config=config)
        trainer.train(sample_train_data)

        # Save
        model_path = tmp_path / "model.pt"
        trainer.save(model_path)
        assert model_path.exists()

        # Load
        loaded_trainer = CrossProjectTrainer.load(model_path)
        assert loaded_trainer._is_trained
        assert loaded_trainer.model is not None

        # Verify predictions match
        test_data = Data(
            x=torch.randn(10, 256),
            edge_index=torch.zeros((2, 0), dtype=torch.long),
        )

        original_preds = trainer.predict_zero_shot(test_data)
        loaded_preds = loaded_trainer.predict_zero_shot(test_data)

        # Predictions should be identical
        for orig, loaded in zip(original_preds, loaded_preds):
            assert abs(orig["buggy_probability"] - loaded["buggy_probability"]) < 1e-5

    def test_get_embeddings(self, sample_train_data):
        """Test embedding extraction."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectTrainer,
            CrossProjectTrainingConfig,
        )

        config = CrossProjectTrainingConfig(epochs=2, batch_size=32)
        trainer = CrossProjectTrainer(training_config=config)
        trainer.train(sample_train_data)

        embeddings = trainer.get_embeddings(sample_train_data)

        assert embeddings.shape[0] == sample_train_data.x.size(0)
        assert embeddings.shape[1] == trainer.model_config.hidden_dim


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch not installed")
class TestCrossProjectDataLoader:
    """Tests for CrossProjectDataLoader."""

    def test_combine_projects(self):
        """Test combining multiple project graphs."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectDataLoader,
            ProjectGraphData,
        )

        # Create mock project graphs
        proj1_data = Data(
            x=torch.randn(50, 256),
            y=torch.randint(0, 2, (50,)),
            edge_index=torch.randint(0, 50, (2, 100)),
            train_mask=torch.ones(50, dtype=torch.bool),
            test_mask=torch.zeros(50, dtype=torch.bool),
        )
        proj2_data = Data(
            x=torch.randn(30, 256),
            y=torch.randint(0, 2, (30,)),
            edge_index=torch.randint(0, 30, (2, 60)),
            train_mask=torch.ones(30, dtype=torch.bool),
            test_mask=torch.zeros(30, dtype=torch.bool),
        )

        proj1 = ProjectGraphData(
            project_name="proj1",
            data=proj1_data,
            node_mapping={"func1": 0, "func2": 1},
            num_buggy=25,
            num_clean=25,
        )
        proj2 = ProjectGraphData(
            project_name="proj2",
            data=proj2_data,
            node_mapping={"func3": 0},
            num_buggy=15,
            num_clean=15,
        )

        loader = CrossProjectDataLoader()
        combined, holdout = loader.combine_projects([proj1, proj2])

        assert combined.x.size(0) == 80  # 50 + 30
        assert holdout is None

    def test_combine_with_holdout(self):
        """Test holding out a project for evaluation."""
        from repotoire.ml.cross_project_trainer import (
            CrossProjectDataLoader,
            ProjectGraphData,
        )

        proj1_data = Data(
            x=torch.randn(50, 256),
            y=torch.randint(0, 2, (50,)),
            edge_index=torch.randint(0, 50, (2, 100)),
            train_mask=torch.ones(50, dtype=torch.bool),
            test_mask=torch.zeros(50, dtype=torch.bool),
        )
        proj2_data = Data(
            x=torch.randn(30, 256),
            y=torch.randint(0, 2, (30,)),
            edge_index=torch.randint(0, 30, (2, 60)),
            train_mask=torch.ones(30, dtype=torch.bool),
            test_mask=torch.zeros(30, dtype=torch.bool),
        )

        proj1 = ProjectGraphData(
            project_name="train_proj",
            data=proj1_data,
            node_mapping={},
            num_buggy=25,
            num_clean=25,
        )
        proj2 = ProjectGraphData(
            project_name="holdout_proj",
            data=proj2_data,
            node_mapping={},
            num_buggy=15,
            num_clean=15,
        )

        loader = CrossProjectDataLoader()
        combined, holdout = loader.combine_projects(
            [proj1, proj2],
            holdout_project="holdout_proj",
        )

        assert combined.x.size(0) == 50  # Only proj1
        assert holdout is not None
        assert holdout.x.size(0) == 30  # proj2 is holdout


class TestGraphSAGEDetector:
    """Tests for GraphSAGE detector."""

    def test_detector_without_model_path(self):
        """Test detector skips when no model path configured."""
        from repotoire.detectors.graphsage_detector import GraphSAGEDetector

        mock_client = MagicMock()
        detector = GraphSAGEDetector(mock_client, model_path=None)

        findings = detector.detect()
        assert findings == []

    def test_detector_with_missing_model(self, tmp_path):
        """Test detector skips when model file doesn't exist."""
        from repotoire.detectors.graphsage_detector import GraphSAGEDetector

        mock_client = MagicMock()
        nonexistent_path = tmp_path / "nonexistent.pt"

        detector = GraphSAGEDetector(mock_client, model_path=nonexistent_path)
        findings = detector.detect()

        assert findings == []

    def test_prediction_to_finding_critical(self):
        """Test high probability creates critical severity."""
        from repotoire.detectors.graphsage_detector import GraphSAGEDetector
        from repotoire.models import Severity

        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{"path": "/test/file.py"}]

        detector = GraphSAGEDetector(mock_client)
        finding = detector._prediction_to_finding("module.risky_func", 0.95)

        assert finding.severity == Severity.CRITICAL
        assert "95%" in finding.description
        assert "URGENT" in finding.suggested_fix

    def test_prediction_to_finding_medium(self):
        """Test moderate probability creates medium severity."""
        from repotoire.detectors.graphsage_detector import GraphSAGEDetector
        from repotoire.models import Severity

        mock_client = MagicMock()
        mock_client.execute_query.return_value = [{"path": "/test/file.py"}]

        detector = GraphSAGEDetector(mock_client)
        finding = detector._prediction_to_finding("module.somewhat_risky", 0.75)

        assert finding.severity == Severity.MEDIUM
        assert "75%" in finding.description

    def test_severity_calculation(self):
        """Test severity method based on probability."""
        from repotoire.detectors.graphsage_detector import GraphSAGEDetector
        from repotoire.models import Finding, Severity

        mock_client = MagicMock()
        detector = GraphSAGEDetector(mock_client)

        # Create findings with different probabilities
        critical_finding = Finding(
            id="1", detector="test", severity=Severity.CRITICAL,
            title="Test", description="Test",
            affected_files=[], affected_nodes=[],
            graph_context={"probability": 0.95},
        )
        high_finding = Finding(
            id="2", detector="test", severity=Severity.HIGH,
            title="Test", description="Test",
            affected_files=[], affected_nodes=[],
            graph_context={"probability": 0.85},
        )
        medium_finding = Finding(
            id="3", detector="test", severity=Severity.MEDIUM,
            title="Test", description="Test",
            affected_files=[], affected_nodes=[],
            graph_context={"probability": 0.75},
        )
        low_finding = Finding(
            id="4", detector="test", severity=Severity.LOW,
            title="Test", description="Test",
            affected_files=[], affected_nodes=[],
            graph_context={"probability": 0.55},
        )

        assert detector.severity(critical_finding) == Severity.CRITICAL
        assert detector.severity(high_finding) == Severity.HIGH
        assert detector.severity(medium_finding) == Severity.MEDIUM
        assert detector.severity(low_finding) == Severity.LOW


class TestTrainingHistory:
    """Tests for TrainingHistory dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        from repotoire.ml.cross_project_trainer import TrainingHistory

        history = TrainingHistory(
            train_loss=[0.5, 0.3, 0.2],
            train_acc=[0.7, 0.8, 0.85],
            val_acc=[0.65, 0.75, 0.78],
            val_auc=[0.70, 0.80, 0.82],
        )

        result = history.to_dict()

        assert result["train_loss"] == [0.5, 0.3, 0.2]
        assert result["train_acc"] == [0.7, 0.8, 0.85]
        assert result["val_acc"] == [0.65, 0.75, 0.78]
        assert result["val_auc"] == [0.70, 0.80, 0.82]

    def test_empty_history(self):
        """Test empty history."""
        from repotoire.ml.cross_project_trainer import TrainingHistory

        history = TrainingHistory()

        assert history.train_loss == []
        assert history.train_acc == []


class TestProjectGraphData:
    """Tests for ProjectGraphData dataclass."""

    def test_total_functions(self):
        """Test total_functions property."""
        from repotoire.ml.cross_project_trainer import ProjectGraphData

        pg = ProjectGraphData(
            project_name="test",
            data=None,
            node_mapping={},
            num_buggy=25,
            num_clean=75,
        )

        assert pg.total_functions == 100
