"""Tests for multimodal fusion model."""

import pytest
import numpy as np

# Skip all tests if torch is not available
torch = pytest.importorskip("torch")

from repotoire.ml.multimodal_fusion import (
    CrossModalAttention,
    FusionConfig,
    GatedFusion,
    MultimodalAttentionFusion,
    MultiTaskLoss,
)


class TestCrossModalAttention:
    """Tests for CrossModalAttention module."""

    def test_output_shapes(self):
        """Test output tensor shapes match expected dimensions."""
        batch_size = 8
        embed_dim = 256

        attention = CrossModalAttention(embed_dim=embed_dim)

        text = torch.randn(batch_size, embed_dim)
        graph = torch.randn(batch_size, embed_dim)

        text_out, graph_out, weights = attention(text, graph)

        assert text_out.shape == (batch_size, embed_dim)
        assert graph_out.shape == (batch_size, embed_dim)
        assert "text_to_graph" in weights
        assert "graph_to_text" in weights

    def test_attention_weights_shape(self):
        """Test attention weight shapes."""
        batch_size = 4
        embed_dim = 128

        attention = CrossModalAttention(embed_dim=embed_dim, num_heads=4)

        text = torch.randn(batch_size, embed_dim)
        graph = torch.randn(batch_size, embed_dim)

        _, _, weights = attention(text, graph)

        # Each attention weight should be [batch_size, 1, 1] after squeeze
        assert weights["text_to_graph"].shape == (batch_size, 1)
        assert weights["graph_to_text"].shape == (batch_size, 1)

    def test_different_embed_dims(self):
        """Test with different embedding dimensions."""
        for embed_dim in [64, 128, 256, 512]:
            attention = CrossModalAttention(embed_dim=embed_dim, num_heads=8)

            text = torch.randn(4, embed_dim)
            graph = torch.randn(4, embed_dim)

            text_out, graph_out, _ = attention(text, graph)

            assert text_out.shape == (4, embed_dim)
            assert graph_out.shape == (4, embed_dim)

    def test_invalid_embed_dim(self):
        """Test that invalid embed_dim raises error."""
        # embed_dim must be divisible by num_heads
        with pytest.raises(ValueError, match="must be divisible"):
            CrossModalAttention(embed_dim=100, num_heads=8)


class TestGatedFusion:
    """Tests for GatedFusion module."""

    def test_gate_weights_sum_to_one(self):
        """Test that gate weights approximately sum to 1."""
        batch_size = 8
        embed_dim = 256

        fusion = GatedFusion(embed_dim=embed_dim)

        text = torch.randn(batch_size, embed_dim)
        graph = torch.randn(batch_size, embed_dim)

        _, weights = fusion(text, graph)

        total = weights["text_weight"] + weights["graph_weight"]
        assert torch.allclose(total, torch.ones_like(total), atol=1e-5)

    def test_output_shape(self):
        """Test fused output shape matches embed_dim."""
        batch_size = 8
        embed_dim = 256

        fusion = GatedFusion(embed_dim=embed_dim)

        text = torch.randn(batch_size, embed_dim)
        graph = torch.randn(batch_size, embed_dim)

        fused, _ = fusion(text, graph)

        assert fused.shape == (batch_size, embed_dim)

    def test_gate_weights_in_valid_range(self):
        """Test that gate weights are between 0 and 1."""
        fusion = GatedFusion(embed_dim=128)

        text = torch.randn(16, 128)
        graph = torch.randn(16, 128)

        _, weights = fusion(text, graph)

        assert (weights["text_weight"] >= 0).all()
        assert (weights["text_weight"] <= 1).all()
        assert (weights["graph_weight"] >= 0).all()
        assert (weights["graph_weight"] <= 1).all()

    def test_different_inputs_produce_different_gates(self):
        """Test that different inputs produce different gate values."""
        fusion = GatedFusion(embed_dim=128)

        # Two different pairs of inputs
        text1 = torch.randn(4, 128)
        graph1 = torch.randn(4, 128)
        text2 = torch.randn(4, 128)
        graph2 = torch.randn(4, 128)

        _, weights1 = fusion(text1, graph1)
        _, weights2 = fusion(text2, graph2)

        # Weights should generally be different
        assert not torch.allclose(weights1["text_weight"], weights2["text_weight"])


