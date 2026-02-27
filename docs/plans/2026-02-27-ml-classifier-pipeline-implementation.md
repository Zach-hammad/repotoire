# ML Classifier Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the 2-layer MLP classifier with a GBDT model using 28 evidence-backed features, add per-file debt scoring, and integrate ranking mode into the analyze command.

**Architecture:** GBDT inference via `gbdt` crate (pure Rust). Feature extraction pulls from graph (petgraph), git history (git2), and finding metadata. Three output modes share the same 28-feature vector and model: classification (TP/FP), ranking (actionability 0-100), debt scoring (per-file risk 0-100). Fallback chain: GBDT → heuristic → raw.

**Tech Stack:** Rust, gbdt crate, petgraph, git2, tree-sitter, serde_json

---

## Task 1: Add `gbdt` Dependency

**Files:**
- Modify: `repotoire-cli/Cargo.toml:40-101`

**Step 1: Add the dependency**

In `repotoire-cli/Cargo.toml`, add after the `dashmap` line (line 95):

```toml
gbdt = "0.1"  # Pure Rust GBDT inference
```

**Step 2: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles without errors

**Step 3: Commit**

```bash
git add repotoire-cli/Cargo.toml Cargo.lock
git commit -m "feat: add gbdt crate dependency for ML classifier"
```

---

## Task 2: Create `features_v2.rs` — 28 Evidence-Backed Feature Extractor

**Files:**
- Create: `repotoire-cli/src/classifier/features_v2.rs`
- Modify: `repotoire-cli/src/classifier/mod.rs:13` (add `pub mod features_v2;`)

This is the largest task. The feature extractor takes a `Finding`, a `&dyn GraphQuery`, and an optional `&GitHistory` reference, and returns a 28-element `f64` vector.

**Step 1: Write the failing test**

Create `repotoire-cli/src/classifier/features_v2.rs` with tests first:

```rust
//! Evidence-backed feature extraction for GBDT classifier
//!
//! 28 features backed by academic research (Yang & Menzies 2021,
//! Wang et al. 2018, DeMuVGN 2024, PLOS ONE 2025, CMU SEI 2018).
//! No leaking features. All computable from existing infrastructure.

use crate::graph::GraphQuery;
use crate::models::{Finding, Severity};
use std::collections::HashMap;

/// 28-element feature vector for GBDT model
#[derive(Debug, Clone)]
pub struct FeaturesV2 {
    pub values: Vec<f64>,
}

impl FeaturesV2 {
    pub fn new(values: Vec<f64>) -> Self {
        Self { values }
    }

    pub fn len(&self) -> usize {
        self.values.len()
    }

    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }
}

/// Feature names for interpretability (matches GBDT JSON format)
pub const FEATURE_NAMES: [&str; 28] = [
    // Warning metadata (6)
    "detector_bucket",
    "severity_ordinal",
    "confidence",
    "detector_category",
    "has_cwe",
    "entity_type",
    // Size (4)
    "function_loc",
    "file_loc",
    "function_count_in_file",
    "finding_line_span_norm",
    // Complexity (2)
    "cyclomatic_complexity",
    "max_nesting_depth",
    // Coupling (3)
    "fan_in",
    "fan_out",
    "scc_membership",
    // Git history (5)
    "file_age_log",
    "recent_churn",
    "developer_count",
    "unique_change_count",
    "is_recently_created",
    // Ownership (2)
    "major_contributor_pct",
    "minor_contributor_count",
    // Path/Context (3)
    "file_depth",
    "fp_path_indicator_count",
    "tp_path_indicator_count",
    // Cross-finding (3)
    "finding_density",
    "same_detector_findings",
    "historical_fp_rate",
];

/// Git history data for feature extraction (optional)
pub struct GitFeatures {
    /// File age in days
    pub file_age_days: f64,
    /// Lines added + deleted in last 30 days
    pub recent_churn: f64,
    /// Number of distinct authors
    pub developer_count: f64,
    /// Number of commits touching this file
    pub commit_count: f64,
    /// Whether file was created in last 7 days
    pub is_recently_created: bool,
    /// Percentage of lines by top contributor
    pub major_contributor_pct: f64,
    /// Number of minor contributors (< 5% of lines)
    pub minor_contributor_count: f64,
}

/// Cross-finding context for a file
pub struct CrossFindingFeatures {
    /// Total findings in this file
    pub findings_in_file: usize,
    /// File LOC (for density calculation)
    pub file_loc: u32,
    /// Same-detector findings in this file
    pub same_detector_count: usize,
    /// Historical FP rate for this detector (0.0 if unknown)
    pub historical_fp_rate: f64,
}

/// Extracts 28 evidence-backed features from a finding
pub struct FeatureExtractorV2 {
    /// Detector name → bucket index (hashed)
    detector_buckets: HashMap<String, u32>,
    /// Number of hash buckets for detector IDs
    num_buckets: u32,
    /// FP path patterns
    fp_path_patterns: Vec<&'static str>,
    /// TP path patterns
    tp_path_patterns: Vec<&'static str>,
}

impl FeatureExtractorV2 {
    pub fn new() -> Self {
        Self {
            detector_buckets: HashMap::new(),
            num_buckets: 32,
            fp_path_patterns: vec![
                "test", "tests", "spec", "specs", "__test__", "__tests__",
                "fixture", "fixtures", "mock", "mocks", "example", "examples",
                "demo", "sample", "vendor", "node_modules", "generated",
                "dist", "build", "benchmark", "docs",
            ],
            tp_path_patterns: vec![
                "src", "lib", "app", "api", "routes", "handlers",
                "controller", "service", "auth", "security",
            ],
        }
    }

    /// Hash a detector name to a bucket (feature 1)
    fn detector_bucket(&self, detector: &str) -> f64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut hasher = DefaultHasher::new();
        detector.hash(&mut hasher);
        (hasher.finish() % self.num_buckets as u64) as f64
    }

    /// Severity to ordinal (feature 2)
    fn severity_ordinal(severity: &Severity) -> f64 {
        match severity {
            Severity::Critical => 3.0,
            Severity::High => 2.0,
            Severity::Medium => 1.0,
            Severity::Low => 0.0,
            Severity::Info => 0.0,
        }
    }

    /// Detector category ordinal (feature 4)
    fn category_ordinal(detector: &str) -> f64 {
        use crate::classifier::thresholds::DetectorCategory;
        match DetectorCategory::from_detector(detector) {
            DetectorCategory::Security => 4.0,
            DetectorCategory::MachineLearning => 3.0,
            DetectorCategory::Performance => 2.0,
            DetectorCategory::CodeQuality => 1.0,
            DetectorCategory::Other => 0.0,
        }
    }

    /// Entity type: 0 = file, 1 = function, 2 = class (feature 6)
    fn entity_type(finding: &Finding, graph: &dyn GraphQuery) -> f64 {
        if let Some(file) = finding.affected_files.first() {
            let path = file.to_string_lossy();
            if let Some(start) = finding.line_start {
                // Check if finding is inside a function
                let funcs = graph.get_functions_in_file(&path);
                for func in &funcs {
                    if start >= func.line_start && start <= func.line_end {
                        return 1.0; // function
                    }
                }
                // Check if finding is inside a class
                let classes = graph.get_classes_in_file(&path);
                for cls in &classes {
                    if start >= cls.line_start && start <= cls.line_end {
                        return 2.0; // class
                    }
                }
            }
        }
        0.0 // file-level
    }

    /// File depth in directory tree (feature 23)
    fn file_depth(path: &str) -> f64 {
        path.matches('/').count() as f64
    }

    /// Count FP path indicators (feature 24)
    fn fp_path_count(&self, path: &str) -> f64 {
        let lower = path.to_lowercase();
        self.fp_path_patterns
            .iter()
            .filter(|p| lower.contains(*p))
            .count() as f64
    }

    /// Count TP path indicators (feature 25)
    fn tp_path_count(&self, path: &str) -> f64 {
        let lower = path.to_lowercase();
        self.tp_path_patterns
            .iter()
            .filter(|p| lower.contains(*p))
            .count() as f64
    }

    /// Extract all 28 features
    pub fn extract(
        &self,
        finding: &Finding,
        graph: &dyn GraphQuery,
        git: Option<&GitFeatures>,
        cross: Option<&CrossFindingFeatures>,
    ) -> FeaturesV2 {
        let path = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();

        let line_start = finding.line_start.unwrap_or(1);
        let line_end = finding.line_end.unwrap_or(line_start);
        let line_span = line_end.saturating_sub(line_start).saturating_add(1);

        // Find the containing function (if any) for size/complexity features
        let containing_func = finding.line_start.and_then(|start| {
            let funcs = graph.get_functions_in_file(&path);
            funcs.into_iter().find(|f| start >= f.line_start && start <= f.line_end)
        });

        // File-level metrics
        let file_funcs = graph.get_functions_in_file(&path);
        let file_loc = file_funcs.iter().map(|f| f.line_end.saturating_sub(f.line_start)).sum::<u32>().max(1);

        // Coupling: fan-in, fan-out, SCC membership
        let (fan_in, fan_out, in_scc) = if let Some(ref func) = containing_func {
            let fi = graph.call_fan_in(&func.qualified_name) as f64;
            let fo = graph.call_fan_out(&func.qualified_name) as f64;
            // SCC: check if this function's file is in any import cycle
            let cycles = graph.find_import_cycles();
            let scc = cycles.iter().any(|cycle| cycle.iter().any(|f| path.ends_with(f) || f.ends_with(&path)));
            (fi, fo, if scc { 1.0 } else { 0.0 })
        } else {
            (0.0, 0.0, 0.0)
        };

        let mut values = Vec::with_capacity(28);

        // === Warning Metadata (6) ===
        // 1. Detector bucket (hashed)
        values.push(self.detector_bucket(&finding.detector));
        // 2. Severity ordinal
        values.push(Self::severity_ordinal(&finding.severity));
        // 3. Confidence score (from finding if available, else 0.5)
        values.push(finding.confidence.unwrap_or(0.5) as f64);
        // 4. Detector category ordinal
        values.push(Self::category_ordinal(&finding.detector));
        // 5. Has CWE ID
        values.push(if finding.cwe_id.is_some() { 1.0 } else { 0.0 });
        // 6. Entity type
        values.push(Self::entity_type(finding, graph));

        // === Size (4) ===
        // 7. Function LOC
        values.push(containing_func.as_ref().map(|f| (f.line_end - f.line_start) as f64).unwrap_or(0.0));
        // 8. File LOC
        values.push(file_loc as f64);
        // 9. Function count in file
        values.push(file_funcs.len() as f64);
        // 10. Finding line span (normalized by function size)
        let func_size = containing_func.as_ref().map(|f| f.line_end - f.line_start).unwrap_or(1).max(1);
        values.push(line_span as f64 / func_size as f64);

        // === Complexity (2) ===
        // 11. Cyclomatic complexity
        values.push(containing_func.as_ref().and_then(|f| f.complexity()).map(|c| c as f64).unwrap_or(1.0));
        // 12. Max nesting depth
        values.push(containing_func.as_ref().and_then(|f| f.get_i64("nesting_depth")).map(|n| n as f64).unwrap_or(0.0));

        // === Coupling (3) ===
        // 13. Fan-in (callers)
        values.push(fan_in);
        // 14. Fan-out (callees)
        values.push(fan_out);
        // 15. SCC membership
        values.push(in_scc);

        // === Git History (5) ===
        // 16-20: from GitFeatures if available
        if let Some(g) = git {
            values.push((g.file_age_days + 1.0).ln()); // 16. log-scaled age
            values.push(g.recent_churn);                // 17. recent churn
            values.push(g.developer_count);             // 18. developer count
            values.push(g.commit_count);                // 19. unique change count
            values.push(if g.is_recently_created { 1.0 } else { 0.0 }); // 20. recently created
        } else {
            values.extend_from_slice(&[0.0; 5]);
        }

        // === Ownership (2) ===
        // 21-22: from GitFeatures if available
        if let Some(g) = git {
            values.push(g.major_contributor_pct);    // 21. major contributor %
            values.push(g.minor_contributor_count);  // 22. minor contributor count
        } else {
            values.extend_from_slice(&[0.0; 2]);
        }

        // === Path/Context (3) ===
        // 23. File depth
        values.push(Self::file_depth(&path));
        // 24. FP path indicator count
        values.push(self.fp_path_count(&path));
        // 25. TP path indicator count
        values.push(self.tp_path_count(&path));

        // === Cross-Finding (3) ===
        if let Some(cf) = cross {
            let kloc = (cf.file_loc as f64 / 1000.0).max(0.001);
            values.push(cf.findings_in_file as f64 / kloc);  // 26. finding density
            values.push(cf.same_detector_count as f64);       // 27. same-detector count
            values.push(cf.historical_fp_rate);               // 28. historical FP rate
        } else {
            values.extend_from_slice(&[0.0; 3]);
        }

        debug_assert_eq!(values.len(), 28, "Expected 28 features, got {}", values.len());

        FeaturesV2::new(values)
    }

    /// Number of features
    pub const fn feature_count() -> usize {
        28
    }
}

impl Default for FeatureExtractorV2 {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore};
    use std::path::PathBuf;

    fn make_finding(detector: &str, severity: Severity, path: &str, line: u32) -> Finding {
        Finding {
            id: "test-1".into(),
            detector: detector.into(),
            severity,
            title: format!("Test finding from {}", detector),
            description: "Test description".into(),
            affected_files: vec![PathBuf::from(path)],
            line_start: Some(line),
            line_end: Some(line + 10),
            cwe_id: Some("CWE-89".into()),
            ..Default::default()
        }
    }

    #[test]
    fn test_extracts_28_features() {
        let store = GraphStore::in_memory();

        // Add a function node
        let func = CodeNode::function("handle_request", "src/api/users.py")
            .with_qualified_name("users::handle_request")
            .with_lines(10, 60)
            .with_property("complexity", 8);
        store.add_node(func);

        let finding = make_finding("SQLInjectionDetector", Severity::High, "src/api/users.py", 25);
        let extractor = FeatureExtractorV2::new();
        let features = extractor.extract(&finding, &store, None, None);

        assert_eq!(features.len(), 28);

        // Severity should be 2.0 (High)
        assert_eq!(features.values[1], 2.0);

        // Has CWE should be 1.0
        assert_eq!(features.values[4], 1.0);

        // Function LOC should be 50 (60 - 10)
        assert_eq!(features.values[6], 50.0);
    }

    #[test]
    fn test_features_without_graph_context() {
        let store = GraphStore::in_memory();
        let finding = make_finding("DeadCodeDetector", Severity::Low, "tests/test_app.py", 5);
        let extractor = FeatureExtractorV2::new();
        let features = extractor.extract(&finding, &store, None, None);

        assert_eq!(features.len(), 28);

        // Severity should be 0.0 (Low)
        assert_eq!(features.values[1], 0.0);

        // FP path indicators should be > 0 (tests/)
        assert!(features.values[23] > 0.0);
    }

    #[test]
    fn test_git_features_populated() {
        let store = GraphStore::in_memory();
        let finding = make_finding("LongMethodsDetector", Severity::Medium, "src/app.py", 1);
        let extractor = FeatureExtractorV2::new();

        let git = GitFeatures {
            file_age_days: 365.0,
            recent_churn: 150.0,
            developer_count: 5.0,
            commit_count: 42.0,
            is_recently_created: false,
            major_contributor_pct: 0.7,
            minor_contributor_count: 3.0,
        };

        let features = extractor.extract(&finding, &store, Some(&git), None);

        // File age (log-scaled): ln(366) ≈ 5.9
        assert!(features.values[15] > 5.0);
        // Recent churn
        assert_eq!(features.values[16], 150.0);
        // Developer count
        assert_eq!(features.values[17], 5.0);
    }

    #[test]
    fn test_cross_finding_features() {
        let store = GraphStore::in_memory();
        let finding = make_finding("MagicNumbersDetector", Severity::Low, "src/utils.py", 10);
        let extractor = FeatureExtractorV2::new();

        let cross = CrossFindingFeatures {
            findings_in_file: 8,
            file_loc: 200,
            same_detector_count: 3,
            historical_fp_rate: 0.4,
        };

        let features = extractor.extract(&finding, &store, None, Some(&cross));

        // Finding density: 8 / 0.2 = 40.0
        assert!((features.values[25] - 40.0).abs() < 0.01);
        // Same-detector count
        assert_eq!(features.values[26], 3.0);
        // Historical FP rate
        assert!((features.values[27] - 0.4).abs() < 0.01);
    }

    #[test]
    fn test_feature_names_match_count() {
        assert_eq!(FEATURE_NAMES.len(), FeatureExtractorV2::feature_count());
    }
}
```

