//! Project-level configuration support
//!
//! Loads per-project configuration from `repotoire.toml`, `.repotoirerc.json`,
//! or `.repotoire.yaml` files in the repository root.
//!
//! # Configuration Format
//!
//! ```toml
//! # repotoire.toml
//!
//! [detectors.god-class]
//! enabled = true
//! thresholds = { method_count = 30, loc = 600 }
//!
//! [detectors.sql-injection]
//! severity = "high"  # Override default severity
//!
//! [scoring]
//! security_multiplier = 5.0
//! pillar_weights = { structure = 0.3, quality = 0.4, architecture = 0.3 }
//!
//! [exclude]
//! paths = ["generated/", "vendor/"]
//!
//! [defaults]
//! format = "text"
//! severity = "low"
//! workers = 8
//! ```

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;
use tracing::{debug, warn};

/// Project type affects detector thresholds and scoring
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ProjectType {
    /// Web apps, REST APIs, CRUD - strictest coupling analysis (default)
    #[default]
    Web,
    /// Language interpreters, VMs - lenient coupling, skip dispatch tables
    Interpreter,
    /// Compilers, transpilers - pipeline architecture
    Compiler,
    /// Reusable libraries - focus on public API
    Library,
    /// UI frameworks, component libraries - high internal coupling expected
    Framework,
    /// Command-line tools - command dispatch patterns
    Cli,
    /// Operating systems, embedded - syscalls, interrupts
    Kernel,
    /// Game engines - ECS, tight loops
    Game,
    /// ML/AI, data science - notebooks, complex pipelines
    DataScience,
    /// iOS/Android mobile apps
    Mobile,
}

impl ProjectType {
    /// Coupling threshold multiplier (higher = more lenient)
    pub fn coupling_multiplier(&self) -> f64 {
        match self {
            ProjectType::Web => 1.0, // Strict - CRUD should have clean separation
            ProjectType::Interpreter => 2.5, // Very lenient - eval loops touch everything
            ProjectType::Compiler => 3.0, // Very lenient - HIR/MIR/AST shared everywhere
            ProjectType::Library => 1.5, // Moderate - internal coupling OK
            ProjectType::Framework => 3.0, // Very lenient - React/Vue cores couple heavily
            ProjectType::Cli => 1.3, // Slight leniency - command dispatch
            ProjectType::Kernel => 3.0, // Most lenient - syscalls, interrupts
            ProjectType::Game => 2.0, // Lenient - ECS, frame loops
            ProjectType::DataScience => 2.0, // Lenient - notebooks, pipelines
            ProjectType::Mobile => 1.5, // Moderate - MVC/MVVM patterns
        }
    }

    /// Complexity threshold multiplier
    pub fn complexity_multiplier(&self) -> f64 {
        match self {
            ProjectType::Web => 1.0,
            ProjectType::Interpreter => 1.8, // Opcodes switches are complex
            ProjectType::Compiler => 1.5,    // Parser/codegen complexity
            ProjectType::Library => 1.2,
            ProjectType::Framework => 1.5, // Core reconciler, scheduler complexity
            ProjectType::Cli => 1.1,
            ProjectType::Kernel => 2.0, // Interrupt handlers, state machines
            ProjectType::Game => 1.5,   // Frame update loops
            ProjectType::DataScience => 1.8, // Data pipelines, complex transforms
            ProjectType::Mobile => 1.3, // UI state, lifecycle complexity
        }
    }

    /// Whether to skip dead code analysis for dispatch-like patterns
    pub fn lenient_dead_code(&self) -> bool {
        matches!(
            self,
            ProjectType::Interpreter
                | ProjectType::Kernel
                | ProjectType::Game
                | ProjectType::Framework
                | ProjectType::DataScience
        )
    }

    /// Detect project type from directory structure and file contents
    pub fn detect(repo_path: &Path) -> ProjectType {
        // Score each project type and pick the highest
        let mut scores: Vec<(ProjectType, u32)> = vec![
            (
                ProjectType::Interpreter,
                score_interpreter_markers(repo_path),
            ),
            (ProjectType::Compiler, score_compiler_markers(repo_path)),
            (ProjectType::Framework, score_framework_markers(repo_path)),
            (ProjectType::Kernel, score_kernel_markers(repo_path)),
            (ProjectType::Game, score_game_markers(repo_path)),
            (
                ProjectType::DataScience,
                score_datascience_markers(repo_path),
            ),
            (ProjectType::Mobile, score_mobile_markers(repo_path)),
            (ProjectType::Cli, score_cli_markers(repo_path)),
            (ProjectType::Library, score_library_markers(repo_path)),
            (ProjectType::Web, score_web_markers(repo_path)),
        ];

        // Sort by score descending
        scores.sort_by(|a, b| b.1.cmp(&a.1));

        // If top score is 0 or very low, default to Library
        if scores[0].1 < 2 {
            return ProjectType::Library;
        }

        scores[0].0
    }
}

