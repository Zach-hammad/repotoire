//! Common graph queries for code analysis
//!
//! Pre-defined Cypher queries for common operations like finding functions,
//! analyzing call graphs, and detecting code patterns.

// =============================================================================
// Basic Entity Queries
// =============================================================================

/// Get all functions in the graph
pub const GET_ALL_FUNCTIONS: &str = r#"
    MATCH (f:Function) 
    RETURN f.qualifiedName AS qualifiedName, 
           f.name AS name, 
           f.filePath AS filePath,
           f.lineStart AS lineStart,
           f.lineEnd AS lineEnd,
           f.complexity AS complexity,
           f.loc AS loc
"#;

/// Get all classes in the graph
pub const GET_ALL_CLASSES: &str = r#"
    MATCH (c:Class) 
    RETURN c.qualifiedName AS qualifiedName,
           c.name AS name,
           c.filePath AS filePath,
           c.lineStart AS lineStart,
           c.lineEnd AS lineEnd,
           c.complexity AS complexity
"#;

/// Get all files in the graph
pub const GET_ALL_FILES: &str = r#"
    MATCH (f:File) 
    RETURN f.qualifiedName AS qualifiedName,
           f.filePath AS filePath,
           f.language AS language,
           f.loc AS loc
"#;

/// Get functions by file path
pub const GET_FUNCTIONS_BY_FILE: &str = r#"
    MATCH (f:Function {filePath: $path})
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           f.lineStart AS lineStart,
           f.lineEnd AS lineEnd
"#;

/// Get classes by file path
pub const GET_CLASSES_BY_FILE: &str = r#"
    MATCH (c:Class {filePath: $path})
    RETURN c.qualifiedName AS qualifiedName,
           c.name AS name,
           c.lineStart AS lineStart,
           c.lineEnd AS lineEnd
"#;

// =============================================================================
// Call Graph Queries
// =============================================================================

/// Get all function calls (call graph edges)
pub const GET_CALL_GRAPH: &str = r#"
    MATCH (a:Function)-[r:CALLS]->(b:Function)
    RETURN a.qualifiedName AS caller,
           b.qualifiedName AS callee,
           r.line AS line,
           r.count AS count
"#;

/// Get callers of a specific function
pub const GET_CALLERS: &str = r#"
    MATCH (caller:Function)-[r:CALLS]->(f:Function {qualifiedName: $name})
    RETURN caller.qualifiedName AS caller,
           caller.name AS callerName,
           r.line AS line
"#;

/// Get callees of a specific function
pub const GET_CALLEES: &str = r#"
    MATCH (f:Function {qualifiedName: $name})-[r:CALLS]->(callee:Function)
    RETURN callee.qualifiedName AS callee,
           callee.name AS calleeName,
           r.line AS line
"#;

/// Get functions with high fan-out (many callees)
pub const GET_HIGH_FAN_OUT: &str = r#"
    MATCH (f:Function)-[r:CALLS]->(callee:Function)
    WITH f, count(callee) AS fanOut
    WHERE fanOut > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           fanOut
    ORDER BY fanOut DESC
"#;

/// Get functions with high fan-in (many callers)
pub const GET_HIGH_FAN_IN: &str = r#"
    MATCH (caller:Function)-[r:CALLS]->(f:Function)
    WITH f, count(caller) AS fanIn
    WHERE fanIn > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           fanIn
    ORDER BY fanIn DESC
"#;

// =============================================================================
// Inheritance Queries
// =============================================================================

/// Get class inheritance hierarchy
pub const GET_INHERITANCE: &str = r#"
    MATCH (child:Class)-[:INHERITS]->(parent:Class)
    RETURN child.qualifiedName AS child,
           child.name AS childName,
           parent.qualifiedName AS parent,
           parent.name AS parentName
"#;

/// Get subclasses of a specific class
pub const GET_SUBCLASSES: &str = r#"
    MATCH (child:Class)-[:INHERITS]->(parent:Class {qualifiedName: $name})
    RETURN child.qualifiedName AS child,
           child.name AS childName
"#;

/// Get parent classes of a specific class
pub const GET_PARENT_CLASSES: &str = r#"
    MATCH (child:Class {qualifiedName: $name})-[:INHERITS]->(parent:Class)
    RETURN parent.qualifiedName AS parent,
           parent.name AS parentName
"#;

// =============================================================================
// Complexity Queries
// =============================================================================

