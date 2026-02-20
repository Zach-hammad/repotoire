//! Pure Rust code analysis detectors — zero external dependencies.
#![allow(unused_imports)]
//!
//! All 112+ detectors are built-in Rust. No shelling out to Python, Node, or any external tool.
//!
//! # Architecture
//!
//! ```text
//! DetectorEngine → Detector trait → detect(graph) → Vec<Finding>
//! ```
//!
//! Detectors run in parallel via rayon. Each receives the code graph
//! and returns findings. Security detectors also use SSA-based
//! intra-function taint analysis via tree-sitter ASTs.

pub mod base;
pub mod content_classifier;
mod engine;
pub mod streaming_engine;

// Context classification using HMM
pub mod context_hmm;

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

// ML/Data Science detectors (PyTorch, TensorFlow, Scikit-Learn, Pandas, NumPy)
mod ml_smells;
mod rust_smells;

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
mod surprisal;
mod taint_detector;
mod unsafe_template;

// Taint analysis module (graph-based data flow tracking)
pub mod data_flow;
pub mod ssa_flow;
pub mod taint;

// Function context and role inference
pub mod function_context;

// Class context and role inference
pub mod class_context;

// Framework/ORM detection for reducing false positives
pub mod framework_detection;

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

mod boolean_trap;
mod broad_exception;
mod callback_hell;
mod cleartext_credentials;
mod command_injection;
mod commented_code;
mod cors_misconfig;
mod dead_store;
mod debug_code;
mod deep_nesting;
mod dep_audit;
mod django_security;
mod duplicate_code;
mod empty_catch;
mod express_security;
mod gh_actions;
mod global_variables;
mod hardcoded_ips;
mod hardcoded_timeout;
mod implicit_coercion;
mod inconsistent_returns;
mod insecure_cookie;
mod insecure_crypto;
mod insecure_deserialize;
mod insecure_random;
mod insecure_tls;
mod jwt_weak;
mod large_files;
mod log_injection;
mod long_methods;
mod magic_numbers;
mod missing_await;
mod missing_docstrings;
mod mutable_default_args;
mod n_plus_one;
mod nosql_injection;
mod path_traversal;
mod prototype_pollution;
mod react_hooks;
mod regex_dos;
mod regex_in_loop;
mod secrets;
mod single_char_names;
mod ssrf;
mod string_concat_loop;
mod sync_in_async;
mod test_in_production;
mod todo_scanner;
mod unhandled_promise;
mod unreachable_code;
mod wildcard_imports;
mod xss;
mod xxe;

// Re-export base types
pub use base::{
    DetectionSummary, Detector, DetectorConfig, DetectorResult, DetectorScope, ProgressCallback,
};

// Re-export engine
pub use engine::{DetectorEngine, DetectorEngineBuilder};

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

// Re-export ML/Data Science detectors
pub use ml_smells::{
    ChainIndexingDetector, DeprecatedTorchApiDetector, ForwardMethodDetector,
    MissingRandomSeedDetector, MissingZeroGradDetector, NanEqualityDetector,
    RequireGradTypoDetector, TorchLoadUnsafeDetector,
};

// Re-export Rust-specific detectors
pub use rust_smells::{
    BoxDynTraitDetector, CloneInHotPathDetector, MissingMustUseDetector,
    MutexPoisoningRiskDetector, UnsafeWithoutSafetyCommentDetector, UnwrapWithoutContextDetector,
};

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
pub use incremental_cache::{CacheStats, CachedScoreResult, IncrementalCache};
pub use query_cache::{ClassData, FileData, FunctionData, QueryCache};
pub use risk_analyzer::{analyze_compound_risks, RiskAnalyzer, RiskAssessment, RiskFactor};
pub use root_cause_analyzer::{RootCauseAnalysis, RootCauseAnalyzer, RootCauseSummary};
pub use voting_engine::{
    ConfidenceMethod, ConsensusResult, DetectorWeight, SeverityResolution, VotingEngine,
    VotingStats, VotingStrategy,
};

