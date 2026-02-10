//! Graph command - query the code graph directly

use crate::graph::GraphStore;
use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run a query against the code graph
/// 
/// Note: Cypher queries are no longer supported. Use the built-in query commands instead.
pub fn run(path: &Path, query: &str, _format: &str) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let db_path = crate::cache::get_graph_db_path(&repo_path);
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphStore::new(&db_path)
        .with_context(|| "Failed to open graph database")?;

    // Parse simple query patterns
    let query_lower = query.to_lowercase();
    
    if query_lower.contains("function") {
        let functions = graph.get_functions();
        println!("\n{} Functions ({})\n", style("📊").bold(), functions.len());
        for func in functions.iter().take(50) {
            println!("  {} ({}:{})", 
                style(&func.qualified_name).cyan(),
                &func.file_path,
                func.line_start
            );
        }
        if functions.len() > 50 {
            println!("  ... and {} more", functions.len() - 50);
        }
    } else if query_lower.contains("class") {
        let classes = graph.get_classes();
        println!("\n{} Classes ({})\n", style("📊").bold(), classes.len());
        for class in classes.iter().take(50) {
            println!("  {} ({}:{})", 
                style(&class.qualified_name).cyan(),
                &class.file_path,
                class.line_start
            );
        }
        if classes.len() > 50 {
            println!("  ... and {} more", classes.len() - 50);
        }
    } else if query_lower.contains("file") {
        let files = graph.get_files();
        println!("\n{} Files ({})\n", style("📊").bold(), files.len());
        for file in files.iter().take(50) {
            println!("  {}", style(&file.file_path).cyan());
        }
        if files.len() > 50 {
            println!("  ... and {} more", files.len() - 50);
        }
    } else if query_lower.contains("call") {
        let calls = graph.get_calls();
        println!("\n{} Call Edges ({})\n", style("📊").bold(), calls.len());
        for (from, to) in calls.iter().take(50) {
            println!("  {} -> {}", style(from).cyan(), style(to).green());
        }
        if calls.len() > 50 {
            println!("  ... and {} more", calls.len() - 50);
        }
    } else if query_lower.contains("import") {
        let imports = graph.get_imports();
        println!("\n{} Import Edges ({})\n", style("📊").bold(), imports.len());
        for (from, to) in imports.iter().take(50) {
            println!("  {} -> {}", style(from).cyan(), style(to).green());
        }
        if imports.len() > 50 {
            println!("  ... and {} more", imports.len() - 50);
        }
    } else if query_lower == "stats" {
        // Redirect to stats command
        return stats(path);
    } else {
        println!("{}", style("Supported queries:").bold());
        println!("  - functions: List all functions");
        println!("  - classes: List all classes");
        println!("  - files: List all files");
        println!("  - calls: List call edges");
        println!("  - imports: List import edges");
        println!("  - stats: Show graph statistics");
        println!("\nNote: Cypher queries are not supported. You can also run 'repotoire stats' directly.");
    }

    Ok(())
}

/// Show graph statistics
pub fn stats(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let db_path = crate::cache::get_graph_db_path(&repo_path);
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphStore::new(&db_path)
        .with_context(|| "Failed to open graph database")?;

    println!("\n{} Graph Statistics\n", style("📊").bold());

    let stats = graph.stats();
    
    // Node counts
    println!("  {}: {}", style("Files").cyan(), style(stats.get("files").copied().unwrap_or(0)).bold());
    println!("  {}: {}", style("Functions").cyan(), style(stats.get("functions").copied().unwrap_or(0)).bold());
    println!("  {}: {}", style("Classes").cyan(), style(stats.get("classes").copied().unwrap_or(0)).bold());
    
    // Edge counts
    println!();
    println!("  {} edges: {}", style("CALLS").cyan(), style(stats.get("call_edges").copied().unwrap_or(0)).bold());
    println!("  {} edges: {}", style("IMPORTS").cyan(), style(stats.get("import_edges").copied().unwrap_or(0)).bold());
    println!("  {} edges: {}", style("CONTAINS").cyan(), style(stats.get("contains_edges").copied().unwrap_or(0)).bold());
    
    // Total
    println!();
    println!("  Total nodes: {}", style(graph.node_count()).bold());
    println!("  Total edges: {}", style(graph.edge_count()).bold());

    Ok(())
}
