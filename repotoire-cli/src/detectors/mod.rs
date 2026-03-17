//! Pure Rust code analysis detectors — zero external dependencies.
#![allow(unused_imports)]
//!
//! All 99 detectors are built-in Rust. No shelling out to Python, Node, or any external tool.
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

/// Shared helper: convert absolute path to relative using repository_path.
pub(crate) fn detector_relative_path(
    repository_path: &std::path::Path,
    path: &std::path::Path,
) -> std::path::PathBuf {
    path.strip_prefix(repository_path)
        .unwrap_or(path)
        .to_path_buf()
}

/// Macro for the standard detector `new()` constructor.
///
/// Generates: `pub fn new(repository_path: impl Into<PathBuf>) -> Self`
/// with fields `repository_path` and `max_findings`.
macro_rules! detector_new {
    ($max:expr) => {
        pub fn new(repository_path: impl Into<std::path::PathBuf>) -> Self {
            Self {
                repository_path: repository_path.into(),
                max_findings: $max,
            }
        }
    };
}
pub(crate) use detector_new;

/// Macro for the standard `set_precomputed_taint` trait override.
///
/// Expects `self.precomputed_cross` and `self.precomputed_intra` to be `OnceLock` fields.
macro_rules! impl_taint_precompute {
    () => {
        fn set_precomputed_taint(
            &self,
            cross: Vec<crate::detectors::taint::TaintPath>,
            intra: Vec<crate::detectors::taint::TaintPath>,
        ) {
            let _ = self.precomputed_cross.set(cross);
            let _ = self.precomputed_intra.set(intra);
        }
    };
}
pub(crate) use impl_taint_precompute;

// ── Registry infrastructure ────────────────────────────────────────────────

/// Everything a detector needs for construction.
/// Built once per analysis from ProjectConfig + StyleProfile.
pub struct DetectorInit<'a> {
    pub repo_path: &'a std::path::Path,
    pub project_config: &'a crate::config::ProjectConfig,
    pub resolver: crate::calibrate::ThresholdResolver,
    pub ngram_model: Option<&'a crate::calibrate::NgramModel>,
}

impl<'a> DetectorInit<'a> {
    /// Build a per-detector config with adaptive thresholds.
    pub fn config_for(&self, detector_name: &str) -> DetectorConfig {
        DetectorConfig::from_project_config_with_type(
            detector_name,
            self.project_config,
            self.repo_path,
        )
        .with_adaptive(self.resolver.clone())
    }

    #[cfg(test)]
    pub fn test_default() -> DetectorInit<'static> {
        let path: &'static std::path::Path =
            Box::leak(std::env::current_dir().unwrap().into_boxed_path());
        DetectorInit {
            repo_path: path,
            project_config: Box::leak(Box::new(crate::config::ProjectConfig::default())),
            resolver: crate::calibrate::ThresholdResolver::default(),
            ngram_model: None,
        }
    }
}

/// Trait for detectors that participate in the automatic registry.
/// Every registered detector implements create() as its canonical factory.
pub trait RegisteredDetector: Detector {
    fn create(init: &DetectorInit) -> Arc<dyn Detector>
    where
        Self: Sized;
}

/// Function pointer type for detector factories.
type DetectorFactory = fn(&DetectorInit) -> Arc<dyn Detector>;

/// Compile-time enforcement that D implements RegisteredDetector.
const fn register<D: RegisteredDetector>() -> DetectorFactory {
    D::create
}

/// Complete list of all registered detectors. Entries added in subsequent tasks.
const DETECTOR_FACTORIES: &[DetectorFactory] = &[];

/// Create all registered detectors from a unified init context.
pub fn create_all_detectors(init: &DetectorInit) -> Vec<Arc<dyn Detector>> {
    DETECTOR_FACTORIES.iter().map(|f| f(init)).collect()
}

// ── Module declarations ────────────────────────────────────────────────────

pub mod analysis_context;
pub mod base;
pub mod confidence_enrichment;
pub mod content_classifier;
pub mod detector_context;
mod engine;
pub mod file_cache;
pub mod file_index;
pub mod file_provider;
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
pub mod ast_fingerprint;

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
mod hierarchical_surprisal;
mod unsafe_template;

// Taint analysis module (graph-based data flow tracking)
pub mod taint;

// Function context and role inference
pub mod function_context;

// Class context and role inference
pub mod class_context;

// Framework/ORM detection for reducing false positives
pub mod framework_detection;

// API surface detection for reducing false positives on public API definitions
pub mod api_surface;

// Pre-computed context enrichment for AnalysisContext
pub mod module_metrics;
pub mod reachability;

// Misc detectors
mod generator_misuse;
mod infinite_loop;
mod unused_imports;

// Cross-detector analysis (ported from Python)
mod health_delta;
mod incremental_cache;
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

// Re-export detector context
pub use detector_context::{ContentFlags, DetectorContext};

// Re-export file cache
pub use file_cache::FileContentCache;

// Re-export analysis context
pub use analysis_context::AnalysisContext;

// Re-export file index
pub use file_index::{FileEntry, FileIndex};

