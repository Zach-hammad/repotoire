//! Pure Rust code analysis detectors — zero external dependencies.
#![allow(unused_imports)]
//!
//! All 110 detectors are built-in Rust. No shelling out to Python, Node, or any external tool.
//!
//! # Architecture
//!
//! ```text
//! run_detectors() → Detector trait → detect(ctx) → Vec<Finding>
//! ```
//!
//! Detectors are organized by category:
//! - `security/` — vulnerabilities, injection, auth, crypto (28 detectors)
//! - `bugs/` — runtime errors, logic errors, missing async (13 detectors)
//! - `architecture/` — coupling, dependencies, graph topology, bus factor (16 detectors)
//! - `performance/` — N+1 queries, sync-in-async, hot loops (3 detectors)
//! - `quality/` — code smells, complexity, naming, style (27 detectors)
//! - `ai/` — AI-generated code patterns (6 detectors)
//! - `ml_smells/` — ML/data science specific (8 detectors)
//! - `rust_smells/` — Rust-specific patterns (7 detectors)
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
            cross: Vec<crate::detectors::security::taint::TaintPath>,
            intra: Vec<crate::detectors::security::taint::TaintPath>,
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

/// Default detectors — high-value detectors that catch real bugs, security issues,
/// performance problems, and architectural debt. These run by default.
const DEFAULT_DETECTOR_FACTORIES: &[DetectorFactory] = &[
    // Security (all — these catch real vulnerabilities)
    register::<CircularDependencyDetector>(),
    register::<SQLInjectionDetector>(),
    register::<XssDetector>(),
    register::<CommandInjectionDetector>(),
    register::<SsrfDetector>(),
    register::<PathTraversalDetector>(),
    register::<SecretDetector>(),
    register::<InsecureCryptoDetector>(),
    register::<XxeDetector>(),
    register::<PrototypePollutionDetector>(),
    register::<InsecureTlsDetector>(),
    register::<CleartextCredentialsDetector>(),
    register::<NosqlInjectionDetector>(),
    register::<LogInjectionDetector>(),
    register::<InsecureDeserializeDetector>(),
    register::<CorsMisconfigDetector>(),
    register::<JwtWeakDetector>(),
    register::<DjangoSecurityDetector>(),
    register::<ExpressSecurityDetector>(),
    register::<ReactHooksDetector>(),
    register::<GHActionsInjectionDetector>(),
    register::<EvalDetector>(),
    register::<PickleDeserializationDetector>(),
    register::<UnsafeTemplateDetector>(),
    register::<InsecureCookieDetector>(),
    register::<InsecureRandomDetector>(),
    register::<RegexDosDetector>(),
    register::<HardcodedIpsDetector>(),
    // Bug patterns
    register::<MissingAwaitDetector>(),
    register::<UnhandledPromiseDetector>(),
    register::<EmptyCatchDetector>(),
    register::<BroadExceptionDetector>(),
    register::<MutableDefaultArgsDetector>(),
    register::<UnreachableCodeDetector>(),
    register::<CallbackHellDetector>(),
    register::<GeneratorMisuseDetector>(),
    register::<InfiniteLoopDetector>(),
    register::<ImplicitCoercionDetector>(),
    register::<WildcardImportsDetector>(),
    register::<GlobalVariablesDetector>(),
    register::<StringConcatLoopDetector>(),
    // Performance
    register::<NPlusOneDetector>(),
    register::<RegexInLoopDetector>(),
    register::<SyncInAsyncDetector>(),
    // Refactoring (strict thresholds)
    register::<GodClassDetector>(),
    register::<LongMethodsDetector>(),
    register::<DeepNestingDetector>(),
    register::<AIComplexitySpikeDetector>(),
    // Architecture
    register::<ShotgunSurgeryDetector>(),
    register::<SinglePointOfFailureDetector>(),
    register::<StructuralBridgeRiskDetector>(),
    register::<MutualRecursionDetector>(),
    register::<HiddenCouplingDetector>(),
    register::<CommunityMisplacementDetector>(),
    register::<PageRankDriftDetector>(),
    register::<TemporalBottleneckDetector>(),
    register::<ArchitecturalBottleneckDetector>(),
    register::<DegreeCentralityDetector>(),
    register::<SingleOwnerModuleDetector>(),
    register::<KnowledgeSiloDetector>(),
    register::<OrphanedKnowledgeDetector>(),
    register::<CriticalPathSingleOwnerDetector>(),
    // Rust-specific (bugs and safety)
    register::<UnwrapWithoutContextDetector>(),
    register::<UnsafeWithoutSafetyCommentDetector>(),
    register::<MutexPoisoningRiskDetector>(),
    register::<PanicDensityDetector>(),
    // Testing
    register::<TestInProductionDetector>(),
    // Dependencies
    register::<DepAuditDetector>(),
    // ML/Data Science (all — these are real bug catchers)
    register::<TorchLoadUnsafeDetector>(),
    register::<NanEqualityDetector>(),
    register::<MissingZeroGradDetector>(),
    register::<ForwardMethodDetector>(),
    register::<MissingRandomSeedDetector>(),
    register::<ChainIndexingDetector>(),
    register::<RequireGradTypoDetector>(),
    register::<DeprecatedTorchApiDetector>(),
    // Other keepers
    register::<HardcodedTimeoutDetector>(),
];

