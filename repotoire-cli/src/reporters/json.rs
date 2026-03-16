//! JSON reporter
//!
//! Outputs the full HealthReport as pretty-printed JSON.
//! Useful for machine consumption, piping to jq, or further processing.

use crate::models::HealthReport;
use anyhow::Result;

/// Render report as JSON
pub fn render(report: &HealthReport) -> Result<String> {
    Ok(serde_json::to_string_pretty(report)?)
}

/// Render report as compact JSON (single line)
#[allow(dead_code)] // Public API helper
pub fn render_compact(report: &HealthReport) -> Result<String> {
    Ok(serde_json::to_string(report)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_json_render_valid() {
        let report = test_report();
        let json_str = render(&report).expect("render JSON");
        let parsed: serde_json::Value = serde_json::from_str(&json_str).expect("parse JSON");
        assert_eq!(parsed["grade"], "B");
        assert!(!parsed["findings"].as_array().expect("findings array").is_empty());
    }

    #[test]
    fn test_json_render_compact() {
        let report = test_report();
        let json_str = render_compact(&report).expect("render compact JSON");
        assert!(!json_str.contains('\n'));
        let _: serde_json::Value = serde_json::from_str(&json_str).expect("parse compact JSON");
    }

    #[test]
    fn test_json_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let json_str = render(&report).expect("render JSON");
        let parsed: serde_json::Value = serde_json::from_str(&json_str).expect("parse JSON");
        assert_eq!(parsed["findings"].as_array().expect("findings array").len(), 0);
    }

    #[test]
    fn test_json_confidence_serialized() {
        use crate::models::{Finding, FindingsSummary, Severity};

        let findings = vec![Finding {
            id: "conf-1".into(),
            detector: "TestDetector".into(),
            severity: Severity::Medium,
            title: "Confidence test".into(),
            description: "Has confidence".into(),
            confidence: Some(0.82),
            threshold_metadata: {
                let mut m = std::collections::BTreeMap::new();
                m.insert("confidence_signals".into(), "bundled_code,multi_detector_agreement".into());
                m
            },
            ..Default::default()
        }];

        let report = crate::models::HealthReport {
            overall_score: 75.0,
            grade: "C".into(),
            structure_score: 70.0,
            quality_score: 70.0,
            architecture_score: Some(70.0),
            findings_summary: FindingsSummary::from_findings(&findings),
            findings,
            total_files: 10,
            total_functions: 20,
            total_classes: 5,
            total_loc: 1000,
        };

        let json_str = render(&report).expect("render JSON");
        let parsed: serde_json::Value = serde_json::from_str(&json_str).expect("parse JSON");

        // Verify confidence is serialized
        let finding = &parsed["findings"][0];
        assert_eq!(finding["confidence"], 0.82);

        // Verify threshold_metadata with confidence_signals is serialized
        assert_eq!(
            finding["threshold_metadata"]["confidence_signals"],
            "bundled_code,multi_detector_agreement"
        );
    }
}
