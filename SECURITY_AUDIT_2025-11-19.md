# Repotoire Security Audit Report
**Date:** 2025-11-19
**Auditor:** Claude (Contrarian Security Review)
**Scope:** Cypher injection, path traversal, secrets handling

## Executive Summary

**CRITICAL VULNERABILITIES FOUND:** Multiple Cypher injection vulnerabilities across detector and graph modules.

**Overall Security Grade:** ⚠️ **D (Major Issues)**

- ✅ **Path Traversal Protection:** GOOD - Proper validation in place
- ✅ **Secrets Handling:** GOOD - No hardcoded secrets in production code
- ❌ **Cypher Injection:** CRITICAL - Widespread use of f-strings instead of parameterized queries
- ✅ **Input Validation:** GOOD - Comprehensive validation framework

---

## 🔴 CRITICAL: Cypher Injection Vulnerabilities

### Summary
Multiple files use f-string interpolation to build Cypher queries instead of using the available parameterized query functionality. This creates **critical injection vulnerabilities**.

### Affected Files

#### 1. `repotoire/detectors/graph_algorithms.py`

**Vulnerable Code:**

```python
# Line 53-60: Injection of projection_name
drop_query = f"""
CALL gds.graph.exists('{projection_name}')
YIELD exists
WHERE exists = true
CALL gds.graph.drop('{projection_name}')
YIELD graphName
RETURN graphName
"""

# Line 67-75: Injection of projection_name
create_query = f"""
CALL gds.graph.project(
    '{projection_name}',
    'Function',
    'CALLS'
)
YIELD graphName, nodeCount, relationshipCount
RETURN graphName, nodeCount, relationshipCount
"""

# Line 110-116: Injection of projection_name and write_property
query = f"""
CALL gds.betweenness.write('{projection_name}', {{
    writeProperty: '{write_property}'
}})
YIELD nodePropertiesWritten, computeMillis
RETURN nodePropertiesWritten, computeMillis
"""

# Line 146-159: Injection of threshold and limit
query = f"""
MATCH (f:Function)
WHERE f.betweenness_score IS NOT NULL
  AND f.betweenness_score > {threshold}
RETURN
    f.qualifiedName as qualified_name,
    f.betweenness_score as betweenness,
    f.complexity as complexity,
    f.loc as loc,
    f.filePath as file_path,
    f.line_start as line_number
ORDER BY f.betweenness_score DESC
LIMIT {limit}
"""

# Line 191-195: Injection of projection_name
query = f"""
CALL gds.graph.drop('{projection_name}')
YIELD graphName
RETURN graphName
"""
```

**Attack Vector:**
If an attacker can control `projection_name`, `write_property`, `threshold`, or `limit`, they can inject arbitrary Cypher code.

**Example Exploit:**
```python
projection_name = "test') YIELD exists MATCH (n) DETACH DELETE n //"
# This would:
# 1. Close the quote early
# 2. Execute MATCH (n) DETACH DELETE n (delete all nodes!)
# 3. Comment out the rest with //
```

**Impact:** Complete database compromise, data deletion, data exfiltration

---

#### 2. `repotoire/detectors/god_class.py`

**Vulnerable Code:**

```python
# Line 58-68: Injection of self.medium_method_count, self.medium_complexity, self.medium_loc
query = f"""
MATCH (file:File)-[:CONTAINS]->(c:Class)
WITH c, file
MATCH (file)-[:CONTAINS]->(m:Function)
WHERE m.qualifiedName STARTS WITH c.qualifiedName + '.'
WITH c, file,
     collect(m) AS methods,
     sum(m.complexity) AS total_complexity,
     COALESCE(c.lineEnd, 0) - COALESCE(c.lineStart, 0) AS loc
WITH c, file, methods, size(methods) AS method_count, total_complexity, loc
WHERE method_count >= {self.medium_method_count} OR total_complexity >= {self.medium_complexity} OR loc >= {self.medium_loc}
...
"""
```

**Risk Level:** MEDIUM (values are class attributes, not directly user-controlled, but poor practice)

---

#### 3. `repotoire/detectors/temporal_metrics.py`

**Vulnerable Code:**

```python
# Line 62+: Similar f-string injection pattern (needs verification)
query = f"""..."""
```

**Status:** Requires detailed review

---

#### 4. `repotoire/graph/client.py`

**Vulnerable Code:**

```python
# Line 314-319: Injection of node_type into CREATE query
query = f"""
UNWIND $entities AS entity
CREATE (n:{node_type})
SET n = entity
RETURN elementId(n) as id, entity.qualifiedName as qualifiedName
"""
```