/// Deep-scan-only detectors — code smells, style, dead code, and speculative detectors.
/// These run only with `--all-detectors`.
const DEEP_ONLY_DETECTOR_FACTORIES: &[DetectorFactory] = &[
    // Rust style preferences (not bugs)
    register::<CloneInHotPathDetector>(),
    register::<MissingMustUseDetector>(),
    // Unused imports (linter-level, 573 findings on next.js alone)
    register::<UnusedImportsDetector>(),
    // Code smells (0% evidence from PRs)
    register::<LongParameterListDetector>(),
    register::<DataClumpsDetector>(),
    register::<LazyClassDetector>(),
    register::<FeatureEnvyDetector>(),
    register::<InappropriateIntimacyDetector>(),
    register::<MessageChainDetector>(),
    register::<MiddleManDetector>(),
    register::<RefusedBequestDetector>(),
    register::<ModuleCohesionDetector>(),
    // Dead code (0.03% of PRs)
    register::<DeadCodeDetector>(),
    register::<DeadStoreDetector>(),
    // Duplicate code (0% of PRs fix this)
    register::<DuplicateCodeDetector>(),
    register::<AIDuplicateBlockDetector>(),
    // AI detectors (speculative)
    register::<AIMissingTestsDetector>(),
    register::<AIBoilerplateDetector>(),
    register::<AIChurnDetector>(),
    register::<AINamingPatternDetector>(),
    // Style/lint (formatters do this)
    register::<TodoScanner>(),
    register::<CommentedCodeDetector>(),
    register::<SingleCharNamesDetector>(),
    register::<DebugCodeDetector>(),
    register::<MagicNumbersDetector>(),
    register::<BooleanTrapDetector>(),
    register::<InconsistentReturnsDetector>(),
    register::<MissingDocstringsDetector>(),
    // Informational (not actionable)
    register::<LargeFilesDetector>(),
    register::<InfluentialCodeDetector>(),
    // Predictive (experimental)
    register::<SurprisalDetector>(),
    register::<HierarchicalSurprisalDetector>(),
    // Rust style (not bugs)
    register::<BoxDynTraitDetector>(),
];

/// Create default detectors (high-value: security, bugs, performance, architecture).
/// Respects `[detectors.X] enabled = false` in repotoire.toml.
pub fn create_default_detectors(init: &DetectorInit) -> Vec<Arc<dyn Detector>> {
    DEFAULT_DETECTOR_FACTORIES
        .iter()
        .map(|f| f(init))
        .filter(|d| init.project_config.is_detector_enabled(d.name()))
        .collect()
}

/// Create ALL detectors including deep-scan detectors (code smells, style, dead code).
/// Respects `[detectors.X] enabled = false` in repotoire.toml.
pub fn create_all_detectors(init: &DetectorInit) -> Vec<Arc<dyn Detector>> {
    DEFAULT_DETECTOR_FACTORIES
        .iter()
        .chain(DEEP_ONLY_DETECTOR_FACTORIES.iter())
        .map(|f| f(init))
        .filter(|d| init.project_config.is_detector_enabled(d.name()))
        .collect()
}

