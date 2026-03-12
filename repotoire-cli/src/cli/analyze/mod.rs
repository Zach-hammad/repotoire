//! Analyze command implementation
//!
//! Orchestrates the full codebase analysis pipeline:
//! 1. Setup environment and validate inputs       (setup.rs)
//! 2. Collect and filter source files              (files.rs)
//! 3. Parse files and build the code graph         (parse.rs, graph.rs)
//! 4. Run detectors                                (detect.rs)
//! 5. Post-process findings (FP filter, escalate)  (postprocess.rs)
//! 6. Calculate health scores                      (scoring.rs)
//! 7. Output results (text, json, sarif, html, md) (output.rs)

mod detect;
mod export;
pub(crate) mod files;
pub(crate) mod graph;
pub(crate) mod output;
mod parse;
mod postprocess;
mod scoring;
mod setup;

use detect::{
    apply_voting, finish_git_enrichment, run_detectors_speculative,
    run_gi_detectors, start_git_enrichment,
};
use files::{collect_file_list, collect_files_for_analysis, walk_files_to_channel};
use graph::{build_graph, build_graph_chunked, parse_and_build_streaming, parse_and_build_streaming_overlapped};
use output::{
    cache_results, check_fail_threshold, format_and_output, load_cached_findings,
    output_cached_results,
};
use parse::{parse_files, parse_files_chunked, parse_files_lite, ParsePhaseResult};
use postprocess::postprocess_findings;
use scoring::{build_health_report, calculate_scores};
use setup::{
    create_bar_style, create_spinner_style, setup_environment, EnvironmentSetup,
    FileCollectionResult, ScoreResult,
};

use crate::detectors::IncrementalCache;
use crate::graph::GraphStore;
use crate::models::{Finding, HealthReport};

use anyhow::{Context, Result};
use console::style;
use indicatif::{MultiProgress, ProgressStyle};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

/// Cache directory for a repository (legacy .repotoire path)
pub fn cache_path(repo_path: &Path) -> PathBuf {
    repo_path.join(".repotoire")
}

