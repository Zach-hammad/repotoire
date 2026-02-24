# AI Code Quality Detector Validation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate and fix the 3 working AI code quality detectors so they produce accurate, meaningful findings on both human-written (Django) and AI-heavy (LangChain) codebases.

**Architecture:** Run detectors against Django (baseline — expect low findings) and LangChain (expect more findings), audit every result, fix FPs and detection gaps. Each detector gets its own audit+fix task.

**Tech Stack:** Rust, cargo test, tree-sitter graph, MockFileProvider for unit tests

---

## Current State

All 3 AI detectors produce **0 findings on Django** (1877 functions, 514K LOC). Root causes:

| Detector | Issue | Root Cause |
|----------|-------|------------|
| AIComplexitySpikeDetector | 0 findings | Minimum complexity 20 + z-score >2.0 + path exclusions may be too aggressive |
| AINamingPatternDetector | 0 findings | Only checks **function names** (not variables); requires name == generic word + LOC>10 |
| AIMissingTestsDetector | 0 findings | Django is well-tested; may also be correct, needs LangChain validation |

---

### Task 1: Setup — Clone LangChain and Run Baseline Analysis

**Files:** None (setup only)

**Step 1: Clone LangChain**

```bash
git clone --depth 1 https://github.com/langchain-ai/langchain.git /tmp/langchain-repo
```

**Step 2: Run analysis on LangChain**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/langchain-repo --format json -o /tmp/langchain-baseline.json
```

**Step 3: Extract AI detector findings from both repos**

```bash
python3 -c "
import json
for repo, path in [('Django', '/tmp/django-lowact.json'), ('LangChain', '/tmp/langchain-baseline.json')]:
    with open(path) as f:
        data = json.load(f)
    print(f'\n=== {repo} ===')
    print(f'Functions: {data.get(\"total_functions\", \"N/A\")}')
    ai_dets = ['AIComplexitySpikeDetector', 'AINamingPatternDetector', 'AIMissingTestsDetector']
    for det in ai_dets:
        findings = [f for f in data['findings'] if f['detector'] == det]
        print(f'{det}: {len(findings)} findings')
        for f in findings[:3]:
            print(f'  - {f[\"title\"]}')
"
```

Record the baseline numbers. Expected: 0 findings from all 3 on both repos (since detectors are broken/too conservative).

**Step 4: Commit setup note** (optional — skip if no code changes)

---

### Task 2: Fix AIComplexitySpikeDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_complexity_spike.rs:362-498` (detect method)

**Context:** The detector uses `graph.get_functions()` and filters for complexity outliers via z-score. It produces 0 findings because:
1. Minimum complexity threshold is 20 (too high — many "complex" functions are 10-15)
2. Path exclusions are very aggressive (detectors/, parsers/, runtime/, vendor/, etc.)
3. The z-score threshold of 2.0 is reasonable but combined with min=20 it's too strict

**Step 1: Write failing tests**

Add to the `#[cfg(test)] mod tests` block:

