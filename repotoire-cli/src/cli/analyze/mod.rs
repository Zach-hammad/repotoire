//! Analyze command implementation
//!
//! This command performs a full codebase analysis:
//! 1. Initialize petgraph graph database
//! 2. Walk repository and parse all supported files
//! 3. Build the code graph (nodes + edges)
//! 4. Enrich with git history (authors, churn, temporal data)
//! 5. Run all registered detectors
//! 6. Calculate health score and grade
//! 7. Output results (text, json, sarif)

mod parse;
mod graph;
mod detect;
mod output;

use parse::{parse_files, parse_files_lite, parse_files_chunked, ParsePhaseResult};
use graph::{build_graph, build_graph_chunked, parse_and_build_streaming};
use detect::{
    start_git_enrichment, finish_git_enrichment, run_detectors, run_detectors_streaming,
    apply_voting, update_incremental_cache, apply_detector_overrides,
};
use output::{
    filter_findings, paginate_findings, format_and_output, check_fail_threshold,
    load_cached_findings, output_cached_results,
};

use crate::config::{load_project_config, ProjectConfig};
use crate::detectors::IncrementalCache;
use crate::graph::GraphStore;
use crate::models::{Finding, FindingsSummary, HealthReport, Severity};

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

/// Supported file extensions for analysis
const SUPPORTED_EXTENSIONS: &[&str] = &[
    "py", "pyi", // Python
    "ts", "tsx", // TypeScript
    "js", "jsx", "mjs",  // JavaScript
    "rs",   // Rust
    "go",   // Go
    "java", // Java
    "c", "h", // C
    "cpp", "hpp", "cc", // C++
    "cs", // C#
    "kt", "kts",   // Kotlin
    "rb",    // Ruby
    "php",   // PHP
    "swift", // Swift
];

/// Quick file list collection (no git, no incremental checking) for cache validation
// TODO(refactor): This module is 3000+ lines and should be split into:
// - cli/analyze/setup.rs (environment, config, validation)
// - cli/analyze/graph.rs (graph building, call edges, import edges)
// - cli/analyze/detect.rs (detection phases, streaming, caching)
// - cli/analyze/output.rs (formatting, reporting, pagination)
// - cli/analyze/cache.rs (unified cache interface)
// See: https://github.com/Zach-hammad/repotoire/issues/TBD

fn collect_file_list(repo_path: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    
    let walker = WalkBuilder::new(repo_path)
        .hidden(true)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .build();
    
    for entry in walker.filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.is_file() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if SUPPORTED_EXTENSIONS.contains(&ext) {
                    files.push(path.to_path_buf());
                }
            }
        }
    }
    
    Ok(files)
}

/// Result of file collection phase
struct FileCollectionResult {
    all_files: Vec<PathBuf>,
    files_to_parse: Vec<PathBuf>,
    cached_findings: Vec<Finding>,
}

/// Configuration applied from CLI and project config
struct AnalysisConfig {
    no_emoji: bool,
    thorough: bool,
    no_git: bool,
    workers: usize,
    #[allow(dead_code)] // Stored for potential future use
    per_page: usize,
    fail_on: Option<String>,
    is_incremental_mode: bool,
    skip_graph: bool,
    max_files: usize,
}

/// Result of environment setup phase
struct EnvironmentSetup {
    repo_path: PathBuf,
    project_config: ProjectConfig,
    config: AnalysisConfig,
    repotoire_dir: PathBuf,
    incremental_cache: IncrementalCache,
    quiet_mode: bool,
}

/// Result of score calculation phase
struct ScoreResult {
    overall_score: f64,
    structure_score: f64,
    quality_score: f64,
    architecture_score: f64,
    grade: String,
    breakdown: crate::scoring::ScoreBreakdown,
}

/// Get the cache directory for a repository
pub fn get_cache_path(repo_path: &Path) -> std::path::PathBuf {
    repo_path.join(".repotoire")
}

