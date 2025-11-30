"""Training and inference for multimodal fusion model.

This module provides the MultimodalAnalyzer class for:
- Preparing training data from Neo4j (combining text + graph embeddings)
- Training the fusion model with multi-task learning
- Making predictions with interpretability features
- Model persistence (save/load)

Example:
    >>> from repotoire.ml.multimodal_analyzer import MultimodalAnalyzer
    >>> from repotoire.graph.client import Neo4jClient
    >>>
    >>> client = Neo4jClient.from_env()
    >>> analyzer = MultimodalAnalyzer(client)
    >>>
    >>> # Prepare data
    >>> train_ds, val_ds = analyzer.prepare_data(bug_labels_path=Path("bugs.json"))
    >>>
    >>> # Train model
    >>> history = analyzer.train(train_ds, val_ds)
    >>>
    >>> # Predict with explanation
    >>> explanation = analyzer.explain_prediction("mymodule.MyClass.method", "bug_prediction")
    >>> print(f"Prediction: {explanation.prediction} ({explanation.confidence:.0%})")
    >>> print(f"Text weight: {explanation.text_weight:.0%}, Graph weight: {explanation.graph_weight:.0%}")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json

import numpy as np
import torch
import torch.nn as nn
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader, Dataset

from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger
from repotoire.ml.multimodal_fusion import (
    FusionConfig,
    MultimodalAttentionFusion,
    MultiTaskLoss,
)

logger = get_logger(__name__)


@dataclass
class TrainingConfig:
    """Configuration for multimodal training.

    Attributes:
        epochs: Maximum training epochs
        batch_size: Training batch size
        learning_rate: Initial learning rate
        weight_decay: L2 regularization weight
        warmup_epochs: Epochs for learning rate warmup
        early_stopping_patience: Epochs without improvement before stopping
        device: Device for training (cuda/cpu/mps)
    """

    epochs: int = 50
    batch_size: int = 64
    learning_rate: float = 0.001
    weight_decay: float = 0.01
    warmup_epochs: int = 5
    early_stopping_patience: int = 10
    device: str = field(default_factory=lambda: "cuda" if torch.cuda.is_available() else "cpu")


@dataclass
class PredictionExplanation:
    """Explanation for a multimodal prediction.

    Provides interpretability by showing which modality (text or graph)
    contributed more to the prediction.

    Attributes:
        qualified_name: Function identifier
        task: Prediction task (bug_prediction, smell_detection, etc.)
        prediction: Predicted label
        confidence: Prediction confidence (0-1)
        text_weight: Weight given to text modality
        graph_weight: Weight given to graph modality
        interpretation: Human-readable explanation
        similar_functions: List of similar functions (for context)
    """

    qualified_name: str
    task: str
    prediction: str
    confidence: float
    text_weight: float
    graph_weight: float
    interpretation: str
    similar_functions: List[str] = field(default_factory=list)


class MultimodalDataset(Dataset):
    """Dataset for multimodal training.

    Combines text embeddings, graph embeddings, and labels for
    multi-task learning.
    """

    def __init__(
        self,
        text_embeddings: np.ndarray,
        graph_embeddings: np.ndarray,
        labels: Dict[str, np.ndarray],
        qualified_names: List[str],
    ):
        """Initialize dataset.

        Args:
            text_embeddings: Text embeddings [N, text_dim]
            graph_embeddings: Graph embeddings [N, graph_dim]
            labels: Dict of task labels {task: [N]}
            qualified_names: List of function qualified names
        """
        self.text_embeddings = torch.FloatTensor(text_embeddings)
        self.graph_embeddings = torch.FloatTensor(graph_embeddings)
        self.labels = {k: torch.LongTensor(v) for k, v in labels.items()}
        self.qualified_names = qualified_names

    def __len__(self) -> int:
        return len(self.text_embeddings)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        item = {
            "text_embedding": self.text_embeddings[idx],
            "graph_embedding": self.graph_embeddings[idx],
            "qualified_name": self.qualified_names[idx],
        }
        for task, task_labels in self.labels.items():
            item[task] = task_labels[idx]
        return item


class MultimodalAnalyzer:
    """Training and inference for multimodal fusion.

    Handles:
    - Data preparation from Neo4j (fetching embeddings, combining with labels)
    - Model training with multi-task learning and uncertainty weighting
    - Inference with interpretability (modality weights, attention)
    - Integration with existing Repotoire embeddings
    """

    def __init__(
        self,
        client: Neo4jClient,
        model_config: Optional[FusionConfig] = None,
        training_config: Optional[TrainingConfig] = None,
    ):
        """Initialize analyzer.

        Args:
            client: Neo4j database client
            model_config: Model architecture configuration
            training_config: Training hyperparameters
        """
        self.client = client
        self.model_config = model_config or FusionConfig()
        self.training_config = training_config or TrainingConfig()

        self.model: Optional[MultimodalAttentionFusion] = None
        self.device = torch.device(self.training_config.device)
        self._is_trained = False
        self._best_state: Optional[Dict[str, Any]] = None

        # Label mappings for each task
        self.label_maps = {
            "bug_prediction": {0: "clean", 1: "buggy"},
            "smell_detection": {
                0: "none",
                1: "long_method",
                2: "god_class",
                3: "feature_envy",
                4: "data_clump",
            },
            "refactoring_benefit": {0: "low", 1: "medium", 2: "high"},
        }

        # Reverse mappings for label encoding
        self._reverse_label_maps = {
            task: {v: k for k, v in mapping.items()}
            for task, mapping in self.label_maps.items()
        }

    def prepare_data(
        self,
        bug_labels_path: Optional[Path] = None,
        smell_labels_path: Optional[Path] = None,
        refactor_labels_path: Optional[Path] = None,
        test_split: float = 0.2,
        random_seed: int = 42,
    ) -> Tuple[MultimodalDataset, MultimodalDataset]:
        """Prepare training and validation datasets.

        Fetches embeddings from Neo4j and combines with labels from JSON files.

        Args:
            bug_labels_path: Path to bug labels JSON
            smell_labels_path: Path to smell labels JSON
            refactor_labels_path: Path to refactoring labels JSON
            test_split: Fraction of data for validation
            random_seed: Random seed for reproducibility

        Returns:
            Tuple of (train_dataset, val_dataset)

        Raises:
            ValueError: If no embeddings found in database
        """
        # Fetch embeddings from Neo4j
        # Support both 'embedding' (OpenAI) and 'fastrp_embedding'/'node2vec_embedding' (graph)
        query = """
        MATCH (f:Function)
        WHERE f.embedding IS NOT NULL
          AND (f.fastrp_embedding IS NOT NULL OR f.node2vec_embedding IS NOT NULL)
        RETURN
            f.qualifiedName AS qualified_name,
            f.embedding AS text_embedding,
            COALESCE(f.fastrp_embedding, f.node2vec_embedding) AS graph_embedding
        """

        results = self.client.execute_query(query)

        if not results:
            raise ValueError(
                "No functions found with both text and graph embeddings. "
                "Run 'repotoire ingest --generate-embeddings' and "
                "'repotoire ml generate-embeddings' first."
            )

        logger.info(f"Fetched {len(results)} functions with embeddings")

        qualified_names = [r["qualified_name"] for r in results]
        text_embeddings = np.array([r["text_embedding"] for r in results])
        graph_embeddings = np.array([r["graph_embedding"] for r in results])

        # Create name to index mapping
        name_to_idx = {name: i for i, name in enumerate(qualified_names)}

        # Load labels from JSON files
        labels: Dict[str, np.ndarray] = {}

        if bug_labels_path:
            bug_labels = self._load_labels(
                bug_labels_path,
                name_to_idx,
                "bug_prediction",
                len(qualified_names),
            )
            labels["bug_prediction"] = bug_labels

        if smell_labels_path:
            smell_labels = self._load_labels(
                smell_labels_path,
                name_to_idx,
                "smell_detection",
                len(qualified_names),
            )
            labels["smell_detection"] = smell_labels

        if refactor_labels_path:
            refactor_labels = self._load_labels(
                refactor_labels_path,
                name_to_idx,
                "refactoring_benefit",
                len(qualified_names),
            )
            labels["refactoring_benefit"] = refactor_labels

        if not labels:
            raise ValueError(
                "At least one label file must be provided. "
                "Use --bug-labels, --smell-labels, or --refactor-labels."
            )

        # Train/val split
        np.random.seed(random_seed)
        n_samples = len(qualified_names)
        indices = np.random.permutation(n_samples)
        split_idx = int((1 - test_split) * n_samples)

        train_idx = indices[:split_idx]
        val_idx = indices[split_idx:]

        train_dataset = MultimodalDataset(
            text_embeddings=text_embeddings[train_idx],
            graph_embeddings=graph_embeddings[train_idx],
            labels={k: v[train_idx] for k, v in labels.items()},
            qualified_names=[qualified_names[i] for i in train_idx],
        )

        val_dataset = MultimodalDataset(
            text_embeddings=text_embeddings[val_idx],
            graph_embeddings=graph_embeddings[val_idx],
            labels={k: v[val_idx] for k, v in labels.items()},
            qualified_names=[qualified_names[i] for i in val_idx],
        )

        logger.info(
            f"Prepared {len(train_dataset)} training, {len(val_dataset)} validation examples"
        )

        return train_dataset, val_dataset

    def _load_labels(
        self,
        path: Path,
        name_to_idx: Dict[str, int],
        task: str,
        n_samples: int,
    ) -> np.ndarray:
        """Load labels from JSON file.

        Expected JSON format:
        [
            {"qualified_name": "module.func", "label": "buggy"},
            ...
        ]
        """
        with open(path) as f:
            data = json.load(f)

        labels = np.zeros(n_samples, dtype=np.int64)
        label_map = self._reverse_label_maps[task]

        matched = 0
        for item in data:
            name = item.get("qualified_name")
            label = item.get("label")

            if name in name_to_idx and label in label_map:
                labels[name_to_idx[name]] = label_map[label]
                matched += 1

        logger.info(f"Loaded {matched}/{len(data)} labels for {task}")
        return labels

    def train(
        self,
        train_dataset: MultimodalDataset,
        val_dataset: MultimodalDataset,
        tasks: Optional[List[str]] = None,
    ) -> Dict[str, List[float]]:
        """Train multimodal fusion model.

        Args:
            train_dataset: Training dataset
            val_dataset: Validation dataset
            tasks: Tasks to train (default: all available in dataset)

        Returns:
            Training history with losses and metrics per epoch
        """
        # Determine tasks from dataset
        available_tasks = list(train_dataset.labels.keys())
        tasks = tasks or available_tasks

        # Filter to tasks in dataset
        tasks = [t for t in tasks if t in available_tasks]
        if not tasks:
            raise ValueError("No valid tasks found in training data")

        logger.info(f"Training on tasks: {tasks}")

        # Initialize model
        self.model = MultimodalAttentionFusion(self.model_config)
        self.model.to(self.device)

        # Loss function
        loss_fn = MultiTaskLoss(tasks)
        loss_fn.to(self.device)

        # Optimizer
        optimizer = AdamW(
            list(self.model.parameters()) + list(loss_fn.parameters()),
            lr=self.training_config.learning_rate,
            weight_decay=self.training_config.weight_decay,
        )

        # Scheduler
        scheduler = CosineAnnealingLR(
            optimizer,
            T_max=self.training_config.epochs,
        )

        # Data loaders
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.training_config.batch_size,
            shuffle=True,
        )
        val_loader = DataLoader(
            val_dataset,
            batch_size=self.training_config.batch_size,
            shuffle=False,
        )

        # Training loop
        history: Dict[str, List[float]] = {"train_loss": [], "val_loss": []}
        for task in tasks:
            history[f"{task}_acc"] = []

        best_val_loss = float("inf")
        patience_counter = 0

        for epoch in range(self.training_config.epochs):
            # Training phase
            self.model.train()
            train_loss = 0.0

            for batch in train_loader:
                text_embed = batch["text_embedding"].to(self.device)
                graph_embed = batch["graph_embedding"].to(self.device)
                targets = {k: batch[k].to(self.device) for k in tasks}

                optimizer.zero_grad()
                outputs = self.model(text_embed, graph_embed)
                loss, task_losses = loss_fn(outputs, targets)
                loss.backward()
                optimizer.step()

                train_loss += loss.item()

            train_loss /= len(train_loader)
            history["train_loss"].append(train_loss)

            # Validation phase
            self.model.eval()
            val_loss = 0.0
            task_correct = {task: 0 for task in tasks}
            task_total = {task: 0 for task in tasks}

            with torch.no_grad():
                for batch in val_loader:
                    text_embed = batch["text_embedding"].to(self.device)
                    graph_embed = batch["graph_embedding"].to(self.device)
                    targets = {k: batch[k].to(self.device) for k in tasks}

                    outputs = self.model(text_embed, graph_embed)
                    loss, _ = loss_fn(outputs, targets)
                    val_loss += loss.item()

                    # Compute accuracy
                    for task in tasks:
                        preds = outputs[f"{task}_logits"].argmax(dim=1)
                        task_correct[task] += (preds == targets[task]).sum().item()
                        task_total[task] += targets[task].size(0)

            val_loss /= len(val_loader)
            history["val_loss"].append(val_loss)

            for task in tasks:
                acc = task_correct[task] / max(task_total[task], 1)
                history[f"{task}_acc"].append(acc)

            # Learning rate scheduling
            scheduler.step()

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                # Save best model
                self._best_state = self.model.state_dict()
            else:
                patience_counter += 1
                if patience_counter >= self.training_config.early_stopping_patience:
                    logger.info(f"Early stopping at epoch {epoch + 1}")
                    break

            # Logging
            if (epoch + 1) % 10 == 0:
                accs = ", ".join(
                    f"{task}={history[f'{task}_acc'][-1]:.3f}" for task in tasks
                )
                logger.info(f"Epoch {epoch+1}: Loss={val_loss:.4f}, {accs}")

        # Load best model
        if self._best_state is not None:
            self.model.load_state_dict(self._best_state)
        self._is_trained = True

        logger.info("Training complete")
        return history

    def predict(
        self,
        qualified_name: str,
        task: str,
    ) -> Tuple[Optional[str], float]:
        """Predict for a single function.

        Args:
            qualified_name: Function qualified name
            task: Prediction task

        Returns:
            Tuple of (prediction_label, confidence), or (None, 0.0) if not found
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        if self.model is None:
            raise RuntimeError("Model not initialized")

        # Fetch embeddings
        query = """
        MATCH (f:Function {qualifiedName: $qualified_name})
        RETURN f.embedding AS text_embedding,
               COALESCE(f.fastrp_embedding, f.node2vec_embedding) AS graph_embedding
        """
        result = self.client.execute_query(query, qualified_name=qualified_name)

        if not result or result[0]["text_embedding"] is None or result[0]["graph_embedding"] is None:
            return None, 0.0

        text_embed = torch.FloatTensor([result[0]["text_embedding"]]).to(self.device)
        graph_embed = torch.FloatTensor([result[0]["graph_embedding"]]).to(self.device)

        # Predict
        self.model.eval()
        with torch.no_grad():
            outputs = self.model(text_embed, graph_embed, task=task)
            logits = outputs[f"{task}_logits"]
            probs = torch.softmax(logits, dim=1)
            pred_idx = probs.argmax(dim=1).item()
            confidence = probs[0, pred_idx].item()

        label = self.label_maps[task][pred_idx]
        return label, confidence

    def explain_prediction(
        self,
        qualified_name: str,
        task: str,
    ) -> Optional[PredictionExplanation]:
        """Generate interpretable explanation for prediction.

        Args:
            qualified_name: Function qualified name
            task: Prediction task

        Returns:
            PredictionExplanation with modality weights and interpretation,
            or None if function not found
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        if self.model is None:
            raise RuntimeError("Model not initialized")

        # Fetch embeddings
        query = """
        MATCH (f:Function {qualifiedName: $qualified_name})
        RETURN f.embedding AS text_embedding,
               COALESCE(f.fastrp_embedding, f.node2vec_embedding) AS graph_embedding
        """
        result = self.client.execute_query(query, qualified_name=qualified_name)

        if not result or result[0]["text_embedding"] is None or result[0]["graph_embedding"] is None:
            return None

        text_embed = torch.FloatTensor([result[0]["text_embedding"]]).to(self.device)
        graph_embed = torch.FloatTensor([result[0]["graph_embedding"]]).to(self.device)

        # Get prediction with attention weights
        self.model.eval()
        with torch.no_grad():
            outputs = self.model(text_embed, graph_embed, task=task)

            logits = outputs[f"{task}_logits"]
            probs = torch.softmax(logits, dim=1)
            pred_idx = probs.argmax(dim=1).item()
            confidence = probs[0, pred_idx].item()

            text_weight = outputs["text_weight"][0].item()
            graph_weight = outputs["graph_weight"][0].item()

        label = self.label_maps[task][pred_idx]

        # Generate interpretation
        if graph_weight > 0.6:
            interpretation = (
                "Prediction primarily based on architectural/structural patterns "
                "(call graph, dependencies, coupling)."
            )
        elif text_weight > 0.6:
            interpretation = (
                "Prediction primarily based on semantic/naming patterns "
                "(function names, documentation, code semantics)."
            )
        else:
            interpretation = (
                "Prediction based on balanced combination of semantic and "
                "structural signals (both modalities contribute equally)."
            )

        return PredictionExplanation(
            qualified_name=qualified_name,
            task=task,
            prediction=label,
            confidence=confidence,
            text_weight=text_weight,
            graph_weight=graph_weight,
            interpretation=interpretation,
        )

    def predict_all_functions(
        self,
        task: str,
        threshold: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Predict for all functions with embeddings.

        Args:
            task: Prediction task
            threshold: Confidence threshold for including predictions

        Returns:
            List of predictions with explanations, sorted by confidence
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        if self.model is None:
            raise RuntimeError("Model not initialized")

        # Fetch all embeddings
        query = """
        MATCH (f:Function)
        WHERE f.embedding IS NOT NULL
          AND (f.fastrp_embedding IS NOT NULL OR f.node2vec_embedding IS NOT NULL)
        RETURN
            f.qualifiedName AS qualified_name,
            f.embedding AS text_embedding,
            COALESCE(f.fastrp_embedding, f.node2vec_embedding) AS graph_embedding
        """

        results = self.client.execute_query(query)

        if not results:
            return []

        # Batch prediction
        qualified_names = [r["qualified_name"] for r in results]
        text_embeddings = torch.FloatTensor([r["text_embedding"] for r in results]).to(
            self.device
        )
        graph_embeddings = torch.FloatTensor(
            [r["graph_embedding"] for r in results]
        ).to(self.device)

        self.model.eval()
        predictions = []

        # Process in batches
        batch_size = 256
        for i in range(0, len(qualified_names), batch_size):
            batch_text = text_embeddings[i : i + batch_size]
            batch_graph = graph_embeddings[i : i + batch_size]
            batch_names = qualified_names[i : i + batch_size]

            with torch.no_grad():
                outputs = self.model(batch_text, batch_graph, task=task)
                logits = outputs[f"{task}_logits"]
                probs = torch.softmax(logits, dim=1)
                pred_indices = probs.argmax(dim=1)
                confidences = probs.gather(1, pred_indices.unsqueeze(1)).squeeze(1)

                text_weights = outputs["text_weight"]
                graph_weights = outputs["graph_weight"]

            for j, name in enumerate(batch_names):
                confidence = confidences[j].item()
                if confidence >= threshold:
                    pred_idx = pred_indices[j].item()
                    label = self.label_maps[task][pred_idx]
                    text_w = text_weights[j].item()
                    graph_w = graph_weights[j].item()

                    # Generate interpretation
                    if graph_w > 0.6:
                        interpretation = "Structural patterns dominate"
                    elif text_w > 0.6:
                        interpretation = "Semantic patterns dominate"
                    else:
                        interpretation = "Balanced semantic/structural signals"

                    predictions.append(
                        {
                            "qualified_name": name,
                            "prediction": label,
                            "confidence": confidence,
                            "text_weight": text_w,
                            "graph_weight": graph_w,
                            "interpretation": interpretation,
                        }
                    )

        return sorted(predictions, key=lambda x: x["confidence"], reverse=True)

    def save(self, path: Path) -> None:
        """Save trained model to disk.

        Args:
            path: Output path for model file

        Raises:
            RuntimeError: If model not trained
        """
        if not self._is_trained or self.model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        path.parent.mkdir(parents=True, exist_ok=True)

        torch.save(
            {
                "model_state": self.model.state_dict(),
                "model_config": self.model_config,
                "label_maps": self.label_maps,
            },
            path,
        )

        logger.info(f"Saved model to {path}")

    @classmethod
    def load(cls, path: Path, client: Neo4jClient) -> "MultimodalAnalyzer":
        """Load trained model from disk.

        Args:
            path: Path to model file
            client: Neo4j client for predictions

        Returns:
            Loaded MultimodalAnalyzer instance
        """
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)

        analyzer = cls(client, model_config=checkpoint["model_config"])
        analyzer.model = MultimodalAttentionFusion(analyzer.model_config)
        analyzer.model.load_state_dict(checkpoint["model_state"])
        analyzer.model.to(analyzer.device)
        analyzer.label_maps = checkpoint["label_maps"]
        analyzer._is_trained = True

        logger.info(f"Loaded model from {path}")
        return analyzer

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the model.

        Returns:
            Dict with model configuration and status
        """
        return {
            "is_trained": self._is_trained,
            "config": {
                "text_dim": self.model_config.text_dim,
                "graph_dim": self.model_config.graph_dim,
                "fusion_dim": self.model_config.fusion_dim,
                "num_heads": self.model_config.num_heads,
                "dropout": self.model_config.dropout,
            },
            "tasks": list(self.label_maps.keys()),
            "device": str(self.device),
        }