```rust
#[test]
fn test_detects_complexity_outlier() {
    use crate::graph::store::GraphStore;
    use crate::graph::store_models::CodeNode;
    use crate::graph::NodeKind;

    let store = GraphStore::in_memory();
    // Add 10 normal functions (complexity 3-5) and 1 outlier (complexity 25)
    for i in 0..10 {
        store.add_node(CodeNode::new_function(
            &format!("func_{}", i),
            &format!("module.func_{}", i),
            "src/module.py",
            i * 10 + 1,
            i * 10 + 5,
            3 + (i % 3) as i64, // complexity 3-5
        ));
    }
    store.add_node(CodeNode::new_function(
        "complex_handler",
        "module.complex_handler",
        "src/handlers.py",
        1,
        50,
        25, // outlier
    ));

    let detector = AIComplexitySpikeDetector::new();
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should detect complexity outlier (25 vs baseline ~4)"
    );
    assert!(findings[0].title.contains("complex_handler"));
}

#[test]
fn test_no_finding_for_normal_complexity() {
    use crate::graph::store::GraphStore;
    use crate::graph::store_models::CodeNode;
    use crate::graph::NodeKind;

    let store = GraphStore::in_memory();
    // All functions have similar complexity (3-7)
    for i in 0..20 {
        store.add_node(CodeNode::new_function(
            &format!("func_{}", i),
            &format!("module.func_{}", i),
            "src/module.py",
            i * 10 + 1,
            i * 10 + 8,
            3 + (i % 5) as i64, // complexity 3-7
        ));
    }

    let detector = AIComplexitySpikeDetector::new();
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag when all functions have similar complexity. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Important:** The test depends on `CodeNode::new_function()` — check if this constructor exists. If not, use the existing test pattern of constructing CodeNode manually with `CodeNode { kind: NodeKind::Function, name: ..., file_path: ..., line_start: ..., line_end: ..., properties: HashMap::from([("complexity", ...)]), ... }`. Adapt the test to match the actual CodeNode API.

**Step 2: Run tests to verify they fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_complexity_spike 2>&1 | tail -20
```

