"""Multimodal fusion architecture for text and graph embeddings.

This module implements attention-based multimodal fusion combining:
- Text embeddings (OpenAI 1536-dim): Capture semantic meaning from code
- Graph embeddings (FastRP/Node2Vec 128-dim): Capture structural patterns

The fusion model uses cross-modal attention and gated fusion to learn
when to trust semantic vs structural signals for optimal prediction.

Architecture:
    Text Embedding → Project → Cross-Attention ← Project ← Graph Embedding
                         ↓
                  Gated Fusion (learned modality weights)
                         ↓
                  Fused Embedding (256-dim)
                         ↓
              Task-specific Heads (Bug/Smell/Refactoring)

Example:
    >>> from repotoire.ml.multimodal_fusion import (
    ...     MultimodalAttentionFusion,
    ...     FusionConfig,
    ... )
    >>> model = MultimodalAttentionFusion(FusionConfig())
    >>> outputs = model(text_embeddings, graph_embeddings)
    >>> bug_logits = outputs["bug_prediction_logits"]
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import math

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class FusionConfig:
    """Configuration for multimodal fusion model.

    Attributes:
        text_dim: Dimension of input text embeddings (OpenAI: 1536)
        graph_dim: Dimension of input graph embeddings (FastRP: 128)
        fusion_dim: Dimension of fused representation
        num_heads: Number of attention heads for cross-modal attention
        dropout: Dropout rate for regularization
        num_tasks: Number of prediction tasks (bug, smell, refactoring)
    """

    text_dim: int = 1536  # OpenAI embedding dimension
    graph_dim: int = 128  # FastRP/Node2Vec embedding dimension
    fusion_dim: int = 256  # Fused representation dimension
    num_heads: int = 8  # Attention heads
    dropout: float = 0.3  # Dropout rate
    num_tasks: int = 3  # Number of prediction tasks


class CrossModalAttention(nn.Module):
    """Cross-modal attention between text and graph embeddings.

    Allows each modality to attend to the other, learning
    complementary information across modalities. This enables
    the model to leverage semantic patterns when structural
    signals are weak and vice versa.

    The attention mechanism uses separate Q/K/V projections for
    bidirectional attention: text→graph and graph→text.
    """

    def __init__(
        self,
        embed_dim: int,
        num_heads: int = 8,
        dropout: float = 0.1,
    ):
        """Initialize cross-modal attention.

        Args:
            embed_dim: Embedding dimension (after projection)
            num_heads: Number of attention heads
            dropout: Dropout rate
        """
        super().__init__()

        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads

        if self.head_dim * num_heads != embed_dim:
            raise ValueError(
                f"embed_dim ({embed_dim}) must be divisible by "
                f"num_heads ({num_heads})"
            )

        # Query, Key, Value projections for text attending to graph
        self.text_to_graph_q = nn.Linear(embed_dim, embed_dim)
        self.text_to_graph_k = nn.Linear(embed_dim, embed_dim)
        self.text_to_graph_v = nn.Linear(embed_dim, embed_dim)

        # Query, Key, Value projections for graph attending to text
        self.graph_to_text_q = nn.Linear(embed_dim, embed_dim)
        self.graph_to_text_k = nn.Linear(embed_dim, embed_dim)
        self.graph_to_text_v = nn.Linear(embed_dim, embed_dim)

        self.dropout = nn.Dropout(dropout)
        self.scale = math.sqrt(self.head_dim)

        # Output projections
        self.text_out = nn.Linear(embed_dim, embed_dim)
        self.graph_out = nn.Linear(embed_dim, embed_dim)

    def forward(
        self,
        text_embed: torch.Tensor,
        graph_embed: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, Dict[str, torch.Tensor]]:
        """Compute cross-modal attention.

        Args:
            text_embed: Text embeddings [batch, embed_dim]
            graph_embed: Graph embeddings [batch, embed_dim]

        Returns:
            Tuple of:
                - text_attended: Text with graph context [batch, embed_dim]
                - graph_attended: Graph with text context [batch, embed_dim]
                - attention_weights: Dict with text_to_graph and graph_to_text weights
        """
        batch_size = text_embed.size(0)

        # Add sequence dimension for attention (batch, 1, dim)
        text_embed = text_embed.unsqueeze(1)
        graph_embed = graph_embed.unsqueeze(1)

        # Text attending to graph
        q_t2g = self.text_to_graph_q(text_embed)
        k_t2g = self.text_to_graph_k(graph_embed)
        v_t2g = self.text_to_graph_v(graph_embed)

        attn_t2g = torch.matmul(q_t2g, k_t2g.transpose(-2, -1)) / self.scale
        attn_t2g = F.softmax(attn_t2g, dim=-1)
        attn_t2g = self.dropout(attn_t2g)
        text_attended = torch.matmul(attn_t2g, v_t2g)
        text_attended = self.text_out(text_attended)

        # Graph attending to text
        q_g2t = self.graph_to_text_q(graph_embed)
        k_g2t = self.graph_to_text_k(text_embed)
        v_g2t = self.graph_to_text_v(text_embed)

        attn_g2t = torch.matmul(q_g2t, k_g2t.transpose(-2, -1)) / self.scale
        attn_g2t = F.softmax(attn_g2t, dim=-1)
        attn_g2t = self.dropout(attn_g2t)
        graph_attended = torch.matmul(attn_g2t, v_g2t)
        graph_attended = self.graph_out(graph_attended)

        # Remove sequence dimension
        text_attended = text_attended.squeeze(1)
        graph_attended = graph_attended.squeeze(1)

        attention_weights = {
            "text_to_graph": attn_t2g.squeeze(1),
            "graph_to_text": attn_g2t.squeeze(1),
        }

        return text_attended, graph_attended, attention_weights


class GatedFusion(nn.Module):
    """Gated fusion layer that learns modality importance.

    Uses learned gates to weight the contribution of each modality
    to the final fused representation. The gates are computed from
    the concatenation of both modalities, allowing the model to
    dynamically adjust weights based on input characteristics.

    For example:
    - Well-named code → higher text weight
    - Complex call patterns → higher graph weight
    """

    def __init__(self, embed_dim: int, dropout: float = 0.1):
        """Initialize gated fusion.

        Args:
            embed_dim: Embedding dimension
            dropout: Dropout rate
        """
        super().__init__()

        # Gating networks
        self.text_gate = nn.Sequential(
            nn.Linear(embed_dim * 2, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, 1),
            nn.Sigmoid(),
        )

        self.graph_gate = nn.Sequential(
            nn.Linear(embed_dim * 2, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, 1),
            nn.Sigmoid(),
        )

        # Fusion MLP
        self.fusion_mlp = nn.Sequential(
            nn.Linear(embed_dim * 2, embed_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim, embed_dim),
        )

        self.layer_norm = nn.LayerNorm(embed_dim)

    def forward(
        self,
        text_embed: torch.Tensor,
        graph_embed: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        """Compute gated fusion of modalities.

        Args:
            text_embed: Text embeddings [batch, embed_dim]
            graph_embed: Graph embeddings [batch, embed_dim]

        Returns:
            Tuple of:
                - fused: Fused embedding [batch, embed_dim]
                - gate_weights: Dict with text_weight and graph_weight
        """
        # Concatenate for gating context
        combined = torch.cat([text_embed, graph_embed], dim=-1)

        # Compute gates
        text_weight = self.text_gate(combined)
        graph_weight = self.graph_gate(combined)

        # Normalize gates to sum to 1
        total = text_weight + graph_weight + 1e-8
        text_weight = text_weight / total
        graph_weight = graph_weight / total

        # Weighted combination
        weighted_text = text_embed * text_weight
        weighted_graph = graph_embed * graph_weight

        # Fuse through MLP
        fused = torch.cat([weighted_text, weighted_graph], dim=-1)
        fused = self.fusion_mlp(fused)
        fused = self.layer_norm(fused)

        gate_weights = {
            "text_weight": text_weight.squeeze(-1),
            "graph_weight": graph_weight.squeeze(-1),
        }

        return fused, gate_weights


class MultimodalAttentionFusion(nn.Module):
    """Full multimodal fusion model combining text and graph embeddings.

    Architecture:
    1. Project both modalities to common dimension (fusion_dim)
    2. Apply cross-modal attention (bidirectional)
    3. Apply self-attention within each modality
    4. Gated fusion to combine modalities with learned weights
    5. Task-specific prediction heads

    This architecture enables:
    - Learning cross-modal correlations via attention
    - Dynamic modality weighting via gated fusion
    - Multi-task learning with shared representations
    """

    def __init__(self, config: Optional[FusionConfig] = None):
        """Initialize multimodal fusion model.

        Args:
            config: Model configuration (uses defaults if not provided)
        """
        super().__init__()
        self.config = config or FusionConfig()

        # Project to common dimension
        self.text_proj = nn.Sequential(
            nn.Linear(self.config.text_dim, self.config.fusion_dim),
            nn.ReLU(),
            nn.Dropout(self.config.dropout),
        )

        self.graph_proj = nn.Sequential(
            nn.Linear(self.config.graph_dim, self.config.fusion_dim),
            nn.ReLU(),
            nn.Dropout(self.config.dropout),
        )

        # Cross-modal attention
        self.cross_attention = CrossModalAttention(
            embed_dim=self.config.fusion_dim,
            num_heads=self.config.num_heads,
            dropout=self.config.dropout,
        )

        # Self-attention for each modality
        self.text_self_attn = nn.MultiheadAttention(
            embed_dim=self.config.fusion_dim,
            num_heads=self.config.num_heads,
            dropout=self.config.dropout,
            batch_first=True,
        )

        self.graph_self_attn = nn.MultiheadAttention(
            embed_dim=self.config.fusion_dim,
            num_heads=self.config.num_heads,
            dropout=self.config.dropout,
            batch_first=True,
        )

        # Gated fusion
        self.gated_fusion = GatedFusion(
            embed_dim=self.config.fusion_dim,
            dropout=self.config.dropout,
        )

        # Task-specific heads
        self.task_heads = nn.ModuleDict(
            {
                "bug_prediction": nn.Sequential(
                    nn.Linear(self.config.fusion_dim, 128),
                    nn.ReLU(),
                    nn.Dropout(self.config.dropout),
                    nn.Linear(128, 2),  # Binary: buggy or clean
                ),
                "smell_detection": nn.Sequential(
                    nn.Linear(self.config.fusion_dim, 128),
                    nn.ReLU(),
                    nn.Dropout(self.config.dropout),
                    nn.Linear(128, 5),  # Long Method, God Class, Feature Envy, Data Clump, None
                ),
                "refactoring_benefit": nn.Sequential(
                    nn.Linear(self.config.fusion_dim, 128),
                    nn.ReLU(),
                    nn.Dropout(self.config.dropout),
                    nn.Linear(128, 3),  # High/Medium/Low benefit
                ),
            }
        )

    def forward(
        self,
        text_embed: torch.Tensor,
        graph_embed: torch.Tensor,
        task: Optional[str] = None,
    ) -> Dict[str, torch.Tensor]:
        """Forward pass through fusion model.

        Args:
            text_embed: Text embeddings [batch, text_dim]
            graph_embed: Graph embeddings [batch, graph_dim]
            task: Specific task to predict (None = all tasks)

        Returns:
            Dict containing:
                - fused_embedding: Fused representation [batch, fusion_dim]
                - text_weight: Gate weight for text modality
                - graph_weight: Gate weight for graph modality
                - cross_attention: Cross-modal attention weights
                - {task}_logits: Prediction logits for each task
        """
        # Project to common dimension
        text_proj = self.text_proj(text_embed)
        graph_proj = self.graph_proj(graph_embed)

        # Cross-modal attention
        text_cross, graph_cross, cross_attn = self.cross_attention(
            text_proj, graph_proj
        )

        # Residual connection
        text_proj = text_proj + text_cross
        graph_proj = graph_proj + graph_cross

        # Self-attention (add sequence dim, then remove)
        text_proj = text_proj.unsqueeze(1)
        graph_proj = graph_proj.unsqueeze(1)

        text_self, _ = self.text_self_attn(text_proj, text_proj, text_proj)
        graph_self, _ = self.graph_self_attn(graph_proj, graph_proj, graph_proj)

        text_proj = text_proj.squeeze(1) + text_self.squeeze(1)
        graph_proj = graph_proj.squeeze(1) + graph_self.squeeze(1)

        # Gated fusion
        fused, gate_weights = self.gated_fusion(text_proj, graph_proj)

        # Task predictions
        outputs = {
            "fused_embedding": fused,
            "text_weight": gate_weights["text_weight"],
            "graph_weight": gate_weights["graph_weight"],
            "cross_attention": cross_attn,
        }

        if task is not None:
            # Single task prediction
            if task not in self.task_heads:
                raise ValueError(
                    f"Unknown task '{task}'. Available: {list(self.task_heads.keys())}"
                )
            outputs[f"{task}_logits"] = self.task_heads[task](fused)
        else:
            # All task predictions
            for task_name, head in self.task_heads.items():
                outputs[f"{task_name}_logits"] = head(fused)

        return outputs

    def get_fused_embedding(
        self,
        text_embed: torch.Tensor,
        graph_embed: torch.Tensor,
    ) -> torch.Tensor:
        """Get fused embedding without task predictions.

        Useful for downstream tasks or similarity search.

        Args:
            text_embed: Text embeddings [batch, text_dim]
            graph_embed: Graph embeddings [batch, graph_dim]

        Returns:
            Fused embedding [batch, fusion_dim]
        """
        outputs = self.forward(text_embed, graph_embed)
        return outputs["fused_embedding"]


class MultiTaskLoss(nn.Module):
    """Multi-task loss with learnable task weights.

    Uses uncertainty weighting (Kendall et al., 2018) to automatically
    balance task losses. Each task has a learnable log variance parameter
    that controls its contribution to the total loss.

    Higher variance → lower weight (more uncertain tasks contribute less)

    Reference:
        Multi-Task Learning Using Uncertainty to Weigh Losses
        https://arxiv.org/abs/1705.07115
    """

    def __init__(self, tasks: List[str]):
        """Initialize multi-task loss.

        Args:
            tasks: List of task names
        """
        super().__init__()
        self.tasks = tasks

        # Learnable log variances for task weighting
        self.log_vars = nn.ParameterDict(
            {task: nn.Parameter(torch.zeros(1)) for task in tasks}
        )

        self.loss_fns = {
            "bug_prediction": nn.CrossEntropyLoss(),
            "smell_detection": nn.CrossEntropyLoss(),
            "refactoring_benefit": nn.CrossEntropyLoss(),
        }

    def forward(
        self,
        predictions: Dict[str, torch.Tensor],
        targets: Dict[str, torch.Tensor],
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """Compute weighted multi-task loss.

        Args:
            predictions: Dict of task predictions (task_logits keys)
            targets: Dict of task targets (task keys)

        Returns:
            Tuple of:
                - total_loss: Weighted sum of task losses
                - task_losses: Dict of individual task loss values
        """
        total_loss = torch.tensor(0.0, device=next(iter(predictions.values())).device)
        task_losses = {}

        for task in self.tasks:
            logits_key = f"{task}_logits"
            if logits_key in predictions and task in targets:
                pred = predictions[logits_key]
                target = targets[task]

                # Task-specific loss
                loss = self.loss_fns[task](pred, target)
                task_losses[task] = loss.item()

                # Uncertainty weighting
                precision = torch.exp(-self.log_vars[task])
                weighted_loss = precision * loss + self.log_vars[task]

                total_loss = total_loss + weighted_loss

        return total_loss, task_losses

    def get_task_weights(self) -> Dict[str, float]:
        """Get current task weights derived from log variances.

        Returns:
            Dict mapping task names to their current weights
        """
        weights = {}
        for task in self.tasks:
            # Weight is inversely proportional to variance
            weights[task] = torch.exp(-self.log_vars[task]).item()
        return weights
