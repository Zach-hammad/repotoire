//! Code smell detectors
#![allow(unused_imports)]
//!
//! This module provides the detector framework and implementations for
//! finding code smells in the code graph.
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │                     DetectorEngine                          │
//! │  - Registers detectors                                      │
//! │  - Runs independent detectors in parallel (rayon)          │
//! │  - Runs dependent detectors sequentially                    │
//! │  - Collects and reports findings                           │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!                              ▼
//! ┌─────────────────────────────────────────────────────────────┐
//! │                      Detector Trait                         │
//! │  - name(): Unique identifier                                │
//! │  - description(): Human-readable description                │
//! │  - detect(graph): Run detection, return findings            │
//! │  - is_dependent(): Whether depends on other detectors       │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!              ┌───────────────┼───────────────┐
//!              ▼               ▼               ▼
//! ┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
//! │ Graph-based      │ │ External     │ │ Hybrid           │
//! │ (CircularDep,    │ │ Tool-based   │ │ (graph + tool)   │
//! │  GodClass, etc.) │ │ (Bandit,     │ │                  │
//! │                  │ │  Ruff, etc.) │ │                  │
//! └──────────────────┘ └──────────────┘ └──────────────────┘
//! ```
//!
//! # Detector Categories
//!
//! ## Graph-based detectors (fast, query the code graph)
//! - `CircularDependencyDetector` - Circular imports/dependencies
//! - `GodClassDetector` - Classes with too many responsibilities
//! - `LongParameterListDetector` - Functions with too many parameters
//!
//! ## External tool detectors (run external tools via subprocess)
//! - `BanditDetector` - Python security vulnerabilities
//! - `RuffLintDetector` - Python code quality (100x faster than Pylint)
//! - `RuffImportDetector` - Unused Python imports
//! - `MypyDetector` - Python type checking
//! - `PylintDetector` - Python code quality (comprehensive)
//! - `ESLintDetector` - JavaScript/TypeScript code quality
//! - `TscDetector` - TypeScript type checking
//! - `NpmAuditDetector` - npm dependency vulnerabilities
//! - `SemgrepDetector` - Security pattern matching
//! - `RadonDetector` - Python complexity metrics
//! - `JscpdDetector` - Duplicate code detection
//! - `VultureDetector` - Dead Python code detection
//! - `GHActionsInjectionDetector` - GitHub Actions command injection
//!
//! # Usage
//!
//! ```ignore
//! use repotoire_cli::detectors::{
//!     DetectorEngine, DetectorEngineBuilder,
//!     CircularDependencyDetector, GodClassDetector, BanditDetector,
//! };
//! use std::sync::Arc;
//!
//! // Build engine with detectors
//! let engine = DetectorEngineBuilder::new()
//!     .workers(4)
//!     .detector(Arc::new(CircularDependencyDetector::new()))
//!     .detector(Arc::new(GodClassDetector::new()))
//!     .detector(Arc::new(BanditDetector::new("/path/to/repo")))
//!     .build();
//!
//! // Run detection
//! let findings = engine.run(&graph_client)?;
//! ```

mod base;
mod engine;

// Graph-based detector implementations
mod circular_dependency;
mod god_class;
mod long_parameter;

// Code smell detectors
mod data_clumps;
mod dead_code;
mod feature_envy;
mod inappropriate_intimacy;
mod lazy_class;
mod message_chain;
mod middle_man;
mod refused_bequest;

// AI-specific detectors
mod ai_boilerplate;
mod ai_churn;
mod ai_complexity_spike;
mod ai_duplicate_block;
mod ai_missing_tests;
mod ai_naming_pattern;

// Graph/architecture detectors
mod architectural_bottleneck;
mod core_utility;
mod degree_centrality;
mod influential_code;
mod module_cohesion;
mod shotgun_surgery;

// Security detectors
mod eval_detector;
mod pickle_detector;
mod sql_injection;
mod taint_detector;
mod unsafe_template;

// Misc detectors
mod generator_misuse;
mod infinite_loop;
mod unused_imports;

// Cross-detector analysis (ported from Python)
mod health_delta;
mod incremental_cache;
mod query_cache;
mod risk_analyzer;
mod root_cause_analyzer;
mod voting_engine;

// External tool utilities
pub mod external_tool;

// External tool-based detector implementations
mod bandit;
mod eslint;
mod gh_actions;
// mod jscpd;
mod mypy;
mod npm_audit;
// mod pylint;
mod radon;
mod ruff;
mod semgrep;
mod tsc;
mod vulture;
mod secrets;
mod empty_catch;
mod todo_scanner;
mod deep_nesting;
mod magic_numbers;
mod large_files;
mod path_traversal;
mod command_injection;
mod ssrf;
mod missing_docstrings;
mod regex_dos;
mod sync_in_async;
mod n_plus_one;