pub use gh_actions::GHActionsInjectionDetector;

// New detectors
pub use boolean_trap::BooleanTrapDetector;
pub use broad_exception::BroadExceptionDetector;
pub use callback_hell::CallbackHellDetector;
pub use cleartext_credentials::CleartextCredentialsDetector;
pub use command_injection::CommandInjectionDetector;
pub use commented_code::CommentedCodeDetector;
pub use cors_misconfig::CorsMisconfigDetector;
pub use dead_store::DeadStoreDetector;
pub use debug_code::DebugCodeDetector;
pub use deep_nesting::DeepNestingDetector;
pub use dep_audit::DepAuditDetector;
pub use django_security::DjangoSecurityDetector;
pub use duplicate_code::DuplicateCodeDetector;
pub use empty_catch::EmptyCatchDetector;
pub use express_security::ExpressSecurityDetector;
pub use global_variables::GlobalVariablesDetector;
pub use hardcoded_ips::HardcodedIpsDetector;
pub use hardcoded_timeout::HardcodedTimeoutDetector;
pub use implicit_coercion::ImplicitCoercionDetector;
pub use inconsistent_returns::InconsistentReturnsDetector;
pub use insecure_cookie::InsecureCookieDetector;
pub use insecure_crypto::InsecureCryptoDetector;
pub use insecure_deserialize::InsecureDeserializeDetector;
pub use insecure_random::InsecureRandomDetector;
pub use insecure_tls::InsecureTlsDetector;
pub use jwt_weak::JwtWeakDetector;
pub use large_files::LargeFilesDetector;
pub use log_injection::LogInjectionDetector;
pub use long_methods::LongMethodsDetector;
pub use magic_numbers::MagicNumbersDetector;
pub use missing_await::MissingAwaitDetector;
pub use missing_docstrings::MissingDocstringsDetector;
pub use mutable_default_args::MutableDefaultArgsDetector;
pub use n_plus_one::NPlusOneDetector;
pub use nosql_injection::NosqlInjectionDetector;
pub use path_traversal::PathTraversalDetector;
pub use prototype_pollution::PrototypePollutionDetector;
pub use react_hooks::ReactHooksDetector;
pub use regex_dos::RegexDosDetector;
pub use regex_in_loop::RegexInLoopDetector;
pub use secrets::SecretDetector;
pub use single_char_names::SingleCharNamesDetector;
pub use ssrf::SsrfDetector;
pub use string_concat_loop::StringConcatLoopDetector;
pub use sync_in_async::SyncInAsyncDetector;
pub use test_in_production::TestInProductionDetector;
pub use todo_scanner::TodoScanner;
pub use unhandled_promise::UnhandledPromiseDetector;
pub use unreachable_code::UnreachableCodeDetector;
pub use wildcard_imports::WildcardImportsDetector;
pub use surprisal::SurprisalDetector;
pub use xss::XssDetector;
pub use xxe::XxeDetector;

// Re-export function context types
pub use function_context::{
    FunctionContext, FunctionContextBuilder, FunctionContextMap, FunctionRole,
};

// External tool wrappers removed — pure Rust detectors only
// External tool wrappers removed — pure Rust detectors only

use crate::config::ProjectConfig;
use std::path::Path;
use std::sync::Arc;

/// Create a default set of graph-based detectors
///
/// Returns detectors that only query the graph (no external tools required).
/// The `repository_path` is used by file-scanning detectors (security, etc.)
/// The `project_config` is used to apply per-project threshold overrides.
pub fn default_detectors(repository_path: &Path) -> Vec<Arc<dyn Detector>> {
    default_detectors_with_config(repository_path, &ProjectConfig::default())
}

/// Create a default set of graph-based detectors with project configuration
///
/// This variant allows passing project-level configuration for threshold overrides.
pub fn default_detectors_with_config(
    repository_path: &Path,
    project_config: &ProjectConfig,
) -> Vec<Arc<dyn Detector>> {
    default_detectors_with_profile(repository_path, project_config, None)
}