// Re-export file provider
pub use file_provider::{FileProvider, SourceFiles};

// Re-export engine
pub use engine::{DetectorEngine, DetectorEngineBuilder, GdPrecomputed, precompute_gd_startup};

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
pub use ai_boilerplate::{AIBoilerplateDetector, BoilerplatePattern};
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
    MutexPoisoningRiskDetector, PanicDensityDetector, UnsafeWithoutSafetyCommentDetector,
    UnwrapWithoutContextDetector,
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
pub use incremental_cache::{CacheStats, CachedScoreResult, ConcurrentCacheView, IncrementalCache};
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
pub use hierarchical_surprisal::HierarchicalSurprisalDetector;
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
    let project_type = project_config.project_type(repository_path);
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
            make_config("long-methods"),
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
        Arc::new(PanicDensityDetector::new(repository_path)),
        // CI/CD security
        Arc::new(GHActionsInjectionDetector::new(repository_path)),
        // TLS/Certificate validation
        Arc::new(InsecureTlsDetector::new(repository_path)),
        // Dependency vulnerability auditing
        Arc::new(DepAuditDetector::new(repository_path)),
    ];

    // Predictive coding: hierarchical surprisal detector (always enabled, no n-gram dependency)
    detectors.push(Arc::new(HierarchicalSurprisalDetector::new()));

    // Legacy n-gram surprisal detector (still available when model is confident)
    match ngram_model {
        Some(model) if model.is_confident() => {
            tracing::debug!("SurprisalDetector enabled (n-gram model is confident)");
            detectors.push(Arc::new(SurprisalDetector::new(repository_path, model)));
        }
        Some(_) => {
            tracing::debug!("SurprisalDetector skipped: n-gram model not confident");
        }
        None => {
            tracing::debug!("SurprisalDetector skipped: no n-gram model available");
        }
    }

    detectors
}

/// Build an adaptive `ThresholdResolver` from an optional style profile.
///
/// This creates the same resolver used internally by `default_detectors_full()`
/// so callers can pass it to `DetectorEngine::set_threshold_resolver()` for
/// propagation into `AnalysisContext`.
pub fn build_threshold_resolver(
    style_profile: Option<&crate::calibrate::StyleProfile>,
) -> crate::calibrate::ThresholdResolver {
    crate::calibrate::ThresholdResolver::new(style_profile.cloned())
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
#[allow(dead_code)] // Public API for inline suppression checking
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
        if (trimmed_lower.starts_with('#')
            || trimmed_lower.starts_with("//")
            || trimmed_lower.starts_with("--")
            || trimmed_lower.starts_with("/*"))
            && check_suppression(prev, detector_name)
        {
            return true;
        }
    }

    false
}

/// Check if a file has a file-level suppression directive in the first 10 lines.
///
/// When a file contains `repotoire:ignore-file` (or `repotoire: ignore-file`)
/// in a comment near the top, ALL findings for that file are suppressed.
///
/// Supports multiple comment styles:
/// - `# repotoire:ignore-file` (Python, Shell)
/// - `// repotoire:ignore-file` (JS, Rust, Go, etc.)
/// - `/* repotoire:ignore-file */` (C-style)
/// - `-- repotoire:ignore-file` (SQL)
///
/// Also supports targeted file-level suppression with bracket syntax:
/// - `repotoire:ignore-file[sql-injection]` — suppresses only the named detector
///
/// # Arguments
/// * `content` - The full file content (only the first 10 lines are examined)
///
/// # Returns
/// `true` if the entire file should be suppressed
pub fn is_file_suppressed(content: &str) -> bool {
    is_file_suppressed_for(content, None)
}

