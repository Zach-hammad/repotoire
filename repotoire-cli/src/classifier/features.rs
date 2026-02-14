//! Feature extraction for FP classification
//!
//! Extracts numerical features from findings for classification.

use crate::models::{Finding, Severity};
use std::collections::HashMap;

/// Feature vector for a finding
#[derive(Debug, Clone)]
pub struct Features {
    /// Raw feature values
    pub values: Vec<f32>,
}

impl Features {
    pub fn new(values: Vec<f32>) -> Self {
        Self { values }
    }
    
    pub fn len(&self) -> usize {
        self.values.len()
    }
    
    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }
}

/// Extracts features from findings
pub struct FeatureExtractor {
    /// Known detector names for one-hot encoding
    detector_vocab: HashMap<String, usize>,
    /// Code pattern keywords
    code_patterns: Vec<&'static str>,
    /// Path patterns that suggest FP
    fp_path_patterns: Vec<&'static str>,
    /// Path patterns that suggest TP
    tp_path_patterns: Vec<&'static str>,
}

impl FeatureExtractor {
    pub fn new() -> Self {
        // Common detectors (order matters for one-hot)
        let detectors = vec![
            "SQLInjectionDetector",
            "CommandInjectionDetector",
            "PathTraversalDetector",
            "XssDetector",
            "InsecureCryptoDetector",
            "TorchLoadUnsafeDetector",
            "DeadCodeDetector",
            "UnreachableCodeDetector",
            "LongMethodsDetector",
            "GodClassDetector",
            "FeatureEnvyDetector",
            "ComplexitySpike",
            "MagicNumbersDetector",
            "NPlusOneDetector",
            "InconsistentReturnsDetector",
        ];
        
        let detector_vocab: HashMap<String, usize> = detectors
            .into_iter()
            .enumerate()
            .map(|(i, d)| (d.to_string(), i))
            .collect();
        
        // Patterns in code that suggest FP
        let code_patterns = vec![
            // Test patterns (likely FP)
            "test", "mock", "stub", "fake", "fixture", "spec",
            "assert", "expect", "should",
            // Config patterns (likely FP)  
            "config", "env", "settings", "constant",
            // Generated code (likely FP)
            "generated", "auto-generated", "@generated",
            // Framework patterns (context-dependent)
            "orm", "query", "model", "schema",
            // Security-relevant (likely TP)
            "user_input", "request", "params", "body",
            "exec", "eval", "shell", "system",
            "password", "secret", "token", "key",
        ];
        
        // Path patterns suggesting FP
        let fp_path_patterns = vec![
            "test", "tests", "spec", "specs",
            "__test__", "__tests__",
            "fixture", "fixtures",
            "mock", "mocks",
            "example", "examples",
            "demo", "sample",
            "vendor", "node_modules",
            "generated", "dist", "build",
            // Utility scripts (not production code)
            "scripts", "script", "tools", "tool",
            "bin", "benchmark", "benchmarks",
            "docs", "documentation",
            // Python utilities often have these patterns
            "fix_agent", "helper", "util", "utils",
            // CLI modules are orchestrators, expected complexity
            "cli/", "/cli",
        ];
        
        // Path patterns suggesting TP
        let tp_path_patterns = vec![
            "src", "lib", "app",
            "api", "routes", "handlers",
            "controller", "service",
            "auth", "security",
        ];
        
        Self {
            detector_vocab,
            code_patterns,
            fp_path_patterns,
            tp_path_patterns,
        }
    }
    
    /// Extract feature vector from a finding
    pub fn extract(&self, finding: &Finding) -> Features {
        let mut features = Vec::new();
        
        // 1. Detector one-hot encoding (15 features)
        let mut detector_onehot = vec![0.0f32; self.detector_vocab.len()];
        if let Some(&idx) = self.detector_vocab.get(&finding.detector) {
            detector_onehot[idx] = 1.0;
        }
        features.extend(detector_onehot);
        
        // 2. Severity encoding (4 features)
        features.push(if finding.severity == Severity::Critical { 1.0 } else { 0.0 });
        features.push(if finding.severity == Severity::High { 1.0 } else { 0.0 });
        features.push(if finding.severity == Severity::Medium { 1.0 } else { 0.0 });
        features.push(if finding.severity == Severity::Low { 1.0 } else { 0.0 });
        
        // 3. Code pattern features
        let desc_lower = finding.description.to_lowercase();
        let title_lower = finding.title.to_lowercase();
        let combined = format!("{} {}", desc_lower, title_lower);
        
        for pattern in &self.code_patterns {
            features.push(if combined.contains(pattern) { 1.0 } else { 0.0 });
        }
        
        // 4. Path pattern features
        let path = finding.affected_files
            .first()
            .map(|p| p.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        
        // FP path indicators
        let fp_path_score: f32 = self.fp_path_patterns
            .iter()
            .filter(|p| path.contains(*p))
            .count() as f32;
        features.push(fp_path_score);
        
        // TP path indicators  
        let tp_path_score: f32 = self.tp_path_patterns
            .iter()
            .filter(|p| path.contains(*p))
            .count() as f32;
        features.push(tp_path_score);
        
        // 5. Numeric features
        // Line count (normalized)
        let line_span = finding.line_end.unwrap_or(1) - finding.line_start.unwrap_or(1) + 1;
        features.push((line_span as f32).min(100.0) / 100.0);
        
        // Description length (normalized, longer = more context = more likely TP)
        features.push((finding.description.len() as f32).min(1000.0) / 1000.0);
        
        // Has suggested fix (more likely TP if tool knows how to fix it)
        features.push(if finding.suggested_fix.is_some() { 1.0 } else { 0.0 });
        
        // Has CWE ID (security-focused = more likely TP)
        features.push(if finding.cwe_id.is_some() { 1.0 } else { 0.0 });
        
        Features::new(features)
    }
    
    /// Number of features extracted
    pub fn feature_count(&self) -> usize {
        self.detector_vocab.len()  // detector one-hot
            + 4                     // severity
            + self.code_patterns.len()  // code patterns
            + 2                     // path scores
            + 4                     // numeric features
    }
}

impl Default for FeatureExtractor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    
    #[test]
    fn test_feature_extraction() {
        let extractor = FeatureExtractor::new();
        
        let finding = Finding {
            id: "test".into(),
            detector: "SQLInjectionDetector".into(),
            severity: Severity::High,
            title: "SQL Injection in query".into(),
            description: "User input passed to exec()".into(),
            affected_files: vec![PathBuf::from("src/api/users.py")],
            line_start: Some(10),
            line_end: Some(15),
            suggested_fix: Some("Use parameterized queries".into()),
            cwe_id: Some("CWE-89".into()),
            ..Default::default()
        };
        
        let features = extractor.extract(&finding);
        
        // Check we got the right number of features
        assert_eq!(features.len(), extractor.feature_count());
        
        // Check detector one-hot (SQL injection should be index 0)
        assert_eq!(features.values[0], 1.0);
        
        // Check severity (HIGH should be set)
        let severity_start = extractor.detector_vocab.len();
        assert_eq!(features.values[severity_start + 1], 1.0); // HIGH
    }
}
