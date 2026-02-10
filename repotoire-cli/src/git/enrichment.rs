//! Git enrichment for the code graph
//!
//! Enriches Function and Class nodes with git history data:
//! - last_modified timestamp
//! - author of last modification
//! - commit_count
//! - Creates Commit nodes and MODIFIED_IN relationships

use anyhow::{Context, Result};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use tracing::{debug, info, warn};

use super::blame::GitBlame;
use super::history::GitHistory;
use crate::graph::GraphClient;

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
}

/// Git enricher for the code graph.
pub struct GitEnricher<'a> {
    blame: GitBlame,
    history: &'a GitHistory,
    graph: &'a GraphClient,
    /// Repository ID for multi-tenant isolation
    repo_id: Option<String>,
    /// Track commits we've already created
    seen_commits: HashSet<String>,
}

impl<'a> GitEnricher<'a> {
    /// Create a new git enricher.
    ///
    /// # Arguments
    /// * `history` - Git history analyzer
    /// * `graph` - Graph database client
    pub fn new(history: &'a GitHistory, graph: &'a GraphClient) -> Result<Self> {
        let repo_root = history.repo_root()?;
        let blame = GitBlame::open(repo_root)?;
        Ok(Self {
            blame,
            history,
            graph,
            repo_id: None,
            seen_commits: HashSet::new(),
        })
    }

    /// Set repository ID for multi-tenant isolation.
    pub fn with_repo_id(mut self, repo_id: impl Into<String>) -> Self {
        self.repo_id = Some(repo_id.into());
        self
    }

    /// Enrich all Function and Class nodes with git data.
    pub fn enrich_all(&mut self) -> Result<EnrichmentStats> {
        let mut stats = EnrichmentStats::default();

        // Enrich Functions
        info!("Enriching Function nodes with git history...");
        let func_stats = self.enrich_functions()?;
        stats.functions_enriched = func_stats.functions_enriched;
        stats.commits_created += func_stats.commits_created;
        stats.edges_created += func_stats.edges_created;

        // Enrich Classes
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

        // Query functions that need enrichment
        let query = r#"
            MATCH (f:Function)
            WHERE f.last_modified IS NULL AND f.filePath IS NOT NULL
            RETURN f.qualifiedName AS qn, f.filePath AS file_path, 
                   f.lineStart AS line_start, f.lineEnd AS line_end
        "#;

        let functions = self.graph.execute_safe(query);
        let total = functions.len();
        debug!("Found {} functions to enrich", total);

        for (i, func) in functions.into_iter().enumerate() {
            if i > 0 && i % 100 == 0 {
                debug!("Enriched {}/{} functions", i, total);
            }

            let qn = match func.get("qn").and_then(|v| v.as_str()) {
                Some(s) => s.to_string(),
                None => continue,
            };

            let file_path = match func.get("file_path").and_then(|v| v.as_str()) {
                Some(s) => s.to_string(),
                None => continue,
            };

            let line_start = func.get("line_start").and_then(|v| v.as_i64()).unwrap_or(0);
            let line_end = func
                .get("line_end")
                .and_then(|v| v.as_i64())
                .unwrap_or(line_start);
            let line_start = line_start as u32;
            let line_end = line_end as u32;

            if line_start == 0 {
                continue;
            }

            // Get blame info for this function
            match self
                .blame
                .get_entity_blame(&file_path, line_start, line_end)
            {
                Ok(blame_info) => {
                    if let (Some(last_modified), Some(author)) =
                        (&blame_info.last_modified, &blame_info.last_author)
                    {
                        // Update function with git data
                        self.update_function_git_data(
                            &qn,
                            last_modified,
                            author,
                            blame_info.commit_count,
                        )?;
                        stats.functions_enriched += 1;

                        // Create commit nodes and edges
                        for entry in &blame_info.blame_entries {
                            if self.create_commit_if_needed(
                                &entry.commit_hash,
                                &entry.full_hash,
                                &entry.author,
                                &entry.timestamp,
                            )? {
                                stats.commits_created += 1;
                            }
                            if self.create_modified_in_func(
                                &qn,
                                &entry.commit_hash,
                                line_start,
                                line_end,
                            )? {
                                stats.edges_created += 1;
                            }
                        }
                    }
                }
                Err(e) => {
                    debug!(
                        "Failed to get blame for {}:{}: {}",
                        file_path, line_start, e
                    );
                    stats.files_skipped += 1;
                }
            }
        }

        Ok(stats)
    }

