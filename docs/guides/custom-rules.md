# Custom Rules

Create custom detection rules tailored to your codebase and coding standards.

## Overview

Repotoire supports two types of custom detectors:

1. **Graph Detectors** - Cypher queries against the knowledge graph
2. **Hybrid Detectors** - External tools combined with graph context

## Detector Architecture

All detectors inherit from `CodeSmellDetector` and implement `detect()`:

```python
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

class MyDetector(CodeSmellDetector):
    """Custom detector description."""

    name = "MyDetector"
    description = "Detects custom code patterns"

    def detect(self) -> list[Finding]:
        """Run detection and return findings."""
        findings = []
        # Detection logic here
        return findings
```

## Graph-Based Detectors

Query the Neo4j knowledge graph using Cypher.

### Example: Detect Large Functions

```python
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

class LargeFunctionDetector(CodeSmellDetector):
    """Detect functions with too many lines of code."""

    name = "LargeFunctionDetector"
    description = "Finds functions exceeding LOC threshold"

    def __init__(self, neo4j_client, max_lines: int = 100):
        super().__init__(neo4j_client)
        self.max_lines = max_lines

    def detect(self) -> list[Finding]:
        query = """
        MATCH (f:Function)
        WHERE f.lineCount > $max_lines
        RETURN f.qualifiedName as name,
               f.filePath as file,
               f.lineStart as line,
               f.lineCount as loc
        ORDER BY f.lineCount DESC
        """

        results = self.neo4j_client.execute_query(
            query, {"max_lines": self.max_lines}
        )

        findings = []
        for record in results:
            severity = Severity.HIGH if record["loc"] > 200 else Severity.MEDIUM

            findings.append(Finding(
                id=f"large-function-{record['name']}",
                detector=self.name,
                severity=severity,
                title=f"Large function: {record['name']}",
                description=f"Function has {record['loc']} lines (threshold: {self.max_lines})",
                affected_files=[record["file"]],
                affected_nodes=[record["name"]],
                line_start=record["line"],
                suggested_fix="Consider breaking into smaller, focused functions"
            ))

        return findings
```

### Example: Detect Circular Dependencies

```python
class CircularImportDetector(CodeSmellDetector):
    """Detect circular import dependencies."""

    name = "CircularImportDetector"
    description = "Finds modules that import each other"

    def detect(self) -> list[Finding]:
        query = """
        MATCH path = (a:Module)-[:IMPORTS*2..5]->(a)
        WHERE all(r IN relationships(path) WHERE type(r) = 'IMPORTS')
        WITH nodes(path) as cycle
        RETURN [n IN cycle | n.name] as modules
        LIMIT 50
        """

        results = self.neo4j_client.execute_query(query)

        findings = []
        for record in results:
            modules = record["modules"]
            cycle_str = " -> ".join(modules)

            findings.append(Finding(
                id=f"circular-import-{hash(cycle_str)}",
                detector=self.name,
                severity=Severity.HIGH,
                title="Circular import detected",
                description=f"Import cycle: {cycle_str}",
                affected_nodes=modules,
                suggested_fix="Break the cycle by extracting shared code into a separate module"
            ))

        return findings
```

## Hybrid Detectors

Combine external tools with graph context for richer analysis.

### Example: Security + Graph Context

```python
import subprocess
import json
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

class EnhancedSecurityDetector(CodeSmellDetector):
    """Run Bandit and enrich with graph context."""

    name = "EnhancedSecurityDetector"
    description = "Security scanning with call graph context"

    def detect(self) -> list[Finding]:
        # Run external tool
        result = subprocess.run(
            ["bandit", "-r", self.repo_path, "-f", "json"],
            capture_output=True, text=True
        )
        bandit_results = json.loads(result.stdout)

        findings = []
        for issue in bandit_results.get("results", []):
            # Enrich with graph context
            callers = self._find_callers(issue["filename"], issue["line_number"])

            severity = self._map_severity(issue["severity"])

            # Increase severity if called from API endpoints
            if any("endpoint" in c.lower() or "route" in c.lower() for c in callers):
                severity = Severity.CRITICAL

            findings.append(Finding(
                id=f"security-{issue['test_id']}-{issue['line_number']}",
                detector=self.name,
                severity=severity,
                title=issue["test_name"],
                description=issue["issue_text"],
                affected_files=[issue["filename"]],
                line_start=issue["line_number"],
                metadata={
                    "callers": callers,
                    "bandit_confidence": issue["confidence"]
                },
                suggested_fix=issue.get("more_info", "Review security best practices")
            ))

        return findings

    def _find_callers(self, file_path: str, line: int) -> list[str]:
        """Find functions that call the vulnerable code."""
        query = """
        MATCH (f:Function)-[:CALLS]->(target:Function)
        WHERE target.filePath = $file
          AND target.lineStart <= $line
          AND target.lineEnd >= $line
        RETURN f.qualifiedName as caller
        """
        results = self.neo4j_client.execute_query(
            query, {"file": file_path, "line": line}
        )
        return [r["caller"] for r in results]

    def _map_severity(self, bandit_severity: str) -> Severity:
        return {
            "LOW": Severity.LOW,
            "MEDIUM": Severity.MEDIUM,
            "HIGH": Severity.HIGH
        }.get(bandit_severity, Severity.MEDIUM)
```