/// Score UI framework markers (React, Vue, Angular, Svelte, etc.)
fn score_framework_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const FRAMEWORK_DIRS: &[&str] = &[
        "reconciler",
        "scheduler",
        "renderer",
        "dom",
        "fiber",
        "packages/react",
        "packages/vue",
        "packages/angular",
    ];

    // Check for framework-specific directories
    for dir in FRAMEWORK_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 3;
        }
    }

    // Check package.json for framework name in "name" field
    let package_json = repo_path.join("package.json");
    if package_json.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json) {
            // Check if this IS a framework (not just uses one)
            if content.contains("\"name\": \"react\"")
                || content.contains("\"name\": \"vue\"")
                || content.contains("\"name\": \"angular\"")
                || content.contains("\"name\": \"svelte\"")
                || content.contains("\"name\": \"preact\"")
                || content.contains("\"name\": \"solid-js\"")
            {
                score += 10; // Strong signal
            }
        }
    }

    // Check for monorepo packages that indicate framework
    if let Ok(packages) = std::fs::read_dir(repo_path.join("packages")) {
        for entry in packages.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.contains("reconciler")
                || name_str.contains("scheduler")
                || name_str.contains("dom")
                || name_str.contains("core")
                || name_str.contains("runtime")
            {
                score += 2;
            }
        }
    }

    score
}

/// Score interpreter/VM markers
fn score_interpreter_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const INTERPRETER_DIRS: &[&str] = &[
        "vm",
        "interpreter",
        "bytecode",
        "runtime",
        "eval",
        "opcode",
        "jit",
        "gc",
        "allocator",
    ];
    const INTERPRETER_FILES: &[&str] = &[
        "vm.c",
        "vm.rs",
        "interpreter.c",
        "interpreter.rs",
        "eval.c",
        "eval.rs",
        "bytecode.c",
        "bytecode.rs",
        "opcode.h",
        "opcodes.h",
    ];

    for dir in INTERPRETER_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("pkg/{}", dir)).is_dir()
        {
            score += 3;
        }
    }
    for file in INTERPRETER_FILES {
        if repo_path.join(file).exists() || repo_path.join(format!("src/{}", file)).exists() {
            score += 2;
        }
    }
    score
}

/// Score compiler markers
fn score_compiler_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const COMPILER_DIRS: &[&str] = &[
        "parser",
        "lexer",
        "codegen",
        "ast",
        "ir",
        "optimizer",
        "frontend",
        "backend",
        "compiler",
        "HIR",
        "MIR",
        "LIR",
        "transform",
        "analysis",
    ];

    for dir in COMPILER_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("packages/{}", dir)).is_dir()
        {
            score += 2;
        }
    }

    // Check for packages/*/compiler pattern (monorepo like React)
    if let Ok(packages) = std::fs::read_dir(repo_path.join("packages")) {
        for entry in packages.filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_dir() {
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if name.contains("compiler") || name.contains("transform") {
                    score += 5; // Strong signal
                }
            }
        }
    }

    score
}

/// Score kernel/embedded markers
fn score_kernel_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const KERNEL_DIRS: &[&str] = &[
        "kernel",
        "drivers",
        "arch",
        "syscall",
        "interrupt",
        "hal",
        "bsp",
    ];
    const KERNEL_FILES: &[&str] = &[
        "Kconfig",
        "Makefile.inc",
        "linker.ld",
        "boot.S",
        "startup.s",
    ];

    for dir in KERNEL_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 4;
        }
    }
    for file in KERNEL_FILES {
        if repo_path.join(file).exists() {
            score += 5;
        }
    }
    score
}