class TestMultimodalAttentionFusion:
    """Tests for full fusion model."""

    @pytest.fixture
    def model(self):
        """Create model with default config."""
        return MultimodalAttentionFusion()

    @pytest.fixture
    def custom_model(self):
        """Create model with custom config."""
        config = FusionConfig(
            text_dim=512,
            graph_dim=64,
            fusion_dim=128,
            num_heads=4,
            dropout=0.1,
        )
        return MultimodalAttentionFusion(config)

    def test_forward_all_tasks(self, model):
        """Test forward pass produces all task outputs."""
        batch_size = 8

        text = torch.randn(batch_size, 1536)
        graph = torch.randn(batch_size, 128)

        outputs = model(text, graph)

        assert "fused_embedding" in outputs
        assert "bug_prediction_logits" in outputs
        assert "smell_detection_logits" in outputs
        assert "refactoring_benefit_logits" in outputs

        assert outputs["bug_prediction_logits"].shape == (batch_size, 2)
        assert outputs["smell_detection_logits"].shape == (batch_size, 5)
        assert outputs["refactoring_benefit_logits"].shape == (batch_size, 3)

    def test_forward_single_task(self, model):
        """Test forward pass for single task."""
        batch_size = 8

        text = torch.randn(batch_size, 1536)
        graph = torch.randn(batch_size, 128)

        outputs = model(text, graph, task="bug_prediction")

        assert "bug_prediction_logits" in outputs
        assert "smell_detection_logits" not in outputs
        assert "refactoring_benefit_logits" not in outputs

    def test_forward_invalid_task(self, model):
        """Test forward pass with invalid task raises error."""
        text = torch.randn(4, 1536)
        graph = torch.randn(4, 128)

        with pytest.raises(ValueError, match="Unknown task"):
            model(text, graph, task="invalid_task")

    def test_get_fused_embedding(self, model):
        """Test fused embedding extraction."""
        batch_size = 8

        text = torch.randn(batch_size, 1536)
        graph = torch.randn(batch_size, 128)

        fused = model.get_fused_embedding(text, graph)

        assert fused.shape == (batch_size, 256)

    def test_custom_config(self, custom_model):
        """Test model with custom configuration."""
        batch_size = 4

        text = torch.randn(batch_size, 512)
        graph = torch.randn(batch_size, 64)

        outputs = custom_model(text, graph)

        assert outputs["fused_embedding"].shape == (batch_size, 128)

    def test_modality_weights_present(self, model):
        """Test that modality weights are returned."""
        text = torch.randn(4, 1536)
        graph = torch.randn(4, 128)

        outputs = model(text, graph)

        assert "text_weight" in outputs
        assert "graph_weight" in outputs
        assert outputs["text_weight"].shape == (4,)
        assert outputs["graph_weight"].shape == (4,)

    def test_gradients_flow(self, model):
        """Test that gradients flow through the model."""
        text = torch.randn(4, 1536, requires_grad=True)
        graph = torch.randn(4, 128, requires_grad=True)

        outputs = model(text, graph, task="bug_prediction")
        loss = outputs["bug_prediction_logits"].sum()
        loss.backward()

        assert text.grad is not None
        assert graph.grad is not None

    def test_eval_mode(self, model):
        """Test model in evaluation mode."""
        model.eval()

        text = torch.randn(4, 1536)
        graph = torch.randn(4, 128)

        with torch.no_grad():
            outputs1 = model(text, graph)
            outputs2 = model(text, graph)

        # In eval mode with same input, outputs should be identical
        assert torch.allclose(
            outputs1["fused_embedding"],
            outputs2["fused_embedding"],
        )


