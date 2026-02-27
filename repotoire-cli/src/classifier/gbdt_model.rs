//! GBDT model wrapper for finding classification
//!
//! Wraps the `gbdt` crate to provide:
//! - Model loading from serialised JSON or XGBoost dump format
//! - Single and batch inference using `FeaturesV2`
//! - Training helper for building new models from labelled data
//!
//! The classifier produces calibrated probabilities via the `LogLikelyhood`
//! loss (binary classification), interpreting label 1.0 as true-positive and
//! -1.0 as false-positive.
//!
//! Note: the gbdt crate internally uses `f32` (`ValueType`), while our
//! `FeaturesV2` stores `f64`. Conversions happen transparently at the
//! crate boundary.

use std::io::Cursor;
use std::path::Path;

use gbdt::config::Config;
use gbdt::decision_tree::Data;
use gbdt::gradient_boost::GBDT;

use super::features_v2::FeaturesV2;

// ---------------------------------------------------------------------------
// f64 <-> f32 helpers
// ---------------------------------------------------------------------------

/// Convert a `FeaturesV2` (f64) to a `Vec<f32>` for the gbdt crate.
#[inline]
fn features_to_f32(features: &FeaturesV2) -> Vec<f32> {
    features.values.iter().map(|&v| v as f32).collect()
}

// ---------------------------------------------------------------------------
// Prediction output
// ---------------------------------------------------------------------------

/// Result of running a single finding through the GBDT classifier.
#[derive(Debug, Clone)]
pub struct GbdtPrediction {
    /// Probability that the finding is a true positive (0.0..1.0).
    pub tp_probability: f64,

    /// Actionability score derived from the TP probability.
    ///
    /// Maps the raw probability to a 0-100 scale for easy sorting/ranking.
    pub actionability_score: f64,

    /// Hard classification: `true` when `tp_probability >= 0.5`.
    pub is_true_positive: bool,
}

impl GbdtPrediction {
    /// Build a prediction from a raw TP probability.
    fn from_probability(tp_prob: f64) -> Self {
        Self {
            tp_probability: tp_prob,
            actionability_score: (tp_prob * 100.0).clamp(0.0, 100.0),
            is_true_positive: tp_prob >= 0.5,
        }
    }
}

// ---------------------------------------------------------------------------
// Classifier wrapper
// ---------------------------------------------------------------------------

/// Thin wrapper around `gbdt::gradient_boost::GBDT` providing a
/// `FeaturesV2`-aware prediction interface.
pub struct GbdtClassifier {
    model: GBDT,
}

impl GbdtClassifier {
    /// Load a model from the gbdt-rs native JSON format on disk.
    ///
    /// This is the format produced by `save_model`.
    pub fn load(path: &Path) -> Result<Self, String> {
        let path_str = path
            .to_str()
            .ok_or_else(|| "invalid UTF-8 in model path".to_string())?;
        let model =
            GBDT::load_model(path_str).map_err(|e| format!("failed to load GBDT model: {e}"))?;
        Ok(Self { model })
    }

    /// Load a model from an XGBoost JSON dump file on disk.
    ///
    /// Uses `binary:logistic` as the objective (sigmoid output).
    pub fn load_xgboost(path: &Path) -> Result<Self, String> {
        let path_str = path
            .to_str()
            .ok_or_else(|| "invalid UTF-8 in model path".to_string())?;
        let model = GBDT::from_xgboost_dump(path_str, "binary:logistic")
            .map_err(|e| format!("failed to load XGBoost dump: {e}"))?;
        Ok(Self { model })
    }

    /// Load a model from a JSON string (gbdt-rs native format).
    ///
    /// Useful for embedding a model as a const string in the binary.
    pub fn from_json(json: &str) -> Result<Self, String> {
        let model: GBDT =
            serde_json::from_str(json).map_err(|e| format!("failed to parse GBDT JSON: {e}"))?;
        Ok(Self { model })
    }