// Re-export base types
pub use base::{
    DetectionSummary,
    Detector,
    DetectorConfig,
    DetectorResult,
    ProgressCallback,
};

// Re-export engine
pub use engine::{
    DetectorEngine,
    DetectorEngineBuilder,
};

// Re-export graph-based detector implementations
pub use circular_dependency::CircularDependencyDetector;
pub use god_class::{GodClassDetector, GodClassThresholds};
pub use long_parameter::{LongParameterListDetector, LongParameterThresholds};

// Re-export code smell detectors
pub use data_clumps::DataClumpsDetector;
pub use dead_code::DeadCodeDetector;
pub use feature_envy::FeatureEnvyDetector;
pub use inappropriate_intimacy::InappropriateIntimacyDetector;
pub use lazy_class::LazyClassDetector;
pub use message_chain::MessageChainDetector;
pub use middle_man::MiddleManDetector;
pub use refused_bequest::RefusedBequestDetector;

// Re-export AI detectors
pub use ai_boilerplate::AIBoilerplateDetector;
pub use ai_churn::AIChurnDetector;
pub use ai_complexity_spike::AIComplexitySpikeDetector;
pub use ai_duplicate_block::AIDuplicateBlockDetector;
pub use ai_missing_tests::AIMissingTestsDetector;
pub use ai_naming_pattern::AINamingPatternDetector;

// Re-export graph/architecture detectors
pub use architectural_bottleneck::ArchitecturalBottleneckDetector;
pub use core_utility::CoreUtilityDetector;
pub use degree_centrality::DegreeCentralityDetector;
pub use influential_code::InfluentialCodeDetector;
pub use module_cohesion::ModuleCohesionDetector;
pub use shotgun_surgery::ShotgunSurgeryDetector;

// Re-export security detectors
pub use eval_detector::EvalDetector;
pub use pickle_detector::PickleDeserializationDetector;
pub use sql_injection::SQLInjectionDetector;
pub use taint_detector::TaintDetector;
pub use unsafe_template::UnsafeTemplateDetector;

// Re-export misc detectors
pub use generator_misuse::GeneratorMisuseDetector;
pub use infinite_loop::InfiniteLoopDetector;
pub use unused_imports::UnusedImportsDetector;

// Re-export cross-detector analysis utilities
pub use health_delta::{
    estimate_batch_fix_impact, estimate_fix_impact, BatchHealthScoreDelta, HealthScoreDelta,
    HealthScoreDeltaCalculator, ImpactLevel, MetricsBreakdown,
};
pub use incremental_cache::{CacheStats, IncrementalCache};
pub use query_cache::{ClassData, FileData, FunctionData, QueryCache};
pub use risk_analyzer::{
    analyze_compound_risks, RiskAnalyzer, RiskAssessment, RiskFactor,
};
pub use root_cause_analyzer::{RootCauseAnalysis, RootCauseAnalyzer, RootCauseSummary};
pub use voting_engine::{
    ConfidenceMethod, ConsensusResult, DetectorWeight, SeverityResolution, VotingEngine,
    VotingStats, VotingStrategy,
};

// Re-export external tool-based detector implementations
pub use bandit::BanditDetector;
pub use eslint::ESLintDetector;
pub use gh_actions::GHActionsInjectionDetector;
// pub use jscpd::JscpdDetector;
pub use mypy::MypyDetector;
pub use npm_audit::NpmAuditDetector;
// pub use pylint::PylintDetector;
pub use radon::RadonDetector;
pub use ruff::{RuffImportDetector, RuffLintDetector};
pub use semgrep::SemgrepDetector;
pub use tsc::TscDetector;
pub use vulture::VultureDetector;

// New detectors
pub use secrets::SecretDetector;
pub use empty_catch::EmptyCatchDetector;
pub use todo_scanner::TodoScanner;
pub use deep_nesting::DeepNestingDetector;
pub use magic_numbers::MagicNumbersDetector;
pub use large_files::LargeFilesDetector;
pub use path_traversal::PathTraversalDetector;
pub use command_injection::CommandInjectionDetector;
pub use ssrf::SsrfDetector;
pub use missing_docstrings::MissingDocstringsDetector;
pub use regex_dos::RegexDosDetector;
pub use sync_in_async::SyncInAsyncDetector;
pub use n_plus_one::NPlusOneDetector;

// Re-export external tool utilities
pub use external_tool::{
    ExternalToolResult,
    GraphContext,
    JsRuntime,
    batch_get_graph_context,
    get_graph_context,
    get_js_exec_command,
    get_js_runtime,
    is_python_tool_installed,
    is_tool_installed,
    run_external_tool,
    run_js_tool,
};

use std::path::Path;
use std::sync::Arc;

