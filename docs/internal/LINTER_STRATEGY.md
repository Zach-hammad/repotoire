# Repotoire Linter Integration Strategy

## Philosophy

**Repotoire is NOT a linter replacement — it's a linter complement.**

Repotoire detects architectural and relational issues that require graph analysis. Traditional linters detect syntax, style, and local issues.

## Positioning

### What Linters Do Well
- Code formatting (Black, Prettier)
- Style enforcement (Ruff, ESLint)
- Type checking (mypy, TypeScript)
- Simple complexity (McCabe)
- Import sorting (isort)
- Security patterns (Bandit, semgrep)

### What Repotoire Does Uniquely
- Circular dependencies (requires graph traversal)
- Architectural bottlenecks (requires centrality analysis)
- God classes (requires relationship counting)
- Feature envy (requires cross-class usage analysis)
- Dead code (requires deep call graph analysis)
- Module coupling (requires relationship mapping)
- Layer violations (requires architectural graph)

## Integration Strategy

### Phase 1: Core Repotoire (Current)
Focus on graph-based detectors only. Market as complement to existing linters.

**Messaging**: "Run Ruff for code style, Repotoire for architecture"

### Phase 2: Aggregation Layer
```bash
# Run multiple tools, aggregate results
repotoire analyze --with-tools ruff,mypy,bandit

# Output unified report
repotoire report --format html --include-all-tools
```

**Implementation**:
```python
# repotoire/integrations/linter_aggregator.py
from typing import List, Dict
from repotoire.models import Finding

class LinterAggregator:
    """Aggregate findings from external linters."""

    def __init__(self, enabled_linters: List[str]):
        self.linters = {
            'ruff': RuffIntegration(),
            'mypy': MypyIntegration(),
            'bandit': BanditIntegration(),
            'eslint': ESLintIntegration(),
        }
        self.enabled = [self.linters[name] for name in enabled_linters]

    def run_all(self, repo_path: str) -> Dict[str, List[Finding]]:
        """Run all enabled linters and return findings."""
        results = {}
        for linter in self.enabled:
            results[linter.name] = linter.run(repo_path)
        return results

class RuffIntegration:
    name = "ruff"

    def run(self, repo_path: str) -> List[Finding]:
        """Run Ruff and convert to Repotoire findings."""
        import subprocess
        import json

        result = subprocess.run(
            ["ruff", "check", repo_path, "--output-format=json"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            return []  # No issues

        findings = []
        for issue in json.loads(result.stdout):
            finding = Finding(
                id=f"ruff_{issue['code']}_{issue['location']['row']}",
                detector="Ruff",
                severity=self._map_severity(issue['code']),
                title=f"{issue['code']}: {issue['message']}",
                description=issue['message'],
                affected_files=[issue['filename']],
                line_start=issue['location']['row'],
                line_end=issue['end_location']['row'],
                suggested_fix=issue.get('fix', {}).get('message')
            )
            findings.append(finding)

        return findings

    def _map_severity(self, code: str) -> Severity:
        """Map Ruff error codes to Repotoire severity."""
        if code.startswith('E9') or code.startswith('F'):
            return Severity.HIGH  # Syntax errors, undefined names
        elif code.startswith('E'):
            return Severity.LOW  # Style issues
        else:
            return Severity.MEDIUM
```

### Phase 3: Graph-Enhanced Linting

Use the graph to make linter checks more accurate:

```python
# repotoire/detectors/graph_linting.py

class GraphEnhancedLinting(CodeSmellDetector):
    """Use graph to improve linter-style checks."""

    def detect_truly_unused_imports(self, db: Neo4jClient) -> List[Finding]:
        """
        Linters detect syntactically unused imports.
        Graph detects semantically unused imports (never called in execution paths).
        """
        query = """
        MATCH (f:File)-[imp:IMPORTS]->(m:Module)
        WHERE NOT EXISTS {
            // Check if imported module is used anywhere in call chain
            MATCH (f)-[:CONTAINS*]->(func:Function)
            MATCH (func)-[:CALLS*1..3]->()-[:CONTAINED_IN]->(m)
        }
        AND NOT EXISTS {
            // Check if imported class is instantiated
            MATCH (f)-[:CONTAINS*]->(func:Function)
            MATCH (func)-[:USES]->(m)
        }
        RETURN f.filePath as file, m.qualifiedName as unused_import
        """

        results = db.execute_query(query)
        findings = []

        for result in results:
            findings.append(Finding(
                id=f"unused_import_{result['file']}_{result['unused_import']}",
                detector="GraphEnhancedLinting",
                severity=Severity.LOW,
                title=f"Truly unused import: {result['unused_import']}",
                description=f"Import {result['unused_import']} is never used in any execution path",
                affected_files=[result['file']],
                suggested_fix=f"Remove import statement for {result['unused_import']}"
            ))

        return findings

    def detect_feature_envy(self, db: Neo4jClient) -> List[Finding]:
        """
        Detect methods that use other classes more than their own.
        Requires graph to track cross-class method calls.
        """
        query = """
        MATCH (c:Class)-[:CONTAINS]->(m:Function)
        MATCH (m)-[r:USES|CALLS]->(target)
        WHERE target.qualifiedName STARTS WITH c.qualifiedName
        WITH m, c, count(r) as internal_uses

        MATCH (m)-[r:USES|CALLS]->(external)
        WHERE NOT external.qualifiedName STARTS WITH c.qualifiedName
        WITH m, c, internal_uses, count(r) as external_uses

        WHERE external_uses > internal_uses * 2
        RETURN m.qualifiedName as method,
               c.qualifiedName as class,
               internal_uses,
               external_uses
        """

        results = db.execute_query(query)
        findings = []

        for result in results:
            findings.append(Finding(
                id=f"feature_envy_{result['method']}",
                detector="GraphEnhancedLinting",
                severity=Severity.MEDIUM,
                title=f"Feature Envy: {result['method']}",
                description=f"Method uses external classes ({result['external_uses']} times) more than its own class ({result['internal_uses']} times)",
                affected_nodes=[result['method']],
                suggested_fix=f"Consider moving this method to the class it uses most, or refactor to reduce external dependencies"
            ))

        return findings

    def detect_shotgun_surgery(self, db: Neo4jClient) -> List[Finding]:
        """
        Detect classes that are used by many other classes.
        Indicates that changes to this class require changes in many places.
        """
        query = """
        MATCH (c:Class)<-[:USES|CALLS]-(caller:Function)
        WITH c, count(DISTINCT caller) as caller_count
        WHERE caller_count > 10
        RETURN c.qualifiedName as class, caller_count
        ORDER BY caller_count DESC
        """

        results = db.execute_query(query)
        findings = []

        for result in results:
            findings.append(Finding(
                id=f"shotgun_surgery_{result['class']}",
                detector="GraphEnhancedLinting",
                severity=Severity.HIGH,
                title=f"Shotgun Surgery Risk: {result['class']}",
                description=f"Class is used by {result['caller_count']} different functions. Changes will require updates across the codebase.",
                affected_nodes=[result['class']],
                suggested_fix="Consider creating a facade or wrapper to isolate changes, or split responsibilities"
            ))

        return findings
```

## Recommended Detectors to Add

### 1. Feature Envy (Graph-only)
Methods that use other classes more than their own class.

### 2. Shotgun Surgery (Graph-only)
Classes used by many others (change amplification).

### 3. Middle Man (Graph-only)
Classes that only delegate to other classes.

### 4. Inappropriate Intimacy (Graph-only)
Classes that access each other's internals too much.

### 5. Data Class (Hybrid)
Classes with only data, no behavior (enhanced by graph).

### 6. Speculative Generality (Graph-only)
Abstract classes/interfaces with only one implementation.

## SaaS Positioning

### Free Tier
- Graph-based architectural analysis only
- Complements your existing linters

### Pro Tier ($29/month)
- All graph detectors
- Linter aggregation (Ruff, mypy, Bandit, ESLint)
- Unified HTML reports
- Trend tracking

### Team Tier ($99/month)
- Everything in Pro
- Graph-enhanced linting (feature envy, shotgun surgery)
- Custom detector rules
- Team analytics

### Enterprise Tier (Custom)
- Everything in Team
- Custom linter integrations
- On-premise deployment
- SLA guarantees

## Implementation Priority

### Sprint 1: Core Value
✅ Circular dependencies
✅ God classes
✅ Dead code
✅ Architectural bottlenecks

### Sprint 2: Graph-Enhanced Linting
- [ ] Feature envy detector
- [ ] Shotgun surgery detector
- [ ] Middle man detector
- [ ] Inappropriate intimacy detector

### Sprint 3: Linter Aggregation
- [ ] Ruff integration
- [ ] mypy integration
- [ ] Bandit integration
- [ ] Unified reporting

### Sprint 4: Multi-Language
- [ ] TypeScript/JavaScript (ESLint integration)
- [ ] Java (PMD/SpotBugs integration)
- [ ] Go (golangci-lint integration)

## Marketing Message

**Don't Replace Your Linter — Augment It**

"Ruff catches style issues. mypy catches type errors. Bandit catches security vulnerabilities.

Repotoire catches architectural problems that require understanding your entire codebase:
- Which classes have become too central to your architecture?
- Where are the circular dependencies slowing down your builds?
- Which dead code can be safely removed?
- How coupled are your modules?

**Use Repotoire alongside your existing tools for complete code health visibility.**"

## Conclusion

**Recommendation**: Don't try to replace linters. Instead:

1. **MVP**: Focus on unique graph-based detection
2. **v1.0**: Add aggregation layer for unified reporting
3. **v2.0**: Add graph-enhanced linting for advanced checks

This positions Repotoire as a premium add-on to the developer's existing toolchain, not a replacement.
