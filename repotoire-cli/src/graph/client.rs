//! Kuzu graph database client
//!
//! Provides a lightweight, embedded graph database for local-first analysis.
//! No Docker or external server required.

use anyhow::{Context, Result};
use kuzu::{Connection, Database, SystemConfig, Value};
use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;
use tracing::{debug, warn};

use super::schema;

/// Query result as a list of rows, where each row is a map of column names to values.
pub type QueryResult = Vec<HashMap<String, serde_json::Value>>;

/// Graph database client using Kuzu.
///
/// Kuzu is an embedded graph database that supports Cypher queries.
/// It runs in-process with no external dependencies.
pub struct GraphClient {
    #[allow(dead_code)]
    db: Database,
    conn: Mutex<Connection<'static>>,
}

// Safety: Kuzu handles thread safety internally
unsafe impl Send for GraphClient {}
unsafe impl Sync for GraphClient {}

impl GraphClient {
    /// Create a new graph client, opening or creating the database at the given path.
    ///
    /// # Arguments
    /// * `db_path` - Path to the database directory (created if doesn't exist)
    pub fn new(db_path: &Path) -> Result<Self> {
        // Ensure parent directory exists
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("Failed to create parent directory: {:?}", parent))?;
        }

        let config = SystemConfig::default();
        let db = Database::new(db_path, config)
            .with_context(|| format!("Failed to open Kuzu database at {:?}", db_path))?;

        // Create connection - we need to leak the database reference to get 'static lifetime
        // This is safe because we own the Database and will only drop it when GraphClient is dropped
        let db_ref: &'static Database = unsafe { &*(&db as *const Database) };
        let conn = Connection::new(db_ref).context("Failed to create Kuzu connection")?;

        let client = Self {
            db,
            conn: Mutex::new(conn),
        };

        // Initialize schema
        client.init_schema()?;

        debug!("Kuzu database opened at {:?}", db_path);
        Ok(client)
    }

    /// Initialize the database schema (node and relationship tables).
    fn init_schema(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        // Execute each schema statement separately
        for statement in schema::get_schema_statements() {
            match conn.query(statement) {
                Ok(_) => {
                    debug!(
                        "Executed schema: {}...",
                        &statement[..statement.len().min(50)]
                    )
                }
                Err(e) => {
                    let err_str = e.to_string();
                    // Ignore "already exists" errors
                    if !err_str.contains("already exists") {
                        warn!("Schema statement failed: {} - {}", statement, err_str);
                    }
                }
            }
        }

        Ok(())
    }

    /// Execute a Cypher query and return results as JSON.
    ///
    /// # Arguments
    /// * `query` - Cypher query string
    pub fn execute(&self, query: &str) -> Result<QueryResult> {
        self.execute_with_params(query, vec![])
    }

    /// Execute a Cypher query with parameters and return results as JSON.
    ///
    /// # Arguments
    /// * `query` - Cypher query string (use $param for parameters)
    /// * `params` - Parameter name-value pairs
    pub fn execute_with_params(
        &self,
        query: &str,
        params: Vec<(&str, Value)>,
    ) -> Result<QueryResult> {
        let conn = self.conn.lock().unwrap();

        let result = if params.is_empty() {
            conn.query(query)
                .with_context(|| format!("Query failed: {}", query))?
        } else {
            let mut stmt = conn
                .prepare(query)
                .with_context(|| format!("Failed to prepare query: {}", query))?;
            conn.execute(&mut stmt, params)
                .with_context(|| format!("Query execution failed: {}", query))?
        };

        let column_names = result.get_column_names();
        let mut rows = Vec::new();

        for row in result {
            let mut record = HashMap::new();
            for (i, value) in row.iter().enumerate() {
                if let Some(col_name) = column_names.get(i) {
                    record.insert(col_name.clone(), value_to_json(value));
                }
            }
            rows.push(record);
        }

        Ok(rows)
    }

    /// Execute a query, returning an empty result on error (for optional queries).
    pub fn execute_safe(&self, query: &str) -> QueryResult {
        self.execute(query).unwrap_or_default()
    }

    /// Clear all data from the graph.
    pub fn clear(&self) -> Result<()> {
        let tables = [
            "Function",
            "Class",
            "File",
            "Module",
            "Variable",
            "Commit",
            "ExternalClass",
            "ExternalFunction",
            "BuiltinFunction",
            "Type",
            "Component",
            "Domain",
            "DetectorMetadata",
            "Concept",
        ];

        for table in tables {
            let query = format!("MATCH (n:{}) DELETE n", table);
            if let Err(e) = self.execute(&query) {
                debug!("Failed to clear table {} (may not exist): {}", table, e);
            }
        }

        Ok(())
    }

    /// Get graph statistics.
    pub fn get_stats(&self) -> Result<HashMap<String, i64>> {
        let mut stats = HashMap::new();

        let tables = [
            ("Function", "total_functions"),
            ("Class", "total_classes"),
            ("File", "total_files"),
            ("Module", "total_modules"),
            ("Variable", "total_variables"),
            ("Type", "total_types"),
            ("Component", "total_components"),
            ("Domain", "total_domains"),
        ];

        for (table, key) in tables {
            let query = format!("MATCH (n:{}) RETURN count(*) AS cnt", table);
            match self.execute(&query) {
                Ok(results) => {
                    if let Some(row) = results.first() {
                        if let Some(serde_json::Value::Number(n)) = row.get("cnt") {
                            stats.insert(key.to_string(), n.as_i64().unwrap_or(0));
                        }
                    }
                }
                Err(_) => {
                    stats.insert(key.to_string(), 0);
                }
            }
        }

        Ok(stats)
    }

    /// Get all file paths in the graph.
    pub fn get_all_file_paths(&self) -> Result<Vec<String>> {
        let query = "MATCH (f:File) RETURN f.filePath AS path";
        let results = self.execute(query)?;

        Ok(results
            .into_iter()
            .filter_map(|row| {
                row.get("path")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .collect())
    }

    /// Delete all entities associated with a file.
    pub fn delete_file_entities(&self, file_path: &str) -> Result<i64> {
        let mut deleted = 0i64;

        // Delete functions in file
        let query = "MATCH (n:Function {filePath: $path}) DELETE n RETURN count(*) AS cnt";
        if let Ok(results) = self.execute_with_params(query, vec![("path", file_path.into())]) {
            if let Some(row) = results.first() {
                if let Some(serde_json::Value::Number(n)) = row.get("cnt") {
                    deleted += n.as_i64().unwrap_or(0);
                }
            }
        }

        // Delete classes in file
        let query = "MATCH (n:Class {filePath: $path}) DELETE n RETURN count(*) AS cnt";
        if let Ok(results) = self.execute_with_params(query, vec![("path", file_path.into())]) {
            if let Some(row) = results.first() {
                if let Some(serde_json::Value::Number(n)) = row.get("cnt") {
                    deleted += n.as_i64().unwrap_or(0);
                }
            }
        }

        // Delete file itself
        let query = "MATCH (f:File {filePath: $path}) DELETE f RETURN count(*) AS cnt";
        if let Ok(results) = self.execute_with_params(query, vec![("path", file_path.into())]) {
            if let Some(row) = results.first() {
                if let Some(serde_json::Value::Number(n)) = row.get("cnt") {
                    deleted += n.as_i64().unwrap_or(0);
                }
            }
        }

        Ok(deleted)
    }

    /// Delete all entities for a repository.
    pub fn delete_repository(&self, repo_id: &str) -> Result<i64> {
        let tables = [
            "Function",
            "Class",
            "File",
            "Module",
            "Variable",
            "Type",
            "Component",
            "Domain",
            "DetectorMetadata",
        ];
        let mut total_deleted = 0i64;

        for table in tables {
            let query = format!(
                "MATCH (n:{}) WHERE n.repoId = $repo_id DELETE n RETURN count(*) AS deleted",
                table
            );
            if let Ok(results) = self.execute_with_params(&query, vec![("repo_id", repo_id.into())])
            {
                if let Some(row) = results.first() {
                    if let Some(serde_json::Value::Number(n)) = row.get("deleted") {
                        total_deleted += n.as_i64().unwrap_or(0);
                    }
                }
            }
        }

        Ok(total_deleted)
    }

    /// Close the database connection.
    pub fn close(self) {
        debug!("Closing Kuzu database connection");
        // Connection and Database will be dropped automatically
        drop(self);
    }

    // =========================================================================
    // Insert methods for building the code graph
    // =========================================================================

    /// Insert a file node into the graph.
    pub fn insert_file(&self, file_path: &str, language: &str, lines: usize) -> Result<()> {
        let query = r#"
            MERGE (f:File {filePath: $path})
            SET f.language = $lang, f.linesOfCode = $lines
        "#;
        self.execute_with_params(query, vec![
            ("path", file_path.into()),
            ("lang", language.into()),
            ("lines", (lines as i64).into()),
        ])?;
        Ok(())
    }

    /// Insert a function node into the graph.
    pub fn insert_function(
        &self,
        qualified_name: &str,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        is_async: bool,
    ) -> Result<()> {
        let query = r#"
            MERGE (func:Function {qualifiedName: $qname})
            SET func.name = $name,
                func.filePath = $path,
                func.lineStart = $line_start,
                func.lineEnd = $line_end,
                func.isAsync = $is_async
        "#;
        self.execute_with_params(query, vec![
            ("qname", qualified_name.into()),
            ("name", name.into()),
            ("path", file_path.into()),
            ("line_start", (line_start as i64).into()),
            ("line_end", (line_end as i64).into()),
            ("is_async", kuzu::Value::Bool(is_async)),
        ])?;

        // Create CONTAINS edge from File to Function
        let edge_query = r#"
            MATCH (f:File {filePath: $path})
            MATCH (func:Function {qualifiedName: $qname})
            MERGE (f)-[:CONTAINS]->(func)
        "#;
        self.execute_with_params(edge_query, vec![
            ("path", file_path.into()),
            ("qname", qualified_name.into()),
        ])?;

        Ok(())
    }

    /// Insert a class node into the graph.
    pub fn insert_class(
        &self,
        qualified_name: &str,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<()> {
        let query = r#"
            MERGE (c:Class {qualifiedName: $qname})
            SET c.name = $name,
                c.filePath = $path,
                c.lineStart = $line_start,
                c.lineEnd = $line_end
        "#;
        self.execute_with_params(query, vec![
            ("qname", qualified_name.into()),
            ("name", name.into()),
            ("path", file_path.into()),
            ("line_start", (line_start as i64).into()),
            ("line_end", (line_end as i64).into()),
        ])?;

        // Create CONTAINS edge from File to Class
        let edge_query = r#"
            MATCH (f:File {filePath: $path})
            MATCH (c:Class {qualifiedName: $qname})
            MERGE (f)-[:CONTAINS]->(c)
        "#;
        self.execute_with_params(edge_query, vec![
            ("path", file_path.into()),
            ("qname", qualified_name.into()),
        ])?;

        Ok(())
    }

    /// Insert a CALLS edge between two functions.
    pub fn insert_call(&self, caller: &str, callee: &str) -> Result<()> {
        let query = r#"
            MATCH (a:Function {qualifiedName: $caller})
            MATCH (b:Function {qualifiedName: $callee})
            MERGE (a)-[:CALLS]->(b)
        "#;
        self.execute_with_params(query, vec![
            ("caller", caller.into()),
            ("callee", callee.into()),
        ])?;
        Ok(())
    }

    /// Insert an IMPORTS edge between two files.
    pub fn insert_import(&self, importer: &str, imported: &str) -> Result<()> {
        let query = r#"
            MATCH (a:File {filePath: $importer})
            MATCH (b:File {filePath: $imported})
            MERGE (a)-[:IMPORTS]->(b)
        "#;
        self.execute_with_params(query, vec![
            ("importer", importer.into()),
            ("imported", imported.into()),
        ])?;
        Ok(())
    }

    /// Insert an INHERITS edge between two classes.
    pub fn insert_inheritance(&self, child: &str, parent: &str) -> Result<()> {
        let query = r#"
            MATCH (c:Class {qualifiedName: $child})
            MATCH (p:Class {qualifiedName: $parent})
            MERGE (c)-[:INHERITS]->(p)
        "#;
        self.execute_with_params(query, vec![
            ("child", child.into()),
            ("parent", parent.into()),
        ])?;
        Ok(())
    }
}

/// Convert a Kuzu Value to serde_json::Value.
fn value_to_json(value: &Value) -> serde_json::Value {
    match value {
        Value::Null(_) => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Int8(n) => serde_json::json!(*n),
        Value::Int16(n) => serde_json::json!(*n),
        Value::Int32(n) => serde_json::json!(*n),
        Value::Int64(n) => serde_json::json!(*n),
        Value::UInt8(n) => serde_json::json!(*n),
        Value::UInt16(n) => serde_json::json!(*n),
        Value::UInt32(n) => serde_json::json!(*n),
        Value::UInt64(n) => serde_json::json!(*n),
        Value::Int128(n) => serde_json::Value::String(n.to_string()),
        Value::Float(f) => serde_json::Number::from_f64(*f as f64)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Value::Double(f) => serde_json::Number::from_f64(*f)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Value::String(s) => serde_json::Value::String(s.clone()),
        Value::Blob(b) => serde_json::Value::String(format!("{:?}", b)),
        Value::Date(d) => serde_json::Value::String(d.to_string()),
        Value::Timestamp(t)
        | Value::TimestampTz(t)
        | Value::TimestampNs(t)
        | Value::TimestampMs(t)
        | Value::TimestampSec(t) => serde_json::Value::String(t.to_string()),
        Value::Interval(i) => serde_json::Value::String(format!("{:?}", i)),
        Value::InternalID(id) => serde_json::json!({
            "offset": id.offset,
            "table_id": id.table_id
        }),
        Value::List(_, items) | Value::Array(_, items) => {
            serde_json::Value::Array(items.iter().map(value_to_json).collect())
        }
        Value::Struct(fields) => {
            let map: serde_json::Map<String, serde_json::Value> = fields
                .iter()
                .map(|(k, v)| (k.clone(), value_to_json(v)))
                .collect();
            serde_json::Value::Object(map)
        }
        Value::Node(node) => {
            let mut map = serde_json::Map::new();
            map.insert(
                "_id".to_string(),
                serde_json::json!({
                    "offset": node.get_node_id().offset,
                    "table_id": node.get_node_id().table_id
                }),
            );
            map.insert(
                "_label".to_string(),
                serde_json::Value::String(node.get_label_name().clone()),
            );
            for (k, v) in node.get_properties() {
                map.insert(k.clone(), value_to_json(v));
            }
            serde_json::Value::Object(map)
        }
        Value::Rel(rel) => {
            let mut map = serde_json::Map::new();
            map.insert(
                "_src".to_string(),
                serde_json::json!({
                    "offset": rel.get_src_node().offset,
                    "table_id": rel.get_src_node().table_id
                }),
            );
            map.insert(
                "_dst".to_string(),
                serde_json::json!({
                    "offset": rel.get_dst_node().offset,
                    "table_id": rel.get_dst_node().table_id
                }),
            );
            map.insert(
                "_label".to_string(),
                serde_json::Value::String(rel.get_label_name().clone()),
            );
            for (k, v) in rel.get_properties() {
                map.insert(k.clone(), value_to_json(v));
            }
            serde_json::Value::Object(map)
        }
        Value::RecursiveRel { nodes, rels } => {
            serde_json::json!({
                "nodes": nodes.iter().map(|n| {
                    let mut map = serde_json::Map::new();
                    map.insert("_label".to_string(), serde_json::Value::String(n.get_label_name().clone()));
                    for (k, v) in n.get_properties() {
                        map.insert(k.clone(), value_to_json(v));
                    }
                    serde_json::Value::Object(map)
                }).collect::<Vec<_>>(),
                "rels": rels.iter().map(|r| {
                    let mut map = serde_json::Map::new();
                    map.insert("_label".to_string(), serde_json::Value::String(r.get_label_name().clone()));
                    for (k, v) in r.get_properties() {
                        map.insert(k.clone(), value_to_json(v));
                    }
                    serde_json::Value::Object(map)
                }).collect::<Vec<_>>()
            })
        }
        Value::Map(_, items) => {
            let map: serde_json::Map<String, serde_json::Value> = items
                .iter()
                .filter_map(|(k, v)| {
                    if let Value::String(key) = k {
                        Some((key.clone(), value_to_json(v)))
                    } else {
                        None
                    }
                })
                .collect();
            serde_json::Value::Object(map)
        }
        Value::Union { value, .. } => value_to_json(value),
        Value::UUID(u) => serde_json::Value::String(u.to_string()),
        Value::Decimal(d) => serde_json::Value::String(d.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_create_database() -> Result<()> {
        let dir = tempdir()?;
        let db_path = dir.path().join("test_db");
        let client = GraphClient::new(&db_path)?;

        // Should be able to get stats
        let stats = client.get_stats()?;
        assert_eq!(stats.get("total_functions"), Some(&0));

        Ok(())
    }

    #[test]
    fn test_execute_query() -> Result<()> {
        let dir = tempdir()?;
        let db_path = dir.path().join("test_db");
        let client = GraphClient::new(&db_path)?;

        // Create a file node
        client.execute(
            "CREATE (:File {qualifiedName: 'test.py', filePath: 'test.py', language: 'python', loc: 100})",
        )?;

        // Query it back
        let results = client.execute("MATCH (f:File) RETURN f.filePath AS path")?;
        assert_eq!(results.len(), 1);
        assert_eq!(
            results[0].get("path"),
            Some(&serde_json::Value::String("test.py".to_string()))
        );

        Ok(())
    }

    #[test]
    fn test_parameterized_query() -> Result<()> {
        let dir = tempdir()?;
        let db_path = dir.path().join("test_db");
        let client = GraphClient::new(&db_path)?;

        // Create a file node with params
        client.execute_with_params(
            "CREATE (:File {qualifiedName: $name, filePath: $path, language: $lang, loc: $loc})",
            vec![
                ("name", "main.rs".into()),
                ("path", "src/main.rs".into()),
                ("lang", "rust".into()),
                ("loc", Value::Int64(50)),
            ],
        )?;

        // Query with params
        let results = client.execute_with_params(
            "MATCH (f:File) WHERE f.language = $lang RETURN f.filePath AS path",
            vec![("lang", "rust".into())],
        )?;
        assert_eq!(results.len(), 1);

        Ok(())
    }
}