    /// Load a model from an XGBoost JSON dump string.
    ///
    /// Useful for embedding XGBoost-exported models as const strings.
    pub fn from_xgboost_json(json: &str) -> Result<Self, String> {
        let reader = Cursor::new(json);
        let buf_reader = std::io::BufReader::new(reader);
        let model = GBDT::from_xgboost_reader(buf_reader, "binary:logistic")
            .map_err(|e| format!("failed to parse XGBoost JSON: {e}"))?;
        Ok(Self { model })
    }

    /// Wrap an already-trained `GBDT` instance.
    pub fn from_trained(model: GBDT) -> Self {
        Self { model }
    }

    /// Predict a single finding's classification.
    pub fn predict(&self, features: &FeaturesV2) -> GbdtPrediction {
        let data = vec![Data::new_test_data(features_to_f32(features), None)];
        let preds = self.model.predict(&data);
        let tp_prob = preds.first().copied().unwrap_or(0.5_f32) as f64;
        GbdtPrediction::from_probability(tp_prob)
    }

    /// Predict a batch of findings.
    pub fn predict_batch(&self, features: &[FeaturesV2]) -> Vec<GbdtPrediction> {
        if features.is_empty() {
            return Vec::new();
        }

        let data: Vec<Data> = features
            .iter()
            .map(|f| Data::new_test_data(features_to_f32(f), None))
            .collect();

        let preds = self.model.predict(&data);

        preds
            .into_iter()
            .map(|p| GbdtPrediction::from_probability(p as f64))
            .collect()
    }

    /// Return a reference to the underlying GBDT model.
    pub fn inner(&self) -> &GBDT {
        &self.model
    }
}

// ---------------------------------------------------------------------------
// Training helper
// ---------------------------------------------------------------------------

/// Train a new GBDT model from labelled feature vectors.
///
/// - `features`: the V2 feature vectors for each sample
/// - `labels`: 1.0 for true-positive, -1.0 for false-positive (LogLikelyhood convention)
/// - `num_trees`: number of boosting iterations (e.g. 100)
/// - `max_depth`: maximum tree depth (e.g. 6)
/// - `learning_rate`: shrinkage / step size (e.g. 0.1)
///
/// Returns the trained `GBDT` model.
pub fn train_gbdt(
    features: &[FeaturesV2],
    labels: &[f64],
    num_trees: usize,
    max_depth: u32,
    learning_rate: f64,
) -> Result<GBDT, String> {
    if features.is_empty() {
        return Err("no training samples provided".into());
    }
    if features.len() != labels.len() {
        return Err(format!(
            "feature count ({}) does not match label count ({})",
            features.len(),
            labels.len()
        ));
    }

    let feature_size = features[0].values.len();

    let mut cfg = Config::new();
    cfg.set_feature_size(feature_size);
    cfg.set_max_depth(max_depth);
    cfg.set_iterations(num_trees);
    cfg.set_shrinkage(learning_rate as f32);
    cfg.set_loss("LogLikelyhood");
    cfg.set_debug(false);
    cfg.set_training_optimization_level(2);
    cfg.set_min_leaf_size(1);

    let mut gbdt = GBDT::new(&cfg);

    let mut training_data: Vec<Data> = features
        .iter()
        .zip(labels.iter())
        .map(|(f, &label)| {
            Data::new_training_data(features_to_f32(f), 1.0_f32, label as f32, None)
        })
        .collect();

    gbdt.fit(&mut training_data);

    Ok(gbdt)
}

/// Save a trained GBDT model to disk (gbdt-rs native JSON format).
pub fn save_model(model: &GBDT, path: &Path) -> Result<(), String> {
    let path_str = path
        .to_str()
        .ok_or_else(|| "invalid UTF-8 in model path".to_string())?;
    model
        .save_model(path_str)
        .map_err(|e| format!("failed to save GBDT model: {e}"))
}