/// Score game engine markers
fn score_game_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const GAME_DIRS: &[&str] = &[
        "engine", "ecs", "physics", "renderer", "assets", "scenes", "shaders",
    ];
    const GAME_FILES: &[&str] = &["game.rs", "game.cpp", "engine.rs", "engine.cpp"];

    for dir in GAME_DIRS {
        if repo_path.join(dir).is_dir() || repo_path.join(format!("src/{}", dir)).is_dir() {
            score += 2;
        }
    }
    for file in GAME_FILES {
        if repo_path.join(file).exists() || repo_path.join(format!("src/{}", file)).exists() {
            score += 3;
        }
    }

    // Check for game-specific dependencies
    let cargo_toml = repo_path.join("Cargo.toml");
    if cargo_toml.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_toml) {
            let game_deps = ["bevy", "ggez", "amethyst", "macroquad", "fyrox", "godot"];
            for dep in game_deps {
                if content.contains(dep) {
                    score += 5;
                }
            }
        }
    }

    score
}

/// Score CLI tool markers
fn score_cli_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    const CLI_DIRS: &[&str] = &["cli", "cmd", "commands"];

    // Check for CLI framework deps
    let cargo_toml = repo_path.join("Cargo.toml");
    if cargo_toml.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_toml) {
            if content.contains("clap") || content.contains("structopt") {
                score += 4;
            }
        }
    }

    // Check go.mod for cobra
    let go_mod = repo_path.join("go.mod");
    if go_mod.exists() {
        if let Ok(content) = std::fs::read_to_string(&go_mod) {
            if content.contains("cobra") || content.contains("urfave/cli") {
                score += 4;
            }
        }
    }

    // Check for click/argparse in Python
    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    for file_path in [requirements, pyproject] {
        if file_path.exists() {
            if let Ok(content) = std::fs::read_to_string(&file_path) {
                if content.contains("click")
                    || content.contains("typer")
                    || content.contains("argparse")
                {
                    score += 3;
                }
            }
        }
    }

    for dir in CLI_DIRS {
        if repo_path.join(dir).is_dir() || repo_path.join(format!("src/{}", dir)).is_dir() {
            score += 2;
        }
    }

    // cli.rs or cli.go is a strong signal
    if repo_path.join("src/cli.rs").exists()
        || repo_path.join("cli.go").exists()
        || repo_path.join("cmd/main.go").exists()
    {
        score += 3;
    }

    score
}

/// Score library markers
fn score_library_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    let lib_rs = repo_path.join("src/lib.rs");
    let main_rs = repo_path.join("src/main.rs");

    // Pure library: has lib.rs but no main.rs
    if lib_rs.exists() && !main_rs.exists() {
        score += 5;
    } else if lib_rs.exists() {
        score += 2; // Both lib and main = mixed
    }

    // Check Cargo.toml for [lib] section
    let cargo_toml = repo_path.join("Cargo.toml");
    if cargo_toml.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_toml) {
            if content.contains("[lib]") {
                score += 2;
            }
            if !content.contains("[[bin]]") {
                score += 1;
            }
        }
    }

    // Check for setup.py / pyproject.toml with library structure
    if (repo_path.join("setup.py").exists() || repo_path.join("pyproject.toml").exists())
        && !repo_path.join("__main__.py").exists()
    {
        score += 3;
    }

    score
}

/// Score web framework markers
fn score_web_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // Check for common web framework dependencies
    let cargo_toml = repo_path.join("Cargo.toml");
    if cargo_toml.exists() {
        if let Ok(content) = std::fs::read_to_string(&cargo_toml) {
            let web_deps = ["actix-web", "axum", "rocket", "warp", "tide"];
            for dep in web_deps {
                if content.contains(dep) {
                    score += 4;
                }
            }
        }
    }

    let package_json = repo_path.join("package.json");
    if package_json.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json) {
            // Backend frameworks
            let backend_deps = ["express", "fastify", "koa", "hapi", "nest"];
            for dep in backend_deps {
                if content.contains(&format!("\"{}\"", dep)) {
                    score += 4;
                }
            }
            // Frontend (but using, not being)
            let frontend_deps = ["next", "nuxt", "gatsby"];
            for dep in frontend_deps {
                if content.contains(&format!("\"{}\"", dep)) {
                    score += 3;
                }
            }
        }
    }

    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    for file_path in [requirements, pyproject] {
        if file_path.exists() {
            if let Ok(content) = std::fs::read_to_string(&file_path) {
                let web_deps = [
                    "flask",
                    "django",
                    "fastapi",
                    "starlette",
                    "tornado",
                    "sanic",
                ];
                for dep in web_deps {
                    if content.contains(dep) {
                        score += 4;
                    }
                }
            }
        }
    }

    // Check go.mod for Go web frameworks
    let go_mod = repo_path.join("go.mod");
    if go_mod.exists() {
        if let Ok(content) = std::fs::read_to_string(&go_mod) {
            let go_web = ["gin-gonic", "echo", "fiber", "chi", "gorilla/mux"];
            for dep in go_web {
                if content.contains(dep) {
                    score += 4;
                }
            }
        }
    }

    // Check for routes/controllers/handlers directories
    const WEB_DIRS: &[&str] = &[
        "routes",
        "controllers",
        "handlers",
        "views",
        "api",
        "endpoints",
    ];
    for dir in WEB_DIRS {
        if repo_path.join(dir).is_dir()
            || repo_path.join(format!("src/{}", dir)).is_dir()
            || repo_path.join(format!("app/{}", dir)).is_dir()
        {
            score += 2;
        }
    }

    score
}

