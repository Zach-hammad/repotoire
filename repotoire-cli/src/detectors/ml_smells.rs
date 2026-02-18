//! ML/AI Code Smell Detectors
//!
//! Detectors for machine learning and data science code, inspired by:
//! - TorchFix (pytorch-labs)
//! - dslinter (SERG-Delft)
//! - MLScent & SpecDetect4AI (arXiv research)
//!
//! Covers PyTorch, TensorFlow, Scikit-Learn, Pandas, NumPy patterns.

use crate::detectors::base::Detector;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

// ============================================================================
// Regex patterns (compiled once)
// ============================================================================

static TORCH_LOAD: OnceLock<Regex> = OnceLock::new();
static TORCH_LOAD_WEIGHTS_ONLY: OnceLock<Regex> = OnceLock::new();
static NAN_EQUALITY: OnceLock<Regex> = OnceLock::new();
static BACKWARD_CALL: OnceLock<Regex> = OnceLock::new();
static ZERO_GRAD_CALL: OnceLock<Regex> = OnceLock::new();
static FORWARD_METHOD: OnceLock<Regex> = OnceLock::new();
static MANUAL_SEED: OnceLock<Regex> = OnceLock::new();
static CHAIN_INDEX: OnceLock<Regex> = OnceLock::new();
static PCA_SVM_CALL: OnceLock<Regex> = OnceLock::new();
static SCALER_CALL: OnceLock<Regex> = OnceLock::new();
static REQUIRE_GRAD_TYPO: OnceLock<Regex> = OnceLock::new();
static DEPRECATED_TORCH: OnceLock<Regex> = OnceLock::new();
static DATALOADER_SHUFFLE: OnceLock<Regex> = OnceLock::new();
static EVAL_MODE: OnceLock<Regex> = OnceLock::new();

fn torch_load() -> &'static Regex {
    TORCH_LOAD.get_or_init(|| Regex::new(r"torch\.load\s*\(").expect("valid regex"))
}

fn torch_load_weights_only() -> &'static Regex {
    TORCH_LOAD_WEIGHTS_ONLY.get_or_init(|| Regex::new(r"weights_only\s*=\s*True").expect("valid regex"))
}

fn nan_equality() -> &'static Regex {
    NAN_EQUALITY.get_or_init(|| {
        Regex::new(r#"(?:==|!=|is|is not)\s*(?:np\.nan|float\(['"]nan['"]\)|math\.nan|torch\.nan|numpy\.nan)"#).expect("valid regex")
    })
}

fn backward_call() -> &'static Regex {
    BACKWARD_CALL.get_or_init(|| Regex::new(r"\.backward\s*\(").expect("valid regex"))
}

fn zero_grad_call() -> &'static Regex {
    ZERO_GRAD_CALL.get_or_init(|| Regex::new(r"\.zero_grad\s*\(|optimizer\.zero_grad").expect("valid regex"))
}

fn forward_method() -> &'static Regex {
    FORWARD_METHOD.get_or_init(|| Regex::new(r"\.\s*forward\s*\(").expect("valid regex"))
}

fn manual_seed() -> &'static Regex {
    MANUAL_SEED.get_or_init(|| {
        Regex::new(r"(?:torch\.manual_seed|torch\.cuda\.manual_seed|np\.random\.seed|random\.seed|tf\.random\.set_seed|set_random_seed)").expect("valid regex")
    })
}

fn chain_index() -> &'static Regex {
    CHAIN_INDEX.get_or_init(|| {
        // df['col1']['col2'] or df["col1"]["col2"]
        Regex::new(r#"\w+\[['"][^'"]+['"]\]\s*\[['"][^'"]+['"]\]"#).expect("valid regex")
    })
}

fn pca_svm_call() -> &'static Regex {
    PCA_SVM_CALL.get_or_init(|| {
        Regex::new(r"(?:PCA|SVC|SVR|SGDClassifier|SGDRegressor|MLPClassifier|MLPRegressor|KMeans|DBSCAN|Lasso|Ridge|ElasticNet)\s*\(").expect("valid regex")
    })
}

fn scaler_call() -> &'static Regex {
    SCALER_CALL.get_or_init(|| {
        Regex::new(r"(?:StandardScaler|MinMaxScaler|RobustScaler|Normalizer|MaxAbsScaler)\s*\(")
            .expect("valid regex")
    })
}

fn require_grad_typo() -> &'static Regex {
    REQUIRE_GRAD_TYPO.get_or_init(|| {
        // require_grad (typo) instead of requires_grad
        Regex::new(r"\.require_grad\s*=|require_grad\s*=\s*True").expect("valid regex")
    })
}