// ── Category modules (detectors grouped by concern) ───────────────────────

pub mod ai;
pub mod architecture;
pub mod bugs;
pub mod performance;
pub mod quality;
pub mod security;

// ── Infra modules (shared machinery, not detectors) ───────────────────────

pub mod analysis_context;
pub mod api_surface;
pub mod ast_fingerprint;
pub mod base;
pub mod class_context;
pub mod confidence_enrichment;
pub mod content_classifier;
pub mod context_hmm;
pub mod detector_context;
mod engine;
pub mod file_cache;
pub mod file_index;
pub mod file_provider;
pub mod framework_detection;
pub mod function_context;
pub mod module_metrics;
pub mod reachability;
pub mod runner;
pub mod user_input;

// ML/Data Science and Rust-specific detectors (existing subdirectories)
mod ml_smells;
mod rust_smells;

// SIMD-accelerated string searching for hot detector loops
pub(crate) mod fast_search;

// Cross-detector analysis
mod core_utility;
mod health_delta;
mod hierarchical_surprisal;
mod incremental_cache;
mod risk_analyzer;
mod root_cause_analyzer;
mod surprisal;
mod voting_engine;

// ── Re-exports (backward-compatible public API) ───────────────────────────

// Base types
pub use analysis_context::AnalysisContext;
pub use base::{
    DetectionSummary, Detector, DetectorConfig, DetectorResult, DetectorScope, ProgressCallback,
};
pub use detector_context::{ContentFlags, DetectorContext};
pub use engine::{precompute_gd_startup, PrecomputedAnalysis, SerializablePrecomputed};
pub use file_cache::FileContentCache;
pub use file_index::{FileEntry, FileIndex};
pub use file_provider::{FileProvider, SourceFiles};
pub use function_context::{
    FunctionContext, FunctionContextBuilder, FunctionContextMap, FunctionRole,
};
pub use runner::{
    apply_hmm_context_filter, filter_test_file_findings, inject_taint_precomputed, run_detectors,
    sort_findings_deterministic,
};

// Taint analysis — re-exported at top level for backward compat
// (used extensively from engine/, predictive/, etc.)
pub use security::taint;

// Security detectors
pub use security::{
    CleartextCredentialsDetector, CommandInjectionDetector, CorsMisconfigDetector,
    DepAuditDetector, DjangoSecurityDetector, EvalDetector, ExpressSecurityDetector,
    GHActionsInjectionDetector, HardcodedIpsDetector, InsecureCookieDetector,
    InsecureCryptoDetector, InsecureDeserializeDetector, InsecureRandomDetector,
    InsecureTlsDetector, JwtWeakDetector, LogInjectionDetector, NosqlInjectionDetector,
    PathTraversalDetector, PickleDeserializationDetector, PrototypePollutionDetector,
    ReactHooksDetector, RegexDosDetector, SQLInjectionDetector, SecretDetector, SsrfDetector,
    UnsafeTemplateDetector, XssDetector, XxeDetector,
};

// Bug detectors
pub use bugs::{
    BroadExceptionDetector, CallbackHellDetector, EmptyCatchDetector, GeneratorMisuseDetector,
    GlobalVariablesDetector, ImplicitCoercionDetector, InfiniteLoopDetector, MissingAwaitDetector,
    MutableDefaultArgsDetector, StringConcatLoopDetector, UnhandledPromiseDetector,
    UnreachableCodeDetector, WildcardImportsDetector,
};

// Architecture detectors
pub use architecture::{
    ArchitecturalBottleneckDetector, CircularDependencyDetector, CommunityMisplacementDetector,
    CriticalPathSingleOwnerDetector, DegreeCentralityDetector, HiddenCouplingDetector,
    KnowledgeSiloDetector, ModuleCohesionDetector, MutualRecursionDetector,
    OrphanedKnowledgeDetector, PageRankDriftDetector, ShotgunSurgeryDetector,
    SingleOwnerModuleDetector, SinglePointOfFailureDetector, StructuralBridgeRiskDetector,
    TemporalBottleneckDetector,
};

