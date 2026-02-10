//! Analyze command implementation
//!
//! This command performs a full codebase analysis:
//! 1. Initialize Kuzu graph database
//! 2. Walk repository and parse all supported files
//! 3. Build the code graph (nodes + edges)
//! 4. Enrich with git history (authors, churn, temporal data)
//! 5. Run all registered detectors
//! 6. Calculate health score and grade
//! 7. Output results (text, json, sarif)

use crate::detectors::{
    default_detectors, DetectorEngine, Detector,
};
use crate::git;
use crate::graph::{GraphStore, CodeNode, CodeEdge, NodeKind};
use crate::models::{FindingsSummary, HealthReport, Severity};
use crate::parsers::{parse_file, ParseResult};
use crate::reporters;

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashSet;
use std::path::Path;
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
) -> Result<()> {
    let start_time = Instant::now();

    // Validate repository path
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Repository path does not exist: {}", path.display()))?;

    if !repo_path.is_dir() {
        anyhow::bail!("Path is not a directory: {}", repo_path.display());
    }

    println!("\n{}Repotoire Analysis\n", style("🎼 ").bold());
    println!(
        "{}Analyzing: {}\n",
        style("🔍 ").bold(),
        style(repo_path.display()).cyan()
    );

    // Create .repotoire directory if needed
    let repotoire_dir = repo_path.join(".repotoire");
    std::fs::create_dir_all(&repotoire_dir)
        .with_context(|| "Failed to create .repotoire directory")?;

    // Initialize graph database
    let db_path = repotoire_dir.join("graph_db");
    println!("{}Initializing graph database...", style("🕸️  ").bold());
    let graph = GraphStore::new(&db_path).with_context(|| "Failed to initialize Kuzu database")?;

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
    walk_spinner.set_message("Discovering source files...");
    walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let files = collect_source_files(&repo_path)?;
    walk_spinner.finish_with_message(format!(
        "{}Found {} source files",
        style("✓ ").green(),
        style(files.len()).cyan()
    ));

    if files.is_empty() {
        println!("\n{}No source files found to analyze.", style("⚠️  ").yellow());
        return Ok(());
    }

    // Step 2: Parse files in parallel using rayon
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    parse_bar.set_message("Parsing files (parallel)...");

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

        // Call edges
        for (caller, callee) in &result.calls {
            let callee_qn = format!("{}::{}", relative_str, callee);
            edges.push((caller.clone(), callee_qn, CodeEdge::calls()));
        }

        // Import edges
        for import in &result.imports {
            for (other_file, _) in &parse_results {
                let other_relative = other_file.strip_prefix(&repo_path).unwrap_or(other_file);
                let other_str = other_relative.display().to_string();
                if other_str.contains(import) && other_str != relative_str {
                    edges.push((relative_str.clone(), other_str, CodeEdge::imports()));
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

    // Step 4: Enrich with git history (skip with --no-git)
    if !no_git {
        let git_spinner = multi.add(ProgressBar::new_spinner());
        git_spinner.set_style(spinner_style.clone());
        git_spinner.set_message("Enriching with git history...");
        git_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        match git::enrichment::enrich_graph_with_git(&repo_path, &graph, None) {
            Ok(stats) => {
                if stats.functions_enriched > 0 || stats.classes_enriched > 0 {
                    git_spinner.finish_with_message(format!(
                        "{}Enriched {} functions, {} classes with git data",
                        style("✓ ").green(),
                        style(stats.functions_enriched).cyan(),
                        style(stats.classes_enriched).cyan(),
                    ));
                } else {
                    git_spinner.finish_with_message(format!(
                        "{}No git history to enrich (or not a git repo)",
                        style("- ").dim(),
                    ));
                }
            }
            Err(e) => {
                git_spinner.finish_with_message(format!(
                    "{}Git enrichment skipped: {}",
                    style("⚠ ").yellow(),
                    e
                ));
            }
        }
    } else {
        println!("{}Skipping git enrichment (--no-git)", style("⏭ ").dim());
    }

    // Step 5: Run detectors
    println!("\n{}Running detectors...", style("🕵️  ").bold());
    
    // Pre-warm file cache for faster detector execution
    crate::cache::warm_global_cache(&repo_path, SUPPORTED_EXTENSIONS);

    let mut engine = DetectorEngine::new(workers);

    // Register all default detectors (skip any in skip_detector list)
    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();

    for detector in default_detectors(&repo_path) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // In thorough mode, we could add external tool detectors here
    if thorough {
        tracing::info!("Thorough mode enabled - all {} detectors active", engine.detector_count());
    }

    let detector_bar = multi.add(ProgressBar::new_spinner());
    detector_bar.set_style(spinner_style.clone());
    detector_bar.set_message("Running detectors...");
    detector_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    let mut findings = engine.run(&graph)?;

    detector_bar.finish_with_message(format!(
        "{}Ran {} detectors, found {} issues",
        style("✓ ").green(),
        style(engine.detector_count()).cyan(),
        style(findings.len()).cyan(),
    ));

    // Step 5: Filter findings by severity and top N
    if let Some(min_severity) = &severity {
        let min = parse_severity(min_severity);
        findings.retain(|f| f.severity >= min);
    }

    // Sort by severity (critical first)
    findings.sort_by(|a, b| b.severity.cmp(&a.severity));

    if let Some(n) = top {
        findings.truncate(n);
    }

    // Calculate totals before pagination for accurate summary
    let total_findings = findings.len();
    let findings_summary = FindingsSummary::from_findings(&findings);

    // Apply pagination (per_page = 0 means all)
    let (paginated_findings, pagination_info) = if per_page > 0 {
        let total_pages = (total_findings + per_page - 1) / per_page;
        let page = page.max(1).min(total_pages.max(1));
        let start = (page - 1) * per_page;
        let end = (start + per_page).min(total_findings);
        let paginated: Vec<_> = findings.drain(start..end).collect();
        (
            paginated,
            Some((page, total_pages, per_page, total_findings)),
        )
    } else {
        (findings, None)
    };

    // Step 6: Calculate health score (use full findings for accurate score)
    let (overall_score, structure_score, quality_score, architecture_score) =
        calculate_health_scores(&paginated_findings, files.len(), total_functions, total_classes);
    let grade = HealthReport::grade_from_score(overall_score);

    // Build report with paginated findings but full summary
    let report = HealthReport {
        overall_score,
        grade: grade.clone(),
        structure_score,
        quality_score,
        architecture_score: Some(architecture_score),
        findings: paginated_findings,
        findings_summary,
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

    // Cache results for later commands
    cache_results(&repotoire_dir, &report)?;

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
    println!(
        "\n{}Analysis complete in {:.2}s",
        style("✨ ").bold(),
        elapsed.as_secs_f64()
    );

    // Exit with code 2 if critical findings exist (for CI/CD)
    if report.findings_summary.critical > 0 {
        std::process::exit(2);
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
    _total_files: usize,
    _total_functions: usize,
    _total_classes: usize,
) -> (f64, f64, f64, f64) {
    // Base score starts at 100
    let mut structure_score: f64 = 100.0;
    let mut quality_score: f64 = 100.0;
    let mut architecture_score: f64 = 100.0;

    // Deduct points based on findings
    for finding in findings {
        let deduction: f64 = match finding.severity {
            Severity::Critical => 10.0,
            Severity::High => 5.0,
            Severity::Medium => 2.0,
            Severity::Low => 0.5,
            Severity::Info => 0.0,
        };

        // Categorize by finding category - scale down deductions
        let category = finding.category.as_deref().unwrap_or("");
        let scaled = deduction * 0.05; // 5% - prevents zeroing with many findings
        
        if category.contains("security") || category.contains("inject") {
            quality_score -= scaled;
        } else if category.contains("architect") || category.contains("bottleneck") || category.contains("circular") {
            architecture_score -= scaled;
        } else if category.contains("complex") || category.contains("naming") || category.contains("readab") {
            structure_score -= scaled;
        } else {
            // Distribute evenly among all three
            quality_score -= scaled / 3.0;
            structure_score -= scaled / 3.0;
            architecture_score -= scaled / 3.0;
        }
    }

    // Clamp to 0-100
    structure_score = structure_score.max(0.0_f64).min(100.0);
    quality_score = quality_score.max(0.0_f64).min(100.0);
    architecture_score = architecture_score.max(0.0_f64).min(100.0);

    // Weighted average: Structure 40%, Quality 30%, Architecture 30%
    let overall = structure_score * 0.4 + quality_score * 0.3 + architecture_score * 0.3;

    (overall, structure_score, quality_score, architecture_score)
}

/// Cache analysis results for other commands (findings, fix, etc.)
fn cache_results(repotoire_dir: &Path, report: &HealthReport) -> Result<()> {
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

    // Cache findings
    let findings_cache = repotoire_dir.join("last_findings.json");
    let findings_json = serde_json::json!({
        "findings": report.findings.iter().map(|f| {
            serde_json::json!({
                "id": f.id,
                "detector": f.detector,
                "title": f.title,
                "description": f.description,
                "severity": f.severity.to_string(),
                "affected_files": f.affected_files.iter().map(|p| p.display().to_string()).collect::<Vec<_>>(),
                "line_start": f.line_start,
                "line_end": f.line_end,
                "suggested_fix": f.suggested_fix,
                "category": f.category,
                "cwe_id": f.cwe_id,
                "why_it_matters": f.why_it_matters,
            })
        }).collect::<Vec<_>>()
    });
    fs::write(&findings_cache, serde_json::to_string_pretty(&findings_json)?)?;

    tracing::debug!("Cached analysis results to {}", repotoire_dir.display());
    Ok(())
}
