//! DOA-based ownership model for bus factor analysis.
//!
//! Implements Degree of Authorship (Avelino et al. 2016) with exponential
//! recency decay. Used by bus factor detectors and knowledge risk reporting.

use std::collections::HashMap;

/// Runtime config for ownership computation (resolved from TOML defaults).
#[derive(Debug, Clone)]
pub struct OwnershipConfig {
    pub half_life_days: f64,
    pub author_threshold: f64,
    pub inactive_months: u32,
    pub min_file_loc: usize,
}

impl Default for OwnershipConfig {
    fn default() -> Self {
        Self {
            half_life_days: 150.0,
            author_threshold: 0.75,
            inactive_months: 6,
            min_file_loc: 10,
        }
    }
}

/// DOA score for one author on one file.
#[derive(Debug, Clone)]
pub struct FileAuthorDOA {
    pub author: String,
    pub email: String,
    pub raw_doa: f64,
    pub normalized_doa: f64,
    pub is_author: bool,
    pub is_first_author: bool,
    pub commit_count: u32,
    pub last_active: i64,
    pub is_active: bool,
}

/// Per-file ownership summary.
#[derive(Debug, Clone)]
pub struct FileOwnershipDOA {
    pub path: String,
    pub authors: Vec<FileAuthorDOA>,
    pub bus_factor: usize,
    pub hhi: f64,
    pub max_doa: f64,
}

/// Per-module (directory) ownership summary.
#[derive(Debug, Clone)]
pub struct ModuleOwnershipSummary {
    pub path: String,
    pub bus_factor: usize,
    pub avg_bus_factor: f64,
    pub hhi: f64,
    pub top_authors: Vec<(String, f64)>,
    pub risk_score: f64,
    pub file_count: usize,
    pub at_risk_file_count: usize,
    pub at_risk_pct: f64,
}

/// Author profile across all files.
#[derive(Debug, Clone)]
pub struct AuthorProfile {
    pub name: String,
    pub email: String,
    pub files_authored: usize,
    pub last_active: i64,
    pub is_active: bool,
}

/// Full ownership model, computed once and shared via Arc.
#[derive(Debug, Clone)]
pub struct OwnershipModel {
    pub files: HashMap<String, FileOwnershipDOA>,
    pub modules: HashMap<String, ModuleOwnershipSummary>,
    pub project_bus_factor: usize,
    pub author_profiles: HashMap<String, AuthorProfile>,
}

impl OwnershipModel {
    /// Empty model for when ownership is disabled or no git data.
    pub fn empty() -> Self {
        Self {
            files: HashMap::new(),
            modules: HashMap::new(),
            project_bus_factor: 0,
            author_profiles: HashMap::new(),
        }
    }
}

/// Compute DOA(e, f) = 3.293 + 1.098*FA + 0.164*DL - 0.321*ln(1+AC)
pub fn compute_doa(is_first_author: bool, dl: f64, ac: f64) -> f64 {
    let fa = if is_first_author { 1.0 } else { 0.0 };
    3.293 + 1.098 * fa + 0.164 * dl - 0.321 * (1.0 + ac).ln()
}

/// Compute exponential decay weight for a commit.
pub fn decay_weight(age_days: f64, half_life_days: f64) -> f64 {
    (-f64::ln(2.0) * age_days / half_life_days).exp()
}

/// Compute HHI from ownership shares. Shares need not sum to 1 -- they are normalized.
pub fn compute_hhi(shares: &[f64]) -> f64 {
    let total: f64 = shares.iter().sum();
    if total <= 0.0 {
        return 1.0;
    }
    shares.iter().map(|s| (s / total).powi(2)).sum()
}

use crate::git::history::CommitInfo;

/// Per-file commit data extracted from git history.
struct FileCommitData {
    first_author: String,
    author_commits: HashMap<String, (String, f64, i64)>,
    total_other_commits: HashMap<String, f64>,
}