/// Check if a file has a file-level suppression directive targeting a specific detector.
///
/// When `detector_name` is `None`, checks for blanket file suppression.
/// When `Some(name)`, also matches `repotoire:ignore-file[name]`.
///
/// # Arguments
/// * `content` - The full file content (only the first 10 lines are examined)
/// * `detector_name` - Optional detector slug to match against
///
/// # Returns
/// `true` if the entire file should be suppressed for the given detector (or all detectors)
pub fn is_file_suppressed_for(content: &str, detector_name: Option<&str>) -> bool {
    let pattern = "repotoire:ignore-file";
    let pattern_alt = "repotoire: ignore-file";

    for line in content.lines().take(10) {
        let lower = line.to_lowercase();

        // Must be in a comment
        let trimmed = lower.trim();
        let is_comment = trimmed.starts_with('#')
            || trimmed.starts_with("//")
            || trimmed.starts_with("/*")
            || trimmed.starts_with("--")
            || trimmed.starts_with('*'); // continuation of block comment

        if !is_comment {
            continue;
        }

        for pat in &[pattern, pattern_alt] {
            if let Some(idx) = lower.find(pat) {
                let after = idx + pat.len();
                let rest = &lower[after..];

                if rest.starts_with('[') {
                    // Targeted file-level suppression: repotoire:ignore-file[detector-name]
                    if let Some(end) = rest.find(']') {
                        let target = rest[1..end].trim();
                        if let Some(det) = detector_name {
                            if target == det.to_lowercase() {
                                return true;
                            }
                        }
                        // If no detector_name given, targeted suppression doesn't match blanket check
                    }
                } else {
                    // Bare file-level suppression: repotoire:ignore-file — suppress all
                    return true;
                }
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

    #[test]
    fn all_detectors_have_scope() {
        let tmp = tempfile::tempdir().expect("create tempdir");
        let detectors = default_detectors(tmp.path());
        for d in &detectors {
            let scope = d.detector_scope();
            // Exhaustive match ensures we handle all variants
            match scope {
                DetectorScope::FileLocal
                | DetectorScope::FileScopedGraph
                | DetectorScope::GraphWide => {}
            }
        }
        let file_local = detectors
            .iter()
            .filter(|d| d.detector_scope() == DetectorScope::FileLocal)
            .count();
        let graph_wide = detectors
            .iter()
            .filter(|d| d.detector_scope() == DetectorScope::GraphWide)
            .count();
        assert!(
            file_local > 20,
            "Expected 20+ FileLocal detectors, got {}",
            file_local
        );
        assert!(
            graph_wide >= 2,
            "Expected 2+ GraphWide detectors, got {}",
            graph_wide
        );
    }

    // ── is_file_suppressed (file-level suppression) ──────────────────

    #[test]
    fn test_file_suppressed_rust_comment() {
        let content = "// repotoire:ignore-file\nfn main() {}";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_rust_comment_with_space() {
        let content = "// repotoire: ignore-file\nfn main() {}";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_python_comment() {
        let content = "# repotoire:ignore-file\nimport os";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_c_style_comment() {
        let content = "/* repotoire:ignore-file */\nint main() {}";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_sql_comment() {
        let content = "-- repotoire:ignore-file\nSELECT 1;";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_block_comment_continuation() {
        // Block comment with directive on continuation line
        let content = "/*\n * repotoire:ignore-file\n */\nfn main() {}";
        assert!(is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_only_first_10_lines() {
        // Directive on line 11 should NOT suppress
        let mut lines: Vec<&str> = Vec::new();
        for _ in 0..10 {
            lines.push("// normal comment");
        }
        lines.push("// repotoire:ignore-file");
        let content = lines.join("\n");
        assert!(!is_file_suppressed(&content));
    }

    #[test]
    fn test_file_suppressed_within_10_lines() {
        // Directive on line 10 SHOULD suppress
        let mut lines: Vec<&str> = Vec::new();
        for _ in 0..9 {
            lines.push("// normal comment");
        }
        lines.push("// repotoire:ignore-file");
        let content = lines.join("\n");
        assert!(is_file_suppressed(&content));
    }

    #[test]
    fn test_file_not_suppressed_plain_code() {
        let content = "fn main() {\n    println!(\"hello\");\n}";
        assert!(!is_file_suppressed(content));
    }

    #[test]
    fn test_file_not_suppressed_in_code_line() {
        // Non-comment line containing the pattern should NOT suppress
        let content = "let x = \"repotoire:ignore-file\";\nfn main() {}";
        assert!(!is_file_suppressed(content));
    }

    #[test]
    fn test_file_suppressed_case_insensitive() {
        let content = "// Repotoire:Ignore-File\nfn main() {}";
        assert!(is_file_suppressed(content));
    }

    // ── is_file_suppressed_for (targeted file-level suppression) ─────

    #[test]
    fn test_file_suppressed_for_targeted() {
        let content = "// repotoire:ignore-file[sql-injection]\nfn main() {}";
        assert!(is_file_suppressed_for(content, Some("sql-injection")));
    }

    #[test]
    fn test_file_suppressed_for_targeted_no_match() {
        let content = "// repotoire:ignore-file[sql-injection]\nfn main() {}";
        assert!(!is_file_suppressed_for(content, Some("xss")));
    }

    #[test]
    fn test_file_suppressed_for_blanket() {
        // Bare ignore-file suppresses any detector
        let content = "// repotoire:ignore-file\nfn main() {}";
        assert!(is_file_suppressed_for(content, Some("any-detector")));
    }

    #[test]
    fn test_file_suppressed_for_targeted_does_not_match_blanket_check() {
        // Targeted suppression with no detector_name given should NOT match
        let content = "// repotoire:ignore-file[sql-injection]\nfn main() {}";
        assert!(!is_file_suppressed_for(content, None));
    }

    // ── Registry infrastructure ─────────────────────────────────────

    #[test]
    fn test_create_all_detectors_empty_registry() {
        let init = DetectorInit::test_default();
        let detectors = create_all_detectors(&init);
        // Registry is empty until detectors are migrated in subsequent tasks.
        assert_eq!(detectors.len(), 0);
    }

    #[test]
    fn test_detector_init_config_for() {
        let init = DetectorInit::test_default();
        let config = init.config_for("GodClassDetector");
        // Verify it produces a valid DetectorConfig with default coupling multiplier.
        assert!(config.coupling_multiplier > 0.0);
    }
}
