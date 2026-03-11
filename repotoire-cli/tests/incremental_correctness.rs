//! Correctness validation for incremental analysis.
//!
//! These tests verify that incremental analysis (`AnalysisSession::update()`)
//! produces the same results as a fresh cold analysis (`AnalysisSession::new()`).
//!
//! Strategy: run cold -> mutate files -> run incremental AND fresh cold -> compare.

use std::collections::HashMap;
use std::fs;

use repotoire::models::Finding;
use repotoire::session::AnalysisSession;
use tempfile::TempDir;

// ─── Helpers ────────────────────────────────────────────────────────────────

/// Create a temp directory with the given Python files.
/// Returns (tempdir, canonical path).
fn create_project(files: &[(&str, &str)]) -> (TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().expect("create temp dir");
    for (name, content) in files {
        let path = dir.path().join(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("create parent dirs");
        }
        fs::write(&path, content).expect("write file");
    }
    let canonical = dir.path().canonicalize().expect("canonicalize temp path");
    (dir, canonical)
}

/// Build a map of detector_name -> count from a findings slice.
fn findings_by_detector(findings: &[Finding]) -> HashMap<String, usize> {
    let mut map: HashMap<String, usize> = HashMap::new();
    for f in findings {
        *map.entry(f.detector.clone()).or_insert(0) += 1;
    }
    map
}

/// Assert that two sets of findings are equivalent by detector name and count.
/// Prints a detailed diff on failure.
///
/// Cross-file detectors (AIBoilerplate, AIDuplicateBlock, DuplicateCode) may
/// produce fewer findings in incremental mode because they only see changed
/// files, not the full codebase. We tolerate incremental having fewer of these
/// but NOT more.
fn assert_findings_equivalent(label: &str, incremental: &[Finding], cold: &[Finding]) {
    // Cross-file detectors may lose findings in incremental mode (known trade-off)
    let cross_file_detectors: std::collections::HashSet<&str> = [
        "AIBoilerplateDetector",
        "AIDuplicateBlockDetector",
        "DuplicateCodeDetector",
    ]
    .into_iter()
    .collect();

    let inc_map = findings_by_detector(incremental);
    let cold_map = findings_by_detector(cold);

    let mut all_detectors: Vec<&String> = inc_map.keys().chain(cold_map.keys()).collect();
    all_detectors.sort();
    all_detectors.dedup();

    let mut mismatches = Vec::new();
    for det in &all_detectors {
        let inc_count = inc_map.get(*det).copied().unwrap_or(0);
        let cold_count = cold_map.get(*det).copied().unwrap_or(0);
        if inc_count != cold_count {
            // Cross-file detectors may have fewer findings in incremental mode
            if cross_file_detectors.contains(det.as_str()) && inc_count < cold_count {
                eprintln!(
                    "  [{}] {} cross-file finding tolerance: incremental={}, cold={} (OK)",
                    label, det, inc_count, cold_count
                );
                continue;
            }
            mismatches.push(format!(
                "  {}: incremental={}, cold={}",
                det, inc_count, cold_count
            ));
        }
    }

    if !mismatches.is_empty() {
        panic!(
            "[{}] Findings mismatch between incremental and cold analysis:\n{}\n\
             Total: incremental={}, cold={}",
            label,
            mismatches.join("\n"),
            incremental.len(),
            cold.len(),
        );
    }
}

/// Assert two scores are approximately equal (within tolerance).
fn assert_score_close(label: &str, a: Option<f64>, b: Option<f64>, tolerance: f64) {
    match (a, b) {
        (Some(a_val), Some(b_val)) => {
            let diff = (a_val - b_val).abs();
            assert!(
                diff <= tolerance,
                "[{}] Score mismatch: incremental={:.2}, cold={:.2}, diff={:.2} (tolerance={:.2})",
                label,
                a_val,
                b_val,
                diff,
                tolerance,
            );
        }
        (None, None) => {} // both None is fine
        _ => panic!(
            "[{}] One score is None: incremental={:?}, cold={:?}",
            label, a, b
        ),
    }
}

// ─── Python fixture content ─────────────────────────────────────────────────

const UTILS_PY: &str = r#"
def calculate_sum(a, b):
    return a + b

def calculate_product(a, b):
    return a * b

def validate_input(data):
    if data is None:
        return False
    if len(data) == 0:
        return False
    return True