/// Create a default set of graph-based detectors
///
/// Returns detectors that only query the graph (no external tools required).
/// The `repository_path` is used by file-scanning detectors (security, etc.)
pub fn default_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    vec![
        // Core detectors
        Arc::new(CircularDependencyDetector::new()),
        Arc::new(GodClassDetector::new()),
        Arc::new(LongParameterListDetector::new()),
        // Code smell detectors
        Arc::new(DataClumpsDetector::new()),
        Arc::new(DeadCodeDetector::new()),
        Arc::new(FeatureEnvyDetector::new()),
        Arc::new(InappropriateIntimacyDetector::new()),
        Arc::new(LazyClassDetector::new()),
        Arc::new(MessageChainDetector::new()),
        Arc::new(MiddleManDetector::new()),
        Arc::new(RefusedBequestDetector::new()),
        // AI detectors
        Arc::new(AIBoilerplateDetector::new()),
        Arc::new(AIChurnDetector::new()),
        Arc::new(AIComplexitySpikeDetector::new()),
        Arc::new(AIDuplicateBlockDetector::new()),
        Arc::new(AIMissingTestsDetector::new()),
        Arc::new(AINamingPatternDetector::new()),
        // Graph/architecture detectors
        Arc::new(ArchitecturalBottleneckDetector::new()),
        Arc::new(CoreUtilityDetector::new()),
        Arc::new(DegreeCentralityDetector::new()),
        Arc::new(InfluentialCodeDetector::new()),
        Arc::new(ModuleCohesionDetector::new()),
        Arc::new(ShotgunSurgeryDetector::new()),
        // Security detectors (need repository path for file scanning)
        Arc::new(EvalDetector::with_repository_path(repository_path.to_path_buf())),
        Arc::new(PickleDeserializationDetector::with_repository_path(repository_path.to_path_buf())),
        Arc::new(SQLInjectionDetector::with_repository_path(repository_path.to_path_buf())),
        Arc::new(TaintDetector::with_repository_path(repository_path.to_path_buf())),
        Arc::new(UnsafeTemplateDetector::new()),
        // Misc detectors
        Arc::new(GeneratorMisuseDetector::new()),
        Arc::new(InfiniteLoopDetector::new()),
        Arc::new(UnusedImportsDetector::new()),
        // New security detectors
        Arc::new(SecretDetector::new(repository_path)),
        Arc::new(PathTraversalDetector::new(repository_path)),
        Arc::new(CommandInjectionDetector::new(repository_path)),
        Arc::new(SsrfDetector::new(repository_path)),
        Arc::new(RegexDosDetector::new(repository_path)),
        // New code quality detectors
        Arc::new(EmptyCatchDetector::new(repository_path)),
        Arc::new(TodoScanner::new(repository_path)),
        Arc::new(DeepNestingDetector::new(repository_path)),
        Arc::new(MagicNumbersDetector::new(repository_path)),
        Arc::new(LargeFilesDetector::new(repository_path)),
        Arc::new(MissingDocstringsDetector::new(repository_path)),
        // New performance detectors
        Arc::new(SyncInAsyncDetector::new(repository_path)),
        Arc::new(NPlusOneDetector::new(repository_path)),
    ]
}

/// Create all Python detectors for a repository
///
/// Includes: Bandit, Ruff, Mypy, Pylint, Radon, Vulture
pub fn python_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    vec![
        Arc::new(BanditDetector::new(repository_path)),
        Arc::new(RuffLintDetector::new(repository_path)),
        Arc::new(RuffImportDetector::new(repository_path)),
        Arc::new(MypyDetector::new(repository_path)),
        // Arc::new(PylintDetector::new(repository_path)),
        Arc::new(RadonDetector::new(repository_path)),
        Arc::new(VultureDetector::new(repository_path)),
    ]
}

/// Create all JavaScript/TypeScript detectors for a repository
///
/// Includes: ESLint, tsc, npm audit
pub fn javascript_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    vec![
        Arc::new(ESLintDetector::new(repository_path)),
        Arc::new(TscDetector::new(repository_path)),
        Arc::new(NpmAuditDetector::new(repository_path)),
    ]
}

/// Create security-focused detectors for a repository
///
/// Includes: Bandit, Semgrep, npm audit, GitHub Actions injection, secrets, path traversal, etc.
pub fn security_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    vec![
        Arc::new(BanditDetector::new(repository_path)),
        Arc::new(SemgrepDetector::new(repository_path)),
        Arc::new(NpmAuditDetector::new(repository_path)),
        Arc::new(GHActionsInjectionDetector::new(repository_path)),
        Arc::new(SecretDetector::new(repository_path)),
        Arc::new(PathTraversalDetector::new(repository_path)),
        Arc::new(CommandInjectionDetector::new(repository_path)),
        Arc::new(SsrfDetector::new(repository_path)),
        Arc::new(RegexDosDetector::new(repository_path)),
    ]
}