    /// Enrich Class nodes with git data.
    fn enrich_classes(&mut self) -> Result<EnrichmentStats> {
        let mut stats = EnrichmentStats::default();

        // Query classes that need enrichment
        let query = r#"
            MATCH (c:Class)
            WHERE c.last_modified IS NULL AND c.filePath IS NOT NULL
            RETURN c.qualifiedName AS qn, c.filePath AS file_path,
                   c.lineStart AS line_start, c.lineEnd AS line_end
        "#;

        let classes = self.graph.execute_safe(query);
        let total = classes.len();
        debug!("Found {} classes to enrich", total);

        for (i, cls) in classes.into_iter().enumerate() {
            if i > 0 && i % 50 == 0 {
                debug!("Enriched {}/{} classes", i, total);
            }

            let qn = match cls.get("qn").and_then(|v| v.as_str()) {
                Some(s) => s.to_string(),
                None => continue,
            };

            let file_path = match cls.get("file_path").and_then(|v| v.as_str()) {
                Some(s) => s.to_string(),
                None => continue,
            };

            let line_start = cls.get("line_start").and_then(|v| v.as_i64()).unwrap_or(0);
            let line_end = cls
                .get("line_end")
                .and_then(|v| v.as_i64())
                .unwrap_or(line_start);
            let line_start = line_start as u32;
            let line_end = line_end as u32;

            if line_start == 0 {
                continue;
            }

            // Get blame info for this class
            match self
                .blame
                .get_entity_blame(&file_path, line_start, line_end)
            {
                Ok(blame_info) => {
                    if let (Some(last_modified), Some(author)) =
                        (&blame_info.last_modified, &blame_info.last_author)
                    {
                        // Update class with git data
                        self.update_class_git_data(
                            &qn,
                            last_modified,
                            author,
                            blame_info.commit_count,
                        )?;
                        stats.classes_enriched += 1;

                        // Create commit nodes and edges
                        for entry in &blame_info.blame_entries {
                            if self.create_commit_if_needed(
                                &entry.commit_hash,
                                &entry.full_hash,
                                &entry.author,
                                &entry.timestamp,
                            )? {
                                stats.commits_created += 1;
                            }
                            if self.create_modified_in_class(
                                &qn,
                                &entry.commit_hash,
                                line_start,
                                line_end,
                            )? {
                                stats.edges_created += 1;
                            }
                        }
                    }
                }
                Err(e) => {
                    debug!(
                        "Failed to get blame for {}:{}: {}",
                        file_path, line_start, e
                    );
                    stats.files_skipped += 1;
                }
            }
        }

        Ok(stats)
    }

    /// Update a Function node with git metadata.
    fn update_function_git_data(
        &self,
        qualified_name: &str,
        last_modified: &str,
        author: &str,
        commit_count: usize,
    ) -> Result<()> {
        let query = r#"
            MATCH (f:Function {qualifiedName: $qn})
            SET f.last_modified = $last_modified,
                f.author = $author,
                f.commit_count = $commit_count
        "#;

        self.graph.execute_with_params(
            query,
            vec![
                ("qn", qualified_name.into()),
                ("last_modified", last_modified.into()),
                ("author", author.into()),
                ("commit_count", (commit_count as i64).into()),
            ],
        )?;

        Ok(())
    }

    /// Update a Class node with git metadata.
    fn update_class_git_data(
        &self,
        qualified_name: &str,
        last_modified: &str,
        author: &str,
        commit_count: usize,
    ) -> Result<()> {
        let query = r#"
            MATCH (c:Class {qualifiedName: $qn})
            SET c.last_modified = $last_modified,
                c.author = $author,
                c.commit_count = $commit_count
        "#;

        self.graph.execute_with_params(
            query,
            vec![
                ("qn", qualified_name.into()),
                ("last_modified", last_modified.into()),
                ("author", author.into()),
                ("commit_count", (commit_count as i64).into()),
            ],
        )?;

        Ok(())
    }

    /// Create a Commit node if it doesn't already exist.
    fn create_commit_if_needed(
        &mut self,
        short_hash: &str,
        full_hash: &str,
        author: &str,
        timestamp: &str,
    ) -> Result<bool> {
        if self.seen_commits.contains(short_hash) {
            return Ok(false);
        }

        let query = r#"
            MERGE (c:Commit {hash: $hash})
            ON CREATE SET c.author = $author,
                          c.timestamp = $timestamp,
                          c.repoId = $repo_id
        "#;

        let repo_id = self.repo_id.clone().unwrap_or_default();

        self.graph.execute_with_params(
            query,
            vec![
                ("hash", short_hash.into()),
                ("author", author.into()),
                ("timestamp", timestamp.into()),
                ("repo_id", repo_id.into()),
            ],
        )?;

        self.seen_commits.insert(short_hash.to_string());
        Ok(true)
    }

