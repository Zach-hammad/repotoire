//! `repotoire watch` — live analysis on file changes
//!
//! Watches your codebase and re-analyzes changed files in real-time.
//! Particularly useful for catching AI-generated code issues as they happen.

use anyhow::Result;
use console::style;
use notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use crate::detectors::{default_detectors_with_ngram, DetectorEngine};
use crate::models::Finding;
use crate::parsers::parse_file;

/// Supported source file extensions
const WATCH_EXTENSIONS: &[&str] = &[
    "rs", "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "java", "c", "h", "cpp", "cc",
    "cxx", "hpp", "cs", "kt", "kts",
];

pub fn run(path: &Path, relaxed: bool, no_emoji: bool, quiet: bool) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;

    if !quiet {
        let icon = if no_emoji { "" } else { "👁️  " };
        println!(
            "\n{}Watching {} for changes...\n",
            style(icon).bold(),
            style(repo_path.display()).cyan()
        );
        println!("  {} Save a file to trigger analysis", style("→").dim());
        println!("  {} Press Ctrl+C to stop\n", style("→").dim());
    }

    // Load config and style profile
    let project_config = crate::config::load_project_config(&repo_path);
    let style_profile = crate::calibrate::StyleProfile::load(&repo_path);

    // Build n-gram model from existing source for predictive coding
    let ngram_model = {
        let mut model = crate::calibrate::NgramModel::new();
        let walker = ignore::WalkBuilder::new(&repo_path)
            .hidden(false)
            .git_ignore(true)
            .build();
        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() { continue; }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !WATCH_EXTENSIONS.contains(&ext) { continue; }
            if is_ignored_path(path, &repo_path) { continue; }
            if let Ok(content) = std::fs::read_to_string(path) {
                let tokens = crate::calibrate::NgramModel::tokenize_file(&content);
                model.train_on_tokens(&tokens);
            }
        }
        if model.is_confident() {
            if !quiet {
                println!("  {} Learned coding patterns ({} tokens)", style("🧠").dim(), model.total_tokens());
            }
            Some(model)
        } else {
            None
        }
    };

    // Set up file watcher with debouncing
    let (tx, rx) = mpsc::channel();

    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result: DebounceEventResult| {
            if let Ok(events) = result {
                let _ = tx.send(events);
            }
        },
    )?;

    debouncer.watch(&repo_path, RecursiveMode::Recursive)?;

    // Track findings per file for diff display
    let mut previous_findings: std::collections::HashMap<PathBuf, Vec<Finding>> =
        std::collections::HashMap::new();
    let mut total_catches = 0u32;

    // Main event loop
    loop {
        match rx.recv() {
            Ok(events) => {
                // Collect unique changed source files
                let changed_files: HashSet<PathBuf> = events
                    .iter()
                    .flat_map(|event| event.paths.iter())
                    .filter(|p| {
                        p.extension()
                            .and_then(|e| e.to_str())
                            .map_or(false, |ext| WATCH_EXTENSIONS.contains(&ext))
                            && !is_ignored_path(p, &repo_path)
                    })
                    .cloned()
                    .collect();

                if changed_files.is_empty() {
                    continue;
                }

                // Analyze each changed file
                for file_path in &changed_files {
                    let findings = analyze_single_file(
                        file_path,
                        &repo_path,
                        &project_config,
                        style_profile.as_ref(),
                        ngram_model.clone(),
                        relaxed,
                    );

                    let prev = previous_findings.get(file_path).cloned().unwrap_or_default();
                    let catches = display_file_diff(file_path, &repo_path, &findings, &prev, no_emoji, quiet);
                    total_catches += catches;
                    previous_findings.insert(file_path.clone(), findings);
                }
            }
            Err(_) => break,
        }
    }

    println!(
        "\n{} Caught {} issues during watch session.",
        if no_emoji { "" } else { "📊" },
        total_catches
    );
    Ok(())
}

