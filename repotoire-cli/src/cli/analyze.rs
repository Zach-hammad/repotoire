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

use crate::config::{load_project_config, ProjectConfig};
use crate::detectors::{
    default_detectors_with_config, ConfidenceMethod, DetectorEngine, IncrementalCache,
    SeverityResolution, VotingEngine, VotingStats, VotingStrategy,
};
use crate::git;
use crate::graph::{CodeEdge, CodeNode, GraphStore, NodeKind};
use crate::models::{Finding, FindingsSummary, HealthReport, Severity};
use crate::parsers::{parse_file, ParseResult};
use crate::reporters;

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Command;
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

/// Result of file collection phase
struct FileCollectionResult {
    all_files: Vec<PathBuf>,
    files_to_parse: Vec<PathBuf>,
    cached_findings: Vec<Finding>,
}

/// Result of parsing phase
struct ParsePhaseResult {
    parse_results: Vec<(PathBuf, ParseResult)>,
    total_functions: usize,
    total_classes: usize,
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
) -> Result<()> {
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
    )?;

    // Phase 2: Initialize graph and collect files
    let (graph, file_result, parse_result) = initialize_graph(&env, &since, &MultiProgress::new())?;

    if file_result.all_files.is_empty() {
        if !env.quiet_mode {
            println!(
                "\n{}No source files found to analyze.",
                style("⚠️  ").yellow()
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

    // Phase 5: Calculate scores and build report
    let score_result = calculate_scores(&graph, &env.project_config, &findings);

    let report = build_health_report(
        &score_result,
        &mut findings,
        &severity,
        top,
        page,
        per_page,
        file_result.files_to_parse.len(),
        parse_result.total_functions,
        parse_result.total_classes,
    );

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
        &project_config,
    );

    let quiet_mode = format == "json" || format == "sarif";
    print_header(&repo_path, config.no_emoji, format);

    let repotoire_dir = crate::cache::ensure_cache_dir(&repo_path)
        .with_context(|| "Failed to create cache directory")?;
    let incremental_cache = IncrementalCache::new(&repotoire_dir.join("incremental"));

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
    let file_result = collect_files_for_analysis(
        &env.repo_path,
        since,
        env.config.is_incremental_mode,
        &mut cache_clone,
        multi,
        &spinner_style,
    )?;

    if file_result.all_files.is_empty() {
        // Return early with empty results
        return Ok((
            Arc::new(GraphStore::new(&env.repotoire_dir.join("graph_db"))?),
            file_result,
            ParsePhaseResult {
                parse_results: vec![],
                total_functions: 0,
                total_classes: 0,
            },
        ));
    }

    if file_result.files_to_parse.is_empty() && env.config.is_incremental_mode && !env.quiet_mode {
        println!(
            "\n{}No files changed since last run. Using cached results.",
            style("✓ ").green()
        );
    }

    // Initialize graph database
    let db_path = env.repotoire_dir.join("graph_db");
    if !env.quiet_mode {
        let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
        println!("{}Initializing graph database...", style(icon_graph).bold());
    }
    let graph =
        Arc::new(GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?);

    // Parse files and build graph
    let parse_result = parse_files(
        &file_result.files_to_parse,
        multi,
        &bar_style,
        env.config.is_incremental_mode,
    )?;

    build_graph(
        &graph,
        &env.repo_path,
        &parse_result.parse_results,
        multi,
        &bar_style,
    )?;

    // Pre-warm file cache
    crate::cache::warm_global_cache(&env.repo_path, SUPPORTED_EXTENSIONS);

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
        &env.repo_path,
        Arc::clone(graph),
        multi,
        spinner_style,
    );

    // Run detectors
    let mut findings = run_detectors(
        graph,
        &env.repo_path,
        &env.project_config,
        skip_detector,
        env.config.thorough,
        env.config.workers,
        multi,
        spinner_style,
        env.quiet_mode,
    )?;

    // Apply voting engine
    let (_voting_stats, _cached_count) = apply_voting(
        &mut findings,
        file_result.cached_findings.clone(),
        env.config.is_incremental_mode,
        multi,
        spinner_style,
    );

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
    let all_findings = findings.clone();

    filter_findings(findings, severity, top);
    let display_summary = FindingsSummary::from_findings(findings);

    let (paginated_findings, pagination_info) = paginate_findings(findings.clone(), page, per_page);

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
    }
}

