//! Standalone detector runner — pure functions, no engine struct.
//!
//! Stateless detector execution — pure functions, no engine struct.
//! callers build an `AnalysisContext`, hand it to `run_detectors()`, and
//! get findings back.
//!
//! # Functions
//!
//! | Function | Purpose |
//! |----------|---------|
//! | `run_detectors` | Parallel execution with rayon, panic safety, per-detector limits |
//! | `inject_taint_precomputed` | Push pre-computed taint paths into security detectors |
//! | `filter_test_file_findings` | Remove findings where ALL affected files are tests |
//! | `apply_hmm_context_filter` | HMM-based FP reduction for coupling/dead-code detectors |
//! | `sort_findings_deterministic` | Canonical sort for stable output |

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{is_test_file, Detector};
use crate::detectors::context_hmm::FunctionContext;
use crate::detectors::taint::centralized::CentralizedTaintResults;
use crate::graph::CodeNode;
use crate::models::Finding;
use rayon::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;
use tracing::{debug, error, info, warn};

// ── run_detectors ────────────────────────────────────────────────────────────

/// Run all `detectors` against `ctx` using `workers` threads.
///
/// Features:
/// - Rayon thread pool with 8 MB stack size (deeply nested C/C++ ASTs).
/// - `catch_unwind` per detector for panic safety.
/// - Per-detector `max_findings` truncation via `detector.config()`.
/// - Timing logged per detector at `debug` level.
///
/// Independent detectors run in parallel; dependent detectors run sequentially
/// afterwards (preserving topological-order semantics from the old engine).
pub fn run_detectors(
    detectors: &[Arc<dyn Detector>],
    ctx: &AnalysisContext<'_>,
    workers: usize,
) -> Vec<Finding> {
    if detectors.is_empty() {
        return Vec::new();
    }

    // Partition into independent / dependent
    let (independent, dependent): (Vec<_>, Vec<_>) =
        detectors.iter().partition(|d| !d.is_dependent());

    info!(
        "run_detectors: {} independent, {} dependent, {} workers",
        independent.len(),
        dependent.len(),
        workers,
    );

    let pool = match rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .stack_size(8 * 1024 * 1024) // 8 MB stack
        .build()
    {
        Ok(p) => p,
        Err(e) => {
            error!("Failed to build rayon pool: {e}");
            return Vec::new();
        }
    };

    // ── Independent (parallel) ──────────────────────────────────────────
    let mut results: Vec<(String, Vec<Finding>)> = pool.install(|| {
        independent
            .par_iter()
            .map(|detector| run_one(detector, ctx))
            .collect()
    });

    // Sort by detector name for deterministic merge order
    results.sort_by(|a, b| a.0.cmp(&b.0));

    let mut all_findings: Vec<Finding> = Vec::new();
    for (_name, findings) in results {
        all_findings.extend(findings);
    }

    // ── Dependent (sequential) ──────────────────────────────────────────
    for detector in dependent {
        let (_name, findings) = run_one(detector, ctx);
        all_findings.extend(findings);
    }

    all_findings
}

/// Execute a single detector with catch_unwind, timing, and max_findings.
fn run_one(detector: &Arc<dyn Detector>, ctx: &AnalysisContext<'_>) -> (String, Vec<Finding>) {
    let name = detector.name().to_string();
    let start = Instant::now();

    // detect() returns Result<Vec<Finding>>; catch_unwind adds another Result layer.
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| detector.detect(ctx)));

    let elapsed_ms = start.elapsed().as_millis() as u64;

    match result {
        // Happy path: detect() succeeded
        Ok(Ok(mut findings)) => {
            // Per-detector max_findings truncation
            if let Some(config) = detector.config() {
                if let Some(max) = config.max_findings {
                    if findings.len() > max {
                        findings.truncate(max);
                    }
                }
            }
            debug!(
                "{} produced {} findings in {}ms",
                name,
                findings.len(),
                elapsed_ms,
            );
            (name, findings)
        }
        // detect() returned an error (query error, missing graph property, etc.)
        Ok(Err(e)) => {
            debug!("{} skipped (query error): {}", name, e);
            (name, Vec::new())
        }
        // Detector panicked
        Err(panic_info) => {
            let panic_msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                s.to_string()
            } else if let Some(s) = panic_info.downcast_ref::<String>() {
                s.clone()
            } else {
                "Unknown panic".to_string()
            };
            error!("{} panicked: {}", name, panic_msg);
            (name, Vec::new())
        }
    }
}