**Step 2: Register the module**

In `repotoire-cli/src/classifier/mod.rs`, add after line 13 (`mod features;`):

```rust
pub mod features_v2;
```

And add to the pub use section (after line 19):

```rust
pub use features_v2::{FeatureExtractorV2, FeaturesV2, GitFeatures, CrossFindingFeatures};
```

**Step 3: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles. Note: `Finding.confidence` may not exist yet — if so, add `pub confidence: Option<f32>` to `Finding` in `src/models.rs`.

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test classifier::features_v2`
Expected: All 5 tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/classifier/features_v2.rs repotoire-cli/src/classifier/mod.rs
git commit -m "feat: add 28-feature evidence-backed extractor (features_v2)"
```

---

## Task 3: Create `gbdt_model.rs` — GBDT Model Loading and Inference

**Files:**
- Create: `repotoire-cli/src/classifier/gbdt_model.rs`
- Modify: `repotoire-cli/src/classifier/mod.rs` (add module + re-exports)

**Step 1: Write the code with tests**

Create `repotoire-cli/src/classifier/gbdt_model.rs`:

```rust
//! GBDT model for FP classification
//!
//! Loads XGBoost-format JSON models via the `gbdt` crate.
//! Inference: microseconds per finding, no GPU.
//! Model can be embedded via include_bytes! or loaded from disk.

use gbdt::config::Config;
use gbdt::decision_tree::Data;
use gbdt::gradient_boost::GBDT;
use super::features_v2::FeaturesV2;
use std::path::Path;

/// GBDT-based classifier wrapping the gbdt crate
pub struct GbdtClassifier {
    model: GBDT,
}

/// Prediction from the GBDT model
#[derive(Debug, Clone)]
pub struct GbdtPrediction {
    /// Probability of being a true positive [0, 1]
    pub tp_probability: f64,
    /// Actionability score [0, 100]
    pub actionability_score: f64,
    /// Binary verdict
    pub is_true_positive: bool,
}

impl GbdtClassifier {
    /// Load model from XGBoost JSON file
    pub fn load(path: &Path) -> Result<Self, String> {
        let model = GBDT::from_xgoost_dump(path, "binary:logistic")
            .map_err(|e| format!("Failed to load GBDT model: {:?}", e))?;
        Ok(Self { model })
    }

    /// Load model from JSON string (for embedded models)
    pub fn from_json(json: &str) -> Result<Self, String> {
        // Write to temp file since gbdt crate needs a path
        let tmp = std::env::temp_dir().join("repotoire_model_load.json");
        std::fs::write(&tmp, json)
            .map_err(|e| format!("Failed to write temp model: {}", e))?;
        let result = Self::load(&tmp);
        let _ = std::fs::remove_file(&tmp);
        result
    }

    /// Run inference on a single feature vector
    pub fn predict(&self, features: &FeaturesV2) -> GbdtPrediction {
        let data = Data::new_test_data(features.values.clone(), None);
        let predictions = self.model.predict(&[data]);
        let tp_prob = predictions.first().copied().unwrap_or(0.5).clamp(0.0, 1.0);

        GbdtPrediction {
            tp_probability: tp_prob,
            actionability_score: tp_prob * 100.0,
            is_true_positive: tp_prob >= 0.5,
        }
    }

    /// Batch inference
    pub fn predict_batch(&self, features: &[FeaturesV2]) -> Vec<GbdtPrediction> {
        let data: Vec<Data> = features
            .iter()
            .map(|f| Data::new_test_data(f.values.clone(), None))
            .collect();
        let predictions = self.model.predict(&data);
        predictions
            .into_iter()
            .map(|tp_prob| {
                let tp_prob = tp_prob.clamp(0.0, 1.0);
                GbdtPrediction {
                    tp_probability: tp_prob,
                    actionability_score: tp_prob * 100.0,
                    is_true_positive: tp_prob >= 0.5,
                }
            })
            .collect()
    }
}

/// Train a GBDT model from labeled data (pure Rust, no Python needed)
pub fn train_gbdt(
    features: &[FeaturesV2],
    labels: &[bool],
    num_trees: u32,
    max_depth: u32,
    learning_rate: f64,
) -> Result<GBDT, String> {
    let mut cfg = Config::new();
    cfg.set_feature_size(features.first().map(|f| f.len()).unwrap_or(28));
    cfg.set_max_depth(max_depth);
    cfg.set_iterations(num_trees as usize);
    cfg.set_shrinkage(learning_rate);
    cfg.set_loss("LogLikelood"); // gbdt crate spelling
    cfg.set_debug(false);
    cfg.set_training_optimization_level(2);

    let data: Vec<Data> = features
        .iter()
        .zip(labels.iter())
        .map(|(f, &label)| {
            Data::new_training_data(
                f.values.clone(),
                1.0, // weight
                if label { 1.0 } else { 0.0 },
                None,
            )
        })
        .collect();

    let mut model = GBDT::new(&cfg);
    model.fit(&data);

    Ok(model)
}

/// Save a trained GBDT model to JSON
pub fn save_model(model: &GBDT, path: &Path) -> Result<(), String> {
    model
        .save_model(path)
        .map_err(|e| format!("Failed to save model: {:?}", e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_train_and_predict() {
        // Create simple training data
        let features: Vec<FeaturesV2> = (0..50)
            .map(|i| {
                let mut vals = vec![0.0; 28];
                if i % 2 == 0 {
                    // TP pattern: high severity, has CWE, in src/
                    vals[1] = 2.0; // severity
                    vals[4] = 1.0; // has_cwe
                    vals[24] = 2.0; // tp_path
                } else {
                    // FP pattern: low severity, test path
                    vals[1] = 0.0;
                    vals[23] = 3.0; // fp_path
                }
                FeaturesV2::new(vals)
            })
            .collect();

        let labels: Vec<bool> = (0..50).map(|i| i % 2 == 0).collect();

        let model = train_gbdt(&features, &labels, 10, 3, 0.1).expect("training should succeed");

        // Predict on a TP-like sample
        let mut tp_vals = vec![0.0; 28];
        tp_vals[1] = 2.0;
        tp_vals[4] = 1.0;
        tp_vals[24] = 2.0;
        let tp_data = Data::new_test_data(tp_vals, None);
        let predictions = model.predict(&[tp_data]);
        assert!(!predictions.is_empty());
    }

    #[test]
    fn test_gbdt_prediction_struct() {
        let pred = GbdtPrediction {
            tp_probability: 0.8,
            actionability_score: 80.0,
            is_true_positive: true,
        };
        assert!(pred.is_true_positive);
        assert_eq!(pred.actionability_score, 80.0);
    }
}
```