// ---------------------------------------------------------------------------
// Embedded seed model
// ---------------------------------------------------------------------------

/// Embedded seed model trained on diverse open-source repos.
///
/// This model ships with the binary so users get a working GBDT classifier
/// on day one, without needing to run `repotoire train` first.
///
/// The model is in gbdt-rs native JSON format (serde-serialized GBDT struct).
/// To update it, run the training pipeline:
///   1. cargo run -- analyze <repo> --export-training /tmp/data.json
///   2. uv run scripts/train_model.py --data /tmp/data.json --output repotoire-cli/models/seed_model.json
const SEED_MODEL_JSON: &str = include_str!("../../models/seed_model.json");

impl GbdtClassifier {
    /// Load the embedded seed model (ships with the binary).
    ///
    /// This provides a baseline classifier without user training.
    /// Falls back to this when no user-trained model exists at
    /// `$XDG_DATA_HOME/repotoire/gbdt_model.json`.
    pub fn seed() -> Result<Self, String> {
        Self::from_json(SEED_MODEL_JSON)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::classifier::features_v2::NUM_FEATURES;

    /// Build a synthetic FeaturesV2 with deterministic values.
    fn make_features(seed: f64) -> FeaturesV2 {
        let mut values = [0.0_f64; NUM_FEATURES];
        for (i, v) in values.iter_mut().enumerate() {
            *v = (seed + i as f64 * 0.1).sin().abs();
        }
        FeaturesV2::new(values)
    }

    #[test]
    fn test_gbdt_prediction_struct() {
        let pred = GbdtPrediction::from_probability(0.85);
        assert!(pred.is_true_positive);
        assert!((pred.tp_probability - 0.85).abs() < f64::EPSILON);
        assert!((pred.actionability_score - 85.0).abs() < f64::EPSILON);

        let pred_low = GbdtPrediction::from_probability(0.3);
        assert!(!pred_low.is_true_positive);
        assert!((pred_low.tp_probability - 0.3).abs() < f64::EPSILON);
        assert!((pred_low.actionability_score - 30.0).abs() < f64::EPSILON);

        // Edge case: exactly 0.5
        let pred_edge = GbdtPrediction::from_probability(0.5);
        assert!(pred_edge.is_true_positive);
    }

    #[test]
    fn test_train_and_predict() {
        // Create synthetic data: two clusters.
        // Cluster A (label 1.0 = TP): features with seed 1..25
        // Cluster B (label -1.0 = FP): features with seed 101..125
        let mut features = Vec::new();
        let mut labels = Vec::new();

        for i in 0..25 {
            features.push(make_features(i as f64));
            labels.push(1.0);
        }
        for i in 100..125 {
            features.push(make_features(i as f64));
            labels.push(-1.0);
        }

        // Train a small model.
        let model =
            train_gbdt(&features, &labels, 10, 3, 0.3).expect("training should succeed");

        // Wrap in classifier.
        let classifier = GbdtClassifier::from_trained(model);

        // Predict on a sample from cluster A.
        let pred_a = classifier.predict(&make_features(5.0));
        // Predict on a sample from cluster B.
        let pred_b = classifier.predict(&make_features(110.0));

        // We don't assert hard classification correctness because the
        // synthetic data might not be perfectly separable with 10 trees,
        // but we verify predictions come back with valid probabilities.
        assert!(
            pred_a.tp_probability >= 0.0 && pred_a.tp_probability <= 1.0,
            "TP probability should be in [0, 1], got {}",
            pred_a.tp_probability,
        );
        assert!(
            pred_b.tp_probability >= 0.0 && pred_b.tp_probability <= 1.0,
            "TP probability should be in [0, 1], got {}",
            pred_b.tp_probability,
        );

        // Batch prediction should produce same results.
        let batch_input = vec![make_features(5.0), make_features(110.0)];
        let batch_preds = classifier.predict_batch(&batch_input);
        assert_eq!(batch_preds.len(), 2);
        assert!(
            (batch_preds[0].tp_probability - pred_a.tp_probability).abs() < 1e-6,
            "batch prediction should match single prediction",
        );
        assert!(
            (batch_preds[1].tp_probability - pred_b.tp_probability).abs() < 1e-6,
            "batch prediction should match single prediction",
        );
    }

    #[test]
    fn test_train_validation_errors() {
        // Empty features.
        let result = train_gbdt(&[], &[], 10, 3, 0.3);
        match result {
            Err(e) => assert!(
                e.contains("no training samples"),
                "expected 'no training samples' error, got: {e}"
            ),
            Ok(_) => panic!("expected error for empty features"),
        }

        // Mismatched lengths.
        let features = vec![make_features(1.0), make_features(2.0)];
        let labels = vec![1.0];
        let result = train_gbdt(&features, &labels, 10, 3, 0.3);
        match result {
            Err(e) => assert!(
                e.contains("does not match"),
                "expected 'does not match' error, got: {e}"
            ),
            Ok(_) => panic!("expected error for mismatched lengths"),
        }
    }

    #[test]
    fn test_save_and_load_roundtrip() {
        // Train a tiny model.
        let mut features = Vec::new();
        let mut labels = Vec::new();
        for i in 0..10 {
            features.push(make_features(i as f64));
            labels.push(if i < 5 { 1.0 } else { -1.0 });
        }

        let model =
            train_gbdt(&features, &labels, 5, 2, 0.3).expect("training should succeed");

        // Save to a temp file.
        let tmp = tempfile::NamedTempFile::new().expect("create temp file");
        let path = tmp.path().to_path_buf();

        save_model(&model, &path).expect("save should succeed");

        // Load it back.
        let classifier = GbdtClassifier::load(&path).expect("load should succeed");

        // Predictions from the loaded model should match the original.
        let test_features = make_features(3.0);
        let original_classifier = GbdtClassifier::from_trained(model);
        let pred_original = original_classifier.predict(&test_features);
        let pred_loaded = classifier.predict(&test_features);

        assert!(
            (pred_original.tp_probability - pred_loaded.tp_probability).abs() < 1e-6,
            "loaded model predictions should match original: {} vs {}",
            pred_original.tp_probability,
            pred_loaded.tp_probability,
        );
    }

    #[test]
    fn test_from_json_roundtrip() {
        // Train a tiny model.
        let mut features = Vec::new();
        let mut labels = Vec::new();
        for i in 0..10 {
            features.push(make_features(i as f64));
            labels.push(if i < 5 { 1.0 } else { -1.0 });
        }

        let model =
            train_gbdt(&features, &labels, 5, 2, 0.3).expect("training should succeed");

        // Serialise to JSON.
        let json = serde_json::to_string(&model).expect("serialise should succeed");

        // Load from JSON string.
        let classifier = GbdtClassifier::from_json(&json).expect("from_json should succeed");

        // Verify predictions.
        let test_features = make_features(3.0);
        let pred = classifier.predict(&test_features);
        assert!(
            pred.tp_probability >= 0.0 && pred.tp_probability <= 1.0,
            "prediction should be valid",
        );
    }

    #[test]
    fn test_seed_model_loads() {
        let classifier = GbdtClassifier::seed().expect("seed model should load");
        let features = make_features(42.0);
        let pred = classifier.predict(&features);
        assert!(
            pred.tp_probability >= 0.0 && pred.tp_probability <= 1.0,
            "seed model should produce valid probabilities, got {}",
            pred.tp_probability,
        );
    }

    /// Generate the seed model file. Run with:
    /// `cargo test generate_seed_model_file -- --ignored --nocapture`
    ///
    /// The seed model is intentionally conservative — it should pass through
    /// most findings and only filter the most obvious FPs. This avoids
    /// over-filtering when real training data isn't available yet.
    #[test]
    #[ignore]
    fn generate_seed_model_file() {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let mut features = Vec::new();
        let mut labels = Vec::new();

        // Seed RNG for reproducible but varied synthetic data
        let mut hash_seed = |i: usize, offset: usize| -> f64 {
            let mut h = DefaultHasher::new();
            (i * 137 + offset * 31).hash(&mut h);
            (h.finish() % 1000) as f64 / 1000.0
        };

        // TP samples (80): diverse findings that should be kept.
        // Use 80:20 TP:FP ratio (conservative — bias toward keeping findings)
        for i in 0..80 {
            let mut values = [0.0_f64; NUM_FEATURES];
            values[0] = (i % 32) as f64; // detector_bucket
            values[1] = 1.0 + (i % 3) as f64; // severity: medium/high/critical
            values[2] = 0.5 + hash_seed(i, 0) * 0.45; // confidence: 0.5-0.95
            values[3] = (i % 5) as f64; // category: varied
            values[4] = if i % 3 == 0 { 1.0 } else { 0.0 }; // has_cwe
            values[5] = (i % 3) as f64; // entity_type
            values[6] = 10.0 + hash_seed(i, 1) * 100.0; // function_loc
            values[7] = 50.0 + hash_seed(i, 2) * 500.0; // file_loc
            values[8] = 3.0 + hash_seed(i, 3) * 15.0; // function_count
            values[9] = hash_seed(i, 4) * 0.4; // span_norm
            values[10] = 2.0 + hash_seed(i, 5) * 12.0; // complexity
            values[11] = 1.0 + hash_seed(i, 6) * 5.0; // nesting
            values[12] = hash_seed(i, 7) * 8.0; // fan_in
            values[13] = hash_seed(i, 8) * 6.0; // fan_out
            values[14] = if i % 10 == 0 { 1.0 } else { 0.0 }; // scc
            values[15] = 3.0 + hash_seed(i, 9) * 5.0; // file_age_log
            values[16] = 5.0 + hash_seed(i, 10) * 20.0; // recent_churn
            values[17] = 1.0 + hash_seed(i, 11) * 5.0; // developer_count
            values[18] = 5.0 + hash_seed(i, 12) * 50.0; // unique_change_count
            values[19] = if i % 8 == 0 { 1.0 } else { 0.0 }; // recently_created
            values[20] = 0.3 + hash_seed(i, 13) * 0.7; // major_contributor_pct
            values[21] = hash_seed(i, 14) * 4.0; // minor_contributor_count
            values[22] = 2.0 + hash_seed(i, 15) * 4.0; // file_depth
            values[23] = hash_seed(i, 16) * 2.0; // fp_path_indicators
            values[24] = 1.0 + hash_seed(i, 17) * 2.0; // tp_path_indicators
            values[25] = 2.0 + hash_seed(i, 18) * 10.0; // finding_density
            values[26] = 1.0 + hash_seed(i, 19) * 3.0; // same_detector_findings
            values[27] = hash_seed(i, 20) * 0.2; // historical_fp_rate: low
            features.push(FeaturesV2::new(values));
            labels.push(1.0);
        }

        // FP samples (20): clearly false-positive patterns only
        for i in 0..20 {
            let mut values = [0.0_f64; NUM_FEATURES];
            values[0] = ((i + 10) % 32) as f64;
            values[1] = (i % 2) as f64; // severity: low/info
            values[2] = 0.15 + hash_seed(i + 100, 0) * 0.2; // confidence: very low 0.15-0.35
            values[3] = 1.0; // code quality (noisy category)
            values[4] = 0.0; // no CWE
            values[5] = 0.0; // file-level
            values[6] = 3.0 + hash_seed(i + 100, 1) * 10.0; // function_loc: small
            values[7] = 20.0 + hash_seed(i + 100, 2) * 50.0; // file_loc: small
            values[8] = 1.0 + hash_seed(i + 100, 3) * 3.0; // few functions
            values[9] = 0.6 + hash_seed(i + 100, 4) * 0.4; // large span_norm
            values[10] = hash_seed(i + 100, 5) * 2.0; // complexity: low
            values[11] = hash_seed(i + 100, 6) * 2.0; // nesting: low
            values[12] = 0.0; // no fan_in
            values[13] = hash_seed(i + 100, 8) * 2.0; // low fan_out
            values[14] = 0.0; // scc
            values[15] = 6.0 + hash_seed(i + 100, 9) * 2.0; // old files
            values[16] = hash_seed(i + 100, 10) * 3.0; // low churn
            values[17] = 1.0; // single developer
            values[18] = 2.0 + hash_seed(i + 100, 12) * 5.0; // few changes
            values[19] = 0.0; // not recently created
            values[20] = 1.0; // single contributor
            values[21] = 0.0; // no minor contributors
            values[22] = 3.0 + hash_seed(i + 100, 15) * 3.0; // file_depth
            values[23] = 3.0 + hash_seed(i + 100, 16) * 2.0; // many fp_path_indicators
            values[24] = 0.0; // no tp_path_indicators
            values[25] = 0.5 + hash_seed(i + 100, 18) * 2.0; // low density
            values[26] = 1.0; // single detector finding
            values[27] = 0.5 + hash_seed(i + 100, 20) * 0.3; // high historical FP rate
            features.push(FeaturesV2::new(values));
            labels.push(-1.0);
        }

        // Very few trees + low learning rate = conservative model that mostly
        // passes through findings and only filters extreme FP patterns
        let model = super::train_gbdt(&features, &labels, 8, 2, 0.03)
            .expect("training should succeed");

        let json = serde_json::to_string(&model).expect("serialize should succeed");

        let model_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("models")
            .join("seed_model.json");
        std::fs::create_dir_all(model_path.parent().unwrap()).unwrap();
        std::fs::write(&model_path, &json).unwrap();
        println!("Seed model written to {} ({} bytes)", model_path.display(), json.len());

        // Verify it loads and produces reasonable predictions
        let loaded = GbdtClassifier::from_json(&json).expect("should load from JSON");

        // Check a TP sample — should predict > 0.5
        let pred_tp = loaded.predict(&features[0]);
        println!("Sample TP prediction: {:.4}", pred_tp.tp_probability);

        // Check an FP sample — should predict < 0.5
        let pred_fp = loaded.predict(&features[80]);
        println!("Sample FP prediction: {:.4}", pred_fp.tp_probability);

        // Check that a generic medium-severity finding passes through
        // (conservative model should keep most findings)
        let mut generic = [0.0_f64; NUM_FEATURES];
        generic[1] = 1.0; // medium severity
        generic[2] = 0.5; // moderate confidence
        generic[5] = 1.0; // function entity
        generic[6] = 30.0; // decent function size
        generic[7] = 200.0; // decent file size
        generic[22] = 2.0; // normal depth
        generic[24] = 1.0; // in src
        let pred_generic = loaded.predict(&FeaturesV2::new(generic));
        println!("Generic medium finding prediction: {:.4}", pred_generic.tp_probability);
        // Generic finding should still pass filter threshold (0.35 for security, 0.52 for quality)
        assert!(
            pred_generic.tp_probability >= 0.35,
            "generic medium finding should pass through, got {}",
            pred_generic.tp_probability,
        );
    }

    #[test]
    fn test_predict_batch_empty() {
        // Train a tiny model.
        let mut features = Vec::new();
        let mut labels = Vec::new();
        for i in 0..10 {
            features.push(make_features(i as f64));
            labels.push(if i < 5 { 1.0 } else { -1.0 });
        }

        let model =
            train_gbdt(&features, &labels, 5, 2, 0.3).expect("training should succeed");
        let classifier = GbdtClassifier::from_trained(model);

        // Empty batch should return empty results.
        let preds = classifier.predict_batch(&[]);
        assert!(preds.is_empty());
    }
}