/// Run the analyze command — main entry point.
pub fn run(
    path: &Path,
    format: &str,
    output_path: Option<&Path>,
    severity: Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: Vec<String>,
    run_external: bool,
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
    rank: bool,
    export_training: Option<&Path>,
    timings: bool,
) -> Result<()> {
    // Normalize skip_detector names to kebab-case so both "TodoScanner" and "todo-scanner" work
    let skip_detector: Vec<String> = skip_detector
        .into_iter()
        .map(|s| normalize_to_kebab(&s))
        .collect();

    let start_time = Instant::now();
    let mut phase_timings: Vec<(&str, std::time::Duration)> = Vec::new();

    // Clear per-run caches (important for MCP long-running server)
    crate::parsers::clear_structural_fingerprint_cache();

    // ─── Session-based incremental path ────────────────────────────────────
    // Try to load a persisted AnalysisSession. If found and files changed,
    // do a fast incremental update instead of the full pipeline.
    {
        use crate::session::AnalysisSession;
        let session_cache_dir = cache_path(path).join("session");
        if let Ok(Some(mut session)) = AnalysisSession::load(&session_cache_dir) {
            let changed = session.detect_changed_files()?;
            if changed.is_empty() {
                // Fast path: nothing changed — output from session
                let quiet = std::env::var("REPOTOIRE_QUIET").is_ok();
                if !quiet {
                    let icon = if no_emoji { "" } else { "⚡ " };
                    eprintln!(
                        "\n{}Using cached session (no changes detected)\n",
                        style(icon).bold()
                    );
                }

                // Build a CachedScoreResult from session data
                let cached_score = crate::detectors::CachedScoreResult {
                    score: session.score().unwrap_or(0.0),
                    grade: session_score_to_grade(session.score().unwrap_or(0.0)),
                    total_files: session.source_files().len(),
                    total_functions: 0, // approximation OK for cached path
                    total_classes: 0,
                    structure_score: None,
                    quality_score: None,
                    architecture_score: None,
                    total_loc: None,
                };

                output_cached_results(
                    no_emoji,
                    quiet,
                    &fail_on,
                    session.findings().to_vec(),
                    &cached_score,
                    format,
                    output_path,
                    start_time,
                    explain_score,
                    &severity,
                    top,
                    page,
                    per_page,
                    &skip_detector,
                    &cache_path(path),
                )?;

                print_final_summary(quiet, no_emoji, start_time);
                return Ok(());
            }

            // Incremental path: update session with changed files
            let inc_start = Instant::now();
            let _delta = session.update(&changed)?;
            let inc_elapsed = inc_start.elapsed();

            let quiet = std::env::var("REPOTOIRE_QUIET").is_ok();
            if !quiet {
                let icon = if no_emoji { "" } else { "⚡ " };
                eprintln!(
                    "\n{}Incremental update: {} files changed ({:.3}s)\n",
                    style(icon).bold(),
                    changed.len(),
                    inc_elapsed.as_secs_f64()
                );
            }

            let cached_score = crate::detectors::CachedScoreResult {
                score: session.score().unwrap_or(0.0),
                grade: session_score_to_grade(session.score().unwrap_or(0.0)),
                total_files: session.source_files().len(),
                total_functions: 0,
                total_classes: 0,
                structure_score: None,
                quality_score: None,
                architecture_score: None,
                total_loc: None,
            };

            output_cached_results(
                no_emoji,
                quiet,
                &fail_on,
                session.findings().to_vec(),
                &cached_score,
                format,
                output_path,
                start_time,
                explain_score,
                &severity,
                top,
                page,
                per_page,
                &skip_detector,
                &cache_path(path),
            )?;

            // Persist updated session for next run
            let _ = session.persist(&session_cache_dir);

            if timings {
                println!(
                    "\nIncremental update: {:.3}s ({} files changed)",
                    inc_elapsed.as_secs_f64(),
                    changed.len()
                );
            }
            print_final_summary(quiet, no_emoji, start_time);
            return Ok(());
        }
    }

    // Phase 1: Validate repository and setup environment
    let phase_start = Instant::now();
    let mut env = setup_environment(
        path,
        format,
        no_emoji,
        run_external,
        no_git,
        workers,
        per_page,
        fail_on,
        incremental,
        since.is_some(),
        skip_graph,
        max_files,
    )?;
    phase_timings.push(("setup", phase_start.elapsed()));

    // Fast path: fully cached results (no changes detected)
    if let Some(result) = try_cached_fast_path(
        &env,
        format,
        output_path,
        &severity,
        top,
        page,
        per_page,
        &skip_detector,
        start_time,
        explain_score,
    )? {
        // Show --verify warning even on cached path (#60)
        if verify {
            let has_claude = std::env::var("ANTHROPIC_API_KEY").is_ok();
            let has_ollama = std::process::Command::new("ollama")
                .arg("list")
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);
            if !has_claude && !has_ollama {
                eprintln!(
                    "\n⚠️  --verify requires an AI backend but none is available.\n\
                     Set ANTHROPIC_API_KEY for Claude, or install Ollama (https://ollama.ai)."
                );
            }
        }
        return Ok(result);
    }

    // Phase 2: Initialize graph, collect files, and optionally run GI detectors
    let phase_start = Instant::now();
    let (graph, file_result, parse_result, early_gi_findings, value_store) =
        initialize_graph(&mut env, &since, &MultiProgress::new(), &skip_detector, timings)?;
    phase_timings.push(("init+parse", phase_start.elapsed()));

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

    // Auto-calibrate if no style profile exists
    let phase_start = Instant::now();
    if env.style_profile.is_none() && !file_result.all_files.is_empty() {
        let parse_pairs: Vec<_> = parse_result
            .parse_results
            .iter()
            .map(|(path, pr)| {
                let loc = std::fs::read_to_string(path)
                    .map(|c| c.lines().count())
                    .unwrap_or(0);
                (crate::parsers::ParseResult::clone(pr), loc)
            })
            .collect();
        let commit_sha = std::process::Command::new("git")
            .args(["rev-parse", "HEAD"])
            .current_dir(&env.repo_path)
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.trim().to_string());
        let profile = crate::calibrate::collect_metrics(
            &parse_pairs,
            file_result.all_files.len(),
            commit_sha,
        );
        let _ = profile.save(&env.repo_path);
        env.style_profile = Some(profile);
        if !env.quiet_mode {
            let icon = if env.config.no_emoji { "" } else { "📐 " };
            println!(
                "{}Auto-calibrated adaptive thresholds ({} functions)",
                icon,
                parse_pairs.len()
            );
        }
    }

    // Build n-gram language model from parsed source files (predictive coding)
    // This learns the project's coding patterns so detectors can flag "surprising" code.
    let ngram_model = build_ngram_model(&parse_result.parse_results);
    if let Some(model) = ngram_model {
        if !env.quiet_mode {
            let icon = if env.config.no_emoji { "" } else { "🧠 " };
            println!(
                "{}Learned coding patterns ({} tokens, {} vocabulary)",
                icon, model.total_tokens(), model.vocab_size()
            );
        }
        env.ngram_model = Some(model);
    }
    phase_timings.push(("calibrate", phase_start.elapsed()));

    // Phase 3: Run detectors
    let phase_start = Instant::now();
    let multi = MultiProgress::new();
    let spinner_style = create_spinner_style();

    let mut findings = execute_detection_phase(
        &env,
        &graph,
        &file_result,
        &skip_detector,
        &multi,
        &spinner_style,
        timings,
        early_gi_findings,
        value_store,
    )?;
    phase_timings.push(("detect", phase_start.elapsed()));

    // Phase 4: Post-process findings
    let phase_start = Instant::now();
    postprocess_findings(
        &mut findings,
        &env.project_config,
        &mut env.incremental_cache,
        env.config.is_incremental_mode,
        &file_result.files_to_parse,
        &file_result.all_files,
        env.config.max_files,
        verify,
        &graph,
        rank,
    );

    // Phase 4b: Export training data (if --export-training)
    if let Some(export_path) = export_training {
        match export::export_training_data(&findings, graph.as_ref(), &env.repo_path, export_path) {
            Ok(count) => {
                if !env.quiet_mode {
                    let icon = if env.config.no_emoji { "" } else { "📊 " };
                    println!("{}Exported {} training samples to {}", icon, count, export_path.display());
                }
            }
            Err(e) => {
                eprintln!("Warning: failed to export training data: {}", e);
            }
        }
    }
    phase_timings.push(("postprocess", phase_start.elapsed()));

    // Phase 5: Calculate scores and build report
    let phase_start = Instant::now();
    let score_result = calculate_scores(&graph, &env.project_config, &findings, &env.repo_path);

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

    // Cache scores for fast path on next run (use env.incremental_cache, not a new instance)
    env.incremental_cache.cache_score_with_subscores(
        score_result.overall_score,
        &score_result.grade,
        file_result.all_files.len(),
        parse_result.total_functions,
        parse_result.total_classes,
        Some(score_result.structure_score),
        Some(score_result.quality_score),
        Some(score_result.architecture_score),
        score_result.total_loc,
    );
    phase_timings.push(("scoring", phase_start.elapsed()));

    // Phase 6: Generate output
    let phase_start = Instant::now();
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
        &env.repo_path,
    )?;

    // Prune stale entries for deleted/renamed files
    env.incremental_cache
        .prune_stale_entries(&file_result.all_files);

    // Cache postprocessed findings for both feedback and incremental fast path (#65)
    let graph_hash = env
        .incremental_cache
        .compute_all_files_hash(&file_result.all_files);
    env.incremental_cache.update_graph_hash(&graph_hash);
    env.incremental_cache
        .cache_graph_findings("__all__", &report.2);
    let _ = env.incremental_cache.save_cache();

    // Fire-and-forget: cache results + findings on background threads.
    // These are pure I/O writes that don't affect the printed output.
    {
        let repotoire_dir = env.repotoire_dir.clone();
        let health_report = report.0.clone();
        let all_findings = report.2.clone();
        std::thread::spawn(move || {
            let _ = cache_results(&repotoire_dir, &health_report, &all_findings);
        });
    }
    {
        let path = path.to_path_buf();
        let all_findings = report.2.clone();
        std::thread::spawn(move || {
            cache_findings(&path, &all_findings);
        });
    }

    // Persist AnalysisSession for future incremental runs (fire-and-forget).
    // This packages the graph, findings, and score into a session that can be
    // loaded on the next `repotoire analyze` for sub-second incremental updates.
    {
        use crate::session::AnalysisSession;
        let session_cache_dir = cache_path(path).join("session");
        let _ = std::fs::create_dir_all(&session_cache_dir);
        let session_graph = Arc::clone(&graph);
        let session_files = file_result.all_files.clone();
        let session_findings = report.2.clone();
        let session_score = score_result.overall_score;
        let session_repo = env.repo_path.clone();
        let session_workers = env.config.workers;
        match AnalysisSession::from_cold_results(
            &session_repo,
            session_workers,
            session_graph,
            session_files,
            session_findings,
            Some(session_score),
        ) {
            Ok(session) => {
                if let Err(e) = session.persist(&session_cache_dir) {
                    tracing::warn!("Failed to persist session: {}", e);
                } else {
                    tracing::info!("Session persisted to {:?}", session_cache_dir);
                }
            }
            Err(e) => {
                eprintln!("Failed to build session from pipeline results: {}", e);
            }
        }
    }

    phase_timings.push(("output", phase_start.elapsed()));

    // Print timing breakdown if requested
    if timings {
        let total = start_time.elapsed();
        println!("\nPhase timings:");
        for (name, dur) in &phase_timings {
            let pct = dur.as_secs_f64() / total.as_secs_f64() * 100.0;
            println!("  {:<16} {:.3}s  ({:.1}%)", name, dur.as_secs_f64(), pct);
        }
        println!("  {:<16} {:.3}s", "TOTAL", total.as_secs_f64());
    }

    // Final summary
    print_final_summary(env.quiet_mode, env.config.no_emoji, start_time);

    // CI/CD threshold check
    check_fail_threshold(&env.config.fail_on, &report.0)?;

    Ok(())
}