/// Get functions with high complexity
pub const GET_COMPLEX_FUNCTIONS: &str = r#"
    MATCH (f:Function)
    WHERE f.complexity > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           f.filePath AS filePath,
           f.complexity AS complexity,
           f.loc AS loc
    ORDER BY f.complexity DESC
"#;

/// Get large files by lines of code
pub const GET_LARGE_FILES: &str = r#"
    MATCH (f:File)
    WHERE f.loc > $threshold
    RETURN f.filePath AS filePath,
           f.loc AS loc,
           f.language AS language
    ORDER BY f.loc DESC
"#;

/// Get methods in a class
pub const GET_CLASS_METHODS: &str = r#"
    MATCH (c:Class {qualifiedName: $name})-[:CONTAINS_METHOD]->(m:Function)
    RETURN m.qualifiedName AS qualifiedName,
           m.name AS name,
           m.complexity AS complexity
"#;

// =============================================================================
// Import/Dependency Queries
// =============================================================================

/// Get all imports for a file
pub const GET_FILE_IMPORTS: &str = r#"
    MATCH (f:File {filePath: $path})-[:IMPORTS]->(m:Module)
    RETURN m.qualifiedName AS module,
           m.name AS name,
           m.is_external AS isExternal
"#;

/// Get files that import a specific module
pub const GET_MODULE_IMPORTERS: &str = r#"
    MATCH (f:File)-[:IMPORTS]->(m:Module {name: $name})
    RETURN f.filePath AS filePath
"#;

/// Get external dependencies
pub const GET_EXTERNAL_DEPS: &str = r#"
    MATCH (f:File)-[:IMPORTS]->(m:Module)
    WHERE m.is_external = true
    RETURN DISTINCT m.name AS module, count(f) AS importCount
    ORDER BY importCount DESC
"#;

// =============================================================================
// Code Smell Queries
// =============================================================================

/// Get long methods (God Method smell)
pub const GET_LONG_METHODS: &str = r#"
    MATCH (f:Function)
    WHERE f.loc > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           f.filePath AS filePath,
           f.loc AS loc
    ORDER BY f.loc DESC
"#;

/// Get deeply nested functions
pub const GET_DEEPLY_NESTED: &str = r#"
    MATCH (f:Function)
    WHERE f.max_chain_depth > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           f.max_chain_depth AS depth,
           f.chain_example AS example
    ORDER BY f.max_chain_depth DESC
"#;

/// Get functions with many parameters
pub const GET_MANY_PARAMS: &str = r#"
    MATCH (f:Function)
    WHERE size(f.parameters) > $threshold
    RETURN f.qualifiedName AS qualifiedName,
           f.name AS name,
           f.parameters AS parameters,
           size(f.parameters) AS paramCount
    ORDER BY paramCount DESC
"#;

// =============================================================================
// Architecture Queries
// =============================================================================

/// Get files in a component
pub const GET_COMPONENT_FILES: &str = r#"
    MATCH (f:File)-[:BELONGS_TO_COMPONENT]->(c:Component {name: $name})
    RETURN f.filePath AS filePath,
           f.loc AS loc
"#;

/// Get component dependencies (via function calls)
pub const GET_COMPONENT_DEPS: &str = r#"
    MATCH (f1:File)-[:BELONGS_TO_COMPONENT]->(c1:Component)
    MATCH (f2:File)-[:BELONGS_TO_COMPONENT]->(c2:Component)
    MATCH (fn1:Function {filePath: f1.filePath})-[:CALLS]->(fn2:Function {filePath: f2.filePath})
    WHERE c1 <> c2
    RETURN c1.name AS source,
           c2.name AS target,
           count(*) AS callCount
"#;

// =============================================================================
// Statistics Queries
// =============================================================================

/// Get overall graph statistics
pub const GET_STATS: &str = r#"
    MATCH (f:Function) WITH count(f) AS functions
    MATCH (c:Class) WITH functions, count(c) AS classes
    MATCH (fi:File) WITH functions, classes, count(fi) AS files
    RETURN functions, classes, files
"#;

/// Get language distribution
pub const GET_LANGUAGE_DIST: &str = r#"
    MATCH (f:File)
    RETURN f.language AS language,
           count(f) AS fileCount,
           sum(f.loc) AS totalLoc
    ORDER BY fileCount DESC
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_queries_compile() {
        // Just verify that queries are valid strings
        assert!(!GET_ALL_FUNCTIONS.is_empty());
        assert!(!GET_CALL_GRAPH.is_empty());
        assert!(!GET_INHERITANCE.is_empty());
    }
}
