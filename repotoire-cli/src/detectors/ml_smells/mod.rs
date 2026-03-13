//! ML/AI Code Smell Detectors
//!
//! Detectors for machine learning and data science code, inspired by:
//! - TorchFix (pytorch-labs)
//! - dslinter (SERG-Delft)
//! - MLScent & SpecDetect4AI (arXiv research)
//!
//! Covers PyTorch, TensorFlow, Scikit-Learn, Pandas, NumPy patterns.

mod data_patterns;
mod training;

pub use data_patterns::{
    ChainIndexingDetector, DeprecatedTorchApiDetector, MissingRandomSeedDetector,
    RequireGradTypoDetector,
};
pub use training::{
    ForwardMethodDetector, MissingZeroGradDetector, NanEqualityDetector, TorchLoadUnsafeDetector,
};

use regex::Regex;
use std::sync::LazyLock;

static TORCH_LOAD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"torch\.load\s*\(").expect("valid regex"));
static TORCH_LOAD_WEIGHTS_ONLY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"weights_only\s*=\s*True").expect("valid regex"));
static NAN_EQUALITY: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?:==|!=|is|is not)\s*(?:np\.nan|float\(['"]nan['"]\)|math\.nan|torch\.nan|numpy\.nan)"#).expect("valid regex")
    });
static BACKWARD_CALL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.backward\s*\(").expect("valid regex"));
static ZERO_GRAD_CALL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.zero_grad\s*\(|optimizer\.zero_grad").expect("valid regex"));
static FORWARD_METHOD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.\s*forward\s*\(").expect("valid regex"));
static MANUAL_SEED: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?:torch\.manual_seed|torch\.cuda\.manual_seed|np\.random\.seed|random\.seed|tf\.random\.set_seed|set_random_seed)").expect("valid regex")
    });
static CHAIN_INDEX: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"\w+\[['"][^'"]+['"]\]\s*\[['"][^'"]+['"]\]"#).expect("valid regex")
    });
#[allow(dead_code)] // Prepared for future ML smell detectors
static PCA_SVM_CALL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?:PCA|SVC|SVR|SGDClassifier|SGDRegressor|MLPClassifier|MLPRegressor|KMeans|DBSCAN|Lasso|Ridge|ElasticNet)\s*\(").expect("valid regex")
    });
#[allow(dead_code)]
static SCALER_CALL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?:StandardScaler|MinMaxScaler|RobustScaler|Normalizer|MaxAbsScaler)\s*\(")
            .expect("valid regex")
    });
static REQUIRE_GRAD_TYPO: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"\.require_grad\s*=|require_grad\s*=\s*True").expect("valid regex")
    });
#[allow(dead_code)]
static DEPRECATED_TORCH: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"torch\.(?:solve|symeig|qr|cholesky|chain_matmul|range)\s*\(")
            .expect("valid regex")
    });
#[allow(dead_code)]
static DATALOADER_SHUFFLE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"DataLoader\s*\([^)]*shuffle\s*=\s*True").expect("valid regex"));
#[allow(dead_code)]
static EVAL_MODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.eval\s*\(").expect("valid regex"));

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;
    use crate::models::Severity;

    #[test]
    fn test_torch_load_unsafe() {
        let graph = GraphStore::in_memory();
        let detector = TorchLoadUnsafeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.py", "import torch\nmodel = torch.load('model.pth')\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Critical);
    }

    #[test]
    fn test_torch_load_safe() {
        let graph = GraphStore::in_memory();
        let detector = TorchLoadUnsafeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.py", "import torch\nmodel = torch.load('model.pth', weights_only=True)\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_nan_equality() {
        let graph = GraphStore::in_memory();
        let detector = NanEqualityDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.py", "import numpy as np\nif x == np.nan:\n    pass\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_require_grad_typo() {
        let graph = GraphStore::in_memory();
        let detector = RequireGradTypoDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.py", "tensor.require_grad = True\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_chain_indexing() {
        let graph = GraphStore::in_memory();
        let detector = ChainIndexingDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.py", "import pandas as pd\ndf['col1']['col2'] = value\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }
}
