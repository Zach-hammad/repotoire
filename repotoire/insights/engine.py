"""Insights engine that enriches analysis with ML and graph metrics.

Post-processes analysis findings to add:
- Bug probability scores (ML-powered)
- Impact radius (downstream dependencies)
- Graph-level insights (bottlenecks, coupling hotspots)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import logging
import time

from repotoire.graph import FalkorDBClient
from repotoire.models import Finding, Severity

logger = logging.getLogger(__name__)


@dataclass
class InsightsConfig:
    """Configuration for insights engine.
    
    Attributes:
        enable_bug_prediction: Use ML model to predict bug probability
        enable_impact_analysis: Calculate downstream impact radius
        enable_graph_metrics: Compute coupling and bottleneck metrics
        bug_model_path: Path to trained bug predictor model (optional)
        high_risk_threshold: Bug probability threshold for high risk (default: 0.7)
        impact_depth: Max depth for impact traversal (default: 3)
    """
    enable_bug_prediction: bool = True
    enable_impact_analysis: bool = True
    enable_graph_metrics: bool = True
    bug_model_path: Optional[str] = None
    high_risk_threshold: float = 0.7
    impact_depth: int = 3


@dataclass 
class ImpactRadius:
    """Impact analysis for a code entity.
    
    Attributes:
        entity: Qualified name of the entity
        direct_dependents: Functions/classes that directly call/use this
        indirect_dependents: Transitive dependents (2+ hops)
        affected_files: Unique files that would be affected by changes
        blast_radius: Total number of affected entities
        risk_multiplier: How much this amplifies bug risk (1.0 = normal)
    """
    entity: str
    direct_dependents: List[str] = field(default_factory=list)
    indirect_dependents: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    blast_radius: int = 0
    risk_multiplier: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity,
            "direct_dependents": len(self.direct_dependents),
            "indirect_dependents": len(self.indirect_dependents),
            "affected_files": len(self.affected_files),
            "blast_radius": self.blast_radius,
            "risk_multiplier": round(self.risk_multiplier, 2),
        }


@dataclass
class GraphInsights:
    """Graph-level insights about the codebase.
    
    Attributes:
        bottlenecks: High fan-in nodes everything depends on
        coupling_hotspots: Modules with excessive cross-dependencies
        dead_code_count: Number of unreachable functions/classes
        circular_dep_count: Number of import cycles
        max_call_depth: Deepest call chain in the codebase
        avg_fan_out: Average outgoing dependencies per function
    """
    bottlenecks: List[Dict[str, Any]] = field(default_factory=list)
    coupling_hotspots: List[Dict[str, Any]] = field(default_factory=list)
    dead_code_count: int = 0
    circular_dep_count: int = 0
    max_call_depth: int = 0
    avg_fan_out: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bottlenecks": self.bottlenecks[:10],  # Top 10
            "coupling_hotspots": self.coupling_hotspots[:10],
            "dead_code_count": self.dead_code_count,
            "circular_dep_count": self.circular_dep_count,
            "max_call_depth": self.max_call_depth,
            "avg_fan_out": round(self.avg_fan_out, 2),
        }


@dataclass
class CodebaseInsights:
    """Complete insights for a codebase.
    
    Attributes:
        graph_insights: Graph-level metrics and hotspots
        high_risk_entities: Entities with high bug probability
        high_impact_entities: Entities with large blast radius
        findings_enriched: Count of findings enriched with insights
        processing_time_ms: Time taken to compute insights
    """
    graph_insights: GraphInsights = field(default_factory=GraphInsights)
    high_risk_entities: List[Dict[str, Any]] = field(default_factory=list)
    high_impact_entities: List[Dict[str, Any]] = field(default_factory=list)
    findings_enriched: int = 0
    processing_time_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_insights": self.graph_insights.to_dict(),
            "high_risk_entities": self.high_risk_entities[:20],
            "high_impact_entities": self.high_impact_entities[:20],
            "findings_enriched": self.findings_enriched,
            "processing_time_ms": self.processing_time_ms,
        }


class InsightsEngine:
    """Enriches analysis findings with ML predictions and graph metrics.
    
    This engine runs after detectors to add:
    1. Bug probability scores to findings (if model trained)
    2. Impact radius for affected entities
    3. Graph-level insights (bottlenecks, coupling)
    
    Example:
        >>> from repotoire.insights import InsightsEngine, InsightsConfig
        >>> engine = InsightsEngine(graph_client, InsightsConfig())
        >>> enriched_findings, insights = engine.enrich(findings)
    """
    
    def __init__(
        self,
        graph_client: FalkorDBClient,
        config: Optional[InsightsConfig] = None,
    ):
        self.client = graph_client
        self.config = config or InsightsConfig()
        self._bug_predictor = None
        self._model_loaded = False
        
    def enrich(
        self,
        findings: List[Finding],
    ) -> Tuple[List[Finding], CodebaseInsights]:
        """Enrich findings with insights and compute graph metrics.
        
        Args:
            findings: List of findings from analysis
            
        Returns:
            Tuple of (enriched findings, codebase insights)
        """
        start_time = time.time()
        insights = CodebaseInsights()
        
        # Collect all affected entities from findings
        affected_entities = set()
        for finding in findings:
            affected_entities.update(finding.affected_nodes)
        
        # 1. Compute graph-level insights
        if self.config.enable_graph_metrics:
            try:
                insights.graph_insights = self._compute_graph_insights()
            except Exception as e:
                logger.warning(f"Failed to compute graph insights: {e}")
        
        # 2. Load bug predictor if available
        if self.config.enable_bug_prediction:
            self._load_bug_predictor()
        
        # 3. Enrich each finding
        enriched_count = 0
        impact_cache: Dict[str, ImpactRadius] = {}
        bug_prob_cache: Dict[str, float] = {}
        
        for finding in findings:
            enriched = False
            
            # Add impact radius for affected entities
            if self.config.enable_impact_analysis:
                for entity in finding.affected_nodes[:5]:  # Limit to avoid slowdown
                    if entity not in impact_cache:
                        try:
                            impact_cache[entity] = self._compute_impact_radius(entity)
                        except Exception as e:
                            logger.debug(f"Failed to compute impact for {entity}: {e}")
                            continue
                    
                    impact = impact_cache[entity]
                    if impact.blast_radius > 0:
                        finding.graph_context["impact_radius"] = impact.to_dict()
                        enriched = True
                        break  # Use first entity with impact
            
            # Add bug probability if model available
            if self._model_loaded and self.config.enable_bug_prediction:
                for entity in finding.affected_nodes[:3]:
                    if entity not in bug_prob_cache:
                        try:
                            prob = self._get_bug_probability(entity)
                            if prob is not None:
                                bug_prob_cache[entity] = prob
                        except Exception as e:
                            logger.debug(f"Failed to get bug prob for {entity}: {e}")
                            continue
                    
                    if entity in bug_prob_cache:
                        prob = bug_prob_cache[entity]
                        finding.graph_context["bug_probability"] = round(prob, 3)
                        finding.graph_context["high_risk"] = prob >= self.config.high_risk_threshold
                        enriched = True
                        break
            
            if enriched:
                enriched_count += 1
        
        insights.findings_enriched = enriched_count
        
        # 4. Identify high-risk and high-impact entities
        insights.high_risk_entities = [
            {"entity": e, "bug_probability": round(p, 3)}
            for e, p in sorted(bug_prob_cache.items(), key=lambda x: -x[1])[:20]
            if p >= self.config.high_risk_threshold
        ]
        
        insights.high_impact_entities = [
            {"entity": e, **i.to_dict()}
            for e, i in sorted(impact_cache.items(), key=lambda x: -x[1].blast_radius)[:20]
            if i.blast_radius >= 5
        ]
        
        insights.processing_time_ms = int((time.time() - start_time) * 1000)
        
        logger.info(
            f"Insights: enriched {enriched_count}/{len(findings)} findings, "
            f"{len(insights.high_risk_entities)} high-risk, "
            f"{len(insights.high_impact_entities)} high-impact entities "
            f"({insights.processing_time_ms}ms)"
        )
        
        return findings, insights
    
    def _compute_graph_insights(self) -> GraphInsights:
        """Compute graph-level insights from the codebase graph."""
        insights = GraphInsights()
        
        # Find bottlenecks (high fan-in nodes)
        try:
            bottleneck_query = """
            MATCH (f:Function)
            OPTIONAL MATCH (caller)-[:CALLS]->(f)
            WITH f, COUNT(DISTINCT caller) AS fan_in
            WHERE fan_in >= 5
            RETURN f.qualifiedName AS name, f.filePath AS file, fan_in
            ORDER BY fan_in DESC
            LIMIT 20
            """
            result = self.client.execute_query(bottleneck_query)
            insights.bottlenecks = [
                {"name": r[0], "file": r[1], "fan_in": r[2]}
                for r in result.result_set if r[0]
            ]
        except Exception as e:
            logger.debug(f"Bottleneck query failed: {e}")
        
        # Find coupling hotspots (modules with many cross-module deps)
        try:
            coupling_query = """
            MATCH (f1:Function)-[:CALLS]->(f2:Function)
            WHERE f1.filePath <> f2.filePath
            WITH f1.filePath AS source_file, 
                 COUNT(DISTINCT f2.filePath) AS coupled_files
            WHERE coupled_files >= 5
            RETURN source_file, coupled_files
            ORDER BY coupled_files DESC
            LIMIT 20
            """
            result = self.client.execute_query(coupling_query)
            insights.coupling_hotspots = [
                {"file": r[0], "coupled_to": r[1]}
                for r in result.result_set if r[0]
            ]
        except Exception as e:
            logger.debug(f"Coupling query failed: {e}")
        
        # Count dead code (functions with no callers and not entry points)
        try:
            dead_code_query = """
            MATCH (f:Function)
            WHERE NOT EXISTS { MATCH ()-[:CALLS]->(f) }
              AND NOT f.qualifiedName STARTS WITH 'test_'
              AND NOT f.qualifiedName CONTAINS '.test_'
              AND NOT f.name IN ['main', '__init__', '__main__']
            RETURN COUNT(f) AS dead_count
            """
            result = self.client.execute_query(dead_code_query)
            if result.result_set:
                insights.dead_code_count = result.result_set[0][0] or 0
        except Exception as e:
            logger.debug(f"Dead code query failed: {e}")
        
        # Calculate average fan-out
        try:
            fan_out_query = """
            MATCH (f:Function)
            OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
            WITH f, COUNT(DISTINCT callee) AS fan_out
            RETURN AVG(fan_out) AS avg_fan_out
            """
            result = self.client.execute_query(fan_out_query)
            if result.result_set and result.result_set[0][0]:
                insights.avg_fan_out = float(result.result_set[0][0])
        except Exception as e:
            logger.debug(f"Fan-out query failed: {e}")
        
        return insights
    
    def _compute_impact_radius(self, entity: str) -> ImpactRadius:
        """Compute impact radius for an entity using graph traversal."""
        impact = ImpactRadius(entity=entity)
        
        # Get direct dependents (1 hop)
        direct_query = """
        MATCH (f:Function {qualifiedName: $entity})<-[:CALLS]-(caller:Function)
        RETURN DISTINCT caller.qualifiedName AS name, caller.filePath AS file
        """
        try:
            result = self.client.execute_query(direct_query, {"entity": entity})
            for row in result.result_set:
                if row[0]:
                    impact.direct_dependents.append(row[0])
                if row[1]:
                    impact.affected_files.append(row[1])
        except Exception as e:
            logger.debug(f"Direct dependents query failed: {e}")
            return impact
        
        # Get indirect dependents (2-3 hops) if depth allows
        if self.config.impact_depth >= 2:
            indirect_query = """
            MATCH (f:Function {qualifiedName: $entity})<-[:CALLS*2..3]-(caller:Function)
            RETURN DISTINCT caller.qualifiedName AS name, caller.filePath AS file
            """
            try:
                result = self.client.execute_query(indirect_query, {"entity": entity})
                for row in result.result_set:
                    if row[0] and row[0] not in impact.direct_dependents:
                        impact.indirect_dependents.append(row[0])
                    if row[1] and row[1] not in impact.affected_files:
                        impact.affected_files.append(row[1])
            except Exception as e:
                logger.debug(f"Indirect dependents query failed: {e}")
        
        # Calculate blast radius and risk multiplier
        impact.blast_radius = len(impact.direct_dependents) + len(impact.indirect_dependents)
        impact.affected_files = list(set(impact.affected_files))
        
        # Risk multiplier: higher impact = bugs here are more dangerous
        if impact.blast_radius >= 20:
            impact.risk_multiplier = 2.0
        elif impact.blast_radius >= 10:
            impact.risk_multiplier = 1.5
        elif impact.blast_radius >= 5:
            impact.risk_multiplier = 1.2
        
        return impact
    
    def _load_bug_predictor(self) -> None:
        """Load the trained bug predictor model if available."""
        if self._model_loaded:
            return
            
        try:
            from repotoire.ml.bug_predictor import BugPredictor
            
            # Try to load from configured path or default location
            model_path = self.config.bug_model_path
            if model_path and Path(model_path).exists():
                self._bug_predictor = BugPredictor.load(model_path)
                self._model_loaded = True
                logger.info(f"Loaded bug predictor from {model_path}")
            else:
                # Check default paths
                default_paths = [
                    Path.home() / ".repotoire" / "models" / "bug_predictor.joblib",
                    Path(".repotoire") / "models" / "bug_predictor.joblib",
                ]
                for path in default_paths:
                    if path.exists():
                        self._bug_predictor = BugPredictor.load(str(path))
                        self._model_loaded = True
                        logger.info(f"Loaded bug predictor from {path}")
                        break
                        
        except ImportError:
            logger.debug("Bug predictor not available (sklearn not installed)")
        except Exception as e:
            logger.debug(f"Failed to load bug predictor: {e}")
    
    def _get_bug_probability(self, entity: str) -> Optional[float]:
        """Get bug probability for an entity from the ML model."""
        if not self._bug_predictor:
            return None
            
        try:
            result = self._bug_predictor.predict([entity])
            if result:
                return result[0].bug_probability
        except Exception as e:
            logger.debug(f"Bug prediction failed for {entity}: {e}")
        
        return None
