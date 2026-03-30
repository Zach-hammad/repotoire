//! Confidence enrichment pipeline — Phase 1 signals.
//!
//! Adjusts finding confidence using contextual signals. This runs AFTER
//! default confidence assignment (Step 0.5) and BEFORE output filtering,
//! so every finding already has `confidence = Some(...)` when we get here.
//!
//! # Phase 1 signals
//!
//! | Signal                  | Condition                                     | Delta  |
//! |-------------------------|-----------------------------------------------|--------|
//! | Bundled code            | Path matches bundled patterns (dist/, .min.)  | -0.40  |
//! | Non-production path     | Path in scripts/, tests/, examples/ etc.      | -0.15  |
//! | Multi-detector agreement| `detector_count` in threshold_metadata >= 2   | +0.10/extra (max +0.30) |
//! | Test/fixture file       | Path contains /test, /fixture, /mock          | -0.20  |
//!
//! After all signals are applied the confidence is clamped to `[0.05, 0.99]`.
//! Signal provenance is stored in `threshold_metadata["confidence_signals"]`
//! as a comma-separated string.

use crate::models::Finding;

/// Minimum allowed confidence after enrichment.
const CONFIDENCE_FLOOR: f64 = 0.05;
/// Maximum allowed confidence after enrichment.
const CONFIDENCE_CEILING: f64 = 0.99;

/// A single contextual signal that was applied to a finding's confidence.
#[derive(Debug, Clone)]
pub struct ConfidenceSignal {
    /// Short machine-readable signal name (e.g. `"bundled_code"`).
    pub signal: String,
    /// The delta that was added to confidence (negative = decreased).
    pub delta: f64,
    /// Human-readable explanation of why the signal fired.
    pub reason: String,
}

/// Enrich a single finding's confidence with Phase 1 contextual signals.
///
/// Returns the list of signals that were applied (empty if none matched).
/// The finding's `confidence` field is mutated in place and the signal
/// provenance is stored in `threshold_metadata["confidence_signals"]`.
pub fn enrich_confidence(finding: &mut Finding) -> Vec<ConfidenceSignal> {
    let mut signals: Vec<ConfidenceSignal> = Vec::new();

    // We need a file path to evaluate path-based signals.
    let file_path = finding
        .affected_files
        .first()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();

    // ── Signal 1: Bundled code ──────────────────────────────────────
    if !file_path.is_empty()
        && crate::detectors::content_classifier::is_likely_bundled_path(&file_path)
    {
        signals.push(ConfidenceSignal {
            signal: "bundled_code".into(),
            delta: -0.4,
            reason: format!("File path matches bundled pattern: {}", file_path),
        });
    }

    // ── Signal 2: Non-production path ───────────────────────────────
    if !file_path.is_empty()
        && crate::detectors::content_classifier::is_non_production_path(&file_path)
    {
        signals.push(ConfidenceSignal {
            signal: "non_production_path".into(),
            delta: -0.15,
            reason: format!("File in non-production path: {}", file_path),
        });
    }

    // ── Signal 3: Multi-detector agreement ──────────────────────────
    if let Some(count_str) = finding.threshold_metadata.get("detector_count") {
        if let Ok(count) = count_str.parse::<u32>() {
            if count >= 2 {
                let extra = (count - 1).min(3); // cap at +0.3
                let delta = extra as f64 * 0.1;
                signals.push(ConfidenceSignal {
                    signal: "multi_detector_agreement".into(),
                    delta,
                    reason: format!("{} detectors agree ({}extra x +0.1)", count, extra),
                });
            }
        }
    }

    // ── Signal 4: Test/fixture file ─────────────────────────────────
    if !file_path.is_empty() && is_test_or_fixture_path(&file_path) {
        signals.push(ConfidenceSignal {
            signal: "test_fixture_file".into(),
            delta: -0.2,
            reason: format!("File path is a test/fixture/mock: {}", file_path),
        });
    }

    // ── Apply signals ───────────────────────────────────────────────
    if !signals.is_empty() {
        let base = finding.confidence.unwrap_or(0.70);
        let total_delta: f64 = signals.iter().map(|s| s.delta).sum();
        let adjusted = (base + total_delta).clamp(CONFIDENCE_FLOOR, CONFIDENCE_CEILING);
        finding.confidence = Some(adjusted);

        // Store provenance
        let names: Vec<&str> = signals.iter().map(|s| s.signal.as_str()).collect();
        finding
            .threshold_metadata
            .insert("confidence_signals".into(), names.join(","));
    }

    signals
}

