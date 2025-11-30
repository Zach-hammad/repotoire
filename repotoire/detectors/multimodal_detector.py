"""Multimodal fusion detector for enhanced code analysis.

This detector uses the multimodal fusion model to combine text embeddings
(semantic) with graph embeddings (structural) for best-in-class bug
prediction and code smell detection.

The fusion model learns when to trust semantic vs structural signals,
providing interpretable predictions with modality importance weights.

Example:
    >>> from repotoire.detectors.multimodal_detector import MultimodalDetector
    >>> from repotoire.graph.client import Neo4jClient
    >>>
    >>> client = Neo4jClient.from_env()
    >>> detector = MultimodalDetector(
    ...     client,
    ...     model_path=Path("models/multimodal.pt"),
    ...     tasks=["bug_prediction"],
    ... )
    >>> findings = detector.detect()
"""

from pathlib import Path
from typing import List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger
from repotoire.models import Finding, Severity

logger = get_logger(__name__)


class MultimodalDetector(CodeSmellDetector):
    """Detector using multimodal fusion for enhanced predictions.

    Combines text embeddings (semantic) and graph embeddings (structural)
    for best-in-class bug prediction and code smell detection.

    Features:
    - Multi-task predictions: bug risk, code smells, refactoring benefit
    - Interpretability: shows text vs graph modality contribution
    - Configurable thresholds per task
    - Lazy model loading for efficiency

    Attributes:
        name: Detector identifier for findings
        description: Human-readable description
    """

    name = "multimodal-analyzer"
    description = "Multimodal fusion for enhanced code analysis"

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        model_path: Optional[Path] = None,
        tasks: Optional[List[str]] = None,
        thresholds: Optional[dict] = None,
    ):
        """Initialize multimodal detector.

        Args:
            neo4j_client: Database client for graph queries
            model_path: Path to trained multimodal model file
            tasks: Tasks to run (default: all available)
            thresholds: Confidence thresholds per task (default: sensible defaults)
        """
        super().__init__(neo4j_client)
        self.model_path = model_path
        self.tasks = tasks or [
            "bug_prediction",
            "smell_detection",
            "refactoring_benefit",
        ]
        self.thresholds = thresholds or {
            "bug_prediction": 0.7,
            "smell_detection": 0.6,
            "refactoring_benefit": 0.7,
        }
        self._analyzer = None

    def _load_analyzer(self):
        """Lazy load the multimodal analyzer."""
        if self._analyzer is not None:
            return

        if self.model_path is None:
            logger.warning("No model path provided, multimodal detector disabled")
            return

        if not self.model_path.exists():
            logger.warning(f"Model file not found: {self.model_path}")
            return

        try:
            from repotoire.ml.multimodal_analyzer import MultimodalAnalyzer

            self._analyzer = MultimodalAnalyzer.load(self.model_path, self.db)
            logger.info(f"Loaded multimodal model from {self.model_path}")
        except ImportError as e:
            logger.warning(f"Failed to import multimodal analyzer: {e}")
        except Exception as e:
            logger.warning(f"Failed to load multimodal model: {e}")

    def detect(self) -> List[Finding]:
        """Detect issues using multimodal model.

        Returns:
            List of findings from all configured tasks
        """
        self._load_analyzer()

        if self._analyzer is None:
            return []

        findings = []

        for task in self.tasks:
            threshold = self.thresholds.get(task, 0.5)
            try:
                predictions = self._analyzer.predict_all_functions(task, threshold)

                for pred in predictions:
                    finding = self._prediction_to_finding(pred, task)
                    if finding:
                        findings.append(finding)

            except Exception as e:
                logger.warning(f"Failed to run {task} predictions: {e}")
                continue

        logger.info(f"Multimodal detector found {len(findings)} issues")
        return findings

    def _prediction_to_finding(
        self, pred: dict, task: str
    ) -> Optional[Finding]:
        """Convert prediction to Finding.

        Args:
            pred: Prediction dict from analyzer
            task: Task name

        Returns:
            Finding object, or None if prediction should be skipped
        """
        # Skip negative predictions
        if task == "bug_prediction" and pred["prediction"] == "clean":
            return None
        if task == "smell_detection" and pred["prediction"] == "none":
            return None
        if task == "refactoring_benefit" and pred["prediction"] == "low":
            return None

        # Determine severity based on confidence
        confidence = pred["confidence"]
        if confidence >= 0.9:
            severity = Severity.CRITICAL
        elif confidence >= 0.8:
            severity = Severity.HIGH
        elif confidence >= 0.7:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Build description based on task
        if task == "bug_prediction":
            title = f"High defect risk: {pred['qualified_name']}"
            description = (
                f"Multimodal model predicts {confidence:.0%} bug probability"
            )
            category = "bug-risk"
        elif task == "smell_detection":
            smell_name = pred["prediction"].replace("_", " ").title()
            title = f"Code smell detected: {smell_name}"
            description = (
                f"Multimodal model detects {smell_name} pattern with "
                f"{confidence:.0%} confidence"
            )
            category = "code-smell"
        else:  # refactoring_benefit
            title = f"High refactoring benefit: {pred['qualified_name']}"
            description = (
                f"Refactoring this function would provide {pred['prediction']} "
                f"benefit ({confidence:.0%} confidence)"
            )
            category = "refactoring"

        # Add modality breakdown
        text_w = pred["text_weight"]
        graph_w = pred["graph_weight"]
        description += f"\n\nModality importance:"
        description += f"\n- Semantic patterns: {text_w:.0%}"
        description += f"\n- Structural patterns: {graph_w:.0%}"
        description += f"\n\n{pred['interpretation']}"

        # Get file path
        file_path = self._get_file_path(pred["qualified_name"])

        return Finding(
            detector=self.name,
            category=category,
            severity=severity,
            title=title,
            description=description,
            file_path=file_path,
            entity_name=pred["qualified_name"],
            metadata={
                "task": task,
                "prediction": pred["prediction"],
                "confidence": pred["confidence"],
                "text_weight": pred["text_weight"],
                "graph_weight": pred["graph_weight"],
            },
        )

    def _get_file_path(self, qualified_name: str) -> str:
        """Get file path for a function.

        Args:
            qualified_name: Function's qualified name

        Returns:
            File path, or "unknown" if not found
        """
        query = """
        MATCH (f:Function {qualifiedName: $qualified_name})<-[:CONTAINS*]-(file:File)
        RETURN file.path AS path
        LIMIT 1
        """
        results = self.db.execute_query(query, qualified_name=qualified_name)
        return results[0]["path"] if results else "unknown"

    def severity(self, finding: Finding) -> Severity:
        """Get severity from finding.

        For multimodal detector, severity is already set during detection
        based on confidence level.

        Args:
            finding: Finding to assess

        Returns:
            Severity from finding metadata or default
        """
        return finding.severity