**Step 2: Register the module**

In `repotoire-cli/src/classifier/mod.rs`, add:
```rust
pub mod gbdt_model;
```

And add to pub use:
```rust
pub use gbdt_model::{GbdtClassifier, GbdtPrediction};
```

**Step 3: Verify it compiles**

Run: `cd repotoire-cli && cargo check`
Expected: Compiles. The `gbdt` crate API may need minor adjustments (method names, `Config` API). Verify against `gbdt` docs and adjust.

**Step 4: Run tests**

Run: `cd repotoire-cli && cargo test classifier::gbdt_model`
Expected: 2 tests pass

**Step 5: Commit**

```bash
git add repotoire-cli/src/classifier/gbdt_model.rs repotoire-cli/src/classifier/mod.rs
git commit -m "feat: add GBDT model wrapper with train/predict/save"
```

---

## Task 4: Create `bootstrap.rs` — Git-Mined Label Generation

**Files:**
- Create: `repotoire-cli/src/classifier/bootstrap.rs`
- Modify: `repotoire-cli/src/classifier/mod.rs` (add module)

**Step 1: Write the code with tests**

Create `repotoire-cli/src/classifier/bootstrap.rs`:

```rust
//! Git-mined weak label generation
//!
//! Mines git history to generate weak labels for training:
//! - Findings on code changed in "fix" commits → likely TP (weight 0.7)
//! - Findings on code stable 6+ months → likely FP (weight 0.5)

use crate::models::Finding;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// A weak label mined from git history
#[derive(Debug, Clone)]
pub struct WeakLabel {
    /// Finding ID this label applies to
    pub finding_id: String,
    /// Detector name
    pub detector: String,
    /// File path
    pub file_path: String,
    /// Line number
    pub line_start: Option<u32>,
    /// Whether this is likely a true positive
    pub is_true_positive: bool,
    /// Confidence weight (0.0 - 1.0)
    /// User labels = 1.0, fix-commit labels = 0.7, stability labels = 0.5
    pub weight: f64,
    /// Source of the label
    pub source: LabelSource,
}

/// How the label was generated
#[derive(Debug, Clone, PartialEq)]
pub enum LabelSource {
    /// User provided via `repotoire feedback`
    User,
    /// File was changed in a fix commit
    FixCommit,
    /// File has been stable for 6+ months
    StableCode,
}

/// Mine weak labels from git history
pub fn mine_labels(
    findings: &[Finding],
    repo_path: &Path,
) -> Vec<WeakLabel> {
    let mut labels = Vec::new();

    // Try to open git repo
    let repo = match git2::Repository::discover(repo_path) {
        Ok(r) => r,
        Err(_) => return labels, // No git repo, no labels
    };

    // Collect fix-commit files and stable files
    let fix_files = find_fix_commit_files(&repo);
    let stable_files = find_stable_files(&repo);

    for finding in findings {
        let file_path = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();

        // Check if file was changed in a fix commit
        if fix_files.contains(&file_path) {
            labels.push(WeakLabel {
                finding_id: finding.id.clone(),
                detector: finding.detector.clone(),
                file_path: file_path.clone(),
                line_start: finding.line_start,
                is_true_positive: true,
                weight: 0.7,
                source: LabelSource::FixCommit,
            });
            continue; // Don't double-label
        }

        // Check if file has been stable
        if stable_files.contains(&file_path) {
            labels.push(WeakLabel {
                finding_id: finding.id.clone(),
                detector: finding.detector.clone(),
                file_path,
                line_start: finding.line_start,
                is_true_positive: false,
                weight: 0.5,
                source: LabelSource::StableCode,
            });
        }
    }

    labels
}

/// Find files changed in commits with "fix" in the message
fn find_fix_commit_files(repo: &git2::Repository) -> HashSet<String> {
    let mut fix_files = HashSet::new();

    let mut revwalk = match repo.revwalk() {
        Ok(rw) => rw,
        Err(_) => return fix_files,
    };

    if revwalk.push_head().is_err() {
        return fix_files;
    }

    revwalk.set_sorting(git2::Sort::TIME).ok();

    let fix_patterns = ["fix", "bug", "patch", "hotfix", "resolve"];
    let mut count = 0;

    for oid in revwalk {
        let oid = match oid {
            Ok(o) => o,
            Err(_) => continue,
        };

        // Limit to last 500 commits for performance
        count += 1;
        if count > 500 {
            break;
        }

        let commit = match repo.find_commit(oid) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let message = commit.message().unwrap_or("").to_lowercase();
        let is_fix = fix_patterns.iter().any(|p| message.contains(p));

        if !is_fix {
            continue;
        }

        // Get files changed in this commit
        if let Ok(tree) = commit.tree() {
            if let Some(parent) = commit.parents().next() {
                if let Ok(parent_tree) = parent.tree() {
                    if let Ok(diff) = repo.diff_tree_to_tree(
                        Some(&parent_tree),
                        Some(&tree),
                        None,
                    ) {
                        for delta in diff.deltas() {
                            if let Some(path) = delta.new_file().path() {
                                fix_files.insert(path.to_string_lossy().to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    fix_files
}

/// Find files that haven't been modified in 6+ months
fn find_stable_files(repo: &git2::Repository) -> HashSet<String> {
    let mut file_last_modified: HashMap<String, i64> = HashMap::new();

    let mut revwalk = match repo.revwalk() {
        Ok(rw) => rw,
        Err(_) => return HashSet::new(),
    };

    if revwalk.push_head().is_err() {
        return HashSet::new();
    }

    revwalk.set_sorting(git2::Sort::TIME).ok();

    let mut count = 0;
    for oid in revwalk {
        let oid = match oid {
            Ok(o) => o,
            Err(_) => continue,
        };

        count += 1;
        if count > 500 {
            break;
        }

        let commit = match repo.find_commit(oid) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let timestamp = commit.time().seconds();

        if let Ok(tree) = commit.tree() {
            if let Some(parent) = commit.parents().next() {
                if let Ok(parent_tree) = parent.tree() {
                    if let Ok(diff) = repo.diff_tree_to_tree(
                        Some(&parent_tree),
                        Some(&tree),
                        None,
                    ) {
                        for delta in diff.deltas() {
                            if let Some(path) = delta.new_file().path() {
                                let path_str = path.to_string_lossy().to_string();
                                file_last_modified
                                    .entry(path_str)
                                    .and_modify(|t| {
                                        if timestamp > *t {
                                            *t = timestamp;
                                        }
                                    })
                                    .or_insert(timestamp);
                            }
                        }
                    }
                }
            }
        }
    }

    // Files not modified in 6+ months (180 days)
    let six_months_ago = chrono::Utc::now().timestamp() - (180 * 24 * 60 * 60);
    file_last_modified
        .into_iter()
        .filter(|(_, last_mod)| *last_mod < six_months_ago)
        .map(|(path, _)| path)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_weak_label_creation() {
        let label = WeakLabel {
            finding_id: "f1".into(),
            detector: "SQLInjectionDetector".into(),
            file_path: "src/api.py".into(),
            line_start: Some(10),
            is_true_positive: true,
            weight: 0.7,
            source: LabelSource::FixCommit,
        };
        assert!(label.is_true_positive);
        assert_eq!(label.weight, 0.7);
        assert_eq!(label.source, LabelSource::FixCommit);
    }

    #[test]
    fn test_mine_labels_no_repo() {
        let findings = vec![Finding {
            id: "f1".into(),
            detector: "TestDetector".into(),
            affected_files: vec![std::path::PathBuf::from("src/main.rs")],
            ..Default::default()
        }];

        // Non-existent path → no labels (graceful fallback)
        let labels = mine_labels(&findings, Path::new("/nonexistent/path"));
        assert!(labels.is_empty());
    }
}
```