/// Build per-file commit data from a flat list of commits.
fn build_file_commit_data(
    commits: &[CommitInfo],
    half_life_days: f64,
    now_ts: i64,
) -> HashMap<String, FileCommitData> {
    let mut files: HashMap<String, FileCommitData> = HashMap::new();

    for commit in commits {
        let commit_ts = chrono::DateTime::parse_from_rfc3339(&commit.timestamp)
            .map(|dt| dt.timestamp())
            .unwrap_or(0);
        let age_days = ((now_ts - commit_ts) as f64 / 86400.0).max(0.0);
        let weight = decay_weight(age_days, half_life_days);

        for file_path in &commit.files_changed {
            let data = files.entry(file_path.clone()).or_insert_with(|| FileCommitData {
                first_author: commit.author.clone(),
                author_commits: HashMap::new(),
                total_other_commits: HashMap::new(),
            });

            let entry = data
                .author_commits
                .entry(commit.author.clone())
                .or_insert((commit.author_email.clone(), 0.0, 0));
            entry.1 += weight;
            if commit_ts > entry.2 {
                entry.2 = commit_ts;
            }
        }
    }

    // Fix first_author: oldest commit touching each file
    for (file_path, data) in &mut files {
        for commit in commits.iter().rev() {
            if commit.files_changed.contains(file_path) {
                data.first_author = commit.author.clone();
                break;
            }
        }

        let total_weighted: f64 = data.author_commits.values().map(|v| v.1).sum();
        for (author, (_, weighted, _)) in &data.author_commits {
            data.total_other_commits
                .insert(author.clone(), total_weighted - weighted);
        }
    }

    files
}