fn deprecated_torch() -> &'static Regex {
    DEPRECATED_TORCH.get_or_init(|| {
        Regex::new(r"torch\.(?:solve|symeig|qr|cholesky|chain_matmul|range)\s*\(").expect("valid regex")
    })
}

fn dataloader_shuffle() -> &'static Regex {
    DATALOADER_SHUFFLE
        .get_or_init(|| Regex::new(r"DataLoader\s*\([^)]*shuffle\s*=\s*True").expect("valid regex"))
}

fn eval_mode() -> &'static Regex {
    EVAL_MODE.get_or_init(|| Regex::new(r"\.eval\s*\(").expect("valid regex"))
}

// ============================================================================
// TorchLoadUnsafeDetector
// ============================================================================

/// Detects torch.load() without weights_only=True (pickle RCE vulnerability)
pub struct TorchLoadUnsafeDetector {
    repository_path: PathBuf,
}

impl TorchLoadUnsafeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for TorchLoadUnsafeDetector {
    fn name(&self) -> &'static str {
        "torch-load-unsafe"
    }

    fn description(&self) -> &'static str {
        "Detects torch.load() without weights_only=True (pickle RCE)"
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if torch_load().is_match(line) && !torch_load_weights_only().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "TorchLoadUnsafeDetector",
                                &file_str,
                                line_num,
                                "torch.load without weights_only",
                            ),
                            detector: "TorchLoadUnsafeDetector".to_string(),
                            severity: Severity::Critical,
                            title: "torch.load() without weights_only=True".to_string(),
                            description: "torch.load() uses pickle by default, which can execute \
                                arbitrary code during deserialization. Malicious model files can \
                                compromise your system.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Add weights_only=True:\n\
                                ```python\n\
                                model = torch.load('model.pth', weights_only=True)\n\
                                ```\n\n\
                                If you need full pickle (trusted source only):\n\
                                ```python\n\
                                model = torch.load('model.pth', weights_only=False)  # explicitly unsafe\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-502".to_string()),
                            why_it_matters: Some(
                                "Pickle deserialization can execute arbitrary code. Attackers can \
                                craft malicious .pth files that run code when loaded. This is a \
                                common supply chain attack vector for ML models.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("TorchLoadUnsafeDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// NanEqualityDetector
// ============================================================================

/// Detects comparisons with NaN (always False due to IEEE 754)
pub struct NanEqualityDetector {
    repository_path: PathBuf,
}

impl NanEqualityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for NanEqualityDetector {
    fn name(&self) -> &'static str {
        "nan-equality"
    }

    fn description(&self) -> &'static str {
        "Detects comparisons with NaN (always False)"
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
            if !matches!(ext, "py" | "js" | "ts") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    if nan_equality().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "NanEqualityDetector",
                                &file_str,
                                line_num,
                                "NaN equality comparison",
                            ),
                            detector: "NanEqualityDetector".to_string(),
                            severity: Severity::High,
                            title: "NaN equality comparison (always False)".to_string(),
                            description: "Comparing values with NaN using == or != always returns \
                                False due to IEEE 754. NaN != NaN by definition.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use dedicated NaN-checking functions:\n\
                                ```python\n\
                                # NumPy\n\
                                np.isnan(x)\n\n\
                                # Pandas\n\
                                pd.isna(x) or pd.isnull(x)\n\n\
                                # Python math\n\
                                math.isnan(x)\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("correctness".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "This is a logic bug. Code like `if x == np.nan` will never execute \
                                the true branch, even when x is NaN. This causes silent data corruption \
                                in data pipelines.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("NanEqualityDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// MissingZeroGradDetector
// ============================================================================

/// Detects .backward() without zero_grad() in training loops
pub struct MissingZeroGradDetector {
    repository_path: PathBuf,
}

impl MissingZeroGradDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }

    /// Check if a file has both backward() and zero_grad()
    fn analyze_file(&self, content: &str, path: &std::path::Path) -> Vec<Finding> {
        let mut findings = vec![];
        let has_backward = backward_call().is_match(content);
        let has_zero_grad = zero_grad_call().is_match(content);

        if has_backward && !has_zero_grad {
            // Find the line with backward()
            for (i, line) in content.lines().enumerate() {
                if backward_call().is_match(line) {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;

                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "MissingZeroGradDetector",
                            &file_str,
                            line_num,
                            "backward without zero_grad",
                        ),
                        detector: "MissingZeroGradDetector".to_string(),
                        severity: Severity::High,
                        title: ".backward() without zero_grad()".to_string(),
                        description: "Calling .backward() without clearing gradients causes \
                            gradient accumulation. Gradients from previous batches add up, \
                            leading to incorrect updates.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some(
                            "Add optimizer.zero_grad() before backward():\n\
                            ```python\n\
                            optimizer.zero_grad()  # Clear gradients\n\
                            loss.backward()        # Compute gradients\n\
                            optimizer.step()       # Update weights\n\
                            ```".to_string()
                        ),
                        estimated_effort: Some("5 minutes".to_string()),
                        category: Some("correctness".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Without zero_grad(), gradients accumulate across batches. This causes \
                            training instability and incorrect weight updates. The model may fail \
                            to converge or produce wrong results.".to_string()
                        ),
                        ..Default::default()
                    });
                    break; // One finding per file
                }
            }
        }

        findings
    }
}