**Step 2: Register the module**

In `repotoire-cli/src/classifier/mod.rs`:
```rust
pub mod bootstrap;
```

**Step 3: Verify and test**

Run: `cd repotoire-cli && cargo check && cargo test classifier::bootstrap`
Expected: Compiles and 2 tests pass

**Step 4: Commit**

```bash
git add repotoire-cli/src/classifier/bootstrap.rs repotoire-cli/src/classifier/mod.rs
git commit -m "feat: add git-mined weak label generation (bootstrap)"
```

---

## Task 5: Create `debt.rs` — Per-File Debt Scoring

**Files:**
- Create: `repotoire-cli/src/classifier/debt.rs`
- Modify: `repotoire-cli/src/classifier/mod.rs` (add module)

**Step 1: Write the code with tests**

Create `repotoire-cli/src/classifier/debt.rs`:

```rust
//! Per-file technical debt scoring
//!
//! Computes a risk score (0-100) for each file based on:
//! - Finding density (weighted by severity)
//! - Coupling score (fan-in + fan-out + SCC)
//! - Churn score (recent change velocity)
//! - Ownership dispersion (many authors = higher risk)
//! - Age factor (recently created = higher risk)

use crate::graph::GraphQuery;
use crate::models::{Finding, Severity};
use std::collections::HashMap;

/// Debt score for a single file
#[derive(Debug, Clone)]
pub struct FileDebt {
    /// File path
    pub file_path: String,
    /// Overall risk score 0-100
    pub risk_score: f64,
    /// Finding density component
    pub finding_density: f64,
    /// Coupling component
    pub coupling_score: f64,
    /// Churn component
    pub churn_score: f64,
    /// Ownership dispersion component
    pub ownership_dispersion: f64,
    /// Age factor component
    pub age_factor: f64,
    /// Trend indicator
    pub trend: DebtTrend,
}

/// Trend indicator for debt
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum DebtTrend {
    /// Getting worse
    Rising,
    /// Getting better
    Falling,
    /// Stable
    Stable,
}

impl std::fmt::Display for DebtTrend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DebtTrend::Rising => write!(f, "↑"),
            DebtTrend::Falling => write!(f, "↓"),
            DebtTrend::Stable => write!(f, "→"),
        }
    }
}

/// Weights for debt components (sum to 1.0)
#[derive(Debug, Clone)]
pub struct DebtWeights {
    pub finding_density: f64,
    pub coupling: f64,
    pub churn: f64,
    pub ownership: f64,
    pub age: f64,
}

impl Default for DebtWeights {
    fn default() -> Self {
        Self {
            finding_density: 0.35,
            coupling: 0.25,
            churn: 0.20,
            ownership: 0.10,
            age: 0.10,
        }
    }
}

/// Compute per-file debt scores
pub fn compute_debt(
    findings: &[Finding],
    graph: &dyn GraphQuery,
    git_churn: &HashMap<String, (f64, usize, f64)>, // path → (churn, authors, age_days)
    weights: &DebtWeights,
) -> Vec<FileDebt> {
    // Group findings by file
    let mut file_findings: HashMap<String, Vec<&Finding>> = HashMap::new();
    for finding in findings {
        if let Some(path) = finding.affected_files.first() {
            let path_str = path.to_string_lossy().to_string();
            file_findings.entry(path_str).or_default().push(finding);
        }
    }

    // Get all files from graph
    let all_files: Vec<String> = graph
        .get_files()
        .into_iter()
        .map(|f| f.file_path.clone())
        .collect();

    let mut debts = Vec::new();

    for file_path in &all_files {
        let file_funcs = graph.get_functions_in_file(file_path);
        let file_loc: u32 = file_funcs
            .iter()
            .map(|f| f.line_end.saturating_sub(f.line_start))
            .sum::<u32>()
            .max(1);
        let kloc = file_loc as f64 / 1000.0;

        // 1. Finding density (severity-weighted findings per kLOC)
        let findings_for_file = file_findings.get(file_path.as_str());
        let finding_density = if let Some(ff) = findings_for_file {
            let weighted_count: f64 = ff
                .iter()
                .map(|f| match f.severity {
                    Severity::Critical => 4.0,
                    Severity::High => 3.0,
                    Severity::Medium => 2.0,
                    Severity::Low => 1.0,
                    Severity::Info => 0.5,
                })
                .sum();
            (weighted_count / kloc.max(0.1)).min(100.0)
        } else {
            0.0
        };

        // 2. Coupling score (fan-in + fan-out + SCC membership)
        let total_fan_in: usize = file_funcs.iter().map(|f| graph.call_fan_in(&f.qualified_name)).sum();
        let total_fan_out: usize = file_funcs.iter().map(|f| graph.call_fan_out(&f.qualified_name)).sum();
        let coupling_score = ((total_fan_in + total_fan_out) as f64).min(100.0);

        // 3. Churn score, 4. Ownership, 5. Age
        let (churn_score, ownership_dispersion, age_factor) =
            if let Some(&(churn, authors, age_days)) = git_churn.get(file_path.as_str()) {
                let churn_norm = churn.min(100.0);
                let ownership = (authors as f64).min(20.0) * 5.0; // max 100
                let age = if age_days < 7.0 { 80.0 } else if age_days < 30.0 { 40.0 } else { 0.0 };
                (churn_norm, ownership, age)
            } else {
                (0.0, 0.0, 0.0)
            };

        // Weighted sum
        let risk_score = (weights.finding_density * finding_density
            + weights.coupling * coupling_score
            + weights.churn * churn_score
            + weights.ownership * ownership_dispersion
            + weights.age * age_factor)
            .clamp(0.0, 100.0);

        // Skip files with zero risk
        if risk_score < 0.01 {
            continue;
        }

        debts.push(FileDebt {
            file_path: file_path.clone(),
            risk_score,
            finding_density,
            coupling_score,
            churn_score,
            ownership_dispersion,
            age_factor,
            trend: DebtTrend::Stable, // trend requires historical data
        });
    }

    // Sort by risk (highest first)
    debts.sort_by(|a, b| b.risk_score.partial_cmp(&a.risk_score).unwrap_or(std::cmp::Ordering::Equal));
    debts
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, GraphStore};
    use std::path::PathBuf;

    #[test]
    fn test_debt_scoring_basic() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::file("src/api.py"));
        let func = CodeNode::function("handle", "src/api.py")
            .with_qualified_name("api::handle")
            .with_lines(1, 100);
        store.add_node(func);

        let findings = vec![
            Finding {
                id: "f1".into(),
                detector: "SQLInjection".into(),
                severity: Severity::High,
                affected_files: vec![PathBuf::from("src/api.py")],
                ..Default::default()
            },
            Finding {
                id: "f2".into(),
                detector: "MagicNumbers".into(),
                severity: Severity::Low,
                affected_files: vec![PathBuf::from("src/api.py")],
                ..Default::default()
            },
        ];

        let git_churn = HashMap::new();
        let weights = DebtWeights::default();
        let debts = compute_debt(&findings, &store, &git_churn, &weights);

        assert!(!debts.is_empty());
        assert!(debts[0].risk_score > 0.0);
        assert!(debts[0].finding_density > 0.0);
    }

    #[test]
    fn test_debt_scoring_empty() {
        let store = GraphStore::in_memory();
        let findings: Vec<Finding> = vec![];
        let git_churn = HashMap::new();
        let weights = DebtWeights::default();
        let debts = compute_debt(&findings, &store, &git_churn, &weights);
        assert!(debts.is_empty());
    }

    #[test]
    fn test_debt_trend_display() {
        assert_eq!(format!("{}", DebtTrend::Rising), "↑");
        assert_eq!(format!("{}", DebtTrend::Falling), "↓");
        assert_eq!(format!("{}", DebtTrend::Stable), "→");
    }
}
```

