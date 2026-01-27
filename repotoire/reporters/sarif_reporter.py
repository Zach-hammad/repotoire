"""SARIF (Static Analysis Results Interchange Format) reporter.

Generates SARIF 2.1.0 compliant output for integration with GitHub Code Scanning,
Azure DevOps, and other SARIF-compatible tools.

SARIF Specification: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from repotoire.models import CodebaseHealth, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# SARIF 2.1.0 schema URI
SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
SARIF_VERSION = "2.1.0"

# Map Repotoire severity to SARIF level
SEVERITY_TO_SARIF_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}

# Map Repotoire severity to SARIF security severity (for GitHub Code Scanning)
SEVERITY_TO_SECURITY_SEVERITY = {
    Severity.CRITICAL: "critical",
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
    Severity.INFO: "low",
}


class SARIFReporter:
    """Generate SARIF 2.1.0 compliant reports from analysis results."""

    def __init__(
        self,
        repo_path: Optional[Path] = None,
        tool_name: str = "Repotoire",
        tool_version: Optional[str] = None,
        include_snippets: bool = True,
    ):
        """Initialize SARIF reporter.

        Args:
            repo_path: Path to repository root (for relative paths and snippets)
            tool_name: Name of the analysis tool
            tool_version: Version of the analysis tool
            include_snippets: Whether to include code snippets in results
        """
        self.repo_path = Path(repo_path) if repo_path else None
        self.tool_name = tool_name
        self.include_snippets = include_snippets

        # Get version from package if not provided
        if tool_version is None:
            try:
                from importlib.metadata import version
                tool_version = version("repotoire")
            except Exception:
                tool_version = "0.0.0"
        self.tool_version = tool_version

    def generate(self, health: CodebaseHealth, output_path: Path) -> None:
        """Generate SARIF report from health data.

        Args:
            health: CodebaseHealth instance with analysis results
            output_path: Path to output SARIF JSON file
        """
        sarif_data = self._build_sarif(health)

        # Write to file with proper formatting
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sarif_data, f, indent=2, ensure_ascii=False)

        logger.info(f"SARIF report generated: {output_path}")

    def generate_string(self, health: CodebaseHealth) -> str:
        """Generate SARIF report as a string.

        Args:
            health: CodebaseHealth instance with analysis results

        Returns:
            SARIF JSON string
        """
        sarif_data = self._build_sarif(health)
        return json.dumps(sarif_data, indent=2, ensure_ascii=False)

    def _build_sarif(self, health: CodebaseHealth) -> Dict[str, Any]:
        """Build complete SARIF document.

        Args:
            health: CodebaseHealth instance

        Returns:
            SARIF document as dictionary
        """
        # Group findings by detector to create rules
        findings_by_detector = self._group_findings_by_detector(health.findings)

        # Build rules from unique detectors
        rules = self._build_rules(findings_by_detector)

        # Build results from findings
        results = self._build_results(health.findings, rules)

        # Build the complete SARIF document
        sarif = {
            "$schema": SARIF_SCHEMA,
            "version": SARIF_VERSION,
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": self.tool_name,
                            "version": self.tool_version,
                            "informationUri": "https://repotoire.com",
                            "rules": list(rules.values()),
                        }
                    },
                    "results": results,
                    "invocations": [
                        {
                            "executionSuccessful": True,
                            "endTimeUtc": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                }
            ],
        }

        # Add originalUriBaseIds if repo path is available
        if self.repo_path:
            sarif["runs"][0]["originalUriBaseIds"] = {
                "%SRCROOT%": {
                    "uri": self.repo_path.as_uri() + "/",
                }
            }

        # Add health summary as tool execution notification
        sarif["runs"][0]["invocations"][0]["toolExecutionNotifications"] = [
            {
                "level": "note",
                "message": {
                    "text": f"Analysis complete. Grade: {health.grade}, Score: {health.overall_score:.1f}/100"
                },
                "descriptor": {"id": "summary"},
            }
        ]

        return sarif

    def _group_findings_by_detector(
        self, findings: List[Finding]
    ) -> Dict[str, List[Finding]]:
        """Group findings by detector for rule generation.

        Args:
            findings: List of findings

        Returns:
            Dictionary mapping detector name to list of findings
        """
        grouped: Dict[str, List[Finding]] = {}
        for finding in findings:
            detector = finding.detector or "unknown"
            if detector not in grouped:
                grouped[detector] = []
            grouped[detector].append(finding)
        return grouped

    def _build_rules(
        self, findings_by_detector: Dict[str, List[Finding]]
    ) -> Dict[str, Dict[str, Any]]:
        """Build SARIF rules from detectors.

        Args:
            findings_by_detector: Findings grouped by detector

        Returns:
            Dictionary mapping rule ID to rule definition
        """
        rules: Dict[str, Dict[str, Any]] = {}

        for detector, findings in findings_by_detector.items():
            # Use detector name as rule ID
            rule_id = self._normalize_rule_id(detector)

            # Get severity from first finding (could vary, take highest)
            max_severity = max(
                (f.severity for f in findings),
                key=lambda s: list(Severity).index(s) if s in Severity else 0,
                default=Severity.INFO,
            )

            # Build rule
            rules[rule_id] = {
                "id": rule_id,
                "name": detector.replace("Detector", ""),
                "shortDescription": {
                    "text": f"Issue detected by {detector.replace('Detector', '')}"
                },
                "fullDescription": {
                    "text": self._get_detector_description(detector)
                },
                "defaultConfiguration": {
                    "level": SEVERITY_TO_SARIF_LEVEL.get(max_severity, "note")
                },
                "properties": {
                    "tags": self._get_detector_tags(detector),
                    "security-severity": self._get_security_severity_value(max_severity),
                },
                "helpUri": f"https://repotoire.com/docs/detectors/{quote(rule_id.lower())}",
            }

        return rules

    def _build_results(
        self, findings: List[Finding], rules: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build SARIF results from findings.

        Args:
            findings: List of findings
            rules: Built rules dictionary

        Returns:
            List of SARIF result objects
        """
        results = []

        for i, finding in enumerate(findings):
            rule_id = self._normalize_rule_id(finding.detector or "unknown")

            result: Dict[str, Any] = {
                "ruleId": rule_id,
                "level": SEVERITY_TO_SARIF_LEVEL.get(finding.severity, "note"),
                "message": {
                    "text": finding.description or finding.title,
                },
            }

            # Add locations from affected files
            locations = self._build_locations(finding)
            if locations:
                result["locations"] = locations

            # Add fingerprint for deduplication
            result["fingerprints"] = {
                "repotoire/finding/v1": finding.id or f"finding-{i}",
            }

            # Add properties with extra metadata
            properties: Dict[str, Any] = {
                "severity": finding.severity.value if finding.severity else "unknown",
            }

            if finding.suggested_fix:
                properties["suggestedFix"] = finding.suggested_fix

            if finding.estimated_effort:
                properties["estimatedEffort"] = finding.estimated_effort

            if finding.priority_score is not None:
                properties["priorityScore"] = finding.priority_score

            if finding.detector_agreement_count:
                properties["detectorAgreementCount"] = finding.detector_agreement_count

            if finding.aggregate_confidence:
                properties["aggregateConfidence"] = finding.aggregate_confidence

            result["properties"] = properties

            # Add code flows for related nodes
            if finding.affected_nodes and len(finding.affected_nodes) > 1:
                result["codeFlows"] = self._build_code_flows(finding)

            # Add fixes if suggested_fix is available
            if finding.suggested_fix:
                result["fixes"] = [
                    {
                        "description": {
                            "text": finding.suggested_fix,
                        }
                    }
                ]

            results.append(result)

        return results

    def _build_locations(self, finding: Finding) -> List[Dict[str, Any]]:
        """Build SARIF locations from finding.

        Args:
            finding: Finding instance

        Returns:
            List of SARIF location objects
        """
        locations = []

        for file_path in finding.affected_files or []:
            location: Dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": file_path,
                        "uriBaseId": "%SRCROOT%",
                    }
                }
            }

            # Try to extract line number from metadata or affected_nodes
            line_number = self._extract_line_number(finding, file_path)
            if line_number:
                location["physicalLocation"]["region"] = {
                    "startLine": line_number,
                }

                # Add snippet if available and enabled
                if self.include_snippets and self.repo_path:
                    snippet = self._get_snippet(file_path, line_number)
                    if snippet:
                        location["physicalLocation"]["region"]["snippet"] = {
                            "text": snippet
                        }

            locations.append(location)

        return locations

    def _build_code_flows(self, finding: Finding) -> List[Dict[str, Any]]:
        """Build SARIF code flows for findings with multiple related nodes.

        Args:
            finding: Finding instance

        Returns:
            List of SARIF code flow objects
        """
        if not finding.affected_nodes:
            return []

        thread_flow_locations = []
        for i, node in enumerate(finding.affected_nodes):
            location: Dict[str, Any] = {
                "location": {
                    "message": {
                        "text": f"Step {i + 1}: {node}"
                    }
                }
            }

            # Try to get file path from node (qualified name format: file.py::Class.method)
            if "::" in node:
                file_part = node.split("::")[0]
                location["location"]["physicalLocation"] = {
                    "artifactLocation": {
                        "uri": file_part,
                        "uriBaseId": "%SRCROOT%",
                    }
                }

            thread_flow_locations.append(location)

        return [
            {
                "threadFlows": [
                    {
                        "locations": thread_flow_locations,
                    }
                ]
            }
        ]

    def _extract_line_number(self, finding: Finding, file_path: str) -> Optional[int]:
        """Extract line number from finding metadata.

        Args:
            finding: Finding instance
            file_path: File path to get line number for

        Returns:
            Line number or None
        """
        # Check metadata for line number
        if hasattr(finding, "metadata") and finding.metadata:
            if "line" in finding.metadata:
                return int(finding.metadata["line"])
            if "start_line" in finding.metadata:
                return int(finding.metadata["start_line"])

        # Try to extract from affected_nodes (qualified name format includes line)
        for node in finding.affected_nodes or []:
            if file_path in node and ":" in node:
                # Format: file.py::Class:140.method:177
                parts = node.split(":")
                for part in parts:
                    if part.isdigit():
                        return int(part)

        return None

    def _get_snippet(self, file_path: str, line_number: int, context: int = 2) -> Optional[str]:
        """Get code snippet from file.

        Args:
            file_path: Relative file path
            line_number: Line number (1-indexed)
            context: Number of context lines before/after

        Returns:
            Code snippet or None
        """
        if not self.repo_path:
            return None

        try:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                return None

            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start = max(0, line_number - 1 - context)
            end = min(len(lines), line_number + context)

            return "".join(lines[start:end])

        except Exception as e:
            logger.debug(f"Could not get snippet for {file_path}: {e}")
            return None

    def _normalize_rule_id(self, detector: str) -> str:
        """Normalize detector name to valid SARIF rule ID.

        Args:
            detector: Detector name

        Returns:
            Normalized rule ID
        """
        # Remove 'Detector' suffix and convert to kebab-case
        name = detector.replace("Detector", "")
        # Convert CamelCase to kebab-case
        result = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                result.append("-")
            result.append(char.lower())
        return "repotoire/" + "".join(result)

    def _get_detector_description(self, detector: str) -> str:
        """Get description for a detector.

        Args:
            detector: Detector name

        Returns:
            Description string
        """
        descriptions = {
            "RuffLintDetector": "Fast Python linter with 400+ rules covering style, errors, and best practices.",
            "RuffImportDetector": "Detects import issues including unused imports and import order problems.",
            "MypyDetector": "Type checking for Python using mypy static analysis.",
            "PylintDetector": "Python code analysis for errors, coding standards, and code smells.",
            "BanditDetector": "Security-focused static analysis for common Python vulnerabilities.",
            "RadonDetector": "Code complexity metrics including cyclomatic complexity.",
            "JscpdDetector": "Duplicate code detection across the codebase.",
            "VultureDetector": "Dead code detection for unused functions, classes, and variables.",
            "SemgrepDetector": "Advanced security pattern detection using Semgrep rules.",
            "CircularDependencyDetector": "Detects circular import dependencies in the codebase.",
            "DeadCodeDetector": "Graph-based detection of unreachable code.",
            "GodClassDetector": "Identifies classes that have grown too large and complex.",
            "FeatureEnvyDetector": "Detects methods that use more features from other classes.",
            "ShotgunSurgeryDetector": "Identifies changes that require modifications in many places.",
            "MiddleManDetector": "Detects classes that only delegate to other classes.",
            "InappropriateIntimacyDetector": "Finds classes that are too tightly coupled.",
            "DataClumpsDetector": "Identifies groups of data that appear together frequently.",
            "TypeHintCoverageDetector": "Measures and reports type hint coverage.",
            "LongParameterListDetector": "Detects functions with too many parameters.",
            "TaintDetector": "Tracks untrusted data from sources to dangerous sinks.",
            "SATDDetector": "Detects self-admitted technical debt in comments.",
            "DependencyScanner": "Scans for vulnerable dependencies in requirements.",
        }
        return descriptions.get(
            detector, f"Code analysis performed by {detector.replace('Detector', '')} detector."
        )

    def _get_detector_tags(self, detector: str) -> List[str]:
        """Get tags for a detector.

        Args:
            detector: Detector name

        Returns:
            List of tags
        """
        # Map detectors to categories
        security_detectors = {
            "BanditDetector", "SemgrepDetector", "TaintDetector", "DependencyScanner"
        }
        quality_detectors = {
            "RuffLintDetector", "RuffImportDetector", "MypyDetector", "PylintDetector"
        }
        complexity_detectors = {
            "RadonDetector", "GodClassDetector", "LongParameterListDetector"
        }
        architecture_detectors = {
            "CircularDependencyDetector", "FeatureEnvyDetector", "ShotgunSurgeryDetector",
            "MiddleManDetector", "InappropriateIntimacyDetector", "DataClumpsDetector"
        }
        maintenance_detectors = {
            "DeadCodeDetector", "VultureDetector", "JscpdDetector", "SATDDetector"
        }

        tags = []
        if detector in security_detectors:
            tags.extend(["security", "vulnerability"])
        if detector in quality_detectors:
            tags.extend(["quality", "style"])
        if detector in complexity_detectors:
            tags.extend(["complexity", "maintainability"])
        if detector in architecture_detectors:
            tags.extend(["architecture", "design"])
        if detector in maintenance_detectors:
            tags.extend(["maintenance", "technical-debt"])

        return tags or ["code-smell"]

    def _get_security_severity_value(self, severity: Severity) -> str:
        """Get numeric security severity for GitHub Code Scanning.

        GitHub uses a 0-10 scale for security severity.

        Args:
            severity: Repotoire severity

        Returns:
            Security severity string (0.0-10.0)
        """
        values = {
            Severity.CRITICAL: "9.5",
            Severity.HIGH: "7.5",
            Severity.MEDIUM: "5.0",
            Severity.LOW: "2.5",
            Severity.INFO: "1.0",
        }
        return values.get(severity, "1.0")