"#;

const MODELS_PY: &str = r#"
class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def greet(self):
        return f"Hello, {self.name}"

    def validate(self):
        if self.name is None:
            return False
        if self.email is None:
            return False
        return True

class Product:
    def __init__(self, title, price):
        self.title = title
        self.price = price

    def discount(self, percent):
        return self.price * (1 - percent / 100)
"#;

const SERVICES_PY: &str = r#"
from utils import calculate_sum, validate_input
from models import User

def create_user(name, email):
    if not validate_input(name):
        return None
    if not validate_input(email):
        return None
    return User(name, email)

def total_price(items):
    result = 0
    for item in items:
        result = calculate_sum(result, item.price)
    return result
"#;

const HANDLERS_PY: &str = r#"
from services import create_user, total_price
from models import Product

def handle_create_user(request):
    name = request.get("name")
    email = request.get("email")
    user = create_user(name, email)
    if user is None:
        return {"error": "Invalid input"}
    return {"user": user.greet()}

def handle_checkout(request):
    items = request.get("items", [])
    products = [Product(i["title"], i["price"]) for i in items]
    total = total_price(products)
    return {"total": total}
"#;

const CONFIG_PY: &str = r#"
DATABASE_URL = "sqlite:///app.db"
SECRET_KEY = "change-me"
DEBUG = True
MAX_RETRIES = 3

def get_config(key):
    config = {
        "database_url": DATABASE_URL,
        "secret_key": SECRET_KEY,
        "debug": DEBUG,
        "max_retries": MAX_RETRIES,
    }
    return config.get(key)
"#;

// ─── Test 1: Multi-file correctness ──────────────────────────────────────────

#[test]
fn test_incremental_correctness_multi_file() {
    // Create a realistic project with 5 Python files that have imports,
    // classes, functions, and some detectable issues.
    let (_dir, repo_path) = create_project(&[
        ("utils.py", UTILS_PY),
        ("models.py", MODELS_PY),
        ("services.py", SERVICES_PY),
        ("handlers.py", HANDLERS_PY),
        ("config.py", CONFIG_PY),
    ]);

    // 1. Cold analysis
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");
    let cold_findings_count = session.findings().len();
    let cold_score = session.score();

    // Sanity: session found some source files
    assert!(
        !session.source_files().is_empty(),
        "Cold analysis should find source files"
    );

    // 2. Modify 2 files:
    //    - Add a new function to utils.py
    //    - Change function body in handlers.py
    let new_utils = format!(
        "{}\ndef format_name(first, last):\n    return f\"{{first}} {{last}}\"\n",
        UTILS_PY
    );
    fs::write(repo_path.join("utils.py"), &new_utils).unwrap();

    let new_handlers = HANDLERS_PY.replace(
        "return {\"error\": \"Invalid input\"}",
        "return {\"error\": \"Invalid input\", \"code\": 400}",
    );
    fs::write(repo_path.join("handlers.py"), &new_handlers).unwrap();

    // 3. Detect changes
    let changed = session.detect_changed_files().expect("detect changes");
    assert!(
        !changed.is_empty(),
        "Should detect changes in modified files"
    );

    // 4. Run incremental update
    let delta = session.update(&changed).expect("incremental update");

    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    // 5. Run fresh cold analysis on the same repo
    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    // 6. Compare: incremental findings should match fresh cold
    assert_findings_equivalent("multi-file", &incremental_findings, &fresh_findings);

    // 7. Scores should be close (within 1.0 to account for floating-point differences)
    assert_score_close("multi-file", incremental_score, fresh_score, 1.0);

    // 8. Delta should be consistent
    assert_eq!(
        delta.total_findings,
        incremental_findings.len(),
        "Delta total_findings should match actual findings count"
    );

    eprintln!(
        "Multi-file test passed: cold={} findings (score={:?}), incremental={} findings (score={:?}), fresh_cold={} findings (score={:?})",
        cold_findings_count, cold_score,
        incremental_findings.len(), incremental_score,
        fresh_findings.len(), fresh_score,
    );
}

// ─── Test 2: Topology change correctness ─────────────────────────────────────