/// Score data science / ML markers
fn score_datascience_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // Check for Jupyter notebooks
    if let Ok(entries) = std::fs::read_dir(repo_path) {
        for entry in entries.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            if name.to_string_lossy().ends_with(".ipynb") {
                score += 3;
            }
        }
    }
    if repo_path.join("notebooks").is_dir() {
        score += 4;
    }

    // Check for ML/DS dependencies
    let requirements = repo_path.join("requirements.txt");
    let pyproject = repo_path.join("pyproject.toml");
    for file_path in [requirements, pyproject] {
        if file_path.exists() {
            if let Ok(content) = std::fs::read_to_string(&file_path) {
                let ml_deps = [
                    "numpy",
                    "pandas",
                    "scikit-learn",
                    "sklearn",
                    "tensorflow",
                    "torch",
                    "pytorch",
                    "keras",
                    "xgboost",
                    "lightgbm",
                    "transformers",
                    "matplotlib",
                    "seaborn",
                    "plotly",
                    "jupyter",
                    "scipy",
                ];
                for dep in ml_deps {
                    if content.contains(dep) {
                        score += 2;
                    }
                }
            }
        }
    }

    // Check for data/models directories
    const DS_DIRS: &[&str] = &[
        "data",
        "models",
        "training",
        "inference",
        "experiments",
        "notebooks",
    ];
    for dir in DS_DIRS {
        if repo_path.join(dir).is_dir() {
            score += 1;
        }
    }

    score
}

/// Score mobile app markers
fn score_mobile_markers(repo_path: &Path) -> u32 {
    let mut score = 0u32;

    // iOS markers
    if repo_path.join("Info.plist").exists() || repo_path.join("AppDelegate.swift").exists() {
        score += 5;
    }
    if repo_path.join("Podfile").exists() || repo_path.join("Package.swift").exists() {
        score += 3;
    }
    let xcodeproj = repo_path.read_dir().ok().and_then(|mut d| {
        d.find(|e| {
            e.as_ref()
                .ok()
                .map(|e| {
                    e.path()
                        .extension()
                        .map(|x| x == "xcodeproj")
                        .unwrap_or(false)
                })
                .unwrap_or(false)
        })
    });
    if xcodeproj.is_some() {
        score += 5;
    }

    // Android markers
    if repo_path.join("AndroidManifest.xml").exists()
        || repo_path.join("app/src/main/AndroidManifest.xml").exists()
    {
        score += 5;
    }
    if repo_path.join("build.gradle").exists() || repo_path.join("build.gradle.kts").exists() {
        if let Ok(content) = std::fs::read_to_string(repo_path.join("build.gradle")) {
            if content.contains("android") {
                score += 4;
            }
        }
    }

    // React Native / Flutter
    let package_json = repo_path.join("package.json");
    if package_json.exists() {
        if let Ok(content) = std::fs::read_to_string(&package_json) {
            if content.contains("react-native") {
                score += 5;
            }
        }
    }
    if repo_path.join("pubspec.yaml").exists() {
        score += 5; // Flutter
    }

    score
}

/// Project-level configuration loaded from repotoire.toml or similar
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ProjectConfig {
    /// Project type (auto-detected if not specified)
    #[serde(default)]
    pub project_type: Option<ProjectType>,

    /// Per-detector configuration overrides
    #[serde(default)]
    pub detectors: HashMap<String, DetectorConfigOverride>,

    /// Scoring configuration
    #[serde(default)]
    pub scoring: ScoringConfig,

    /// Path exclusion patterns
    #[serde(default)]
    pub exclude: ExcludeConfig,

    /// Default CLI flags
    #[serde(default)]
    pub defaults: CliDefaults,

    /// Cached auto-detected project type (not serialized)
    #[serde(skip)]
    detected_type: Option<ProjectType>,
}