// ── inject_taint_precomputed ─────────────────────────────────────────────────

/// Inject pre-computed taint analysis results into security detectors.
///
/// Iterates `detectors`, checks `taint_category()`, and calls
/// `set_precomputed_taint()` with the matching cross/intra paths from
/// `precomputed`.
pub fn inject_taint_precomputed(
    detectors: &[Arc<dyn Detector>],
    precomputed: &CentralizedTaintResults,
) {
    for detector in detectors {
        if let Some(category) = detector.taint_category() {
            let cross = precomputed
                .cross_function
                .get(&category)
                .cloned()
                .unwrap_or_default();
            let intra = precomputed
                .intra_function
                .get(&category)
                .cloned()
                .unwrap_or_default();
            detector.set_precomputed_taint(cross, intra);
        }
    }
}

// ── filter_test_file_findings ────────────────────────────────────────────────

/// Remove findings where **all** affected files are test files.
///
/// Findings with no affected files are kept (can't determine origin).
pub fn filter_test_file_findings(findings: &mut Vec<Finding>) {
    let before = findings.len();
    findings.retain(|f| {
        if f.affected_files.is_empty() {
            return true; // keep — unknown origin
        }
        !f.affected_files.iter().all(|p| is_test_file(p))
    });
    let removed = before - findings.len();
    if removed > 0 {
        debug!("Filtered out {} findings from test files", removed);
    }
}

// ── apply_hmm_context_filter ─────────────────────────────────────────────────

/// Apply HMM-based context filtering to reduce false positives.
///
/// - Skip coupling findings for UTILITY / HANDLER / TEST functions.
/// - Skip dead-code findings for HANDLER / TEST functions.
///
/// Uses `ctx.hmm_classifications` (`HashMap<String, (FunctionContext, f64)>`)
/// and builds a per-file function lookup from `ctx.graph` for binary search.
/// Filtering is parallel via `par_iter` (matching original engine behaviour).
pub fn apply_hmm_context_filter(findings: Vec<Finding>, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
    use rustc_hash::FxHashSet;

    let hmm = &ctx.hmm_classifications;
    if hmm.is_empty() {
        return findings;
    }

    // O(1) lookup sets for relevant detector names
    static COUPLING_DETECTORS: &[&str] = &[
        "DegreeCentralityDetector",
        "ShotgunSurgeryDetector",
        "FeatureEnvyDetector",
        "InappropriateIntimacyDetector",
    ];
    static DEAD_CODE_DETECTORS: &[&str] = &["UnreachableCodeDetector", "DeadCodeDetector"];

    let coupling_set: FxHashSet<&str> = COUPLING_DETECTORS.iter().copied().collect();
    let dead_code_set: FxHashSet<&str> = DEAD_CODE_DETECTORS.iter().copied().collect();

    // Build per-file function list from graph, sorted by line_start for binary search
    let i = ctx.graph.interner();
    let mut func_by_file: HashMap<&str, Vec<&CodeNode>> = HashMap::new();
    for &func_idx in ctx.graph.functions_idx() {
        let Some(func) = ctx.graph.node_idx(func_idx) else { continue };
        func_by_file.entry(func.path(i)).or_default().push(func);
    }
    // Sort each file's functions by line_start (done once, shared by all findings)
    for funcs in func_by_file.values_mut() {
        funcs.sort_unstable_by_key(|f| f.line_start);
    }

    let before = findings.len();
    let filtered: Vec<Finding> = findings
        .into_par_iter()
        .filter(|finding| {
            let is_coupling = coupling_set.contains(finding.detector.as_str());
            let is_dead_code = dead_code_set.contains(finding.detector.as_str());

            // Fast path: irrelevant detector
            if !is_coupling && !is_dead_code {
                return true;
            }

            // Locate function at finding's file + line via binary search
            let func_qn = find_function_at_line(finding, &func_by_file, i);

            if let Some(qn) = func_qn {
                if let Some((context, _confidence)) = hmm.get(qn) {
                    if is_coupling && context.skip_coupling() {
                        return false;
                    }
                    if is_dead_code && context.skip_dead_code() {
                        return false;
                    }
                }
            }

            true
        })
        .collect();

    let removed = before - filtered.len();
    if removed > 0 {
        info!("HMM context filter removed {} false positives", removed);
    }

    filtered
}