/// Run the analyze command
pub fn run(
    path: &Path,
    format: &str,
    output_path: Option<&Path>,
    severity: Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: Vec<String>,
    thorough: bool,
    no_git: bool,
    workers: usize,
    fail_on: Option<String>,
    no_emoji: bool,
    incremental: bool,
    since: Option<String>,
    explain_score: bool,
    verify: bool,
    skip_graph: bool,
    max_files: usize,
    compact: bool,
) -> Result<()> {
    // TODO: When compact=true, use CompactGraphStore instead of GraphStore
    // This will reduce memory by 60-70% via string interning
    if compact {
        tracing::info!("Compact mode enabled (string interning)");
    }
    let start_time = Instant::now();

    // Phase 1: Validate repository and setup environment
    let mut env = setup_environment(
        path,
        format,
        no_emoji,
        thorough,
        no_git,
        workers,
        per_page,
        fail_on,
        incremental,
        since.is_some(),
        skip_graph,
        max_files,
    )?;

    // Fast path: Check if we have complete cached results (findings + scores)
    // This avoids graph initialization entirely when nothing has changed
    {
        let cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
        let all_files = collect_file_list(&env.repo_path)?;
        
        if cache.has_complete_cache(&all_files) {
            // Load post-processed findings from last_findings.json (includes voting,
            // FP filtering, compound escalation, security downgrading).
            // Falls back to raw incremental cache if last_findings.json doesn't exist.
            let findings = load_cached_findings(&env.repotoire_dir)
                .unwrap_or_else(|| cache.get_all_cached_graph_findings());
            let cached_score = cache.get_cached_score().unwrap();
            
            if !env.quiet_mode {
                let icon = if env.config.no_emoji { "" } else { "⚡ " };
                println!(
                    "\n{}Using fully cached results (no changes detected)\n",
                    style(icon).bold()
                );
            }
            
            // Output cached results (with same filters as normal path)
            output_cached_results(
                env.config.no_emoji,
                env.quiet_mode,
                &env.config.fail_on,
                findings,
                cached_score,
                format,
                output_path,
                start_time,
                explain_score,
                &severity,
                top,
                page,
                per_page,
                &skip_detector,
                &env.repotoire_dir,
            )?;
            
            return Ok(());
        }
    }

    // Phase 2: Initialize graph and collect files
    let (graph, file_result, parse_result) = initialize_graph(&env, &since, &MultiProgress::new())?;

    if file_result.all_files.is_empty() {
        if !env.quiet_mode {
            let warn_icon = if env.config.no_emoji { "" } else { "⚠️  " };
            println!(
                "\n{}No source files found to analyze.",
                style(warn_icon).yellow()
            );
        }
        return Ok(());
    }

    // Phase 3: Run detectors
    let multi = MultiProgress::new();
    let spinner_style = create_spinner_style();

    let mut findings = execute_detection_phase(
        &env,
        &graph,
        &file_result,
        &skip_detector,
        &multi,
        &spinner_style,
    )?;

    // Phase 4: Post-process findings
    update_incremental_cache(
        env.config.is_incremental_mode,
        &mut env.incremental_cache,
        &file_result.files_to_parse,
        &findings,
    );
    apply_detector_overrides(&mut findings, &env.project_config);
    
    // Filter findings to only include files in the analyzed set (respects --max-files)
    if env.config.max_files > 0 {
        let allowed_files: std::collections::HashSet<_> = file_result.all_files.iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect();
        findings.retain(|f| {
            f.affected_files.is_empty() || f.affected_files.iter().any(|p| {
                let ps = p.to_string_lossy().to_string();
                allowed_files.contains(&ps) || allowed_files.iter().any(|a| ps.ends_with(a.trim_start_matches("./")) || a.ends_with(ps.trim_start_matches("./")))
            })
        });
    }
    
    // Phase 4.5: Escalate compound smells (multiple issues in same location)
    crate::scoring::escalate_compound_smells(&mut findings);
    
    // Phase 4.55: Downgrade security findings in non-production paths
    // Scripts, tests, fixtures, examples shouldn't have critical security findings
    {
        use crate::detectors::content_classifier::is_non_production_path;
        
        let security_detectors: &[&str] = &[
            "CommandInjectionDetector",
            "SQLInjectionDetector", 
            "XssDetector",
            "SsrfDetector",
            "PathTraversalDetector",
            "LogInjectionDetector",
            "EvalDetector",
            "InsecureRandomDetector",
            "HardcodedCredentialsDetector",
            "CleartextCredentialsDetector",
        ];
        
        for finding in findings.iter_mut() {
            // Check if any affected file is in a non-production path
            let is_non_prod = finding.affected_files.iter().any(|p| {
                is_non_production_path(&p.to_string_lossy())
            });
            
            if is_non_prod && security_detectors.contains(&finding.detector.as_str()) {
                // Downgrade critical/high to medium in non-prod paths
                if finding.severity == Severity::Critical || finding.severity == Severity::High {
                    finding.severity = Severity::Medium;
                    finding.description = format!("[Non-production path] {}", finding.description);
                }
            }
        }
    }
    
    // Phase 4.6: FP filtering with category-aware thresholds
    // Always runs - uses different thresholds for different detector types:
    // - Security: conservative (0.35) - don't miss real vulnerabilities
    // - Code Quality: aggressive (0.55) - filter noisy complexity warnings
    // - ML/AI: moderate (0.45) - domain-specific accuracy
    {
        use crate::classifier::{
            FeatureExtractor, 
            model::HeuristicClassifier,
            CategoryThresholds,
            DetectorCategory,
        };
        
        let extractor = FeatureExtractor::new();
        let classifier = HeuristicClassifier;
        let thresholds = CategoryThresholds::default();
        
        let before_count = findings.len();
        let mut filtered_by_category: std::collections::HashMap<DetectorCategory, usize> = 
            std::collections::HashMap::new();
        
        findings.retain(|f| {
                let features = extractor.extract(f);
                let tp_prob = classifier.score(&features);
                let category = DetectorCategory::from_detector(&f.detector);
                let config = thresholds.get_category(category);
                
                // Keep if TP probability meets category-specific threshold
                if tp_prob >= config.filter_threshold {
                    true
                } else {
                    *filtered_by_category.entry(category).or_insert(0) += 1;
                    false
                }
            });
        
        let total_filtered = before_count - findings.len();
        if total_filtered > 0 {
            tracing::info!(
                "FP classifier filtered {} findings (Security: {}, Quality: {}, ML: {}, Perf: {}, Other: {})",
                total_filtered,
                filtered_by_category.get(&DetectorCategory::Security).unwrap_or(&0),
                filtered_by_category.get(&DetectorCategory::CodeQuality).unwrap_or(&0),
                filtered_by_category.get(&DetectorCategory::MachineLearning).unwrap_or(&0),
                filtered_by_category.get(&DetectorCategory::Performance).unwrap_or(&0),
                filtered_by_category.get(&DetectorCategory::Other).unwrap_or(&0),
            );
        }
    }
    
    // Phase 4.7: LLM verification (if --verify flag) - more expensive, optional
    if verify {
        // TODO: Wire up LLM verification for remaining HIGH+ findings
        // This uses Ollama/Claude to double-check ambiguous cases
        tracing::debug!("LLM verification requested but not yet wired up");
    }

    // Phase 5: Calculate scores and build report
    let score_result = calculate_scores(&graph, &env.project_config, &findings);

    let report = build_health_report(
        &score_result,
        &mut findings,
        &severity,
        top,
        page,
        per_page,
        file_result.all_files.len(),
        parse_result.total_functions,
        parse_result.total_classes,
    );
    
    // Cache scores for fast path on next run
    {
        let mut cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
        cache.cache_score(
            score_result.overall_score,
            &score_result.grade,
            file_result.all_files.len(),
            parse_result.total_functions,
            parse_result.total_classes,
        );
        let _ = cache.save_cache();
    }

    // Phase 6: Generate output
    generate_reports(
        &report,
        &findings,
        format,
        output_path,
        &env.repotoire_dir,
        report.1,
        env.config.no_emoji,
        explain_score,
        &score_result,
        &graph,
        &env.project_config,
    )?;

    // Cache findings for feedback command
    let cache_path = get_cache_path(path);
    if let Err(e) = std::fs::create_dir_all(&cache_path) {
        tracing::warn!("Failed to create cache directory {}: {}", cache_path.display(), e);
    }
    let findings_cache = cache_path.join("findings.json");
    if let Ok(json) = serde_json::to_string(&findings) {
        if let Err(e) = std::fs::write(&findings_cache, &json) {
            tracing::warn!("Failed to write findings cache {}: {}", findings_cache.display(), e);
        }
    }

    // Final summary
    print_final_summary(env.quiet_mode, env.config.no_emoji, start_time);

    // CI/CD threshold check
    check_fail_threshold(&env.config.fail_on, &report.0)?;

    Ok(())
}