/// Configuration override for a specific detector
#[derive(Debug, Clone, Deserialize, Default)]
pub struct DetectorConfigOverride {
    /// Whether the detector is enabled (default: true)
    #[serde(default)]
    pub enabled: Option<bool>,

    /// Override the default severity (critical, high, medium, low, info)
    #[serde(default)]
    pub severity: Option<String>,

    /// Detector-specific threshold overrides
    /// Keys depend on the detector (e.g., method_count, loc, max_params)
    #[serde(default)]
    pub thresholds: HashMap<String, ThresholdValue>,
}

/// A threshold value can be an integer, float, or boolean
#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum ThresholdValue {
    Integer(i64),
    Float(f64),
    Boolean(bool),
    String(String),
}

impl ThresholdValue {
    /// Get as i64 (returns None for non-integer types)
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            ThresholdValue::Integer(v) => Some(*v),
            ThresholdValue::Float(v) => Some(*v as i64),
            _ => None,
        }
    }

    /// Get as f64
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            ThresholdValue::Integer(v) => Some(*v as f64),
            ThresholdValue::Float(v) => Some(*v),
            _ => None,
        }
    }

    /// Get as bool
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            ThresholdValue::Boolean(v) => Some(*v),
            _ => None,
        }
    }

    /// Get as string
    pub fn as_str(&self) -> Option<&str> {
        match self {
            ThresholdValue::String(v) => Some(v.as_str()),
            _ => None,
        }
    }
}

/// Scoring configuration for health score calculation
#[derive(Debug, Clone, Deserialize)]
pub struct ScoringConfig {
    /// Multiplier for security-related findings (default: 3.0)
    #[serde(default = "default_security_multiplier")]
    pub security_multiplier: f64,

    /// Weights for each pillar (must sum to 1.0)
    #[serde(default)]
    pub pillar_weights: PillarWeights,
}

impl Default for ScoringConfig {
    fn default() -> Self {
        Self {
            security_multiplier: default_security_multiplier(),
            pillar_weights: PillarWeights::default(),
        }
    }
}

fn default_security_multiplier() -> f64 {
    3.0
}

/// Weights for the three scoring pillars
#[derive(Debug, Clone, Deserialize)]
pub struct PillarWeights {
    /// Weight for structure score (default: 0.4)
    #[serde(default = "default_structure_weight")]
    pub structure: f64,

    /// Weight for quality score (default: 0.3)
    #[serde(default = "default_quality_weight")]
    pub quality: f64,

    /// Weight for architecture score (default: 0.3)
    #[serde(default = "default_architecture_weight")]
    pub architecture: f64,
}

impl Default for PillarWeights {
    fn default() -> Self {
        Self {
            structure: default_structure_weight(),
            quality: default_quality_weight(),
            architecture: default_architecture_weight(),
        }
    }
}

fn default_structure_weight() -> f64 {
    0.4
}
fn default_quality_weight() -> f64 {
    0.3
}
fn default_architecture_weight() -> f64 {
    0.3
}

impl PillarWeights {
    /// Validate that weights sum to 1.0 (with tolerance)
    pub fn is_valid(&self) -> bool {
        let sum = self.structure + self.quality + self.architecture;
        (sum - 1.0).abs() < 0.001
    }

    /// Normalize weights to sum to 1.0
    pub fn normalize(&mut self) {
        let sum = self.structure + self.quality + self.architecture;
        if sum > 0.0 {
            self.structure /= sum;
            self.quality /= sum;
            self.architecture /= sum;
        }
    }
}

/// Path exclusion configuration
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ExcludeConfig {
    /// Paths/patterns to exclude from analysis
    #[serde(default)]
    pub paths: Vec<String>,
}

/// Default CLI flags that can be set in project config
#[derive(Debug, Clone, Deserialize, Default)]
pub struct CliDefaults {
    /// Default output format (text, json, sarif, html, markdown)
    #[serde(default)]
    pub format: Option<String>,

    /// Default minimum severity filter
    #[serde(default)]
    pub severity: Option<String>,

    /// Default number of workers
    #[serde(default)]
    pub workers: Option<usize>,

    /// Default findings per page
    #[serde(default)]
    pub per_page: Option<usize>,

