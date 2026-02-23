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

use crate::detectors::base::{
    is_test_file, DetectionSummary, Detector, DetectorResult, ProgressCallback,
};
use crate::detectors::context_hmm::{ContextClassifier, FunctionContext, FunctionFeatures};
use crate::detectors::function_context::{FunctionContextBuilder, FunctionContextMap};
use crate::graph::GraphStore;
use crate::models::Finding;
use anyhow::Result;
use rayon::prelude::*;
use std::collections::HashMap;
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
    /// HMM-based context classification for adaptive detection
    hmm_contexts: Option<Arc<HashMap<String, FunctionContext>>>,
    /// Skip findings from test files (default: true)
    /// This filters out findings where all affected_files are test files
    skip_test_files: bool,
    /// Path to cache HMM model for faster subsequent runs
    hmm_cache_path: Option<std::path::PathBuf>,
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
            hmm_contexts: None,
            skip_test_files: true, // Skip test files by default
            hmm_cache_path: None,
        }
    }

    /// Set path for HMM model caching
    pub fn with_hmm_cache(mut self, path: std::path::PathBuf) -> Self {
        self.hmm_cache_path = Some(path);
        self
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

    /// Set whether to skip test files (default: true)
    /// When true, findings from test files are filtered out
    pub fn with_skip_test_files(mut self, skip: bool) -> Self {
        self.skip_test_files = skip;
        self
    }

    /// Get function contexts (builds them from graph if not already set)
    pub fn get_or_build_contexts(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Arc<FunctionContextMap> {
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

    /// Build HMM-based function contexts from the call graph
    /// This provides adaptive context classification per codebase
    pub fn build_hmm_contexts(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Arc<HashMap<String, FunctionContext>> {
        if let Some(ref ctx) = self.hmm_contexts {
            return Arc::clone(ctx);
        }

        // Try to load cached HMM+CRF model (borrow, don't clone)
        let cache_path = self.hmm_cache_path.as_ref();
        let mut classifier = if let Some(path) = cache_path {
            let model_path = path.join("hmm_model.json");
            if model_path.exists() {
                info!("Loading cached HMM+CRF model from {:?}", model_path);
                ContextClassifier::load(&model_path).unwrap_or_else(|| {
                    // Fallback to legacy format
                    ContextClassifier::for_codebase(Some(&model_path))
                })
            } else {
                ContextClassifier::new()
            }
        } else {
            ContextClassifier::new()
        };

        info!("Building HMM function contexts from graph...");
        let mut functions = graph.get_functions();

        if functions.is_empty() {
            let empty = Arc::new(HashMap::new());
            self.hmm_contexts = Some(Arc::clone(&empty));
            return empty;
        }

        // Limit function count to prevent OOM on huge codebases
        const MAX_FUNCTIONS_FOR_HMM: usize = 20_000;
        if functions.len() > MAX_FUNCTIONS_FOR_HMM {
            warn!(
                "Limiting HMM analysis to {} functions (codebase has {})",
                MAX_FUNCTIONS_FOR_HMM,
                functions.len()
            );
            // Keep functions with highest fan-in (most important to classify correctly)
            functions.sort_by(|a, b| {
                let a_callers = graph.get_callers(&a.qualified_name).len();
                let b_callers = graph.get_callers(&b.qualified_name).len();
                b_callers.cmp(&a_callers)
            });
            functions.truncate(MAX_FUNCTIONS_FOR_HMM);
        }

        // Compute graph statistics for normalization
        let mut max_fan_in = 1usize;
        let mut max_fan_out = 1usize;
        let mut total_complexity = 0i64;
        let mut complexity_count = 0usize;
        let mut total_loc = 0u32;
        let mut total_params = 0usize;

        for func in &functions {
            let fan_in = graph.get_callers(&func.qualified_name).len();
            let fan_out = graph.get_callees(&func.qualified_name).len();
            max_fan_in = max_fan_in.max(fan_in);
            max_fan_out = max_fan_out.max(fan_out);

            if let Some(c) = func.complexity() {
                total_complexity += c;
                complexity_count += 1;
            }
            total_loc += func.line_end.saturating_sub(func.line_start) + 1;
            // Assume 3 params average if not available
            total_params += 3;
        }

        let avg_complexity = if complexity_count > 0 {
            total_complexity as f64 / complexity_count as f64
        } else {
            10.0
        };
        let avg_loc = total_loc as f64 / functions.len().max(1) as f64;
        let avg_params = total_params as f64 / functions.len().max(1) as f64;

        // Extract features for training
        let mut function_data = Vec::new();

        for func in &functions {
            let callers = graph.get_callers(&func.qualified_name);
            let fan_in = callers.len();
            let fan_out = graph.get_callees(&func.qualified_name).len();
            let caller_files: std::collections::HashSet<_> =
                callers.iter().map(|c| &c.file_path).collect();

            let loc = func.line_end.saturating_sub(func.line_start) + 1;
            let address_taken = func
                .properties
                .get("address_taken")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);

            let features = FunctionFeatures::extract(
                &func.name,
                &func.file_path,
                fan_in,
                fan_out,
                max_fan_in,
                max_fan_out,
                caller_files.len(),
                func.complexity(),
                avg_complexity,
                loc,
                avg_loc,
                3, // Default param count
                avg_params,
                address_taken,
            );

            function_data.push((features, fan_in, fan_out, address_taken));
        }

        // Bootstrap training from call graph patterns
        classifier.train(&function_data);

        // Save trained model to cache
        if let Some(path) = cache_path {
            if let Err(e) = std::fs::create_dir_all(path) {
                warn!("Failed to create HMM cache directory: {}", e);
            } else {
                let model_path = path.join("hmm_model.json");
                if let Err(e) = classifier.save(&model_path) {
                    warn!("Failed to save HMM model: {}", e);
                } else {
                    info!("Saved HMM model to {:?}", model_path);
                }
            }
        }

        // Classify all functions
        let mut contexts = HashMap::new();
        for (func, (features, _, _, _)) in functions.iter().zip(function_data.iter()) {
            let context = classifier.classify(&func.qualified_name, features);
            contexts.insert(func.qualified_name.clone(), context);
        }

        info!("Classified {} functions using HMM", contexts.len());

        // Log distribution
        let mut counts = [0usize; 5];
        for ctx in contexts.values() {
            counts[ctx.index()] += 1;
        }
        info!(
            "Context distribution: Utility={}, Handler={}, Core={}, Internal={}, Test={}",
            counts[0], counts[1], counts[2], counts[3], counts[4]
        );

        let arc = Arc::new(contexts);
        self.hmm_contexts = Some(Arc::clone(&arc));
        arc
    }

    /// Get HMM contexts (returns None if not built)
    pub fn hmm_contexts(&self) -> Option<&Arc<HashMap<String, FunctionContext>>> {
        self.hmm_contexts.as_ref()
    }

    /// Get context for a specific function
    pub fn get_function_context(&self, qualified_name: &str) -> Option<FunctionContext> {
        self.hmm_contexts
            .as_ref()
            .and_then(|ctx| ctx.get(qualified_name).copied())
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
    pub fn run(&mut self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let start = Instant::now();
        info!(
            "Starting detection with {} detectors on {} workers",
            self.detectors.len(),
            self.workers
        );

        // Build function contexts (if not already set)
        let contexts = self.get_or_build_contexts(graph);

        // Build HMM-based contexts for adaptive detection
        let hmm_contexts = self.build_hmm_contexts(graph);

        // Partition detectors into independent and dependent
        let (independent, dependent): (Vec<_>, Vec<_>) = self
            .detectors
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
                    let result = self.run_single_detector(detector, graph, files, &contexts_for_parallel);

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
        // Future: Build dependency graph and run in topological order
        for detector in dependent {
            let result = self.run_single_detector(&detector, graph, files, &contexts);

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

        // Filter out test file findings if enabled
        if self.skip_test_files {
            let before_count = all_findings.len();
            all_findings.retain(|finding| !self.is_test_file_finding(finding));
            let filtered = before_count - all_findings.len();
            if filtered > 0 {
                debug!("Filtered out {} findings from test files", filtered);
            }
        }

        // Apply HMM-based context filtering
        let before_hmm = all_findings.len();
        all_findings = self.apply_hmm_context_filter(all_findings, &hmm_contexts, graph);
        let hmm_filtered = before_hmm - all_findings.len();
        if hmm_filtered > 0 {
            info!(
                "HMM context filter removed {} false positives",
                hmm_filtered
            );
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
    pub fn run_detailed(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Result<(Vec<DetectorResult>, DetectionSummary)> {
        let start = Instant::now();

        // Build function contexts
        let contexts = self.get_or_build_contexts(graph);

        // Partition detectors
        let (independent, dependent): (Vec<_>, Vec<_>) = self
            .detectors
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
                .map(|detector| self.run_single_detector(detector, graph, files, &contexts_for_parallel))
                .collect()
        });

        // Run dependent sequentially
        for detector in dependent {
            all_results.push(self.run_single_detector(&detector, graph, files, &contexts));
        }

        // Filter out test file findings if enabled
        if self.skip_test_files {
            for result in &mut all_results {
                let before_count = result.findings.len();
                result
                    .findings
                    .retain(|finding| !self.is_test_file_finding(finding));
                let filtered = before_count - result.findings.len();
                if filtered > 0 {
                    debug!(
                        "Filtered {} test file findings from {}",
                        filtered, result.detector_name
                    );
                }
            }
        }

        // Build summary
        let mut summary = DetectionSummary::default();
        for result in &all_results {
            summary.add_result(result);
        }
        summary.total_duration_ms = start.elapsed().as_millis() as u64;

        Ok((all_results, summary))
    }

    /// Apply HMM-based context filtering to reduce false positives
    ///
    /// - Skip coupling findings for UTILITY/HANDLER functions
    /// - Skip dead code findings for HANDLER functions
    /// - Downgrade severity for functions with lenient contexts
    fn apply_hmm_context_filter(
        &self,
        mut findings: Vec<Finding>,
        hmm_contexts: &HashMap<String, FunctionContext>,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Vec<Finding> {
        // Detectors that should skip UTILITY functions
        const COUPLING_DETECTORS: &[&str] = &[
            "DegreeCentralityDetector",
            "ShotgunSurgeryDetector",
            "FeatureEnvyDetector",
            "InappropriateIntimacyDetector",
        ];

        // Detectors that should skip HANDLER functions
        const DEAD_CODE_DETECTORS: &[&str] = &["UnreachableCodeDetector", "DeadCodeDetector"];

        findings.retain(|finding| {
            // Try to get the function associated with this finding
            let func_name = self.extract_function_from_finding(finding, graph);

            if let Some(name) = func_name {
                if let Some(context) = hmm_contexts.get(&name) {
                    // Skip coupling findings for utility/handler/test functions
                    if COUPLING_DETECTORS
                        .iter()
                        .any(|d| finding.detector.contains(d))
                        && context.skip_coupling()
                    {
                        debug!(
                            "HMM filter: skipping coupling finding for {} (context: {:?})",
                            name, context
                        );
                        return false;
                    }

                    // Skip dead code findings for handler functions
                    if DEAD_CODE_DETECTORS
                        .iter()
                        .any(|d| finding.detector.contains(d))
                        && context.skip_dead_code()
                    {
                        debug!(
                            "HMM filter: skipping dead code finding for {} (context: {:?})",
                            name, context
                        );
                        return false;
                    }
                }
            }

            true
        });

        findings
    }

    /// Try to extract the function qualified name from a finding
    fn extract_function_from_finding(
        &self,
        finding: &Finding,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Option<String> {
        // Try to find function by file path and line number
        if let (Some(file), Some(line)) = (finding.affected_files.first(), finding.line_start) {
            let file_str = file.to_string_lossy();

            // Look up function in graph by location
            for func in graph.get_functions() {
                if func.file_path == file_str && func.line_start <= line && func.line_end >= line {
                    return Some(func.qualified_name.clone());
                }
            }
        }

        // Fallback: try to extract from title (e.g., "Dead function: func_name")
        if finding.title.contains(':') {
            let parts: Vec<&str> = finding.title.splitn(2, ':').collect();
            if parts.len() == 2 {
                let name = parts[1].trim();
                // Look up in graph
                for func in graph.get_functions() {
                    if func.name == name || func.qualified_name.ends_with(name) {
                        return Some(func.qualified_name.clone());
                    }
                }
            }
        }

        None
    }

    /// Check if a finding is from test files only
    /// Returns true if ALL affected files are test files
    fn is_test_file_finding(&self, finding: &Finding) -> bool {
        // If no affected files, can't determine - don't filter
        if finding.affected_files.is_empty() {
            return false;
        }
        // Filter only if ALL affected files are test files
        finding.affected_files.iter().all(|path| is_test_file(path))
    }

    /// Run a single detector with error handling and timing
    fn run_single_detector(
        &self,
        detector: &Arc<dyn Detector>,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &Arc<FunctionContextMap>,
    ) -> DetectorResult {
        let name = detector.name().to_string();
        let start = Instant::now();

        debug!("Running detector: {}", name);

        // Wrap in catch_unwind to handle panics
        let contexts_clone = Arc::clone(contexts);
        let detect_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            if detector.uses_context() {
                detector.detect_with_context(graph, files, &contexts_clone)
            } else {
                detector.detect(graph, files)
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
    skip_test_files: bool,
}

impl DetectorEngineBuilder {
    /// Create a new builder
    pub fn new() -> Self {
        Self {
            workers: 0,
            max_findings: MAX_FINDINGS_LIMIT,
            detectors: Vec::new(),
            progress_callback: None,
            skip_test_files: true,
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

    /// Set whether to skip test files (default: true)
    pub fn skip_test_files(mut self, skip: bool) -> Self {
        self.skip_test_files = skip;
        self
    }

    /// Build the engine
    pub fn build(self) -> DetectorEngine {
        let mut engine = DetectorEngine::new(self.workers)
            .with_max_findings(self.max_findings)
            .with_skip_test_files(self.skip_test_files);

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

        fn detect(&self, _graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
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