Expected: New tests fail (outlier test fails because min_complexity=20 filters it out, or because the test setup doesn't match the API).

**Step 3: Fix the detect() method**

In `detect()` at line 362-498, make these changes:

1. **Lower minimum complexity from 20 to 10** (line ~463):
```rust
let min_complexity = if is_ast_code { 25 } else { 10 };
```

2. **Add a minimum function count check** — if fewer than 5 functions, skip (can't compute meaningful statistics):
```rust
if complexities.len() < 5 {
    return Ok(vec![]);
}
```

3. **Ensure z-score calculation handles small stddev** — if stddev < 1.0, use 1.0:
```rust
let std_dev = variance.sqrt().max(1.0);
```
(This already exists at line 379, verify it's correct.)

**Step 4: Run tests to verify they pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_complexity_spike 2>&1 | tail -20
```

Expected: ALL tests pass (3 existing + 2 new = 5).

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/ai_complexity_spike.rs && git commit -m "fix: AIComplexitySpikeDetector lower minimum complexity threshold from 20 to 10"
```

---

### Task 3: Fix AINamingPatternDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_naming_pattern.rs:444-483` (detect method)

**Context:** The current `detect()` method (lines 444-483) is a stub — it only checks **function names** against a tiny list of 11 generic words. The class has sophisticated infrastructure for analyzing variable names within functions (`is_generic_name()`, `analyze_identifiers()`, `FunctionNamingAnalysis`) that is completely unused.

The fix: rewrite `detect()` to use `FileProvider` to read function bodies, extract identifiers, and use the existing `is_generic_name()` infrastructure to compute generic naming ratios.

**Step 1: Write failing tests**

Add to the `#[cfg(test)] mod tests` block:

```rust
#[test]
fn test_detects_generic_naming_in_function_body() {
    use crate::graph::store::GraphStore;

    let store = GraphStore::in_memory();
    let detector = AINamingPatternDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("generic.py", "def process_users(users):\n    result = []\n    for item in users:\n        data = item.get('name')\n        temp = data.strip()\n        value = temp.lower()\n        obj = {'name': value}\n        result.append(obj)\n    output = sorted(result)\n    return output\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should flag function with high generic naming ratio (result, item, data, temp, value, obj, output)"
    );
}

#[test]
fn test_no_finding_for_domain_specific_naming() {
    use crate::graph::store::GraphStore;

    let store = GraphStore::in_memory();
    let detector = AINamingPatternDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("users.py", "def create_user(username, email, password):\n    hashed_password = hash_password(password)\n    user = User(username=username, email=email)\n    user.set_password(hashed_password)\n    user.save()\n    confirmation_email = build_welcome_email(user)\n    send_email(confirmation_email)\n    return user\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag function with domain-specific names. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_naming_pattern 2>&1 | tail -20
```

Expected: `test_detects_generic_naming_in_function_body` fails (current detect() doesn't analyze function bodies).

**Step 3: Rewrite detect() to analyze function bodies**

Replace the current `detect()` method (lines 444-483) with a file-based implementation:

```rust
fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
    let mut findings = Vec::new();

    // Regex to extract Python function definitions
    static FUNC_DEF: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let func_re = FUNC_DEF.get_or_init(|| {
        Regex::new(r"^(\s*)def\s+(\w+)\s*\(").expect("valid regex")
    });

    // Regex to extract identifiers from assignments (left side of =)
    static ASSIGNMENT: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let assign_re = ASSIGNMENT.get_or_init(|| {
        Regex::new(r"^\s+(\w+)\s*=\s").expect("valid regex")
    });

    // Regex to extract for-loop variables
    static FOR_VAR: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let for_re = FOR_VAR.get_or_init(|| {
        Regex::new(r"^\s+for\s+(\w+)\s+in\s").expect("valid regex")
    });

    for path in files.files_with_extensions(&["py"]) {
        if findings.len() >= self.max_findings {
            break;
        }

        // Skip test files
        if crate::detectors::base::is_test_path(&path.to_string_lossy()) {
            continue;
        }

        if let Some(content) = files.content(path) {
            let lines: Vec<&str> = content.lines().collect();
            let mut i = 0;

            while i < lines.len() {
                if let Some(caps) = func_re.captures(lines[i]) {
                    let func_indent = caps.get(1).map(|m| m.as_str().len()).unwrap_or(0);
                    let func_name = caps.get(2).unwrap().as_str().to_string();
                    let func_start = i;

                    // Skip private/dunder functions
                    if func_name.starts_with('_') {
                        i += 1;
                        continue;
                    }

                    // Collect identifiers from function body
                    let mut identifiers = Vec::new();
                    let mut is_loop_var = std::collections::HashSet::new();
                    let mut j = i + 1;

                    while j < lines.len() {
                        let line = lines[j];
                        let trimmed = line.trim();

                        // End of function: non-empty line at same or lesser indent
                        if !trimmed.is_empty() {
                            let line_indent = line.len() - line.trim_start().len();
                            if line_indent <= func_indent && !trimmed.starts_with('#') {
                                break;
                            }
                        }

                        // Extract assignment targets
                        if let Some(caps) = assign_re.captures(line) {
                            let name = caps.get(1).unwrap().as_str().to_string();
                            identifiers.push(name);
                        }

                        // Extract for-loop variables
                        if let Some(caps) = for_re.captures(line) {
                            let name = caps.get(1).unwrap().as_str().to_string();
                            is_loop_var.insert(name.clone());
                            identifiers.push(name);
                        }

                        j += 1;
                    }

                    let func_loc = j - func_start;

                    // Need enough identifiers to compute meaningful ratio
                    if identifiers.len() >= self.min_identifiers && func_loc >= 8 {
                        let generic_count = identifiers
                            .iter()
                            .filter(|name| {
                                let is_loop = is_loop_var.contains(name.as_str());
                                self.is_generic_name(name, is_loop)
                            })
                            .count();

                        let ratio = generic_count as f64 / identifiers.len() as f64;

                        if ratio >= self.generic_ratio_threshold {
                            let generic_names: Vec<String> = identifiers
                                .iter()
                                .filter(|name| {
                                    let is_loop = is_loop_var.contains(name.as_str());
                                    self.is_generic_name(name, is_loop)
                                })
                                .cloned()
                                .collect();

                            let analysis = FunctionNamingAnalysis {
                                file_path: path.to_string_lossy().to_string(),
                                function_name: func_name.clone(),
                                qualified_name: format!("{}:{}", path.to_string_lossy(), func_name),
                                total_identifiers: identifiers.len(),
                                generic_count,
                                generic_ratio: ratio,
                                generic_identifiers: generic_names,
                                line_number: (func_start + 1) as u32,
                            };

                            findings.push(self.create_finding(&analysis));
                        }
                    }

                    i = j;
                } else {
                    i += 1;
                }
            }
        }
    }

    info!("AINamingPatternDetector found {} findings", findings.len());
    Ok(findings)
}
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_naming_pattern 2>&1 | tail -20
```

Expected: ALL tests pass (4 existing + 2 new = 6). Adjust as needed.

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/ai_naming_pattern.rs && git commit -m "feat: AINamingPatternDetector analyzes variable names in function bodies (not just function names)"
```

---

### Task 4: Fix AIMissingTestsDetector

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_missing_tests.rs:341-416` (detect method)

**Context:** The detector checks for `test_FUNCNAME` in the test function set. It produces 0 findings on Django (well-tested codebase). This might be correct for Django but needs LangChain validation. Additionally, the current thresholds (complexity >= 5 AND loc >= 20) are reasonable but the minimum LOC of 20 is high — many important functions are 10-19 lines.

Fixes needed:
1. Lower LOC threshold from 20 to 15
2. Add file-based test detection (check if ANY test file mentions the function name) for better coverage detection
3. Skip framework boilerplate (Django management commands, migrations, admin configs, etc.)

**Step 1: Write failing tests**

Add to the `#[cfg(test)] mod tests` block:

```rust
#[test]
fn test_detects_untested_complex_function() {
    use crate::graph::store::GraphStore;

    let store = GraphStore::in_memory();
    // Add a complex function with no corresponding test
    store.add_function_node(
        "calculate_risk_score",
        "analytics.calculate_risk_score",
        "src/analytics.py",
        1, 30,
        8, // complexity
    );

    let detector = AIMissingTestsDetector::new();
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should flag complex function without tests"
    );
}

#[test]
fn test_no_finding_for_tested_function() {
    use crate::graph::store::GraphStore;

    let store = GraphStore::in_memory();
    // Add the function and its test
    store.add_function_node(
        "calculate_risk_score",
        "analytics.calculate_risk_score",
        "src/analytics.py",
        1, 30,
        8,
    );
    store.add_function_node(
        "test_calculate_risk_score",
        "tests.test_analytics.test_calculate_risk_score",
        "tests/test_analytics.py",
        1, 15,
        2,
    );

    let detector = AIMissingTestsDetector::new();
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag function that has a test. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_simple_function() {
    use crate::graph::store::GraphStore;

    let store = GraphStore::in_memory();
    // Simple function (low complexity, low LOC)
    store.add_function_node(
        "get_name",
        "models.get_name",
        "src/models.py",
        1, 5,
        1,
    );

    let detector = AIMissingTestsDetector::new();
    let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    let findings = detector.detect(&store, &empty_files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag simple functions. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Important:** The test depends on `store.add_function_node()` — this may not exist. Check the GraphStore API. If it doesn't exist, construct the test using the existing graph API or use a mock. Adapt the tests to match the actual API.

**Step 2: Run tests to verify they fail**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_missing_tests 2>&1 | tail -20
```

Expected: First test should fail (if the function doesn't meet LOC >= 20 threshold). May need to adjust test setup.

**Step 3: Implement fixes**

In `detect()` at line 341-416:

1. **Lower LOC threshold from 20 to 15** (line ~374):
```rust
if complexity < 5 && loc < 15 {
    continue;
}
```

2. **Skip framework boilerplate paths** — add before the private function check:
```rust
// Skip framework boilerplate (migrations, admin, management commands, configs)
let path_lower = func.file_path.to_lowercase();
if path_lower.contains("/migrations/")
    || path_lower.contains("/admin.py")
    || path_lower.contains("/apps.py")
    || path_lower.contains("/manage.py")
    || path_lower.contains("/settings")
    || path_lower.contains("/conftest")
    || path_lower.contains("/setup.py")
    || path_lower.contains("/conf.py")
{
    continue;
}
```

3. **Apply same fixes to `detect_with_context()`** (lines 422-532) — mirror the same threshold and path changes.

**Step 4: Run tests to verify they pass**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test --lib ai_missing_tests 2>&1 | tail -20
```

Expected: ALL tests pass (2 existing + 3 new = 5).

**Step 5: Commit**

```bash
cd /home/zhammad/personal/repotoire && git add repotoire-cli/src/detectors/ai_missing_tests.rs && git commit -m "fix: AIMissingTestsDetector lower LOC threshold and skip framework boilerplate"
```

---

### Task 5: Post-Fix Validation on Django + LangChain

**Files:** None (validation only)

**Step 1: Run full test suite**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo test 2>&1 | grep "test result"
```

Expected: ALL tests pass.

**Step 2: Run analysis on Django**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/django-repo --format json -o /tmp/django-ai-fixed.json
```

Extract AI findings:
```bash
python3 -c "
import json
with open('/tmp/django-ai-fixed.json') as f:
    data = json.load(f)
ai_dets = ['AIComplexitySpikeDetector', 'AINamingPatternDetector', 'AIMissingTestsDetector']
for det in ai_dets:
    findings = [f for f in data['findings'] if f['detector'] == det]
    print(f'{det}: {len(findings)} findings')
    for f in findings[:5]:
        print(f'  - {f[\"title\"]} in {f[\"affected_files\"][0]}')
    if len(findings) > 5:
        print(f'  ... and {len(findings)-5} more')
"
```

**Expected**: Some findings from each detector (not 0), but not excessive. Django is human-written so findings should be moderate.

**Step 3: Run analysis on LangChain**

```bash
cd /home/zhammad/personal/repotoire/repotoire-cli && cargo run -- analyze /tmp/langchain-repo --format json -o /tmp/langchain-ai-fixed.json
```

Extract AI findings with same script.

**Expected**: More AI findings on LangChain than Django (demonstrating the detectors actually differentiate).

**Step 4: Compare Django vs LangChain**

```bash
python3 -c "
import json
print(f'{'Detector':<30} {'Django':>8} {'LangChain':>10} {'Ratio':>7}')
print('-' * 60)
for det in ['AIComplexitySpikeDetector', 'AINamingPatternDetector', 'AIMissingTestsDetector']:
    dj = len([f for f in json.load(open('/tmp/django-ai-fixed.json'))['findings'] if f['detector'] == det])
    lc = len([f for f in json.load(open('/tmp/langchain-ai-fixed.json'))['findings'] if f['detector'] == det])
    ratio = f'{lc/max(dj,1):.1f}x' if dj > 0 else 'N/A'
    print(f'{det:<30} {dj:>8} {lc:>10} {ratio:>7}')
"
```

**Success criteria**:
- Django: <15 total AI findings (low FP rate)
- LangChain: Meaningfully more findings than Django
- No regressions on overall score (Django ≥99/A+)

**Step 5: Audit a sample of findings**

For each detector, manually examine 5-10 findings and categorize as TP/FP/low-actionability. Record results.

---

### Task 6: Second Fix Pass (if needed)

**Files:** Depends on audit results from Task 5

Based on the Task 5 audit, make targeted fixes for any FP patterns discovered. Follow the same test-first pattern:

1. Write failing test for the FP pattern
2. Run test to verify it fails
3. Implement the fix
4. Run test to verify it passes
5. Commit

**This task may be empty if the Task 2-4 fixes are sufficient.**

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Django AI findings | <15 total (low FP rate on human-written code) |
| LangChain AI findings | >Django (detectors differentiate AI code) |
| True positive rate | >85% on both repos |
| Django overall score | ≥99/A+ (no regression) |
| Test count | +6-10 new tests (2-3 per detector) |
| All tests passing | Yes |