/// Phase 1: Validate repository path and setup analysis environment
fn setup_environment(
    path: &Path,
    format: &str,
    no_emoji: bool,
    thorough: bool,
    no_git: bool,
    workers: usize,
    per_page: usize,
    fail_on: Option<String>,
    incremental: bool,
    has_since: bool,
    skip_graph: bool,
    max_files: usize,
) -> Result<EnvironmentSetup> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Repository path does not exist: {}", path.display()))?;
    if !repo_path.is_dir() {
        anyhow::bail!("Path is not a directory: {}", repo_path.display());
    }

    let project_config = load_project_config(&repo_path);
    let config = apply_config_defaults(
        no_emoji,
        thorough,
        no_git,
        workers,
        per_page,
        fail_on,
        incremental,
        has_since,
        skip_graph,
        max_files,
        &project_config,
    );

    let quiet_mode = format == "json" || format == "sarif";
    let detected_type = project_config.get_project_type(&repo_path);
    print_header(&repo_path, config.no_emoji, format, &detected_type);

    let repotoire_dir = crate::cache::ensure_cache_dir(&repo_path)
        .with_context(|| "Failed to create cache directory")?;
    let incremental_cache = IncrementalCache::new(&repotoire_dir.join("incremental"));
    
    // Auto-enable incremental mode if warm cache exists
    let has_warm_cache = incremental_cache.has_cache();
    let auto_incremental = has_warm_cache && !config.is_incremental_mode;
    let config = if auto_incremental {
        if !quiet_mode {
            println!("{}Using cached analysis (auto-incremental)\n", 
                if config.no_emoji { "" } else { "⚡ " });
        }
        AnalysisConfig {
            is_incremental_mode: true,
            ..config
        }
    } else {
        config
    };

    Ok(EnvironmentSetup {
        repo_path,
        project_config,
        config,
        repotoire_dir,
        incremental_cache,
        quiet_mode,
    })
}

