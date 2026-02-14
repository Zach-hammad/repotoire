//! Category-specific classification thresholds
//!
//! Different detector categories have different FP characteristics.
//! Security findings need high recall (don't miss real vulns).
//! Quality findings can tolerate more filtering.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Detector categories for threshold grouping
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DetectorCategory {
    /// SQL injection, XSS, command injection, path traversal
    Security,
    /// Complexity, coupling, dead code, god class
    CodeQuality,
    /// torch.load, zero_grad, NaN equality, etc.
    MachineLearning,
    /// N+1 queries, lazy loading
    Performance,
    /// Anything else
    Other,
}

impl DetectorCategory {
    /// Classify a detector name into a category
    pub fn from_detector(detector: &str) -> Self {
        let lower = detector.to_lowercase();
        
        // Security detectors (high recall needed - don't miss real vulns)
        if lower.contains("injection")
            || lower.contains("xss")
            || lower.contains("traversal")
            || lower.contains("crypto")
            || lower.contains("credential")
            || lower.contains("secret")
            || lower.contains("auth")
            || lower.contains("csrf")
            || lower.contains("ssrf")
            || lower.contains("xxe")
            || lower.contains("deserializ")
            || lower.contains("eval")
        {
            return Self::Security;
        }
        
        // ML/AI detectors
        if lower.contains("torch")
            || lower.contains("tensorflow")
            || lower.contains("keras")
            || lower.contains("pytorch")
            || lower.contains("grad")
            || lower.contains("nan")
            || lower.contains("forward")
            || lower.contains("seed")
            || lower.contains("chain_index")
            || lower.contains("deprecated")  // often ML API deprecations
        {
            return Self::MachineLearning;
        }
        
        // Performance detectors
        if lower.contains("n+1")
            || lower.contains("nplus")
            || lower.contains("lazy")
            || lower.contains("cache")
            || lower.contains("bottleneck")
            || lower.contains("performance")
        {
            return Self::Performance;
        }
        
        // Code quality (default for most code smell detectors)
        if lower.contains("complexity")
            || lower.contains("coupling")
            || lower.contains("dead")
            || lower.contains("unreachable")
            || lower.contains("god")
            || lower.contains("long")
            || lower.contains("envy")
            || lower.contains("intimacy")
            || lower.contains("duplicate")
            || lower.contains("magic")
            || lower.contains("inconsistent")
            || lower.contains("centrality")
            || lower.contains("cohesion")
        {
            return Self::CodeQuality;
        }
        
        Self::Other
    }
}

/// Thresholds for each detector category
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CategoryThresholds {
    /// Threshold configs per category
    configs: HashMap<DetectorCategory, ThresholdConfig>,
    /// Default threshold if category not found
    default: ThresholdConfig,
}

/// Configuration for a single category's threshold
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThresholdConfig {
    /// Minimum TP probability to keep finding (filter threshold)
    pub filter_threshold: f32,
    /// Threshold to mark as "high confidence TP"
    pub high_confidence_threshold: f32,
    /// Threshold to mark as "likely FP" (for flagging)
    pub likely_fp_threshold: f32,
    /// Weight adjustment for this category's features
    pub feature_weight_multiplier: f32,
}

impl Default for ThresholdConfig {
    fn default() -> Self {
        Self {
            filter_threshold: 0.5,
            high_confidence_threshold: 0.8,
            likely_fp_threshold: 0.3,
            feature_weight_multiplier: 1.0,
        }
    }
}

impl Default for CategoryThresholds {
    fn default() -> Self {
        let mut configs = HashMap::new();
        
        // Security: Conservative thresholds (high recall, don't miss real vulns)
        // Lower filter threshold = keep more findings
        configs.insert(DetectorCategory::Security, ThresholdConfig {
            filter_threshold: 0.35,        // Keep anything with 35%+ TP chance
            high_confidence_threshold: 0.85,
            likely_fp_threshold: 0.2,      // Only mark FP if very confident
            feature_weight_multiplier: 1.2, // Boost security feature signals
        });
        
        // Code Quality: More aggressive filtering (FPs are annoying, not dangerous)
        configs.insert(DetectorCategory::CodeQuality, ThresholdConfig {
            filter_threshold: 0.52,        // Filter aggressively
            high_confidence_threshold: 0.75,
            likely_fp_threshold: 0.45,     // Mark FP more readily
            feature_weight_multiplier: 1.0,
        });
        
        // ML/AI: Moderate thresholds (domain-specific, need accuracy)
        configs.insert(DetectorCategory::MachineLearning, ThresholdConfig {
            filter_threshold: 0.45,
            high_confidence_threshold: 0.8,
            likely_fp_threshold: 0.35,
            feature_weight_multiplier: 1.1,
        });
        
        // Performance: Filter aggressively in utility code
        configs.insert(DetectorCategory::Performance, ThresholdConfig {
            filter_threshold: 0.52,         // Slightly more aggressive
            high_confidence_threshold: 0.75,
            likely_fp_threshold: 0.40,
            feature_weight_multiplier: 1.0,
        });
        
        // Other: Default balanced thresholds
        configs.insert(DetectorCategory::Other, ThresholdConfig::default());
        
        Self {
            configs,
            default: ThresholdConfig::default(),
        }
    }
}

impl CategoryThresholds {
    /// Create new thresholds with default values
    pub fn new() -> Self {
        Self::default()
    }
    
    /// Get threshold config for a detector
    pub fn get(&self, detector: &str) -> &ThresholdConfig {
        let category = DetectorCategory::from_detector(detector);
        self.configs.get(&category).unwrap_or(&self.default)
    }
    
