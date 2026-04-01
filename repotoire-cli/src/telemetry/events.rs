//! Telemetry event structs and helper utilities

use serde::Serialize;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Event structs
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Default)]
pub struct AnalysisComplete {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nth_analysis: Option<u64>,
    pub score: f64,
    pub grade: String,
    pub pillar_structure: f64,
    pub pillar_quality: f64,
    pub pillar_architecture: f64,
    pub languages: HashMap<String, u64>,
    pub primary_language: String,
    pub frameworks: Vec<String>,
    pub total_files: u64,
    pub total_kloc: f64,
    pub repo_shape: String,
    pub has_workspace: bool,
    pub workspace_member_count: u32,
    pub buildable_roots: u32,
    pub language_count: u32,
    pub primary_language_ratio: f64,
    pub findings_by_severity: HashMap<String, u64>,
    pub findings_by_detector: HashMap<String, HashMap<String, u64>>,
    pub findings_by_category: HashMap<String, u64>,
    pub graph_nodes: u64,
    pub graph_edges: u64,
    pub graph_modularity: f64,
    pub graph_scc_count: u64,
    pub graph_avg_degree: f64,
    pub graph_articulation_points: u64,
    pub calibration_total: u32,
    pub calibration_at_default: u32,
    pub calibration_outliers: HashMap<String, f64>,
    pub analysis_duration_ms: u64,
    pub analysis_mode: String,
    pub incremental_files_changed: u64,
    pub ci: bool,
    pub os: String,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct DetectorFeedback {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    pub detector: String,
    pub verdict: String,
    pub severity: String,
    pub language: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub framework: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_extension: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finding_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct FixApplied {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    pub detector: String,
    pub fix_type: String,
    pub accepted: bool,
    pub language: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ai_provider: Option<String>,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct DiffRun {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    pub score_before: f64,
    pub score_after: f64,
    pub score_delta: f64,
    pub findings_added: u64,
    pub findings_removed: u64,
    pub findings_added_by_severity: HashMap<String, u64>,
    pub findings_removed_by_severity: HashMap<String, u64>,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct WatchSession {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    pub duration_s: u64,
    pub reanalysis_count: u64,
    pub files_changed_total: u64,
    pub score_start: f64,
    pub score_end: f64,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct CommandUsed {
    pub command: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subcommand: Option<String>,
    pub flags: Vec<String>,
    pub duration_ms: u64,
    pub exit_code: i32,
    pub version: String,
    pub os: String,
    pub ci: bool,
}

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

/// Flags that are safe to send in telemetry (no user data).
const ALLOWED_FLAGS: &[&str] = &[
    "--format",
    "--output",
    "--severity",
    "--top",
    "--page",
    "--fail-on",
    "--explain-score",
    "--timings",
    "--verify",
    "--relaxed",
    "--no-emoji",
    "--json",
];

/// Strip any flags not on the allowlist so we never leak user paths or values.
///
/// Only flag names are sent, never values — `--output=/tmp/report.html` becomes `--output`.
pub fn filter_flags(flags: &[String]) -> Vec<String> {
    flags
        .iter()
        .filter_map(|f| {
            // Match the flag name ignoring a `=value` suffix
            let name = f.split('=').next().unwrap_or(f.as_str());
            if ALLOWED_FLAGS.contains(&name) {
                // Return only the flag name, never the value
                Some(name.to_string())
            } else {
                None
            }
        })
        .collect()
}

/// Returns `true` if the command+subcommand pair should be tracked.
///
/// Excluded:
/// - `--help` / `--version` (meta-flags surfaced as commands by clap)
/// - `config telemetry` (privacy-sensitive command)
pub fn should_track_command(command: &str, subcommand: Option<&str>) -> bool {
    if command == "--help" || command == "--version" {
        return false;
    }
    if command == "config" && subcommand == Some("telemetry") {
        return false;
    }
    true
}

/// Given calibrated thresholds and default thresholds, compute:
/// - `total`: number of calibrated keys
/// - `at_default`: number of keys whose calibrated value equals the default
/// - top-10 outliers by deviation ratio `|calibrated - default| / default`
///
/// Returns `(total, at_default, outliers_map)`.
pub fn select_calibration_outliers(
    calibrated: &HashMap<String, f64>,
    defaults: &HashMap<String, f64>,
) -> (usize, usize, HashMap<String, f64>) {
    let total = calibrated.len();

    let mut at_default = 0usize;
    let mut deviations: Vec<(String, f64)> = Vec::with_capacity(calibrated.len());

    for (key, &cal_val) in calibrated {
        let default_val = defaults.get(key).copied().unwrap_or(0.0);

        // Consider "at default" when values are within floating-point epsilon
        if (cal_val - default_val).abs() < f64::EPSILON {
            at_default += 1;
        }

        let ratio = if default_val.abs() > f64::EPSILON {
            (cal_val - default_val).abs() / default_val.abs()
        } else {
            // If default is ~0 and calibrated differs, treat as maximum deviation
            if cal_val.abs() > f64::EPSILON {
                f64::INFINITY
            } else {
                0.0
            }
        };
        deviations.push((key.clone(), ratio));
    }

    // Sort descending by deviation ratio; stable sort for determinism
    deviations.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    let outliers: HashMap<String, f64> = deviations
        .into_iter()
        .take(10)
        .filter(|(_, ratio)| ratio.is_finite() && *ratio > 0.0)
        .collect();

    (total, at_default, outliers)
}

/// Map file extension to language name for telemetry.
/// Keep in sync with parsers/mod.rs extension registry.
pub fn ext_to_language(ext: &str) -> &'static str {
    match ext {
        "py" => "python",
        "js" | "mjs" | "cjs" => "javascript",
        "ts" | "mts" | "cts" => "typescript",
        "jsx" => "jsx",
        "tsx" => "tsx",
        "rs" => "rust",
        "go" => "go",
        "java" => "java",
        "cs" => "csharp",
        "c" => "c",
        "h" => "c_or_cpp",
        "cpp" | "cc" | "cxx" | "hpp" => "cpp",
        _ => "unknown",
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_analysis_complete_serializes() {
        let mut languages = HashMap::new();
        languages.insert("rust".to_string(), 42_u64);

        let event = AnalysisComplete {
            score: 87.5,
            grade: "B+".to_string(),
            primary_language: "rust".to_string(),
            languages,
            os: "linux".to_string(),
            version: "0.1.0".to_string(),
            ..Default::default()
        };

        let json = serde_json::to_value(&event).expect("serialization should succeed");

        assert_eq!(json["score"], 87.5);
        assert_eq!(json["grade"], "B+");
        assert_eq!(json["primary_language"], "rust");
        assert_eq!(json["languages"]["rust"], 42);
        // Optional fields not set should not appear
        assert!(json.get("repo_id").is_none() || json["repo_id"].is_null());
    }

    #[test]
    fn test_command_used_serializes() {
        let event = CommandUsed {
            command: "analyze".to_string(),
            flags: vec!["--format".to_string(), "--json".to_string()],
            duration_ms: 1500,
            exit_code: 0,
            version: "0.1.0".to_string(),
            os: "linux".to_string(),
            ci: false,
            subcommand: None,
        };

        let json = serde_json::to_value(&event).expect("serialization should succeed");

        assert_eq!(json["command"], "analyze");
        let flags = json["flags"].as_array().expect("flags should be an array");
        assert_eq!(flags.len(), 2);
        assert_eq!(flags[0], "--format");
        assert_eq!(flags[1], "--json");
        assert_eq!(json["duration_ms"], 1500);
    }

    #[test]
    fn test_command_exclusion_list() {
        assert!(!should_track_command("--help", None));
        assert!(!should_track_command("--version", None));
        assert!(!should_track_command("config", Some("telemetry")));

        // These should be tracked
        assert!(should_track_command("analyze", None));
        assert!(should_track_command("diff", None));
        assert!(should_track_command("config", Some("show")));
        assert!(should_track_command("config", None));
    }

    #[test]
    fn test_flags_allowlist() {
        let raw = vec![
            "--format".to_string(),
            "--output=/tmp/report.html".to_string(), // value suffix — flag name is allowed
            "--path=/secret/repo".to_string(),       // not on allowlist
            "--json".to_string(),
            "--unknown".to_string(),
        ];

        let filtered = filter_flags(&raw);

        // --format and --json are allowed; --output name kept but value stripped; --path and --unknown excluded
        assert!(filtered.contains(&"--format".to_string()));
        // Only the flag name is kept — never the value
        assert!(filtered.contains(&"--output".to_string()));
        assert!(
            !filtered.iter().any(|f| f.contains('=')),
            "flag values must be stripped"
        );
        assert!(filtered.contains(&"--json".to_string()));
        assert!(!filtered.iter().any(|f| f.starts_with("--path")));
        assert!(!filtered.contains(&"--unknown".to_string()));
    }

    #[test]
    fn test_calibration_outlier_selection() {
        let mut calibrated = HashMap::new();
        calibrated.insert("max_fn_len".to_string(), 100.0_f64);
        calibrated.insert("max_nesting".to_string(), 4.0_f64);
        calibrated.insert("max_params".to_string(), 6.0_f64);
        calibrated.insert("at_default_key".to_string(), 10.0_f64);

        let mut defaults = HashMap::new();
        defaults.insert("max_fn_len".to_string(), 50.0_f64); // 100% deviation
        defaults.insert("max_nesting".to_string(), 3.0_f64); // 33% deviation
        defaults.insert("max_params".to_string(), 5.0_f64); // 20% deviation
        defaults.insert("at_default_key".to_string(), 10.0_f64); // 0% — at default

        let (total, at_default, outliers) = select_calibration_outliers(&calibrated, &defaults);

        assert_eq!(total, 4);
        assert_eq!(at_default, 1);

        // max_fn_len has the highest deviation ratio (1.0 = 100%)
        let max_fn_ratio = outliers
            .get("max_fn_len")
            .copied()
            .expect("max_fn_len should be an outlier");
        assert!((max_fn_ratio - 1.0).abs() < 1e-9);

        // at_default_key should not appear (ratio == 0)
        assert!(!outliers.contains_key("at_default_key"));

        // At most 10 outliers returned
        assert!(outliers.len() <= 10);
    }

    #[test]
    fn test_ext_to_language() {
        assert_eq!(ext_to_language("ts"), "typescript");
        assert_eq!(ext_to_language("js"), "javascript");
        assert_eq!(ext_to_language("mjs"), "javascript");
        assert_eq!(ext_to_language("py"), "python");
        assert_eq!(ext_to_language("rs"), "rust");
        assert_eq!(ext_to_language("h"), "c_or_cpp");
        assert_eq!(ext_to_language("xyz"), "unknown");
    }

    #[test]
    fn test_detector_feedback_enriched_serializes() {
        let event = DetectorFeedback {
            detector: "GlobalVariablesDetector".to_string(),
            verdict: "false_positive".to_string(),
            severity: "low".to_string(),
            language: "typescript".to_string(),
            file_extension: Some("ts".to_string()),
            finding_title: Some("Global mutable variable: currentAuth".to_string()),
            reason: Some("Module-scoped let in TS".to_string()),
            version: "0.6.0".to_string(),
            ..Default::default()
        };

        let json = serde_json::to_value(&event).expect("should serialize");
        assert_eq!(json["language"], "typescript");
        assert_eq!(json["file_extension"], "ts");
        assert_eq!(
            json["finding_title"],
            "Global mutable variable: currentAuth"
        );
        assert_eq!(json["reason"], "Module-scoped let in TS");
        // Optional None fields should not appear
        assert!(json.get("repo_id").is_none() || json["repo_id"].is_null());
        assert!(json.get("framework").is_none() || json["framework"].is_null());
    }

    #[test]
    fn test_detector_feedback_reason_omitted_when_none() {
        let event = DetectorFeedback {
            detector: "Test".to_string(),
            verdict: "true_positive".to_string(),
            severity: "high".to_string(),
            language: "rust".to_string(),
            version: "0.6.0".to_string(),
            ..Default::default()
        };

        let json = serde_json::to_value(&event).expect("should serialize");
        // reason is None → should not appear in JSON (skip_serializing_if)
        assert!(json.get("reason").is_none() || json["reason"].is_null());
    }
}
