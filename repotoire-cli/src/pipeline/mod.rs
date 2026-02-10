//! Code ingestion pipeline
//!
//! Orchestrates the full analysis pipeline:
//! 1. Walk source files
//! 2. Parse and extract entities (functions, classes)
//! 3. Build the code graph
//! 4. Enrich with git history (if available)
//! 5. Run detectors

use anyhow::Result;
use std::path::Path;
use tracing::{debug, info};

use crate::git::{self, EnrichmentStats, GitHistory};
use crate::graph::GraphStore;

/// Full analysis pipeline.
pub struct Pipeline {
    graph: GraphStore,
    /// Whether to enrich with git history
    enable_git_enrichment: bool,
    /// Repository ID for multi-tenant isolation
    repo_id: Option<String>,
}

impl Pipeline {
    /// Create a new pipeline with a graph client.
    pub fn new(graph: GraphStore) -> Self {
        Self {
            graph,
            enable_git_enrichment: true,
            repo_id: None,
        }
    }

    /// Disable git enrichment.
    pub fn without_git(mut self) -> Self {
        self.enable_git_enrichment = false;
        self
    }

    /// Set repository ID for multi-tenant isolation.
    pub fn with_repo_id(mut self, repo_id: impl Into<String>) -> Self {
        self.repo_id = Some(repo_id.into());
        self
    }

    /// Run the full ingestion pipeline.
    ///
    /// # Arguments
    /// * `repo_path` - Path to the repository to analyze
    pub fn ingest(&self, repo_path: &Path) -> Result<IngestStats> {
        let mut stats = IngestStats::default();

        // TODO: Walk files, parse, insert into graph
        // (existing ingestion logic would go here)

        // Enrich with git history if enabled
        if self.enable_git_enrichment {
            match self.enrich_with_git(repo_path) {
                Ok(git_stats) => {
                    stats.git_enrichment = Some(git_stats);
                }
                Err(e) => {
                    debug!("Git enrichment skipped or failed: {}", e);
                }
            }
        }

        Ok(stats)
    }

    /// Enrich the graph with git history data.
    fn enrich_with_git(&self, repo_path: &Path) -> Result<EnrichmentStats> {
        git::enrichment::enrich_graph_with_git(
            repo_path,
            &self.graph,
            self.repo_id.as_deref(),
        )
    }

    /// Run git enrichment on an already-populated graph.
    ///
    /// Use this when you've already ingested code and want to add git data.
    pub fn enrich_git_only(&self, repo_path: &Path) -> Result<EnrichmentStats> {
        self.enrich_with_git(repo_path)
    }

    /// Get a reference to the graph client.
    pub fn graph(&self) -> &GraphStore {
        &self.graph
    }
}

/// Statistics from the ingestion pipeline.
#[derive(Default, Debug)]
pub struct IngestStats {
    /// Number of files processed
    pub files: usize,
    /// Number of functions found
    pub functions: usize,
    /// Number of classes found
    pub classes: usize,
    /// Number of edges created
    pub edges: usize,
    /// Git enrichment statistics (if enabled)
    pub git_enrichment: Option<EnrichmentStats>,
}

impl IngestStats {
    /// Check if git enrichment was performed.
    pub fn has_git_enrichment(&self) -> bool {
        self.git_enrichment.is_some()
    }

    /// Get a summary string.
    pub fn summary(&self) -> String {
        let mut parts = vec![
            format!("{} files", self.files),
            format!("{} functions", self.functions),
            format!("{} classes", self.classes),
            format!("{} edges", self.edges),
        ];

        if let Some(ref git) = self.git_enrichment {
            parts.push(format!(
                "git: {} funcs, {} classes, {} commits",
                git.functions_enriched, git.classes_enriched, git.commits_created
            ));
        }

        parts.join(", ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_pipeline_creation() -> Result<()> {
        let dir = tempdir()?;
        let graph_path = dir.path().join("graph");
        let graph = GraphStore::new(&graph_path)?;

        let pipeline = Pipeline::new(graph)
            .without_git()
            .with_repo_id("test-repo");

        assert!(!pipeline.enable_git_enrichment);
        assert_eq!(pipeline.repo_id, Some("test-repo".to_string()));
        Ok(())
    }

    #[test]
    fn test_ingest_stats_summary() {
        let mut stats = IngestStats {
            files: 10,
            functions: 50,
            classes: 5,
            edges: 100,
            git_enrichment: None,
        };

        assert!(!stats.has_git_enrichment());
        assert!(stats.summary().contains("10 files"));

        stats.git_enrichment = Some(EnrichmentStats {
            functions_enriched: 45,
            classes_enriched: 5,
            commits_created: 20,
            edges_created: 50,
            files_skipped: 2,
        });

        assert!(stats.has_git_enrichment());
        assert!(stats.summary().contains("git:"));
    }
}