// ============================================================================
// Pipeline phases (private orchestration helpers)
// ============================================================================

/// Build an n-gram language model from parsed source files, skipping test/vendor paths.
/// Returns None if the model doesn't have enough data to be confident.
fn build_ngram_model(parse_results: &[(PathBuf, Arc<crate::parsers::ParseResult>)]) -> Option<crate::calibrate::NgramModel> {
    let mut model = crate::calibrate::NgramModel::new();
    for (path, _pr) in parse_results {
        let path_lower = path.to_string_lossy().to_lowercase();
        if path_lower.contains("/test") || path_lower.contains("/vendor")
            || path_lower.contains("/node_modules") || path_lower.contains("/generated")
        {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(path) else { continue };
        let tokens = crate::calibrate::NgramModel::tokenize_file(&content);
        model.train_on_tokens(&tokens);
    }
    model.is_confident().then_some(model)
}

/// Try the fast cache path — returns Some(()) if cache hit, None if cache miss.
fn try_cached_fast_path(
    env: &EnvironmentSetup,
    format: &str,
    output_path: Option<&Path>,
    severity: &Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: &[String],
    start_time: Instant,
    explain_score: bool,
) -> Result<Option<()>> {
    // Fast-path is only safe for full-repo analysis. With --max-files we must
    // run the normal pipeline so file limiting/filtering is applied correctly.
    if env.config.max_files > 0 {
        return Ok(None);
    }

    let mut cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
    let all_files = collect_file_list(&env.repo_path, &env.project_config.exclude)?;

    if !cache.has_complete_cache(&all_files) {
        return Ok(None);
    }

    let findings = load_cached_findings(&env.repotoire_dir)
        .unwrap_or_else(|| cache.all_cached_graph_findings());
    let cached_score = match cache.cached_score() {
        Some(s) => s,
        None => return Ok(None),
    };

    if !env.quiet_mode {
        let icon = if env.config.no_emoji { "" } else { "⚡ " };
        println!(
            "\n{}Using fully cached results (no changes detected)\n",
            style(icon).bold()
        );
    }

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
        severity,
        top,
        page,
        per_page,
        skip_detector,
        &env.repotoire_dir,
    )?;

    Ok(Some(()))
}