    /// Create a MODIFIED_IN_FUNC edge from Function to Commit.
    fn create_modified_in_func(
        &self,
        func_qn: &str,
        commit_hash: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<bool> {
        let query = r#"
            MATCH (f:Function {qualifiedName: $func_qn})
            MATCH (c:Commit {hash: $commit_hash})
            MERGE (f)-[r:MODIFIED_IN_FUNC]->(c)
            ON CREATE SET r.line_start = $line_start, r.line_end = $line_end
        "#;

        self.graph.execute_with_params(
            query,
            vec![
                ("func_qn", func_qn.into()),
                ("commit_hash", commit_hash.into()),
                ("line_start", (line_start as i64).into()),
                ("line_end", (line_end as i64).into()),
            ],
        )?;

        Ok(true)
    }

    /// Create a MODIFIED_IN_CLASS edge from Class to Commit.
    fn create_modified_in_class(
        &self,
        class_qn: &str,
        commit_hash: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<bool> {
        let query = r#"
            MATCH (c:Class {qualifiedName: $class_qn})
            MATCH (cm:Commit {hash: $commit_hash})
            MERGE (c)-[r:MODIFIED_IN_CLASS]->(cm)
            ON CREATE SET r.line_start = $line_start, r.line_end = $line_end
        "#;

        self.graph.execute_with_params(
            query,
            vec![
                ("class_qn", class_qn.into()),
                ("commit_hash", commit_hash.into()),
                ("line_start", (line_start as i64).into()),
                ("line_end", (line_end as i64).into()),
            ],
        )?;

        Ok(true)
    }
}

/// Enrich a graph with git history (standalone function).
///
/// Convenience function that creates a GitEnricher and runs enrichment.
pub fn enrich_graph_with_git(
    repo_path: &Path,
    graph: &GraphClient,
    repo_id: Option<&str>,
) -> Result<EnrichmentStats> {
    // Check if this is a git repo
    if !GitHistory::is_git_repo(repo_path) {
        info!("Not a git repository, skipping git enrichment");
        return Ok(EnrichmentStats::default());
    }

    let history = GitHistory::open(repo_path)?;
    let mut enricher = GitEnricher::new(&history, graph)?;

    if let Some(id) = repo_id {
        enricher = enricher.with_repo_id(id);
    }

    enricher.enrich_all()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn create_test_repo_and_graph() -> Result<(tempfile::TempDir, GraphClient, git2::Repository)> {
        // Create temp dir
        let dir = tempdir()?;

        // Initialize git repo
        let repo = git2::Repository::init(dir.path())?;
        let mut config = repo.config()?;
        config.set_str("user.name", "Test User")?;
        config.set_str("user.email", "test@example.com")?;

        // Create a Python file
        let py_content = r#"class TestClass:
    def test_method(self):
        pass

def test_function():
    return 42
"#;
        fs::write(dir.path().join("test.py"), py_content)?;

        // Commit
        let sig = repo.signature()?;
        let tree_id = {
            let mut index = repo.index()?;
            index.add_path(Path::new("test.py"))?;
            index.write()?;
            index.write_tree()?
        };
        let tree = repo.find_tree(tree_id)?;
        repo.commit(Some("HEAD"), &sig, &sig, "Add test file", &tree, &[])?;

        // Create graph
        let graph_path = dir.path().join(".repotoire/graph");
        let graph = GraphClient::new(&graph_path)?;

        // Insert test nodes
        graph.insert_file("test.py", "python", 7)?;
        graph.insert_function(
            "test.py::test_function",
            "test_function",
            "test.py",
            5,
            6,
            false,
        )?;
        graph.insert_class("test.py::TestClass", "TestClass", "test.py", 1, 3)?;

        Ok((dir, graph, repo))
    }

    #[test]
    fn test_enrich_functions() -> Result<()> {
        let (dir, graph, _repo) = create_test_repo_and_graph()?;

        let history = GitHistory::open(dir.path())?;
        let mut enricher = GitEnricher::new(&history, &graph)?;

        let stats = enricher.enrich_functions()?;
        assert!(stats.functions_enriched >= 1);
        Ok(())
    }

    #[test]
    fn test_enrich_classes() -> Result<()> {
        let (dir, graph, _repo) = create_test_repo_and_graph()?;

        let history = GitHistory::open(dir.path())?;
        let mut enricher = GitEnricher::new(&history, &graph)?;

        let stats = enricher.enrich_classes()?;
        assert!(stats.classes_enriched >= 1);
        Ok(())
    }

    #[test]
    fn test_enrich_all() -> Result<()> {
        let (dir, graph, _repo) = create_test_repo_and_graph()?;

        let stats = enrich_graph_with_git(dir.path(), &graph, None)?;
        assert!(stats.functions_enriched >= 1);
        assert!(stats.classes_enriched >= 1);
        Ok(())
    }

    #[test]
    fn test_non_git_repo() -> Result<()> {
        let dir = tempdir()?;
        let graph_path = dir.path().join("graph");
        let graph = GraphClient::new(&graph_path)?;

        let stats = enrich_graph_with_git(dir.path(), &graph, None)?;
        assert_eq!(stats.functions_enriched, 0);
        assert_eq!(stats.classes_enriched, 0);
        Ok(())
    }
}
