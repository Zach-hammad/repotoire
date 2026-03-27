//! `repotoire debt` command — per-file technical debt risk scores

use anyhow::{Context, Result};
use console::style;
use std::collections::HashMap;
use std::path::Path;

use crate::classifier::debt::{compute_debt, DebtWeights};
use crate::graph::{CodeGraph, GraphQuery};
use crate::models::Finding;

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

/// Load the graph from the session cache (bincode format).
fn load_graph(path: &Path) -> Result<CodeGraph> {
    let session_dir = crate::cache::paths::cache_dir(path).join("session");
    let graph_path = session_dir.join("graph.bin");
    if !graph_path.exists() {
        anyhow::bail!(
            "No graph database found. Run {} first.",
            style("repotoire analyze").cyan()
        );
    }

    CodeGraph::load_cache(&graph_path)
        .ok_or_else(|| anyhow::anyhow!("Failed to load graph cache (corrupt or version mismatch). Run {} again.", style("repotoire analyze").cyan()))
}

/// Run the `repotoire debt` command.
pub fn run(path: &Path, path_filter: Option<&str>, top_n: usize) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    let findings = load_findings(&repo_path)?;
    let graph = load_graph(&repo_path)?;

    let git_churn = compute_churn_for_debt(&repo_path);
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

/// Compute per-file churn data for debt scoring from git history.
///
/// Returns `(churn_score, author_count, age_days)` per file path.
/// Uses the fast 90-day/500-commit window from `compute_file_churn`,
/// then enriches with author counts from `get_recent_commits`.
/// Returns an empty map if git is unavailable.
fn compute_churn_for_debt(repo_path: &Path) -> HashMap<String, (f64, usize, f64)> {
    use std::collections::HashSet;

    let history = match crate::git::history::GitHistory::open(repo_path) {
        Ok(h) => h,
        Err(_) => return HashMap::new(),
    };

    let since = chrono::Utc::now() - chrono::Duration::days(90);
    let commits = match history.get_recent_commits(500, Some(since)) {
        Ok(c) => c,
        Err(_) => return HashMap::new(),
    };

    // Accumulate per-file: commit count, unique authors, most recent timestamp
    struct ChurnAccum {
        commits: u32,
        authors: HashSet<String>,
        latest_ts: Option<chrono::DateTime<chrono::Utc>>,
    }

    let mut accum: HashMap<String, ChurnAccum> = HashMap::new();

    for commit in &commits {
        let ts = chrono::DateTime::parse_from_rfc3339(&commit.timestamp)
            .map(|dt| dt.with_timezone(&chrono::Utc))
            .ok();

        for file in &commit.files_changed {
            let entry = accum.entry(file.clone()).or_insert_with(|| ChurnAccum {
                commits: 0,
                authors: HashSet::new(),
                latest_ts: None,
            });
            entry.commits += 1;
            entry.authors.insert(commit.author.clone());
            if let Some(t) = ts {
                entry.latest_ts = Some(match entry.latest_ts {
                    Some(prev) if prev > t => prev,
                    _ => t,
                });
            }
        }
    }

    let now = chrono::Utc::now();
    accum
        .into_iter()
        .map(|(path, a)| {
            let churn_score = (a.commits as f64).min(100.0);
            let author_count = a.authors.len();
            let age_days = a
                .latest_ts
                .map(|ts| (now - ts).num_days().max(0) as f64)
                .unwrap_or(365.0);
            (path, (churn_score, author_count, age_days))
        })
        .collect()
}