/// Phase 2: Initialize graph database, collect files, and parse.
///
/// Returns `(graph, files, parse_result, Option<gi_findings>)`.
/// When the overlapped pipeline is used, GI detectors run in parallel with
/// parse+graph, so `gi_findings` is Some. Otherwise None.
fn initialize_graph(
    env: &mut EnvironmentSetup,
    since: &Option<String>,
    multi: &MultiProgress,
    skip_detector: &[String],
    timings: bool,
) -> Result<(Arc<GraphStore>, FileCollectionResult, ParsePhaseResult, Option<Vec<Finding>>, Option<Arc<crate::values::store::ValueStore>>)> {
    let spinner_style = create_spinner_style();
    let bar_style = create_bar_style();

    // Overlapped walk+parse+GI: when in full mode (no --since, no incremental, no
    // skip-graph, no max-files), we overlap file discovery with parsing AND
    // run GI detectors in parallel with parse. GI detectors only need the file
    // list (not the graph), so they can start ~100ms into the pipeline.
    let can_overlap = since.is_none()
        && !env.config.is_incremental_mode
        && !env.config.skip_graph
        && env.config.max_files == 0;

    if can_overlap {
        return initialize_graph_overlapped(env, multi, &bar_style, skip_detector, timings);
    }

    // Standard sequential path: walk first, then parse.
    // Collect files
    let mut cache_clone = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
    let mut file_result = collect_files_for_analysis(
        &env.repo_path,
        since,
        env.config.is_incremental_mode,
        &mut cache_clone,
        multi,
        &spinner_style,
        &env.project_config.exclude,
    )?;

    // Apply max_files limit
    apply_max_files_limit(
        &mut file_result,
        env.config.max_files,
        env.quiet_mode,
        env.config.no_emoji,
    );

    if file_result.all_files.is_empty() {
        return Ok((
            Arc::new(GraphStore::in_memory()),
            file_result,
            ParsePhaseResult {
                parse_results: vec![],
                total_functions: 0,
                total_classes: 0,
            },
            None,
            None,
        ));
    }

    if file_result.files_to_parse.is_empty() && env.config.is_incremental_mode && !env.quiet_mode {
        let check_icon = if env.config.no_emoji { "" } else { "✓ " };
        println!(
            "\n{}No files changed since last run. Using cached results.",
            style(check_icon).green()
        );
    }

    // Skip graph mode
    if env.config.skip_graph {
        if !env.quiet_mode {
            println!(
                "{}Skipping graph building (--skip-graph or --lite mode)",
                style("⏭ ").dim()
            );
        }

        let graph = Arc::new(GraphStore::in_memory());
        let parse_result = parse_files_lite(&file_result.files_to_parse, multi, &bar_style)?;

        return Ok((graph, file_result, parse_result, None, None));
    }

    // Initialize graph database
    let graph = init_graph_db(env, &file_result, multi)?;

    // Parse files and build graph
    let (parse_result, value_store) = parse_and_build(env, &file_result, &graph, multi, &bar_style)?;

    // Release build-phase caches (edge_set ~1.8MB) now that graph is complete
    graph.clear_build_caches();

    // Save graph cache for future incremental runs (background thread).
    // Guard: skip if incremental mode with no changed files (cache is already warm).
    if !file_result.files_to_parse.is_empty() || !env.config.is_incremental_mode {
        spawn_graph_cache_save(&graph, &env.repotoire_dir);
    }

    Ok((graph, file_result, parse_result, None, value_store))
}