    /// Skip detectors by default
    #[serde(default)]
    pub skip_detectors: Vec<String>,

    /// Enable thorough mode by default
    #[serde(default)]
    pub thorough: Option<bool>,

    /// Skip git enrichment by default
    #[serde(default)]
    pub no_git: Option<bool>,

    /// Disable emoji by default
    #[serde(default)]
    pub no_emoji: Option<bool>,

    /// Fail-on severity threshold for CI
    #[serde(default)]
    pub fail_on: Option<String>,
}

/// Load project configuration from the repository root.
///
/// Searches for configuration files in this order:
/// 1. `repotoire.toml`
/// 2. `.repotoirerc.json`
/// 3. `.repotoire.yaml` / `.repotoire.yml`
///
/// Returns default configuration if no config file is found.
pub fn load_project_config(repo_path: &Path) -> ProjectConfig {
    // Try TOML first (preferred format)
    let toml_path = repo_path.join("repotoire.toml");
    if toml_path.exists() {
        match load_toml_config(&toml_path) {
            Ok(config) => {
                debug!("Loaded project config from {}", toml_path.display());
                return config;
            }
            Err(e) => {
                warn!("Failed to load {}: {}", toml_path.display(), e);
            }
        }
    }

    // Try JSON
    let json_path = repo_path.join(".repotoirerc.json");
    if json_path.exists() {
        match load_json_config(&json_path) {
            Ok(config) => {
                debug!("Loaded project config from {}", json_path.display());
                return config;
            }
            Err(e) => {
                warn!("Failed to load {}: {}", json_path.display(), e);
            }
        }
    }

    // Try YAML (.yaml or .yml)
    for yaml_name in &[".repotoire.yaml", ".repotoire.yml"] {
        let yaml_path = repo_path.join(yaml_name);
        if yaml_path.exists() {
            match load_yaml_config(&yaml_path) {
                Ok(config) => {
                    debug!("Loaded project config from {}", yaml_path.display());
                    return config;
                }
                Err(e) => {
                    warn!("Failed to load {}: {}", yaml_path.display(), e);
                }
            }
        }
    }

    // No config found, return defaults
    debug!("No project config found, using defaults");
    ProjectConfig::default()
}

/// Load configuration from a TOML file
fn load_toml_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;
    let config: ProjectConfig = toml::from_str(&content)?;
    Ok(config)
}

/// Load configuration from a JSON file
fn load_json_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;
    let config: ProjectConfig = serde_json::from_str(&content)?;
    Ok(config)
}

/// Load configuration from a YAML file
fn load_yaml_config(path: &Path) -> anyhow::Result<ProjectConfig> {
    let content = std::fs::read_to_string(path)?;

    // Try JSON first (YAML is a superset of JSON, so pure-JSON YAML files work)
    if let Ok(config) = serde_json::from_str::<ProjectConfig>(&content) {
        return Ok(config);
    }

    // For actual YAML syntax, give a clear error (#34)
    anyhow::bail!(
        "YAML config files with non-JSON syntax are not yet supported.\n\
         Please convert {} to TOML format (repotoire.toml) or use JSON syntax.\n\
         See: https://repotoire.com/docs/cli/config",
        path.display()
    )
}

impl ProjectConfig {
    /// Get the effective project type (explicit config > auto-detected > default)
    pub fn get_project_type(&self, repo_path: &Path) -> ProjectType {
        if let Some(explicit) = self.project_type {
            debug!("Using explicit project type: {:?}", explicit);
            return explicit;
        }
        // Auto-detect based on repo structure
        let detected = ProjectType::detect(repo_path);
        debug!(
            "Auto-detected project type: {:?} (coupling multiplier: {})",
            detected,
            detected.coupling_multiplier()
        );
        detected
    }

    /// Get coupling threshold multiplier based on project type
    pub fn coupling_multiplier(&self, repo_path: &Path) -> f64 {
        self.get_project_type(repo_path).coupling_multiplier()
    }

    /// Get complexity threshold multiplier based on project type
    pub fn complexity_multiplier(&self, repo_path: &Path) -> f64 {
        self.get_project_type(repo_path).complexity_multiplier()
    }

    /// Check if a detector is enabled (defaults to true if not specified)
    pub fn is_detector_enabled(&self, name: &str) -> bool {
        // Normalize detector name for lookup (support both kebab-case and snake_case)
        let normalized = normalize_detector_name(name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(name))
            .and_then(|c| c.enabled)
            .unwrap_or(true)
    }