**Risk Level:** HIGH - `node_type` comes from `NodeType` enum but direct interpolation is unsafe

---

### Root Cause

**The Neo4jClient ALREADY supports parameterized queries:**

```python
def execute_query(
    self,
    query: str,
    parameters: Optional[Dict] = None,  # ✅ Parameterization supported!
    timeout: Optional[float] = None,
) -> List[Dict]:
    ...
    result: Result = session.run(query, parameters or {}, timeout=timeout_ms)
```

But detectors are NOT using it! They're using f-strings instead.

---

### Recommended Fix

**BEFORE (Vulnerable):**
```python
query = f"""
MATCH (f:Function)
WHERE f.betweenness_score > {threshold}
LIMIT {limit}
"""
result = self.client.execute_query(query)
```

**AFTER (Secure):**
```python
query = """
MATCH (f:Function)
WHERE f.betweenness_score > $threshold
LIMIT $limit
"""
result = self.client.execute_query(query, parameters={
    "threshold": threshold,
    "limit": limit
})
```

---

## ✅ PASS: Path Traversal Protection

### Status: **SECURE**

The ingestion pipeline has robust path traversal protection:

**Security Measures:**

1. **Path Resolution** (`repotoire/pipeline/ingestion.py:62`):
   ```python
   self.repo_path = repo_path_obj.resolve()
   ```

2. **Boundary Validation** (`repotoire/pipeline/ingestion.py:127-138`):
   ```python
   def _validate_file_path(self, file_path: Path) -> None:
       """Validate file path is within repository boundary."""
       resolved_file = file_path.resolve()

       try:
           resolved_file.relative_to(self.repo_path)
       except ValueError:
           raise SecurityError(
               f"Security violation: File is outside repository boundary\n"
               f"File: {file_path}\n"
               f"Repository: {self.repo_path}\n"
               f"This could be a path traversal attack."
           )
   ```

3. **Enforcement Points:**
   - Called at `ingestion.py:189` during file skipping check
   - Called at `ingestion.py:262` during parse and extract

4. **Symlink Protection** (`ingestion.py:54-59`):
   ```python
   if repo_path_obj.is_symlink():
       raise SecurityError(
           f"Repository path cannot be a symlink: {repo_path}\n"
           f"This could be a security risk (symlink attack)."
       )
   ```

**Attack Resistance:**
- ✅ Prevents `../../etc/passwd` style attacks
- ✅ Resolves symlinks before validation
- ✅ Enforces repository boundaries
- ✅ Stores relative paths (prevents system path exposure)

---

## ✅ PASS: Secrets Handling

### Status: **SECURE**

No hardcoded secrets found in production code.

**Findings:**

1. **Test Files Only:**
   - `test_ingestion.py:31` has `neo4j_password = "repotoire-password"` (test fixture - acceptable)
   - `tests/test_secrets_scanner.py` has example secrets for testing the scanner (expected)

2. **Credential Handling:**
   - Credentials passed as CLI parameters (good)
   - Environment variable support (`${NEO4J_PASSWORD}`)
   - No secrets in version control

3. **Secrets Scanner:**
   - Project has `SecretsScanner` to detect secrets in analyzed codebases
   - Tests verify detection of API keys, JWT tokens, AWS keys

**Recommendations:**
- ✅ Continue using environment variables
- ✅ Document `.env` file usage in setup guide
- ✅ Add `.env` to `.gitignore` (verify present)

---

## 🟡 MODERATE: Input Validation

### Status: **MOSTLY SECURE** with minor concerns

**Strengths:**

1. **Comprehensive Validation Framework** (`repotoire/validation.py`):
   - Repository path validation
   - Neo4j URI validation
   - Credential validation
   - File size limits
   - Batch size limits (10-10,000)
   - Retry configuration validation

2. **Helpful Error Messages:**
   ```python
   class ValidationError(Exception):
       def __init__(self, message: str, suggestion: Optional[str] = None):
           self.message = message
           self.suggestion = suggestion
   ```

3. **Early Validation:**
   - All validation happens before expensive operations
   - Fail-fast approach

**Potential Weaknesses:**

1. **No validation of `projection_name` format:**
   - Should enforce alphanumeric + hyphens only
   - Prevent Cypher injection

2. **No validation of `write_property` format:**
   - Should enforce valid property name format

---

## Detailed Recommendations

### Priority 1: CRITICAL - Fix Cypher Injection

**Estimated Effort:** 4-6 hours

**Files to Fix:**
1. `repotoire/detectors/graph_algorithms.py` (5 injection points)
2. `repotoire/graph/client.py` (1 injection point)
3. `repotoire/detectors/god_class.py` (1 injection point)
4. `repotoire/detectors/temporal_metrics.py` (verify and fix)
5. `repotoire/detectors/engine.py` (verify - may have queries)