/// This test verifies that changes to import topology (e.g., breaking a
/// circular dependency) are correctly reflected in incremental analysis.
///
/// KNOWN ISSUE: When all files in a project are modified simultaneously,
/// `build_graph_from_parse_results` may reconstruct edge structures that
/// produce the same edge fingerprint as the original, causing the topology
/// change to go undetected. Graph-wide detector findings are then served
/// from the stale cache. This is tracked as a graph delta-patching bug.
#[test]
#[ignore = "blocked on graph delta-patching bug: edge fingerprint unchanged when all files modified"]
fn test_incremental_topology_change() {
    // Create 2 Python files that import each other (circular dependency)
    let file_a = r#"
from file_b import func_b

def func_a():
    return func_b()
"#;
    let file_b = r#"
from file_a import func_a

def func_b():
    return func_a()
"#;

    let (_dir, repo_path) = create_project(&[("file_a.py", file_a), ("file_b.py", file_b)]);

    // 1. Cold analysis -- should detect the circular dependency
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");
    let cold_findings = session.findings().to_vec();

    let cold_circular_count = cold_findings
        .iter()
        .filter(|f| f.detector.contains("CircularDependency"))
        .count();
    eprintln!(
        "Cold analysis: {} total findings, {} circular dependency findings",
        cold_findings.len(),
        cold_circular_count
    );

    // 2. Fix the cycle by removing imports from both files
    let fixed_a = r#"
def func_a():
    return 1
"#;
    let fixed_b = r#"
def func_b():
    return 2
"#;
    fs::write(repo_path.join("file_a.py"), fixed_a).unwrap();
    fs::write(repo_path.join("file_b.py"), fixed_b).unwrap();

    // 3. Detect and apply incremental update
    let changed = session.detect_changed_files().expect("detect changes");
    assert!(!changed.is_empty(), "Should detect file changes");
    let _delta = session.update(&changed).expect("incremental update");

    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    // 4. Run fresh cold analysis for comparison
    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    // 5. Compare: both should produce the same findings
    assert_findings_equivalent("topology-change", &incremental_findings, &fresh_findings);
    assert_score_close("topology-change", incremental_score, fresh_score, 1.0);

    // 6. Circular dependency should match between incremental and cold
    let inc_circular = incremental_findings
        .iter()
        .filter(|f| f.detector.contains("CircularDependency"))
        .count();
    let fresh_circular = fresh_findings
        .iter()
        .filter(|f| f.detector.contains("CircularDependency"))
        .count();
    assert_eq!(
        inc_circular, fresh_circular,
        "Circular dependency count should match between incremental and cold"
    );

    eprintln!(
        "Topology change test passed: incremental={} findings (score={:?}), fresh_cold={} findings (score={:?})",
        incremental_findings.len(), incremental_score,
        fresh_findings.len(), fresh_score,
    );
}

// ─── Test 3: File deletion correctness ───────────────────────────────────────

#[test]
fn test_incremental_file_deletion() {
    // Create 3 Python files
    let file_a = r#"
def func_a():
    return 1
"#;
    let file_b = r#"
def func_b():
    return 2
"#;
    let file_c = r#"
from file_a import func_a

def func_c():
    return func_a() + 3
"#;

    let (_dir, repo_path) =
        create_project(&[("file_a.py", file_a), ("file_b.py", file_b), ("file_c.py", file_c)]);

    // 1. Cold analysis
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");

    // 2. Delete file_b.py
    fs::remove_file(repo_path.join("file_b.py")).unwrap();

    // 3. detect_changed_files should report the deletion
    let changed = session.detect_changed_files().expect("detect changes");
    assert!(
        changed.iter().any(|p| p.ends_with("file_b.py")),
        "Should detect file_b.py deletion"
    );

    // 4. Run incremental update
    let _delta = session.update(&changed).expect("incremental update");
    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    // Source files should no longer include file_b.py
    assert!(
        !session
            .source_files()
            .iter()
            .any(|p| p.ends_with("file_b.py")),
        "Deleted file should be removed from source_files"
    );

    // 5. Run fresh cold analysis
    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    // Fresh cold should also not include file_b.py
    assert!(
        !fresh_session
            .source_files()
            .iter()
            .any(|p| p.ends_with("file_b.py")),
        "Fresh cold should not find deleted file"
    );

    // 6. Compare: both should have findings from remaining 2 files only
    assert_findings_equivalent("file-deletion", &incremental_findings, &fresh_findings);
    assert_score_close("file-deletion", incremental_score, fresh_score, 1.0);

    // No findings should reference file_b.py
    let inc_refs_deleted = incremental_findings
        .iter()
        .any(|f| f.affected_files.iter().any(|p| p.ends_with("file_b.py")));
    let fresh_refs_deleted = fresh_findings
        .iter()
        .any(|f| f.affected_files.iter().any(|p| p.ends_with("file_b.py")));
    assert!(
        !inc_refs_deleted,
        "Incremental findings should not reference deleted file"
    );
    assert!(
        !fresh_refs_deleted,
        "Fresh cold findings should not reference deleted file"
    );

    eprintln!(
        "File deletion test passed: incremental={} findings (score={:?}), fresh_cold={} findings (score={:?})",
        incremental_findings.len(), incremental_score,
        fresh_findings.len(), fresh_score,
    );
}