/// Check if a file path looks like a test, fixture, or mock file.
///
/// This is a path-heuristic check complementary to
/// `content_classifier::is_non_production_path` — it targets individual
/// test/fixture/mock files rather than entire directory subtrees.
fn is_test_or_fixture_path(path: &str) -> bool {
    let lower = path.to_lowercase();

    // Directory segments
    lower.contains("/test/")
        || lower.contains("/tests/")
        || lower.contains("/__tests__/")
        || lower.contains("/fixture/")
        || lower.contains("/fixtures/")
        || lower.contains("/__fixtures__/")
        || lower.contains("/mock/")
        || lower.contains("/mocks/")
        || lower.contains("/__mocks__/")
        // File-name patterns
        || lower.contains("_test.")
        || lower.contains(".test.")
        || lower.contains("_spec.")
        || lower.contains(".spec.")
        || lower.contains("_mock.")
        || lower.contains(".mock.")
}

/// Batch-enrich all findings in place.
///
/// This is the main entry point called from the postprocess pipeline.
/// Findings that don't match any signal are left untouched.
pub fn enrich_all(findings: &mut [Finding]) {
    let mut enriched = 0usize;
    for finding in findings.iter_mut() {
        let signals = enrich_confidence(finding);
        if !signals.is_empty() {
            enriched += 1;
        }
    }
    if enriched > 0 {
        tracing::debug!(
            "Confidence enrichment: adjusted {} findings with contextual signals",
            enriched
        );
    }
}