// Performance detectors
pub use performance::{NPlusOneDetector, RegexInLoopDetector, SyncInAsyncDetector};

// Quality detectors
pub use quality::{
    BooleanTrapDetector, CommentedCodeDetector, DataClumpsDetector, DeadCodeDetector,
    DeadStoreDetector, DebugCodeDetector, DeepNestingDetector, DuplicateCodeDetector,
    FeatureEnvyDetector, GodClassDetector, GodClassThresholds, HardcodedTimeoutDetector,
    InappropriateIntimacyDetector, InconsistentReturnsDetector, InfluentialCodeDetector,
    LargeFilesDetector, LazyClassDetector, LongMethodsDetector, LongParameterListDetector,
    LongParameterThresholds, MagicNumbersDetector, MessageChainDetector, MiddleManDetector,
    MissingDocstringsDetector, RefusedBequestDetector, SingleCharNamesDetector,
    TestInProductionDetector, TodoScanner, UnusedImportsDetector,
};

// AI detectors
pub use ai::{
    AIBoilerplateDetector, AIChurnDetector, AIComplexitySpikeDetector, AIDuplicateBlockDetector,
    AIMissingTestsDetector, AINamingPatternDetector, BoilerplatePattern,
};

// ML/Data Science detectors
pub use ml_smells::{
    ChainIndexingDetector, DeprecatedTorchApiDetector, ForwardMethodDetector,
    MissingRandomSeedDetector, MissingZeroGradDetector, NanEqualityDetector,
    RequireGradTypoDetector, TorchLoadUnsafeDetector,
};

// Rust-specific detectors
pub use rust_smells::{
    BoxDynTraitDetector, CloneInHotPathDetector, MissingMustUseDetector,
    MutexPoisoningRiskDetector, PanicDensityDetector, UnsafeWithoutSafetyCommentDetector,
    UnwrapWithoutContextDetector,
};

// Cross-detector analysis
pub use health_delta::{
    estimate_batch_fix_impact, estimate_fix_impact, BatchHealthScoreDelta, HealthScoreDelta,
    HealthScoreDeltaCalculator, ImpactLevel, MetricsBreakdown,
};
pub use incremental_cache::{
    binary_file_hash, compute_fingerprint, prune_stale_caches, CacheStats, CachedScoreResult,
    ConcurrentCacheView, IncrementalCache,
};
pub use risk_analyzer::{analyze_compound_risks, RiskAnalyzer, RiskAssessment, RiskFactor};
pub use root_cause_analyzer::{RootCauseAnalysis, RootCauseAnalyzer, RootCauseSummary};
pub use voting_engine::{
    ConfidenceMethod, ConsensusResult, DetectorWeight, SeverityResolution, VotingEngine,
    VotingStats, VotingStrategy,
};

// Predictive detectors
pub use hierarchical_surprisal::HierarchicalSurprisalDetector;
pub use surprisal::SurprisalDetector;

use std::path::Path;
use std::sync::Arc;

/// Build an adaptive `ThresholdResolver` from an optional style profile.
///
/// Callers pass this to `PrecomputedAnalysis::to_context()` for
/// propagation into `AnalysisContext`.
pub fn build_threshold_resolver(
    style_profile: Option<&crate::calibrate::StyleProfile>,
) -> crate::calibrate::ThresholdResolver {
    crate::calibrate::ThresholdResolver::new(style_profile.cloned())
}

