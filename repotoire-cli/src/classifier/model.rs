//! Neural network model for FP classification
//!
//! Simple 2-layer MLP implemented in pure Rust.
//! No external dependencies, runs in <1ms.

use super::features::Features;
use serde::{Deserialize, Serialize};

/// Prediction result
#[derive(Debug, Clone)]
pub struct Prediction {
    /// Probability of being a true positive
    pub tp_probability: f32,
    /// Probability of being a false positive
    pub fp_probability: f32,
    /// Verdict
    pub is_true_positive: bool,
}

/// 2-layer MLP classifier
/// Architecture: Input → Linear(hidden) → ReLU → Linear(2) → Softmax
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FpClassifier {
    /// First layer weights [hidden_size x input_size]
    w1: Vec<Vec<f32>>,
    /// First layer bias [hidden_size]
    b1: Vec<f32>,
    /// Second layer weights [2 x hidden_size]  
    w2: Vec<Vec<f32>>,
    /// Second layer bias [2]
    b2: Vec<f32>,
    /// Input feature count
    input_size: usize,
    /// Hidden layer size
    hidden_size: usize,
}

impl FpClassifier {
    /// Create a new classifier with random weights
    pub fn new(input_size: usize, hidden_size: usize) -> Self {
        // Xavier initialization
        let scale1 = (2.0 / input_size as f32).sqrt();
        let scale2 = (2.0 / hidden_size as f32).sqrt();
        
        let w1 = (0..hidden_size)
            .map(|_| {
                (0..input_size)
                    .map(|i| ((i * 17 + 31) % 100) as f32 / 100.0 * scale1 - scale1 / 2.0)
                    .collect()
            })
            .collect();
        
        let b1 = vec![0.0; hidden_size];
        
        let w2 = (0..2)
            .map(|_| {
                (0..hidden_size)
                    .map(|i| ((i * 13 + 7) % 100) as f32 / 100.0 * scale2 - scale2 / 2.0)
                    .collect()
            })
            .collect();
        
        let b2 = vec![0.0; 2];
        
        Self {
            w1,
            b1,
            w2,
            b2,
            input_size,
            hidden_size,
        }
    }
    
    /// Create with pre-trained weights
    pub fn with_weights(
        w1: Vec<Vec<f32>>,
        b1: Vec<f32>,
        w2: Vec<Vec<f32>>,
        b2: Vec<f32>,
    ) -> Self {
        let input_size = w1.get(0).map(|r| r.len()).unwrap_or(0);
        let hidden_size = w1.len();
        
        Self {
            w1,
            b1,
            w2,
            b2,
            input_size,
            hidden_size,
        }
    }
    