pub fn default_detectors_with_profile(
    repository_path: &Path,
    project_config: &ProjectConfig,
    style_profile: Option<&crate::calibrate::StyleProfile>,
) -> Vec<Arc<dyn Detector>> {
    default_detectors_full(repository_path, project_config, style_profile, None)
}

pub fn default_detectors_with_ngram(
    repository_path: &Path,
    project_config: &ProjectConfig,
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
) -> Vec<Arc<dyn Detector>> {
    default_detectors_full(repository_path, project_config, style_profile, ngram_model)
}

fn default_detectors_full(
    repository_path: &Path,
    project_config: &ProjectConfig,
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
) -> Vec<Arc<dyn Detector>> {
    // Get project type for coupling/complexity multipliers
    let project_type = project_config.get_project_type(repository_path);
    tracing::info!(
        "Detected project type: {:?} (coupling multiplier: {:.1}x)",
        project_type,
        project_type.coupling_multiplier()
    );

    // Build adaptive threshold resolver
    let resolver = crate::calibrate::ThresholdResolver::new(style_profile.cloned());

    // Helper to build detector config with adaptive resolver
    let make_config = |name: &str| -> DetectorConfig {
        DetectorConfig::from_project_config_with_type(name, project_config, repository_path)
            .with_adaptive(resolver.clone())
    };

    let mut detectors: Vec<Arc<dyn Detector>> = vec![
        // Core detectors (with project config support)
        Arc::new(CircularDependencyDetector::new()),
        Arc::new(GodClassDetector::with_config(make_config(
            "GodClassDetector",
        ))),
        Arc::new(LongParameterListDetector::with_config(make_config(
            "LongParameterListDetector",
        ))),
        // Code smell detectors
        Arc::new(DataClumpsDetector::with_config(make_config(
            "DataClumpsDetector",
        ))),
        Arc::new(DeadCodeDetector::new()),
        Arc::new(FeatureEnvyDetector::with_config(make_config(
            "FeatureEnvyDetector",
        ))),
        Arc::new(InappropriateIntimacyDetector::new()),
        Arc::new(LazyClassDetector::new()),
        Arc::new(MessageChainDetector::new(repository_path)),
        Arc::new(MiddleManDetector::new()),
        Arc::new(RefusedBequestDetector::new()),
        // AI detectors
        Arc::new(AIBoilerplateDetector::new()),
        Arc::new(AIChurnDetector::new()),
        Arc::new(AIComplexitySpikeDetector::new()),
        Arc::new(AIDuplicateBlockDetector::new()),
        Arc::new(AIMissingTestsDetector::new()),
        Arc::new(AINamingPatternDetector::new()),
        // ML/Data Science detectors (PyTorch, TensorFlow, Scikit-Learn, Pandas, NumPy)
        Arc::new(TorchLoadUnsafeDetector::new(repository_path)),
        Arc::new(NanEqualityDetector::new(repository_path)),
        Arc::new(MissingZeroGradDetector::new(repository_path)),
        Arc::new(ForwardMethodDetector::new(repository_path)),
        Arc::new(MissingRandomSeedDetector::new(repository_path)),
        Arc::new(ChainIndexingDetector::new(repository_path)),
        Arc::new(RequireGradTypoDetector::new(repository_path)),
        Arc::new(DeprecatedTorchApiDetector::new(repository_path)),
        // Graph/architecture detectors
        Arc::new(ArchitecturalBottleneckDetector::with_config(make_config(
            "ArchitecturalBottleneckDetector",
        ))),
        Arc::new(CoreUtilityDetector::new()),
        Arc::new(DegreeCentralityDetector::with_config(make_config(
            "DegreeCentralityDetector",
        ))),
        Arc::new(InfluentialCodeDetector::with_config(make_config(
            "InfluentialCodeDetector",
        ))),
        Arc::new(ModuleCohesionDetector::with_config(make_config(
            "ModuleCohesionDetector",
        ))),
        Arc::new(ShotgunSurgeryDetector::with_config(make_config(
            "ShotgunSurgeryDetector",
        ))),
        // Security detectors (need repository path for file scanning)
        Arc::new(EvalDetector::with_repository_path(
            repository_path.to_path_buf(),
        )),
        Arc::new(PickleDeserializationDetector::with_repository_path(
            repository_path.to_path_buf(),
        )),
        Arc::new(SQLInjectionDetector::with_repository_path(
            repository_path.to_path_buf(),
        )),
        // TaintDetector disabled - naive file-based analysis, replaced by graph-based detectors:
        // PathTraversalDetector, CommandInjectionDetector, SqlInjectionDetector, etc.
        // Arc::new(TaintDetector::with_repository_path(repository_path.to_path_buf())),
        Arc::new(UnsafeTemplateDetector::with_repository_path(
            repository_path.to_path_buf(),
        )),
        // Misc detectors
        Arc::new(GeneratorMisuseDetector::with_path(repository_path)),
        Arc::new(InfiniteLoopDetector::with_path(repository_path)),
        Arc::new(UnusedImportsDetector::new(repository_path)),
        // New security detectors
        Arc::new(SecretDetector::new(repository_path)),
        Arc::new(PathTraversalDetector::new(repository_path)),
        Arc::new(CommandInjectionDetector::new(repository_path)),
        Arc::new(SsrfDetector::new(repository_path)),
        Arc::new(RegexDosDetector::new(repository_path)),
        // New code quality detectors
        Arc::new(EmptyCatchDetector::new(repository_path)),
        Arc::new(TodoScanner::new(repository_path)),
        Arc::new(DeepNestingDetector::with_resolver(
            repository_path,
            &resolver,
        )),
        Arc::new(MagicNumbersDetector::new(repository_path)),
        Arc::new(LargeFilesDetector::with_resolver(
            repository_path,
            &resolver,
        )),
        Arc::new(MissingDocstringsDetector::new(repository_path)),
        // New performance detectors
        Arc::new(SyncInAsyncDetector::new(repository_path)),
        Arc::new(NPlusOneDetector::new(repository_path)),
        // More security detectors
        Arc::new(InsecureCryptoDetector::new(repository_path)),
        Arc::new(XssDetector::new(repository_path)),
        Arc::new(HardcodedIpsDetector::new(repository_path)),
        Arc::new(InsecureRandomDetector::new(repository_path)),
        Arc::new(CorsMisconfigDetector::new(repository_path)),
        // More code quality detectors
        Arc::new(DebugCodeDetector::new(repository_path)),
        Arc::new(CommentedCodeDetector::new(repository_path)),
        Arc::new(LongMethodsDetector::with_config(
            repository_path,
            DetectorConfig::from_project_config("long-methods", project_config),
        )),
        Arc::new(DuplicateCodeDetector::new(repository_path)),
        Arc::new(UnreachableCodeDetector::new(repository_path)),
        Arc::new(StringConcatLoopDetector::new(repository_path)),
        // Additional security
        Arc::new(XxeDetector::new(repository_path)),
        Arc::new(InsecureDeserializeDetector::new(repository_path)),
        Arc::new(CleartextCredentialsDetector::new(repository_path)),
        // Code quality
        Arc::new(WildcardImportsDetector::new(repository_path)),
        Arc::new(MutableDefaultArgsDetector::new(repository_path)),
        Arc::new(GlobalVariablesDetector::new(repository_path)),
        Arc::new(ImplicitCoercionDetector::new(repository_path)),
        Arc::new(SingleCharNamesDetector::new(repository_path)),
        // Async issues
        Arc::new(MissingAwaitDetector::new(repository_path)),
        Arc::new(UnhandledPromiseDetector::new(repository_path)),
        Arc::new(CallbackHellDetector::new(repository_path)),
        // Testing
        Arc::new(TestInProductionDetector::new(repository_path)),
        // More security
        Arc::new(InsecureCookieDetector::new(repository_path)),
        Arc::new(JwtWeakDetector::new(repository_path)),
        Arc::new(PrototypePollutionDetector::new(repository_path)),
        Arc::new(NosqlInjectionDetector::new(repository_path)),
        Arc::new(LogInjectionDetector::new(repository_path)),
        // More quality
        Arc::new(BroadExceptionDetector::new(repository_path)),
        Arc::new(BooleanTrapDetector::new(repository_path)),
        Arc::new(InconsistentReturnsDetector::new(repository_path)),
        Arc::new(DeadStoreDetector::new(repository_path)),
        Arc::new(HardcodedTimeoutDetector::new(repository_path)),
        // Performance
        Arc::new(RegexInLoopDetector::new(repository_path)),
        // Framework-specific
        Arc::new(ReactHooksDetector::new(repository_path)),
        Arc::new(DjangoSecurityDetector::new(repository_path)),
        Arc::new(ExpressSecurityDetector::new(repository_path)),
        // Rust-specific detectors
        Arc::new(UnwrapWithoutContextDetector::new(repository_path)),
        Arc::new(UnsafeWithoutSafetyCommentDetector::new(repository_path)),
        Arc::new(CloneInHotPathDetector::new(repository_path)),
        Arc::new(MissingMustUseDetector::new(repository_path)),
        Arc::new(BoxDynTraitDetector::new(repository_path)),
        Arc::new(MutexPoisoningRiskDetector::new(repository_path)),
        // CI/CD security
        Arc::new(GHActionsInjectionDetector::new(repository_path)),
        // TLS/Certificate validation
        Arc::new(InsecureTlsDetector::new(repository_path)),
        // Dependency vulnerability auditing
        Arc::new(DepAuditDetector::new(repository_path)),
    ];

    // Predictive coding: surprisal detector (only when n-gram model is available)
    if let Some(model) = ngram_model {
        if model.is_confident() {
            detectors.push(Arc::new(SurprisalDetector::new(repository_path, model)));
        }
    }

    detectors
}

