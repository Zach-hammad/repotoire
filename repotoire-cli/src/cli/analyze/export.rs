//! Training data export for the GBDT classifier.
//!
//! Extracts 28-dimensional feature vectors from findings with full context
//! (graph, git history, cross-finding) and pairs them with bootstrap labels
//! mined from git history.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{Context, Result};

use crate::classifier::bootstrap;
use crate::classifier::features_v2::{
    compute_cross_features, FeatureExtractorV2, GitFeatures, FEATURE_NAMES,
};
use crate::git::history::GitHistory;
use crate::graph::traits::GraphQuery;
use crate::models::Finding;

/// A single training sample with features, label, and metadata.
#[derive(serde::Serialize)]
struct TrainingSample {
    features: Vec<f64>,
    is_tp: bool,
    weight: f64,
    label_source: String,
    detector: String,
    severity: String,
    file: String,
    feature_names: Vec<&'static str>,
}

/// Export training data for all findings that have a bootstrap label.
///
/// Steps:
/// 1. Build git features from file churn history
/// 2. Build file LOC map from graph
/// 3. Compute cross-finding features
/// 4. Mine bootstrap labels from git history
/// 5. Extract features for each labeled finding
/// 6. Write JSON to output_path
///
/// Returns the number of labeled samples exported.
pub fn export_training_data(
    findings: &[Finding],
    graph: &dyn GraphQuery,
    repo_path: &Path,
    output_path: &Path,
) -> Result<usize> {
    // Step 1: Build git features from file churn
    let git_features_map = build_git_features(repo_path);

    // Step 2: Build file LOC map from graph
    let file_loc_map = build_file_loc_map(graph);

    // Step 3: Compute cross-finding features
    let cross_map = compute_cross_features(findings, &file_loc_map);

    // Step 4: Mine bootstrap labels
    let labels = bootstrap::mine_labels(findings, repo_path);

    // Build label lookup: finding_id -> WeakLabel
    let label_map: HashMap<String, &bootstrap::WeakLabel> =
        labels.iter().map(|l| (l.finding_id.clone(), l)).collect();

    // Step 5: Extract features for each labeled finding
    let extractor = FeatureExtractorV2::new();
    let mut samples = Vec::new();

    let mut tp_count = 0usize;
    let mut fp_count = 0usize;

    for finding in findings {
        let label = match label_map.get(&finding.id) {
            Some(l) => l,
            None => continue, // skip unlabeled findings
        };

        let file_path = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();

        let git = git_features_map.get(&file_path);
        let cross = cross_map
            .get(&file_path)
            .and_then(|m| m.get(&finding.detector));

        let features = extractor.extract(finding, Some(graph), git, cross);

        if label.is_true_positive {
            tp_count += 1;
        } else {
            fp_count += 1;
        }

        samples.push(TrainingSample {
            features: features.to_vec(),
            is_tp: label.is_true_positive,
            weight: label.weight,
            label_source: label.source.to_string(),
            detector: finding.detector.clone(),
            severity: format!("{:?}", finding.severity).to_lowercase(),
            file: file_path,
            feature_names: FEATURE_NAMES.to_vec(),
        });
    }

    // Step 6: Write JSON
    let json = serde_json::to_string_pretty(&samples)
        .context("failed to serialize training data")?;

    if let Some(parent) = output_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create directory {}", parent.display()))?;
    }

    std::fs::write(output_path, json)
        .with_context(|| format!("failed to write training data to {}", output_path.display()))?;

    // Print summary
    let unlabeled = findings.len() - samples.len();
    eprintln!(
        "  Training data: {} labeled ({} TP, {} FP), {} unlabeled (skipped)",
        samples.len(),
        tp_count,
        fp_count,
        unlabeled
    );

    Ok(samples.len())
}

/// Build per-file GitFeatures from git history churn data.
fn build_git_features(repo_path: &Path) -> HashMap<String, GitFeatures> {
    let now_epoch = chrono::Utc::now().timestamp();

    let git_history = match GitHistory::new(repo_path) {
        Ok(h) => h,
        Err(e) => {
            tracing::debug!("export: could not open git history: {}", e);
            return HashMap::new();
        }
    };

    let churn_map = match git_history.get_all_file_churn(500) {
        Ok(m) => m,
        Err(e) => {
            tracing::debug!("export: could not get file churn: {}", e);
            return HashMap::new();
        }
    };

    churn_map
        .iter()
        .map(|(path, churn)| {
            (
                path.clone(),
                GitFeatures::from_file_churn(churn, now_epoch),
            )
        })
        .collect()
}

/// Build a file path -> LOC map from the graph's file nodes.
fn build_file_loc_map(graph: &dyn GraphQuery) -> HashMap<String, f64> {
    let files = graph.get_files();
    let mut loc_map = HashMap::new();

    for file_node in &files {
        let loc = file_node.loc() as f64;
        loc_map.insert(file_node.file_path.clone(), loc);
    }

    // Also aggregate function LOC per file for files that might not have
    // a direct LOC measurement on the file node
    let functions = graph.get_functions();
    for func in &functions {
        let entry = loc_map
            .entry(func.file_path.clone())
            .or_insert(0.0);
        // Only use function LOC if file LOC isn't already set
        if *entry == 0.0 {
            *entry += func.loc() as f64;
        }
    }

    loc_map
}