/// Create all external tool detectors for a repository
///
/// Includes all language-specific and cross-language detectors.
pub fn all_external_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    vec![
        // Python
        Arc::new(BanditDetector::new(repository_path)),
        Arc::new(RuffLintDetector::new(repository_path)),
        Arc::new(RuffImportDetector::new(repository_path)),
        Arc::new(MypyDetector::new(repository_path)),
        // Arc::new(PylintDetector::new(repository_path)),
        Arc::new(RadonDetector::new(repository_path)),
        Arc::new(VultureDetector::new(repository_path)),
        // JavaScript/TypeScript
        Arc::new(ESLintDetector::new(repository_path)),
        Arc::new(TscDetector::new(repository_path)),
        Arc::new(NpmAuditDetector::new(repository_path)),
        // Cross-language
        Arc::new(SemgrepDetector::new(repository_path)),
        // Arc::new(JscpdDetector::new(repository_path)),
        Arc::new(GHActionsInjectionDetector::new(repository_path)),
    ]
}

/// Create a detector engine with all default detectors
///
/// Convenience function for quickly setting up detection.
pub fn create_default_engine(workers: usize, repository_path: &Path) -> DetectorEngine {
    DetectorEngineBuilder::new()
        .workers(workers)
        .detectors(default_detectors(repository_path))
        .build()
}

/// Create a detector engine with all detectors for a repository
pub fn create_full_engine(workers: usize, repository_path: &Path) -> DetectorEngine {
    let mut detectors = default_detectors(repository_path);
    detectors.extend(all_external_detectors(repository_path));

    DetectorEngineBuilder::new()
        .workers(workers)
        .detectors(detectors)
        .build()
}

/// Create a file walker that respects .gitignore and .repotoireignore
/// 
/// Use this instead of `walkdir::WalkDir` to ensure ignored files are skipped.
/// 
/// # Arguments
/// * `repository_path` - Path to the repository root
/// * `extensions` - Optional list of file extensions to filter (e.g., &["py", "pyi"])
/// 
/// # Returns
/// Iterator over file paths (not directories)
/// 
/// # Example
/// ```rust,ignore
/// for path in walk_source_files(&repo_path, Some(&["py"])) {
///     // Process Python file
/// }
/// ```
pub fn walk_source_files<'a>(
    repository_path: &'a Path,
    extensions: Option<&'a [&'a str]>,
) -> impl Iterator<Item = std::path::PathBuf> + 'a {
    use ignore::WalkBuilder;
    
    let mut builder = WalkBuilder::new(repository_path);
    builder
        .hidden(true) // Respect hidden files setting
        .git_ignore(true) // Respect .gitignore
        .git_global(true) // Respect global gitignore
        .git_exclude(true) // Respect .git/info/exclude
        .require_git(false) // Work even if not a git repo
        .add_custom_ignore_filename(".repotoireignore"); // Support .repotoireignore files
    
    builder.build().filter_map(move |entry| {
        let entry = entry.ok()?;
        let path = entry.path();
        
        // Skip directories
        if !path.is_file() {
            return None;
        }
        
        // Filter by extension if specified
        if let Some(exts) = extensions {
            let ext = path.extension()?.to_str()?;
            if !exts.contains(&ext) {
                return None;
            }
        }
        
        Some(path.to_path_buf())
    })
}

/// Check if a line has a repotoire suppression comment
/// 
/// Supports multiple comment styles:
/// - `# repotoire: ignore` (Python, Shell)
/// - `// repotoire: ignore` (JS, Rust, Go, etc.)
/// - `/* repotoire: ignore */` (C-style)
/// - `-- repotoire: ignore` (SQL)
/// 
/// Also checks the previous line for standalone suppression comments.
/// 
/// # Arguments
/// * `line` - The current line to check
/// * `prev_line` - Optional previous line (for standalone comments)
/// 
/// # Returns
/// `true` if the line should be suppressed
pub fn is_line_suppressed(line: &str, prev_line: Option<&str>) -> bool {
    let suppression_pattern = "repotoire: ignore";
    let suppression_pattern_alt = "repotoire:ignore";
    
    // Check current line for inline suppression
    let line_lower = line.to_lowercase();
    if line_lower.contains(suppression_pattern) || line_lower.contains(suppression_pattern_alt) {
        return true;
    }
    
    // Check previous line for standalone suppression comment
    if let Some(prev) = prev_line {
        let prev_lower = prev.trim().to_lowercase();
        // Only count if previous line is just a comment (not code + comment)
        if (prev_lower.starts_with('#') || prev_lower.starts_with("//") || 
            prev_lower.starts_with("--") || prev_lower.starts_with("/*")) &&
           (prev_lower.contains(suppression_pattern) || prev_lower.contains(suppression_pattern_alt)) {
            return true;
        }
    }
    
    false
}