/// Phase 2: Initialize graph database, collect files, and parse
fn initialize_graph(
    env: &EnvironmentSetup,
    since: &Option<String>,
    multi: &MultiProgress,
) -> Result<(Arc<GraphStore>, FileCollectionResult, ParsePhaseResult)> {
    let spinner_style = create_spinner_style();
    let bar_style = create_bar_style();

    // Collect files - need mutable cache temporarily
    let mut cache_clone = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
    let mut file_result = collect_files_for_analysis(
        &env.repo_path,
        since,
        env.config.is_incremental_mode,
        &mut cache_clone,
        multi,
        &spinner_style,
    )?;

    // Apply max_files limit if set (for memory-constrained analysis)
    let max_files = env.config.max_files;
    if max_files > 0 && file_result.all_files.len() > max_files {
        if !env.quiet_mode {
            let warn_icon = if env.config.no_emoji { "" } else { "⚠️  " };
            println!(
                "{}Limiting analysis to {} files (out of {} total) to reduce memory usage",
                style(warn_icon).yellow(),
                style(max_files).cyan(),
                style(file_result.all_files.len()).dim()
            );
        }
        file_result.all_files.truncate(max_files);
        // Re-filter files_to_parse and cached_findings to only include files in the truncated list
        let all_set: std::collections::HashSet<_> = file_result.all_files.iter().collect();
        file_result.files_to_parse.retain(|f| all_set.contains(f));
        if file_result.files_to_parse.len() > max_files {
            file_result.files_to_parse.truncate(max_files);
        }
        // Filter cached findings to match truncated file set
        file_result.cached_findings.retain(|f| {
            f.affected_files.iter().any(|p| all_set.contains(p))
        });
    }

    if file_result.all_files.is_empty() {
        // Return early with empty results
        return Ok((
            Arc::new(GraphStore::in_memory()),
            file_result,
            ParsePhaseResult {
                parse_results: vec![],
                total_functions: 0,
                total_classes: 0,
            },
        ));
    }

    if file_result.files_to_parse.is_empty() && env.config.is_incremental_mode && !env.quiet_mode {
        let check_icon = if env.config.no_emoji { "" } else { "✓ " };
        println!(
            "\n{}No files changed since last run. Using cached results.",
            style(check_icon).green()
        );
    }

    // Skip graph mode: use in-memory graph, parse files but don't build edges
    if env.config.skip_graph {
        if !env.quiet_mode {
            println!("{}Skipping graph building (--skip-graph or --lite mode)", style("⏭ ").dim());
        }
        
        let graph = Arc::new(GraphStore::in_memory());
        
        // Still parse files for function/class counts
        let _cache_mutex = std::sync::Mutex::new(IncrementalCache::new(&env.repotoire_dir.join("incremental")));
        let parse_result = parse_files_lite(
            &file_result.files_to_parse,
            multi,
            &bar_style,
        )?;
        
        return Ok((graph, file_result, parse_result));
    }

    // Initialize graph database (use lazy mode for large repos to reduce memory)
    let db_path = env.repotoire_dir.join("graph_db");
    let use_lazy = file_result.all_files.len() > 20000; // 20k+ files = use lazy loading
    
    if !env.quiet_mode {
        let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
        let mode_info = if use_lazy { " (lazy mode)" } else { "" };
        println!("{}Initializing graph database{}...", style(icon_graph).bold(), style(mode_info).dim());
    }
    
    let graph = if use_lazy {
        Arc::new(GraphStore::new_lazy(&db_path).with_context(|| "Failed to initialize graph database")?)
    } else {
        Arc::new(GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?)
    };

    // Parse files and build graph
    // Use TRUE streaming for repos with 2k+ files to prevent memory issues
    // This processes one file at a time, never holding more than 1 AST in memory
    let use_streaming = file_result.files_to_parse.len() > 2000;
    
    let parse_result = if use_streaming {
        // STREAMING MODE: Parse and build graph one file at a time
        // This prevents OOM on repos with 75k+ files
        if !env.quiet_mode {
            let stream_icon = if env.config.no_emoji { "" } else { "🌊 " };
            println!(
                "{}Using streaming mode for {} files (memory efficient)",
                style(stream_icon).bold(),
                style(file_result.files_to_parse.len()).cyan()
            );
        }
        
        let (total_functions, total_classes) = parse_and_build_streaming(
            &file_result.files_to_parse,
            &env.repo_path,
            Arc::clone(&graph),
            multi,
            &bar_style,
        )?;
        
        // Return empty parse_results since we built the graph already
        ParsePhaseResult {
            parse_results: vec![],
            total_functions,
            total_classes,
        }
    } else {
        // Traditional mode: collect parse results then build graph
        let cache_mutex = std::sync::Mutex::new(IncrementalCache::new(&env.repotoire_dir.join("incremental")));
        
        let result = if file_result.files_to_parse.len() > 10000 {
            // Chunked parsing for large repos (10k-50k files)
            parse_files_chunked(
                &file_result.files_to_parse,
                multi,
                &bar_style,
                env.config.is_incremental_mode,
                &cache_mutex,
                5000, // Process 5000 files at a time
            )?
        } else {
            parse_files(
                &file_result.files_to_parse,
                multi,
                &bar_style,
                env.config.is_incremental_mode,
                &cache_mutex,
            )?
        };
        
        // Save parse cache
        if let Ok(mut cache) = cache_mutex.into_inner() {
            let _ = cache.save_cache();
        }

        // Build graph in chunks for large repos
        if result.parse_results.len() > 10000 {
            build_graph_chunked(
                &graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                &bar_style,
                5000, // Build 5000 files at a time
            )?;
        } else {
            build_graph(
                &graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                &bar_style,
            )?;
        }
        
        result
    };

    // Pre-warm file cache (skip for huge repos)
    if file_result.all_files.len() < 20000 {
        crate::cache::warm_global_cache(&env.repo_path, SUPPORTED_EXTENSIONS);
    }

    Ok((graph, file_result, parse_result))
}

