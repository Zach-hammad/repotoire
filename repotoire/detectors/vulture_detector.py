"""Vulture-based unused code detector with Neo4j graph enrichment.

This hybrid detector combines vulture's accurate dead code detection with Neo4j graph data
to provide detailed unused code findings with rich context.

Architecture:
    1. Run vulture on repository (fast AST-based unused code detection)
    2. Parse vulture output
    3. Filter false positives using graph-based dynamic usage detection (REPO-153)
    4. Enrich findings with Neo4j graph data (LOC, complexity, dependencies)
    5. Generate detailed dead code findings

This approach achieves:
    - High accuracy (minimal false positives compared to graph-based detection)
    - Fast detection (AST-based, O(n))
    - Rich context (graph-based metadata)
    - Dynamic usage filtering (getattr, factories, decorators)
    - Actionable insights (safe to remove vs needs investigation)

Performance: ~2-5 seconds even on large codebases
"""

import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class VultureDetector(CodeSmellDetector):
    """Detects unused code using vulture with graph enrichment.

    Uses vulture for accurate dead code detection and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        min_confidence: Minimum confidence level (0-100, default: 80)
        max_findings: Maximum findings to report (default: 100)
        exclude: List of patterns to exclude (default: tests, migrations)
    """

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize vulture detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - min_confidence: Min confidence (0-100)
                - max_findings: Max findings to report
                - exclude: List of patterns to exclude
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.min_confidence = config.get("min_confidence", 80)
        self.max_findings = config.get("max_findings", 100)
        self.enricher = enricher  # Graph enrichment for cross-detector collaboration

        # Default exclude patterns - don't check tests, migrations, or scripts
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            "scripts/",
            "setup.py",
            "conftest.py",
        ]
        self.exclude = config.get("exclude", default_exclude)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

        # Dynamic usage patterns that reduce confidence (REPO-153)
        self.dynamic_call_patterns = {
            "getattr", "setattr", "hasattr", "delattr",
            "__getattribute__", "__getattr__", "__setattr__",
        }

        # Factory/registry patterns that might use items dynamically
        self.factory_patterns = {
            "factory", "registry", "create_", "build_", "make_",
            "get_handler", "get_processor", "dispatch",
        }

        # Cache for dynamic usage check results
        self._dynamic_usage_cache: Dict[str, Dict] = {}

    def detect(self) -> List[Finding]:
        """Run vulture and enrich findings with graph data.

        Includes dynamic usage filtering to reduce false positives (REPO-153).

        Returns:
            List of dead code findings
        """
        logger.info(f"Running vulture on {self.repository_path}")

        # Clear dynamic usage cache for fresh analysis
        self._dynamic_usage_cache = {}

        # Run vulture and get results
        vulture_findings = self._run_vulture()

        if not vulture_findings:
            logger.info("No unused code found by vulture")
            return []

        # Filter out likely false positives (REPO-153)
        filtered_count = 0
        filtered_findings = []
        for vf in vulture_findings:
            if self._should_filter_finding(vf):
                filtered_count += 1
            else:
                filtered_findings.append(vf)

        if filtered_count > 0:
            logger.info(f"Filtered {filtered_count} likely false positives")

        # Group by file and type
        findings_by_file: Dict[str, List[Dict]] = {}
        for vf in filtered_findings[:self.max_findings]:
            file_path = vf["file"]
            if file_path not in findings_by_file:
                findings_by_file[file_path] = []
            findings_by_file[file_path].append(vf)

        # Create enriched findings
        findings = []
        for file_path, file_findings in findings_by_file.items():
            graph_context = self._get_file_context(file_path)

            for vf in file_findings:
                finding = self._create_finding(vf, graph_context)
                if finding:
                    findings.append(finding)

        logger.info(f"Created {len(findings)} unused code findings (after filtering)")
        return findings

    def _run_vulture(self) -> List[Dict[str, Any]]:
        """Run vulture and parse output.

        Returns:
            List of unused code dictionaries
        """
        try:
            # Build vulture command
            cmd = [
                "vulture",
                str(self.repository_path),
                f"--min-confidence={self.min_confidence}",
            ]

            # Add exclude patterns
            for pattern in self.exclude:
                cmd.extend(["--exclude", pattern])

            # Run vulture
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=60  # Dead code detection is fast, 60s is generous
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Vulture timed out after 60s on {self.repository_path}")
                return []

            # Parse output (vulture outputs to stdout)
            # Format: <file>:<line>: unused <type> '<name>' (confidence%)
            findings = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parsed = self._parse_vulture_line(line)
                if parsed:
                    findings.append(parsed)

            logger.info(f"vulture found {len(findings)} unused items")
            return findings

        except FileNotFoundError:
            logger.error("vulture not found. Install with: pip install vulture")
            return []
        except Exception as e:
            logger.error(f"Error running vulture: {e}")
            return []

    def _parse_vulture_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single vulture output line.

        Args:
            line: Vulture output line

        Returns:
            Parsed finding dictionary or None
        """
        try:
            # Format: <file>:<line>: unused <type> '<name>' (confidence%)
            parts = line.split(":", 2)
            if len(parts) < 3:
                return None

            file_path = parts[0].strip()
            line_num = int(parts[1].strip())
            message = parts[2].strip()

            # Extract type and name
            # Example: "unused function 'my_function' (100% confidence)"
            if "unused" not in message:
                return None

            # Extract type (function, class, variable, etc.)
            type_start = message.index("unused") + 7
            type_end = message.index("'")
            item_type = message[type_start:type_end].strip()

            # Extract name
            name_start = message.index("'") + 1
            name_end = message.index("'", name_start)
            name = message[name_start:name_end]

            # Extract confidence
            confidence_start = message.index("(") + 1
            confidence_end = message.index("%")
            confidence = int(message[confidence_start:confidence_end])

            return {
                "file": file_path,
                "line": line_num,
                "type": item_type,
                "name": name,
                "confidence": confidence,
                "message": message
            }

        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse vulture line: {line} - {e}")
            return None

    def _create_finding(
        self,
        vulture_finding: Dict[str, Any],
        graph_context: Dict[str, Any]
    ) -> Optional[Finding]:
        """Create finding from vulture result.

        Args:
            vulture_finding: vulture finding dictionary
            graph_context: Graph context for file

        Returns:
            Finding object or None if creation fails
        """
        file_path = vulture_finding["file"]
        line = vulture_finding["line"]
        item_type = vulture_finding["type"]
        name = vulture_finding["name"]
        confidence = vulture_finding["confidence"]

        # Check for dynamic usage patterns (REPO-153)
        dynamic_info = self._check_dynamic_usage(name, file_path)

        # Adjust confidence based on dynamic usage patterns
        adjusted_confidence = confidence
        if dynamic_info["dynamic_usage"]:
            # Reduce confidence if dynamic patterns detected
            reduction = int(dynamic_info["confidence_reduction"] * 100)
            adjusted_confidence = max(confidence - reduction, 50)

        # Determine severity based on adjusted confidence and type
        if adjusted_confidence >= 95:
            severity = Severity.MEDIUM  # Very likely unused
        elif adjusted_confidence >= 80:
            severity = Severity.LOW  # Probably unused
        else:
            severity = Severity.INFO  # Might be unused

        # Adjust severity for functions/classes (higher impact)
        if item_type in ("function", "class", "method") and adjusted_confidence >= 90:
            severity = Severity.HIGH

        # Create finding
        finding_id = str(uuid.uuid4())

        description = f"Unused {item_type} '{name}' detected by vulture.\n\n"
        description += f"**Confidence**: {adjusted_confidence}%"
        if dynamic_info["dynamic_usage"]:
            description += f" (reduced from {confidence}% due to dynamic patterns)\n"
            description += f"**Dynamic patterns detected**: {', '.join(dynamic_info['patterns'])}\n"
        else:
            description += "\n"

        if graph_context.get("file_loc"):
            description += f"**File Size**: {graph_context['file_loc']} LOC\n"

        if item_type in ("function", "class", "method"):
            description += "\n**Impact**: Removing this would reduce code complexity and maintenance burden.\n"
        else:
            description += "\n**Impact**: Dead code increases cognitive load and may confuse developers.\n"

        # Add warning if dynamic patterns detected
        if dynamic_info["dynamic_usage"]:
            description += "\n**Warning**: This item may be used dynamically. Review carefully before removing.\n"

        finding = Finding(
            id=finding_id,
            detector="VultureDetector",
            severity=severity,
            title=f"Unused {item_type}: {name}",
            description=description,
            affected_nodes=[],  # vulture doesn't know about graph nodes
            affected_files=[file_path],
            graph_context={
                "tool": "vulture",
                "item_type": item_type,
                "item_name": name,
                "line": line,
                "confidence": adjusted_confidence,
                "original_confidence": confidence,
                "file_loc": graph_context.get("file_loc", 0),
                "dynamic_usage": dynamic_info["dynamic_usage"],
                "dynamic_patterns": dynamic_info["patterns"],
            },
            suggested_fix=self._suggest_fix(item_type, name, adjusted_confidence),
            estimated_effort=self._estimate_effort(item_type, adjusted_confidence),
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration (REPO-151 Phase 2)
        # Note: Vulture doesn't provide qualified names, so we flag by file
        if self.enricher:
            try:
                # Create a pseudo-qualified name for the unused entity
                entity_qname = f"{file_path}:{name}"
                confidence_score = confidence / 100.0  # Convert to 0-1 scale

                self.enricher.flag_entity(
                    entity_qualified_name=entity_qname,
                    detector="VultureDetector",
                    severity=severity.value,
                    issues=[f"unused_{item_type}"],
                    confidence=confidence_score,
                    metadata={
                        "item_type": item_type,
                        "item_name": name,
                        "file": file_path,
                        "line": line,
                        "vulture_confidence": confidence
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to flag entity {name} in graph: {e}")

        # Add collaboration metadata to finding (REPO-150 Phase 1, REPO-153)
        evidence = ["vulture", f"confidence_{adjusted_confidence}", "external_tool"]
        tags = ["vulture", "unused_code", self._get_category_tag(item_type)]

        # Add dynamic usage info to evidence and tags
        if dynamic_info["dynamic_usage"]:
            evidence.append("dynamic_patterns_detected")
            tags.append("review_required")
            for pattern in dynamic_info["patterns"]:
                evidence.append(f"pattern:{pattern}")
        else:
            if adjusted_confidence >= 90:
                tags.append("high_confidence")

        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="VultureDetector",
                confidence=adjusted_confidence / 100.0,
                evidence=evidence,
                tags=tags
            )
        )

        return finding

    def _get_file_context(self, file_path: str) -> Dict[str, Any]:
        """Get context from Neo4j graph for file.

        Args:
            file_path: Relative file path

        Returns:
            Dictionary with graph context
        """
        # Normalize path for Neo4j
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        RETURN file.loc as file_loc
        LIMIT 1
        """

        try:
            results = self.db.execute_query(query, {"file_path": normalized_path})
            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0}

    def _suggest_fix(self, item_type: str, name: str, confidence: int) -> str:
        """Suggest fix based on item type and confidence.

        Args:
            item_type: Type of unused item
            name: Name of unused item
            confidence: Confidence level (0-100)

        Returns:
            Fix suggestion
        """
        if confidence >= 95:
            if item_type in ("function", "class", "method"):
                return f"Safe to remove: Delete unused {item_type} '{name}' and run tests to confirm"
            else:
                return f"Remove unused {item_type} '{name}'"
        elif confidence >= 80:
            return f"Investigate and remove if truly unused: Check for dynamic usage of '{name}'"
        else:
            return f"Review usage patterns: May be used dynamically or in external modules"

    def _estimate_effort(self, item_type: str, confidence: int) -> str:
        """Estimate effort to fix.

        Args:
            item_type: Type of unused item
            confidence: Confidence level

        Returns:
            Effort estimate
        """
        if confidence >= 95:
            if item_type in ("function", "class"):
                return "Small (15-30 minutes)"
            else:
                return "Tiny (5 minutes)"
        elif confidence >= 80:
            return "Small (30 minutes - 1 hour)"
        else:
            return "Medium (1-2 hours for investigation)"

    def _get_category_tag(self, item_type: str) -> str:
        """Get semantic category tag from item type.

        Args:
            item_type: Vulture item type (e.g., "function", "variable")

        Returns:
            Semantic category tag
        """
        # Map item types to semantic categories for cross-detector correlation
        if item_type in {"function", "method"}:
            return "unused_function"
        elif item_type in {"class"}:
            return "unused_class"
        elif item_type in {"variable", "attribute"}:
            return "unused_variable"
        elif item_type in {"import"}:
            return "unused_import"
        elif item_type in {"property"}:
            return "unused_property"
        else:
            return "unused_other"

    def _check_dynamic_usage(self, name: str, file_path: str) -> Dict[str, Any]:
        """Check if an item might be used dynamically via graph analysis.

        Reduces false positives by detecting:
        - getattr/setattr patterns
        - Factory/registry patterns
        - Decorator patterns
        - Test fixtures

        Args:
            name: Item name to check
            file_path: File path where item is defined

        Returns:
            Dict with dynamic_usage: bool, patterns: List[str], confidence_reduction: float
        """
        cache_key = f"{file_path}:{name}"
        if cache_key in self._dynamic_usage_cache:
            return self._dynamic_usage_cache[cache_key]

        result = {
            "dynamic_usage": False,
            "patterns": [],
            "confidence_reduction": 0.0,
        }

        try:
            # Check for getattr/setattr calls that might reference this item
            dynamic_query = """
            MATCH (f:Function)
            WHERE f.filePath = $file_path
            OPTIONAL MATCH (f)-[:CALLS]->(called:Function)
            WHERE called.name IN ['getattr', 'setattr', 'hasattr', 'delattr']
            WITH f, count(called) as dynamic_calls
            RETURN dynamic_calls > 0 as has_dynamic_calls
            """

            results = self.db.execute_query(dynamic_query, {"file_path": file_path})
            if results and results[0].get("has_dynamic_calls"):
                result["dynamic_usage"] = True
                result["patterns"].append("getattr/setattr")
                result["confidence_reduction"] += 0.15

            # Check if item name matches factory/registry patterns
            name_lower = name.lower()
            for pattern in self.factory_patterns:
                if pattern in name_lower:
                    result["dynamic_usage"] = True
                    result["patterns"].append(f"factory_pattern:{pattern}")
                    result["confidence_reduction"] += 0.10
                    break

            # Check for decorator usage on the item
            decorator_query = """
            MATCH (entity)
            WHERE (entity.qualifiedName ENDS WITH $name OR entity.name = $name)
              AND entity.decorators IS NOT NULL
              AND size(entity.decorators) > 0
            RETURN count(entity) > 0 as has_decorators
            """

            decorator_results = self.db.execute_query(decorator_query, {"name": name})
            if decorator_results and decorator_results[0].get("has_decorators"):
                result["dynamic_usage"] = True
                result["patterns"].append("has_decorators")
                result["confidence_reduction"] += 0.20

            # Check if this is a pytest fixture
            if name.startswith("fixture_") or file_path.endswith("conftest.py"):
                result["dynamic_usage"] = True
                result["patterns"].append("pytest_fixture")
                result["confidence_reduction"] += 0.30

            # Cap confidence reduction
            result["confidence_reduction"] = min(result["confidence_reduction"], 0.50)

        except Exception as e:
            logger.warning(f"Error checking dynamic usage for {name}: {e}")

        self._dynamic_usage_cache[cache_key] = result
        return result

    def _should_filter_finding(self, vulture_finding: Dict[str, Any]) -> bool:
        """Determine if a vulture finding should be filtered out.

        Args:
            vulture_finding: Vulture finding dictionary

        Returns:
            True if finding should be filtered (likely false positive)
        """
        name = vulture_finding.get("name", "")
        item_type = vulture_finding.get("type", "")
        confidence = vulture_finding.get("confidence", 0)

        # Always keep high-confidence findings
        if confidence >= 95:
            return False

        # Filter pytest fixtures
        if name.startswith("fixture_") or name.endswith("_fixture"):
            return True

        # Filter common framework callbacks
        callback_patterns = [
            "on_", "handle_", "_handler", "_callback",
            "setUp", "tearDown", "setUpClass", "tearDownClass",
        ]
        for pattern in callback_patterns:
            if pattern in name:
                return True

        # Filter factory/builder methods
        if item_type in ("function", "method"):
            for pattern in self.factory_patterns:
                if pattern in name.lower():
                    return True

        return False

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for an unused code finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
