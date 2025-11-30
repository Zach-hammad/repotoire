"""GraphSAGE-based zero-shot defect detector.

This detector uses a pre-trained GraphSAGE model to identify high-risk
functions in any codebase without requiring project-specific training data.

Key capability: Train once on open-source projects, apply everywhere.

The model learned generalizable patterns from:
- Call graph structure (which functions call which)
- Code metrics (complexity, lines of code)
- Semantic embeddings (code meaning)

These patterns transfer to new codebases because they capture universal
indicators of defect-prone code.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import uuid

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

logger = logging.getLogger(__name__)


class GraphSAGEDetector(CodeSmellDetector):
    """Zero-shot defect detector using GraphSAGE.

    Uses a pre-trained GraphSAGE model to predict defect probability
    for all functions in the codebase. The model was trained on multiple
    open-source projects and can generalize to any new codebase.

    Key advantage over traditional ML detectors:
    - **Inductive**: Works on completely unseen codebases
    - **No retraining**: One model works everywhere
    - **Fast inference**: ~2-5 seconds for 10k functions

    Example:
        >>> detector = GraphSAGEDetector(
        ...     client=neo4j_client,
        ...     model_path=Path("models/graphsage.pt"),
        ...     risk_threshold=0.7,
        ... )
        >>> findings = detector.detect()
        >>> for f in findings:
        ...     print(f"{f.title}: {f.graph_context['probability']:.0%}")
    """

    name = "graphsage-zero-shot"
    description = "Zero-shot defect prediction using GraphSAGE trained on open-source projects"

    def __init__(
        self,
        client: Any,
        model_path: Optional[Path] = None,
        risk_threshold: float = 0.5,
        embedding_property: str = "embedding",
    ):
        """Initialize GraphSAGE detector.

        Args:
            client: Database client (Neo4jClient or FalkorDBClient)
            model_path: Path to pre-trained GraphSAGE model
            risk_threshold: Probability threshold for flagging (0.0-1.0)
            embedding_property: Node property containing embeddings
        """
        super().__init__(client)
        self.client = client
        self.model_path = model_path
        self.risk_threshold = risk_threshold
        self.embedding_property = embedding_property
        self._trainer = None

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on bug probability.

        Args:
            finding: Finding to calculate severity for

        Returns:
            Severity level based on probability threshold
        """
        probability = finding.graph_context.get("probability", 0.5)

        if probability >= 0.9:
            return Severity.CRITICAL
        elif probability >= 0.8:
            return Severity.HIGH
        elif probability >= 0.7:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def detect(self) -> List[Finding]:
        """Detect defects using zero-shot GraphSAGE.

        Applies the pre-trained model to the current codebase's graph
        structure to identify high-risk functions.

        Returns:
            List of findings for high-risk functions, sorted by probability
        """
        # Skip if no model configured
        if self.model_path is None:
            logger.info(
                "GraphSAGE detector skipped: no model path configured. "
                "Train a model with 'repotoire ml train-graphsage'"
            )
            return []

        # Check if model exists
        if not self.model_path.exists():
            logger.warning(
                f"GraphSAGE detector skipped: model not found at {self.model_path}"
            )
            return []

        # Try to load model
        try:
            from repotoire.ml.cross_project_trainer import CrossProjectTrainer
            self._trainer = CrossProjectTrainer.load(self.model_path)
        except ImportError:
            logger.warning(
                "GraphSAGE detector skipped: torch or torch-geometric not installed. "
                "Install with: pip install torch torch-geometric"
            )
            return []
        except Exception as e:
            logger.error(f"Failed to load GraphSAGE model: {e}")
            return []

        # Extract graph data
        try:
            data, node_mapping = self._export_graph()
        except Exception as e:
            logger.error(f"Failed to export graph: {e}")
            return []

        if data.x.size(0) == 0:
            logger.info("No functions with embeddings found for GraphSAGE prediction")
            return []

        # Run zero-shot prediction
        try:
            predictions = self._trainer.predict_zero_shot(data)
        except Exception as e:
            logger.error(f"GraphSAGE prediction failed: {e}")
            return []

        # Convert to findings
        findings = []
        idx_to_name = {v: k for k, v in node_mapping.items()}

        for pred in predictions:
            probability = pred["buggy_probability"]
            if probability >= self.risk_threshold:
                qualified_name = idx_to_name.get(pred["node_idx"], "unknown")
                finding = self._prediction_to_finding(
                    qualified_name=qualified_name,
                    probability=probability,
                )
                findings.append(finding)

        # Sort by probability descending
        findings.sort(
            key=lambda f: f.graph_context.get("probability", 0),
            reverse=True,
        )

        logger.info(
            f"GraphSAGE detector found {len(findings)} high-risk functions "
            f"(threshold: {self.risk_threshold:.0%})"
        )

        return findings

    def _export_graph(self) -> Any:
        """Export current project's graph to PyTorch Geometric format.

        Returns:
            Tuple of (Data object, node_mapping dict)
        """
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor

        extractor = GraphFeatureExtractor(
            self.client,
            embedding_property=self.embedding_property,
        )

        return extractor.extract_graph_data()

    def _prediction_to_finding(
        self,
        qualified_name: str,
        probability: float,
    ) -> Finding:
        """Convert a prediction to a Finding.

        Args:
            qualified_name: Function qualified name
            probability: Defect probability (0.0-1.0)

        Returns:
            Finding object for the analysis report
        """
        # Determine severity
        if probability >= 0.9:
            severity = Severity.CRITICAL
        elif probability >= 0.8:
            severity = Severity.HIGH
        elif probability >= 0.7:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Build description
        description = (
            f"GraphSAGE zero-shot model predicts {probability:.0%} defect probability. "
            f"This prediction is based on structural patterns learned from open-source "
            f"projects and applied without any project-specific training."
        )

        # Get file path
        file_path = self._get_file_path(qualified_name)

        # Build recommendation
        recommendations = [
            "Review this function for potential bugs",
            "Add comprehensive unit tests covering edge cases",
            "Check error handling and input validation",
            "Consider code review by a senior developer",
        ]

        if probability >= 0.9:
            recommendations.insert(0, "URGENT: This function has very high defect risk")

        return Finding(
            id=str(uuid.uuid4()),
            detector=self.name,
            severity=severity,
            title=f"Zero-shot defect risk: {qualified_name.split('.')[-1]}",
            description=description,
            affected_files=[file_path] if file_path != "unknown" else [],
            affected_nodes=[qualified_name],
            suggested_fix="\n".join(f"{i+1}. {r}" for i, r in enumerate(recommendations[:4])),
            graph_context={
                "probability": probability,
                "model_type": "graphsage-zero-shot",
                "risk_threshold": self.risk_threshold,
                "category": "bug-risk",
            },
        )

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
        try:
            results = self.client.execute_query(query, qualified_name=qualified_name)
            return results[0]["path"] if results else "unknown"
        except Exception:
            return "unknown"

    def get_model_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the loaded model.

        Returns:
            Dict with model configuration, or None if not loaded
        """
        if self._trainer is None:
            return None

        return {
            "model_path": str(self.model_path),
            "risk_threshold": self.risk_threshold,
            "model_config": {
                "input_dim": self._trainer.model_config.input_dim,
                "hidden_dim": self._trainer.model_config.hidden_dim,
                "num_layers": self._trainer.model_config.num_layers,
                "aggregator": self._trainer.model_config.aggregator,
            },
            "device": self._trainer.training_config.device,
        }