/// Overlapped walk+parse+GI: walk, parse, and run GI detectors concurrently.
///
/// Three-way overlap:
///   1. Walker discovers files → sends to parse channel + publishes file list
///   2. Parse pipeline consumes from channel → builds graph (3.8s)
///   3. GI detectors start as soon as file list is ready (~100ms) and run in
///      parallel with parse (1.8s), finishing well before parse completes
///
/// Timeline:
///   [Walk 100ms] ─→ [early_files published]
///                    ├→ [Parse + Graph Build 3.7s] ─────────────→ graph ready
///                    └→ [GI detectors 1.8s] ─→ done (overlapped)
///
/// This removes GI from the critical path: GI runs "for free" during parse.
fn initialize_graph_overlapped(
    env: &mut EnvironmentSetup,
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    skip_detector: &[String],
    timings: bool,
) -> Result<(Arc<GraphStore>, FileCollectionResult, ParsePhaseResult, Option<Vec<Finding>>, Option<Arc<crate::values::store::ValueStore>>)> {
    use crate::parsers::bounded_pipeline::PipelineConfig;

    if !env.quiet_mode {
        let stream_icon = if env.config.no_emoji { "" } else { "🌊 " };
        println!(
            "{}Overlapped walk+parse+GI mode (streaming)",
            style(stream_icon).bold(),
        );
    }

    // Initialize graph database eagerly (fast on empty DB).
    let db_path = env.repotoire_dir.join("graph_db");
    if !env.quiet_mode {
        let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
        println!(
            "{}Initializing graph database...",
            style(icon_graph).bold(),
        );
    }
    let graph = Arc::new(
        GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?,
    );

    let config = PipelineConfig::default();
    let (file_tx, file_rx) = crossbeam_channel::bounded::<PathBuf>(config.buffer_size);

    // OnceLock for early file list publication — walker sets this as soon as
    // all files are discovered, allowing GI detectors to start immediately.
    let early_files: Arc<std::sync::OnceLock<Vec<PathBuf>>> =
        Arc::new(std::sync::OnceLock::new());
    let early_files_for_walker = Arc::clone(&early_files);

    let repo_path = env.repo_path.clone();
    let exclude = env.project_config.exclude.clone();
    let walk_handle = std::thread::spawn(move || {
        walk_files_to_channel(&repo_path, &exclude, file_tx, Some(early_files_for_walker))
    });

    // Borrow references for thread::scope closures
    let repo_path_ref = &env.repo_path;
    let project_config_ref = &env.project_config;
    let style_profile_ref = env.style_profile.as_ref();
    let ngram_clone = env.ngram_model.clone();
    let workers = env.config.workers;

    // Run parse pipeline + GI detectors in parallel via thread::scope.
    // thread::scope ensures all threads complete before we proceed, and
    // allows borrowing from the enclosing scope.
    let (parse_result_inner, gi_findings) = std::thread::scope(|s| {
        // Thread 1: Parse pipeline — consumes files from channel, builds graph
        let graph_for_parse = Arc::clone(&graph);
        let parse_handle = s.spawn(move || {
            parse_and_build_streaming_overlapped(
                file_rx,
                repo_path_ref,
                graph_for_parse,
                multi,
                bar_style,
                config,
            )
        });

        // Thread 2: GI detectors — wait for early_files, then run
        let gi_handle = s.spawn(|| {
            // Spin-wait for file list (walker finishes in ~100ms)
            let files = loop {
                if let Some(f) = early_files.get() {
                    break f;
                }
                std::thread::sleep(std::time::Duration::from_millis(2));
            };

            run_gi_detectors(
                files,
                repo_path_ref,
                project_config_ref,
                skip_detector,
                style_profile_ref,
                ngram_clone,
                workers,
                timings,
            )
        });

        let parse_res = parse_handle.join().expect("parse thread panicked");
        let gi_res = gi_handle.join().expect("GI thread panicked");
        (parse_res, gi_res)
    });

    let (total_functions, total_classes) = parse_result_inner?;
    let gi_findings = gi_findings?;
    let gi_count = gi_findings.len();

    let all_files = walk_handle
        .join()
        .map_err(|_| anyhow::anyhow!("Walk thread panicked"))?
        .context("File walk failed")?;

    if !env.quiet_mode {
        println!(
            "{}Found {} source files, {} GI findings (overlapped with parse)",
            style("✓ ").green(),
            style(all_files.len()).cyan(),
            style(gi_count).cyan(),
        );
    }

    let file_result = FileCollectionResult {
        files_to_parse: all_files.clone(),
        all_files,
        cached_findings: Vec::new(),
    };

    let parse_result = ParsePhaseResult {
        parse_results: vec![],
        total_functions,
        total_classes,
    };

    // Release build-phase caches (edge_set ~1.8MB) now that graph is complete
    graph.clear_build_caches();

    // Save graph cache for future incremental runs (background thread).
    // The overlapped path always builds a fresh graph, so always save.
    spawn_graph_cache_save(&graph, &env.repotoire_dir);

    Ok((graph, file_result, parse_result, Some(gi_findings), None))
}

