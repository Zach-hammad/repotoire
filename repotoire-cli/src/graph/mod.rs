//! Graph database client for code analysis (Kuzu)
//!
//! This module provides an embedded graph database client for storing and querying
//! code structure (functions, classes, files) and relationships (calls, imports, inheritance).
//!
//! # Example
//!
//! ```no_run
//! use repotoire::graph::GraphClient;
//! use std::path::Path;
//!
//! let client = GraphClient::new(Path::new(".repotoire/graph")).unwrap();
//!
//! // Create a file node
//! client.execute("CREATE (:File {qualifiedName: 'main.py', filePath: 'main.py', language: 'python', loc: 100})").unwrap();
//!
//! // Query functions
//! let results = client.execute("MATCH (f:Function) RETURN f.name").unwrap();
//! ```

mod client;
pub mod queries;
pub mod schema;

pub use client::{GraphClient, QueryResult};