    /// Get threshold config for a category
    pub fn get_category(&self, category: DetectorCategory) -> &ThresholdConfig {
        self.configs.get(&category).unwrap_or(&self.default)
    }
    
    /// Update threshold for a category
    pub fn set(&mut self, category: DetectorCategory, config: ThresholdConfig) {
        self.configs.insert(category, config);
    }
    
    /// Load thresholds from JSON file
    pub fn load(path: &std::path::Path) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(path)?;
        serde_json::from_str(&content)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
    }
    
    /// Save thresholds to JSON file
    pub fn save(&self, path: &std::path::Path) -> Result<(), std::io::Error> {
        let content = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        std::fs::write(path, content)
    }
    
    /// Should this finding be filtered out (likely FP)?
    pub fn should_filter(&self, detector: &str, tp_probability: f32) -> bool {
        let config = self.get(detector);
        tp_probability < config.filter_threshold
    }
    
    /// Is this a high-confidence true positive?
    pub fn is_high_confidence(&self, detector: &str, tp_probability: f32) -> bool {
        let config = self.get(detector);
        tp_probability >= config.high_confidence_threshold
    }
    
    /// Should this be flagged as likely FP (for review)?
    pub fn is_likely_fp(&self, detector: &str, tp_probability: f32) -> bool {
        let config = self.get(detector);
        tp_probability < config.likely_fp_threshold
    }
}

/// Prediction with category-aware classification
#[derive(Debug, Clone)]
pub struct CategoryAwarePrediction {
    /// Raw TP probability from model
    pub tp_probability: f32,
    /// Raw FP probability from model  
    pub fp_probability: f32,
    /// Detector category
    pub category: DetectorCategory,
    /// Final verdict considering category threshold
    pub is_true_positive: bool,
    /// High confidence flag
    pub high_confidence: bool,
    /// Likely FP flag (for review)
    pub likely_fp: bool,
    /// Should be filtered out
    pub should_filter: bool,
}

impl CategoryAwarePrediction {
    /// Create from raw prediction and detector name
    pub fn from_prediction(
        tp_probability: f32,
        detector: &str,
        thresholds: &CategoryThresholds,
    ) -> Self {
        let category = DetectorCategory::from_detector(detector);
        let config = thresholds.get_category(category);
        
        Self {
            tp_probability,
            fp_probability: 1.0 - tp_probability,
            category,
            is_true_positive: tp_probability >= config.filter_threshold,
            high_confidence: tp_probability >= config.high_confidence_threshold,
            likely_fp: tp_probability < config.likely_fp_threshold,
            should_filter: tp_probability < config.filter_threshold,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_detector_categorization() {
        assert_eq!(
            DetectorCategory::from_detector("SQLInjectionDetector"),
            DetectorCategory::Security
        );
        assert_eq!(
            DetectorCategory::from_detector("CommandInjectionDetector"),
            DetectorCategory::Security
        );
        assert_eq!(
            DetectorCategory::from_detector("TorchLoadUnsafeDetector"),
            DetectorCategory::MachineLearning
        );
        assert_eq!(
            DetectorCategory::from_detector("MissingZeroGradDetector"),
            DetectorCategory::MachineLearning
        );
        assert_eq!(
            DetectorCategory::from_detector("ComplexitySpike"),
            DetectorCategory::CodeQuality
        );
        assert_eq!(
            DetectorCategory::from_detector("NPlusOneDetector"),
            DetectorCategory::Performance
        );
        assert_eq!(
            DetectorCategory::from_detector("SomeRandomDetector"),
            DetectorCategory::Other
        );
    }
    
    #[test]
    fn test_category_thresholds() {
        let thresholds = CategoryThresholds::default();
        
        // Security should have lower filter threshold (more permissive)
        let security = thresholds.get("SQLInjectionDetector");
        let quality = thresholds.get("ComplexitySpike");
        
        assert!(security.filter_threshold < quality.filter_threshold);
    }
    
    #[test]
    fn test_filtering_decisions() {
        let thresholds = CategoryThresholds::default();
        
        // Security with 40% TP should NOT be filtered (threshold is 35%)
        assert!(!thresholds.should_filter("SQLInjectionDetector", 0.40));
        
        // Code quality with 40% TP SHOULD be filtered (threshold is 55%)
        assert!(thresholds.should_filter("ComplexitySpike", 0.40));
        
        // Both with 60% should pass
        assert!(!thresholds.should_filter("SQLInjectionDetector", 0.60));
        assert!(!thresholds.should_filter("ComplexitySpike", 0.60));
    }
    
    #[test]
    fn test_category_aware_prediction() {
        let thresholds = CategoryThresholds::default();
        
        // Security finding at 40% - should NOT be filtered
        let pred = CategoryAwarePrediction::from_prediction(
            0.40,
            "SQLInjectionDetector",
            &thresholds,
        );
        assert!(!pred.should_filter);
        assert!(pred.is_true_positive);
        assert!(!pred.high_confidence);
        
        // Quality finding at 40% - SHOULD be filtered
        let pred = CategoryAwarePrediction::from_prediction(
            0.40,
            "ComplexitySpike",
            &thresholds,
        );
        assert!(pred.should_filter);
        assert!(!pred.is_true_positive);
    }
    
    #[test]
    fn test_save_load() {
        let thresholds = CategoryThresholds::default();
        let path = std::path::Path::new("/tmp/test_thresholds.json");
        
        thresholds.save(path).unwrap();
        let loaded = CategoryThresholds::load(path).unwrap();
        
        // Check a specific value
        let orig = thresholds.get("SQLInjectionDetector");
        let load = loaded.get("SQLInjectionDetector");
        assert_eq!(orig.filter_threshold, load.filter_threshold);
        
        std::fs::remove_file(path).ok();
    }
}
