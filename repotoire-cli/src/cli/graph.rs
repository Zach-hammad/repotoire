//! Graph command - query the code graph directly

use crate::graph::GraphStore;
use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run a query against the code graph
///
/// Note: Cypher queries are no longer supported. Use the built-in query commands instead.
pub fn run(path: &Path, query: &str, format: &str) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let db_path = crate::cache::graph_db_path(&repo_path);
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphStore::new(&db_path).with_context(|| "Failed to open graph database")?;

    let json_output = format == "json";

    // Parse simple query patterns
    let query_lower = query.to_lowercase();

    if query_lower.contains("function") {
        query_functions(&graph, json_output)?;
    } else if query_lower.contains("class") {
        query_classes(&graph, json_output)?;
    } else if query_lower.contains("file") {
        query_files(&graph, json_output)?;
    } else if query_lower.contains("call") {
        query_calls(&graph, json_output)?;
    } else if query_lower.contains("import") {
        query_imports(&graph, json_output)?;
    } else if query_lower == "stats" {
        // Redirect to stats command
        return stats(path);
    } else {
        print_usage();
    }

    Ok(())
}

fn query_functions(graph: &GraphStore, json_output: bool) -> Result<()> {
    let i = graph.interner();
    let functions = graph.get_functions();
    if json_output {
        let json: Vec<_> = functions.iter().map(function_to_json).collect();
        println!("{}", serde_json::to_string_pretty(&json)?);
    } else {
        println!("\n{} Functions ({})\n", style("📊").bold(), functions.len());
        for func in functions.iter().take(50) {
            println!(
                "  {} ({}:{})",
                style(func.qn(i)).cyan(),
                func.path(i),
                func.line_start
            );
        }
        if functions.len() > 50 {
            println!("  ... and {} more", functions.len() - 50);
        }
    }
    Ok(())
}

fn query_classes(graph: &GraphStore, json_output: bool) -> Result<()> {
    let i = graph.interner();
    let classes = graph.get_classes();
    if json_output {
        let json: Vec<_> = classes.iter().map(class_to_json).collect();
        println!("{}", serde_json::to_string_pretty(&json)?);
    } else {
        println!("\n{} Classes ({})\n", style("📊").bold(), classes.len());
        for class in classes.iter().take(50) {
            println!(
                "  {} ({}:{})",
                style(class.qn(i)).cyan(),
                class.path(i),
                class.line_start
            );
        }
        if classes.len() > 50 {
            println!("  ... and {} more", classes.len() - 50);
        }
    }
    Ok(())
}

fn query_files(graph: &GraphStore, json_output: bool) -> Result<()> {
    let i = graph.interner();
    let files = graph.get_files();
    if json_output {
        let json: Vec<_> = files.iter().map(|f| serde_json::json!({"path": f.path(i)})).collect();
        println!("{}", serde_json::to_string_pretty(&json)?);
    } else {
        println!("\n{} Files ({})\n", style("📊").bold(), files.len());
        for file in files.iter().take(50) {
            println!("  {}", style(file.path(i)).cyan());
        }
        if files.len() > 50 {
            println!("  ... and {} more", files.len() - 50);
        }
    }
    Ok(())
}

fn query_calls(graph: &GraphStore, json_output: bool) -> Result<()> {
    let i = graph.interner();
    let calls = graph.get_calls();
    if json_output {
        let json: Vec<_> = calls.iter().map(|(from, to)| serde_json::json!({"from": i.resolve(*from), "to": i.resolve(*to)})).collect();
        println!("{}", serde_json::to_string_pretty(&json)?);
    } else {
        println!("\n{} Call Edges ({})\n", style("📊").bold(), calls.len());
        for (from, to) in calls.iter().take(50) {
            println!("  {} -> {}", style(i.resolve(*from)).cyan(), style(i.resolve(*to)).green());
        }
        if calls.len() > 50 {
            println!("  ... and {} more", calls.len() - 50);
        }
    }
    Ok(())
}

fn query_imports(graph: &GraphStore, json_output: bool) -> Result<()> {
    let i = graph.interner();
    let imports = graph.get_imports();
    if json_output {
        let json: Vec<_> = imports.iter().map(|(from, to)| serde_json::json!({"from": i.resolve(*from), "to": i.resolve(*to)})).collect();
        println!("{}", serde_json::to_string_pretty(&json)?);
    } else {
        println!(
            "\n{} Import Edges ({})\n",
            style("📊").bold(),
            imports.len()
        );
        for (from, to) in imports.iter().take(50) {
            println!("  {} -> {}", style(i.resolve(*from)).cyan(), style(i.resolve(*to)).green());
        }
        if imports.len() > 50 {
            println!("  ... and {} more", imports.len() - 50);
        }
    }
    Ok(())
}