**Step 2: Register module and verify**

Add `pub mod debt;` and `pub use debt::{compute_debt, FileDebt, DebtTrend, DebtWeights};` to `mod.rs`.

Run: `cd repotoire-cli && cargo check && cargo test classifier::debt`
Expected: 3 tests pass

**Step 3: Commit**

```bash
git add repotoire-cli/src/classifier/debt.rs repotoire-cli/src/classifier/mod.rs
git commit -m "feat: add per-file debt scoring with weighted components"
```

---

## Task 6: Update `train.rs` — GBDT Training Integration

**Files:**
- Modify: `repotoire-cli/src/classifier/train.rs`

**Step 1: Add GBDT training path**

Add a new function to `train.rs` that trains a GBDT model instead of the MLP. Keep the existing MLP `train()` function as a fallback.

Add at the end of `train.rs` (before `#[cfg(test)]`):

```rust
/// Train a GBDT classifier on labeled + weak-labeled data
pub fn train_gbdt_model(config: &TrainConfig) -> Result<TrainResult, String> {
    use super::features_v2::FeatureExtractorV2;
    use super::gbdt_model;

    let collector = FeedbackCollector::default();
    let examples = collector
        .load_all()
        .map_err(|e| format!("Failed to load training data: {}", e))?;

    if examples.is_empty() {
        return Err("No training data found. Use `repotoire feedback` to label findings.".into());
    }

    if examples.len() < 10 {
        return Err(format!(
            "Need at least 10 labeled examples, found {}.",
            examples.len()
        ));
    }

    tracing::info!("Loaded {} labeled examples for GBDT training", examples.len());

    // Convert to FeaturesV2 (simplified — without graph/git context for training)
    // The V2 extractor can work with just finding metadata when graph/git aren't available
    let extractor = FeatureExtractorV2::new();
    let graph = crate::graph::GraphStore::in_memory(); // empty graph for offline training

    let features: Vec<_> = examples
        .iter()
        .map(|ex| {
            let finding = labeled_to_finding(ex);
            extractor.extract(&finding, &graph, None, None)
        })
        .collect();
    let labels: Vec<bool> = examples.iter().map(|ex| ex.is_true_positive).collect();

    // Train GBDT
    let model = gbdt_model::train_gbdt(
        &features,
        &labels,
        100,  // num_trees
        6,    // max_depth
        0.1,  // learning_rate
    )?;

    // Save model
    let model_path = dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("repotoire")
        .join("gbdt_model.json");

    if let Some(parent) = model_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create model directory: {}", e))?;
    }

    gbdt_model::save_model(&model, &model_path)?;
    tracing::info!("GBDT model saved to {}", model_path.display());

    Ok(TrainResult {
        train_loss: 0.0, // GBDT doesn't report loss the same way
        val_loss: None,
        train_accuracy: 0.0, // Would need eval pass
        val_accuracy: None,
        epochs: 100,
        model_path,
    })
}
```

**Step 2: Verify and test**

Run: `cd repotoire-cli && cargo check && cargo test classifier::train`
Expected: Compiles and existing tests still pass

**Step 3: Commit**

```bash
git add repotoire-cli/src/classifier/train.rs
git commit -m "feat: add GBDT training path alongside existing MLP"
```

