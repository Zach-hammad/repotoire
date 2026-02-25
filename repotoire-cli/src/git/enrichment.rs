//! Git enrichment for the code graph
//!
//! Enriches Function and Class nodes with git history data:
//! - last_modified timestamp
//! - author of last modification
//! - commit_count
//! - Creates Commit nodes and MODIFIED_IN relationships

use anyhow::Result;
use std::collections::HashSet;
use std::path::Path;
use tracing::{debug, info};

use super::blame::GitBlame;
use super::history::GitHistory;
use crate::graph::{CodeEdge, CodeNode, EdgeKind, GraphStore, NodeKind};

/// Statistics from git enrichment.
#[derive(Debug, Clone, Default)]
pub struct EnrichmentStats {
    /// Number of functions enriched with git data
    pub functions_enriched: usize,
    /// Number of classes enriched with git data
    pub classes_enriched: usize,
    /// Number of Commit nodes created
    pub commits_created: usize,
    /// Number of MODIFIED_IN edges created
    pub edges_created: usize,
    /// Files skipped (not in git)
    pub files_skipped: usize,
    /// Files loaded from disk cache
    pub cache_hits: usize,
    /// Files computed fresh
    pub cache_misses: usize,
}

/// Git enricher for the code graph.
pub struct GitEnricher<'a> {
    blame: GitBlame,
    #[allow(dead_code)] // Stored for future commit history analysis
    history: &'a GitHistory,
    graph: &'a GraphStore,
    /// Track commits we've already created
    #[allow(dead_code)] // Used by create_commit_if_needed
    seen_commits: HashSet<String>,
}

impl<'a> GitEnricher<'a> {
    /// Create a new git enricher.
    pub fn new(history: &'a GitHistory, graph: &'a GraphStore) -> Result<Self> {
        let repo_root = history.repo_root()?;
        let blame = GitBlame::open(repo_root)?;
        Ok(Self {
            blame,
            history,
            graph,
            seen_commits: HashSet::new(),
        })
    }

    /// Enrich all Function and Class nodes with git data.
    pub fn enrich_all(&mut self) -> Result<EnrichmentStats> {
        let mut stats = EnrichmentStats::default();

        // Collect all unique files from functions and classes
        let functions = self.graph.get_functions();
        let classes = self.graph.get_classes();

        let mut unique_files: HashSet<String> = HashSet::new();
        for f in &functions {
            if f.get_str("last_modified").is_none() {
                unique_files.insert(f.file_path.clone());
            }
        }
        for c in &classes {
            if c.get_str("last_modified").is_none() {
                unique_files.insert(c.file_path.clone());
            }
        }

        // Pre-warm blame cache in parallel (uses disk cache for unchanged files)
        let file_list: Vec<String> = unique_files.into_iter().collect();
        let (cache_hits, cache_misses) = if !file_list.is_empty() {
            info!(
                "Pre-warming git blame cache for {} files...",
                file_list.len()
            );
            let (hits, misses) = self.blame.prewarm_cache(&file_list);
            debug!("Git cache: {} hits, {} computed", hits, misses);
            (hits, misses)
        } else {
            (0, 0)
        };
        stats.cache_hits = cache_hits;
        stats.cache_misses = cache_misses;

        // Enrich Functions (now just cache lookups)
        info!("Enriching Function nodes with git history...");
        let func_stats = self.enrich_functions()?;
        stats.functions_enriched = func_stats.functions_enriched;
        stats.commits_created += func_stats.commits_created;
        stats.edges_created += func_stats.edges_created;

        // Enrich Classes (now just cache lookups)
        info!("Enriching Class nodes with git history...");
        let class_stats = self.enrich_classes()?;
        stats.classes_enriched = class_stats.classes_enriched;
        stats.commits_created += class_stats.commits_created;
        stats.edges_created += class_stats.edges_created;

        info!(
            "Git enrichment complete: {} functions, {} classes, {} commits, {} edges",
            stats.functions_enriched,
            stats.classes_enriched,
            stats.commits_created,
            stats.edges_created
        );

        Ok(stats)
    }

