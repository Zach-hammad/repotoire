//! Detector execution engine with parallel support
//!
//! The DetectorEngine orchestrates the execution of all registered detectors:
//! - Runs independent detectors in parallel using rayon
//! - Runs dependent detectors sequentially in dependency order
//! - Collects and aggregates findings
//! - Reports progress through callbacks
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────┐
//! │                    DetectorEngine                       │
//! ├─────────────────────────────────────────────────────────┤
//! │  1. Register detectors                                  │
//! │  2. Partition into independent/dependent                │
//! │  3. Run independent in parallel (rayon)                 │
//! │  4. Run dependent sequentially                          │
//! │  5. Collect and return findings                         │
//! └─────────────────────────────────────────────────────────┘
//! ```

use crate::detectors::base::{DetectionSummary, Detector, DetectorResult, ProgressCallback};
use crate::detectors::function_context::{FunctionContextMap, FunctionContextBuilder};
use crate::graph::GraphStore;
use crate::models::Finding;
use anyhow::Result;
use rayon::prelude::*;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;
use tracing::{debug, error, info, warn};

/// Maximum findings to keep to prevent memory exhaustion
const MAX_FINDINGS_LIMIT: usize = 10_000;

/// Orchestrates code smell detection across all registered detectors
pub struct DetectorEngine {
    /// Registered detectors
    detectors: Vec<Arc<dyn Detector>>,
    /// Number of worker threads for parallel execution
    workers: usize,
    /// Maximum findings to return (prevents memory issues on large codebases)
    max_findings: usize,
    /// Progress callback for reporting execution status
    progress_callback: Option<ProgressCallback>,
    /// Pre-computed function contexts (built from graph on first run)
    function_contexts: Option<Arc<FunctionContextMap>>,
}

impl DetectorEngine {
    /// Create a new detector engine
    ///
    /// # Arguments
    /// * `workers` - Number of worker threads (0 = auto-detect)
    pub fn new(workers: usize) -> Self {
        let actual_workers = if workers == 0 {
            std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(4)
                .min(16) // Cap at 16 threads
        } else {
            workers
        };

        Self {
            detectors: Vec::new(),
            workers: actual_workers,
            max_findings: MAX_FINDINGS_LIMIT,
            progress_callback: None,
            function_contexts: None,
        }
    }

    /// Create engine with default settings
    pub fn default() -> Self {
        Self::new(0)
    }

    /// Set the maximum number of findings to return
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set a progress callback
    pub fn with_progress_callback(mut self, callback: ProgressCallback) -> Self {
        self.progress_callback = Some(callback);
        self
    }

    /// Set pre-computed function contexts
    pub fn with_function_contexts(mut self, contexts: Arc<FunctionContextMap>) -> Self {
        self.function_contexts = Some(contexts);
        self
    }

    /// Get function contexts (builds them from graph if not already set)
    pub fn get_or_build_contexts(&mut self, graph: &GraphStore) -> Arc<FunctionContextMap> {
        if let Some(ref ctx) = self.function_contexts {
            return Arc::clone(ctx);
        }
        
        info!("Building function contexts from graph...");
        let contexts = FunctionContextBuilder::new(graph).build();
        let arc = Arc::new(contexts);
        self.function_contexts = Some(Arc::clone(&arc));
        arc
    }

    /// Get function contexts (returns None if not built)
    pub fn function_contexts(&self) -> Option<&Arc<FunctionContextMap>> {
        self.function_contexts.as_ref()
    }

    /// Register a detector
    ///
    /// Detectors are partitioned into independent and dependent sets
    /// based on their `is_dependent()` method.
    pub fn register(&mut self, detector: Arc<dyn Detector>) {
        debug!("Registering detector: {}", detector.name());
        self.detectors.push(detector);
    }

    /// Register multiple detectors at once
    pub fn register_all(&mut self, detectors: impl IntoIterator<Item = Arc<dyn Detector>>) {
        for detector in detectors {
            self.register(detector);
        }
    }

    /// Get the number of registered detectors
    pub fn detector_count(&self) -> usize {
        self.detectors.len()
    }