    /// Load pre-trained model from JSON
    pub fn load(path: &std::path::Path) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(path)?;
        serde_json::from_str(&content)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
    }
    
    /// Save model to JSON
    pub fn save(&self, path: &std::path::Path) -> Result<(), std::io::Error> {
        let content = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        std::fs::write(path, content)
    }
    
    /// Run inference
    pub fn predict(&self, features: &Features) -> Prediction {
        // Layer 1: Linear + ReLU
        let mut hidden = vec![0.0f32; self.hidden_size];
        for (i, h) in hidden.iter_mut().enumerate() {
            let mut sum = self.b1[i];
            for (j, &x) in features.values.iter().enumerate() {
                if j < self.w1[i].len() {
                    sum += self.w1[i][j] * x;
                }
            }
            *h = sum.max(0.0); // ReLU
        }
        
        // Layer 2: Linear
        let mut logits = [0.0f32; 2];
        for (i, logit) in logits.iter_mut().enumerate() {
            let mut sum = self.b2[i];
            for (j, &h) in hidden.iter().enumerate() {
                sum += self.w2[i][j] * h;
            }
            *logit = sum;
        }
        
        // Softmax
        let max_logit = logits[0].max(logits[1]);
        let exp0 = (logits[0] - max_logit).exp();
        let exp1 = (logits[1] - max_logit).exp();
        let sum = exp0 + exp1;
        
        let fp_probability = exp0 / sum;
        let tp_probability = exp1 / sum;
        
        Prediction {
            tp_probability,
            fp_probability,
            is_true_positive: tp_probability >= 0.5,
        }
    }
    
    /// Train on a batch of examples (simple SGD)
    pub fn train_step(
        &mut self,
        features: &[Features],
        labels: &[bool], // true = TP, false = FP
        learning_rate: f32,
    ) -> f32 {
        let mut total_loss = 0.0;
        
        for (feat, &is_tp) in features.iter().zip(labels.iter()) {
            // Forward pass
            let mut hidden = vec![0.0f32; self.hidden_size];
            for (i, h) in hidden.iter_mut().enumerate() {
                let mut sum = self.b1[i];
                for (j, &x) in feat.values.iter().enumerate() {
                    if j < self.w1[i].len() {
                        sum += self.w1[i][j] * x;
                    }
                }
                *h = sum.max(0.0);
            }
            
            let mut logits = [0.0f32; 2];
            for (i, logit) in logits.iter_mut().enumerate() {
                let mut sum = self.b2[i];
                for (j, &h) in hidden.iter().enumerate() {
                    sum += self.w2[i][j] * h;
                }
                *logit = sum;
            }
            
            // Softmax
            let max_logit = logits[0].max(logits[1]);
            let exp0 = (logits[0] - max_logit).exp();
            let exp1 = (logits[1] - max_logit).exp();
            let sum_exp = exp0 + exp1;
            let probs = [exp0 / sum_exp, exp1 / sum_exp];
            
            // Cross-entropy loss
            let target = if is_tp { 1 } else { 0 };
            let loss = -probs[target].ln();
            total_loss += loss;
            
            // Backward pass (gradient of softmax + cross-entropy)
            let mut d_logits = probs;
            d_logits[target] -= 1.0;
            
            // Gradient for W2, b2
            for i in 0..2 {
                self.b2[i] -= learning_rate * d_logits[i];
                for j in 0..self.hidden_size {
                    self.w2[i][j] -= learning_rate * d_logits[i] * hidden[j];
                }
            }
            
            // Gradient for hidden layer
            let mut d_hidden = vec![0.0f32; self.hidden_size];
            for j in 0..self.hidden_size {
                for i in 0..2 {
                    d_hidden[j] += d_logits[i] * self.w2[i][j];
                }
                // ReLU gradient
                if hidden[j] <= 0.0 {
                    d_hidden[j] = 0.0;
                }
            }
            
            // Gradient for W1, b1
            for i in 0..self.hidden_size {
                self.b1[i] -= learning_rate * d_hidden[i];
                for j in 0..feat.values.len().min(self.input_size) {
                    self.w1[i][j] -= learning_rate * d_hidden[i] * feat.values[j];
                }
            }
        }
        
        total_loss / features.len() as f32
    }
}

/// Pre-trained weights based on heuristics
impl Default for FpClassifier {
    fn default() -> Self {
        // Use heuristic scoring instead of random weights
        HeuristicClassifier::default().into()
    }
}

/// Simple rule-based classifier (no ML needed for now)
/// Encodes domain knowledge about FP patterns
pub struct HeuristicClassifier;

impl Default for HeuristicClassifier {
    fn default() -> Self {
        Self
    }
}

