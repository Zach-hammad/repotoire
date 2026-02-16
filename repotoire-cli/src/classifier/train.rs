//! Training for the FP classifier
//!
//! Trains the neural network on labeled examples.

use super::features::{FeatureExtractor, Features};
use super::feedback::{FeedbackCollector, LabeledFinding};
use super::model::FpClassifier;
use crate::models::{Finding, Severity};
use std::path::PathBuf;

/// Training configuration
#[derive(Debug, Clone)]
pub struct TrainConfig {
    /// Learning rate
    pub learning_rate: f32,
    /// Number of epochs
    pub epochs: usize,
    /// Batch size
    pub batch_size: usize,
    /// Hidden layer size
    pub hidden_size: usize,
    /// Validation split (0.0 - 1.0)
    pub val_split: f32,
}

impl Default for TrainConfig {
    fn default() -> Self {
        Self {
            learning_rate: 0.01,
            epochs: 100,
            batch_size: 32,
            hidden_size: 32,
            val_split: 0.2,
        }
    }
}

/// Training result
#[derive(Debug)]
pub struct TrainResult {
    /// Final training loss
    pub train_loss: f32,
    /// Final validation loss (if val_split > 0)
    pub val_loss: Option<f32>,
    /// Training accuracy
    pub train_accuracy: f32,
    /// Validation accuracy
    pub val_accuracy: Option<f32>,
    /// Number of epochs trained
    pub epochs: usize,
    /// Path to saved model
    pub model_path: PathBuf,
}

/// Train the classifier on labeled data
pub fn train(config: &TrainConfig) -> Result<TrainResult, String> {
    let collector = FeedbackCollector::default();
    let examples = collector.load_all()
        .map_err(|e| format!("Failed to load training data: {}", e))?;
    
    if examples.is_empty() {
        return Err("No training data found. Use `repotoire feedback` to label findings.".into());
    }
    
    if examples.len() < 10 {
        return Err(format!(
            "Need at least 10 labeled examples, found {}. Label more findings first.",
            examples.len()
        ));
    }
    
    tracing::info!("Loaded {} labeled examples", examples.len());
    
    // Convert to features
    let extractor = FeatureExtractor::new();
    let mut data: Vec<(Features, bool)> = examples
        .iter()
        .map(|ex| {
            let finding = labeled_to_finding(ex);
            let features = extractor.extract(&finding);
            (features, ex.is_true_positive)
        })
        .collect();
    
    // Shuffle
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    std::time::SystemTime::now().hash(&mut hasher);
    let seed = hasher.finish() as usize;
    
    for i in (1..data.len()).rev() {
        let j = (seed + i * 17) % (i + 1);
        data.swap(i, j);
    }
    
    // Split train/val
    let val_size = (data.len() as f32 * config.val_split) as usize;
    let (val_data, train_data) = data.split_at(val_size);
    
    tracing::info!("Training: {} examples, Validation: {} examples", 
        train_data.len(), val_data.len());
    
    // Create model
    let input_size = extractor.feature_count();
    let mut model = FpClassifier::new(input_size, config.hidden_size);
    
    // Training loop
    let mut best_val_acc = 0.0;
    let mut train_loss = 0.0;
    
    for epoch in 0..config.epochs {
        // Training
        let mut epoch_loss = 0.0;
        let mut correct = 0;
        
        for chunk in train_data.chunks(config.batch_size) {
            let features: Vec<_> = chunk.iter().map(|(f, _)| f.clone()).collect();
            let labels: Vec<_> = chunk.iter().map(|(_, l)| *l).collect();
            
            let loss = model.train_step(&features, &labels, config.learning_rate);
            epoch_loss += loss * chunk.len() as f32;
            
            // Count correct
            for (f, label) in chunk {
                let pred = model.predict(f);
                if pred.is_true_positive == *label {
                    correct += 1;
                }
            }
        }
        
        train_loss = epoch_loss / train_data.len() as f32;
        let train_acc = correct as f32 / train_data.len() as f32;
        
        // Validation
        let (_val_loss, val_acc) = if !val_data.is_empty() {
            let mut loss = 0.0;
            let mut correct = 0;
            
            for (f, label) in val_data {
                let pred = model.predict(f);
                if pred.is_true_positive == *label {
                    correct += 1;
                }
                // Cross-entropy loss
                let prob = if *label { pred.tp_probability } else { pred.fp_probability };
                loss -= prob.max(1e-7).ln();
            }
            
            let val_loss = loss / val_data.len() as f32;
            let val_acc = correct as f32 / val_data.len() as f32;
            
            if val_acc > best_val_acc {
                best_val_acc = val_acc;
            }
            
            (Some(val_loss), Some(val_acc))
        } else {
            (None, None)
        };
        
        if epoch % 10 == 0 || epoch == config.epochs - 1 {
            tracing::info!(
                "Epoch {}/{}: train_loss={:.4}, train_acc={:.2}%, val_acc={:.2}%",
                epoch + 1,
                config.epochs,
                train_loss,
                train_acc * 100.0,
                val_acc.unwrap_or(0.0) * 100.0
            );
        }
    }
    
    // Save model
    let model_path = dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("repotoire")
        .join("classifier_model.json");
    
    if let Some(parent) = model_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create model directory: {}", e))?;
    }
    
    model.save(&model_path)
        .map_err(|e| format!("Failed to save model: {}", e))?;
    
    tracing::info!("Model saved to {}", model_path.display());
    
    // Final evaluation
    let mut correct = 0;
    for (f, label) in train_data {
        let pred = model.predict(f);
        if pred.is_true_positive == *label {
            correct += 1;
        }
    }
    let train_accuracy = correct as f32 / train_data.len() as f32;
    
    let val_accuracy = if !val_data.is_empty() {
        let mut correct = 0;
        for (f, label) in val_data {
            let pred = model.predict(f);
            if pred.is_true_positive == *label {
                correct += 1;
            }
        }
        Some(correct as f32 / val_data.len() as f32)
    } else {
        None
    };
    
    Ok(TrainResult {
        train_loss,
        val_loss: None, // We don't track final val loss
        train_accuracy,
        val_accuracy,
        epochs: config.epochs,
        model_path,
    })
}

/// Convert labeled finding back to Finding for feature extraction
fn labeled_to_finding(labeled: &LabeledFinding) -> Finding {
    Finding {
        id: labeled.finding_id.clone(),
        detector: labeled.detector.clone(),
        severity: match labeled.severity.to_lowercase().as_str() {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "medium" => Severity::Medium,
            _ => Severity::Low,
        },
        title: labeled.title.clone(),
        description: labeled.description.clone(),
        affected_files: vec![PathBuf::from(&labeled.file_path)],
        line_start: labeled.line_start,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_train_config_default() {
        let config = TrainConfig::default();
        assert!(config.learning_rate > 0.0);
        assert!(config.epochs > 0);
    }
}
