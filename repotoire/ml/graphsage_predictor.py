"""GraphSAGE model for inductive defect prediction.

GraphSAGE (Graph SAmple and aggreGatE) is a framework for inductive representation
learning on graphs. Unlike transductive methods like Node2Vec that learn embeddings
for specific nodes, GraphSAGE learns aggregation functions that can generalize to
completely unseen nodes and graphs.

Key advantages:
- **Inductive**: Can generate embeddings for new nodes without retraining
- **Cross-project**: Train on open-source projects, apply to any new codebase
- **Zero-shot**: No project-specific training data required for inference
- **Scalable**: Mini-batch training via neighborhood sampling

Research shows GraphSAGE achieves:
- 98.4% AUC for vulnerability detection
- 75-80% cross-project accuracy for defect prediction

This module implements:
- `GraphSAGEDefectPredictor`: Standard GraphSAGE with mean/max/lstm aggregation
- `GraphSAGEWithAttention`: Enhanced version with attention-based neighbor weighting
- `GraphSAGEConfig`: Configuration dataclass for model hyperparameters
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Check for PyTorch Geometric availability
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch_geometric.nn import SAGEConv, GATConv, global_mean_pool
    from torch_geometric.data import Data
    TORCH_GEOMETRIC_AVAILABLE = True
except ImportError:
    TORCH_GEOMETRIC_AVAILABLE = False
    torch = None
    nn = None


@dataclass
class GraphSAGEConfig:
    """Configuration for GraphSAGE model.

    Attributes:
        input_dim: Input feature dimension (default: 256)
            Should match combined embedding + metrics dimension
        hidden_dim: Hidden layer dimension (default: 128)
        output_dim: Output classes (default: 2 for buggy/clean)
        num_layers: Number of GraphSAGE layers (default: 2)
            Each layer aggregates k-hop neighbors
        dropout: Dropout rate for regularization (default: 0.5)
        aggregator: Aggregation type (default: "mean")
            Options: "mean", "max", "lstm"
    """
    input_dim: int = 256
    hidden_dim: int = 128
    output_dim: int = 2
    num_layers: int = 2
    dropout: float = 0.5
    aggregator: str = "mean"


if TORCH_GEOMETRIC_AVAILABLE:

    class GraphSAGEDefectPredictor(nn.Module):
        """GraphSAGE model for defect prediction.

        Key insight: GraphSAGE learns to AGGREGATE neighbor features,
        not specific node embeddings. This enables generalization to
        completely unseen graphs (inductive learning).

        Architecture:
        1. Multiple SAGEConv layers aggregate neighborhood information
        2. Each layer samples and aggregates k-hop neighbors
        3. Batch normalization and dropout for regularization
        4. Final linear layers predict defect probability

        Example:
            >>> config = GraphSAGEConfig(input_dim=256, hidden_dim=128)
            >>> model = GraphSAGEDefectPredictor(config)
            >>> out = model(x, edge_index)
            >>> probabilities = F.softmax(out, dim=1)
        """

        def __init__(self, config: Optional[GraphSAGEConfig] = None):
            """Initialize GraphSAGE model.

            Args:
                config: Model configuration (uses defaults if not provided)
            """
            super().__init__()
            self.config = config or GraphSAGEConfig()

            # GraphSAGE convolutional layers
            self.convs = nn.ModuleList()

            # First layer: input_dim -> hidden_dim
            self.convs.append(SAGEConv(
                self.config.input_dim,
                self.config.hidden_dim,
                aggr=self.config.aggregator,
            ))

            # Hidden layers: hidden_dim -> hidden_dim
            for _ in range(self.config.num_layers - 1):
                self.convs.append(SAGEConv(
                    self.config.hidden_dim,
                    self.config.hidden_dim,
                    aggr=self.config.aggregator,
                ))

            # Batch normalization for each layer
            self.batch_norms = nn.ModuleList([
                nn.BatchNorm1d(self.config.hidden_dim)
                for _ in range(self.config.num_layers)
            ])

            # Prediction head
            self.lin1 = nn.Linear(self.config.hidden_dim, self.config.hidden_dim // 2)
            self.lin2 = nn.Linear(self.config.hidden_dim // 2, self.config.output_dim)

            self.dropout = nn.Dropout(self.config.dropout)

        def forward(
            self,
            x: torch.Tensor,
            edge_index: torch.Tensor,
        ) -> torch.Tensor:
            """Forward pass through GraphSAGE.

            Args:
                x: Node features [num_nodes, input_dim]
                edge_index: Graph connectivity [2, num_edges]

            Returns:
                Node predictions [num_nodes, output_dim]
            """
            # Handle empty graphs or single nodes (BatchNorm needs batch size > 1)
            if x.size(0) == 0:
                return torch.zeros((0, self.config.output_dim), device=x.device)

            # GraphSAGE message passing
            for i, conv in enumerate(self.convs):
                x = conv(x, edge_index)
                # Skip batch norm for single node (batch norm requires > 1 sample)
                if x.size(0) > 1:
                    x = self.batch_norms[i](x)
                x = F.relu(x)
                x = self.dropout(x)

            # Prediction head
            x = self.lin1(x)
            x = F.relu(x)
            x = self.dropout(x)
            x = self.lin2(x)

            return x

        def get_embeddings(
            self,
            x: torch.Tensor,
            edge_index: torch.Tensor,
        ) -> torch.Tensor:
            """Get node embeddings without prediction head.

            Useful for downstream tasks, similarity search, or
            transfer learning.

            Args:
                x: Node features [num_nodes, input_dim]
                edge_index: Graph connectivity [2, num_edges]

            Returns:
                Node embeddings [num_nodes, hidden_dim]
            """
            # Handle empty graphs
            if x.size(0) == 0:
                return torch.zeros((0, self.config.hidden_dim), device=x.device)

            for i, conv in enumerate(self.convs):
                x = conv(x, edge_index)
                # Skip batch norm for single node (batch norm requires > 1 sample)
                if x.size(0) > 1:
                    x = self.batch_norms[i](x)
                x = F.relu(x)
                # No dropout during embedding extraction

            return x


    class GraphSAGEWithAttention(nn.Module):
        """GraphSAGE with attention mechanism for neighbor weighting.

        Enhanced version that learns to weight neighbors by importance,
        providing better aggregation for heterogeneous code graphs.

        Uses Graph Attention Networks (GAT) instead of standard aggregation,
        which can:
        - Learn which neighbors are most relevant
        - Handle heterogeneous relationships (CALLS vs IMPORTS)
        - Provide interpretable attention weights

        Example:
            >>> config = GraphSAGEConfig(input_dim=256, hidden_dim=128)
            >>> model = GraphSAGEWithAttention(config)
            >>> out = model(x, edge_index)
        """

        def __init__(self, config: Optional[GraphSAGEConfig] = None):
            """Initialize GraphSAGE with attention.

            Args:
                config: Model configuration (uses defaults if not provided)
            """
            super().__init__()
            self.config = config or GraphSAGEConfig()

            # Use GAT layers for attention-based aggregation
            self.convs = nn.ModuleList()
            self.num_heads = 4

            # First layer: input_dim -> hidden_dim
            self.convs.append(GATConv(
                self.config.input_dim,
                self.config.hidden_dim // self.num_heads,
                heads=self.num_heads,
                dropout=self.config.dropout,
            ))

            # Hidden layers: hidden_dim -> hidden_dim
            for _ in range(self.config.num_layers - 1):
                self.convs.append(GATConv(
                    self.config.hidden_dim,
                    self.config.hidden_dim // self.num_heads,
                    heads=self.num_heads,
                    dropout=self.config.dropout,
                ))

            # Prediction head
            self.lin1 = nn.Linear(self.config.hidden_dim, self.config.hidden_dim // 2)
            self.lin2 = nn.Linear(self.config.hidden_dim // 2, self.config.output_dim)

            self.dropout = nn.Dropout(self.config.dropout)

        def forward(
            self,
            x: torch.Tensor,
            edge_index: torch.Tensor,
        ) -> torch.Tensor:
            """Forward pass with attention.

            Args:
                x: Node features [num_nodes, input_dim]
                edge_index: Graph connectivity [2, num_edges]

            Returns:
                Node predictions [num_nodes, output_dim]
            """
            # Handle empty graphs
            if x.size(0) == 0:
                return torch.zeros((0, self.config.output_dim), device=x.device)

            for conv in self.convs:
                x = conv(x, edge_index)
                x = F.elu(x)
                x = self.dropout(x)

            x = self.lin1(x)
            x = F.relu(x)
            x = self.dropout(x)
            x = self.lin2(x)

            return x

        def get_embeddings(
            self,
            x: torch.Tensor,
            edge_index: torch.Tensor,
        ) -> torch.Tensor:
            """Get node embeddings without prediction head.

            Args:
                x: Node features [num_nodes, input_dim]
                edge_index: Graph connectivity [2, num_edges]

            Returns:
                Node embeddings [num_nodes, hidden_dim]
            """
            if x.size(0) == 0:
                return torch.zeros((0, self.config.hidden_dim), device=x.device)

            for conv in self.convs:
                x = conv(x, edge_index)
                x = F.elu(x)

            return x


    class GraphFeatureExtractor:
        """Extract and normalize features from Neo4j graph for GraphSAGE.

        Combines existing embeddings with code metrics to create a
        consistent feature vector for training and inference.

        Feature vector (256 dimensions by default):
        - [0:254] Semantic embedding (truncated/padded)
        - [254] Cyclomatic complexity (normalized)
        - [255] Lines of code (normalized)
        """

        def __init__(
            self,
            client,
            embedding_property: str = "embedding",
            input_dim: int = 256,
        ):
            """Initialize feature extractor.

            Args:
                client: Neo4j database client
                embedding_property: Node property containing embeddings
                input_dim: Total feature dimension for GraphSAGE input
            """
            self.client = client
            self.embedding_property = embedding_property
            self.input_dim = input_dim
            self.metrics_dim = 2  # complexity, loc

        def extract_graph_data(
            self,
            labels: Optional[Dict[str, int]] = None,
        ) -> Tuple[Data, Dict[str, int]]:
            """Extract graph data from Neo4j in PyTorch Geometric format.

            Args:
                labels: Optional dict mapping qualified_name -> label (0/1)
                    If not provided, all labels will be 0

            Returns:
                Tuple of (PyG Data object, node_mapping dict)
            """
            # Fetch nodes with features
            node_query = f"""
            MATCH (f:Function)
            WHERE f.{self.embedding_property} IS NOT NULL
            RETURN
                f.qualifiedName AS qualified_name,
                f.{self.embedding_property} AS embedding,
                f.complexity AS complexity,
                f.loc AS loc
            """
            nodes = self.client.execute_query(node_query)

            if not nodes:
                logger.warning("No functions with embeddings found")
                return Data(
                    x=torch.zeros((0, self.input_dim)),
                    edge_index=torch.zeros((2, 0), dtype=torch.long),
                ), {}

            # Build node mapping
            node_mapping = {n["qualified_name"]: i for i, n in enumerate(nodes)}
            num_nodes = len(nodes)

            # Build feature matrix
            features = []
            node_labels = []

            for node in nodes:
                embedding = np.array(node["embedding"])

                # Pad/truncate to input_dim - metrics_dim
                embed_target_dim = self.input_dim - self.metrics_dim
                if len(embedding) < embed_target_dim:
                    embedding = np.pad(embedding, (0, embed_target_dim - len(embedding)))
                elif len(embedding) > embed_target_dim:
                    embedding = embedding[:embed_target_dim]

                # Code metrics (normalized)
                complexity = min((node.get("complexity") or 1) / 50.0, 1.0)
                loc = min((node.get("loc") or 10) / 500.0, 1.0)

                feature = np.concatenate([embedding, [complexity, loc]])
                features.append(feature)

                # Labels (default to 0 if not provided)
                label = 0
                if labels:
                    label = labels.get(node["qualified_name"], 0)
                node_labels.append(label)

            # Fetch edges (CALLS relationships)
            edge_query = f"""
            MATCH (f1:Function)-[:CALLS]->(f2:Function)
            WHERE f1.{self.embedding_property} IS NOT NULL
              AND f2.{self.embedding_property} IS NOT NULL
            RETURN f1.qualifiedName AS source, f2.qualifiedName AS target
            """
            edges = self.client.execute_query(edge_query)

            # Build edge index
            edge_list = []
            for edge in edges:
                src = edge["source"]
                tgt = edge["target"]
                if src in node_mapping and tgt in node_mapping:
                    edge_list.append([node_mapping[src], node_mapping[tgt]])

            # Convert to tensors
            x = torch.FloatTensor(np.array(features))
            y = torch.LongTensor(node_labels)

            if edge_list:
                edge_index = torch.LongTensor(edge_list).t().contiguous()
            else:
                edge_index = torch.zeros((2, 0), dtype=torch.long)

            # Create PyG Data object
            data = Data(x=x, y=y, edge_index=edge_index)

            logger.info(
                f"Extracted graph: {num_nodes} nodes, {len(edge_list)} edges"
            )

            return data, node_mapping

        def create_train_test_masks(
            self,
            data: Data,
            train_ratio: float = 0.8,
            seed: int = 42,
        ) -> Data:
            """Add train/test masks to data.

            Args:
                data: PyG Data object
                train_ratio: Fraction for training (default: 0.8)
                seed: Random seed for reproducibility

            Returns:
                Data object with train_mask and test_mask attributes
            """
            np.random.seed(seed)
            num_nodes = data.x.size(0)

            indices = np.random.permutation(num_nodes)
            train_size = int(num_nodes * train_ratio)

            train_mask = torch.zeros(num_nodes, dtype=torch.bool)
            test_mask = torch.zeros(num_nodes, dtype=torch.bool)

            train_mask[indices[:train_size]] = True
            test_mask[indices[train_size:]] = True

            data.train_mask = train_mask
            data.test_mask = test_mask

            return data

else:
    # Stub classes when PyTorch Geometric is not available
    class GraphSAGEDefectPredictor:
        """Stub: PyTorch Geometric required."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PyTorch Geometric required for GraphSAGE. "
                "Install with: pip install torch torch-geometric"
            )


    class GraphSAGEWithAttention:
        """Stub: PyTorch Geometric required."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PyTorch Geometric required for GraphSAGE. "
                "Install with: pip install torch torch-geometric"
            )


    class GraphFeatureExtractor:
        """Stub: PyTorch Geometric required."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PyTorch Geometric required for GraphSAGE. "
                "Install with: pip install torch torch-geometric"
            )
