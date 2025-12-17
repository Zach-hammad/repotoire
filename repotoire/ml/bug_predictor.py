"""Bug prediction model using Node2Vec embeddings + code metrics.

This module implements a machine learning classifier to predict which functions
are likely to contain bugs. The model combines:

1. **Node2Vec Embeddings (128-dim)**: Capture structural patterns in the call graph
   - Tightly coupled clusters
   - Central bottleneck functions
   - Unusual call patterns

2. **Code Metrics (10-dim)**: Traditional software metrics
   - Cyclomatic complexity
   - Lines of code
   - Fan-in/fan-out coupling
   - Git churn metrics
   - Test coverage indicators

The combined 138-dimensional feature vector is fed to a RandomForest classifier
trained on historical bug-fix data extracted from git history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import logging

import numpy as np

try:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import (
        train_test_split,
        cross_val_score,
        GridSearchCV,
        StratifiedKFold,
    )
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
    )
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

from repotoire.graph.client import Neo4jClient
from repotoire.ml.training_data import TrainingDataset, TrainingExample

# Try to import Rust accelerated functions (REPO-248)
try:
    from repotoire_fast import combine_features_batch as _rust_combine_features
    from repotoire_fast import normalize_features_batch as _rust_normalize_features
    HAS_RUST_FEATURES = True
except ImportError:
    HAS_RUST_FEATURES = False

logger = logging.getLogger(__name__)


@dataclass
class BugPredictorConfig:
    """Configuration for bug predictor model.

    Attributes:
        n_estimators: Number of trees in the forest (default: 100)
        max_depth: Maximum depth of trees (default: 10)
        min_samples_split: Minimum samples to split a node (default: 20)
        class_weight: Handle imbalanced data (default: "balanced")
        random_state: Random seed for reproducibility (default: 42)
        test_split: Fraction of data for testing (default: 0.2)
        cv_folds: Number of cross-validation folds (default: 5)
    """
    n_estimators: int = 100
    max_depth: int = 10
    min_samples_split: int = 20
    class_weight: str = "balanced"  # Handle imbalanced data
    random_state: int = 42
    test_split: float = 0.2
    cv_folds: int = 5


@dataclass
class PredictionResult:
    """Result of bug prediction for a function.

    Attributes:
        qualified_name: Fully qualified function name
        file_path: Path to the file containing the function
        bug_probability: Predicted probability of containing a bug (0.0-1.0)
        is_high_risk: Whether probability exceeds risk threshold
        contributing_factors: Top factors contributing to the prediction
        similar_buggy_functions: Functions with similar embeddings that had bugs
    """
    qualified_name: str
    file_path: str
    bug_probability: float
    is_high_risk: bool
    contributing_factors: List[str] = field(default_factory=list)
    similar_buggy_functions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "bug_probability": round(self.bug_probability, 4),
            "is_high_risk": self.is_high_risk,
            "contributing_factors": self.contributing_factors,
            "similar_buggy_functions": self.similar_buggy_functions,
        }


@dataclass
class ModelMetrics:
    """Evaluation metrics for trained model.

    Attributes:
        accuracy: Overall accuracy (correct predictions / total)
        precision: True positives / (true positives + false positives)
        recall: True positives / (true positives + false negatives)
        f1_score: Harmonic mean of precision and recall
        auc_roc: Area under ROC curve
        cv_scores: Cross-validation scores
    """
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    cv_scores: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "auc_roc": round(self.auc_roc, 4),
            "cv_mean": round(float(np.mean(self.cv_scores)), 4) if self.cv_scores else 0.0,
            "cv_std": round(float(np.std(self.cv_scores)), 4) if self.cv_scores else 0.0,
        }


class FeatureExtractor:
    """Extract combined features for bug prediction.

    Combines Node2Vec graph embeddings with traditional code metrics
    to create a comprehensive feature vector for each function.

    Feature Vector (138 dimensions):
    - [0:128] Node2Vec embedding (structural patterns from call graph)
    - [128] Cyclomatic complexity
    - [129] Lines of code
    - [130] Fan-in (number of callers)
    - [131] Fan-out (number of callees)
    - [132] Git churn (number of changes)
    - [133] Age in days
    - [134] Number of authors
    - [135] Has tests (0 or 1)
    - [136] Total coupling (fan_in + fan_out)
    - [137] Complexity density (complexity / LOC)
    """

    # Names for the metric features (for interpretability)
    METRIC_NAMES = [
        "complexity",
        "loc",
        "fan_in",
        "fan_out",
        "churn",
        "age_days",
        "num_authors",
        "has_tests",
        "total_coupling",
        "complexity_density",
    ]

    def __init__(
        self,
        client: Neo4jClient,
        embedding_property: str = "node2vec_embedding",
    ):
        """Initialize feature extractor.

        Args:
            client: Neo4j database client
            embedding_property: Node property containing embeddings
        """
        self.client = client
        self.embedding_property = embedding_property
        self._scaler: Optional[StandardScaler] = None

    def extract_features(
        self,
        qualified_name: str,
    ) -> Optional[np.ndarray]:
        """Extract combined feature vector for a function.

        Retrieves Node2Vec embedding and code metrics from the graph
        and combines them into a single feature vector.

        Args:
            qualified_name: Fully qualified function name

        Returns:
            138-dimensional feature vector, or None if function not found
        """
        query = f"""
        MATCH (f:Function {{qualifiedName: $qualified_name}})
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
        RETURN
            f.{self.embedding_property} AS embedding,
            f.complexity AS complexity,
            f.loc AS loc,
            COUNT(DISTINCT caller) AS fan_in,
            COUNT(DISTINCT callee) AS fan_out,
            f.churn AS churn,
            f.age_days AS age_days,
            f.num_authors AS num_authors,
            CASE WHEN f.has_tests THEN 1 ELSE 0 END AS has_tests
        """

        result = self.client.execute_query(query, qualified_name=qualified_name)

        if not result or result[0].get("embedding") is None:
            return None

        row = result[0]
        embedding = np.array(row["embedding"])

        # Code metrics (with defaults for missing values)
        complexity = row.get("complexity") or 1
        loc = row.get("loc") or 10
        fan_in = row.get("fan_in") or 0
        fan_out = row.get("fan_out") or 0

        metrics = np.array([
            complexity,
            loc,
            fan_in,
            fan_out,
            row.get("churn") or 0,
            row.get("age_days") or 365,
            row.get("num_authors") or 1,
            row.get("has_tests") or 0,
            # Derived features
            fan_in + fan_out,  # total coupling
            complexity / max(loc, 1),  # complexity density
        ])

        return np.concatenate([embedding, metrics])

    def extract_batch_features(
        self,
        qualified_names: List[str],
    ) -> Tuple[np.ndarray, List[str]]:
        """Extract features for multiple functions.

        Args:
            qualified_names: List of function qualified names

        Returns:
            Tuple of (feature_matrix, valid_names):
            - feature_matrix: 2D array of shape (n_valid, 138)
            - valid_names: List of names that had valid features
        """
        features = []
        valid_names = []

        for name in qualified_names:
            feat = self.extract_features(name)
            if feat is not None:
                features.append(feat)
                valid_names.append(name)

        if not features:
            return np.array([]), []

        return np.array(features), valid_names

    def extract_metrics_only(
        self,
        qualified_name: str,
    ) -> Optional[np.ndarray]:
        """Extract only code metrics (without embedding).

        Useful when embeddings are not available.

        Args:
            qualified_name: Function qualified name

        Returns:
            10-dimensional metric vector, or None if not found
        """
        query = """
        MATCH (f:Function {qualifiedName: $qualified_name})
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
        RETURN
            f.complexity AS complexity,
            f.loc AS loc,
            COUNT(DISTINCT caller) AS fan_in,
            COUNT(DISTINCT callee) AS fan_out,
            f.churn AS churn,
            f.age_days AS age_days,
            f.num_authors AS num_authors,
            CASE WHEN f.has_tests THEN 1 ELSE 0 END AS has_tests
        """

        result = self.client.execute_query(query, qualified_name=qualified_name)

        if not result:
            return None

        row = result[0]
        complexity = row.get("complexity") or 1
        loc = row.get("loc") or 10
        fan_in = row.get("fan_in") or 0
        fan_out = row.get("fan_out") or 0

        return np.array([
            complexity,
            loc,
            fan_in,
            fan_out,
            row.get("churn") or 0,
            row.get("age_days") or 365,
            row.get("num_authors") or 1,
            row.get("has_tests") or 0,
            fan_in + fan_out,
            complexity / max(loc, 1),
        ])

    @staticmethod
    def combine_features(
        embeddings: np.ndarray,
        metrics: np.ndarray,
    ) -> np.ndarray:
        """Combine embedding vectors with metric vectors.

        Uses Rust implementation for ~2x speedup when available,
        with numpy fallback.

        Args:
            embeddings: 2D array of embeddings (n × embedding_dim)
            metrics: 2D array of metrics (n × metrics_dim)

        Returns:
            Combined feature matrix (n × (embedding_dim + metrics_dim))
        """
        if HAS_RUST_FEATURES and len(embeddings) > 0:
            try:
                # Rust expects f32 arrays, returns numpy array directly
                emb_f32 = embeddings.astype(np.float32)
                met_f32 = metrics.astype(np.float32)
                return _rust_combine_features(emb_f32, met_f32)
            except Exception as e:
                logger.debug(f"Rust combine_features failed, using numpy: {e}")

        # Numpy fallback
        return np.hstack([embeddings, metrics])

    @staticmethod
    def normalize_features(
        features: np.ndarray,
    ) -> np.ndarray:
        """Apply Z-score normalization to features.

        Uses Rust implementation for ~2x speedup when available,
        with numpy fallback.

        Args:
            features: 2D array of features (n × m)

        Returns:
            Normalized features with mean=0, std=1 per column
        """
        if HAS_RUST_FEATURES and len(features) > 0:
            try:
                # Rust expects f32 arrays, returns numpy array directly
                feat_f32 = features.astype(np.float32)
                return _rust_normalize_features(feat_f32)
            except Exception as e:
                logger.debug(f"Rust normalize_features failed, using numpy: {e}")

        # Numpy fallback
        if len(features) == 0:
            return features

        mean = np.mean(features, axis=0)
        std = np.std(features, axis=0)
        # Avoid division by zero
        std = np.where(std < 1e-10, 1.0, std)
        return (features - mean) / std


class BugPredictor:
    """ML-based bug prediction using Node2Vec + RandomForest.

    This classifier combines graph-based structural analysis (Node2Vec embeddings)
    with traditional software metrics to predict which functions are likely to
    contain bugs.

    Architecture:
        Call Graph → Node2Vec Embeddings (128-dim)
                          ↓
                 Combine with metrics (complexity, LOC, coupling)
                          ↓
                 RandomForest Classifier
                          ↓
                 Bug Probability (0-1)

    Example:
        >>> # Train a model
        >>> client = Neo4jClient.from_env()
        >>> predictor = BugPredictor(client)
        >>> metrics = predictor.train(training_dataset)
        >>> print(f"AUC-ROC: {metrics.auc_roc:.2f}")
        >>>
        >>> # Make predictions
        >>> result = predictor.predict("module.MyClass.risky_method")
        >>> if result.is_high_risk:
        >>>     print(f"High risk: {result.bug_probability:.1%}")
        >>>
        >>> # Save model for later use
        >>> predictor.save(Path("models/bug_predictor.pkl"))
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: Optional[BugPredictorConfig] = None,
    ):
        """Initialize bug predictor.

        Args:
            client: Neo4j database client
            config: Model configuration (uses defaults if not provided)

        Raises:
            ImportError: If scikit-learn is not installed
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError(
                "scikit-learn required for bug prediction. "
                "Install with: pip install scikit-learn"
            )

        self.client = client
        self.config = config or BugPredictorConfig()
        self.feature_extractor = FeatureExtractor(client)
        self.model: Optional[RandomForestClassifier] = None
        self.metrics: Optional[ModelMetrics] = None
        self._is_trained = False
        self._feature_importances: Optional[np.ndarray] = None

    def train(
        self,
        dataset: TrainingDataset,
        hyperparameter_search: bool = False,
    ) -> ModelMetrics:
        """Train bug prediction model on labeled data.

        Args:
            dataset: Training dataset with labeled examples (buggy/clean)
            hyperparameter_search: Whether to run GridSearchCV for tuning

        Returns:
            ModelMetrics with evaluation results

        Raises:
            ValueError: If insufficient training data (<50 samples with embeddings)
        """
        # Extract features for all examples
        X, y, valid_names = self._prepare_training_data(dataset)

        if len(X) < 50:
            raise ValueError(
                f"Insufficient training data: {len(X)} samples with embeddings "
                f"(need at least 50). Try generating Node2Vec embeddings first."
            )

        logger.info(f"Training on {len(X)} samples ({sum(y)} buggy, {len(y) - sum(y)} clean)")

        # Train/test split with stratification
        X_train, X_test, y_train, y_test = train_test_split(
            X, y,
            test_size=self.config.test_split,
            random_state=self.config.random_state,
            stratify=y,
        )

        # Train model
        if hyperparameter_search:
            self.model = self._grid_search(X_train, y_train)
        else:
            self.model = RandomForestClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                min_samples_split=self.config.min_samples_split,
                class_weight=self.config.class_weight,
                random_state=self.config.random_state,
                n_jobs=-1,
            )
            self.model.fit(X_train, y_train)

        # Store feature importances
        self._feature_importances = self.model.feature_importances_

        # Evaluate
        self.metrics = self._evaluate(X_train, X_test, y_train, y_test)
        self._is_trained = True

        logger.info(
            f"Model trained: accuracy={self.metrics.accuracy:.3f}, "
            f"AUC-ROC={self.metrics.auc_roc:.3f}"
        )

        return self.metrics

    def _prepare_training_data(
        self,
        dataset: TrainingDataset,
    ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """Prepare feature matrix and labels from dataset.

        Args:
            dataset: Training dataset

        Returns:
            Tuple of (X, y, valid_names)
        """
        qualified_names = [ex.qualified_name for ex in dataset.examples]
        labels = [1 if ex.label == "buggy" else 0 for ex in dataset.examples]

        X, valid_names = self.feature_extractor.extract_batch_features(qualified_names)

        if len(X) == 0:
            return np.array([]), np.array([]), []

        # Filter labels to match valid names
        name_to_label = dict(zip(qualified_names, labels))
        y = np.array([name_to_label[name] for name in valid_names])

        return X, y, valid_names

    def _grid_search(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
    ) -> RandomForestClassifier:
        """Hyperparameter tuning with GridSearchCV.

        Args:
            X_train: Training features
            y_train: Training labels

        Returns:
            Best estimator from grid search
        """
        param_grid = {
            "n_estimators": [50, 100, 200],
            "max_depth": [5, 10, 15, None],
            "min_samples_split": [10, 20, 50],
        }

        grid_search = GridSearchCV(
            RandomForestClassifier(
                class_weight=self.config.class_weight,
                random_state=self.config.random_state,
                n_jobs=-1,
            ),
            param_grid,
            cv=self.config.cv_folds,
            scoring="roc_auc",
            n_jobs=-1,
        )

        grid_search.fit(X_train, y_train)

        logger.info(f"Best parameters: {grid_search.best_params_}")
        logger.info(f"Best CV AUC-ROC: {grid_search.best_score_:.4f}")

        return grid_search.best_estimator_

    def _evaluate(
        self,
        X_train: np.ndarray,
        X_test: np.ndarray,
        y_train: np.ndarray,
        y_test: np.ndarray,
    ) -> ModelMetrics:
        """Evaluate trained model on test data.

        Args:
            X_train: Training features (for CV)
            X_test: Test features
            y_train: Training labels (for CV)
            y_test: Test labels

        Returns:
            ModelMetrics with evaluation results
        """
        # Test set predictions
        y_pred = self.model.predict(X_test)
        y_proba = self.model.predict_proba(X_test)[:, 1]

        # Cross-validation scores on training data
        cv = StratifiedKFold(
            n_splits=self.config.cv_folds,
            shuffle=True,
            random_state=self.config.random_state,
        )
        cv_scores = cross_val_score(
            self.model, X_train, y_train, cv=cv, scoring="roc_auc"
        )

        return ModelMetrics(
            accuracy=float(accuracy_score(y_test, y_pred)),
            precision=float(precision_score(y_test, y_pred, zero_division=0)),
            recall=float(recall_score(y_test, y_pred, zero_division=0)),
            f1_score=float(f1_score(y_test, y_pred, zero_division=0)),
            auc_roc=float(roc_auc_score(y_test, y_proba)),
            cv_scores=cv_scores.tolist(),
        )

    def predict(
        self,
        qualified_name: str,
        risk_threshold: float = 0.7,
    ) -> Optional[PredictionResult]:
        """Predict bug probability for a single function.

        Args:
            qualified_name: Fully qualified function name
            risk_threshold: Probability threshold for high risk classification

        Returns:
            PredictionResult with probability and analysis, or None if not found

        Raises:
            RuntimeError: If model has not been trained
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        features = self.feature_extractor.extract_features(qualified_name)
        if features is None:
            return None

        proba = self.model.predict_proba([features])[0][1]

        # Get contributing factors from feature importance
        contributing = self._get_contributing_factors(features)

        # Find similar buggy functions
        similar = self._find_similar_buggy(qualified_name)

        # Get file path
        file_path = self._get_file_path(qualified_name)

        return PredictionResult(
            qualified_name=qualified_name,
            file_path=file_path,
            bug_probability=float(proba),
            is_high_risk=proba >= risk_threshold,
            contributing_factors=contributing,
            similar_buggy_functions=similar,
        )

    def predict_batch(
        self,
        qualified_names: List[str],
        risk_threshold: float = 0.7,
    ) -> List[PredictionResult]:
        """Predict bug probability for multiple functions.

        Args:
            qualified_names: List of function names
            risk_threshold: Probability threshold for high risk

        Returns:
            List of PredictionResults (excludes functions not found)
        """
        results = []
        for name in qualified_names:
            result = self.predict(name, risk_threshold)
            if result:
                results.append(result)
        return results

    def predict_all_functions(
        self,
        risk_threshold: float = 0.7,
    ) -> List[PredictionResult]:
        """Predict bug probability for all functions in the graph.

        Args:
            risk_threshold: Probability threshold for high risk

        Returns:
            List of PredictionResults for all functions with embeddings
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        query = f"""
        MATCH (f:Function)
        WHERE f.{self.feature_extractor.embedding_property} IS NOT NULL
        RETURN f.qualifiedName AS qualified_name
        """

        results = self.client.execute_query(query)
        names = [r["qualified_name"] for r in results]

        logger.info(f"Predicting bug probability for {len(names)} functions...")
        return self.predict_batch(names, risk_threshold)

    def get_high_risk_functions(
        self,
        risk_threshold: float = 0.7,
        limit: Optional[int] = None,
    ) -> List[PredictionResult]:
        """Get functions with high bug probability.

        Args:
            risk_threshold: Minimum probability to include
            limit: Maximum results to return

        Returns:
            List of high-risk PredictionResults, sorted by probability
        """
        all_predictions = self.predict_all_functions(risk_threshold)

        # Filter and sort
        high_risk = [p for p in all_predictions if p.is_high_risk]
        high_risk.sort(key=lambda p: p.bug_probability, reverse=True)

        if limit:
            high_risk = high_risk[:limit]

        return high_risk

    def _get_contributing_factors(
        self,
        features: np.ndarray,
    ) -> List[str]:
        """Identify top factors contributing to prediction.

        Args:
            features: Feature vector for the function

        Returns:
            List of factor descriptions sorted by importance
        """
        if self._feature_importances is None:
            return []

        # Focus on metric features (last 10 dimensions)
        metric_importances = self._feature_importances[-10:]
        metric_values = features[-10:]

        factors = []
        for name, importance, value in zip(
            FeatureExtractor.METRIC_NAMES,
            metric_importances,
            metric_values,
        ):
            if importance > 0.03:  # Only include significant factors
                if name in ("complexity_density", "has_tests"):
                    value_str = f"{value:.2f}"
                else:
                    value_str = f"{value:.0f}"
                factors.append(
                    f"{name}={value_str} (importance: {importance:.2f})"
                )

        return sorted(
            factors,
            key=lambda x: float(x.split("importance: ")[1].rstrip(")")),
            reverse=True,
        )[:5]

    def _find_similar_buggy(
        self,
        qualified_name: str,
        limit: int = 3,
    ) -> List[str]:
        """Find similar functions that were labeled as buggy.

        Uses cosine similarity on embeddings to find structurally similar
        functions that had bugs.

        Args:
            qualified_name: Target function name
            limit: Maximum similar functions to return

        Returns:
            List of similar buggy function names
        """
        # Use Neo4j GDS cosine similarity if available
        query = f"""
        MATCH (target:Function {{qualifiedName: $qualified_name}})
        MATCH (other:Function)
        WHERE other.qualifiedName <> $qualified_name
          AND other.{self.feature_extractor.embedding_property} IS NOT NULL
          AND other.was_buggy = true
        WITH target, other,
             gds.similarity.cosine(
                 target.{self.feature_extractor.embedding_property},
                 other.{self.feature_extractor.embedding_property}
             ) AS similarity
        WHERE similarity > 0.8
        RETURN other.qualifiedName AS similar_name
        ORDER BY similarity DESC
        LIMIT $limit
        """

        try:
            results = self.client.execute_query(
                query, qualified_name=qualified_name, limit=limit
            )
            return [r["similar_name"] for r in results]
        except Exception:
            # GDS similarity might not be available
            return []

    def _get_file_path(self, qualified_name: str) -> str:
        """Get file path for a function.

        Args:
            qualified_name: Function qualified name

        Returns:
            File path or "unknown" if not found
        """
        query = """
        MATCH (f:Function {qualifiedName: $qualified_name})<-[:CONTAINS*]-(file:File)
        RETURN file.path AS path
        LIMIT 1
        """
        results = self.client.execute_query(query, qualified_name=qualified_name)
        return results[0]["path"] if results else "unknown"

    def get_feature_importance_report(self) -> Dict[str, float]:
        """Get report of feature importances.

        Returns:
            Dict mapping feature names to importance values
        """
        if self._feature_importances is None:
            return {}

        report = {}

        # Embedding features (aggregate)
        embedding_importance = float(np.sum(self._feature_importances[:-10]))
        report["embedding_total"] = round(embedding_importance, 4)

        # Individual metric features
        for name, importance in zip(
            FeatureExtractor.METRIC_NAMES,
            self._feature_importances[-10:],
        ):
            report[name] = round(float(importance), 4)

        return report

    def save(self, path: Path) -> None:
        """Save trained model to disk.

        Args:
            path: Path to save the model file

        Raises:
            RuntimeError: If model has not been trained
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        model_data = {
            "model": self.model,
            "config": self.config,
            "metrics": self.metrics,
            "feature_importances": self._feature_importances,
            "embedding_property": self.feature_extractor.embedding_property,
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model_data, path)
        logger.info(f"Model saved to {path}")

    @classmethod
    def load(cls, path: Path, client: Neo4jClient) -> "BugPredictor":
        """Load trained model from disk.

        Args:
            path: Path to the saved model file
            client: Neo4j database client

        Returns:
            BugPredictor instance with loaded model
        """
        model_data = joblib.load(path)

        predictor = cls(client, config=model_data["config"])
        predictor.model = model_data["model"]
        predictor.metrics = model_data["metrics"]
        predictor._feature_importances = model_data.get("feature_importances")
        predictor.feature_extractor.embedding_property = model_data.get(
            "embedding_property", "node2vec_embedding"
        )
        predictor._is_trained = True

        logger.info(f"Model loaded from {path}")
        return predictor

    def export_predictions(
        self,
        predictions: List[PredictionResult],
        output_path: Path,
    ) -> None:
        """Export predictions to JSON file.

        Args:
            predictions: List of prediction results
            output_path: Path to output JSON file
        """
        data = {
            "predictions": [p.to_dict() for p in predictions],
            "model_metrics": self.metrics.to_dict() if self.metrics else None,
            "feature_importances": self.get_feature_importance_report(),
        }

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Predictions exported to {output_path}")