/// Phase 3: Run git enrichment and detectors
fn execute_detection_phase(
    env: &EnvironmentSetup,
    graph: &Arc<GraphStore>,
    file_result: &FileCollectionResult,
    skip_detector: &[String],
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> Result<Vec<Finding>> {
    // Start git enrichment in background
    let git_handle = start_git_enrichment(
        env.config.no_git,
        env.quiet_mode,
        &env.repo_path,
        Arc::clone(graph),
        multi,
        spinner_style,
    );

    // Use streaming detection for large repos (>5000 files) to prevent OOM
    let use_streaming = file_result.all_files.len() > 5000;
    
    let mut findings = if use_streaming {
        run_detectors_streaming(
            graph,
            &env.repo_path,
            &env.repotoire_dir,
            &env.project_config,
            skip_detector,
            env.config.thorough,
            multi,
            spinner_style,
            env.quiet_mode,
            env.config.no_emoji,
        )?
    } else {
        // Run detectors (with caching)
        let mut detector_cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
        run_detectors(
            graph,
            &env.repo_path,
            &env.project_config,
            skip_detector,
            env.config.thorough,
            env.config.workers,
            multi,
            spinner_style,
            env.quiet_mode,
            env.config.no_emoji,
            &mut detector_cache,
            &file_result.all_files,
        )?
    };

    // Apply voting engine (skip for streaming - already filtered)
    if !use_streaming {
        let (_voting_stats, _cached_count) = apply_voting(
            &mut findings,
            file_result.cached_findings.clone(),
            env.config.is_incremental_mode,
            multi,
            spinner_style,
        );
    }

    // Wait for git enrichment
    finish_git_enrichment(git_handle);

    Ok(findings)
}

/// Phase 5: Calculate health scores using graph-aware scorer
fn calculate_scores(
    graph: &Arc<GraphStore>,
    project_config: &ProjectConfig,
    findings: &[Finding],
) -> ScoreResult {
    let scorer = crate::scoring::GraphScorer::new(graph, project_config);
    let breakdown = scorer.calculate(findings);

    // Log graph metrics
    let metrics = &breakdown.graph_metrics;
    tracing::info!(
        "Graph metrics: {} modules, {:.1}% coupling, {:.1}% cohesion, {} cycles, {:.1}% simple fns",
        metrics.module_count,
        metrics.avg_coupling * 100.0,
        metrics.avg_cohesion * 100.0,
        metrics.cycle_count,
        metrics.simple_function_ratio * 100.0
    );

    ScoreResult {
        overall_score: breakdown.overall_score,
        structure_score: breakdown.structure.final_score,
        quality_score: breakdown.quality.final_score,
        architecture_score: breakdown.architecture.final_score,
        grade: breakdown.grade.clone(),
        breakdown,
    }
}

/// Build the health report with filtered and paginated findings
fn build_health_report(
    score_result: &ScoreResult,
    findings: &mut Vec<Finding>,
    severity: &Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    total_files: usize,
    total_functions: usize,
    total_classes: usize,
) -> (
    HealthReport,
    Option<(usize, usize, usize, usize)>,
    Vec<Finding>,
) {
    filter_findings(findings, severity, top);
    let all_findings = findings.clone();
    let display_summary = FindingsSummary::from_findings(findings);

    let (paginated_findings, pagination_info) = paginate_findings(std::mem::take(findings), page, per_page);

    let report = HealthReport {
        overall_score: score_result.overall_score,
        grade: score_result.grade.clone(),
        structure_score: score_result.structure_score,
        quality_score: score_result.quality_score,
        architecture_score: Some(score_result.architecture_score),
        findings: paginated_findings,
        findings_summary: display_summary,
        total_files,
        total_functions,
        total_classes,
    };

    (report, pagination_info, all_findings)
}

/// Phase 6: Generate and output reports
fn generate_reports(
    report_data: &(
        HealthReport,
        Option<(usize, usize, usize, usize)>,
        Vec<Finding>,
    ),
    findings: &[Finding],
    format: &str,
    output_path: Option<&Path>,
    repotoire_dir: &Path,
    pagination_info: Option<(usize, usize, usize, usize)>,
    no_emoji: bool,
    explain_score: bool,
    score_result: &ScoreResult,
    graph: &Arc<GraphStore>,
    project_config: &ProjectConfig,
) -> Result<()> {
    let (report, _, all_findings) = report_data;
    let displayed_findings = findings.len();

    format_and_output(
        report,
        all_findings,
        format,
        output_path,
        repotoire_dir,
        pagination_info,
        displayed_findings,
        no_emoji,
    )?;

    // Show score explanation if requested
    if explain_score && format == "text" {
        println!("\n{}", style("─".repeat(60)).dim());
        let scorer = crate::scoring::GraphScorer::new(graph, project_config);
        let explanation = scorer.explain(&score_result.breakdown);
        println!("{}", explanation);
    }

    Ok(())
}

/// Print final summary message
fn print_final_summary(quiet_mode: bool, no_emoji: bool, start_time: Instant) {
    if !quiet_mode {
        let elapsed = start_time.elapsed();
        let icon_done = if no_emoji { "" } else { "✨ " };
        println!(
            "\n{}Analysis complete in {:.2}s",
            style(icon_done).bold(),
            elapsed.as_secs_f64()
        );
    }
}

/// Apply CLI defaults from project config
fn apply_config_defaults(
    no_emoji: bool,
    thorough: bool,
    no_git: bool,
    workers: usize,
    per_page: usize,
    fail_on: Option<String>,
    incremental: bool,
    has_since: bool,
    skip_graph: bool,
    max_files: usize,
    project_config: &ProjectConfig,
) -> AnalysisConfig {
    AnalysisConfig {
        no_emoji: no_emoji || project_config.defaults.no_emoji.unwrap_or(false),
        thorough: thorough || project_config.defaults.thorough.unwrap_or(false),
        no_git: no_git || project_config.defaults.no_git.unwrap_or(false),
        workers: if workers == 8 {
            project_config.defaults.workers.unwrap_or(workers)
        } else {
            workers
        },
        per_page: if per_page == 20 {
            project_config.defaults.per_page.unwrap_or(per_page)
        } else {
            per_page
        },
        fail_on: fail_on.or_else(|| project_config.defaults.fail_on.clone()),
        is_incremental_mode: incremental || has_since,
        skip_graph,
        max_files,
    }
}

/// Print analysis header
fn print_header(repo_path: &Path, no_emoji: bool, format: &str, project_type: &crate::config::ProjectType) {
    // Suppress progress output for machine-readable formats
    if format == "json" || format == "sarif" {
        return;
    }

    let icon_analyze = if no_emoji { "" } else { "🎼 " };
    let icon_search = if no_emoji { "" } else { "🔍 " };
    let icon_type = if no_emoji { "" } else { "📦 " };

    println!("\n{}Repotoire Analysis\n", style(icon_analyze).bold());
    println!(
        "{}Analyzing: {}",
        style(icon_search).bold(),
        style(repo_path.display()).cyan()
    );
    println!(
        "{}Detected:  {:?}\n",
        style(icon_type).dim(),
        project_type
    );
}

/// Create spinner progress style
fn create_spinner_style() -> ProgressStyle {
    ProgressStyle::default_spinner()
        .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")
        .template("{spinner:.green} {msg}")
        .unwrap()
}

/// Create bar progress style
fn create_bar_style() -> ProgressStyle {
    ProgressStyle::default_bar()
        .template("{spinner:.green} [{bar:40.cyan/blue}] {pos}/{len} {msg}")
        .unwrap()
        .progress_chars("█▓▒░  ")
}

/// Collect files for analysis based on mode (full, incremental, or since)
fn collect_files_for_analysis(
    repo_path: &Path,
    since: &Option<String>,
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> Result<FileCollectionResult> {
    let walk_spinner = multi.add(ProgressBar::new_spinner());
    walk_spinner.set_style(spinner_style.clone());

    let (all_files, files_to_parse, cached_findings) = if let Some(ref commit) = since {
        // --since mode: only analyze files changed since specified commit
        walk_spinner.set_message(format!("Finding files changed since {}...", commit));
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let changed = get_changed_files_since(repo_path, commit)?;
        let all = collect_source_files(repo_path)?;

        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files (since {}) out of {} total",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(commit).yellow(),
            style(all.len()).dim()
        ));

        let cached = get_cached_findings_for_unchanged(&all, &changed, incremental_cache);
        (all, changed, cached)
    } else if is_incremental_mode {
        // --incremental mode: only analyze files changed since last run
        walk_spinner.set_message("Discovering source files (incremental mode)...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let all = collect_source_files(repo_path)?;
        let changed = incremental_cache.get_changed_files(&all);
        let cache_stats = incremental_cache.get_stats();

        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files out of {} total ({} cached)",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(all.len()).dim(),
            style(cache_stats.cached_files).dim()
        ));

        let cached = get_cached_findings_for_unchanged(&all, &changed, incremental_cache);
        (all, changed, cached)
    } else {
        // Full mode: analyze all files
        walk_spinner.set_message("Discovering source files...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let files = collect_source_files(repo_path)?;
        walk_spinner.finish_with_message(format!(
            "{}Found {} source files",
            style("✓ ").green(),
            style(files.len()).cyan()
        ));

        (files.clone(), files, Vec::new())
    };

    Ok(FileCollectionResult {
        all_files,
        files_to_parse,
        cached_findings,
    })
}

