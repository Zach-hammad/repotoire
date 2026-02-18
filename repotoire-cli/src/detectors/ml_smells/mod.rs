//! ML/AI Code Smell Detectors
//!
//! Detectors for machine learning and data science code, inspired by:
//! - TorchFix (pytorch-labs)
//! - dslinter (SERG-Delft)
//! - MLScent & SpecDetect4AI (arXiv research)
//!
//! Covers PyTorch, TensorFlow, Scikit-Learn, Pandas, NumPy patterns.

mod training;
mod data_patterns;

pub use training::{TorchLoadUnsafeDetector, NanEqualityDetector, MissingZeroGradDetector, ForwardMethodDetector};
pub use data_patterns::{MissingRandomSeedDetector, ChainIndexingDetector, RequireGradTypoDetector, DeprecatedTorchApiDetector};

use regex::Regex;
use std::sync::OnceLock;

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

pub(crate) fn torch_load() -> &'static Regex {
    TORCH_LOAD.get_or_init(|| Regex::new(r"torch\.load\s*\(").expect("valid regex"))
}
pub(crate) fn torch_load_weights_only() -> &'static Regex {
    TORCH_LOAD_WEIGHTS_ONLY.get_or_init(|| Regex::new(r"weights_only\s*=\s*True").expect("valid regex"))
}
pub(crate) fn nan_equality() -> &'static Regex {
    NAN_EQUALITY.get_or_init(|| {
        Regex::new(r#"(?:==|!=|is|is not)\s*(?:np\.nan|float\(['"]nan['"]\)|math\.nan|torch\.nan|numpy\.nan)"#).expect("valid regex")
    })
}
pub(crate) fn backward_call() -> &'static Regex {
    BACKWARD_CALL.get_or_init(|| Regex::new(r"\.backward\s*\(").expect("valid regex"))
}
pub(crate) fn zero_grad_call() -> &'static Regex {
    ZERO_GRAD_CALL.get_or_init(|| Regex::new(r"\.zero_grad\s*\(|optimizer\.zero_grad").expect("valid regex"))
}
pub(crate) fn forward_method() -> &'static Regex {
    FORWARD_METHOD.get_or_init(|| Regex::new(r"\.\s*forward\s*\(").expect("valid regex"))
}
pub(crate) fn manual_seed() -> &'static Regex {
    MANUAL_SEED.get_or_init(|| {
        Regex::new(r"(?:torch\.manual_seed|torch\.cuda\.manual_seed|np\.random\.seed|random\.seed|tf\.random\.set_seed|set_random_seed)").expect("valid regex")
    })
}
pub(crate) fn chain_index() -> &'static Regex {
    CHAIN_INDEX.get_or_init(|| Regex::new(r#"\w+\[['"][^'"]+['"]\]\s*\[['"][^'"]+['"]\]"#).expect("valid regex"))
}
pub(crate) fn pca_svm_call() -> &'static Regex {
    PCA_SVM_CALL.get_or_init(|| {
        Regex::new(r"(?:PCA|SVC|SVR|SGDClassifier|SGDRegressor|MLPClassifier|MLPRegressor|KMeans|DBSCAN|Lasso|Ridge|ElasticNet)\s*\(").expect("valid regex")
    })
}
pub(crate) fn scaler_call() -> &'static Regex {
    SCALER_CALL.get_or_init(|| {
        Regex::new(r"(?:StandardScaler|MinMaxScaler|RobustScaler|Normalizer|MaxAbsScaler)\s*\(").expect("valid regex")
    })
}
pub(crate) fn require_grad_typo() -> &'static Regex {
    REQUIRE_GRAD_TYPO.get_or_init(|| Regex::new(r"\.require_grad\s*=|require_grad\s*=\s*True").expect("valid regex"))
}
pub(crate) fn deprecated_torch() -> &'static Regex {
    DEPRECATED_TORCH.get_or_init(|| {
        Regex::new(r"torch\.(?:solve|symeig|qr|cholesky|chain_matmul|range)\s*\(").expect("valid regex")
    })
}
pub(crate) fn dataloader_shuffle() -> &'static Regex {
    DATALOADER_SHUFFLE.get_or_init(|| Regex::new(r"DataLoader\s*\([^)]*shuffle\s*=\s*True").expect("valid regex"))
}
pub(crate) fn eval_mode() -> &'static Regex {
    EVAL_MODE.get_or_init(|| Regex::new(r"\.eval\s*\(").expect("valid regex"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;
    use crate::models::Severity;
    use crate::detectors::base::Detector;
    use std::fs;
    use std::path::PathBuf;
    use tempfile::TempDir;

    fn setup_test_file(content: &str) -> (TempDir, PathBuf) {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.py");
        fs::write(&path, content).unwrap();
        (dir, path)
    }

    #[test]
    fn test_torch_load_unsafe() {
        let content = "import torch\nmodel = torch.load('model.pth')\n";
        let (dir, _) = setup_test_file(content);
        let detector = TorchLoadUnsafeDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Critical);
    }

    #[test]
    fn test_torch_load_safe() {
        let content = "import torch\nmodel = torch.load('model.pth', weights_only=True)\n";
        let (dir, _) = setup_test_file(content);
        let detector = TorchLoadUnsafeDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_nan_equality() {
        let content = "import numpy as np\nif x == np.nan:\n    pass\n";
        let (dir, _) = setup_test_file(content);
        let detector = NanEqualityDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_require_grad_typo() {
        let content = "tensor.require_grad = True\n";
        let (dir, _) = setup_test_file(content);
        let detector = RequireGradTypoDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_chain_indexing() {
        let content = "import pandas as pd\ndf['col1']['col2'] = value\n";
        let (dir, _) = setup_test_file(content);
        let detector = ChainIndexingDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }
}