/// Phase 3: Run git enrichment and detectors.
///
/// Uses speculative execution for normal repos: graph-independent detectors run
/// in parallel with git enrichment, then graph-dependent detectors run after
/// git enrichment completes. This overlaps file-local detection with background
/// git history processing.
///
/// When `pre_gi_findings` is Some, the GI phase was already executed during
/// parse (Parse ∥ GI overlap). The detection phase skips GI and only runs
/// GD pre-compute + GD parallel detectors.
fn execute_detection_phase(
    env: &EnvironmentSetup,
    graph: &Arc<GraphStore>,
    file_result: &FileCollectionResult,
    skip_detector: &[String],
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    timings: bool,
    pre_gi_findings: Option<Vec<Finding>>,
    value_store: Option<Arc<crate::values::store::ValueStore>>,
) -> Result<Vec<Finding>> {
    // Speculative execution path:
    // 1. Start git enrichment (background thread)
    // 2. Run graph-independent detectors NOW (parallel with git enrichment)
    // 3. Finish git enrichment (wait for background thread)
    // 4. Run graph-dependent detectors (they need the enriched graph)
    // 5. Merge findings + apply voting

    let git_handle = start_git_enrichment(
        env.config.no_git,
        env.quiet_mode,
        &env.repo_path,
        Arc::clone(graph),
        multi,
        spinner_style,
    );

    let mut detector_cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));

    let mut findings = run_detectors_speculative(
        graph,
        &env.repo_path,
        &env.project_config,
        skip_detector,
        env.config.run_external,
        env.config.workers,
        multi,
        spinner_style,
        env.quiet_mode,
        env.config.no_emoji,
        &mut detector_cache,
        &file_result.all_files,
        env.style_profile.as_ref(),
        env.ngram_model.clone(),
        timings,
        || finish_git_enrichment(git_handle),
        pre_gi_findings,
        value_store,
    )?;

    let (_voting_stats, _cached_count) = apply_voting(
        &mut findings,
        file_result.cached_findings.clone(),
        env.config.is_incremental_mode,
        multi,
        spinner_style,
    );

    Ok(findings)
}

/// Phase 6: Generate and output reports.
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
    project_config: &crate::config::ProjectConfig,
    repo_path: &Path,
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

    if explain_score {
        let scorer = crate::scoring::GraphScorer::new(graph, project_config, repo_path);
        let explanation = scorer.explain(&score_result.breakdown);
        match format {
            "json" => {
                let explain_json = build_explain_json(&explanation, &score_result.breakdown);
                eprintln!(
                    "{}",
                    serde_json::to_string_pretty(&explain_json).unwrap_or_default()
                );
            }
            _ => {
                println!("\n{}", style("─".repeat(60)).dim());
                println!("{}", explanation);
            }
        }
    }

    Ok(())
}

// ============================================================================
// Internal helpers
// ============================================================================

