//! Streaming detector engine for memory-constrained analysis
//!
//! This module runs detectors and streams findings to disk immediately,
//! preventing OOM on large repositories with many findings.
//!
//! # Memory Model
//!
//! Traditional:
//! ```text
//! Run all detectors → Vec<Finding> (100k+ items, 500MB+) → process
//! ```
//!
//! Streaming:
//! ```text
//! Run detector batch → write to JSONL → drop findings → next batch
//! Final: stream from disk for scoring/display
//! ```

use crate::config::ProjectConfig;
use crate::detectors::{default_detectors_with_config, Detector, DetectorEngine, FunctionContext};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Stats from streaming detection
#[derive(Debug, Clone, Default)]
pub struct StreamingDetectionStats {
    pub detectors_run: usize,
    pub total_findings: usize,
    pub critical: usize,
    pub high: usize,
    pub medium: usize,
    pub low: usize,
    pub bytes_written: usize,
}

impl StreamingDetectionStats {
    fn add_finding(&mut self, finding: &Finding) {
        self.total_findings += 1;
        match finding.severity {
            Severity::Critical => self.critical += 1,
            Severity::High => self.high += 1,
            Severity::Medium => self.medium += 1,
            Severity::Low => self.low += 1,
            Severity::Info => self.low += 1, // Count info as low
        }
    }

    /// Human-readable summary
    pub fn summary(&self) -> String {
        format!(
            "{} findings ({} critical, {} high, {} medium, {} low)",
            self.total_findings, self.critical, self.high, self.medium, self.low
        )
    }
}

/// Streaming detector engine that writes findings to disk
pub struct StreamingDetectorEngine {
    output_path: PathBuf,
    batch_size: usize,
    max_findings_per_detector: usize,
    workers: usize,
}

impl StreamingDetectorEngine {
    /// Create a new streaming engine
    ///
    /// # Arguments
    /// * `output_path` - Path to write findings JSONL
    /// * `batch_size` - Detectors per batch (default: 10)
    pub fn new(output_path: PathBuf) -> Self {
        Self {
            output_path,
            batch_size: 10,
            max_findings_per_detector: 5000,
            workers: num_cpus::get(),
        }
    }

    /// Set batch size
    pub fn with_batch_size(mut self, size: usize) -> Self {
        self.batch_size = size;
        self
    }

    /// Set max findings per detector (prevents single detector from exploding memory)
    pub fn with_max_per_detector(mut self, max: usize) -> Self {
        self.max_findings_per_detector = max;
        self
    }

    /// Run detectors and stream findings to disk
    pub fn run(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        repo_path: &Path,
        project_config: &ProjectConfig,
        skip_detectors: &[String],
        run_external: bool,
        progress: Option<&dyn Fn(&str, usize, usize)>,
    ) -> Result<StreamingDetectionStats> {
        use std::collections::HashSet;

        let mut stats = StreamingDetectionStats::default();

        // Open output file
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&self.output_path)?;
        let mut writer = BufWriter::new(file);

        // Collect detectors
        let skip_set: HashSet<&str> = skip_detectors.iter().map(|s| s.as_str()).collect();
        let detectors: Vec<Arc<dyn Detector>> =
            default_detectors_with_config(repo_path, project_config)
                .into_iter()
                .filter(|d| !skip_set.contains(d.name()))
                .collect();

        // All detectors are now built-in pure Rust — no external tools
        let _ = run_external;

        let total_detectors = detectors.len();

        // Build a minimal engine just for context building
        let mut context_engine = DetectorEngine::new(self.workers);
        let _contexts = context_engine.get_or_build_contexts(graph);

