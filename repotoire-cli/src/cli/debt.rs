//! `repotoire debt` command — per-file technical debt risk scores

use anyhow::{Context, Result};
use console::style;
use std::path::Path;

use crate::classifier::debt::{compute_debt, DebtWeights};
use crate::graph::{GraphQuery, GraphStore};
use crate::models::Finding;
use std::collections::HashMap;

/// Load findings from the cached analysis results.
fn load_findings(path: &Path) -> Result<Vec<Finding>> {
    let findings_path = crate::cache::findings_cache_path(path);
    if !findings_path.exists() {
        anyhow::bail!(
            "No findings found. Run {} first.\n\
             Looking for: {}",
            style("repotoire analyze").cyan(),
            findings_path.display()
        );
    }

    let findings_json =
        std::fs::read_to_string(&findings_path).context("Failed to read findings file")?;

    let parsed: serde_json::Value =
        serde_json::from_str(&findings_json).context("Failed to parse findings file")?;
    let findings: Vec<Finding> = serde_json::from_value(
        parsed
            .get("findings")
            .cloned()
            .unwrap_or(serde_json::json!([])),
    )
    .context("Failed to parse findings array")?;

    Ok(findings)
}

/// Load the graph from the cached graph database.
fn load_graph(path: &Path) -> Result<GraphStore> {
    let db_path = crate::cache::graph_db_path(path);
    if !db_path.exists() {
        anyhow::bail!(
            "No graph database found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    GraphStore::new(&db_path).with_context(|| "Failed to open graph database")
}

/// Run the `repotoire debt` command.
pub fn run(path: &Path, path_filter: Option<&str>, top_n: usize) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let findings = load_findings(&repo_path)?;
    let graph = load_graph(&repo_path)?;

    let git_churn: HashMap<String, (f64, usize, f64)> = HashMap::new(); // TODO: populate from git history
    let weights = DebtWeights::default();
    let mut debts = compute_debt(&findings, &graph as &dyn GraphQuery, &git_churn, &weights);

    if let Some(filter) = path_filter {
        debts.retain(|d| d.file_path.contains(filter));
    }

    debts.truncate(top_n);

    if debts.is_empty() {
        println!("No debt hotspots found.");
        return Ok(());
    }

    println!();
    println!(
        "  {:<50} {:>6} {:>8} {:>8} {:>6} {:>5}",
        "File", "Score", "Density", "Couple", "Churn", "Trend"
    );
    println!("  {}", "\u{2500}".repeat(85));

    for debt in &debts {
        let short_path = if debt.file_path.len() > 48 {
            format!("\u{2026}{}", &debt.file_path[debt.file_path.len() - 47..])
        } else {
            debt.file_path.clone()
        };

        let score_style = if debt.risk_score >= 70.0 {
            style(format!("{:>5.1}", debt.risk_score)).red()
        } else if debt.risk_score >= 40.0 {
            style(format!("{:>5.1}", debt.risk_score)).yellow()
        } else {
            style(format!("{:>5.1}", debt.risk_score)).green()
        };

        println!(
            "  {:<50} {} {:>8.1} {:>8.1} {:>6.1} {:>5}",
            short_path,
            score_style,
            debt.finding_density,
            debt.coupling_score,
            debt.churn_score,
            debt.trend,
        );
    }

    println!();
    println!("  Showing top {} files by debt risk score", debts.len());

    Ok(())
}
