//! Graph command - query the code graph directly

use crate::graph::GraphStore;
use anyhow::{Context, Result};
use console::style;
use serde_json;
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

    // Try to read from cached JSON stats first (avoids sled lock issues)
    let stats_path = crate::cache::get_graph_stats_path(&repo_path);
    if stats_path.exists() {
        let stats_json = std::fs::read_to_string(&stats_path)
            .with_context(|| "Failed to read graph stats")?;
        let stats: serde_json::Value = serde_json::from_str(&stats_json)
            .with_context(|| "Failed to parse graph stats")?;

        println!("\n{} Graph Statistics\n", style("📊").bold());

        // Node counts
        println!("  {}: {}", style("Files").cyan(), 
            style(stats["total_files"].as_u64().unwrap_or(0)).bold());
        println!("  {}: {}", style("Functions").cyan(), 
            style(stats["total_functions"].as_u64().unwrap_or(0)).bold());
        println!("  {}: {}", style("Classes").cyan(), 
            style(stats["total_classes"].as_u64().unwrap_or(0)).bold());

        // Edge counts by type
        let calls = stats["calls"].as_u64().unwrap_or(0);
        let imports = stats["imports"].as_u64().unwrap_or(0);
        let total_edges = stats["total_edges"].as_u64().unwrap_or(0);
        let contains = total_edges.saturating_sub(calls + imports);
        println!();
        println!("  {} edges: {}", style("CALLS").cyan(), style(calls).bold());
        println!("  {} edges: {}", style("IMPORTS").cyan(), style(imports).bold());
        println!("  {} edges: {}", style("CONTAINS").cyan(), style(contains).bold());

        // Total
        println!();
        println!("  Total nodes: {}", style(stats["total_nodes"].as_u64().unwrap_or(0)).bold());
        println!("  Total edges: {}", style(total_edges).bold());

        return Ok(());
    }

    // Fallback to opening sled database (may fail with lock issues)
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
    
    // Node counts (stats uses "total_*" keys)
    println!("  {}: {}", style("Files").cyan(), style(stats.get("total_files").copied().unwrap_or(0)).bold());
    println!("  {}: {}", style("Functions").cyan(), style(stats.get("total_functions").copied().unwrap_or(0)).bold());
    println!("  {}: {}", style("Classes").cyan(), style(stats.get("total_classes").copied().unwrap_or(0)).bold());
    
    // Edge counts by type
    let calls = graph.get_calls().len();
    let imports = graph.get_imports().len();
    let contains = graph.edge_count() - calls - imports;
    println!();
    println!("  {} edges: {}", style("CALLS").cyan(), style(calls).bold());
    println!("  {} edges: {}", style("IMPORTS").cyan(), style(imports).bold());
    println!("  {} edges: {}", style("CONTAINS").cyan(), style(contains).bold());
    
    // Total
    println!();
    println!("  Total nodes: {}", style(graph.node_count()).bold());
    println!("  Total edges: {}", style(graph.edge_count()).bold());

    Ok(())
}
