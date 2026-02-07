"""Pickle Deserialization detector using graph queries.

Fast replacement for semgrep pickle rules. Detects unsafe deserialization patterns
that can lead to Remote Code Execution (RCE):

1. pickle.load(), pickle.loads() - always unsafe (arbitrary code execution)
2. torch.load() without weights_only=True - can execute arbitrary code
3. joblib.load() without trusted source verification
4. numpy.load() with allow_pickle=True - enables pickle execution
5. yaml.load() without Loader=SafeLoader - arbitrary code execution

CWE-502: Deserialization of Untrusted Data

Pattern: Scan source files using regex + graph CALLS relationships.
Severity: HIGH (deserialization can lead to RCE)
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


# Dangerous deserialization functions to look for
PICKLE_SINK_FUNCTIONS = {
    # Core pickle
    "load",
    "loads",
    # torch (ML models)
    "torch.load",
    # joblib (sklearn models)
    "joblib.load",
    # numpy with pickle
    "numpy.load",
    "np.load",
    # PyYAML unsafe
    "yaml.load",
    "yaml.unsafe_load",
    "yaml.full_load",
    # dill (extended pickle)
    "dill.load",
    "dill.loads",
    # shelve (uses pickle)
    "shelve.open",
    # marshal (Python bytecode)
    "marshal.load",
    "marshal.loads",
    # cPickle (Python 2 compat)
    "cPickle.load",
    "cPickle.loads",
}

# Module patterns that indicate pickle usage
PICKLE_MODULE_PATTERNS = {
    "pickle",
    "cPickle",
    "_pickle",
    "dill",
    "cloudpickle",
    "joblib",
    "torch",
    "numpy",
    "yaml",
    "marshal",
    "shelve",
}

# ============================================================================
# Regex Patterns for detecting dangerous deserialization
# ============================================================================

# Pattern 1: pickle.load() or pickle.loads() - ALWAYS DANGEROUS
PICKLE_LOAD_PATTERN = re.compile(
    r'\b(?:pickle|cPickle|_pickle|dill|cloudpickle)\.(?:load|loads)\s*\(',
    re.IGNORECASE
)

# Pattern 2: torch.load() without weights_only=True
# Match torch.load(...) and check if weights_only=True is missing
TORCH_LOAD_PATTERN = re.compile(
    r'\btorch\.load\s*\([^)]*\)',
    re.IGNORECASE
)
TORCH_SAFE_PATTERN = re.compile(
    r'weights_only\s*=\s*True',
    re.IGNORECASE
)

# Pattern 3: joblib.load() - inherently uses pickle
JOBLIB_LOAD_PATTERN = re.compile(
    r'\bjoblib\.load\s*\(',
    re.IGNORECASE
)

# Pattern 4: numpy.load() with allow_pickle=True
NUMPY_LOAD_PATTERN = re.compile(
    r'\b(?:numpy|np)\.load\s*\([^)]*\)',
    re.IGNORECASE
)
NUMPY_PICKLE_PATTERN = re.compile(
    r'allow_pickle\s*=\s*True',
    re.IGNORECASE
)

# Pattern 5: yaml.load() without safe Loader
# Safe loaders: SafeLoader, CSafeLoader, BaseLoader, yaml.safe_load
YAML_LOAD_PATTERN = re.compile(
    r'\byaml\.(?:load|unsafe_load|full_load)\s*\([^)]*\)',
    re.IGNORECASE
)
YAML_SAFE_LOADERS = re.compile(
    r'Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe|Base)Loader',
    re.IGNORECASE
)

# Pattern 6: marshal.load() - bytecode execution
MARSHAL_LOAD_PATTERN = re.compile(
    r'\bmarshal\.(?:load|loads)\s*\(',
    re.IGNORECASE
)

# Pattern 7: shelve.open() - uses pickle internally
SHELVE_PATTERN = re.compile(
    r'\bshelve\.open\s*\(',
    re.IGNORECASE
)


class PickleDeserializationDetector(CodeSmellDetector):
    """Detects potential unsafe deserialization vulnerabilities.

    Uses graph queries to find CALLS to deserialization functions, then analyzes
    the call site for safe usage patterns (like weights_only=True for torch.load).

    This is a fast replacement for semgrep pickle/deserialization rules, using the
    code graph for efficient pattern matching.

    CWE-502: Deserialization of Untrusted Data
    OWASP: A8:2017 - Insecure Deserialization
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize pickle deserialization detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with:
                - repository_path: Path to repository root (for source analysis)
                - max_findings: Maximum findings to report (default: 100)
                - exclude_patterns: File patterns to exclude
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)

        # Default exclude patterns
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            "__pycache__/",
            ".git/",
            "node_modules/",
            "venv/",
            ".venv/",
        ]
        self.exclude_patterns = config.get("exclude_patterns", default_exclude)

    def detect(self) -> List[Finding]:
        """Detect potential unsafe deserialization vulnerabilities.

        Returns:
            List of findings for detected deserialization patterns
        """
        findings = []

        # Strategy 1: Query graph for CALLS to deserialization functions
        graph_findings = self._detect_via_graph()
        findings.extend(graph_findings)

        # Strategy 2: Scan source files for dangerous patterns
        # (catches patterns that may not be in the graph)
        if self.repository_path.exists():
            source_findings = self._detect_via_source_scan()
            # Deduplicate by file + line
            seen_locations = {
                (f.affected_files[0] if f.affected_files else "", f.line_start)
                for f in findings
            }
            for f in source_findings:
                loc = (f.affected_files[0] if f.affected_files else "", f.line_start)
                if loc not in seen_locations:
                    findings.append(f)
                    seen_locations.add(loc)

        # Limit findings
        findings = findings[: self.max_findings]

        self.logger.info(
            f"PickleDeserializationDetector found {len(findings)} potential vulnerabilities"
        )
        return findings

    def _detect_via_graph(self) -> List[Finding]:
        """Detect deserialization via graph CALLS relationships.

        Queries the graph for calls to known deserialization functions and
        analyzes the calling context.

        Returns:
            List of findings
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for pickle deserialization detection")
            return self._detect_cached()

        findings = []

        # Query for calls to deserialization-related functions
        repo_filter = self._get_isolation_filter("caller")

        query = f"""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE caller.qualifiedName IS NOT NULL {repo_filter}
          AND (callee.name IN $pickle_sinks
               OR callee.qualifiedName CONTAINS 'pickle.load'
               OR callee.qualifiedName CONTAINS 'torch.load'
               OR callee.qualifiedName CONTAINS 'joblib.load'
               OR callee.qualifiedName CONTAINS 'yaml.load'
               OR callee.qualifiedName CONTAINS 'marshal.load')
        OPTIONAL MATCH (caller)<-[:CONTAINS*]-(f:File)
        RETURN DISTINCT
            caller.qualifiedName AS caller_name,
            caller.name AS caller_simple_name,
            caller.lineStart AS line_start,
            caller.lineEnd AS line_end,
            caller.filePath AS caller_file,
            callee.name AS callee_name,
            callee.qualifiedName AS callee_qname,
            f.filePath AS file_path
        LIMIT 200
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(pickle_sinks=list(PICKLE_SINK_FUNCTIONS)),
            )
        except Exception as e:
            self.logger.error(f"Error executing pickle deserialization graph query: {e}")
            return []

        for row in results:
            caller_name = row.get("caller_name", "")
            file_path = row.get("file_path") or row.get("caller_file") or ""

            # Skip excluded patterns
            if self._should_exclude(file_path):
                continue

            # Check source code for dangerous patterns at this location
            line_start = row.get("line_start")
            if self.repository_path.exists() and file_path:
                full_path = self.repository_path / file_path
                if full_path.exists():
                    vulnerability = self._check_source_at_location(
                        full_path, line_start, row.get("line_end")
                    )
                    if vulnerability:
                        finding = self._create_finding(
                            file_path=file_path,
                            line_start=line_start,
                            line_end=row.get("line_end"),
                            caller_name=caller_name,
                            callee_name=row.get("callee_name", ""),
                            pattern_type=vulnerability["type"],
                            snippet=vulnerability.get("snippet", ""),
                        )
                        findings.append(finding)

        return findings

    def _detect_cached(self) -> List[Finding]:
        """Detect pickle deserialization using QueryCache.

        O(1) lookup from prefetched call data.

        Returns:
            List of findings
        """
        findings = []

        # Find functions that call deserialization functions
        for caller_name, callees in self.query_cache.calls.items():
            caller_data = self.query_cache.get_function(caller_name)
            if not caller_data:
                continue

            file_path = caller_data.file_path
            if self._should_exclude(file_path):
                continue

            # Check if any callee is a deserialization sink
            for callee_name in callees:
                simple_name = callee_name.split(".")[-1]
                is_sink = (
                    simple_name in PICKLE_SINK_FUNCTIONS
                    or "pickle.load" in callee_name
                    or "torch.load" in callee_name
                    or "joblib.load" in callee_name
                    or "yaml.load" in callee_name
                    or "marshal.load" in callee_name
                )
                if is_sink:
                    # Check source code for dangerous patterns
                    if self.repository_path.exists() and file_path:
                        full_path = self.repository_path / file_path
                        if full_path.exists():
                            vulnerability = self._check_source_at_location(
                                full_path, caller_data.line_start, caller_data.line_end
                            )
                            if vulnerability:
                                finding = self._create_finding(
                                    file_path=file_path,
                                    line_start=caller_data.line_start,
                                    line_end=caller_data.line_end,
                                    caller_name=caller_name,
                                    callee_name=callee_name,
                                    pattern_type=vulnerability["type"],
                                    snippet=vulnerability.get("snippet", ""),
                                )
                                findings.append(finding)
                                break  # One finding per caller

        return findings

    def _detect_via_source_scan(self) -> List[Finding]:
        """Scan source files for dangerous deserialization patterns.

        Direct source scanning catches patterns that may not be captured
        in the graph (e.g., inline deserialization in expressions).

        Returns:
            List of findings
        """
        findings = []

        if not self.repository_path.exists():
            return findings

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        for path in self.repository_path.rglob("*.py"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and path not in changed_files:
                continue
            rel_path = str(path.relative_to(self.repository_path))
            if self._should_exclude(rel_path):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                # Skip very large files
                if len(content) > 500_000:
                    continue

                # Check each line for dangerous patterns
                for line_no, line in enumerate(content.split("\n"), start=1):
                    vulnerability = self._check_line_for_patterns(line)
                    if vulnerability:
                        finding = self._create_finding(
                            file_path=rel_path,
                            line_start=line_no,
                            line_end=line_no,
                            caller_name="",
                            callee_name="",
                            pattern_type=vulnerability["type"],
                            snippet=line.strip()[:100],
                        )
                        findings.append(finding)

                        if len(findings) >= self.max_findings:
                            return findings

            except (OSError, UnicodeDecodeError) as e:
                self.logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return findings

    def _check_source_at_location(
        self, file_path: Path, line_start: Optional[int], line_end: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """Check source code at a specific location for deserialization patterns.

        Args:
            file_path: Path to the source file
            line_start: Starting line number
            line_end: Ending line number

        Returns:
            Dictionary with vulnerability info if found, None otherwise
        """
        if not line_start:
            return None

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            # Check lines in the function/call range
            start = max(0, line_start - 1)
            end = min(len(lines), (line_end or line_start) + 5)

            for i in range(start, end):
                line = lines[i]
                vulnerability = self._check_line_for_patterns(line)
                if vulnerability:
                    vulnerability["snippet"] = line.strip()[:100]
                    return vulnerability

        except (OSError, UnicodeDecodeError):
            pass

        return None

    def _check_line_for_patterns(self, line: str) -> Optional[Dict[str, Any]]:
        """Check a line of code for dangerous deserialization patterns.

        Args:
            line: Source code line

        Returns:
            Dictionary with pattern type if found, None otherwise
        """
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            return None

        # Pattern 1: pickle.load() / pickle.loads() - ALWAYS DANGEROUS
        if PICKLE_LOAD_PATTERN.search(line):
            return {"type": "pickle_load"}

        # Pattern 2: torch.load() without weights_only=True
        if TORCH_LOAD_PATTERN.search(line):
            if not TORCH_SAFE_PATTERN.search(line):
                return {"type": "torch_load_unsafe"}

        # Pattern 3: joblib.load() - uses pickle internally
        if JOBLIB_LOAD_PATTERN.search(line):
            return {"type": "joblib_load"}

        # Pattern 4: numpy.load() with allow_pickle=True
        numpy_match = NUMPY_LOAD_PATTERN.search(line)
        if numpy_match:
            if NUMPY_PICKLE_PATTERN.search(line):
                return {"type": "numpy_pickle"}

        # Pattern 5: yaml.load() without SafeLoader
        if YAML_LOAD_PATTERN.search(line):
            # Check if using safe loader
            if not YAML_SAFE_LOADERS.search(line):
                # Also check for yaml.safe_load which is fine
                if "safe_load" not in line.lower():
                    return {"type": "yaml_unsafe"}

        # Pattern 6: marshal.load() - bytecode execution
        if MARSHAL_LOAD_PATTERN.search(line):
            return {"type": "marshal_load"}

        # Pattern 7: shelve.open() - uses pickle
        if SHELVE_PATTERN.search(line):
            return {"type": "shelve_open"}

        return None

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded.

        Args:
            path: Relative path to check

        Returns:
            True if path should be excluded
        """
        import fnmatch

        for pattern in self.exclude_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in path.split("/"):
                    return True
            elif "*" in pattern:
                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(
                    Path(path).name, pattern
                ):
                    return True
            elif pattern in path:
                return True
        return False

    def _create_finding(
        self,
        file_path: str,
        line_start: Optional[int],
        line_end: Optional[int],
        caller_name: str,
        callee_name: str,
        pattern_type: str,
        snippet: str,
    ) -> Finding:
        """Create a finding for detected deserialization vulnerability.

        Args:
            file_path: Path to the affected file
            line_start: Starting line number
            line_end: Ending line number
            caller_name: Qualified name of the calling function
            callee_name: Name of the deserialization function being called
            pattern_type: Type of dangerous pattern detected
            snippet: Code snippet showing the vulnerability

        Returns:
            Finding object
        """
        pattern_descriptions = {
            "pickle_load": "pickle.load()/loads() - arbitrary code execution on untrusted data",
            "torch_load_unsafe": "torch.load() without weights_only=True - can execute arbitrary code",
            "joblib_load": "joblib.load() - uses pickle internally, arbitrary code execution",
            "numpy_pickle": "numpy.load() with allow_pickle=True - enables pickle execution",
            "yaml_unsafe": "yaml.load() without SafeLoader - arbitrary code execution",
            "marshal_load": "marshal.load() - Python bytecode execution",
            "shelve_open": "shelve.open() - uses pickle internally",
        }

        pattern_desc = pattern_descriptions.get(pattern_type, "unsafe deserialization")

        title = "Unsafe Deserialization (CWE-502)"
        if caller_name:
            func_name = caller_name.split(".")[-1]
            title = f"Unsafe Deserialization in {func_name}"

        description = f"""**Unsafe Deserialization Vulnerability**

**Pattern detected**: {pattern_desc}

**Location**: {file_path}:{line_start or '?'}

**Code snippet**:
```python
{snippet}
```

Deserializing untrusted data can allow attackers to execute arbitrary code.
Pickle, joblib, torch.load, yaml.load, and similar functions execute code
embedded in the serialized data. An attacker who controls the input can
achieve Remote Code Execution (RCE).

This vulnerability is classified as:
- **CWE-502**: Deserialization of Untrusted Data
- **OWASP A8:2017**: Insecure Deserialization
"""

        if caller_name:
            description += f"\n**Containing function**: `{caller_name}`\n"
        if callee_name:
            description += f"**Deserialization function**: `{callee_name}`\n"

        recommendation = self._get_recommendation(pattern_type)

        finding_id = f"pickle_deser_{file_path}_{line_start or 0}"

        finding = Finding(
            id=finding_id,
            detector="PickleDeserializationDetector",
            severity=Severity.HIGH,
            title=title,
            description=description,
            affected_nodes=[caller_name] if caller_name else [],
            affected_files=[file_path] if file_path else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Medium (2-8 hours)",
            graph_context={
                "vulnerability": "unsafe_deserialization",
                "cwe": "CWE-502",
                "pattern_type": pattern_type,
                "caller": caller_name,
                "callee": callee_name,
                "snippet": snippet,
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="PickleDeserializationDetector",
                confidence=0.90,  # High confidence for pattern matches
                evidence=["pattern_match", pattern_type, "deserialization_sink"],
                tags=["security", "deserialization", "cwe-502", "rce", "high"],
            )
        )

        # Flag entity in graph for cross-detector collaboration
        if self.enricher and caller_name:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=caller_name,
                    detector="PickleDeserializationDetector",
                    severity=Severity.HIGH.value,
                    issues=["unsafe_deserialization"],
                    confidence=0.90,
                    metadata={
                        "vulnerability": "unsafe_deserialization",
                        "cwe": "CWE-502",
                        "pattern_type": pattern_type,
                        "file": file_path,
                    },
                )
            except Exception as e:
                self.logger.warning(f"Failed to flag entity {caller_name}: {e}")

        return finding

    def _get_recommendation(self, pattern_type: str) -> str:
        """Get specific recommendation based on the pattern type.

        Args:
            pattern_type: Type of dangerous pattern detected

        Returns:
            Recommendation string with code examples
        """
        recommendations = {
            "pickle_load": """**Recommended fixes for pickle.load()**:

1. **Avoid pickle for untrusted data** (preferred):
   ```python
   # Instead of pickle, use JSON for data exchange:
   import json
   data = json.loads(untrusted_input)
   ```

2. **Use safer alternatives**:
   - JSON for structured data
   - Protocol Buffers for binary data
   - msgpack with strict mode
   - YAML with SafeLoader

3. **If pickle is required**, validate the source:
   ```python
   # Only load from trusted, signed sources
   if verify_signature(file_path, trusted_key):
       data = pickle.load(open(file_path, 'rb'))
   ```
""",
            "torch_load_unsafe": """**Recommended fixes for torch.load()**:

1. **Use weights_only=True** (preferred):
   ```python
   # Safe: only loads tensor weights, no arbitrary code
   model = torch.load('model.pt', weights_only=True)
   ```

2. **Use safetensors format**:
   ```python
   from safetensors.torch import load_file
   state_dict = load_file('model.safetensors')
   model.load_state_dict(state_dict)
   ```

3. **Validate model source** before loading.
""",
            "joblib_load": """**Recommended fixes for joblib.load()**:

1. **Verify the source** - only load from trusted sources:
   ```python
   # Verify checksum before loading
   if verify_checksum(model_path, expected_hash):
       model = joblib.load(model_path)
   ```

2. **Use ONNX format** for ML models (safer):
   ```python
   import onnxruntime as ort
   session = ort.InferenceSession('model.onnx')
   ```

3. **Consider skops** for scikit-learn:
   ```python
   from skops.io import load
   model = load('model.skops', trusted=['sklearn.linear_model.LogisticRegression'])
   ```
""",
            "numpy_pickle": """**Recommended fixes for numpy.load() with allow_pickle**:

1. **Avoid allow_pickle=True** if possible:
   ```python
   # Load only array data (no pickle)
   data = np.load('data.npy', allow_pickle=False)
   ```

2. **Use .npz files without pickle**:
   ```python
   # Save without object arrays
   np.savez('data.npz', array1=arr1, array2=arr2)
   ```

3. **Verify source** before enabling pickle:
   ```python
   if is_trusted_source(file_path):
       data = np.load(file_path, allow_pickle=True)
   ```
""",
            "yaml_unsafe": """**Recommended fixes for yaml.load()**:

1. **Use SafeLoader** (preferred):
   ```python
   import yaml
   # Safe: only loads basic Python types
   data = yaml.load(content, Loader=yaml.SafeLoader)
   
   # Or use the safe_load shortcut:
   data = yaml.safe_load(content)
   ```

2. **Use FullLoader with caution** (limited code execution):
   ```python
   # Less safe but more capable:
   data = yaml.load(content, Loader=yaml.FullLoader)
   ```

3. **Never use yaml.unsafe_load()** on untrusted data.
""",
            "marshal_load": """**Recommended fixes for marshal.load()**:

1. **Avoid marshal for data exchange** - it's for Python bytecode:
   ```python
   # Use JSON or pickle for data serialization
   import json
   data = json.loads(content)
   ```

2. **Validate source strictly** if marshal is required:
   ```python
   # Only load bytecode from verified, signed sources
   if verify_code_signature(path):
       code = marshal.load(open(path, 'rb'))
   ```
""",
            "shelve_open": """**Recommended fixes for shelve.open()**:

1. **Use safer alternatives** for key-value storage:
   ```python
   # Use SQLite for persistent storage:
   import sqlite3
   conn = sqlite3.connect('data.db')
   
   # Or use JSON files:
   import json
   with open('data.json') as f:
       data = json.load(f)
   ```

2. **Validate source** before opening shelve databases from external sources.
""",
        }

        return recommendations.get(
            pattern_type,
            """**General recommendations**:

1. Never deserialize untrusted data with pickle or similar libraries
2. Use JSON, Protocol Buffers, or other safe formats for data exchange
3. Verify the source and integrity of any serialized data before loading
4. Consider using signed/encrypted containers for trusted data
""",
        )

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding.

        Unsafe deserialization is always HIGH severity (potential RCE).

        Args:
            finding: Finding to assess

        Returns:
            Severity level (always HIGH for deserialization vulnerabilities)
        """
        return Severity.HIGH