class TestMultiTaskLoss:
    """Tests for multi-task loss."""

    def test_loss_computation(self):
        """Test loss is computed correctly."""
        tasks = ["bug_prediction", "smell_detection"]
        loss_fn = MultiTaskLoss(tasks)

        predictions = {
            "bug_prediction_logits": torch.randn(8, 2),
            "smell_detection_logits": torch.randn(8, 5),
        }
        targets = {
            "bug_prediction": torch.randint(0, 2, (8,)),
            "smell_detection": torch.randint(0, 5, (8,)),
        }

        total_loss, task_losses = loss_fn(predictions, targets)

        assert total_loss.requires_grad
        assert "bug_prediction" in task_losses
        assert "smell_detection" in task_losses

    def test_loss_is_positive(self):
        """Test that loss values are positive."""
        tasks = ["bug_prediction"]
        loss_fn = MultiTaskLoss(tasks)

        predictions = {"bug_prediction_logits": torch.randn(8, 2)}
        targets = {"bug_prediction": torch.randint(0, 2, (8,))}

        total_loss, task_losses = loss_fn(predictions, targets)

        # Note: total_loss includes log_vars which can be negative,
        # but task_losses should be positive
        assert task_losses["bug_prediction"] >= 0

    def test_task_weights(self):
        """Test that task weights can be retrieved."""
        tasks = ["bug_prediction", "smell_detection"]
        loss_fn = MultiTaskLoss(tasks)

        weights = loss_fn.get_task_weights()

        assert "bug_prediction" in weights
        assert "smell_detection" in weights
        assert all(w > 0 for w in weights.values())

    def test_missing_task_in_predictions(self):
        """Test behavior when task is missing from predictions."""
        tasks = ["bug_prediction", "smell_detection"]
        loss_fn = MultiTaskLoss(tasks)

        # Only provide one task
        predictions = {"bug_prediction_logits": torch.randn(8, 2)}
        targets = {"bug_prediction": torch.randint(0, 2, (8,))}

        total_loss, task_losses = loss_fn(predictions, targets)

        assert "bug_prediction" in task_losses
        assert "smell_detection" not in task_losses


class TestFusionConfig:
    """Tests for FusionConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FusionConfig()

        assert config.text_dim == 1536
        assert config.graph_dim == 128
        assert config.fusion_dim == 256
        assert config.num_heads == 8
        assert config.dropout == 0.3
        assert config.num_tasks == 3

    def test_custom_config(self):
        """Test custom configuration values."""
        config = FusionConfig(
            text_dim=512,
            graph_dim=64,
            fusion_dim=128,
            num_heads=4,
            dropout=0.1,
        )

        assert config.text_dim == 512
        assert config.graph_dim == 64
        assert config.fusion_dim == 128
        assert config.num_heads == 4
        assert config.dropout == 0.1


class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_end_to_end_training_step(self):
        """Test a complete training step."""
        # Create model
        model = MultimodalAttentionFusion()

        # Create loss function
        tasks = ["bug_prediction", "smell_detection"]
        loss_fn = MultiTaskLoss(tasks)

        # Create optimizer
        optimizer = torch.optim.Adam(
            list(model.parameters()) + list(loss_fn.parameters()),
            lr=0.001,
        )

        # Create batch
        batch_size = 16
        text = torch.randn(batch_size, 1536)
        graph = torch.randn(batch_size, 128)
        targets = {
            "bug_prediction": torch.randint(0, 2, (batch_size,)),
            "smell_detection": torch.randint(0, 5, (batch_size,)),
        }

        # Training step
        model.train()
        optimizer.zero_grad()

        outputs = model(text, graph)
        loss, task_losses = loss_fn(outputs, targets)
        loss.backward()
        optimizer.step()

        # Verify training occurred
        assert loss.item() > 0
        assert all(l > 0 for l in task_losses.values())

    def test_batch_size_flexibility(self):
        """Test that model handles different batch sizes."""
        model = MultimodalAttentionFusion()

        for batch_size in [1, 4, 16, 64]:
            text = torch.randn(batch_size, 1536)
            graph = torch.randn(batch_size, 128)

            outputs = model(text, graph)

            assert outputs["fused_embedding"].shape[0] == batch_size
            assert outputs["bug_prediction_logits"].shape[0] == batch_size
