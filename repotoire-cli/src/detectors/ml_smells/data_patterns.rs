use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::{
    chain_index, deprecated_torch, manual_seed, pca_svm_call, require_grad_typo, scaler_call,
};

pub struct MissingRandomSeedDetector {
    repository_path: PathBuf,
}

impl MissingRandomSeedDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }

    fn is_ml_file(&self, content: &str) -> bool {
        content.contains("torch")
            || content.contains("tensorflow")
            || content.contains("sklearn")
            || content.contains("keras")
            || content.contains("from transformers")
    }

    fn has_training_code(&self, content: &str) -> bool {
        content.contains(".fit(")
            || content.contains(".train()")
            || content.contains(".backward(")
            || content.contains("train_loader")
            || content.contains("training_loop")
    }
}

impl Detector for MissingRandomSeedDetector {
    fn name(&self) -> &'static str {
        "missing-random-seed"
    }

    fn description(&self) -> &'static str {
        "Detects ML training without random seed"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            // Skip test files - they often don't need seeds
            let path_str = path.to_string_lossy().to_lowercase();
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                if !self.is_ml_file(&content) || !self.has_training_code(&content) {
                    continue;
                }

                if !manual_seed().is_match(&content) {
                    let file_str = path.to_string_lossy();

                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "MissingRandomSeedDetector",
                            &file_str,
                            1,
                            "missing random seed",
                        ),
                        detector: "MissingRandomSeedDetector".to_string(),
                        severity: Severity::Medium,
                        title: "ML training without random seed".to_string(),
                        description: "This file contains ML training code but doesn't set \
                            random seeds. Results won't be reproducible."
                            .to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Add seed setting at the start of training:\n\
                            ```python\n\
                            import random\n\
                            import numpy as np\n\
                            import torch\n\n\
                            def set_seed(seed=42):\n    \
                                random.seed(seed)\n    \
                                np.random.seed(seed)\n    \
                                torch.manual_seed(seed)\n    \
                                torch.cuda.manual_seed_all(seed)\n    \
                                # For full determinism (slower):\n    \
                                # torch.use_deterministic_algorithms(True)\n\n\
                            set_seed(42)\n\
                            ```"
                            .to_string(),
                        ),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("reproducibility".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Without fixed seeds, ML experiments aren't reproducible. Different \
                            runs produce different results, making debugging and comparison \
                            impossible. This is a major issue for research and production ML."
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "MissingRandomSeedDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

// ============================================================================
// ChainIndexingDetector
// ============================================================================

/// Detects pandas chain indexing df['a']['b'] (SettingWithCopyWarning)
pub struct ChainIndexingDetector {
    repository_path: PathBuf,
}

impl ChainIndexingDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for ChainIndexingDetector {
    fn name(&self) -> &'static str {
        "chain-indexing"
    }

    fn description(&self) -> &'static str {
        "Detects pandas chain indexing df['a']['b']"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                // Skip files that don't use pandas
                if !content.contains("pandas")
                    && !content.contains("import pd")
                    && !content.contains("as pd")
                {
                    continue;
                }

                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if chain_index().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "ChainIndexingDetector",
                                &file_str,
                                line_num,
                                "chain indexing",
                            ),
                            detector: "ChainIndexingDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Pandas chain indexing".to_string(),
                            description: "df['col1']['col2'] uses chain indexing, which can cause \
                                SettingWithCopyWarning and silent bugs when assigning values.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use .loc[] for explicit indexing:\n\
                                ```python\n\
                                # Instead of:\n\
                                df['col1']['col2'] = value  # Unreliable!\n\n\
                                # Use:\n\
                                df.loc[:, 'col1'] = value  # Single column\n\
                                df.loc[mask, 'col'] = value  # With condition\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("correctness".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Chain indexing returns a copy in some cases and a view in others. \
                                Assignments may silently fail, corrupting your data without warning. \
                                The SettingWithCopyWarning exists for this reason.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("ChainIndexingDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// RequireGradTypoDetector
// ============================================================================