/// Compute the full ownership model from git history.
pub fn compute_ownership_model(
    commits: &[CommitInfo],
    config: &OwnershipConfig,
) -> OwnershipModel {
    if commits.is_empty() {
        return OwnershipModel::empty();
    }

    let now_ts = chrono::Utc::now().timestamp();
    let inactive_cutoff = now_ts - (config.inactive_months as i64 * 30 * 86400);

    let file_data = build_file_commit_data(commits, config.half_life_days, now_ts);

    let mut files: HashMap<String, FileOwnershipDOA> = HashMap::new();
    let mut author_profiles: HashMap<String, AuthorProfile> = HashMap::new();

    for (path, data) in &file_data {
        let mut author_doas: Vec<FileAuthorDOA> = Vec::new();

        for (author, (email, weighted_commits, last_ts)) in &data.author_commits {
            let is_first = *author == data.first_author;
            let ac = data.total_other_commits.get(author).copied().unwrap_or(0.0);
            let raw_doa = compute_doa(is_first, *weighted_commits, ac);

            author_doas.push(FileAuthorDOA {
                author: author.clone(),
                email: email.clone(),
                raw_doa,
                normalized_doa: 0.0,
                is_author: false,
                is_first_author: is_first,
                commit_count: *weighted_commits as u32,
                last_active: *last_ts,
                is_active: *last_ts >= inactive_cutoff,
            });

            let profile = author_profiles
                .entry(author.clone())
                .or_insert_with(|| AuthorProfile {
                    name: author.clone(),
                    email: email.clone(),
                    files_authored: 0,
                    last_active: 0,
                    is_active: false,
                });
            if *last_ts > profile.last_active {
                profile.last_active = *last_ts;
                profile.is_active = *last_ts >= inactive_cutoff;
            }
        }

        if !author_doas.is_empty() {
            let min_doa = author_doas
                .iter()
                .map(|a| a.raw_doa)
                .fold(f64::INFINITY, f64::min);
            let max_doa = author_doas
                .iter()
                .map(|a| a.raw_doa)
                .fold(f64::NEG_INFINITY, f64::max);
            let range = max_doa - min_doa;

            for a in &mut author_doas {
                a.normalized_doa = if range > 0.0 {
                    (a.raw_doa - min_doa) / range
                } else {
                    1.0
                };
                a.is_author = a.normalized_doa > config.author_threshold && a.raw_doa > 3.293;
            }
        }

        for a in &author_doas {
            if a.is_author {
                if let Some(p) = author_profiles.get_mut(&a.author) {
                    p.files_authored += 1;
                }
            }
        }

        author_doas.sort_by(|a, b| {
            b.raw_doa
                .partial_cmp(&a.raw_doa)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let bus_factor = author_doas.iter().filter(|a| a.is_author).count();
        let shares: Vec<f64> = author_doas.iter().map(|a| a.normalized_doa).collect();
        let hhi = compute_hhi(&shares);
        let max_doa_val = author_doas.first().map(|a| a.normalized_doa).unwrap_or(0.0);

        files.insert(
            path.clone(),
            FileOwnershipDOA {
                path: path.clone(),
                authors: author_doas,
                bus_factor,
                hhi,
                max_doa: max_doa_val,
            },
        );
    }

    let modules = aggregate_modules(&files);
    let project_bus_factor = compute_project_bus_factor(&files);

    OwnershipModel {
        files,
        modules,
        project_bus_factor,
        author_profiles,
    }
}

/// Aggregate file ownership into module (directory) summaries.
fn aggregate_modules(
    files: &HashMap<String, FileOwnershipDOA>,
) -> HashMap<String, ModuleOwnershipSummary> {
    use crate::detectors::base::is_non_production_file;

    let mut module_files: HashMap<String, Vec<&FileOwnershipDOA>> = HashMap::new();

    for (path, ownership) in files {
        if is_non_production_file(std::path::Path::new(path)) {
            continue;
        }
        let dir = std::path::Path::new(path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".")
            .to_string();
        module_files.entry(dir).or_default().push(ownership);
    }

    let mut modules = HashMap::new();
    for (dir, file_ownerships) in &module_files {
        if file_ownerships.is_empty() {
            continue;
        }

        let file_count = file_ownerships.len();
        let mut bus_factors: Vec<usize> = file_ownerships.iter().map(|f| f.bus_factor).collect();
        bus_factors.sort();

        let p10_idx = (file_count as f64 * 0.1).ceil() as usize;
        let p10_idx = p10_idx.saturating_sub(1).min(bus_factors.len() - 1);
        let bus_factor = bus_factors[p10_idx];

        let avg_bus_factor = bus_factors.iter().sum::<usize>() as f64 / file_count as f64;
        let avg_hhi = file_ownerships.iter().map(|f| f.hhi).sum::<f64>() / file_count as f64;
        let at_risk_count = file_ownerships.iter().filter(|f| f.bus_factor <= 1).count();
        let at_risk_pct = at_risk_count as f64 / file_count as f64;

        let mut author_totals: HashMap<String, f64> = HashMap::new();
        for fo in file_ownerships {
            for a in &fo.authors {
                *author_totals.entry(a.author.clone()).or_default() += a.normalized_doa;
            }
        }
        let mut top_authors: Vec<(String, f64)> = author_totals.into_iter().collect();
        top_authors.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        top_authors.truncate(3);

        let risk_score = ((1.0 - avg_bus_factor / 5.0).max(0.0) * 0.4
            + avg_hhi * 0.3
            + at_risk_pct * 0.3)
            .clamp(0.0, 1.0);

        modules.insert(
            dir.clone(),
            ModuleOwnershipSummary {
                path: dir.clone(),
                bus_factor,
                avg_bus_factor,
                hhi: avg_hhi,
                top_authors,
                risk_score,
                file_count,
                at_risk_file_count: at_risk_count,
                at_risk_pct,
            },
        );
    }

    modules
}

/// Greedy set cover: minimum authors to remove before >50% files are orphaned.
fn compute_project_bus_factor(files: &HashMap<String, FileOwnershipDOA>) -> usize {
    let mut author_files: HashMap<String, Vec<String>> = HashMap::new();
    for (path, fo) in files {
        for a in &fo.authors {
            if a.is_author {
                author_files
                    .entry(a.author.clone())
                    .or_default()
                    .push(path.clone());
            }
        }
    }

    let total_files = files.len();
    if total_files == 0 {
        return 0;
    }

    let mut file_author_count: HashMap<String, usize> = files
        .iter()
        .map(|(p, fo)| {
            (
                p.clone(),
                fo.authors.iter().filter(|a| a.is_author).count(),
            )
        })
        .collect();

    let threshold = total_files / 2;
    let mut removed = 0;

    loop {
        let covered = file_author_count.values().filter(|&&c| c > 0).count();
        if covered <= threshold {
            break;
        }

        let best_author = author_files
            .iter()
            .filter(|(_, af)| !af.is_empty())
            .max_by_key(|(_, af)| {
                af.iter()
                    .filter(|f| file_author_count.get(*f).copied().unwrap_or(0) > 0)
                    .count()
            });

        let best = match best_author {
            Some((author, _)) => author.clone(),
            None => break,
        };

        if let Some(affected_files) = author_files.remove(&best) {
            for f in &affected_files {
                if let Some(count) = file_author_count.get_mut(f) {
                    *count = count.saturating_sub(1);
                }
            }
        }
        removed += 1;
    }

    removed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_doa_first_author_no_others() {
        let doa = compute_doa(true, 10.0, 0.0);
        assert!((doa - 6.031).abs() < 0.001, "got {doa}");
    }

    #[test]
    fn test_doa_non_first_author() {
        let doa = compute_doa(false, 5.0, 20.0);
        assert!((doa - 3.135).abs() < 0.05, "got {doa}");
    }

    #[test]
    fn test_decay_weight_zero_age() {
        assert!((decay_weight(0.0, 150.0) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_decay_weight_one_half_life() {
        assert!((decay_weight(150.0, 150.0) - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_decay_weight_two_half_lives() {
        assert!((decay_weight(300.0, 150.0) - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_hhi_single_author() {
        assert!((compute_hhi(&[1.0]) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_hhi_equal_four() {
        assert!((compute_hhi(&[0.25, 0.25, 0.25, 0.25]) - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_hhi_dominated() {
        assert!((compute_hhi(&[0.9, 0.1]) - 0.82).abs() < 0.001);
    }

    #[test]
    fn test_hhi_empty() {
        assert!((compute_hhi(&[]) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_project_bus_factor_single_author() {
        let mut files = HashMap::new();
        for i in 0..10 {
            files.insert(
                format!("file{i}.rs"),
                FileOwnershipDOA {
                    path: format!("file{i}.rs"),
                    authors: vec![FileAuthorDOA {
                        author: "alice".into(),
                        email: "a@x.com".into(),
                        raw_doa: 5.0,
                        normalized_doa: 1.0,
                        is_author: true,
                        is_first_author: true,
                        commit_count: 10,
                        last_active: 0,
                        is_active: true,
                    }],
                    bus_factor: 1,
                    hhi: 1.0,
                    max_doa: 1.0,
                },
            );
        }
        assert_eq!(compute_project_bus_factor(&files), 1);
    }

    #[test]
    fn test_project_bus_factor_two_authors_even_split() {
        let mut files = HashMap::new();
        for i in 0..10 {
            let author = if i < 5 { "alice" } else { "bob" };
            files.insert(
                format!("file{i}.rs"),
                FileOwnershipDOA {
                    path: format!("file{i}.rs"),
                    authors: vec![FileAuthorDOA {
                        author: author.into(),
                        email: "x@x.com".into(),
                        raw_doa: 5.0,
                        normalized_doa: 1.0,
                        is_author: true,
                        is_first_author: true,
                        commit_count: 10,
                        last_active: 0,
                        is_active: true,
                    }],
                    bus_factor: 1,
                    hhi: 1.0,
                    max_doa: 1.0,
                },
            );
        }
        assert_eq!(compute_project_bus_factor(&files), 1);
    }

    #[test]
    fn test_empty_model() {
        let model = OwnershipModel::empty();
        assert_eq!(model.project_bus_factor, 0);
        assert!(model.files.is_empty());
    }
}