/// Get cached findings for unchanged files
fn get_cached_findings_for_unchanged(
    all_files: &[PathBuf],
    changed_files: &[PathBuf],
    incremental_cache: &IncrementalCache,
) -> Vec<Finding> {
    let unchanged: Vec<_> = all_files
        .iter()
        .filter(|f| !changed_files.contains(f))
        .collect();

    let mut cached = Vec::new();
    for file in unchanged {
        cached.extend(incremental_cache.get_cached_findings(file));
    }
    cached
}

// Parsing functions extracted to parse.rs

// ============================================================================
// Helper functions
// ============================================================================

/// Collect all source files in the repository, respecting .gitignore
fn collect_source_files(repo_path: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();

    let mut builder = WalkBuilder::new(repo_path);
    builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .require_git(false)
        .add_custom_ignore_filename(".repotoireignore");

    let walker = builder.build();

    for entry in walker.flatten() {
        let path = entry.path();

        if !path.is_file() {
            continue;
        }

        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            if SUPPORTED_EXTENSIONS.contains(&ext) {
                files.push(path.to_path_buf());
            }
        }
    }

    Ok(files)
}

/// Get files changed since a specific git commit
fn get_changed_files_since(repo_path: &Path, since: &str) -> Result<Vec<PathBuf>> {
    use std::process::Command;
    
    let output = Command::new("git")
        .args(["diff", "--name-only", since, "HEAD"])
        .current_dir(repo_path)
        .output()
        .with_context(|| format!("Failed to run git diff since '{}'", since))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("git diff failed: {}", stderr.trim());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut files: Vec<PathBuf> = stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(|l| repo_path.join(l))
        .filter(|p| p.exists())
        .collect();

    // Also get untracked files
    let untracked = Command::new("git")
        .args(["ls-files", "--others", "--exclude-standard"])
        .current_dir(repo_path)
        .output();

    if let Ok(out) = untracked {
        if out.status.success() {
            let new_files = String::from_utf8_lossy(&out.stdout);
            for line in new_files.lines().filter(|l| !l.is_empty()) {
                let path = repo_path.join(line);
                if path.exists() && !files.contains(&path) {
                    files.push(path);
                }
            }
        }
    }

    files.retain(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|ext| SUPPORTED_EXTENSIONS.contains(&ext))
            .unwrap_or(false)
    });

    Ok(files)
}

// Graph building functions extracted to graph.rs
// Detection functions extracted to detect.rs
// Output functions extracted to output.rs