    /// Get names of all registered detectors
    pub fn detector_names(&self) -> Vec<&'static str> {
        self.detectors.iter().map(|d| d.name()).collect()
    }

    /// Run all detectors and collect findings
    ///
    /// # Arguments
    /// * `graph` - Graph database client
    ///
    /// # Returns
    /// All findings from all detectors, sorted by severity (highest first)
    pub fn run(&mut self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let start = Instant::now();
        info!(
            "Starting detection with {} detectors on {} workers",
            self.detectors.len(),
            self.workers
        );

        // Build function contexts (if not already set)
        let contexts = self.get_or_build_contexts(graph);

        // Partition detectors into independent and dependent
        let (independent, dependent): (Vec<_>, Vec<_>) = self.detectors
            .iter()
            .cloned()
            .partition(|d| !d.is_dependent());

        info!(
            "Detectors: {} independent, {} dependent",
            independent.len(),
            dependent.len()
        );

        // Progress tracking
        let completed = Arc::new(AtomicUsize::new(0));
        let total = self.detectors.len();

        // Run independent detectors in parallel
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .build()?;

        let contexts_for_parallel = Arc::clone(&contexts);
        let independent_results: Vec<DetectorResult> = pool.install(|| {
            independent
                .par_iter()
                .map(|detector| {
                    let result = self.run_single_detector(detector, graph, &contexts_for_parallel);
                    
                    // Update progress
                    let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(ref callback) = self.progress_callback {
                        callback(detector.name(), done, total);
                    }
                    
                    result
                })
                .collect()
        });

        // Collect findings from independent detectors
        let mut all_findings: Vec<Finding> = Vec::new();
        let mut summary = DetectionSummary::default();

        for result in independent_results {
            summary.add_result(&result);
            if result.success {
                all_findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Run dependent detectors sequentially
        // TODO: Build dependency graph and run in topological order
        for detector in dependent {
            let result = self.run_single_detector(&detector, graph, &contexts);
            
            // Update progress
            let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
            if let Some(ref callback) = self.progress_callback {
                callback(detector.name(), done, total);
            }
            
            summary.add_result(&result);
            if result.success {
                all_findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Sort by severity (highest first)
        all_findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings to prevent memory exhaustion
        if all_findings.len() > self.max_findings {
            warn!(
                "Truncating findings from {} to {} (max limit)",
                all_findings.len(),
                self.max_findings
            );
            all_findings.truncate(self.max_findings);
        }

        let duration = start.elapsed();
        info!(
            "Detection complete: {} findings from {}/{} detectors in {:?}",
            all_findings.len(),
            summary.detectors_succeeded,
            summary.detectors_run,
            duration
        );

        Ok(all_findings)
    }

    /// Run all detectors and return detailed results
    ///
    /// Unlike `run()`, this returns individual results for each detector,
    /// useful for debugging and detailed reporting.
    pub fn run_detailed(&mut self, graph: &GraphStore) -> Result<(Vec<DetectorResult>, DetectionSummary)> {
        let start = Instant::now();
        
        // Build function contexts
        let contexts = self.get_or_build_contexts(graph);
        
        // Partition detectors
        let (independent, dependent): (Vec<_>, Vec<_>) = self.detectors
            .iter()
            .cloned()
            .partition(|d| !d.is_dependent());

        // Run independent in parallel
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .build()?;

        let contexts_for_parallel = Arc::clone(&contexts);
        let mut all_results: Vec<DetectorResult> = pool.install(|| {
            independent
                .par_iter()
                .map(|detector| self.run_single_detector(detector, graph, &contexts_for_parallel))
                .collect()
        });

        // Run dependent sequentially
        for detector in dependent {
            all_results.push(self.run_single_detector(&detector, graph, &contexts));
        }

        // Build summary
        let mut summary = DetectionSummary::default();
        for result in &all_results {
            summary.add_result(result);
        }
        summary.total_duration_ms = start.elapsed().as_millis() as u64;

        Ok((all_results, summary))
    }

    /// Run a single detector with error handling and timing
    fn run_single_detector(
        &self,
        detector: &Arc<dyn Detector>,
        graph: &GraphStore,
        contexts: &Arc<FunctionContextMap>,
    ) -> DetectorResult {
        let name = detector.name().to_string();
        let start = Instant::now();

        debug!("Running detector: {}", name);

        // Wrap in catch_unwind to handle panics
        let contexts_clone = Arc::clone(contexts);
        let detect_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            if detector.uses_context() {
                detector.detect_with_context(graph, &contexts_clone)
            } else {
                detector.detect(graph)
            }
        }));

        match detect_result {
            Ok(Ok(mut findings)) => {
                let duration = start.elapsed().as_millis() as u64;
                
                // Apply per-detector finding limit if configured
                if let Some(config) = detector.config() {
                    if let Some(max) = config.max_findings {
                        if findings.len() > max {
                            findings.truncate(max);
                        }
                    }
                }

                debug!(
                    "Detector {} found {} findings in {}ms",
                    name,
                    findings.len(),
                    duration
                );

                DetectorResult::success(name, findings, duration)
            }
            Ok(Err(e)) => {
                let duration = start.elapsed().as_millis() as u64;
                // Downgrade to debug - many detectors fail due to missing graph properties
                debug!("Detector {} skipped (query error): {}", name, e);
                DetectorResult::failure(name, e.to_string(), duration)
            }
            Err(panic_info) => {
                let duration = start.elapsed().as_millis() as u64;
                let panic_msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                    s.to_string()
                } else if let Some(s) = panic_info.downcast_ref::<String>() {
                    s.clone()
                } else {
                    "Unknown panic".to_string()
                };
                error!("Detector {} panicked: {}", name, panic_msg);
                DetectorResult::failure(name, format!("Panic: {}", panic_msg), duration)
            }
        }
    }
}