    /// Enrich Function nodes with git data.
    fn enrich_functions(&mut self) -> Result<EnrichmentStats> {
        let mut stats = EnrichmentStats::default();

        // Get all functions without git data
        let functions = self.graph.get_functions();
        let functions_to_enrich: Vec<_> = functions
            .into_iter()
            .filter(|f| f.get_str("last_modified").is_none())
            .collect();

        let total = functions_to_enrich.len();
        debug!("Found {} functions to enrich", total);

        for (i, func) in functions_to_enrich.into_iter().enumerate() {
            if i > 0 && i % 500 == 0 {
                debug!("Enriched {}/{} functions", i, total);
            }

            let line_start = func.line_start;
            let line_end = func.line_end;

            if line_start == 0 {
                continue;
            }

            // Get blame info for this function
            let blame_result = self
                .blame
                .get_entity_blame(&func.file_path, line_start, line_end)
                .inspect_err(|e| {
                    debug!("Failed to get blame for {}:{}: {}", func.file_path, line_start, e);
                });
            let Ok(blame_info) = blame_result else {
                stats.files_skipped += 1;
                continue;
            };
            let Some(last_modified) = &blame_info.last_modified else {
                continue;
            };
            let Some(author) = &blame_info.last_author else {
                continue;
            };
            self.graph.update_node_properties(
                &func.qualified_name,
                &[
                    (
                        "last_modified",
                        serde_json::Value::String(last_modified.clone()),
                    ),
                    ("author", serde_json::Value::String(author.clone())),
                    (
                        "commit_count",
                        serde_json::Value::Number((blame_info.commit_count as i64).into()),
                    ),
                ],
            );
            stats.functions_enriched += 1;
        }

        Ok(stats)
    }

    /// Enrich Class nodes with git data.
    fn enrich_classes(&mut self) -> Result<EnrichmentStats> {
        let mut stats = EnrichmentStats::default();

        // Get all classes without git data
        let classes = self.graph.get_classes();
        let classes_to_enrich: Vec<_> = classes
            .into_iter()
            .filter(|c| c.get_str("last_modified").is_none())
            .collect();

        let total = classes_to_enrich.len();
        debug!("Found {} classes to enrich", total);

        for (i, class) in classes_to_enrich.into_iter().enumerate() {
            if i > 0 && i % 50 == 0 {
                debug!("Enriched {}/{} classes", i, total);
            }

            let line_start = class.line_start;
            let line_end = class.line_end;

            if line_start == 0 {
                continue;
            }

            // Get blame info for this class
            let blame_result = self
                .blame
                .get_entity_blame(&class.file_path, line_start, line_end)
                .inspect_err(|e| {
                    debug!("Failed to get blame for {}:{}: {}", class.file_path, line_start, e);
                });
            let Ok(blame_info) = blame_result else {
                stats.files_skipped += 1;
                continue;
            };

            let (Some(last_modified), Some(author)) =
                (&blame_info.last_modified, &blame_info.last_author)
            else {
                continue;
            };

            // Update class with git data (skip Commit nodes for speed)
            self.graph.update_node_properties(
                &class.qualified_name,
                &[
                    (
                        "last_modified",
                        serde_json::Value::String(last_modified.clone()),
                    ),
                    ("author", serde_json::Value::String(author.clone())),
                    (
                        "commit_count",
                        serde_json::Value::Number(
                            (blame_info.commit_count as i64).into(),
                        ),
                    ),
                ],
            );
            stats.classes_enriched += 1;
        }

        Ok(stats)
    }

    /// Create a Commit node if it doesn't already exist.
    #[allow(dead_code)] // Infrastructure for git graph enrichment
    fn create_commit_if_needed(&mut self, hash: &str, author: &str, timestamp: &str) -> bool {
        if self.seen_commits.contains(hash) {
            return false;
        }

        // Create commit node
        let node = CodeNode::new(NodeKind::Commit, hash, "")
            .with_qualified_name(hash)
            .with_property("author", author)
            .with_property("timestamp", timestamp);

        self.graph.add_node(node);
        self.seen_commits.insert(hash.to_string());
        true
    }

    /// Create a MODIFIED_IN edge from entity to commit.
    #[allow(dead_code)] // Infrastructure for git graph enrichment
    fn create_modified_in_edge(&self, entity_qn: &str, commit_hash: &str) -> bool {
        self.graph
            .add_edge_by_name(entity_qn, commit_hash, CodeEdge::new(EdgeKind::ModifiedIn))
    }
}

/// Convenience function to enrich a graph with git data.
pub fn enrich_graph_with_git(
    repo_path: &Path,
    graph: &GraphStore,
    _repo_id: Option<&str>,
) -> Result<EnrichmentStats> {
    let history = GitHistory::new(repo_path)?;
    let mut enricher = GitEnricher::new(&history, graph)?;
    enricher.enrich_all()
}
