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
    default_detectors, DetectorEngine, Detector, IncrementalCache,
    VotingEngine, VotingStrategy, ConfidenceMethod, SeverityResolution,
};
use crate::git;
use crate::graph::{GraphStore, CodeNode, CodeEdge, NodeKind};
use crate::models::{Finding, FindingsSummary, HealthReport, Severity};
use crate::parsers::{parse_file, ParseResult};
use crate::reporters;

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::Instant;

/// Supported file extensions for analysis
const SUPPORTED_EXTENSIONS: &[&str] = &[
    "py", "pyi", // Python
    "ts", "tsx", // TypeScript
    "js", "jsx", "mjs", // JavaScript
    "rs",  // Rust
    "go",  // Go
    "java", // Java
    "c", "h", // C
    "cpp", "hpp", "cc", // C++
    "cs",  // C#
    "kt", "kts", // Kotlin
    "rb",  // Ruby
    "php", // PHP
    "swift", // Swift
];

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
) -> Result<()> {
    let start_time = Instant::now();

    // Validate repository path
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Repository path does not exist: {}", path.display()))?;

    if !repo_path.is_dir() {
        anyhow::bail!("Path is not a directory: {}", repo_path.display());
    }

    // Load project configuration (repotoire.toml, .repotoirerc.json, etc.)
    let project_config = load_project_config(&repo_path);
    
    // Apply CLI defaults from project config (CLI args override config)
    let no_emoji = no_emoji || project_config.defaults.no_emoji.unwrap_or(false);
    let thorough = thorough || project_config.defaults.thorough.unwrap_or(false);
    let no_git = no_git || project_config.defaults.no_git.unwrap_or(false);
    let workers = if workers == 8 { // 8 is the CLI default
        project_config.defaults.workers.unwrap_or(workers)
    } else {
        workers
    };
    let per_page = if per_page == 20 { // 20 is the CLI default
        project_config.defaults.per_page.unwrap_or(per_page)
    } else {
        per_page
    };
    let fail_on = fail_on.or_else(|| project_config.defaults.fail_on.clone());

    // Header with optional emoji
    let icon_analyze = if no_emoji { "" } else { "🎼 " };
    let icon_search = if no_emoji { "" } else { "🔍 " };
    
    println!("\n{}Repotoire Analysis\n", style(icon_analyze).bold());
    println!(
        "{}Analyzing: {}\n",
        style(icon_search).bold(),
        style(repo_path.display()).cyan()
    );

    // Create cache directory (~/.cache/repotoire/<repo-hash>/)
    let repotoire_dir = crate::cache::ensure_cache_dir(&repo_path)
        .with_context(|| "Failed to create cache directory")?;

    // Initialize incremental cache for file fingerprinting
    let incremental_cache_dir = repotoire_dir.join("incremental");
    let mut incremental_cache = IncrementalCache::new(&incremental_cache_dir);
    let is_incremental_mode = incremental || since.is_some();

    // Initialize graph database
    let db_path = repotoire_dir.join("graph_db");
    let icon_graph = if no_emoji { "" } else { "🕸️  " };
    println!("{}Initializing graph database...", style(icon_graph).bold());
    let graph = Arc::new(GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?);
    let graph_ref = &graph; // For local usage

    // Set up progress bars
    let multi = MultiProgress::new();
    let spinner_style = ProgressStyle::default_spinner()
        .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")
        .template("{spinner:.green} {msg}")
        .unwrap();
    let bar_style = ProgressStyle::default_bar()
        .template("{spinner:.green} [{bar:40.cyan/blue}] {pos}/{len} {msg}")
        .unwrap()
        .progress_chars("█▓▒░  ");

    // Step 1: Walk repository and collect files
    let walk_spinner = multi.add(ProgressBar::new_spinner());
    walk_spinner.set_style(spinner_style.clone());
    
    // Determine which files to analyze based on incremental mode
    let (all_files, files_to_parse, cached_findings) = if let Some(ref commit) = since {
        // --since mode: only analyze files changed since specified commit
        walk_spinner.set_message(format!("Finding files changed since {}...", commit));
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));
        
        let changed = get_changed_files_since(&repo_path, commit)?;
        let all = collect_source_files(&repo_path)?;
        
        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files (since {}) out of {} total",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(commit).yellow(),
            style(all.len()).dim()
        ));
        
        // Get cached findings for unchanged files
        let unchanged: Vec<_> = all.iter()
            .filter(|f| !changed.contains(f))
            .cloned()
            .collect();
        let mut cached: Vec<Finding> = Vec::new();
        for file in &unchanged {
            cached.extend(incremental_cache.get_cached_findings(file));
        }
        
        (all, changed, cached)
    } else if incremental {
        // --incremental mode: only analyze files changed since last run
        walk_spinner.set_message("Discovering source files (incremental mode)...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));
        
        let all = collect_source_files(&repo_path)?;
        let changed = incremental_cache.get_changed_files(&all);
        let cache_stats = incremental_cache.get_stats();
        
        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files out of {} total ({} cached)",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(all.len()).dim(),
            style(cache_stats.cached_files).dim()
        ));
        
        // Get cached findings for unchanged files
        let unchanged: Vec<_> = all.iter()
            .filter(|f| !changed.contains(f))
            .cloned()
            .collect();
        let mut cached: Vec<Finding> = Vec::new();
        for file in &unchanged {
            cached.extend(incremental_cache.get_cached_findings(file));
        }
        
        (all, changed, cached)
    } else {
        // Full mode: analyze all files
        walk_spinner.set_message("Discovering source files...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));
        
        let files = collect_source_files(&repo_path)?;
        walk_spinner.finish_with_message(format!(
            "{}Found {} source files",
            style("✓ ").green(),
            style(files.len()).cyan()
        ));
        
        (files.clone(), files, Vec::new())
    };

    // Use all_files for total count, files_to_parse for actual parsing
    let files = files_to_parse;

    if all_files.is_empty() {
        println!("\n{}No source files found to analyze.", style("⚠️  ").yellow());
        return Ok(());
    }

    if files.is_empty() && is_incremental_mode {
        println!("\n{}No files changed since last run. Using cached results.", style("✓ ").green());
        // Still need to output cached findings - continue with empty parse results
    }

    // Step 2: Parse files in parallel using rayon
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    let parse_msg = if is_incremental_mode {
        "Parsing changed files (parallel)..."
    } else {
        "Parsing files (parallel)..."
    };
    parse_bar.set_message(parse_msg);

    use rayon::prelude::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    
    let counter = AtomicUsize::new(0);
    let total_files = files.len();
    
    // Parse files in parallel
    let parse_results: Vec<(std::path::PathBuf, ParseResult)> = files
        .par_iter()
        .filter_map(|file_path| {
            let count = counter.fetch_add(1, Ordering::Relaxed);
            if count % 100 == 0 {
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

    // Step 3: Insert into graph database (batched for performance)
    let graph_bar = multi.add(ProgressBar::new(parse_results.len() as u64));
    graph_bar.set_style(bar_style.clone());
    graph_bar.set_message("Building code graph...");

    // Collect all nodes first, then batch insert
    let mut file_nodes = Vec::with_capacity(parse_results.len());
    let mut func_nodes = Vec::with_capacity(total_functions);
    let mut class_nodes = Vec::with_capacity(total_classes);
    let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();
    
    // Build a global function lookup: function name -> qualified_name
    // This helps resolve cross-file function calls
    let mut global_func_map: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for (_, result) in &parse_results {
        for func in &result.functions {
            // Map simple name to qualified name (last one wins for duplicates)
            global_func_map.insert(func.name.clone(), func.qualified_name.clone());
        }
    }

    for (file_path, result) in &parse_results {
        let relative_path = file_path.strip_prefix(&repo_path).unwrap_or(file_path);
        let relative_str = relative_path.display().to_string();
        let language = detect_language(file_path);
        let loc = count_lines(file_path).unwrap_or(0);

        // File node
        file_nodes.push(
            CodeNode::new(NodeKind::File, &relative_str, &relative_str)
                .with_qualified_name(&relative_str)
                .with_language(&language)
                .with_property("loc", loc as i64)
        );

        // Function nodes
        for func in &result.functions {
            let loc = if func.line_end >= func.line_start {
                func.line_end - func.line_start + 1
            } else { 1 };
            let complexity = func.complexity.unwrap_or(1);
            
            func_nodes.push(
                CodeNode::new(NodeKind::Function, &func.name, &relative_str)
                    .with_qualified_name(&func.qualified_name)
                    .with_lines(func.line_start, func.line_end)
                    .with_property("is_async", func.is_async)
                    .with_property("complexity", complexity as i64)
                    .with_property("loc", loc as i64)
            );
            edges.push((relative_str.clone(), func.qualified_name.clone(), CodeEdge::contains()));
        }

        // Class nodes
        for class in &result.classes {
            class_nodes.push(
                CodeNode::new(NodeKind::Class, &class.name, &relative_str)
                    .with_qualified_name(&class.qualified_name)
                    .with_lines(class.line_start, class.line_end)
                    .with_property("methodCount", class.methods.len() as i64)
            );
            edges.push((relative_str.clone(), class.qualified_name.clone(), CodeEdge::contains()));
        }

        // Call edges - look up callee's full qualified_name
        for (caller, callee) in &result.calls {
            // Extract the module path and function name
            // e.g., "text::render" -> module="text", func="render"
            // e.g., "Self::method" -> module="Self", func="method"
            let parts: Vec<&str> = callee.rsplitn(2, "::").collect();
            let callee_name = parts[0];
            let callee_module = if parts.len() > 1 { Some(parts[1]) } else { None };
            
            // Also handle method calls like "self.method" or "obj.method"
            let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);
            
            // Try to find the callee function in this file first
            let callee_qn = if let Some(callee_func) = result.functions.iter().find(|f| f.name == callee_name) {
                callee_func.qualified_name.clone()
            } else {
                // For module::func calls (like text::render), try to find in that module's file
                let mut found = None;
                if let Some(module) = callee_module {
                    // Look for file matching the module name (e.g., "text" -> "text.rs")
                    for (other_path, other_result) in &parse_results {
                        let other_relative = other_path.strip_prefix(&repo_path).unwrap_or(other_path);
                        let other_str = other_relative.display().to_string();
                        let file_stem = other_relative.file_stem()
                            .and_then(|s| s.to_str())
                            .unwrap_or("");
                        
                        // Check if this file matches the module name
                        if file_stem == module || other_str.contains(&format!("/{}.rs", module)) {
                            if let Some(func) = other_result.functions.iter().find(|f| f.name == callee_name) {
                                found = Some(func.qualified_name.clone());
                                break;
                            }
                        }
                    }
                }
                
                // Fall back to global lookup
                if found.is_none() {
                    found = global_func_map.get(callee_name).cloned();
                }
                
                match found {
                    Some(qn) => qn,
                    None => continue, // External function, skip
                }
            };
            edges.push((caller.clone(), callee_qn, CodeEdge::calls()));
        }

        // Import edges - resolve imports to actual file paths
        // Type-only imports are marked separately for coupling analysis
        for import_path in &result.imports {
            // Handle different import styles:
            // - TypeScript/JS: './utils', '../lib/helper'
            // - Rust: 'crate::module::item', 'super::sibling'
            let clean_import = import_path
                .trim_start_matches("./")
                .trim_start_matches("../")
                .trim_start_matches("crate::")
                .trim_start_matches("super::");
            
            // For Rust, extract the module path (first component after crate/super)
            // e.g., "crate::detectors::base" -> "detectors"
            let module_parts: Vec<&str> = clean_import.split("::").collect();
            let first_module = module_parts.first().copied().unwrap_or("");
            
            for (other_file, _) in &parse_results {
                let other_relative = other_file.strip_prefix(&repo_path).unwrap_or(other_file);
                let other_str = other_relative.display().to_string();
                if other_str == relative_str {
                    continue; // Skip self
                }
                
                let other_name = other_relative.file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("");
                
                // Match strategies:
                // 1. Direct path match: './utils' -> 'utils.ts'
                // 2. Rust module: 'crate::detectors::base' -> 'src/detectors/base.rs'
                // 3. Rust mod.rs: 'crate::detectors' -> 'src/detectors/mod.rs'
                // Python: convert dots to slashes (nanochat.gpt -> nanochat/gpt)
                let python_path = clean_import.replace('.', "/");
                
                let matches = 
                    other_str.contains(clean_import) ||
                    (clean_import == other_name) ||
                    // TypeScript patterns
                    other_str.ends_with(&format!("{}.ts", clean_import)) ||
                    other_str.ends_with(&format!("{}.tsx", clean_import)) ||
                    other_str.ends_with(&format!("{}.js", clean_import)) ||
                    other_str.ends_with(&format!("{}/index.ts", clean_import)) ||
                    // Rust patterns: convert :: to /
                    other_str.ends_with(&format!("{}.rs", clean_import.replace("::", "/"))) ||
                    other_str.ends_with(&format!("{}/mod.rs", first_module)) ||
                    (other_name == first_module && other_str.ends_with(".rs")) ||
                    // Python patterns: convert dots to slashes
                    other_str.ends_with(&format!("{}.py", python_path)) ||
                    other_str.contains(&format!("{}/", python_path)) ||
                    other_str.ends_with(&format!("{}/__init__.py", python_path));
                
                if matches {
                    // TODO: Add is_type_only property when parsers support it
                    // Type-only imports (e.g., TypeScript's `import type`) don't create runtime dependencies
                    let import_edge = CodeEdge::imports();
                    edges.push((relative_str.clone(), other_str, import_edge));
                    break;
                }
            }
        }
        graph_bar.inc(1);
    }

    // Batch insert all nodes (single lock acquisition per batch)
    graph_bar.set_message("Inserting nodes...");
    graph.add_nodes_batch(file_nodes);
    graph.add_nodes_batch(func_nodes);
    graph.add_nodes_batch(class_nodes);
    
    // Batch insert all edges
    graph_bar.set_message("Inserting edges...");
    graph.add_edges_batch(edges);

    graph_bar.finish_with_message(format!("{}Built code graph", style("✓ ").green(),));

    // Persist graph to disk so other commands (stats, graph, findings) can access it
    graph.save().with_context(|| "Failed to save graph database")?;

    // Save graph stats to a separate JSON file (avoids sled lock issues)
    let graph_stats = serde_json::json!({
        "total_files": graph.get_files().len(),
        "total_functions": graph.get_functions().len(),
        "total_classes": graph.get_classes().len(),
        "total_nodes": graph.node_count(),
        "total_edges": graph.edge_count(),
        "calls": graph.get_calls().len(),
        "imports": graph.get_imports().len(),
    });
    let stats_path = crate::cache::get_graph_stats_path(&repo_path);
    std::fs::write(&stats_path, serde_json::to_string_pretty(&graph_stats)?)?;

    // Step 4 & 5: Run git enrichment and detectors IN PARALLEL
    // Git enrichment updates graph properties while detectors read graph structure
    // Both are safe due to RwLock on GraphStore
    
    // Pre-warm file cache for faster detector execution
    crate::cache::warm_global_cache(&repo_path, SUPPORTED_EXTENSIONS);

    let git_result = if !no_git {
        let git_spinner = multi.add(ProgressBar::new_spinner());
        git_spinner.set_style(spinner_style.clone());
        git_spinner.set_message("Enriching with git history (async)...");
        git_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        // Run git enrichment in a background thread
        let repo_path_clone = repo_path.clone();
        let graph_clone = Arc::clone(&graph);
        let git_handle = std::thread::spawn(move || {
            git::enrichment::enrich_graph_with_git(&repo_path_clone, &graph_clone, None)
        });
        Some((git_handle, git_spinner))
    } else {
        println!("{}Skipping git enrichment (--no-git)", style("⏭ ").dim());
        None
    };

    // Step 5: Run detectors (in parallel with git enrichment)
    println!("\n{}Running detectors...", style("🕵️  ").bold());

    let mut engine = DetectorEngine::new(workers);

    // Register all default detectors (skip any in skip_detector list)
    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();

    for detector in default_detectors(&repo_path) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // In thorough mode, add external tool detectors (Bandit, Ruff, ESLint, etc.)
    if thorough {
        let external = crate::detectors::all_external_detectors(&repo_path);
        let external_count = external.len();
        for detector in external {
            engine.register(detector);
        }
        tracing::info!("Thorough mode: added {} external detectors ({} total)", external_count, engine.detector_count());
    }

    let detector_bar = multi.add(ProgressBar::new_spinner());
    detector_bar.set_style(spinner_style.clone());
    detector_bar.set_message("Running detectors...");
    detector_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    let findings = engine.run(&graph)?;

    detector_bar.finish_with_message(format!(
        "{}Ran {} detectors, found {} raw issues",
        style("✓ ").green(),
        style(engine.detector_count()).cyan(),
        style(findings.len()).cyan(),
    ));

    // Step 5.5: Run voting engine to consolidate findings from multiple detectors
    let voting_spinner = multi.add(ProgressBar::new_spinner());
    voting_spinner.set_style(spinner_style.clone());
    voting_spinner.set_message("Consolidating findings with voting engine...");
    voting_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let voting_engine = VotingEngine::with_config(
        VotingStrategy::Weighted,
        ConfidenceMethod::Bayesian,
        SeverityResolution::Highest,
        0.5, // confidence threshold - allow findings with 50%+ confidence
        2,   // min detectors for consensus boost
    );
    let (consolidated_findings, voting_stats) = voting_engine.vote(findings);
    let mut findings = consolidated_findings;

    // In incremental mode, merge with cached findings from unchanged files
    let cached_findings_count = cached_findings.len();
    if is_incremental_mode && !cached_findings.is_empty() {
        findings.extend(cached_findings);
        tracing::debug!(
            "Merged {} cached findings with {} new findings",
            cached_findings_count,
            voting_stats.total_output
        );
    }

    // Update incremental cache with findings for parsed files
    if is_incremental_mode {
        // Group findings by file and cache them
        for file_path in &files {
            let file_findings: Vec<_> = findings
                .iter()
                .filter(|f| f.affected_files.iter().any(|af| af == file_path))
                .cloned()
                .collect();
            incremental_cache.cache_findings(file_path, &file_findings);
        }
        // Save cache for next run
        if let Err(e) = incremental_cache.save_cache() {
            tracing::warn!("Failed to save incremental cache: {}", e);
        }
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

    // Wait for git enrichment to complete (if running)
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
                git_spinner.finish_with_message(format!(
                    "{}Git enrichment failed",
                    style("⚠ ").yellow(),
                ));
            }
        }
    }

    // Apply detector config overrides from repotoire.toml (enabled/disabled, severity)
    if !project_config.detectors.is_empty() {
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
        for finding in &mut findings {
            let detector_name = crate::config::normalize_detector_name(&finding.detector);
            if let Some(config) = detector_configs.get(&detector_name) {
                if let Some(ref sev) = config.severity {
                    finding.severity = parse_severity(sev);
                }
            }
        }
    }

    // Calculate health score from ALL findings (before any filtering)
    // This ensures consistent grading regardless of --top or severity filters
    let all_findings_summary = FindingsSummary::from_findings(&findings);
    let (overall_score, structure_score, quality_score, architecture_score) =
        calculate_health_scores(&findings, files.len(), total_functions, total_classes, &project_config);

    // Step 6: Filter findings by severity and top N (for display only)
    if let Some(min_severity) = &severity {
        let min = parse_severity(min_severity);
        findings.retain(|f| f.severity >= min);
    }

    // Sort by severity (critical first)
    findings.sort_by(|a, b| b.severity.cmp(&a.severity));

    if let Some(n) = top {
        findings.truncate(n);
    }

    // Create display summary from filtered findings (only shows counts for displayed severities)
    let display_summary = FindingsSummary::from_findings(&findings);

    // Track displayed findings count for pagination
    let displayed_findings = findings.len();
    
    // Keep all findings for caching (don't destroy with drain!)
    let all_findings = findings.clone();

    // Apply pagination (per_page = 0 means all)
    let (paginated_findings, pagination_info) = if per_page > 0 {
        let total_pages = (displayed_findings + per_page - 1) / per_page;
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
    };
    let mut grade = HealthReport::grade_from_score(overall_score);
    
    // Cap grade based on security findings - can't get A/B with critical vulns
    if all_findings_summary.critical > 0 {
        // Any critical finding caps grade at C
        if grade == "A" || grade == "B" {
            grade = "C".to_string();
        }
    } else if all_findings_summary.high > 0 {
        // High findings (no critical) caps grade at B
        if grade == "A" {
            grade = "B".to_string();
        }
    }

    // Build report with paginated findings and filtered summary (for display)
    // Grade calculation already used all_findings_summary, so display_summary is just for output
    let report = HealthReport {
        overall_score,
        grade: grade.clone(),
        structure_score,
        quality_score,
        architecture_score: Some(architecture_score),
        findings: paginated_findings,
        findings_summary: display_summary,
        total_files: files.len(),
        total_functions,
        total_classes,
    };

    // Step 7: Output results
    let output = reporters::report(&report, format)?;

    // Determine output destination
    let write_to_file = output_path.is_some()
        || matches!(format, "html" | "sarif" | "markdown" | "md");

    if write_to_file {
        // Determine output path
        let out_path = if let Some(p) = output_path {
            p.to_path_buf()
        } else {
            // Auto-generate filename based on format
            let ext = match format {
                "html" => "html",
                "sarif" => "sarif.json",
                "markdown" | "md" => "md",
                "json" => "json",
                _ => "txt",
            };
            repotoire_dir.join(format!("report.{}", ext))
        };

        // Write to file
        std::fs::write(&out_path, &output)?;
        println!(
            "\n{}Report written to: {}",
            style("📄 ").bold(),
            style(out_path.display()).cyan()
        );
    } else {
        // Print to stdout
        println!();
        println!("{}", output);
    }

    // Cache results for later commands (pass ALL findings, not paginated)
    cache_results(&repotoire_dir, &report, &all_findings)?;

    // Show pagination info if applicable
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

    // Final summary
    let elapsed = start_time.elapsed();
    let icon_done = if no_emoji { "" } else { "✨ " };
    println!(
        "\n{}Analysis complete in {:.2}s",
        style(icon_done).bold(),
        elapsed.as_secs_f64()
    );

    // Exit with code 1 if --fail-on threshold is met (for CI/CD)
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

/// Collect all source files in the repository, respecting .gitignore
fn collect_source_files(repo_path: &Path) -> Result<Vec<std::path::PathBuf>> {
    let mut files = Vec::new();

    let mut builder = WalkBuilder::new(repo_path);
    builder
        .hidden(true) // Respect hidden files setting
        .git_ignore(true) // Respect .gitignore
        .git_global(true) // Respect global gitignore
        .git_exclude(true) // Respect .git/info/exclude
        .require_git(false) // Work even if not a git repo
        .add_custom_ignore_filename(".repotoireignore"); // Support .repotoireignore files
    
    let walker = builder.build();

    for entry in walker.flatten() {
        let path = entry.path();

        // Skip directories and non-files
        if !path.is_file() {
            continue;
        }

        // Check if supported extension
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

/// Truncate a path for display
fn truncate_path(path: &Path, max_len: usize) -> String {
    let s = path.display().to_string();
    if s.len() <= max_len {
        s
    } else {
        format!("...{}", &s[s.len() - max_len + 3..])
    }
}

/// Escape special characters for Cypher queries
fn escape_cypher(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
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

/// Calculate health scores based on findings
/// Returns (overall, structure, quality, architecture)
fn calculate_health_scores(
    findings: &[crate::models::Finding],
    total_files: usize,
    total_functions: usize,
    _total_classes: usize,
    project_config: &ProjectConfig,
) -> (f64, f64, f64, f64) {
    // Base score starts at 100
    let mut structure_score: f64 = 100.0;
    let mut quality_score: f64 = 100.0;
    let mut architecture_score: f64 = 100.0;

    // Normalize by codebase size to prevent large projects from always scoring 0
    // Use sqrt to dampen the effect of very large codebases
    // Floor of 5.0 for small projects so vulns still hurt
    let size_factor = ((total_files + total_functions) as f64).sqrt().max(5.0);
    
    // Get security multiplier from project config (default: 3.0)
    let config_security_multiplier = project_config.scoring.security_multiplier;
    
    // Deduct points based on findings, normalized by size
    for finding in findings {
        let base_deduction: f64 = match finding.severity {
            Severity::Critical => 10.0,
            Severity::High => 5.0,
            Severity::Medium => 1.5,
            Severity::Low => 0.3,
            Severity::Info => 0.0,
        };

        // Scale deduction by codebase size - larger codebases get smaller per-finding penalty
        let scaled = base_deduction / size_factor;
        
        // Categorize by finding category
        let category = finding.category.as_deref().unwrap_or("");
        let detector = finding.detector.to_lowercase();
        
        // Security findings get configurable weight - injection vulns and secrets are serious
        let is_security = category.contains("security") 
            || category.contains("inject")
            || detector.contains("sql")
            || detector.contains("xss")
            || detector.contains("secret")
            || detector.contains("credential")
            || detector.contains("command")
            || detector.contains("path_traversal")
            || detector.contains("ssrf")
            || finding.cwe_id.is_some();
        
        let security_multiplier = if is_security { config_security_multiplier } else { 1.0 };
        let effective_deduction = scaled * security_multiplier;
        
        if is_security {
            quality_score -= effective_deduction;
        } else if category.contains("architect") || category.contains("bottleneck") || category.contains("circular") {
            architecture_score -= effective_deduction;
        } else if category.contains("complex") || category.contains("naming") || category.contains("readab") {
            structure_score -= effective_deduction;
        } else {
            // Distribute evenly among all three
            quality_score -= effective_deduction / 3.0;
            structure_score -= effective_deduction / 3.0;
            architecture_score -= effective_deduction / 3.0;
        }
    }

    // Clamp to 25-100 (floor of 25 so no codebase looks "hopeless")
    structure_score = structure_score.max(25.0_f64).min(100.0);
    quality_score = quality_score.max(25.0_f64).min(100.0);
    architecture_score = architecture_score.max(25.0_f64).min(100.0);

    // Use pillar weights from project config (default: 0.4, 0.3, 0.3)
    let weights = &project_config.scoring.pillar_weights;
    let overall = structure_score * weights.structure 
        + quality_score * weights.quality 
        + architecture_score * weights.architecture;
    
    // Floor at 5.0 to allow F grade gradation (pillar scores already penalize security)
    let overall = overall.max(5.0);

    (overall, structure_score, quality_score, architecture_score)
}

/// Normalize a path to be relative (strip common prefixes)
fn normalize_path(path: &Path) -> String {
    let s = path.display().to_string();
    // Strip common absolute prefixes to make paths relative
    if let Some(stripped) = s.strip_prefix("/tmp/") {
        // For temp dirs, keep just the relative part after the repo name
        if let Some(pos) = stripped.find('/') {
            return stripped[pos + 1..].to_string();
        }
    }
    // Strip home directory prefixes
    if let Ok(home) = std::env::var("HOME") {
        if let Some(stripped) = s.strip_prefix(&home) {
            return stripped.trim_start_matches('/').to_string();
        }
    }
    // Return as-is if already relative or no match
    s
}

/// Cache analysis results for other commands (findings, fix, etc.)
fn cache_results(repotoire_dir: &Path, report: &HealthReport, all_findings: &[Finding]) -> Result<()> {
    use std::fs;

    // Cache health data
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

    // Cache ALL findings (not just paginated)
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
    fs::write(&findings_cache, serde_json::to_string_pretty(&findings_json)?)?;

    tracing::debug!("Cached analysis results to {}", repotoire_dir.display());
    Ok(())
}

/// Normalize detector name for config matching
/// Converts various formats to kebab-case: GodClassDetector -> god-class
fn normalize_detector_name_for_config(name: &str) -> String {
    let mut result = String::new();
    let chars: Vec<char> = name.chars().collect();
    
    for (i, c) in chars.iter().enumerate() {
        if c.is_uppercase() {
            if i > 0 && !chars[i - 1].is_uppercase() {
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
    result
        .trim_end_matches("-detector")
        .to_string()
}

/// Get files changed since a specific git commit
/// 
/// Uses `git diff --name-only <since> HEAD` to find changed files.
/// Also includes untracked files via `git ls-files --others --exclude-standard`.
fn get_changed_files_since(repo_path: &Path, since: &str) -> Result<Vec<PathBuf>> {
    // Get tracked files that changed
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
        .filter(|p| p.exists()) // Only include files that still exist
        .collect();

    // Also get untracked files (new files not yet committed)
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

    // Filter to only supported file extensions
    files.retain(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|ext| SUPPORTED_EXTENSIONS.contains(&ext))
            .unwrap_or(false)
    });

    Ok(files)
}

/// Get files that have been staged or modified (uncommitted changes)
fn get_uncommitted_files(repo_path: &Path) -> Result<Vec<PathBuf>> {
    // Get staged and modified tracked files
    let output = Command::new("git")
        .args(["diff", "--name-only", "HEAD"])
        .current_dir(repo_path)
        .output()
        .with_context(|| "Failed to run git diff HEAD")?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut files: Vec<PathBuf> = stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(|l| repo_path.join(l))
        .filter(|p| p.exists())
        .collect();

    // Also get staged files
    let staged = Command::new("git")
        .args(["diff", "--name-only", "--cached"])
        .current_dir(repo_path)
        .output();

    if let Ok(out) = staged {
        let staged_files = String::from_utf8_lossy(&out.stdout);
        for line in staged_files.lines().filter(|l| !l.is_empty()) {
            let path = repo_path.join(line);
            if path.exists() && !files.contains(&path) {
                files.push(path);
            }
        }
    }

    // Get untracked files
    let untracked = Command::new("git")
        .args(["ls-files", "--others", "--exclude-standard"])
        .current_dir(repo_path)
        .output();

    if let Ok(out) = untracked {
        let new_files = String::from_utf8_lossy(&out.stdout);
        for line in new_files.lines().filter(|l| !l.is_empty()) {
            let path = repo_path.join(line);
            if path.exists() && !files.contains(&path) {
                files.push(path);
            }
        }
    }

    // Filter to supported extensions
    files.retain(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|ext| SUPPORTED_EXTENSIONS.contains(&ext))
            .unwrap_or(false)
    });

    Ok(files)
}