/// Create a detector engine with all default detectors
///
/// Convenience function for quickly setting up detection.
#[allow(dead_code)] // Public API - may be used by external callers
pub fn create_default_engine(workers: usize, repository_path: &Path) -> DetectorEngine {
    DetectorEngineBuilder::new()
        .workers(workers)
        .detectors(default_detectors(repository_path))
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
        if (prev_lower.starts_with('#')
            || prev_lower.starts_with("//")
            || prev_lower.starts_with("--")
            || prev_lower.starts_with("/*"))
            && (prev_lower.contains(suppression_pattern)
                || prev_lower.contains(suppression_pattern_alt))
        {
            return true;
        }
    }

    false
}

/// Check if a line has a repotoire suppression comment targeting a specific detector.
///
/// Supports targeted suppression via bracket syntax:
/// - `repotoire:ignore[sql-injection]` — suppresses only the named detector
/// - `repotoire:ignore` (no brackets) — suppresses ALL detectors
///
/// Also checks the previous line for standalone suppression comments, using
/// the same logic as [`is_line_suppressed`].
///
/// # Arguments
/// * `line` - The current line to check
/// * `prev_line` - Optional previous line (for standalone comments)
/// * `detector_name` - The detector slug to match against (e.g. `"sql-injection"`)
///
/// # Returns
/// `true` if the line should be suppressed for the given detector
pub fn is_line_suppressed_for(line: &str, prev_line: Option<&str>, detector_name: &str) -> bool {
    fn check_suppression(text: &str, detector_name: &str) -> bool {
        let lower = text.to_lowercase();
        let det = detector_name.to_lowercase();

        // Look for both "repotoire:ignore" and "repotoire: ignore" variants
        for prefix in &["repotoire:ignore", "repotoire: ignore"] {
            if let Some(idx) = lower.find(prefix) {
                let after = idx + prefix.len();
                let rest = &lower[after..];
                if rest.starts_with('[') {
                    // Targeted suppression: repotoire:ignore[name]
                    if let Some(end) = rest.find(']') {
                        let target = &rest[1..end];
                        if target.trim() == det {
                            return true;
                        }
                    }
                } else {
                    // Bare suppression: repotoire:ignore (no brackets) — suppress all
                    return true;
                }
            }
        }
        false
    }

    // Check current line for inline suppression
    if check_suppression(line, detector_name) {
        return true;
    }

    // Check previous line for standalone suppression comment
    if let Some(prev) = prev_line {
        let trimmed = prev.trim();
        let trimmed_lower = trimmed.to_lowercase();
        if trimmed_lower.starts_with('#')
            || trimmed_lower.starts_with("//")
            || trimmed_lower.starts_with("--")
            || trimmed_lower.starts_with("/*")
        {
            if check_suppression(prev, detector_name) {
                return true;
            }
        }
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── is_line_suppressed (existing behaviour) ──────────────────────

    #[test]
    fn test_inline_suppression() {
        assert!(is_line_suppressed(
            "x = 1  // repotoire:ignore",
            None
        ));
        assert!(is_line_suppressed(
            "x = 1  // repotoire: ignore",
            None
        ));
    }

    #[test]
    fn test_prev_line_suppression() {
        assert!(is_line_suppressed(
            "x = 1",
            Some("// repotoire:ignore")
        ));
    }

    #[test]
    fn test_no_suppression() {
        assert!(!is_line_suppressed("x = 1", None));
        assert!(!is_line_suppressed("x = 1", Some("// normal comment")));
    }

    // ── is_line_suppressed_for (targeted suppression) ────────────────

    #[test]
    fn test_targeted_suppression() {
        // Inline targeted suppression matches correct detector
        assert!(is_line_suppressed_for(
            "x = 1  // repotoire:ignore[sql-injection]",
            None,
            "sql-injection"
        ));
        // Inline targeted suppression does NOT match a different detector
        assert!(!is_line_suppressed_for(
            "x = 1  // repotoire:ignore[sql-injection]",
            None,
            "xss"
        ));
        // Bare ignore suppresses ALL detectors
        assert!(is_line_suppressed_for(
            "x = 1  // repotoire:ignore",
            None,
            "xss"
        ));
        // Previous-line targeted suppression matches
        assert!(is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore[xss]"),
            "xss"
        ));
        // Previous-line targeted suppression does NOT match different detector
        assert!(!is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore[xss]"),
            "sql-injection"
        ));
    }

    #[test]
    fn test_targeted_suppression_with_space() {
        // Variant with space: "repotoire: ignore[name]"
        assert!(is_line_suppressed_for(
            "x = 1  // repotoire: ignore[sql-injection]",
            None,
            "sql-injection"
        ));
        assert!(!is_line_suppressed_for(
            "x = 1  // repotoire: ignore[sql-injection]",
            None,
            "xss"
        ));
    }

    #[test]
    fn test_targeted_suppression_case_insensitive() {
        assert!(is_line_suppressed_for(
            "x = 1  // Repotoire:Ignore[SQL-Injection]",
            None,
            "sql-injection"
        ));
    }

    #[test]
    fn test_targeted_suppression_bare_prev_line() {
        // Bare ignore on previous line suppresses all detectors
        assert!(is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore"),
            "any-detector"
        ));
    }

    #[test]
    fn test_targeted_suppression_prev_line_non_comment() {
        // Previous line that is code (not a comment) should NOT suppress
        assert!(!is_line_suppressed_for(
            "x = 1",
            Some("x = 1 repotoire:ignore[xss]"),
            "xss"
        ));
    }

    #[test]
    fn test_targeted_suppression_python_comment() {
        assert!(is_line_suppressed_for(
            "x = 1  # repotoire:ignore[magic-numbers]",
            None,
            "magic-numbers"
        ));
    }

    #[test]
    fn test_targeted_suppression_no_match_no_suppress() {
        assert!(!is_line_suppressed_for(
            "x = 1",
            None,
            "sql-injection"
        ));
        assert!(!is_line_suppressed_for(
            "x = 1",
            Some("// normal comment"),
            "sql-injection"
        ));
    }
}