/// Analyze a single file with all detectors
fn analyze_single_file(
    file_path: &Path,
    repo_path: &Path,
    project_config: &crate::config::ProjectConfig,
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
    relaxed: bool,
) -> Vec<Finding> {
    let Ok(parse_result) = parse_file(file_path) else {
        return vec![];
    };

    let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
    let rel_str = rel_path.to_string_lossy();

    // Build a minimal graph with just this file
    let graph = crate::graph::GraphStore::in_memory();
    for func in &parse_result.functions {
        let node =
            crate::graph::CodeNode::new(crate::graph::NodeKind::Function, &func.name, &rel_str)
                .with_property("complexity", func.complexity.unwrap_or(1) as i64)
                .with_property("loc", (func.line_end - func.line_start + 1) as i64)
                .with_property("is_async", func.is_async);
        graph.add_node(node);
    }
    for class in &parse_result.classes {
        let node =
            crate::graph::CodeNode::new(crate::graph::NodeKind::Class, &class.name, &rel_str)
                .with_property("methodCount", class.methods.len() as i64);
        graph.add_node(node);
    }

    // Read file content for line-based detectors
    let source = std::fs::read_to_string(file_path).unwrap_or_default();
    let loc = source.lines().count();
    let file_node = crate::graph::CodeNode::new(crate::graph::NodeKind::File, &rel_str, &rel_str)
        .with_property("loc", loc as i64)
        .with_property(
            "language",
            crate::parsers::language_for_extension(
                file_path.extension().and_then(|e| e.to_str()).unwrap_or(""),
            )
            .unwrap_or("unknown"),
        );
    graph.add_node(file_node);

    // Run detectors
    let mut engine = DetectorEngine::new(1);
    let skip_set: HashSet<&str> = HashSet::new();
    let detectors = default_detectors_with_ngram(repo_path, project_config, style_profile, ngram_model);

    for detector in detectors {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    let mut findings = match engine.run(&graph) {
        Ok(f) => f,
        Err(_) => return vec![],
    };

    // Filter to only findings in this file
    findings.retain(|f| {
        f.affected_files.iter().any(|af| {
            let af_str = af.to_string_lossy();
            af_str.contains(&*rel_str) || af_str == rel_str.as_ref()
        })
    });

    // Filter by severity if relaxed
    if relaxed {
        findings.retain(|f| {
            matches!(
                f.severity,
                crate::models::Severity::High | crate::models::Severity::Critical
            )
        });
    }

    findings
}

/// Check if a detector is AI-focused
fn is_ai_detector(name: &str) -> bool {
    name.starts_with("AI")
        || name.contains("Complexity")
        || name.contains("Naming")
        || name.contains("MissingTest")
        || name.contains("Duplicate")
        || name.contains("Boilerplate")
}

/// Check if path should be ignored (build dirs, node_modules, etc.)
fn is_ignored_path(path: &Path, repo_path: &Path) -> bool {
    let rel = path.strip_prefix(repo_path).unwrap_or(path);
    let rel_str = rel.to_string_lossy();

    rel_str.contains("target/")
        || rel_str.contains("node_modules/")
        || rel_str.contains(".git/")
        || rel_str.contains(".repotoire/")
        || rel_str.contains("__pycache__/")
        || rel_str.contains(".next/")
        || rel_str.contains("dist/")
        || rel_str.contains("build/")
        || rel_str.starts_with('.')
}

/// Display diff between previous and current findings for a file. Returns count of new catches.
fn display_file_diff(
    file_path: &Path,
    repo_path: &Path,
    findings: &[Finding],
    prev: &[Finding],
    no_emoji: bool,
    quiet: bool,
) -> u32 {
    let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);

    let new_findings: Vec<_> = findings.iter().filter(|f| {
        !prev.iter().any(|pf| pf.detector == f.detector && pf.line_start == f.line_start && pf.title == f.title)
    }).collect();

    let fixed_findings: Vec<_> = prev.iter().filter(|pf| {
        !findings.iter().any(|f| f.detector == pf.detector && f.line_start == pf.line_start && f.title == pf.title)
    }).collect();

    if new_findings.is_empty() && fixed_findings.is_empty() {
        if !quiet && !findings.is_empty() {
            let time = chrono::Local::now().format("%H:%M:%S");
            println!(
                "{} {} {} ({} findings, no changes)",
                style(format!("[{}]", time)).dim(),
                if no_emoji { "→" } else { "📝" },
                style(rel_path.display()).dim(),
                findings.len()
            );
        }
        return 0;
    }

    let time = chrono::Local::now().format("%H:%M:%S");
    println!(
        "{} {} {}",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "→" } else { "📝" },
        style(rel_path.display()).cyan().bold()
    );

    let mut catches = 0u32;
    for f in &new_findings {
        catches += 1;
        let sev_icon = severity_icon(f.severity, no_emoji);
        let line = f.line_start.map_or(String::new(), |l| format!(":{}", l));
        println!(
            "  {} {} {} {}",
            sev_icon,
            style(&f.detector.replace("Detector", "")).yellow(),
            style(format!("{}{}", rel_path.display(), line)).dim(),
            f.title
        );
        if is_ai_detector(&f.detector) {
            println!(
                "     {} {}",
                style("⚡").dim(),
                style("Possible AI-generated code issue").dim().italic()
            );
        }
    }

    for f in &fixed_findings {
        let line = f.line_start.map_or(String::new(), |l| format!(":{}", l));
        println!(
            "  {} {} {} {}",
            if no_emoji { "FIX " } else { "✅" },
            style(&f.detector.replace("Detector", "")).green(),
            style(format!("{}{}", rel_path.display(), line)).dim(),
            style(&f.title).strikethrough()
        );
    }

    println!();
    catches
}

/// Map severity to display icon
fn severity_icon(severity: crate::models::Severity, no_emoji: bool) -> &'static str {
    use crate::models::Severity;
    match (severity, no_emoji) {
        (Severity::Critical, true) => "CRIT",
        (Severity::Critical, false) => "🔴",
        (Severity::High, true) => "HIGH",
        (Severity::High, false) => "🟠",
        (Severity::Medium, true) => "MED ",
        (Severity::Medium, false) => "🟡",
        (Severity::Low, true) => "LOW ",
        (Severity::Low, false) => "🔵",
        (Severity::Info, true) => "INFO",
        (Severity::Info, false) => "⚪",
    }
}
