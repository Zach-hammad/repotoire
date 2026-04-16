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
use std::collections::{HashMap, HashSet};
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
) -> (Vec<Finding>, HashSet<String>) {
    if detectors.is_empty() {
        return (Vec::new(), HashSet::new());
    }

    // Build the bypass set: detector names that opt out of GBDT postprocessor filtering
    let bypass_set: HashSet<String> = detectors
        .iter()
        .filter(|d| d.bypass_postprocessor())
        .map(|d| d.name().to_string())
        .collect();

    // Partition into independent / dependent
    let (independent, dependent): (Vec<_>, Vec<_>) =
        detectors.iter().partition(|d| !d.is_dependent());

    info!(
        "run_detectors: {} independent, {} dependent, {} workers, {} bypass postprocessor",
        independent.len(),
        dependent.len(),
        workers,
        bypass_set.len(),
    );

    let pool = match rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .stack_size(8 * 1024 * 1024) // 8 MB stack
        .build()
    {
        Ok(p) => p,
        Err(e) => {
            error!("Failed to build rayon pool: {e}");
            return (Vec::new(), bypass_set);
        }
    };

    // ── Independent (parallel) ──────────────────────────────────────────
    // Each result carries its own wall-time so we can emit a summary of the
    // slowest detectors at debug level — without that, the detect stage is a
    // 2s black box and future perf work is blind to which of the 110
    // detectors actually dominates.
    let mut results: Vec<(String, Vec<Finding>, bool, u64)> = pool.install(|| {
        independent
            .par_iter()
            .map(|detector| run_one_timed(detector, ctx))
            .collect()
    });

    // Sort by detector name for deterministic merge order
    results.sort_by(|a, b| a.0.cmp(&b.0));

    let mut all_findings: Vec<Finding> = Vec::new();
    let mut skipped_count: usize = 0;
    let mut timings: Vec<(String, u64)> = Vec::with_capacity(results.len() + dependent.len());
    for (name, findings, skipped, elapsed_ms) in results {
        if skipped {
            skipped_count += 1;
        }
        timings.push((name, elapsed_ms));
        all_findings.extend(findings);
    }

    // ── Dependent (sequential) ──────────────────────────────────────────
    for detector in dependent {
        let (name, findings, skipped, elapsed_ms) = run_one_timed(detector, ctx);
        if skipped {
            skipped_count += 1;
        }
        timings.push((name, elapsed_ms));
        all_findings.extend(findings);
    }

    if skipped_count > 0 {
        warn!(
            "{} detector(s) skipped due to errors (see warnings above)",
            skipped_count,
        );
    }

    // Emit a sorted top-K slowest-detector summary at debug level. This is
    // the hook future perf audits need to identify which detectors deserve
    // targeted optimization — no more guessing at where the 2s goes.
    log_slowest_detectors(&timings);

    (all_findings, bypass_set)
}

/// Log top-10 slowest detectors at debug level, plus total detect-stage
/// wall time sum. Noise-free when debug logging is disabled.
fn log_slowest_detectors(timings: &[(String, u64)]) {
    if !tracing::enabled!(tracing::Level::DEBUG) {
        return;
    }
    let mut sorted: Vec<&(String, u64)> = timings.iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(&a.1));
    let total_ms: u64 = timings.iter().map(|(_, ms)| *ms).sum();
    debug!(
        "detect: {} detectors ran, total serial time {}ms (wall time amortized by rayon)",
        timings.len(),
        total_ms,
    );
    for (name, ms) in sorted.iter().take(10) {
        debug!("  slow: {:>6}ms  {}", ms, name);
    }
}

/// Execute a single detector with catch_unwind, timing, and max_findings.
///
/// Returns `(name, findings, skipped, elapsed_ms)`. The timing is surfaced so
/// the caller can emit a top-N slowest-detector summary.
fn run_one_timed(
    detector: &Arc<dyn Detector>,
    ctx: &AnalysisContext<'_>,
) -> (String, Vec<Finding>, bool, u64) {
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
            (name, findings, false, elapsed_ms)
        }
        // detect() returned an error (query error, missing graph property, etc.)
        Ok(Err(e)) => {
            warn!("{} skipped (query error): {}", name, e);
            (name, Vec::new(), true, elapsed_ms)
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
            (name, Vec::new(), true, elapsed_ms)
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

/// Remove findings where **all** affected files are test files, and downgrade
/// findings where **all** affected files are non-production code (scripts,
/// benchmarks, tools, examples) to LOW severity.
///
/// Findings with no affected files are kept (can't determine origin).
pub fn filter_test_file_findings(findings: &mut Vec<Finding>) {
    let before = findings.len();

    // Downgrade non-production findings to LOW before filtering tests
    for f in findings.iter_mut() {
        if !f.affected_files.is_empty()
            && f.affected_files
                .iter()
                .all(|p| super::base::is_non_production_file(p))
            && f.severity != crate::models::Severity::Low
            && f.severity != crate::models::Severity::Info
        {
            debug!("Downgrading non-production finding to LOW: {}", f.title);
            f.severity = crate::models::Severity::Low;
        }
    }

    // Remove test-only findings
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
        let Some(func) = ctx.graph.node_idx(func_idx) else {
            continue;
        };
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
pub fn sort_findings_deterministic(findings: &mut [Finding]) {
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
    use crate::graph::builder::GraphBuilder;
    use crate::models::Severity;
    use std::path::PathBuf;

    #[test]
    fn test_run_detectors_empty() {
        let graph = GraphBuilder::new().freeze();
        let ctx = AnalysisContext::test(&graph);
        let (findings, bypass_set) = run_detectors(&[], &ctx, 1);
        assert!(findings.is_empty());
        assert!(bypass_set.is_empty());
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