/// Find the function qualified name at a finding's file + line using binary search.
fn find_function_at_line<'a>(
    finding: &Finding,
    func_by_file: &HashMap<&str, Vec<&'a CodeNode>>,
    interner: &'a crate::graph::interner::StringInterner,
) -> Option<&'a str> {
    let (file, line) = match (finding.affected_files.first(), finding.line_start) {
        (Some(f), Some(l)) => (f, l),
        _ => return None,
    };

    let file_str = file.to_string_lossy();
    let funcs = func_by_file.get(file_str.as_ref())?;

    // Binary search: find the last function whose line_start <= line
    let idx = funcs.partition_point(|f| f.line_start <= line);
    if idx > 0 {
        let func = funcs[idx - 1];
        if func.line_end >= line {
            return Some(func.qn(interner));
        }
    }

    None
}

// ── sort_findings_deterministic ──────────────────────────────────────────────

/// Canonical sort: severity (desc), affected_files, line_start, detector, title.
///
/// Ensures deterministic output across runs.
pub fn sort_findings_deterministic(findings: &mut Vec<Finding>) {
    findings.sort_by(|a, b| {
        b.severity
            .cmp(&a.severity)
            .then_with(|| {
                let a_file = a
                    .affected_files
                    .first()
                    .map(|f| f.to_string_lossy())
                    .unwrap_or_default();
                let b_file = b
                    .affected_files
                    .first()
                    .map(|f| f.to_string_lossy())
                    .unwrap_or_default();
                a_file.cmp(&b_file)
            })
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.detector.cmp(&b.detector))
            .then_with(|| a.title.cmp(&b.title))
    });
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;
    use crate::models::Severity;
    use std::path::PathBuf;

    #[test]
    fn test_run_detectors_empty() {
        let graph = GraphStore::in_memory();
        let ctx = AnalysisContext::test(&graph);
        let findings = run_detectors(&[], &ctx, 1);
        assert!(findings.is_empty());
    }

    #[test]
    fn test_sort_findings_deterministic() {
        let mut findings = vec![
            Finding {
                severity: Severity::Low,
                detector: "B".into(),
                title: "b".into(),
                affected_files: vec![PathBuf::from("z.py")],
                line_start: Some(10),
                ..Default::default()
            },
            Finding {
                severity: Severity::High,
                detector: "A".into(),
                title: "a".into(),
                affected_files: vec![PathBuf::from("a.py")],
                line_start: Some(1),
                ..Default::default()
            },
            Finding {
                severity: Severity::High,
                detector: "A".into(),
                title: "a".into(),
                affected_files: vec![PathBuf::from("a.py")],
                line_start: Some(5),
                ..Default::default()
            },
        ];

        sort_findings_deterministic(&mut findings);

        // High severity first
        assert_eq!(findings[0].severity, Severity::High);
        assert_eq!(findings[1].severity, Severity::High);
        assert_eq!(findings[2].severity, Severity::Low);

        // Within same severity: ordered by file, then line
        assert_eq!(findings[0].line_start, Some(1));
        assert_eq!(findings[1].line_start, Some(5));
    }

    #[test]
    fn test_filter_test_file_findings() {
        let mut findings = vec![
            // All affected files are test files — should be removed
            Finding {
                detector: "X".into(),
                affected_files: vec![PathBuf::from("tests/test_foo.py")],
                ..Default::default()
            },
            // Non-test file — should be kept
            Finding {
                detector: "Y".into(),
                affected_files: vec![PathBuf::from("src/lib.rs")],
                ..Default::default()
            },
            // No affected files — should be kept
            Finding {
                detector: "Z".into(),
                affected_files: vec![],
                ..Default::default()
            },
        ];

        filter_test_file_findings(&mut findings);

        assert_eq!(findings.len(), 2);
        assert_eq!(findings[0].detector, "Y");
        assert_eq!(findings[1].detector, "Z");
    }
}
