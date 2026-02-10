//! Git history analysis module
//!
//! Provides functionality for extracting git history, calculating churn,
//! and enriching the code graph with temporal data.
//!
//! # Features
//!
//! - Extract commit history for files and functions
//! - Calculate code churn (lines added/removed)
//! - Track authorship and ownership
//! - Add temporal edges (MODIFIED_IN) to the graph
//!
//! # Example
//!
//! ```no_run
//! use repotoire::git::{GitHistory, GitEnricher};
//! use repotoire::graph::GraphStore;
//! use std::path::Path;
//!
//! let history = GitHistory::open(Path::new("/path/to/repo")).unwrap();
//! let commits = history.get_file_commits("src/main.rs", 50).unwrap();
//!
//! // Enrich graph with git data
//! let graph = GraphStore::new(Path::new(".repotoire/graph")).unwrap();
//! let enricher = GitEnricher::new(&history, &graph);
//! let stats = enricher.enrich_all().unwrap();
//! ```

pub mod blame;
pub mod enrichment;
pub mod history;

pub use blame::{BlameInfo, LineBlame};
pub use enrichment::{EnrichmentStats, GitEnricher};
pub use history::{CommitInfo, FileChurn, GitHistory};