impl Detector for MissingZeroGradDetector {
    fn name(&self) -> &'static str {
        "missing-zero-grad"
    }

    fn description(&self) -> &'static str {
        "Detects .backward() without zero_grad()"
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                findings.extend(self.analyze_file(&content, path));
            }
        }

        info!("MissingZeroGradDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// ForwardMethodDetector
// ============================================================================

/// Detects model.forward() instead of model() - skips hooks
pub struct ForwardMethodDetector {
    repository_path: PathBuf,
}

impl ForwardMethodDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
        }
    }
}

impl Detector for ForwardMethodDetector {
    fn name(&self) -> &'static str {
        "forward-method"
    }

    fn description(&self) -> &'static str {
        "Detects model.forward() instead of model()"
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Skip files that don't use PyTorch
                if !content.contains("torch") && !content.contains("nn.Module") {
                    continue;
                }

                for (i, line) in content.lines().enumerate() {
                    // Skip definitions of forward method
                    if line.contains("def forward") {
                        continue;
                    }

                    if forward_method().is_match(line) {
                        // Skip valid .forward() patterns:
                        // - super().forward() - calling parent class
                        // - self.forward() - calling own method within class
                        // - .remote().forward() - RPC pipeline parallelism
                        // - .rpc_async().forward() - async RPC
                        if line.contains("super()")
                            || line.contains("super(")
                            || line.contains("self.forward")
                            || line.contains(".remote().forward")
                            || line.contains(".rpc_async().forward")
                            || line.contains(".rpc_sync().forward")
                        {
                            continue;
                        }

                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "ForwardMethodDetector",
                                &file_str,
                                line_num,
                                "direct forward() call",
                            ),
                            detector: "ForwardMethodDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Direct .forward() call instead of model()".to_string(),
                            description: "Calling model.forward() directly bypasses hooks \
                                (forward_pre_hooks, forward_hooks). Use model() instead."
                                .to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Call the model directly:\n\
                                ```python\n\
                                # Instead of:\n\
                                output = model.forward(x)\n\n\
                                # Use:\n\
                                output = model(x)\n\
                                ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("best-practice".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "PyTorch hooks (for debugging, profiling, gradient modification) \
                                are only triggered when calling model() via __call__. Direct \
                                forward() calls skip these hooks, breaking tools like SHAP, \
                                GradCAM, and profilers."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("ForwardMethodDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// MissingRandomSeedDetector
// ============================================================================

/// Detects ML code without random seed setting (reproducibility)
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Skip files that don't use pandas
                if !content.contains("pandas")
                    && !content.contains("import pd")
                    && !content.contains("as pd")
                {
                    continue;
                }

                for (i, line) in content.lines().enumerate() {
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                if !content.contains("torch") {
                    continue;
                }

                for (i, line) in content.lines().enumerate() {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_test_file(content: &str) -> (TempDir, PathBuf) {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.py");
        fs::write(&path, content).unwrap();
        (dir, path)
    }

    #[test]
    fn test_torch_load_unsafe() {
        let content = r#"
import torch
model = torch.load('model.pth')
"#;
        let (dir, _) = setup_test_file(content);
        let detector = TorchLoadUnsafeDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Critical);
    }

    #[test]
    fn test_torch_load_safe() {
        let content = r#"
import torch
model = torch.load('model.pth', weights_only=True)
"#;
        let (dir, _) = setup_test_file(content);
        let detector = TorchLoadUnsafeDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_nan_equality() {
        let content = r#"
import numpy as np
if x == np.nan:
    pass
"#;
        let (dir, _) = setup_test_file(content);
        let detector = NanEqualityDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_require_grad_typo() {
        let content = r#"
tensor.require_grad = True
"#;
        let (dir, _) = setup_test_file(content);
        let detector = RequireGradTypoDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_chain_indexing() {
        let content = r#"
import pandas as pd
df['col1']['col2'] = value
"#;
        let (dir, _) = setup_test_file(content);
        let detector = ChainIndexingDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }
}