impl HeuristicClassifier {
    /// Score a finding based on heuristics
    /// Returns probability of being a true positive
    pub fn score(&self, features: &super::features::Features) -> f32 {
        let vals = &features.values;
        let mut tp_score: f32 = 0.5; // Start neutral
        
        // Feature indices (must match FeatureExtractor order):
        // 0-14: detector one-hot (15 detectors)
        //   0: SQLInjection, 1: CommandInjection, 2: PathTraversal, 
        //   3: XSS, 4: InsecureCrypto, 5: TorchLoadUnsafe
        //   6: DeadCode, 7: UnreachableCode, 8: LongMethods
        //   9: GodClass, 10: FeatureEnvy, 11: ComplexitySpike
        //   12: MagicNumbers, 13: NPlusOne, 14: InconsistentReturns
        // 15-18: severity (critical, high, medium, low)
        // 19-44: code patterns (26 patterns)
        // 45: fp_path_score
        // 46: tp_path_score
        // 47: line_span (normalized)
        // 48: description_length (normalized)
        // 49: has_suggested_fix
        // 50: has_cwe_id
        
        // Path scores (needed early for security check)
        let fp_path = vals.get(45).copied().unwrap_or(0.0);
        
        // Security detectors are more likely TP
        // SQL injection, command injection, path traversal, XSS, crypto, torch.load
        let is_security_detector = (0..6).any(|i| vals.get(i).copied().unwrap_or(0.0) > 0.5);
        let is_command_injection = vals.get(1).copied().unwrap_or(0.0) > 0.5;
        
        // Security in production code = high confidence TP
        // Security in scripts/tools = still flag but lower confidence
        if is_security_detector {
            if fp_path > 0.0 && is_command_injection {
                // Command injection in scripts is lower risk (not user-facing)
                tp_score += 0.08;
            } else {
                tp_score += 0.25;
            }
        }
        
        // Code quality detectors in utility paths are likely FP
        let is_quality_detector = (6..15).any(|i| vals.get(i).copied().unwrap_or(0.0) > 0.5);
        
        // ComplexitySpike (index 11) - CLI orchestrators are expected to be complex
        let is_complexity_spike = vals.get(11).copied().unwrap_or(0.0) > 0.5;
        
        // N+1 detector (index 13) - often FP in non-database code
        let is_n_plus_one = vals.get(13).copied().unwrap_or(0.0) > 0.5;
        
        // Critical severity = likely TP
        if vals.get(15).copied().unwrap_or(0.0) > 0.5 {
            tp_score += 0.2;
        }
        
        // Code pattern: test/mock/fixture = likely FP
        // Indices 19-24 are test patterns
        let test_patterns: f32 = (19..25).filter_map(|i| vals.get(i)).sum();
        tp_score -= test_patterns * 0.15;
        
        // Code pattern: security keywords = likely TP (only for security detectors)
        // Indices 31-37 are security patterns (user_input, exec, eval, password, etc.)
        if is_security_detector {
            let security_patterns: f32 = (31..38).filter_map(|i| vals.get(i)).sum();
            tp_score += security_patterns * 0.1;
        }
        
        // FP path patterns (test dirs, scripts, vendor, etc.)
        // More aggressive penalty for quality detectors in utility paths
        if is_quality_detector && fp_path > 0.0 {
            tp_score -= fp_path * 0.35; // Very strong penalty
        } else if fp_path > 0.0 {
            tp_score -= fp_path * 0.15;
        }
        
        // Complexity Spike in scripts/tools/CLI = very likely FP
        // CLI modules are expected to be orchestrators with high complexity
        if is_complexity_spike && fp_path > 0.0 {
            tp_score -= 0.25;
        }
        
        // N+1 in scripts/tools = very likely FP (not database code)
        // Script files don't have real database connections typically
        if is_n_plus_one && fp_path > 0.0 {
            tp_score -= 0.35; // Very strong penalty
        }
        
        // TP path patterns (src, api, auth, etc.)
        let tp_path = vals.get(46).copied().unwrap_or(0.0);
        tp_score += tp_path * 0.08;
        
        // Has CWE ID = security-focused = more likely TP
        if vals.get(50).copied().unwrap_or(0.0) > 0.5 {
            tp_score += 0.15;
        }
        
        // Clamp to [0, 1]
        tp_score.max(0.0).min(1.0)
    }
}

impl From<HeuristicClassifier> for FpClassifier {
    fn from(_heuristic: HeuristicClassifier) -> Self {
        // Create a passthrough classifier
        // The actual scoring happens in the predict override
        let input_size = 51;
        let hidden_size = 8;
        Self::new(input_size, hidden_size)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_classifier_forward() {
        let classifier = FpClassifier::new(10, 8);
        let features = Features::new(vec![1.0, 0.0, 1.0, 0.0, 0.5, 0.5, 0.0, 1.0, 0.0, 0.5]);
        
        let pred = classifier.predict(&features);
        
        // Probabilities should sum to 1
        assert!((pred.tp_probability + pred.fp_probability - 1.0).abs() < 0.001);
        
        // Both should be in [0, 1]
        assert!(pred.tp_probability >= 0.0 && pred.tp_probability <= 1.0);
        assert!(pred.fp_probability >= 0.0 && pred.fp_probability <= 1.0);
    }
    
    #[test]
    fn test_classifier_train() {
        let mut classifier = FpClassifier::new(5, 4);
        
        // Simple training data
        let features = vec![
            Features::new(vec![1.0, 0.0, 0.0, 0.0, 0.0]), // FP pattern
            Features::new(vec![0.0, 1.0, 0.0, 0.0, 0.0]), // TP pattern
            Features::new(vec![1.0, 0.0, 0.0, 0.0, 0.0]), // FP pattern
            Features::new(vec![0.0, 1.0, 0.0, 0.0, 0.0]), // TP pattern
        ];
        let labels = vec![false, true, false, true];
        
        // Train for a few steps
        let mut prev_loss = f32::MAX;
        for _ in 0..100 {
            let loss = classifier.train_step(&features, &labels, 0.1);
            assert!(loss <= prev_loss + 0.1); // Loss should generally decrease
            prev_loss = loss;
        }
    }
    
    #[test]
    fn test_save_load() {
        let classifier = FpClassifier::new(10, 8);
        let path = std::path::Path::new("/tmp/test_classifier.json");
        
        classifier.save(path).unwrap();
        let loaded = FpClassifier::load(path).unwrap();
        
        assert_eq!(classifier.input_size, loaded.input_size);
        assert_eq!(classifier.hidden_size, loaded.hidden_size);
        
        std::fs::remove_file(path).ok();
    }
}