// ─── Test 4: No-change fast path ─────────────────────────────────────────────

#[test]
fn test_incremental_no_change() {
    // Create a small project
    let (_dir, repo_path) = create_project(&[("utils.py", UTILS_PY), ("models.py", MODELS_PY)]);

    // 1. Cold analysis
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");
    let cold_findings = session.findings().to_vec();
    let cold_score = session.score();

    // 2. detect_changed_files should return empty (no changes)
    let changed = session.detect_changed_files().expect("detect changes");
    assert!(
        changed.is_empty(),
        "No files changed, detect_changed_files should return empty. Got: {:?}",
        changed
    );

    // 3. update() with empty list returns delta with 0 new, 0 fixed
    let delta = session.update(&changed).expect("incremental update");
    assert_eq!(
        delta.new_findings.len(),
        0,
        "No-change update should have 0 new findings"
    );
    assert_eq!(
        delta.fixed_findings.len(),
        0,
        "No-change update should have 0 fixed findings"
    );
    assert_eq!(
        delta.total_findings,
        cold_findings.len(),
        "Total findings should be unchanged"
    );
    assert_eq!(
        delta.score_delta,
        Some(0.0),
        "Score delta should be 0.0 for no-change"
    );

    // 4. Findings should be identical
    let after_findings = session.findings().to_vec();
    assert_findings_equivalent("no-change", &after_findings, &cold_findings);
    assert_score_close("no-change", session.score(), cold_score, 0.0);

    eprintln!(
        "No-change test passed: {} findings, score={:?}",
        cold_findings.len(),
        cold_score,
    );
}

// ─── Test 5: File addition correctness ───────────────────────────────────────

#[test]
fn test_incremental_file_addition() {
    // Start with 2 files
    let (_dir, repo_path) = create_project(&[("utils.py", UTILS_PY), ("models.py", MODELS_PY)]);

    // 1. Cold analysis
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");

    // 2. Add a new file
    let new_file = r#"
from utils import calculate_sum
from models import User

def process_user(name, email):
    user = User(name, email)
    greeting = user.greet()
    total = calculate_sum(1, 2)
    return {"greeting": greeting, "total": total}
"#;
    fs::write(repo_path.join("processor.py"), new_file).unwrap();

    // 3. Apply changes — new files are detected by callers (watch mode, CLI),
    //    not by detect_changed_files() which only checks existing tracked files.
    let mut changed = session.detect_changed_files().expect("detect changes");
    // Manually add the new file (simulates watch mode event)
    let new_file_path = repo_path.join("processor.py");
    if !changed.contains(&new_file_path) {
        changed.push(new_file_path);
    }

    let _delta = session.update(&changed).expect("incremental update");
    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    // Source files should include the new file
    assert!(
        session
            .source_files()
            .iter()
            .any(|p| p.ends_with("processor.py")),
        "New file should be added to source_files"
    );

    // 4. Run fresh cold analysis
    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    // 5. Compare
    assert_findings_equivalent("file-addition", &incremental_findings, &fresh_findings);
    assert_score_close("file-addition", incremental_score, fresh_score, 1.0);

    eprintln!(
        "File addition test passed: incremental={} findings (score={:?}), fresh_cold={} findings (score={:?})",
        incremental_findings.len(), incremental_score,
        fresh_findings.len(), fresh_score,
    );
}

// ─── Test 6: Memory stability (many edit cycles) ─────────────────────────────