/// Map a numeric health score to a letter grade string.
///
/// Used by the session-based incremental path where we don't have the full
/// `GraphScorer` available. Matches the grade boundaries in `graph_scorer.rs`.
fn session_score_to_grade(score: f64) -> String {
    match score as u32 {
        97..=100 => "A+",
        93..=96 => "A",
        90..=92 => "A-",
        87..=89 => "B+",
        83..=86 => "B",
        80..=82 => "B-",
        77..=79 => "C+",
        73..=76 => "C",
        70..=72 => "C-",
        60..=69 => "D+",
        50..=59 => "D",
        40..=49 => "D-",
        _ => "F",
    }
    .to_string()
}

/// Spawn a background thread to save the graph cache.
///
/// The thread handle is intentionally detached (fire-and-forget). This is safe
/// because `save_graph_cache` uses atomic write-to-temp-then-rename: the worst
/// case on early process exit is that no cache file is saved (a stale `.bin.tmp`
/// may remain), never a corrupt cache.
fn spawn_graph_cache_save(graph: &Arc<GraphStore>, repotoire_dir: &Path) {
    let cache_path = repotoire_dir.join("graph_cache.bin");
    let graph_for_cache = Arc::clone(graph);
    std::thread::spawn(move || {
        let start = std::time::Instant::now();
        if let Err(e) = graph_for_cache.save_graph_cache(&cache_path) {
            tracing::warn!("Failed to save graph cache: {}", e);
        } else {
            tracing::debug!("Graph cache saved in {:?}", start.elapsed());
        }
    });
}

/// Apply max_files limit to file collection result.
fn apply_max_files_limit(
    file_result: &mut FileCollectionResult,
    max_files: usize,
    quiet_mode: bool,
    no_emoji: bool,
) {
    if max_files == 0 || file_result.all_files.len() <= max_files {
        return;
    }

    if !quiet_mode {
        let warn_icon = if no_emoji { "" } else { "⚠️  " };
        println!(
            "{}Limiting analysis to {} files (out of {} total) to reduce memory usage",
            style(warn_icon).yellow(),
            style(max_files).cyan(),
            style(file_result.all_files.len()).dim()
        );
    }

    file_result.all_files.truncate(max_files);
    let all_set: std::collections::HashSet<_> = file_result.all_files.iter().collect();
    file_result.files_to_parse.retain(|f| all_set.contains(f));
    if file_result.files_to_parse.len() > max_files {
        file_result.files_to_parse.truncate(max_files);
    }
    file_result
        .cached_findings
        .retain(|f| f.affected_files.iter().any(|p| all_set.contains(p)));
}

/// Initialize graph database (lazy mode for 20k+ files).
///
/// In incremental mode, tries to load a persistent graph cache first and
/// delta-patches it by removing entities for changed files. Falls back to
/// creating a fresh graph when no cache exists.
fn init_graph_db(
    env: &EnvironmentSetup,
    file_result: &FileCollectionResult,
    multi: &MultiProgress,
) -> Result<Arc<GraphStore>> {
    let db_path = env.repotoire_dir.join("graph_db");
    let cache_path = env.repotoire_dir.join("graph_cache.bin");

    // Try loading persistent graph cache for incremental mode
    if env.config.is_incremental_mode {
        if let Some(cached_store) = GraphStore::load_graph_cache(&cache_path) {
            // Delta patch: remove entities for changed files so re-parse can add updated ones
            if !file_result.files_to_parse.is_empty() {
                tracing::info!(
                    "Delta patching graph: removing {} changed files",
                    file_result.files_to_parse.len()
                );
                cached_store.remove_file_entities(&file_result.files_to_parse);
            }
            if !env.quiet_mode {
                let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
                println!(
                    "{}Loaded graph cache ({} files delta-patched)",
                    style(icon_graph).bold(),
                    file_result.files_to_parse.len()
                );
            }
            return Ok(Arc::new(cached_store));
        }
    }

    // Cold path: create fresh graph
    let use_lazy = file_result.all_files.len() > 20000;

    if !env.quiet_mode {
        let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
        let mode_info = if use_lazy { " (lazy mode)" } else { "" };
        let _ = multi; // suppress unused warning
        println!(
            "{}Initializing graph database{}...",
            style(icon_graph).bold(),
            style(mode_info).dim()
        );
    }

    let graph = if use_lazy {
        Arc::new(
            GraphStore::new_lazy(&db_path)
                .with_context(|| "Failed to initialize graph database")?,
        )
    } else {
        Arc::new(GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?)
    };

    Ok(graph)
}