## Registering Custom Detectors

### Method 1: Plugin File

Create `repotoire_plugins.py` in your project root:

```python
# repotoire_plugins.py
from my_detectors import LargeFunctionDetector, CircularImportDetector

DETECTORS = [
    LargeFunctionDetector,
    CircularImportDetector,
]
```

### Method 2: Configuration

Add to `.repotoirerc`:

```yaml
detectors:
  custom:
    - module: my_detectors
      class: LargeFunctionDetector
      config:
        max_lines: 150

    - module: my_detectors
      class: CircularImportDetector
```

### Method 3: Programmatic

```python
from repotoire.detectors.engine import AnalysisEngine
from my_detectors import LargeFunctionDetector

engine = AnalysisEngine(neo4j_client)
engine.register_detector(LargeFunctionDetector(neo4j_client, max_lines=100))

health = engine.analyze()
```

## Testing Custom Detectors

### Unit Tests

```python
import pytest
from unittest.mock import Mock
from my_detectors import LargeFunctionDetector

def test_large_function_detector():
    # Mock Neo4j client
    mock_client = Mock()
    mock_client.execute_query.return_value = [
        {"name": "big_func", "file": "utils.py", "line": 10, "loc": 150}
    ]

    detector = LargeFunctionDetector(mock_client, max_lines=100)
    findings = detector.detect()

    assert len(findings) == 1
    assert findings[0].severity.name == "MEDIUM"
    assert "big_func" in findings[0].title

def test_large_function_high_severity():
    mock_client = Mock()
    mock_client.execute_query.return_value = [
        {"name": "huge_func", "file": "main.py", "line": 5, "loc": 250}
    ]

    detector = LargeFunctionDetector(mock_client, max_lines=100)
    findings = detector.detect()

    assert findings[0].severity.name == "HIGH"
```

### Integration Tests

```python
@pytest.fixture
def neo4j_client():
    client = Neo4jClient(
        uri="bolt://localhost:7687",
        password="test-password"
    )
    yield client
    client.close()

def test_detector_with_real_graph(neo4j_client):
    detector = LargeFunctionDetector(neo4j_client, max_lines=50)
    findings = detector.detect()

    # Verify findings have required fields
    for finding in findings:
        assert finding.id
        assert finding.severity
        assert finding.affected_files
```

## Best Practices

### 1. Optimize Cypher Queries

```cypher
// Use indexes
MATCH (f:Function {qualifiedName: $name}) ...

// Limit results
MATCH (f:Function) WHERE f.complexity > 20
RETURN f LIMIT 100

// Use parameters, not string interpolation
MATCH (f:Function) WHERE f.lineCount > $threshold
```

### 2. Set Appropriate Severity

| Severity | When to Use |
|----------|-------------|
| CRITICAL | Security vulnerabilities, data loss risk |
| HIGH | Major architectural issues, performance problems |
| MEDIUM | Code smells, maintainability concerns |
| LOW | Style issues, minor improvements |
| INFO | Informational observations |

### 3. Provide Actionable Fixes

```python
Finding(
    ...
    suggested_fix="Break function into smaller units. Consider extracting:\n"
                  "- Validation logic into validate_input()\n"
                  "- Data transformation into transform_data()\n"
                  "- I/O operations into save_results()"
)
```

## Next Steps

- [RAG Features](rag.md) - AI-powered analysis
- [CI/CD Integration](cicd.md) - Run custom rules in CI
- [API Reference](../api/overview.md) - Programmatic access