---

## Task 7: Update `postprocess.rs` — Use GBDT Model in FP Filter

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs:215-260`

**Step 1: Add GBDT model loading and fallback**

Update `filter_false_positives()` to try loading a trained GBDT model first, falling back to the heuristic classifier:

Replace the existing `filter_false_positives` function (lines 215-260) with:

```rust
/// FP filtering with GBDT model (primary) or heuristic fallback.
///
/// Tries to load a trained GBDT model first. If unavailable,
/// falls back to the heuristic classifier with category thresholds.
fn filter_false_positives(findings: &mut Vec<Finding>, graph: &dyn crate::graph::GraphQuery) {
    use crate::classifier::{
        model::HeuristicClassifier, CategoryThresholds, DetectorCategory, FeatureExtractor,
        features_v2::FeatureExtractorV2, gbdt_model::GbdtClassifier,
    };

    let thresholds = CategoryThresholds::default();
    let before_count = findings.len();
    let mut filtered_by_category: std::collections::HashMap<DetectorCategory, usize> =
        std::collections::HashMap::new();

    // Try to load GBDT model
    let model_path = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("repotoire")
        .join("gbdt_model.json");

    if let Ok(gbdt) = GbdtClassifier::load(&model_path) {
        // GBDT path: use V2 features
        let extractor = FeatureExtractorV2::new();
        tracing::info!("Using trained GBDT model for FP filtering");

        findings.retain(|f| {
            let features = extractor.extract(f, graph, None, None);
            let pred = gbdt.predict(&features);
            let category = DetectorCategory::from_detector(&f.detector);
            let config = thresholds.get_category(category);

            if pred.tp_probability >= config.filter_threshold as f64 {
                true
            } else {
                *filtered_by_category.entry(category).or_insert(0) += 1;
                false
            }
        });
    } else {
        // Heuristic fallback (existing behavior)
        let extractor = FeatureExtractor::new();
        let classifier = HeuristicClassifier;

        findings.retain(|f| {
            let features = extractor.extract(f);
            let tp_prob = classifier.score(&features);
            let category = DetectorCategory::from_detector(&f.detector);
            let config = thresholds.get_category(category);

            if tp_prob >= config.filter_threshold {
                true
            } else {
                *filtered_by_category.entry(category).or_insert(0) += 1;
                false
            }
        });
    }

    let total_filtered = before_count - findings.len();
    if total_filtered > 0 {
        tracing::info!(
            "FP classifier filtered {} findings (Security: {}, Quality: {}, ML: {}, Perf: {}, Other: {})",
            total_filtered,
            filtered_by_category.get(&DetectorCategory::Security).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::CodeQuality).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::MachineLearning).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::Performance).unwrap_or(&0),
            filtered_by_category.get(&DetectorCategory::Other).unwrap_or(&0),
        );
    }
}
```

**Important**: The function signature changes to accept `graph: &dyn GraphQuery`. Update the call site (around line 85 in `postprocess.rs`) to pass the graph reference.

**Step 2: Verify**

Run: `cd repotoire-cli && cargo check`
Expected: May need to update the call site. Find where `filter_false_positives(findings)` is called and add the graph argument.

**Step 3: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass (heuristic fallback maintains existing behavior)

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "feat: use GBDT model in FP filter with heuristic fallback"
```

---

## Task 8: Add `--rank` Flag to Analyze Command

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs` (add flag to Analyze command)
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs` (add ranking logic)

**Step 1: Add the flag**

In `repotoire-cli/src/cli/mod.rs`, find the `Analyze` command struct and add:

```rust
/// Sort findings by actionability score instead of severity
#[arg(long)]
rank: bool,
```

**Step 2: Add ranking logic**

In `postprocess.rs`, add a new function:

```rust
/// Rank findings by actionability score (0-100)
///
/// Uses the GBDT model's TP probability as actionability score.
/// Falls back to severity-based ranking if no model is available.
fn rank_findings(findings: &mut Vec<Finding>, graph: &dyn crate::graph::GraphQuery) {
    use crate::classifier::{
        model::HeuristicClassifier, FeatureExtractor,
        features_v2::FeatureExtractorV2, gbdt_model::GbdtClassifier,
    };

    let model_path = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("repotoire")
        .join("gbdt_model.json");

    if let Ok(gbdt) = GbdtClassifier::load(&model_path) {
        let extractor = FeatureExtractorV2::new();
        let mut scored: Vec<(f64, usize)> = findings
            .iter()
            .enumerate()
            .map(|(i, f)| {
                let features = extractor.extract(f, graph, None, None);
                let pred = gbdt.predict(&features);
                (pred.actionability_score, i)
            })
            .collect();
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        let reordered: Vec<Finding> = scored.into_iter().map(|(_, i)| findings[i].clone()).collect();
        *findings = reordered;
    } else {
        // Fallback: sort by heuristic score
        let extractor = FeatureExtractor::new();
        let classifier = HeuristicClassifier;
        let mut scored: Vec<(f32, usize)> = findings
            .iter()
            .enumerate()
            .map(|(i, f)| {
                let features = extractor.extract(f);
                (classifier.score(&features), i)
            })
            .collect();
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        let reordered: Vec<Finding> = scored.into_iter().map(|(_, i)| findings[i].clone()).collect();
        *findings = reordered;
    }
}
```

Call this function from the postprocess pipeline when `--rank` is set.

**Step 3: Verify and test**

Run: `cd repotoire-cli && cargo check && cargo test`

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "feat: add --rank flag to sort findings by actionability"
```

---

## Task 9: Add `repotoire debt` Command

**Files:**
- Create: `repotoire-cli/src/cli/debt.rs`
- Modify: `repotoire-cli/src/cli/mod.rs` (add Debt command variant)
- Modify: `repotoire-cli/src/cli/mod.rs` (add match arm in run())

**Step 1: Create the debt command handler**

Create `repotoire-cli/src/cli/debt.rs`:

```rust
//! `repotoire debt` command
//!
//! Shows per-file technical debt risk scores.

use crate::classifier::debt::{compute_debt, DebtWeights};
use crate::graph::GraphQuery;
use crate::models::Finding;
use std::collections::HashMap;