**Implementation Steps:**

1. **Add validation functions:**
   ```python
   # repotoire/validation.py
   def validate_identifier(name: str, context: str = "identifier") -> str:
       """Validate identifier is alphanumeric + underscores/hyphens."""
       if not re.match(r'^[a-zA-Z0-9_-]+$', name):
           raise ValidationError(
               f"Invalid {context}: {name}",
               f"{context} must be alphanumeric with underscores/hyphens only"
           )
       return name
   ```

2. **Convert all f-string queries to parameterized:**
   ```python
   # Pattern to find:
   query = f"""...{variable}..."""

   # Replace with:
   query = """...$paramName..."""
   result = self.client.execute_query(query, parameters={"paramName": variable})
   ```

3. **For GDS procedure calls** (can't parameterize procedure names):
   ```python
   # Validate input first
   projection_name = validate_identifier(projection_name, "projection name")

   # Then use in query (now safe because validated)
   query = f"CALL gds.graph.drop('{projection_name}')"
   ```

4. **Add security tests:**
   ```python
   def test_cypher_injection_protection():
       """Test that malicious input is rejected."""
       malicious_input = "test') MATCH (n) DELETE n //"
       with pytest.raises(ValidationError):
           validate_identifier(malicious_input)
   ```

### Priority 2: HIGH - Add Identifier Validation

**Estimated Effort:** 2-3 hours

Add validation for all string inputs used in queries:
- Projection names
- Property names
- Node type strings
- Label strings

### Priority 3: MEDIUM - Security Hardening

**Estimated Effort:** 2-4 hours

1. **Add security documentation** - Document Cypher injection risks
2. **Security testing** - Add penetration tests for injection
3. **Code review checklist** - Add "never use f-strings in queries" rule
4. **Linting rule** - Add ruff/flake8 rule to catch f-string queries

---

## Testing Recommendations

### Security Test Suite

```python
# tests/security/test_cypher_injection.py

def test_projection_name_injection():
    """Test that malicious projection names are rejected."""
    malicious_names = [
        "test') MATCH (n) DELETE n //",
        "test' OR '1'='1",
        "test'; DROP TABLE users; --",
    ]

    algo = GraphAlgorithms(mock_client)
    for name in malicious_names:
        with pytest.raises(ValidationError):
            algo.create_call_graph_projection(projection_name=name)

def test_threshold_injection():
    """Test that malicious threshold values are rejected."""
    algo = GraphAlgorithms(mock_client)

    # These should work
    algo.get_high_betweenness_functions(threshold=0.5, limit=100)

    # Type enforcement should prevent string injection
    with pytest.raises(TypeError):
        algo.get_high_betweenness_functions(threshold="0.5 OR 1=1")
```

---

## Compliance & Best Practices

### OWASP Top 10 (2021)

| Risk | Status | Notes |
|------|--------|-------|
| A01: Broken Access Control | ⚠️ | Path traversal protected, but Cypher injection allows privilege escalation |
| A02: Cryptographic Failures | ✅ | No sensitive data stored unencrypted |
| A03: Injection | ❌ | **CRITICAL: Cypher injection vulnerabilities** |
| A04: Insecure Design | 🟡 | Good validation framework, but query building needs redesign |
| A05: Security Misconfiguration | ✅ | Good defaults, secrets properly handled |
| A06: Vulnerable Components | 🟡 | Dependencies should be audited (use `safety check`) |
| A07: Identity/Auth Failures | ✅ | Relies on Neo4j auth |
| A08: Data Integrity Failures | ✅ | Transactions used properly |
| A09: Logging Failures | ✅ | Comprehensive logging in place |
| A10: SSRF | N/A | No server-side requests to user-controlled URLs |

---

## References

- [Neo4j Cypher Injection Prevention](https://neo4j.com/developer/kb/protecting-against-cypher-injection/)
- [OWASP Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_Cheat_Sheet.html)
- [CWE-89: SQL Injection](https://cwe.mitre.org/data/definitions/89.html) (analogous to Cypher injection)

---

## Audit Metadata

**Tools Used:**
- Manual code review
- Pattern matching (grep)
- Security-focused analysis

**Limitations:**
- Dynamic code paths not fully analyzed
- Third-party dependencies not audited
- Runtime behavior not tested (no actual injection attempts performed)

**Follow-up:**
- Run `bandit` security scanner
- Run `safety check` for dependency vulnerabilities
- Perform penetration testing with actual exploit attempts
- Add security scanning to CI/CD pipeline