// ────────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Helper: build a minimal finding with a given file path and optional confidence.
    fn make_finding(path: &str, confidence: Option<f64>) -> Finding {
        Finding {
            detector: "TestDetector".into(),
            severity: crate::models::Severity::Medium,
            title: "Test finding".into(),
            description: "desc".into(),
            affected_files: if path.is_empty() {
                vec![]
            } else {
                vec![PathBuf::from(path)]
            },
            confidence,
            ..Default::default()
        }
    }

    // ── Bundled code signal ──────────────────────────────────────────

    #[test]
    fn test_bundled_code_dist() {
        let mut f = make_finding("project/dist/bundle.js", Some(0.75));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "bundled_code"));
        assert!(f.confidence.unwrap() < 0.75);
    }

    #[test]
    fn test_bundled_code_min() {
        let mut f = make_finding("lib/react.min.js", Some(0.80));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "bundled_code"));
    }

    #[test]
    fn test_bundled_code_build() {
        let mut f = make_finding("project/build/output.js", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "bundled_code"));
    }

    // ── Non-production path signal ───────────────────────────────────

    #[test]
    fn test_non_production_scripts() {
        let mut f = make_finding("scripts/deploy.sh", Some(0.75));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "non_production_path"));
        assert!(f.confidence.unwrap() < 0.75);
    }

    #[test]
    fn test_non_production_examples() {
        let mut f = make_finding("examples/demo.py", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "non_production_path"));
    }

    // ── Multi-detector agreement signal ──────────────────────────────

    #[test]
    fn test_multi_detector_two() {
        let mut f = make_finding("src/app.py", Some(0.70));
        f.threshold_metadata
            .insert("detector_count".into(), "2".into());
        let signals = enrich_confidence(&mut f);
        assert!(signals
            .iter()
            .any(|s| s.signal == "multi_detector_agreement"));
        // +0.1 for one extra detector
        assert!((f.confidence.unwrap() - 0.80).abs() < f64::EPSILON);
    }

    #[test]
    fn test_multi_detector_four_capped() {
        let mut f = make_finding("src/app.py", Some(0.60));
        f.threshold_metadata
            .insert("detector_count".into(), "4".into());
        let signals = enrich_confidence(&mut f);
        let sig = signals
            .iter()
            .find(|s| s.signal == "multi_detector_agreement")
            .expect("signal present");
        // 4 detectors => 3 extra, capped at +0.3
        assert!((sig.delta - 0.3).abs() < f64::EPSILON);
        assert!((f.confidence.unwrap() - 0.90).abs() < f64::EPSILON);
    }

    #[test]
    fn test_multi_detector_five_capped_at_three() {
        let mut f = make_finding("src/app.py", Some(0.50));
        f.threshold_metadata
            .insert("detector_count".into(), "5".into());
        let signals = enrich_confidence(&mut f);
        let sig = signals
            .iter()
            .find(|s| s.signal == "multi_detector_agreement")
            .expect("signal present");
        // 5 detectors => 4 extra, but capped at 3 => +0.3
        assert!((sig.delta - 0.3).abs() < f64::EPSILON);
    }

    #[test]
    fn test_multi_detector_one_no_signal() {
        let mut f = make_finding("src/app.py", Some(0.70));
        f.threshold_metadata
            .insert("detector_count".into(), "1".into());
        let signals = enrich_confidence(&mut f);
        assert!(!signals
            .iter()
            .any(|s| s.signal == "multi_detector_agreement"));
    }

    // ── Test/fixture file signal ─────────────────────────────────────

    #[test]
    fn test_test_file() {
        let mut f = make_finding("src/tests/test_utils.py", Some(0.75));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "test_fixture_file"));
        assert!(f.confidence.unwrap() < 0.75);
    }

    #[test]
    fn test_fixture_file() {
        let mut f = make_finding("tests/fixtures/bad_code.py", Some(0.80));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "test_fixture_file"));
    }

    #[test]
    fn test_mock_file() {
        let mut f = make_finding("src/__mocks__/api.js", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "test_fixture_file"));
    }

    #[test]
    fn test_spec_file() {
        let mut f = make_finding("src/utils.spec.ts", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.iter().any(|s| s.signal == "test_fixture_file"));
    }

    // ── Clamping ─────────────────────────────────────────────────────

    #[test]
    fn test_clamp_floor() {
        // Bundled (-0.4) + test/fixture (-0.2) + non-production (-0.15) = -0.75
        // Starting at 0.30 => 0.30 - 0.75 = -0.45, clamped to 0.05
        let mut f = make_finding("dist/fixtures/test.min.js", Some(0.30));
        enrich_confidence(&mut f);
        assert!((f.confidence.unwrap() - CONFIDENCE_FLOOR).abs() < f64::EPSILON);
    }

    #[test]
    fn test_clamp_ceiling() {
        // Multi-detector +0.3 on a base of 0.95 => 1.25, clamped to 0.99
        let mut f = make_finding("src/app.py", Some(0.95));
        f.threshold_metadata
            .insert("detector_count".into(), "4".into());
        enrich_confidence(&mut f);
        assert!((f.confidence.unwrap() - CONFIDENCE_CEILING).abs() < f64::EPSILON);
    }

    // ── No signals ───────────────────────────────────────────────────

    #[test]
    fn test_no_signals_no_change() {
        let mut f = make_finding("src/main.rs", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.is_empty());
        assert!((f.confidence.unwrap() - 0.70).abs() < f64::EPSILON);
        assert!(!f.threshold_metadata.contains_key("confidence_signals"));
    }

    #[test]
    fn test_no_file_path_no_signals() {
        let mut f = make_finding("", Some(0.70));
        let signals = enrich_confidence(&mut f);
        assert!(signals.is_empty());
    }

    // ── Multiple signals combined ────────────────────────────────────

    #[test]
    fn test_multiple_signals_combined() {
        // dist/ triggers bundled (-0.4) and test/fixture (-0.2 from /fixtures/ in bundled path)
        // Actually dist/fixtures/ triggers bundled AND test_fixture
        let mut f = make_finding("project/dist/fixtures/helper.js", Some(0.80));
        let signals = enrich_confidence(&mut f);
        assert!(signals.len() >= 2);
        // Check provenance stored
        let provenance = f
            .threshold_metadata
            .get("confidence_signals")
            .expect("signals stored");
        assert!(provenance.contains("bundled_code"));
    }

    // ── Provenance ───────────────────────────────────────────────────

    #[test]
    fn test_provenance_stored() {
        let mut f = make_finding("scripts/setup.sh", Some(0.75));
        enrich_confidence(&mut f);
        let provenance = f
            .threshold_metadata
            .get("confidence_signals")
            .expect("stored");
        assert!(provenance.contains("non_production_path"));
    }

    // ── enrich_all batch ─────────────────────────────────────────────

    #[test]
    fn test_enrich_all_batch() {
        let mut findings = vec![
            make_finding("src/main.rs", Some(0.70)), // no signals
            make_finding("project/dist/bundle.js", Some(0.80)), // bundled
            make_finding("tests/test_foo.py", Some(0.75)), // test file + non-prod
        ];
        enrich_all(&mut findings);

        // First finding untouched
        assert!((findings[0].confidence.unwrap() - 0.70).abs() < f64::EPSILON);
        // Second finding reduced (bundled -0.4)
        assert!(findings[1].confidence.unwrap() < 0.80);
        // Third finding reduced (test/fixture -0.2, non-prod -0.15)
        assert!(findings[2].confidence.unwrap() < 0.75);
    }

    #[test]
    fn test_enrich_all_empty() {
        let mut findings: Vec<Finding> = vec![];
        enrich_all(&mut findings);
        assert!(findings.is_empty());
    }

    // ── is_test_or_fixture_path ──────────────────────────────────────

    #[test]
    fn test_is_test_or_fixture_path_positive() {
        assert!(is_test_or_fixture_path("src/tests/foo.py"));
        assert!(is_test_or_fixture_path("foo/__tests__/bar.js"));
        assert!(is_test_or_fixture_path("src/fixture/data.json"));
        assert!(is_test_or_fixture_path("project/mocks/api.ts"));
        assert!(is_test_or_fixture_path("lib/utils_test.go"));
        assert!(is_test_or_fixture_path("src/app.test.tsx"));
        assert!(is_test_or_fixture_path("src/app.spec.ts"));
        assert!(is_test_or_fixture_path("src/helper_mock.py"));
        assert!(is_test_or_fixture_path("src/data.mock.ts"));
    }

    #[test]
    fn test_is_test_or_fixture_path_negative() {
        assert!(!is_test_or_fixture_path("src/main.rs"));
        assert!(!is_test_or_fixture_path("lib/utils.py"));
        assert!(!is_test_or_fixture_path("src/testing_utils.py")); // "testing" != "test/"
    }
}