    /// Get severity override for a detector (if any)
    pub fn get_severity_override(&self, name: &str) -> Option<&str> {
        let normalized = normalize_detector_name(name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(name))
            .and_then(|c| c.severity.as_deref())
    }

    /// Get threshold value for a detector
    pub fn get_threshold(
        &self,
        detector_name: &str,
        threshold_name: &str,
    ) -> Option<&ThresholdValue> {
        let normalized = normalize_detector_name(detector_name);

        self.detectors
            .get(&normalized)
            .or_else(|| self.detectors.get(detector_name))
            .and_then(|c| c.thresholds.get(threshold_name))
    }

    /// Get threshold as i64
    pub fn get_threshold_i64(&self, detector_name: &str, threshold_name: &str) -> Option<i64> {
        self.get_threshold(detector_name, threshold_name)
            .and_then(|v| v.as_i64())
    }

    /// Get threshold as f64
    pub fn get_threshold_f64(&self, detector_name: &str, threshold_name: &str) -> Option<f64> {
        self.get_threshold(detector_name, threshold_name)
            .and_then(|v| v.as_f64())
    }

    /// Check if a path should be excluded
    pub fn should_exclude(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();

        for pattern in &self.exclude.paths {
            // Simple glob matching (supports * and **)
            if glob_match(pattern, &path_str) {
                return true;
            }
        }

        false
    }

    /// Get all detector names that should be skipped
    pub fn get_disabled_detectors(&self) -> Vec<String> {
        let mut disabled = Vec::new();

        // From explicit enabled: false
        for (name, config) in &self.detectors {
            if config.enabled == Some(false) {
                disabled.push(name.clone());
            }
        }

        // From defaults.skip_detectors
        disabled.extend(self.defaults.skip_detectors.clone());

        disabled
    }
}

/// Normalize detector name for config lookup
/// Converts various formats to kebab-case for matching
pub fn normalize_detector_name(name: &str) -> String {
    // GodClassDetector -> god-class
    // SQLInjectionDetector -> sql-injection
    // god_class -> god-class
    // god-class -> god-class

    let mut result = String::new();
    let chars: Vec<char> = name.chars().collect();

    for (i, c) in chars.iter().enumerate() {
        if c.is_uppercase() {
            // Add hyphen if:
            // 1. Not first char AND previous is lowercase (e.g., godClass -> god-class)
            // 2. Not first char AND previous is uppercase AND next is lowercase (e.g., SQLInjection -> sql-injection)
            let prev_is_lower = i > 0 && chars[i - 1].is_lowercase();
            let is_acronym_end = i > 0
                && chars[i - 1].is_uppercase()
                && i + 1 < chars.len()
                && chars[i + 1].is_lowercase();

            if prev_is_lower || is_acronym_end {
                result.push('-');
            }
            result.push(c.to_lowercase().next().unwrap());
        } else if *c == '_' {
            result.push('-');
        } else {
            result.push(*c);
        }
    }

    // Remove common suffixes
    result.trim_end_matches("-detector").to_string()
}