impl Default for DetectorEngine {
    fn default() -> Self {
        Self::new(0)
    }
}

/// Builder for DetectorEngine with fluent API
pub struct DetectorEngineBuilder {
    workers: usize,
    max_findings: usize,
    detectors: Vec<Arc<dyn Detector>>,
    progress_callback: Option<ProgressCallback>,
}

impl DetectorEngineBuilder {
    /// Create a new builder
    pub fn new() -> Self {
        Self {
            workers: 0,
            max_findings: MAX_FINDINGS_LIMIT,
            detectors: Vec::new(),
            progress_callback: None,
        }
    }

    /// Set number of worker threads
    pub fn workers(mut self, workers: usize) -> Self {
        self.workers = workers;
        self
    }

    /// Set maximum findings
    pub fn max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Add a detector
    pub fn detector(mut self, detector: Arc<dyn Detector>) -> Self {
        self.detectors.push(detector);
        self
    }

    /// Add multiple detectors
    pub fn detectors(mut self, detectors: impl IntoIterator<Item = Arc<dyn Detector>>) -> Self {
        self.detectors.extend(detectors);
        self
    }

    /// Set progress callback
    pub fn on_progress(mut self, callback: ProgressCallback) -> Self {
        self.progress_callback = Some(callback);
        self
    }

    /// Build the engine
    pub fn build(self) -> DetectorEngine {
        let mut engine = DetectorEngine::new(self.workers)
            .with_max_findings(self.max_findings);

        if let Some(callback) = self.progress_callback {
            engine = engine.with_progress_callback(callback);
        }

        engine.register_all(self.detectors);
        engine
    }
}

impl Default for DetectorEngineBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    // Mock detector for testing
    struct MockDetector {
        name: &'static str,
        findings_count: usize,
        dependent: bool,
    }

    impl Detector for MockDetector {
        fn name(&self) -> &'static str {
            self.name
        }

        fn description(&self) -> &'static str {
            "Mock detector for testing"
        }

        fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
            Ok((0..self.findings_count)
                .map(|i| Finding {
                    id: format!("{}-{}", self.name, i),
                    detector: self.name.to_string(),
                    severity: Severity::Medium,
                    title: format!("Finding {}", i),
                    description: "Test finding".to_string(),
                    affected_files: vec![PathBuf::from("test.py")],
                    line_start: Some(1),
                    line_end: Some(10),
                    suggested_fix: None,
                    estimated_effort: None,
                    category: None,
                    cwe_id: None,
                    why_it_matters: None,
                    ..Default::default()
                })
                .collect())
        }

        fn is_dependent(&self) -> bool {
            self.dependent
        }
    }

    #[test]
    fn test_engine_creation() {
        let engine = DetectorEngine::new(4);
        assert_eq!(engine.workers, 4);
        assert_eq!(engine.detector_count(), 0);
    }

    #[test]
    fn test_engine_default_workers() {
        let engine = DetectorEngine::new(0);
        assert!(engine.workers > 0);
        assert!(engine.workers <= 16);
    }

    #[test]
    fn test_register_detectors() {
        let mut engine = DetectorEngine::new(2);
        
        engine.register(Arc::new(MockDetector {
            name: "Detector1",
            findings_count: 5,
            dependent: false,
        }));
        
        engine.register(Arc::new(MockDetector {
            name: "Detector2",
            findings_count: 3,
            dependent: true,
        }));

        assert_eq!(engine.detector_count(), 2);
        assert_eq!(engine.detector_names(), vec!["Detector1", "Detector2"]);
    }

    #[test]
    fn test_builder() {
        let engine = DetectorEngineBuilder::new()
            .workers(4)
            .max_findings(100)
            .detector(Arc::new(MockDetector {
                name: "Test",
                findings_count: 1,
                dependent: false,
            }))
            .build();

        assert_eq!(engine.workers, 4);
        assert_eq!(engine.max_findings, 100);
        assert_eq!(engine.detector_count(), 1);
    }
}
