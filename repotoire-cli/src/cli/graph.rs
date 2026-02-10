//! Graph command - query the code graph directly

use crate::graph::GraphClient;
use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run a Cypher query against the code graph
pub fn run(path: &Path, query: &str, format: &str) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let db_path = repo_path.join(".repotoire").join("kuzu_db");
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphClient::new(&db_path)
        .with_context(|| "Failed to open graph database")?;

    let results = graph.execute(query)
        .with_context(|| format!("Query failed: {}", query))?;

    match format {
        "json" => {
            let json = serde_json::to_string_pretty(&results)?;
            println!("{}", json);
        }
        "table" | "text" => {
            if results.is_empty() {
                println!("{}", style("No results").dim());
                return Ok(());
            }

            // Get column names from first row
            if let Some(first_row) = results.first() {
                let columns: Vec<&str> = first_row.keys().map(|s| s.as_str()).collect();
                
                // Print header
                println!("{}", style(columns.join(" | ")).bold());
                println!("{}", "-".repeat(columns.len() * 20));

                // Print rows
                for row in &results {
                    let values: Vec<String> = columns
                        .iter()
                        .map(|col| {
                            row.get(*col)
                                .map(|v| format!("{}", v))
                                .unwrap_or_else(|| "null".to_string())
                        })
                        .collect();
                    println!("{}", values.join(" | "));
                }
            }

            println!("\n{} {} rows", style("→").dim(), results.len());
        }
        _ => {
            anyhow::bail!("Unknown format: {}. Use 'json' or 'table'", format);
        }
    }

    Ok(())
}

/// Show graph statistics
pub fn stats(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let db_path = repo_path.join(".repotoire").join("kuzu_db");
    if !db_path.exists() {
        anyhow::bail!(
            "No analysis found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    let graph = GraphClient::new(&db_path)
        .with_context(|| "Failed to open graph database")?;

    println!("\n{} Graph Statistics\n", style("📊").bold());

    // Count nodes
    let queries = vec![
        ("Files", "MATCH (n:File) RETURN count(n) AS count"),
        ("Functions", "MATCH (n:Function) RETURN count(n) AS count"),
        ("Classes", "MATCH (n:Class) RETURN count(n) AS count"),
        ("Commits", "MATCH (n:Commit) RETURN count(n) AS count"),
    ];

    for (label, query) in queries {
        match graph.execute(query) {
            Ok(results) => {
                let count = results
                    .first()
                    .and_then(|r| r.get("count"))
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                println!("  {}: {}", style(label).cyan(), style(count).bold());
            }
            Err(_) => {
                println!("  {}: {}", style(label).cyan(), style("N/A").dim());
            }
        }
    }

    // Count edges
    println!();
    let edge_queries = vec![
        ("CALLS", "MATCH ()-[r:CALLS]->() RETURN count(r) AS count"),
        ("CONTAINS", "MATCH ()-[r:CONTAINS_FUNCTION|CONTAINS_CLASS|CONTAINS_METHOD]->() RETURN count(r) AS count"),
        ("IMPORTS", "MATCH ()-[r:IMPORTS|IMPORTS_FILE]->() RETURN count(r) AS count"),
    ];

    for (label, query) in edge_queries {
        match graph.execute(query) {
            Ok(results) => {
                let count = results
                    .first()
                    .and_then(|r| r.get("count"))
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                println!("  {} edges: {}", style(label).cyan(), style(count).bold());
            }
            Err(_) => {
                println!("  {} edges: {}", style(label).cyan(), style("N/A").dim());
            }
        }
    }

    Ok(())
}