        // Process detectors in batches
        for (batch_idx, batch) in detectors.chunks(self.batch_size).enumerate() {
            let batch_start = batch_idx * self.batch_size;

            // Run batch in parallel using rayon
            let batch_findings: Vec<Vec<Finding>> = batch
                .iter()
                .enumerate()
                .map(|(i, detector)| {
                    let detector_idx = batch_start + i;

                    if let Some(cb) = progress {
                        cb(detector.name(), detector_idx + 1, total_detectors);
                    }

                    // Run detector
                    match detector.detect(graph) {
                        Ok(mut findings) => {
                            // Limit findings per detector
                            if findings.len() > self.max_findings_per_detector {
                                tracing::warn!(
                                    "Detector {} produced {} findings, truncating to {}",
                                    detector.name(),
                                    findings.len(),
                                    self.max_findings_per_detector
                                );
                                findings.truncate(self.max_findings_per_detector);
                            }
                            findings
                        }
                        Err(e) => {
                            tracing::warn!("Detector {} failed: {}", detector.name(), e);
                            Vec::new()
                        }
                    }
                })
                .collect();

            // Write findings to disk immediately
            for findings in batch_findings {
                for finding in findings {
                    stats.add_finding(&finding);

                    // Write as JSON line
                    let json = serde_json::to_string(&finding)?;
                    writeln!(writer, "{}", json)?;
                    stats.bytes_written += json.len() + 1;
                }
            }

            // Flush after each batch
            writer.flush()?;
            stats.detectors_run = batch_start + batch.len();

            // Findings from this batch are dropped here - memory freed
        }

        writer.flush()?;
        Ok(stats)
    }

    /// Read findings from disk (streaming iterator)
    pub fn read_findings(&self) -> Result<impl Iterator<Item = Finding>> {
        let file = File::open(&self.output_path)?;
        let reader = BufReader::new(file);

        Ok(reader
            .lines()
            .filter_map(|line| line.ok().and_then(|l| serde_json::from_str(&l).ok())))
    }

    /// Read findings with limit (for display)
    pub fn read_findings_limited(&self, limit: usize) -> Result<Vec<Finding>> {
        let file = File::open(&self.output_path)?;
        let reader = BufReader::new(file);

        let findings: Vec<Finding> = reader
            .lines()
            .filter_map(|line| line.ok().and_then(|l| serde_json::from_str(&l).ok()))
            .take(limit)
            .collect();

        Ok(findings)
    }

    /// Read high-severity findings only (for scoring)
    pub fn read_high_severity(&self) -> Result<Vec<Finding>> {
        let file = File::open(&self.output_path)?;
        let reader = BufReader::new(file);

        let findings: Vec<Finding> = reader
            .lines()
            .filter_map(|line| line.ok().and_then(|l| serde_json::from_str(&l).ok()))
            .filter(|f: &Finding| matches!(f.severity, Severity::Critical | Severity::High))
            .collect();

        Ok(findings)
    }

    /// Count findings by severity (without loading all)
    pub fn count_by_severity(&self) -> Result<HashMap<Severity, usize>> {
        let file = File::open(&self.output_path)?;
        let reader = BufReader::new(file);

        let mut counts: HashMap<Severity, usize> = HashMap::new();

        for l in reader.lines().map_while(Result::ok) {
            if let Ok(finding) = serde_json::from_str::<Finding>(&l) {
                *counts.entry(finding.severity).or_insert(0) += 1;
            }
        }

        Ok(counts)
    }
}

/// Run detection in streaming mode
///
/// This is the main entry point for memory-efficient detection on large repos.
pub fn run_streaming_detection(
    graph: &dyn crate::graph::GraphQuery,
    repo_path: &Path,
    cache_dir: &Path,
    project_config: &ProjectConfig,
    skip_detectors: &[String],
    run_external: bool,
    progress: Option<&dyn Fn(&str, usize, usize)>,
) -> Result<(StreamingDetectionStats, PathBuf)> {
    let findings_path = cache_dir.join("findings_stream.jsonl");

    let engine = StreamingDetectorEngine::new(findings_path.clone())
        .with_batch_size(10)
        .with_max_per_detector(5000);

    let stats = engine.run(
        graph,
        repo_path,
        project_config,
        skip_detectors,
        run_external,
        progress,
    )?;

    Ok((stats, findings_path))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_streaming_stats() {
        let mut stats = StreamingDetectionStats::default();

        let finding = Finding {
            detector: "test".to_string(),
            severity: Severity::High,
            title: "Test".to_string(),
            description: "Test finding".to_string(),
            affected_files: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: None,
            confidence: Some(1.0),
            ..Default::default()
        };

        stats.add_finding(&finding);

        assert_eq!(stats.total_findings, 1);
        assert_eq!(stats.high, 1);
    }
}
