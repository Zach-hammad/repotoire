"""ML-based bug prediction detector.

This detector uses a trained machine learning model combining Node2Vec graph
embeddings with code metrics to identify functions with high bug probability.

Unlike rule-based detectors, this approach learns patterns from historical
bug data, capturing complex structural and metric correlations that are
difficult to express as explicit rules.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

logger = logging.getLogger(__name__)


class MLBugDetector(CodeSmellDetector):
    """ML-based bug prediction detector using Node2Vec embeddings.

    Uses a trained bug prediction model to identify high-risk functions
    based on structural patterns in the call graph and code metrics.

    The detector:
    1. Loads a pre-trained model (RandomForest on Node2Vec + metrics)
    2. Predicts bug probability for all functions with embeddings
    3. Reports high-risk functions as findings

    Example:
        >>> detector = MLBugDetector(
        ...     client=neo4j_client,
        ...     model_path=Path("models/bug_predictor.pkl"),
        ...     risk_threshold=0.7,
        ... )
        >>> findings = detector.detect()
        >>> for f in findings:
        ...     print(f"{f.entity_name}: {f.metadata['bug_probability']:.1%}")
    """

    name = "ml-bug-predictor"
    description = "ML-based bug prediction using graph embeddings and code metrics"

    def __init__(
        self,
        client: Any,
        model_path: Optional[Path] = None,
        risk_threshold: float = 0.7,
    ):
        """Initialize ML bug detector.

        Args:
            client: Database client (Neo4jClient or FalkorDBClient)
            model_path: Path to trained model file (.pkl)
            risk_threshold: Probability threshold for flagging (0.0-1.0)
        """
        super().__init__(client)
        self.client = client  # Also store as client for bug predictor
        self.model_path = model_path
        self.risk_threshold = risk_threshold
        self._predictor = None

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity of a finding based on bug probability.

        Args:
            finding: Finding to calculate severity for

        Returns:
            Severity level based on bug probability threshold
        """
        bug_probability = finding.graph_context.get("bug_probability", 0.5)

        if bug_probability >= 0.9:
            return Severity.CRITICAL
        elif bug_probability >= 0.8:
            return Severity.HIGH
        elif bug_probability >= 0.7:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def detect(self) -> List[Finding]:
        """Detect high-risk functions using ML model.

        Loads the trained model and predicts bug probability for all
        functions that have Node2Vec embeddings.

        Returns:
            List of findings for functions exceeding the risk threshold
        """
        # Skip if no model path configured
        if self.model_path is None:
            logger.info(
                "ML bug detector skipped: no model path configured. "
                "Train a model with 'repotoire ml train-bug-predictor'"
            )
            return []

        # Check if model file exists
        if not self.model_path.exists():
            logger.warning(
                f"ML bug detector skipped: model file not found at {self.model_path}"
            )
            return []

        # Load model
        try:
            from repotoire.ml.bug_predictor import BugPredictor
            self._predictor = BugPredictor.load(self.model_path, self.client)
        except ImportError:
            logger.warning(
                "ML bug detector skipped: scikit-learn not installed. "
                "Install with: pip install scikit-learn"
            )
            return []
        except Exception as e:
            logger.error(f"Failed to load bug prediction model: {e}")
            return []

        # Predict for all functions
        try:
            predictions = self._predictor.predict_all_functions(
                risk_threshold=self.risk_threshold
            )
        except Exception as e:
            logger.error(f"Bug prediction failed: {e}")
            return []

        # Convert high-risk predictions to findings
        findings = []
        for pred in predictions:
            if pred.is_high_risk:
                findings.append(self._prediction_to_finding(pred))

        logger.info(
            f"ML bug detector found {len(findings)} high-risk functions "
            f"(threshold: {self.risk_threshold:.0%})"
        )

        return findings

    def _prediction_to_finding(self, pred: Any) -> Finding:
        """Convert a PredictionResult to a Finding.

        Args:
            pred: PredictionResult from the bug predictor

        Returns:
            Finding object for the analysis report
        """
        # Determine severity based on probability
        if pred.bug_probability >= 0.9:
            severity = Severity.CRITICAL
        elif pred.bug_probability >= 0.8:
            severity = Severity.HIGH
        elif pred.bug_probability >= 0.7:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Build description
        description = (
            f"ML model predicts {pred.bug_probability:.0%} probability "
            f"of this function containing a bug"
        )

        if pred.contributing_factors:
            factors_str = ", ".join(pred.contributing_factors[:3])
            description += f". Key factors: {factors_str}"

        if pred.similar_buggy_functions:
            similar_str = ", ".join(pred.similar_buggy_functions[:2])
            description += f". Similar to past buggy functions: {similar_str}"

        # Build recommendation based on contributing factors
        recommendations = self._generate_recommendations(pred)

        import uuid
        return Finding(
            id=str(uuid.uuid4()),
            detector=self.name,
            severity=severity,
            title=f"High defect risk: {pred.qualified_name.split('.')[-1]}",
            description=description,
            affected_files=[pred.file_path],
            affected_nodes=[pred.qualified_name],
            suggested_fix="\n".join(
                f"{i+1}. {r}" for i, r in enumerate(recommendations)
            ),
            graph_context={
                "bug_probability": pred.bug_probability,
                "contributing_factors": pred.contributing_factors,
                "similar_buggy_functions": pred.similar_buggy_functions,
                "risk_threshold": self.risk_threshold,
                "category": "bug-risk",
            },
        )

    def _generate_recommendations(self, pred: Any) -> List[str]:
        """Generate contextual recommendations based on contributing factors.

        Args:
            pred: PredictionResult with contributing factors

        Returns:
            List of actionable recommendations
        """
        recommendations = []

        # Analyze contributing factors to give specific advice
        factors_str = " ".join(pred.contributing_factors).lower()

        if "complexity" in factors_str:
            recommendations.append(
                "Reduce cyclomatic complexity by extracting helper functions "
                "or simplifying conditionals"
            )

        if "fan_out" in factors_str or "coupling" in factors_str:
            recommendations.append(
                "Reduce dependencies by applying dependency injection "
                "or breaking into smaller modules"
            )

        if "fan_in" in factors_str:
            recommendations.append(
                "This function is called by many others - ensure robust error handling "
                "and input validation"
            )

        if "churn" in factors_str:
            recommendations.append(
                "High change frequency indicates instability - consider stabilizing the API "
                "and adding regression tests"
            )

        if "has_tests" in factors_str:
            recommendations.append(
                "Add comprehensive unit and integration tests to catch bugs early"
            )

        if "loc" in factors_str:
            recommendations.append(
                "Large function size increases bug risk - consider breaking into "
                "smaller, focused functions"
            )

        # Always include generic recommendations if none matched
        if not recommendations:
            recommendations = [
                "Add comprehensive unit and integration tests",
                "Review error handling and edge cases",
                "Consider code review by a second developer",
                "Add input validation and defensive checks",
            ]

        return recommendations[:4]  # Limit to 4 recommendations

    def get_model_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the loaded model.

        Returns:
            Dict with model metrics and configuration, or None if not loaded
        """
        if self._predictor is None or self._predictor.metrics is None:
            return None

        return {
            "model_path": str(self.model_path),
            "risk_threshold": self.risk_threshold,
            "metrics": self._predictor.metrics.to_dict(),
            "feature_importances": self._predictor.get_feature_importance_report(),
        }