fn print_usage() {
    println!("{}", style("Supported queries:").bold());
    println!("  - functions: List all functions");
    println!("  - classes: List all classes");
    println!("  - files: List all files");
    println!("  - calls: List call edges");
    println!("  - imports: List import edges");
    println!("  - stats: Show graph statistics");
    println!("\nNote: Cypher queries are not supported. You can also run 'repotoire stats' directly.");
}

/// Show graph statistics
pub fn stats(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    // Try to read from cached JSON stats first (avoids redb lock issues)
    let stats_path = crate::cache::graph_stats_path(&repo_path);
    if stats_path.exists() {
        let stats_json =
            std::fs::read_to_string(&stats_path).with_context(|| "Failed to read graph stats")?;
        let stats: serde_json::Value =
            serde_json::from_str(&stats_json).with_context(|| "Failed to parse graph stats")?;

        println!("\n{} Graph Statistics\n", style("📊").bold());

        // Node counts
        println!(
            "  {}: {}",
            style("Files").cyan(),
            style(stats["total_files"].as_u64().unwrap_or(0)).bold()
        );
        println!(
            "  {}: {}",
            style("Functions").cyan(),
            style(stats["total_functions"].as_u64().unwrap_or(0)).bold()
        );
        println!(
            "  {}: {}",
            style("Classes").cyan(),
            style(stats["total_classes"].as_u64().unwrap_or(0)).bold()
        );

        // Edge counts by type
        let calls = stats["calls"].as_u64().unwrap_or(0);
        let imports = stats["imports"].as_u64().unwrap_or(0);
        let total_edges = stats["total_edges"].as_u64().unwrap_or(0);
        let contains = total_edges.saturating_sub(calls + imports);
        println!();
        println!("  {} edges: {}", style("CALLS").cyan(), style(calls).bold());
        println!(
            "  {} edges: {}",
            style("IMPORTS").cyan(),
            style(imports).bold()
        );
        println!(
            "  {} edges: {}",
            style("CONTAINS").cyan(),
            style(contains).bold()
        );

        // Total
        println!();
        println!(
            "  Total nodes: {}",
            style(stats["total_nodes"].as_u64().unwrap_or(0)).bold()
        );
        println!("  Total edges: {}", style(total_edges).bold());

        return Ok(());
    }

    // Fallback to opening redb database (may fail with lock issues)
    let db_path = crate::cache::graph_db_path(&repo_path);
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphStore::new(&db_path).with_context(|| "Failed to open graph database")?;

    println!("\n{} Graph Statistics\n", style("📊").bold());

    let stats = graph.stats();

    // Node counts (stats uses "total_*" keys)
    println!(
        "  {}: {}",
        style("Files").cyan(),
        style(stats.get("total_files").copied().unwrap_or(0)).bold()
    );
    println!(
        "  {}: {}",
        style("Functions").cyan(),
        style(stats.get("total_functions").copied().unwrap_or(0)).bold()
    );
    println!(
        "  {}: {}",
        style("Classes").cyan(),
        style(stats.get("total_classes").copied().unwrap_or(0)).bold()
    );

    // Edge counts by type
    let calls = graph.get_calls().len();
    let imports = graph.get_imports().len();
    let contains = graph.edge_count() - calls - imports;
    println!();
    println!("  {} edges: {}", style("CALLS").cyan(), style(calls).bold());
    println!(
        "  {} edges: {}",
        style("IMPORTS").cyan(),
        style(imports).bold()
    );
    println!(
        "  {} edges: {}",
        style("CONTAINS").cyan(),
        style(contains).bold()
    );

    // Total
    println!();
    println!("  Total nodes: {}", style(graph.node_count()).bold());
    println!("  Total edges: {}", style(graph.edge_count()).bold());

    Ok(())
}

fn function_to_json(f: &crate::graph::CodeNode) -> serde_json::Value {
    let i = crate::graph::interner::global_interner();
    serde_json::json!({
        "name": f.node_name(i),
        "qualified_name": f.qn(i),
        "file": f.path(i),
        "line_start": f.line_start,
        "line_end": f.line_end,
    })
}

fn class_to_json(c: &crate::graph::CodeNode) -> serde_json::Value {
    let i = crate::graph::interner::global_interner();
    serde_json::json!({
        "name": c.node_name(i),
        "qualified_name": c.qn(i),
        "file": c.path(i),
        "line_start": c.line_start,
        "line_end": c.line_end,
    })
}