/// Create a file walker that respects .gitignore and .repotoireignore
pub fn walk_source_files<'a>(
    repository_path: &'a Path,
    extensions: Option<&'a [&'a str]>,
) -> impl Iterator<Item = std::path::PathBuf> + 'a {
    use ignore::WalkBuilder;

    let mut builder = WalkBuilder::new(repository_path);
    builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .require_git(false)
        .add_custom_ignore_filename(".repotoireignore");

    builder.build().filter_map(move |entry| {
        let entry = entry.ok()?;
        let path = entry.path();

        if !path.is_file() {
            return None;
        }

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
pub fn is_line_suppressed(line: &str, prev_line: Option<&str>) -> bool {
    let suppression_pattern = "repotoire: ignore";
    let suppression_pattern_alt = "repotoire:ignore";

    let line_lower = line.to_lowercase();
    if line_lower.contains(suppression_pattern) || line_lower.contains(suppression_pattern_alt) {
        return true;
    }

    if let Some(prev) = prev_line {
        let prev_lower = prev.trim().to_lowercase();
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
#[allow(dead_code)]
pub fn is_line_suppressed_for(line: &str, prev_line: Option<&str>, detector_name: &str) -> bool {
    fn check_suppression(text: &str, detector_name: &str) -> bool {
        let lower = text.to_lowercase();
        let det = detector_name.to_lowercase();

        for prefix in &["repotoire:ignore", "repotoire: ignore"] {
            if let Some(idx) = lower.find(prefix) {
                let after = idx + prefix.len();
                let rest = &lower[after..];
                if rest.starts_with('[') {
                    if let Some(end) = rest.find(']') {
                        let target = &rest[1..end];
                        if target.trim() == det {
                            return true;
                        }
                    }
                } else {
                    return true;
                }
            }
        }
        false
    }

    if check_suppression(line, detector_name) {
        return true;
    }

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
pub fn is_file_suppressed(content: &str) -> bool {
    is_file_suppressed_for(content, None)
}

/// Check if a file has a file-level suppression directive targeting a specific detector.
pub fn is_file_suppressed_for(content: &str, detector_name: Option<&str>) -> bool {
    let pattern = "repotoire:ignore-file";
    let pattern_alt = "repotoire: ignore-file";

    for line in content.lines().take(10) {
        let lower = line.to_lowercase();
        let trimmed = lower.trim();
        let is_comment = trimmed.starts_with('#')
            || trimmed.starts_with("//")
            || trimmed.starts_with("/*")
            || trimmed.starts_with("--")
            || trimmed.starts_with('*');

        if !is_comment {
            continue;
        }

        for pat in &[pattern, pattern_alt] {
            if let Some(idx) = lower.find(pat) {
                let after = idx + pat.len();
                let rest = &lower[after..];

                if rest.starts_with('[') {
                    if let Some(end) = rest.find(']') {
                        let target = rest[1..end].trim();
                        if let Some(det) = detector_name {
                            if target == det.to_lowercase() {
                                return true;
                            }
                        }
                    }
                } else {
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

    #[test]
    fn test_inline_suppression() {
        assert!(is_line_suppressed("x = 1  // repotoire:ignore", None));
        assert!(is_line_suppressed("x = 1  // repotoire: ignore", None));
    }

    #[test]
    fn test_prev_line_suppression() {
        assert!(is_line_suppressed("x = 1", Some("// repotoire:ignore")));
    }

    #[test]
    fn test_no_suppression() {
        assert!(!is_line_suppressed("x = 1", None));
        assert!(!is_line_suppressed("x = 1", Some("// normal comment")));
    }

    #[test]
    fn test_targeted_suppression() {
        assert!(is_line_suppressed_for(
            "x = 1  // repotoire:ignore[sql-injection]",
            None,
            "sql-injection"
        ));
        assert!(!is_line_suppressed_for(
            "x = 1  // repotoire:ignore[sql-injection]",
            None,
            "xss"
        ));
        assert!(is_line_suppressed_for(
            "x = 1  // repotoire:ignore",
            None,
            "xss"
        ));
        assert!(is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore[xss]"),
            "xss"
        ));
        assert!(!is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore[xss]"),
            "sql-injection"
        ));
    }

    #[test]
    fn test_targeted_suppression_with_space() {
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
        assert!(is_line_suppressed_for(
            "x = 1",
            Some("// repotoire:ignore"),
            "any-detector"
        ));
    }

    #[test]
    fn test_targeted_suppression_prev_line_non_comment() {
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
        assert!(!is_line_suppressed_for("x = 1", None, "sql-injection"));
        assert!(!is_line_suppressed_for(
            "x = 1",
            Some("// normal comment"),
            "sql-injection"
        ));
    }

    #[test]
    fn all_detectors_have_scope() {
        let init = DetectorInit::test_default();
        let detectors = create_all_detectors(&init);
        for d in &detectors {
            let scope = d.detector_scope();
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

    #[test]
    fn test_file_suppressed_rust_comment() {
        assert!(is_file_suppressed("// repotoire:ignore-file\nfn main() {}"));
    }

    #[test]
    fn test_file_suppressed_rust_comment_with_space() {
        assert!(is_file_suppressed(
            "// repotoire: ignore-file\nfn main() {}"
        ));
    }

    #[test]
    fn test_file_suppressed_python_comment() {
        assert!(is_file_suppressed("# repotoire:ignore-file\nimport os"));
    }

    #[test]
    fn test_file_suppressed_c_style_comment() {
        assert!(is_file_suppressed(
            "/* repotoire:ignore-file */\nint main() {}"
        ));
    }

    #[test]
    fn test_file_suppressed_sql_comment() {
        assert!(is_file_suppressed("-- repotoire:ignore-file\nSELECT 1;"));
    }

    #[test]
    fn test_file_suppressed_block_comment_continuation() {
        assert!(is_file_suppressed(
            "/*\n * repotoire:ignore-file\n */\nfn main() {}"
        ));
    }

    #[test]
    fn test_file_suppressed_only_first_10_lines() {
        let mut lines: Vec<&str> = Vec::new();
        for _ in 0..10 {
            lines.push("// normal comment");
        }
        lines.push("// repotoire:ignore-file");
        assert!(!is_file_suppressed(&lines.join("\n")));
    }

    #[test]
    fn test_file_suppressed_within_10_lines() {
        let mut lines: Vec<&str> = Vec::new();
        for _ in 0..9 {
            lines.push("// normal comment");
        }
        lines.push("// repotoire:ignore-file");
        assert!(is_file_suppressed(&lines.join("\n")));
    }

    #[test]
    fn test_file_not_suppressed_plain_code() {
        assert!(!is_file_suppressed(
            "fn main() {\n    println!(\"hello\");\n}"
        ));
    }

    #[test]
    fn test_file_not_suppressed_in_code_line() {
        assert!(!is_file_suppressed(
            "let x = \"repotoire:ignore-file\";\nfn main() {}"
        ));
    }

    #[test]
    fn test_file_suppressed_case_insensitive() {
        assert!(is_file_suppressed("// Repotoire:Ignore-File\nfn main() {}"));
    }

    #[test]
    fn test_file_suppressed_for_targeted() {
        assert!(is_file_suppressed_for(
            "// repotoire:ignore-file[sql-injection]\nfn main() {}",
            Some("sql-injection")
        ));
    }

    #[test]
    fn test_file_suppressed_for_targeted_no_match() {
        assert!(!is_file_suppressed_for(
            "// repotoire:ignore-file[sql-injection]\nfn main() {}",
            Some("xss")
        ));
    }

    #[test]
    fn test_file_suppressed_for_blanket() {
        assert!(is_file_suppressed_for(
            "// repotoire:ignore-file\nfn main() {}",
            Some("any-detector")
        ));
    }

    #[test]
    fn test_file_suppressed_for_targeted_does_not_match_blanket_check() {
        assert!(!is_file_suppressed_for(
            "// repotoire:ignore-file[sql-injection]\nfn main() {}",
            None
        ));
    }

    #[test]
    fn test_create_all_detectors_registry() {
        let init = DetectorInit::test_default();
        let default = create_default_detectors(&init);
        let all = create_all_detectors(&init);
        assert!(default.len() > 50, "default detectors: {}", default.len());
        assert!(
            all.len() > default.len(),
            "all ({}) should exceed default ({})",
            all.len(),
            default.len()
        );
        assert_eq!(all.len(), 110);
    }

    #[test]
    fn test_detector_init_config_for() {
        let init = DetectorInit::test_default();
        let config = init.config_for("GodClassDetector");
        assert!(config.coupling_multiplier > 0.0);
    }
}