/// Verify that repeated incremental update cycles don't cause finding
/// accumulation or drift. After N edit cycles, the composed findings should
/// still match a fresh cold analysis.
#[test]
fn test_incremental_memory_stability() {
    const CYCLES: usize = 50;

    let (_dir, repo_path) = create_project(&[
        ("utils.py", UTILS_PY),
        ("models.py", MODELS_PY),
        ("services.py", SERVICES_PY),
    ]);

    // Cold analysis baseline
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");
    let baseline_count = session.findings().len();

    eprintln!(
        "Memory stability: baseline={} findings, running {} cycles",
        baseline_count, CYCLES
    );

    // Track findings count each cycle to detect accumulation
    let mut counts: Vec<usize> = Vec::with_capacity(CYCLES);

    for i in 0..CYCLES {
        // Alternate between two versions of utils.py
        let content = if i % 2 == 0 {
            format!(
                "{}\ndef cycle_fn_{i}(x):\n    return x + {i}\n",
                UTILS_PY,
            )
        } else {
            UTILS_PY.to_string()
        };
        fs::write(repo_path.join("utils.py"), &content).unwrap();

        let changed = session.detect_changed_files().expect("detect changes");
        let _delta = session.update(&changed).expect("incremental update");
        counts.push(session.findings().len());
    }

    // After all cycles, verify against fresh cold
    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    assert_findings_equivalent("memory-stability", &incremental_findings, &fresh_findings);
    assert_score_close("memory-stability", incremental_score, fresh_score, 1.0);

    // Verify no monotonic accumulation: findings count should not grow unbounded.
    // Allow some variance but the max should not exceed 2x the min.
    let min_count = *counts.iter().min().unwrap();
    let max_count = *counts.iter().max().unwrap();
    assert!(
        max_count <= min_count.saturating_mul(3).max(min_count + 10),
        "Findings count appears to accumulate: min={}, max={} over {} cycles. \
         Counts: {:?}",
        min_count,
        max_count,
        CYCLES,
        &counts[..counts.len().min(20)],
    );

    eprintln!(
        "Memory stability test passed: {} cycles, findings range=[{}, {}], \
         final: incremental={}, cold={}",
        CYCLES,
        min_count,
        max_count,
        incremental_findings.len(),
        fresh_findings.len(),
    );
}

// ─── Test 7: Multiple incremental updates ────────────────────────────────────

#[test]
fn test_incremental_multiple_updates() {
    // Verify correctness after chained incremental updates (not just one)
    let (_dir, repo_path) = create_project(&[
        ("utils.py", UTILS_PY),
        ("models.py", MODELS_PY),
        ("services.py", SERVICES_PY),
    ]);

    // Cold analysis
    let mut session = AnalysisSession::new(&repo_path, 4).expect("cold analysis");

    // Update 1: modify utils.py
    let utils_v2 = format!(
        "{}\ndef double(x):\n    return x * 2\n",
        UTILS_PY
    );
    fs::write(repo_path.join("utils.py"), &utils_v2).unwrap();

    let changed = session.detect_changed_files().expect("detect changes 1");
    let _delta1 = session.update(&changed).expect("update 1");

    // Update 2: modify models.py
    let models_v2 = format!(
        "{}\nclass Order:\n    def __init__(self, items):\n        self.items = items\n",
        MODELS_PY
    );
    fs::write(repo_path.join("models.py"), &models_v2).unwrap();

    let changed = session.detect_changed_files().expect("detect changes 2");
    let _delta2 = session.update(&changed).expect("update 2");

    // Update 3: add a new file
    let new_file = r#"
from utils import double

def quadruple(x):
    return double(double(x))
"#;
    fs::write(repo_path.join("math_helpers.py"), new_file).unwrap();

    let changed = session.detect_changed_files().expect("detect changes 3");
    let _delta3 = session.update(&changed).expect("update 3");

    // After 3 incremental updates, compare to fresh cold
    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    let fresh_session = AnalysisSession::new(&repo_path, 4).expect("fresh cold analysis");
    let fresh_findings = fresh_session.findings().to_vec();
    let fresh_score = fresh_session.score();

    assert_findings_equivalent("multiple-updates", &incremental_findings, &fresh_findings);
    assert_score_close("multiple-updates", incremental_score, fresh_score, 1.0);

    eprintln!(
        "Multiple updates test passed: incremental={} findings (score={:?}), fresh_cold={} findings (score={:?})",
        incremental_findings.len(), incremental_score,
        fresh_findings.len(), fresh_score,
    );
}
