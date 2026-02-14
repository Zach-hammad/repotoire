//! Feedback collection for training data
//!
//! Collects user feedback on findings to build training data.
//! Stores labeled examples in JSONL format.

use crate::models::Finding;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

/// A labeled training example
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LabeledFinding {
    /// Finding ID
    pub finding_id: String,
    /// Detector name
    pub detector: String,
    /// Severity level
    pub severity: String,
    /// Title
    pub title: String,
    /// Description (truncated)
    pub description: String,
    /// Affected file path
    pub file_path: String,
    /// Line number
    pub line_start: Option<u32>,
    /// Whether user marked as true positive
    pub is_true_positive: bool,
    /// Optional reason from user
    pub reason: Option<String>,
    /// Timestamp
    pub timestamp: String,
}

impl LabeledFinding {
    pub fn from_finding(finding: &Finding, is_tp: bool, reason: Option<String>) -> Self {
        Self {
            finding_id: finding.id.clone(),
            detector: finding.detector.clone(),
            severity: format!("{:?}", finding.severity),
            title: finding.title.clone(),
            description: finding.description.chars().take(500).collect(),
            file_path: finding
                .affected_files
                .first()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default(),
            line_start: finding.line_start,
            is_true_positive: is_tp,
            reason,
            timestamp: chrono::Utc::now().to_rfc3339(),
        }
    }
}

/// Feedback collector - stores labeled examples
pub struct FeedbackCollector {
    data_path: PathBuf,
}

impl FeedbackCollector {
    /// Create collector with default path
    pub fn new() -> Self {
        let data_path = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("repotoire")
            .join("training_data.jsonl");
        
        Self { data_path }
    }
    
    /// Create with custom path
    pub fn with_path(path: impl Into<PathBuf>) -> Self {
        Self {
            data_path: path.into(),
        }
    }
    
    /// Record a labeled finding
    pub fn record(&self, finding: &Finding, is_tp: bool, reason: Option<String>) -> std::io::Result<()> {
        // Ensure directory exists
        if let Some(parent) = self.data_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        
        let labeled = LabeledFinding::from_finding(finding, is_tp, reason);
        let json = serde_json::to_string(&labeled)?;
        
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.data_path)?;
        
        writeln!(file, "{}", json)?;
        Ok(())
    }
    
    /// Record multiple findings with same label
    pub fn record_batch(&self, findings: &[Finding], is_tp: bool) -> std::io::Result<usize> {
        let mut count = 0;
        for finding in findings {
            self.record(finding, is_tp, None)?;
            count += 1;
        }
        Ok(count)
    }
    
    /// Load all labeled examples
    pub fn load_all(&self) -> std::io::Result<Vec<LabeledFinding>> {
        if !self.data_path.exists() {
            return Ok(Vec::new());
        }
        
        let file = File::open(&self.data_path)?;
        let reader = BufReader::new(file);
        
        let mut examples = Vec::new();
        for line in reader.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(labeled) = serde_json::from_str::<LabeledFinding>(&line) {
                examples.push(labeled);
            }
        }
        
        Ok(examples)
    }
    
    /// Get training statistics
    pub fn stats(&self) -> std::io::Result<TrainingStats> {
        let examples = self.load_all()?;
        
        let tp_count = examples.iter().filter(|e| e.is_true_positive).count();
        let fp_count = examples.iter().filter(|e| !e.is_true_positive).count();
        
        // Count by detector
        let mut by_detector: std::collections::HashMap<String, (usize, usize)> = std::collections::HashMap::new();
        for ex in &examples {
            let entry = by_detector.entry(ex.detector.clone()).or_insert((0, 0));
            if ex.is_true_positive {
                entry.0 += 1;
            } else {
                entry.1 += 1;
            }
        }
        
        Ok(TrainingStats {
            total: examples.len(),
            true_positives: tp_count,
            false_positives: fp_count,
            by_detector,
        })
    }
    
    /// Path to the data file
    pub fn data_path(&self) -> &Path {
        &self.data_path
    }
}

impl Default for FeedbackCollector {
    fn default() -> Self {
        Self::new()
    }
}

/// Training data statistics
#[derive(Debug)]
pub struct TrainingStats {
    pub total: usize,
    pub true_positives: usize,
    pub false_positives: usize,
    pub by_detector: std::collections::HashMap<String, (usize, usize)>,
}

impl std::fmt::Display for TrainingStats {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "Training Data Statistics:")?;
        writeln!(f, "  Total examples: {}", self.total)?;
        writeln!(f, "  True positives: {} ({:.1}%)", 
            self.true_positives, 
            if self.total > 0 { self.true_positives as f64 / self.total as f64 * 100.0 } else { 0.0 }
        )?;
        writeln!(f, "  False positives: {} ({:.1}%)", 
            self.false_positives,
            if self.total > 0 { self.false_positives as f64 / self.total as f64 * 100.0 } else { 0.0 }
        )?;
        writeln!(f, "\n  By detector:")?;
        
        let mut detectors: Vec<_> = self.by_detector.iter().collect();
        detectors.sort_by(|a, b| (b.1.0 + b.1.1).cmp(&(a.1.0 + a.1.1)));
        
        for (detector, (tp, fp)) in detectors.iter().take(10) {
            writeln!(f, "    {}: {} TP, {} FP", detector, tp, fp)?;
        }
        
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    
    #[test]
    fn test_record_and_load() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test_feedback.jsonl");
        let collector = FeedbackCollector::with_path(&path);
        
        let finding = Finding {
            id: "test-123".into(),
            detector: "TestDetector".into(),
            severity: crate::models::Severity::High,
            title: "Test finding".into(),
            description: "A test finding for testing".into(),
            ..Default::default()
        };
        
        collector.record(&finding, true, Some("Real issue".into())).unwrap();
        collector.record(&finding, false, Some("Not a problem".into())).unwrap();
        
        let loaded = collector.load_all().unwrap();
        assert_eq!(loaded.len(), 2);
        assert!(loaded[0].is_true_positive);
        assert!(!loaded[1].is_true_positive);
    }
}