/// Run the debt analysis and display results
pub fn run_debt(
    findings: &[Finding],
    graph: &dyn GraphQuery,
    path_filter: Option<&str>,
    top_n: usize,
) {
    let git_churn = HashMap::new(); // TODO: populate from git history
    let weights = DebtWeights::default();
    let mut debts = compute_debt(findings, graph, &git_churn, &weights);

    // Filter by path if specified
    if let Some(filter) = path_filter {
        debts.retain(|d| d.file_path.contains(filter));
    }

    // Truncate to top N
    debts.truncate(top_n);

    if debts.is_empty() {
        println!("No debt hotspots found.");
        return;
    }

    // Display header
    println!();
    println!("  {:<50} {:>6} {:>8} {:>8} {:>6} {:>5}",
        "File", "Score", "Density", "Couple", "Churn", "Trend");
    println!("  {}", "─".repeat(85));

    for debt in &debts {
        let short_path = if debt.file_path.len() > 48 {
            format!("…{}", &debt.file_path[debt.file_path.len()-47..])
        } else {
            debt.file_path.clone()
        };

        let color = if debt.risk_score >= 70.0 {
            "\x1b[31m" // red
        } else if debt.risk_score >= 40.0 {
            "\x1b[33m" // yellow
        } else {
            "\x1b[32m" // green
        };

        println!("  {:<50} {}{:>5.1}\x1b[0m {:>8.1} {:>8.1} {:>6.1} {:>5}",
            short_path,
            color,
            debt.risk_score,
            debt.finding_density,
            debt.coupling_score,
            debt.churn_score,
            debt.trend,
        );
    }

    println!();
    println!("  Showing top {} files by debt risk score", debts.len());
}
```

**Step 2: Add Debt command to CLI**

In `repotoire-cli/src/cli/mod.rs`, add to the `Commands` enum:

```rust
/// Show per-file technical debt risk scores
Debt {
    /// Filter to a specific path
    #[arg(long)]
    path: Option<String>,

    /// Number of files to show
    #[arg(long, default_value = "20")]
    top: usize,
},
```

Add the match arm in the `run()` function to handle the Debt command.

**Step 3: Verify and test**

Run: `cd repotoire-cli && cargo check && cargo test`

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/debt.rs repotoire-cli/src/cli/mod.rs
git commit -m "feat: add 'repotoire debt' command for per-file risk scoring"
```

---

## Task 10: Add MCP `repotoire_predict_debt` Tool

**Files:**
- Modify: `repotoire-cli/src/mcp/tools/` (add debt prediction tool)

**Step 1: Add the tool**

Find the MCP tools directory and add a debt prediction tool that calls `compute_debt()` and returns JSON results. Follow the existing tool patterns (e.g., `repotoire_get_hotspots`).

**Step 2: Register the tool**

Add the tool to the MCP server's tool registry.

**Step 3: Verify**

Run: `cd repotoire-cli && cargo check && cargo test`

**Step 4: Commit**

```bash
git add repotoire-cli/src/mcp/
git commit -m "feat: add repotoire_predict_debt MCP tool (FREE tier)"
```

---

## Task 11: Add `confidence` Field to `Finding` Model

**Files:**
- Modify: `repotoire-cli/src/models.rs` (add `confidence: Option<f32>` to `Finding`)

This is needed by `features_v2.rs` (feature 3: confidence score). Add the field with `#[serde(default)]` to maintain backward compatibility.

**Step 1: Add the field**

In `repotoire-cli/src/models.rs`, add to the `Finding` struct:

```rust
/// Confidence score from detector (0.0-1.0)
#[serde(default)]
pub confidence: Option<f32>,
```

**Step 2: Verify**

Run: `cd repotoire-cli && cargo check && cargo test`
Expected: All tests pass (serde default handles missing field)

**Step 3: Commit**

```bash
git add repotoire-cli/src/models.rs
git commit -m "feat: add confidence field to Finding model"
```

---

## Task 12: Create Python Training Script

**Files:**
- Create: `scripts/train_model.py`

This script is used to train the seed model in Python (XGBoost) and export to JSON for the `gbdt` crate. It's a developer tool, not part of the Rust binary.

**Step 1: Create the script**

```python
#!/usr/bin/env python3
"""Train the seed GBDT model for repotoire's FP classifier.

Usage:
    uv run scripts/train_model.py --data labeled_findings.json --output repotoire-cli/models/seed_model.json

Trains XGBoost on manually labeled findings, exports to JSON format
compatible with gbdt-rs (XGBoost dump format).
"""

import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser(description="Train seed GBDT model")
    parser.add_argument("--data", required=True, help="Path to labeled findings JSON")
    parser.add_argument("--output", required=True, help="Output model JSON path")
    parser.add_argument("--trees", type=int, default=100, help="Number of trees")
    parser.add_argument("--depth", type=int, default=6, help="Max tree depth")
    parser.add_argument("--lr", type=float, default=0.1, help="Learning rate")
    args = parser.parse_args()

    try:
        import xgboost as xgb
        import numpy as np
    except ImportError:
        print("Install dependencies: uv pip install xgboost numpy")
        sys.exit(1)

    # Load labeled data
    with open(args.data) as f:
        data = json.load(f)

    if not data:
        print("No training data found")
        sys.exit(1)

    # Extract features and labels
    X = np.array([d["features"] for d in data])
    y = np.array([1 if d["is_tp"] else 0 for d in data])

    print(f"Training on {len(X)} examples ({sum(y)} TP, {len(y) - sum(y)} FP)")

    # Train
    dtrain = xgb.DMatrix(X, label=y)
    params = {
        "max_depth": args.depth,
        "eta": args.lr,
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "nthread": 4,
    }
    model = xgb.train(params, dtrain, num_boost_round=args.trees)

    # Export to JSON dump (gbdt-rs compatible)
    model.dump_model(args.output, dump_format="json")
    print(f"Model saved to {args.output}")

if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add scripts/train_model.py
git commit -m "feat: add Python XGBoost training script for seed model"
```

---

## Task 13: Final Integration Test

**Step 1: Run full test suite**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass (927+ existing + new tests)

**Step 2: Run clippy**

Run: `cd repotoire-cli && cargo clippy`
Expected: No new warnings

**Step 3: Self-analysis**

Run: `cd repotoire-cli && cargo run -- analyze .`
Expected: Score similar to baseline (97+ A+), no crashes, classifier still works (heuristic fallback)

**Step 4: Run on Flask**

Run: `cd repotoire-cli && cargo run -- analyze /tmp/flask`
Expected: No new false positives from classifier changes

**Step 5: Final commit (if needed)**

```bash
git add -A
git commit -m "chore: integration testing and cleanup"
```

---

## Execution Order

1. **Task 11** (confidence field) — unblocks Task 2
2. **Task 1** (gbdt dependency) — unblocks Tasks 3, 6
3. **Task 2** (features_v2.rs) — unblocks Tasks 3, 5, 7, 8
4. **Task 3** (gbdt_model.rs) — unblocks Tasks 6, 7, 8
5. **Task 4** (bootstrap.rs) — independent, can parallel with 3
6. **Task 5** (debt.rs) — depends on Task 2
7. **Task 6** (train.rs update) — depends on Tasks 2, 3
8. **Task 7** (postprocess.rs) — depends on Tasks 2, 3
9. **Task 8** (--rank flag) — depends on Tasks 2, 3, 7
10. **Task 9** (debt command) — depends on Task 5
11. **Task 10** (MCP tool) — depends on Task 5
12. **Task 12** (Python script) — independent
13. **Task 13** (integration test) — last

## Verification

1. `cargo check` after each task
2. `cargo test` after all tasks — all 927+ tests must pass
3. `cargo clippy` — no new warnings
4. Self-analysis: `cargo run -- analyze .` — score and finding count stable
5. Flask benchmark: `cargo run -- analyze /tmp/flask` — no new FPs