/// Detects require_grad typo (should be requires_grad)
pub struct RequireGradTypoDetector {
    repository_path: PathBuf,
}

impl RequireGradTypoDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for RequireGradTypoDetector {
    fn name(&self) -> &'static str {
        "require-grad-typo"
    }

    fn description(&self) -> &'static str {
        "Detects require_grad typo (should be requires_grad)"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if require_grad_typo().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "RequireGradTypoDetector",
                                &file_str,
                                line_num,
                                "require_grad typo",
                            ),
                            detector: "RequireGradTypoDetector".to_string(),
                            severity: Severity::High,
                            title: "Typo: require_grad instead of requires_grad".to_string(),
                            description: "PyTorch uses `requires_grad` (with 's'). This typo \
                                silently creates a new attribute instead of setting gradients.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Fix the typo:\n\
                                ```python\n\
                                # Wrong:\n\
                                tensor.require_grad = True\n\n\
                                # Correct:\n\
                                tensor.requires_grad = True\n\
                                # Or:\n\
                                tensor.requires_grad_(True)\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("2 minutes".to_string()),
                            category: Some("correctness".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Python doesn't error on setting new attributes. The typo creates \
                                a `require_grad` attribute that PyTorch ignores, while `requires_grad` \
                                stays False. Gradients won't be computed, breaking training.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("RequireGradTypoDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// DeprecatedTorchApiDetector
// ============================================================================

/// Detects deprecated PyTorch API usage
pub struct DeprecatedTorchApiDetector {
    repository_path: PathBuf,
}

impl DeprecatedTorchApiDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }

    fn get_deprecation_info(api: &str) -> (&'static str, &'static str) {
        match api {
            "solve" => ("torch.linalg.solve(A, B)", "Removed in PyTorch 1.9+"),
            "symeig" => ("torch.linalg.eigh()", "Removed in PyTorch 1.9+"),
            "qr" => ("torch.linalg.qr()", "Deprecated, use linalg version"),
            "cholesky" => ("torch.linalg.cholesky()", "Deprecated, use linalg version"),
            "chain_matmul" => ("torch.linalg.multi_dot([a, b, c])", "Deprecated"),
            "range" => (
                "torch.arange()",
                "Use arange (matches Python range semantics)",
            ),
            _ => ("See PyTorch docs", "Deprecated"),
        }
    }
}

impl Detector for DeprecatedTorchApiDetector {
    fn name(&self) -> &'static str {
        "deprecated-torch-api"
    }

    fn description(&self) -> &'static str {
        "Detects deprecated PyTorch API usage"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        let deprecated_apis = ["solve", "symeig", "qr", "cholesky", "chain_matmul", "range"];

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                if !content.contains("torch") {
                    continue;
                }

                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    for api in &deprecated_apis {
                        let pattern = format!("torch.{}", api);
                        if line.contains(&pattern) {
                            let (replacement, status) = Self::get_deprecation_info(api);
                            let file_str = path.to_string_lossy();
                            let line_num = (i + 1) as u32;

                            // torch.range is common, make it Medium; removed APIs are High
                            let severity = if *api == "solve" || *api == "symeig" {
                                Severity::High
                            } else {
                                Severity::Medium
                            };

                            findings.push(Finding {
                                id: deterministic_finding_id(
                                    "DeprecatedTorchApiDetector",
                                    &file_str,
                                    line_num,
                                    &format!("torch.{}", api),
                                ),
                                detector: "DeprecatedTorchApiDetector".to_string(),
                                severity,
                                title: format!("Deprecated API: torch.{}", api),
                                description: format!(
                                    "torch.{}() is deprecated/removed. {}",
                                    api, status
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some(format!(
                                    "Replace with:\n```python\n{}\n```",
                                    replacement
                                )),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("compatibility".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Deprecated APIs may be removed in future PyTorch versions, \
                                    breaking your code. Migrate now for forward compatibility."
                                        .to_string(),
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "DeprecatedTorchApiDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

// ============================================================================
// Tests
// ============================================================================