/// Parse files and build graph, choosing strategy based on repo size.
fn parse_and_build(
    env: &EnvironmentSetup,
    file_result: &FileCollectionResult,
    graph: &Arc<GraphStore>,
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<(ParsePhaseResult, Option<Arc<crate::values::store::ValueStore>>)> {
    let use_streaming = file_result.files_to_parse.len() > 2000;

    if use_streaming {
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
            Arc::clone(graph),
            multi,
            bar_style,
        )?;

        Ok((ParsePhaseResult {
            parse_results: vec![],
            total_functions,
            total_classes,
        }, None))
    } else {
        // Build a lock-free concurrent cache view for parallel parsing.
        // Pre-validates cached entries so the par_iter loop needs no Mutex.
        let mut parse_cache = IncrementalCache::new(
            &env.repotoire_dir.join("incremental"),
        );
        let cache_view = parse_cache.concurrent_view(&file_result.files_to_parse);
        let new_results = dashmap::DashMap::new();

        let result = if file_result.files_to_parse.len() > 10000 {
            parse_files_chunked(
                &file_result.files_to_parse,
                multi,
                bar_style,
                env.config.is_incremental_mode,
                &cache_view,
                &new_results,
                5000,
            )?
        } else {
            parse_files(
                &file_result.files_to_parse,
                multi,
                bar_style,
                env.config.is_incremental_mode,
                &cache_view,
                &new_results,
            )?
        };

        // Merge newly parsed results back into the persistent cache
        parse_cache.merge_new_parse_results(new_results);
        let _ = parse_cache.save_cache();

        // Build graph and construct ValueStore with cross-function propagation.
        let value_store = if result.parse_results.len() > 10000 {
            build_graph_chunked(
                graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                bar_style,
                5000,
            )?
        } else {
            build_graph(
                graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                bar_style,
            )?
        };

        Ok((result, Some(Arc::new(value_store))))
    }
}

/// Cache scores for fast path on next run.
fn build_explain_json(explanation: &str, bd: &crate::scoring::ScoreBreakdown) -> serde_json::Value {
    fn pillar_json(p: &crate::scoring::PillarBreakdown) -> serde_json::Value {
        serde_json::json!({
            "score": p.final_score,
            "base": p.base_score,
            "penalty": p.penalty_points,
            "findings": p.finding_count,
        })
    }
    serde_json::json!({
        "explanation": explanation,
        "breakdown": {
            "overall_score": bd.overall_score,
            "grade": &bd.grade,
            "kloc": bd.graph_metrics.total_loc as f64 / 1000.0,
            "structure": pillar_json(&bd.structure),
            "quality": pillar_json(&bd.quality),
            "architecture": pillar_json(&bd.architecture),
        }
    })
}

/// Cache findings for the feedback command.
fn cache_findings(path: &Path, findings: &[Finding]) {
    let cache_path = cache_path(path);
    if let Err(e) = std::fs::create_dir_all(&cache_path) {
        tracing::warn!(
            "Failed to create cache directory {}: {}",
            cache_path.display(),
            e
        );
    }
    let findings_cache = cache_path.join("findings.json");
    if let Ok(json) = serde_json::to_string(findings) {
        if let Err(e) = std::fs::write(&findings_cache, &json) {
            tracing::warn!(
                "Failed to write findings cache {}: {}",
                findings_cache.display(),
                e
            );
        }
    }
}

/// Print final summary message.
fn print_final_summary(quiet_mode: bool, no_emoji: bool, start_time: Instant) {
    if !quiet_mode {
        let elapsed = start_time.elapsed();
        let icon_done = if no_emoji { "" } else { "✨ " };
        eprintln!(
            "\n{}Analysis complete in {:.2}s",
            style(icon_done).bold(),
            elapsed.as_secs_f64()
        );
    }
}

/// Convert PascalCase or camelCase to kebab-case (e.g. "TodoScanner" → "todo-scanner").
fn normalize_to_kebab(s: &str) -> String {
    if s.contains('-') {
        return s.to_lowercase();
    }
    let mut result = String::with_capacity(s.len() + 4);
    for (i, ch) in s.chars().enumerate() {
        if ch.is_uppercase() && i > 0 {
            result.push('-');
        }
        result.push(ch.to_ascii_lowercase());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_pascal_case() {
        assert_eq!(normalize_to_kebab("TodoScanner"), "todo-scanner");
        assert_eq!(normalize_to_kebab("DeadCodeDetector"), "dead-code-detector");
        assert_eq!(
            normalize_to_kebab("AIComplexitySpike"),
            "a-i-complexity-spike"
        );
    }

    #[test]
    fn test_normalize_already_kebab() {
        assert_eq!(normalize_to_kebab("todo-scanner"), "todo-scanner");
        assert_eq!(normalize_to_kebab("dead-code"), "dead-code");
    }

    #[test]
    fn test_normalize_lowercase() {
        assert_eq!(normalize_to_kebab("simple"), "simple");
    }

    #[test]
    fn test_normalize_mixed_case_kebab() {
        assert_eq!(normalize_to_kebab("Todo-Scanner"), "todo-scanner");
    }
}
