"""Cross-project training for GraphSAGE zero-shot defect prediction.

This module provides the infrastructure for training GraphSAGE models on
multiple open-source projects to enable zero-shot generalization to any
new codebase.

Key capabilities:
- Load and combine graph data from multiple projects
- Train with mini-batch sampling via NeighborLoader
- Hold out entire projects for validation (true cross-project evaluation)
- Save/load trained models for deployment

Training workflow:
1. Extract labels from multiple projects' git history
2. Load and combine project graphs
3. Train GraphSAGE with early stopping
4. Evaluate on held-out project (zero-shot)
5. Save model for inference on any new codebase
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Check for PyTorch availability
try:
    import torch
    import torch.nn.functional as F
    from torch.optim import AdamW
    from torch.optim.lr_scheduler import CosineAnnealingLR
    from torch_geometric.data import Data
    from torch_geometric.loader import NeighborLoader
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None

try:
    from sklearn.metrics import roc_auc_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


@dataclass
class CrossProjectTrainingConfig:
    """Configuration for cross-project training.

    Attributes:
        epochs: Maximum training epochs (default: 100)
        batch_size: Mini-batch size for neighbor sampling (default: 128)
        learning_rate: Initial learning rate (default: 0.001)
        weight_decay: L2 regularization strength (default: 0.01)
        num_neighbors: Neighbors to sample per hop (default: [10, 5])
            First element is 1-hop neighbors, second is 2-hop neighbors
        early_stopping_patience: Epochs without improvement before stopping
        device: Compute device (auto-detects GPU if available)
    """
    epochs: int = 100
    batch_size: int = 128
    learning_rate: float = 0.001
    weight_decay: float = 0.01
    num_neighbors: List[int] = field(default_factory=lambda: [10, 5])
    early_stopping_patience: int = 15
    device: str = field(
        default_factory=lambda: "cuda" if torch and torch.cuda.is_available() else "cpu"
    )


@dataclass
class ProjectGraphData:
    """Graph data for a single project.

    Encapsulates a project's graph along with metadata for training.

    Attributes:
        project_name: Human-readable project identifier
        data: PyTorch Geometric Data object with features, labels, edges
        node_mapping: Maps qualified_name -> node index
        num_buggy: Count of functions labeled as buggy
        num_clean: Count of functions labeled as clean
    """
    project_name: str
    data: Any  # Data object (typed as Any to avoid import issues)
    node_mapping: Dict[str, int]
    num_buggy: int
    num_clean: int

    @property
    def total_functions(self) -> int:
        """Total number of functions in project."""
        return self.num_buggy + self.num_clean


@dataclass
class TrainingHistory:
    """Records training metrics over epochs.

    Attributes:
        train_loss: Loss per epoch
        train_acc: Training accuracy per epoch
        val_acc: Validation accuracy per epoch
        val_auc: Validation AUC-ROC per epoch
    """
    train_loss: List[float] = field(default_factory=list)
    train_acc: List[float] = field(default_factory=list)
    val_acc: List[float] = field(default_factory=list)
    val_auc: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, List[float]]:
        """Convert to dictionary."""
        return {
            "train_loss": self.train_loss,
            "train_acc": self.train_acc,
            "val_acc": self.val_acc,
            "val_auc": self.val_auc,
        }


if TORCH_AVAILABLE:
    from repotoire.ml.graphsage_predictor import (
        GraphSAGEDefectPredictor,
        GraphSAGEConfig,
        GraphFeatureExtractor,
    )


    class CrossProjectDataLoader:
        """Load and combine graph data from multiple projects.

        Handles loading project graphs from Neo4j instances and combining
        them into a single training graph while preserving project boundaries
        for held-out evaluation.

        Example:
            >>> loader = CrossProjectDataLoader({"proj_a": client_a, "proj_b": client_b})
            >>> graphs = [loader.load_project_graph("proj_a", labels_a)]
            >>> train_data, holdout = loader.combine_projects(graphs, holdout="proj_b")
        """

        def __init__(
            self,
            clients: Optional[Dict[str, Any]] = None,
            embedding_property: str = "embedding",
            input_dim: int = 256,
        ):
            """Initialize data loader.

            Args:
                clients: Dict mapping project_name -> Neo4jClient
                    Can be None if loading from exported data
                embedding_property: Node property containing embeddings
                input_dim: Input feature dimension for GraphSAGE
            """
            self.clients = clients or {}
            self.embedding_property = embedding_property
            self.input_dim = input_dim

        def load_project_graph(
            self,
            project_name: str,
            labels: Dict[str, int],
            train_ratio: float = 0.8,
        ) -> ProjectGraphData:
            """Load graph data for a single project.

            Args:
                project_name: Name of the project (must be in clients)
                labels: Dict mapping qualified_name -> label (0=clean, 1=buggy)
                train_ratio: Fraction of data for training (rest is testing)

            Returns:
                ProjectGraphData with PyG Data object

            Raises:
                ValueError: If project not found in clients
            """
            if project_name not in self.clients:
                raise ValueError(f"Project '{project_name}' not found in clients")

            client = self.clients[project_name]
            extractor = GraphFeatureExtractor(
                client,
                embedding_property=self.embedding_property,
                input_dim=self.input_dim,
            )

            # Extract graph data
            data, node_mapping = extractor.extract_graph_data(labels=labels)

            # Add train/test masks
            data = extractor.create_train_test_masks(data, train_ratio=train_ratio)

            # Count labels
            num_buggy = int(data.y.sum().item())
            num_clean = len(data.y) - num_buggy

            logger.info(
                f"Loaded {project_name}: {len(data.y)} functions, "
                f"{num_buggy} buggy, {num_clean} clean"
            )

            return ProjectGraphData(
                project_name=project_name,
                data=data,
                node_mapping=node_mapping,
                num_buggy=num_buggy,
                num_clean=num_clean,
            )

        def load_project_from_json(
            self,
            project_name: str,
            features_path: Path,
            edges_path: Path,
            labels: Dict[str, int],
            train_ratio: float = 0.8,
        ) -> ProjectGraphData:
            """Load project graph from exported JSON files.

            Useful for loading pre-exported graphs without Neo4j connection.

            Args:
                project_name: Project identifier
                features_path: Path to JSON file with node features
                edges_path: Path to JSON file with edge list
                labels: Dict mapping qualified_name -> label
                train_ratio: Train/test split ratio

            Returns:
                ProjectGraphData with loaded graph
            """
            # Load features
            with open(features_path) as f:
                features_data = json.load(f)

            # Load edges
            with open(edges_path) as f:
                edges_data = json.load(f)

            # Build node mapping and feature matrix
            node_mapping = {}
            features = []
            node_labels = []

            for i, node in enumerate(features_data["nodes"]):
                qname = node["qualified_name"]
                node_mapping[qname] = i

                # Get embedding and pad/truncate
                embedding = np.array(node["embedding"])
                embed_target_dim = self.input_dim - 2
                if len(embedding) < embed_target_dim:
                    embedding = np.pad(embedding, (0, embed_target_dim - len(embedding)))
                elif len(embedding) > embed_target_dim:
                    embedding = embedding[:embed_target_dim]

                # Metrics
                complexity = min((node.get("complexity") or 1) / 50.0, 1.0)
                loc = min((node.get("loc") or 10) / 500.0, 1.0)

                feature = np.concatenate([embedding, [complexity, loc]])
                features.append(feature)

                # Label
                node_labels.append(labels.get(qname, 0))

            # Build edge index
            edge_list = []
            for edge in edges_data["edges"]:
                src, tgt = edge["source"], edge["target"]
                if src in node_mapping and tgt in node_mapping:
                    edge_list.append([node_mapping[src], node_mapping[tgt]])

            # Convert to tensors
            x = torch.FloatTensor(np.array(features))
            y = torch.LongTensor(node_labels)
            edge_index = (
                torch.LongTensor(edge_list).t().contiguous()
                if edge_list
                else torch.zeros((2, 0), dtype=torch.long)
            )

            # Create masks
            num_nodes = x.size(0)
            np.random.seed(42)
            indices = np.random.permutation(num_nodes)
            train_size = int(num_nodes * train_ratio)

            train_mask = torch.zeros(num_nodes, dtype=torch.bool)
            test_mask = torch.zeros(num_nodes, dtype=torch.bool)
            train_mask[indices[:train_size]] = True
            test_mask[indices[train_size:]] = True

            data = Data(
                x=x,
                y=y,
                edge_index=edge_index,
                train_mask=train_mask,
                test_mask=test_mask,
            )

            num_buggy = int(y.sum().item())
            num_clean = len(y) - num_buggy

            return ProjectGraphData(
                project_name=project_name,
                data=data,
                node_mapping=node_mapping,
                num_buggy=num_buggy,
                num_clean=num_clean,
            )

        def combine_projects(
            self,
            project_graphs: List[ProjectGraphData],
            holdout_project: Optional[str] = None,
        ) -> Tuple[Data, Optional[Data]]:
            """Combine multiple project graphs for training.

            Merges node features and edges from all projects into a single
            graph. Optionally holds out one project for cross-project evaluation.

            Args:
                project_graphs: List of project graph data
                holdout_project: Project name to hold out (for zero-shot testing)

            Returns:
                Tuple of (combined_train_data, holdout_data)
                holdout_data is None if holdout_project not specified
            """
            holdout_data = None
            all_x = []
            all_y = []
            all_edges = []
            all_train_mask = []
            all_test_mask = []

            node_offset = 0

            for pg in project_graphs:
                if pg.project_name == holdout_project:
                    holdout_data = pg.data
                    continue

                all_x.append(pg.data.x)
                all_y.append(pg.data.y)

                # Offset edge indices to account for combined node list
                edges = pg.data.edge_index + node_offset
                all_edges.append(edges)

                all_train_mask.append(pg.data.train_mask)
                all_test_mask.append(pg.data.test_mask)

                node_offset += pg.data.x.size(0)

            if not all_x:
                raise ValueError("No projects to combine (all held out?)")

            # Concatenate everything
            combined_data = Data(
                x=torch.cat(all_x, dim=0),
                y=torch.cat(all_y, dim=0),
                edge_index=torch.cat(all_edges, dim=1) if all_edges else torch.zeros((2, 0), dtype=torch.long),
                train_mask=torch.cat(all_train_mask, dim=0),
                test_mask=torch.cat(all_test_mask, dim=0),
            )

            total_buggy = combined_data.y.sum().item()
            total_clean = len(combined_data.y) - total_buggy
            logger.info(
                f"Combined {len(project_graphs) - (1 if holdout_data else 0)} projects: "
                f"{len(combined_data.y)} functions, {total_buggy} buggy, {total_clean} clean"
            )

            return combined_data, holdout_data


    class CrossProjectTrainer:
        """Train GraphSAGE on multiple projects for zero-shot generalization.

        Implements mini-batch training with neighborhood sampling for scalability,
        early stopping for preventing overfitting, and cross-project evaluation.

        Example:
            >>> trainer = CrossProjectTrainer()
            >>> history = trainer.train(train_data, val_data=holdout_data)
            >>> predictions = trainer.predict_zero_shot(new_project_data)
            >>> trainer.save(Path("models/graphsage.pt"))
        """

        def __init__(
            self,
            model_config: Optional[GraphSAGEConfig] = None,
            training_config: Optional[CrossProjectTrainingConfig] = None,
        ):
            """Initialize trainer.

            Args:
                model_config: GraphSAGE model configuration
                training_config: Training hyperparameters
            """
            self.model_config = model_config or GraphSAGEConfig()
            self.training_config = training_config or CrossProjectTrainingConfig()

            self.model: Optional[GraphSAGEDefectPredictor] = None
            self.device = torch.device(self.training_config.device)
            self._is_trained = False
            self._best_state: Optional[Dict] = None

        def train(
            self,
            train_data: Data,
            val_data: Optional[Data] = None,
        ) -> TrainingHistory:
            """Train GraphSAGE model.

            Uses mini-batch training with NeighborLoader for scalability.
            Supports optional validation on held-out project data.

            Args:
                train_data: Combined training graph data
                val_data: Optional held-out project for validation

            Returns:
                TrainingHistory with metrics per epoch
            """
            # Initialize model
            self.model = GraphSAGEDefectPredictor(self.model_config)
            self.model.to(self.device)

            # Move data to device
            train_data = train_data.to(self.device)
            if val_data is not None:
                val_data = val_data.to(self.device)

            # Setup optimizer and scheduler
            optimizer = AdamW(
                self.model.parameters(),
                lr=self.training_config.learning_rate,
                weight_decay=self.training_config.weight_decay,
            )
            scheduler = CosineAnnealingLR(
                optimizer,
                T_max=self.training_config.epochs,
            )

            # Class weights for imbalanced data
            num_buggy = train_data.y[train_data.train_mask].sum().item()
            num_total = train_data.train_mask.sum().item()
            num_clean = num_total - num_buggy

            if num_buggy > 0 and num_clean > 0:
                weight = torch.FloatTensor([
                    num_buggy / num_total,
                    num_clean / num_total,
                ]).to(self.device)
            else:
                weight = None

            criterion = torch.nn.CrossEntropyLoss(weight=weight)

            # Mini-batch loader for scalability
            train_loader = NeighborLoader(
                train_data,
                num_neighbors=self.training_config.num_neighbors,
                batch_size=self.training_config.batch_size,
                input_nodes=train_data.train_mask,
                shuffle=True,
            )

            # Training loop
            history = TrainingHistory()
            best_val_acc = 0.0
            patience_counter = 0

            for epoch in range(self.training_config.epochs):
                # Training phase
                self.model.train()
                total_loss = 0.0
                total_correct = 0
                total_samples = 0

                for batch in train_loader:
                    batch = batch.to(self.device)
                    optimizer.zero_grad()

                    out = self.model(batch.x, batch.edge_index)

                    # Only compute loss for batch nodes (not sampled neighbors)
                    batch_size = batch.batch_size
                    out = out[:batch_size]
                    y = batch.y[:batch_size]

                    loss = criterion(out, y)
                    loss.backward()
                    optimizer.step()

                    total_loss += loss.item() * batch_size
                    pred = out.argmax(dim=1)
                    total_correct += (pred == y).sum().item()
                    total_samples += batch_size

                train_loss = total_loss / max(total_samples, 1)
                train_acc = total_correct / max(total_samples, 1)
                history.train_loss.append(train_loss)
                history.train_acc.append(train_acc)

                # Validation phase
                if val_data is not None:
                    val_acc, val_auc = self._evaluate(val_data)
                    history.val_acc.append(val_acc)
                    history.val_auc.append(val_auc)

                    # Early stopping
                    if val_acc > best_val_acc:
                        best_val_acc = val_acc
                        patience_counter = 0
                        self._best_state = {
                            k: v.cpu().clone() for k, v in self.model.state_dict().items()
                        }
                    else:
                        patience_counter += 1
                        if patience_counter >= self.training_config.early_stopping_patience:
                            logger.info(f"Early stopping at epoch {epoch + 1}")
                            break
                else:
                    history.val_acc.append(0.0)
                    history.val_auc.append(0.0)

                scheduler.step()

                # Logging
                if (epoch + 1) % 10 == 0:
                    val_str = (
                        f", Val Acc={history.val_acc[-1]:.3f}, Val AUC={history.val_auc[-1]:.3f}"
                        if val_data is not None
                        else ""
                    )
                    logger.info(
                        f"Epoch {epoch + 1}: Loss={train_loss:.4f}, "
                        f"Train Acc={train_acc:.3f}{val_str}"
                    )

            # Load best model if we have validation data
            if val_data is not None and self._best_state is not None:
                self.model.load_state_dict(self._best_state)

            self._is_trained = True
            return history

        def _evaluate(self, data: Data) -> Tuple[float, float]:
            """Evaluate model on data.

            Args:
                data: Graph data to evaluate

            Returns:
                Tuple of (accuracy, auc_roc)
            """
            self.model.eval()

            with torch.no_grad():
                out = self.model(data.x, data.edge_index)
                pred = out.argmax(dim=1)
                probs = F.softmax(out, dim=1)[:, 1]

                # Use test_mask if available, otherwise evaluate all
                if hasattr(data, "test_mask") and data.test_mask is not None:
                    mask = data.test_mask
                else:
                    mask = torch.ones(data.y.size(0), dtype=torch.bool, device=data.y.device)

                correct = (pred[mask] == data.y[mask]).sum().item()
                total = mask.sum().item()
                accuracy = correct / max(total, 1)

                # AUC-ROC
                auc = 0.0
                if SKLEARN_AVAILABLE:
                    try:
                        y_true = data.y[mask].cpu().numpy()
                        y_score = probs[mask].cpu().numpy()
                        # Need both classes present
                        if len(np.unique(y_true)) > 1:
                            auc = roc_auc_score(y_true, y_score)
                    except Exception as e:
                        logger.warning(f"Could not compute AUC: {e}")

            return accuracy, auc

        def predict_zero_shot(
            self,
            new_data: Data,
        ) -> List[Dict[str, Any]]:
            """Apply trained model to completely new codebase (zero-shot).

            This is the key capability: the model was trained on other projects
            but can generate predictions for any new codebase without retraining.

            Args:
                new_data: Graph data from unseen project

            Returns:
                List of predictions with probabilities

            Raises:
                RuntimeError: If model has not been trained
            """
            if not self._is_trained or self.model is None:
                raise RuntimeError("Model not trained. Call train() first.")

            new_data = new_data.to(self.device)

            self.model.eval()
            with torch.no_grad():
                out = self.model(new_data.x, new_data.edge_index)
                probs = F.softmax(out, dim=1)

            predictions = []
            for i in range(new_data.x.size(0)):
                prob_buggy = probs[i, 1].item()
                predictions.append({
                    "node_idx": i,
                    "buggy_probability": prob_buggy,
                    "prediction": "buggy" if prob_buggy > 0.5 else "clean",
                })

            return predictions

        def get_embeddings(self, data: Data) -> np.ndarray:
            """Get GraphSAGE embeddings for a codebase.

            Useful for similarity analysis or downstream tasks.

            Args:
                data: Graph data

            Returns:
                Numpy array of embeddings [num_nodes, hidden_dim]

            Raises:
                RuntimeError: If model not trained
            """
            if not self._is_trained or self.model is None:
                raise RuntimeError("Model not trained. Call train() first.")

            data = data.to(self.device)

            self.model.eval()
            with torch.no_grad():
                embeddings = self.model.get_embeddings(data.x, data.edge_index)

            return embeddings.cpu().numpy()

        def save(self, path: Path) -> None:
            """Save trained model to disk.

            Args:
                path: Path to save the model

            Raises:
                RuntimeError: If model has not been trained
            """
            if not self._is_trained or self.model is None:
                raise RuntimeError("Model not trained. Call train() first.")

            path.parent.mkdir(parents=True, exist_ok=True)

            torch.save({
                "model_state": self.model.state_dict(),
                "model_config": self.model_config,
                "training_config": self.training_config,
            }, path)

            logger.info(f"Model saved to {path}")

        @classmethod
        def load(cls, path: Path) -> "CrossProjectTrainer":
            """Load trained model from disk.

            Args:
                path: Path to the saved model

            Returns:
                CrossProjectTrainer with loaded model
            """
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)

            trainer = cls(
                model_config=checkpoint["model_config"],
                training_config=checkpoint["training_config"],
            )
            trainer.model = GraphSAGEDefectPredictor(trainer.model_config)
            trainer.model.load_state_dict(checkpoint["model_state"])
            trainer.model.to(trainer.device)
            trainer._is_trained = True

            logger.info(f"Model loaded from {path}")
            return trainer

else:
    # Stub classes when PyTorch is not available
    class CrossProjectDataLoader:
        """Stub: PyTorch required."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PyTorch and PyTorch Geometric required for cross-project training. "
                "Install with: pip install torch torch-geometric"
            )


    class CrossProjectTrainer:
        """Stub: PyTorch required."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PyTorch and PyTorch Geometric required for cross-project training. "
                "Install with: pip install torch torch-geometric"
            )