/// Print analysis header
fn print_header(repo_path: &Path, no_emoji: bool, format: &str) {
    // Suppress progress output for machine-readable formats
    if format == "json" || format == "sarif" {
        return;
    }

    let icon_analyze = if no_emoji { "" } else { "🎼 " };
    let icon_search = if no_emoji { "" } else { "🔍 " };

    println!("\n{}Repotoire Analysis\n", style(icon_analyze).bold());
    println!(
        "{}Analyzing: {}\n",
        style(icon_search).bold(),
        style(repo_path.display()).cyan()
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

/// Parse files in parallel
fn parse_files(
    files: &[PathBuf],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    is_incremental: bool,
) -> Result<ParsePhaseResult> {
    use rayon::prelude::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    let parse_msg = if is_incremental {
        "Parsing changed files (parallel)..."
    } else {
        "Parsing files (parallel)..."
    };
    parse_bar.set_message(parse_msg);

    let counter = AtomicUsize::new(0);
    let total_files = files.len();

    let parse_results: Vec<(PathBuf, ParseResult)> = files
        .par_iter()
        .filter_map(|file_path| {
            let count = counter.fetch_add(1, Ordering::Relaxed);
            if count.is_multiple_of(100) {
                parse_bar.set_position(count as u64);
            }

            match parse_file(file_path) {
                Ok(result) => Some((file_path.clone(), result)),
                Err(e) => {
                    tracing::warn!("Failed to parse {}: {}", file_path.display(), e);
                    None
                }
            }
        })
        .collect();

    let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
    let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();

    parse_bar.finish_with_message(format!(
        "{}Parsed {} files ({} functions, {} classes)",
        style("✓ ").green(),
        style(total_files).cyan(),
        style(total_functions).cyan(),
        style(total_classes).cyan(),
    ));

    Ok(ParsePhaseResult {
        parse_results,
        total_functions,
        total_classes,
    })
}

/// Build the code graph from parse results
fn build_graph(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, ParseResult)],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<()> {
    let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
    let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();

    let graph_bar = multi.add(ProgressBar::new(parse_results.len() as u64));
    graph_bar.set_style(bar_style.clone());
    graph_bar.set_message("Building code graph...");

    // Collect all nodes
    let mut file_nodes = Vec::with_capacity(parse_results.len());
    let mut func_nodes = Vec::with_capacity(total_functions);
    let mut class_nodes = Vec::with_capacity(total_classes);
    let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();

    // Build global function lookup
    let global_func_map = build_global_function_map(parse_results);

    for (file_path, result) in parse_results {
        let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let relative_str = relative_path.display().to_string();
        let language = detect_language(file_path);
        let loc = count_lines(file_path).unwrap_or(0);

        // File node
        file_nodes.push(
            CodeNode::new(NodeKind::File, &relative_str, &relative_str)
                .with_qualified_name(&relative_str)
                .with_language(&language)
                .with_property("loc", loc as i64),
        );

        // Function nodes
        for func in &result.functions {
            let loc = if func.line_end >= func.line_start {
                func.line_end - func.line_start + 1
            } else {
                1
            };
            let complexity = func.complexity.unwrap_or(1);
            let address_taken = result.address_taken.contains(&func.name);

            func_nodes.push(
                CodeNode::new(NodeKind::Function, &func.name, &relative_str)
                    .with_qualified_name(&func.qualified_name)
                    .with_lines(func.line_start, func.line_end)
                    .with_property("is_async", func.is_async)
                    .with_property("complexity", complexity as i64)
                    .with_property("loc", loc as i64)
                    .with_property("address_taken", address_taken),
            );
            edges.push((
                relative_str.clone(),
                func.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Class nodes
        for class in &result.classes {
            class_nodes.push(
                CodeNode::new(NodeKind::Class, &class.name, &relative_str)
                    .with_qualified_name(&class.qualified_name)
                    .with_lines(class.line_start, class.line_end)
                    .with_property("methodCount", class.methods.len() as i64),
            );
            edges.push((
                relative_str.clone(),
                class.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Call edges
        build_call_edges(
            &mut edges,
            result,
            parse_results,
            repo_path,
            &global_func_map,
        );

        // Import edges
        build_import_edges(&mut edges, result, &relative_str, parse_results, repo_path);

        graph_bar.inc(1);
    }

    // Batch insert all nodes
    graph_bar.set_message("Inserting nodes...");
    graph.add_nodes_batch(file_nodes);
    graph.add_nodes_batch(func_nodes);
    graph.add_nodes_batch(class_nodes);

    // Batch insert all edges
    graph_bar.set_message("Inserting edges...");
    graph.add_edges_batch(edges);

    graph_bar.finish_with_message(format!("{}Built code graph", style("✓ ").green()));

    // Persist graph and stats
    graph
        .save()
        .with_context(|| "Failed to save graph database")?;
    save_graph_stats(graph, repo_path)?;

    Ok(())
}

/// Build global function name -> qualified name map
fn build_global_function_map(parse_results: &[(PathBuf, ParseResult)]) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for (_, result) in parse_results {
        for func in &result.functions {
            map.insert(func.name.clone(), func.qualified_name.clone());
        }
    }
    map
}

/// Build call edges for a file
fn build_call_edges(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    parse_results: &[(PathBuf, ParseResult)],
    repo_path: &Path,
    global_func_map: &HashMap<String, String>,
) {
    for (caller, callee) in &result.calls {
        let parts: Vec<&str> = callee.rsplitn(2, "::").collect();
        let callee_name = parts[0];
        let callee_module = if parts.len() > 1 {
            Some(parts[1])
        } else {
            None
        };
        let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);

        // Try to find callee in this file first
        let callee_qn = if let Some(callee_func) =
            result.functions.iter().find(|f| f.name == callee_name)
        {
            callee_func.qualified_name.clone()
        } else {
            // Look in other modules
            let mut found = None;
            if let Some(module) = callee_module {
                for (other_path, other_result) in parse_results {
                    let other_relative = other_path.strip_prefix(repo_path).unwrap_or(other_path);
                    let other_str = other_relative.display().to_string();
                    let file_stem = other_relative
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("");

                    if file_stem == module || other_str.contains(&format!("/{}.rs", module)) {
                        if let Some(func) = other_result
                            .functions
                            .iter()
                            .find(|f| f.name == callee_name)
                        {
                            found = Some(func.qualified_name.clone());
                            break;
                        }
                    }
                }
            }

            if found.is_none() {
                found = global_func_map.get(callee_name).cloned();
            }

            match found {
                Some(qn) => qn,
                None => continue,
            }
        };
        edges.push((caller.clone(), callee_qn, CodeEdge::calls()));
    }
}

/// Build import edges for a file
fn build_import_edges(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    relative_str: &str,
    parse_results: &[(PathBuf, ParseResult)],
    repo_path: &Path,
) {
    for import_info in &result.imports {
        let clean_import = import_info
            .path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let module_parts: Vec<&str> = clean_import.split("::").collect();
        let first_module = module_parts.first().copied().unwrap_or("");

        for (other_file, _) in parse_results {
            let other_relative = other_file.strip_prefix(repo_path).unwrap_or(other_file);
            let other_str = other_relative.display().to_string();
            if other_str == relative_str {
                continue;
            }

            let other_name = other_relative
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("");

            let python_path = clean_import.replace('.', "/");

            let matches = other_str.contains(clean_import)
                || (clean_import == other_name)
                || other_str.ends_with(&format!("{}.ts", clean_import))
                || other_str.ends_with(&format!("{}.tsx", clean_import))
                || other_str.ends_with(&format!("{}.js", clean_import))
                || other_str.ends_with(&format!("{}/index.ts", clean_import))
                || other_str.ends_with(&format!("{}.rs", clean_import.replace("::", "/")))
                || other_str.ends_with(&format!("{}/mod.rs", first_module))
                || (other_name == first_module && other_str.ends_with(".rs"))
                || other_str.ends_with(&format!("{}.py", python_path))
                || other_str.contains(&format!("{}/", python_path))
                || other_str.ends_with(&format!("{}/__init__.py", python_path));

            if matches {
                let import_edge =
                    CodeEdge::imports().with_property("is_type_only", import_info.is_type_only);
                edges.push((relative_str.to_string(), other_str, import_edge));
                break;
            }
        }
    }
}

/// Save graph statistics to JSON
fn save_graph_stats(graph: &GraphStore, repo_path: &Path) -> Result<()> {
    let graph_stats = serde_json::json!({
        "total_files": graph.get_files().len(),
        "total_functions": graph.get_functions().len(),
        "total_classes": graph.get_classes().len(),
        "total_nodes": graph.node_count(),
        "total_edges": graph.edge_count(),
        "calls": graph.get_calls().len(),
        "imports": graph.get_imports().len(),
    });
    let stats_path = crate::cache::get_graph_stats_path(repo_path);
    std::fs::write(&stats_path, serde_json::to_string_pretty(&graph_stats)?)?;
    Ok(())
}

/// Start git enrichment in background thread
fn start_git_enrichment(
    no_git: bool,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> Option<(
    std::thread::JoinHandle<Result<git::enrichment::EnrichmentStats, anyhow::Error>>,
    ProgressBar,
)> {
    if no_git {
        println!("{}Skipping git enrichment (--no-git)", style("⏭ ").dim());
        return None;
    }

    let git_spinner = multi.add(ProgressBar::new_spinner());
    git_spinner.set_style(spinner_style.clone());
    git_spinner.set_message("Enriching with git history (async)...");
    git_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let repo_path_clone = repo_path.to_path_buf();
    let git_handle = std::thread::spawn(move || {
        git::enrichment::enrich_graph_with_git(&repo_path_clone, &graph, None)
    });

    Some((git_handle, git_spinner))
}

/// Wait for git enrichment to complete
fn finish_git_enrichment(
    git_result: Option<(
        std::thread::JoinHandle<Result<git::enrichment::EnrichmentStats, anyhow::Error>>,
        ProgressBar,
    )>,
) {
    if let Some((git_handle, git_spinner)) = git_result {
        match git_handle.join() {
            Ok(Ok(stats)) => {
                if stats.functions_enriched > 0 || stats.classes_enriched > 0 {
                    let cache_info = if stats.cache_hits > 0 {
                        format!(" ({} cached)", stats.cache_hits)
                    } else {
                        String::new()
                    };
                    git_spinner.finish_with_message(format!(
                        "{}Enriched {} functions, {} classes{}",
                        style("✓ ").green(),
                        style(stats.functions_enriched).cyan(),
                        style(stats.classes_enriched).cyan(),
                        style(cache_info).dim(),
                    ));
                } else {
                    git_spinner.finish_with_message(format!(
                        "{}No git history to enrich",
                        style("- ").dim(),
                    ));
                }
            }
            Ok(Err(e)) => {
                git_spinner.finish_with_message(format!(
                    "{}Git enrichment skipped: {}",
                    style("⚠ ").yellow(),
                    e
                ));
            }
            Err(_) => {
                git_spinner
                    .finish_with_message(format!("{}Git enrichment failed", style("⚠ ").yellow(),));
            }
        }
    }
}

/// Run all detectors on the graph
fn run_detectors(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    project_config: &ProjectConfig,
    skip_detector: &[String],
    thorough: bool,
    workers: usize,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    quiet_mode: bool,
) -> Result<Vec<Finding>> {
    if !quiet_mode {
        println!("\n{}Running detectors...", style("🕵️  ").bold());
    }

    let mut engine = DetectorEngine::new(workers);
    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();

    // Register default detectors
    for detector in default_detectors_with_config(repo_path, project_config) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // In thorough mode, add external tool detectors
    if thorough {
        let external = crate::detectors::all_external_detectors(repo_path);
        let external_count = external.len();
        for detector in external {
            engine.register(detector);
        }
        tracing::info!(
            "Thorough mode: added {} external detectors ({} total)",
            external_count,
            engine.detector_count()
        );
    }

    let detector_bar = multi.add(ProgressBar::new_spinner());
    detector_bar.set_style(spinner_style.clone());
    detector_bar.set_message("Running detectors...");
    detector_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    let findings = engine.run(graph)?;

    detector_bar.finish_with_message(format!(
        "{}Ran {} detectors, found {} raw issues",
        style("✓ ").green(),
        style(engine.detector_count()).cyan(),
        style(findings.len()).cyan(),
    ));

    Ok(findings)
}

/// Apply voting engine to consolidate findings
fn apply_voting(
    findings: &mut Vec<Finding>,
    cached_findings: Vec<Finding>,
    is_incremental_mode: bool,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> (VotingStats, usize) {
    let voting_spinner = multi.add(ProgressBar::new_spinner());
    voting_spinner.set_style(spinner_style.clone());
    voting_spinner.set_message("Consolidating findings with voting engine...");
    voting_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let voting_engine = VotingEngine::with_config(
        VotingStrategy::Weighted,
        ConfidenceMethod::Bayesian,
        SeverityResolution::Highest,
        0.5,
        2,
    );
    let (consolidated_findings, voting_stats) = voting_engine.vote(std::mem::take(findings));
    *findings = consolidated_findings;

    // Merge cached findings
    let cached_findings_count = cached_findings.len();
    if is_incremental_mode && !cached_findings.is_empty() {
        findings.extend(cached_findings);
        tracing::debug!(
            "Merged {} cached findings with {} new findings",
            cached_findings_count,
            voting_stats.total_output
        );
    }

    voting_spinner.finish_with_message(format!(
        "{}Consolidated {} -> {} findings ({} merged, {} rejected{})",
        style("✓ ").green(),
        style(voting_stats.total_input).cyan(),
        style(voting_stats.total_output).cyan(),
        style(voting_stats.boosted_by_consensus).dim(),
        style(voting_stats.rejected_low_confidence).dim(),
        if cached_findings_count > 0 {
            format!(", {} from cache", style(cached_findings_count).dim())
        } else {
            String::new()
        }
    ));

    (voting_stats, cached_findings_count)
}

/// Update incremental cache with new findings
fn update_incremental_cache(
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    files: &[PathBuf],
    findings: &[Finding],
) {
    if !is_incremental_mode {
        return;
    }

    for file_path in files {
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.affected_files.iter().any(|af| af == file_path))
            .cloned()
            .collect();
        incremental_cache.cache_findings(file_path, &file_findings);
    }

    if let Err(e) = incremental_cache.save_cache() {
        tracing::warn!("Failed to save incremental cache: {}", e);
    }
}

/// Apply detector config overrides from project config
fn apply_detector_overrides(findings: &mut Vec<Finding>, project_config: &ProjectConfig) {
    if project_config.detectors.is_empty() {
        return;
    }

    let detector_configs = &project_config.detectors;

    // Filter out disabled detectors
    findings.retain(|f| {
        let detector_name = crate::config::normalize_detector_name(&f.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(false) = config.enabled {
                return false;
            }
        }
        true
    });

    // Apply severity overrides
    for finding in findings.iter_mut() {
        let detector_name = crate::config::normalize_detector_name(&finding.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(ref sev) = config.severity {
                finding.severity = parse_severity(sev);
            }
        }
    }
}

/// Filter findings by severity and limit
fn filter_findings(findings: &mut Vec<Finding>, severity: &Option<String>, top: Option<usize>) {
    if let Some(min_severity) = severity {
        let min = parse_severity(min_severity);
        findings.retain(|f| f.severity >= min);
    }

    findings.sort_by(|a, b| b.severity.cmp(&a.severity));

    if let Some(n) = top {
        findings.truncate(n);
    }
}

/// Paginate findings
fn paginate_findings(
    findings: Vec<Finding>,
    page: usize,
    per_page: usize,
) -> (Vec<Finding>, Option<(usize, usize, usize, usize)>) {
    let displayed_findings = findings.len();

    if per_page > 0 {
        let total_pages = displayed_findings.div_ceil(per_page);
        let page = page.max(1).min(total_pages.max(1));
        let start = (page - 1) * per_page;
        let end = (start + per_page).min(displayed_findings);
        let paginated: Vec<_> = findings[start..end].to_vec();
        (
            paginated,
            Some((page, total_pages, per_page, displayed_findings)),
        )
    } else {
        (findings, None)
    }
}

/// Format and output results
fn format_and_output(
    report: &HealthReport,
    all_findings: &[Finding],
    format: &str,
    output_path: Option<&Path>,
    repotoire_dir: &Path,
    pagination_info: Option<(usize, usize, usize, usize)>,
    _displayed_findings: usize,
    _no_emoji: bool,
) -> Result<()> {
    // For machine-readable formats, include ALL findings (not paginated)
    let report_for_output = if format == "json" || format == "sarif" {
        HealthReport {
            findings: all_findings.to_vec(),
            findings_summary: FindingsSummary::from_findings(all_findings),
            ..report.clone()
        }
    } else {
        report.clone()
    };

    let output = reporters::report(&report_for_output, format)?;

    let write_to_file =
        output_path.is_some() || matches!(format, "html" | "sarif" | "markdown" | "md");

    if write_to_file {
        let out_path = if let Some(p) = output_path {
            p.to_path_buf()
        } else {
            let ext = match format {
                "html" => "html",
                "sarif" => "sarif.json",
                "markdown" | "md" => "md",
                "json" => "json",
                _ => "txt",
            };
            repotoire_dir.join(format!("report.{}", ext))
        };

        std::fs::write(&out_path, &output)?;
        println!(
            "\n{}Report written to: {}",
            style("📄 ").bold(),
            style(out_path.display()).cyan()
        );
    } else {
        println!();
        println!("{}", output);
    }

    // Cache results
    cache_results(repotoire_dir, report, all_findings)?;

    // Show pagination info (suppress for machine-readable formats)
    let quiet_mode = format == "json" || format == "sarif";
    if !quiet_mode {
        if let Some((current_page, total_pages, per_page, total)) = pagination_info {
            println!(
                "\n{}Showing page {} of {} ({} findings per page, {} total)",
                style("📑 ").bold(),
                style(current_page).cyan(),
                style(total_pages).cyan(),
                style(per_page).dim(),
                style(total).cyan(),
            );
            if current_page < total_pages {
                println!(
                    "   Use {} to see more",
                    style(format!("--page {}", current_page + 1)).yellow()
                );
            }
        }
    }

    Ok(())
}

/// Check if fail threshold is met
fn check_fail_threshold(fail_on: &Option<String>, report: &HealthReport) -> Result<()> {
    if let Some(ref threshold) = fail_on {
        let should_fail = match threshold.to_lowercase().as_str() {
            "critical" => report.findings_summary.critical > 0,
            "high" => report.findings_summary.critical > 0 || report.findings_summary.high > 0,
            "medium" => {
                report.findings_summary.critical > 0
                    || report.findings_summary.high > 0
                    || report.findings_summary.medium > 0
            }
            "low" => {
                report.findings_summary.critical > 0
                    || report.findings_summary.high > 0
                    || report.findings_summary.medium > 0
                    || report.findings_summary.low > 0
            }
            _ => false,
        };
        if should_fail {
            eprintln!("Failing due to --fail-on={} threshold", threshold);
            std::process::exit(1);
        }
    }
    Ok(())
}

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

/// Detect the language from file extension
fn detect_language(path: &Path) -> String {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    match ext {
        "py" | "pyi" => "Python",
        "ts" | "tsx" => "TypeScript",
        "js" | "jsx" | "mjs" => "JavaScript",
        "rs" => "Rust",
        "go" => "Go",
        "java" => "Java",
        "c" | "h" => "C",
        "cpp" | "hpp" | "cc" => "C++",
        "cs" => "C#",
        "kt" | "kts" => "Kotlin",
        "rb" => "Ruby",
        "php" => "PHP",
        "swift" => "Swift",
        _ => "Unknown",
    }
    .to_string()
}

/// Count lines in a file
fn count_lines(path: &Path) -> Result<usize> {
    let content = std::fs::read_to_string(path)?;
    Ok(content.lines().count())
}

/// Parse a severity string
fn parse_severity(s: &str) -> Severity {
    match s.to_lowercase().as_str() {
        "critical" => Severity::Critical,
        "high" => Severity::High,
        "medium" => Severity::Medium,
        "low" => Severity::Low,
        _ => Severity::Info,
    }
}

/// Normalize a path to be relative
fn normalize_path(path: &Path) -> String {
    let s = path.display().to_string();
    if let Some(stripped) = s.strip_prefix("/tmp/") {
        if let Some(pos) = stripped.find('/') {
            return stripped[pos + 1..].to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Some(stripped) = s.strip_prefix(&home) {
            return stripped.trim_start_matches('/').to_string();
        }
    }
    s
}

/// Cache analysis results for other commands
fn cache_results(
    repotoire_dir: &Path,
    report: &HealthReport,
    all_findings: &[Finding],
) -> Result<()> {
    use std::fs;

    let health_cache = repotoire_dir.join("last_health.json");
    let health_json = serde_json::json!({
        "health_score": report.overall_score,
        "structure_score": report.structure_score,
        "quality_score": report.quality_score,
        "architecture_score": report.architecture_score,
        "grade": report.grade,
        "total_files": report.total_files,
        "total_functions": report.total_functions,
        "total_classes": report.total_classes,
    });
    fs::write(&health_cache, serde_json::to_string_pretty(&health_json)?)?;

    let findings_cache = repotoire_dir.join("last_findings.json");
    let findings_json = serde_json::json!({
        "findings": all_findings.iter().map(|f| {
            serde_json::json!({
                "id": f.id,
                "detector": f.detector,
                "title": f.title,
                "description": f.description,
                "severity": f.severity.to_string(),
                "affected_files": f.affected_files.iter().map(|p| normalize_path(p)).collect::<Vec<_>>(),
                "line_start": f.line_start,
                "line_end": f.line_end,
                "suggested_fix": f.suggested_fix,
                "category": f.category,
                "cwe_id": f.cwe_id,
                "why_it_matters": f.why_it_matters,
                "confidence": f.confidence,
            })
        }).collect::<Vec<_>>()
    });
    fs::write(
        &findings_cache,
        serde_json::to_string_pretty(&findings_json)?,
    )?;

    tracing::debug!("Cached analysis results to {}", repotoire_dir.display());
    Ok(())
}

/// Get files changed since a specific git commit
fn get_changed_files_since(repo_path: &Path, since: &str) -> Result<Vec<PathBuf>> {
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