/// Simple glob pattern matching
fn glob_match(pattern: &str, path: &str) -> bool {
    // Handle **/X/** patterns (match if path contains X as a directory)
    if pattern.starts_with("**/") && pattern.ends_with("/**") {
        let middle = pattern.trim_start_matches("**/").trim_end_matches("/**");
        // Check if path contains /middle/ or starts with middle/
        return path.contains(&format!("/{}/", middle))
            || path.starts_with(&format!("{}/", middle));
    }

    // Handle ** (match any path segments)
    if pattern.contains("**") {
        let parts: Vec<&str> = pattern.split("**").collect();
        if parts.len() == 2 {
            let prefix = parts[0].trim_end_matches('/');
            let suffix = parts[1].trim_start_matches('/');

            // Check prefix
            if !prefix.is_empty() && !path.starts_with(prefix) {
                return false;
            }

            // Check suffix
            if !suffix.is_empty() && !path.ends_with(suffix) {
                return false;
            }

            return true;
        }
    }

    // Handle single * (match within segment)
    if pattern.contains('*') {
        let parts: Vec<&str> = pattern.split('*').collect();
        if parts.len() == 2 {
            let prefix = parts[0];
            let suffix = parts[1];
            return path.starts_with(prefix) && path.ends_with(suffix);
        }
    }

    // Exact match or prefix match (for directories)
    path.starts_with(pattern) || path == pattern
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_detector_name() {
        assert_eq!(normalize_detector_name("GodClassDetector"), "god-class");
        assert_eq!(normalize_detector_name("god_class"), "god-class");
        assert_eq!(normalize_detector_name("god-class"), "god-class");
        // Consecutive uppercase stays together: SQL -> sql
        assert_eq!(
            normalize_detector_name("SQLInjectionDetector"),
            "sql-injection"
        );
        assert_eq!(normalize_detector_name("NPlusOneDetector"), "n-plus-one");
    }

    #[test]
    fn test_glob_match() {
        // ** patterns
        assert!(glob_match("**/vendor/**", "src/vendor/lib/foo.py"));
        assert!(glob_match("generated/", "generated/model.py"));
        assert!(glob_match("*.test.ts", "foo.test.ts"));

        // Prefix patterns
        assert!(glob_match("vendor/", "vendor/lib/foo.py"));
        assert!(!glob_match("vendor/", "src/vendor/foo.py"));
    }

    #[test]
    fn test_pillar_weights_validation() {
        let valid = PillarWeights {
            structure: 0.4,
            quality: 0.3,
            architecture: 0.3,
        };
        assert!(valid.is_valid());

        let invalid = PillarWeights {
            structure: 0.5,
            quality: 0.5,
            architecture: 0.5,
        };
        assert!(!invalid.is_valid());
    }

    #[test]
    fn test_pillar_weights_normalize() {
        let mut weights = PillarWeights {
            structure: 2.0,
            quality: 1.0,
            architecture: 1.0,
        };
        weights.normalize();
        assert!((weights.structure - 0.5).abs() < 0.001);
        assert!((weights.quality - 0.25).abs() < 0.001);
        assert!((weights.architecture - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_threshold_value() {
        let int_val = ThresholdValue::Integer(42);
        assert_eq!(int_val.as_i64(), Some(42));
        assert_eq!(int_val.as_f64(), Some(42.0));
        assert_eq!(int_val.as_bool(), None);

        let float_val = ThresholdValue::Float(2.5);
        assert_eq!(float_val.as_i64(), Some(2));
        assert_eq!(float_val.as_f64(), Some(2.5));

        let bool_val = ThresholdValue::Boolean(true);
        assert_eq!(bool_val.as_bool(), Some(true));
        assert_eq!(bool_val.as_i64(), None);
    }

    #[test]
    fn test_default_config() {
        let config = ProjectConfig::default();

        // All detectors enabled by default
        assert!(config.is_detector_enabled("god-class"));
        assert!(config.is_detector_enabled("unknown-detector"));

        // No severity overrides
        assert!(config.get_severity_override("god-class").is_none());

        // Default scoring
        assert!((config.scoring.security_multiplier - 3.0).abs() < 0.001);
        assert!(config.scoring.pillar_weights.is_valid());
    }

    #[test]
    fn test_parse_toml_config() {
        let toml_content = r#"
[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600 }

[detectors.sql-injection]
severity = "high"
enabled = false

[scoring]
security_multiplier = 5.0

[scoring.pillar_weights]
structure = 0.3
quality = 0.4
architecture = 0.3

[exclude]
paths = ["generated/", "vendor/"]

[defaults]
format = "json"
workers = 4
skip_detectors = ["debug-code"]
"#;

        let config: ProjectConfig = toml::from_str(toml_content).unwrap();

        // Check detectors
        assert!(config.is_detector_enabled("god-class"));
        assert!(!config.is_detector_enabled("sql-injection"));
        assert_eq!(config.get_severity_override("sql-injection"), Some("high"));
        assert_eq!(
            config.get_threshold_i64("god-class", "method_count"),
            Some(30)
        );
        assert_eq!(config.get_threshold_i64("god-class", "loc"), Some(600));

        // Check scoring
        assert!((config.scoring.security_multiplier - 5.0).abs() < 0.001);
        assert!((config.scoring.pillar_weights.structure - 0.3).abs() < 0.001);

        // Check exclude
        assert_eq!(config.exclude.paths.len(), 2);
        assert!(config.should_exclude(Path::new("generated/foo.py")));

        // Check defaults
        assert_eq!(config.defaults.format, Some("json".to_string()));
        assert_eq!(config.defaults.workers, Some(4));
        assert!(config
            .defaults
            .skip_detectors
            .contains(&"debug-code".to_string()));
    }
}
