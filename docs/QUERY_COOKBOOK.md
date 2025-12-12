# Cypher Query Cookbook

A comprehensive guide to exploring your codebase using Repotoire's Neo4j knowledge graph. These production-ready queries help you understand code structure, detect issues, and analyze architecture.

## Table of Contents

- [Quick Reference](#quick-reference)
- [Running Queries](#running-queries)
- [Basic Exploration](#basic-exploration)
- [Relationship Analysis](#relationship-analysis)
- [Code Smell Detection](#code-smell-detection)
- [Architecture Analysis](#architecture-analysis)
- [Test Coverage](#test-coverage)
- [Security Queries](#security-queries)
- [Advanced Patterns](#advanced-patterns)

## Quick Reference

The 5 most useful queries for quick codebase insights:

### 1. Codebase Summary Stats

```cypher
MATCH (f:File) WITH count(f) AS files, sum(f.loc) AS total_loc
MATCH (c:Class) WITH files, total_loc, count(c) AS classes
MATCH (fn:Function) WITH files, total_loc, classes, count(fn) AS functions
RETURN files, classes, functions, total_loc
```

### 2. Find Most Complex Functions

```cypher
MATCH (f:Function)
WHERE f.complexity > 10
OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
RETURN f.name AS function, f.complexity AS complexity, file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 10
```

### 3. Find Circular Dependencies

```cypher
MATCH path = (f1:File)-[:IMPORTS*2..5]->(f1)
WITH [n IN nodes(path) | n.filePath] AS cycle
RETURN DISTINCT cycle, size(cycle) AS length
ORDER BY length
LIMIT 10
```

### 4. Find God Classes

```cypher
MATCH (c:Class)
OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
WITH c, count(m) AS method_count, sum(m.complexity) AS total_complexity
WHERE method_count > 15 OR total_complexity > 50
RETURN c.name AS class, method_count, total_complexity
ORDER BY method_count DESC
LIMIT 10
```

### 5. Find Unused Code

```cypher
MATCH (f:Function)
WHERE NOT (f)<-[:CALLS]-()
  AND NOT f.name STARTS WITH 'test_'
  AND NOT f.name IN ['main', '__init__']
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function, file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 20
```

---

## Running Queries

### Neo4j Browser

1. Open Neo4j Browser at `http://localhost:7474`
2. Log in with your credentials
3. Paste queries directly into the query bar
4. Press Ctrl+Enter to execute

### Python API

```python
from repotoire.graph.client import Neo4jClient

client = Neo4jClient(
    uri="bolt://localhost:7687",
    password="your-password"
)

results = client.execute_query("""
    MATCH (f:Function)
    WHERE f.complexity > 10
    RETURN f.name, f.complexity
    ORDER BY f.complexity DESC
    LIMIT 10
""")

for record in results:
    print(f"{record['f.name']}: {record['f.complexity']}")
```

### RAG API (Natural Language)

For natural language queries, use the RAG API:

```bash
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the most complex functions?"}'
```

See [RAG_API.md](RAG_API.md) for complete documentation.

---

## Basic Exploration

### List All Files by Language

**Use case**: Understand the language composition of your codebase.

```cypher
MATCH (f:File)
RETURN f.language AS language, count(f) AS file_count, sum(f.loc) AS total_loc
ORDER BY file_count DESC
```

**Interpretation**: Shows language distribution. Large Python codebases with minimal test files may indicate test coverage gaps.

### Find Largest Files

**Use case**: Identify files that may need splitting due to size.

```cypher
MATCH (f:File)
WHERE f.loc > 0
RETURN f.filePath AS file, f.loc AS lines_of_code
ORDER BY f.loc DESC
LIMIT 20
```

**Customization**: Adjust the `LIMIT` or add `WHERE f.loc > 500` to filter to very large files.

### Count Entities Per File

**Use case**: Find files with too many classes or functions.

```cypher
MATCH (f:File)
OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)
OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function)
WITH f, count(DISTINCT c) AS classes, count(DISTINCT fn) AS functions
WHERE classes + functions > 10
RETURN f.filePath AS file, classes, functions, classes + functions AS total_entities
ORDER BY total_entities DESC
LIMIT 20
```

**Interpretation**: Files with many entities may violate single-responsibility principle.

### Find All Classes with Inheritance

**Use case**: Map your class hierarchy.

```cypher
MATCH (child:Class)-[:INHERITS]->(parent:Class)
OPTIONAL MATCH (file:File)-[:CONTAINS]->(child)
RETURN child.name AS child_class,
       parent.name AS parent_class,
       file.filePath AS file
ORDER BY parent.name, child.name
```

**Tip**: Add `WHERE parent.name = 'BaseClass'` to focus on a specific hierarchy.

### Find Dataclasses and Exceptions

**Use case**: Inventory your data models and custom exceptions.

```cypher
MATCH (c:Class)
WHERE c.is_dataclass = true OR c.is_exception = true
OPTIONAL MATCH (file:File)-[:CONTAINS]->(c)
RETURN c.name AS class,
       CASE WHEN c.is_dataclass THEN 'dataclass' ELSE 'exception' END AS type,
       file.filePath AS file
ORDER BY type, c.name
```

### Find Async Functions

**Use case**: Identify all async code for concurrency review.

```cypher
MATCH (f:Function)
WHERE f.is_async = true
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY f.complexity DESC
```

---

## Relationship Analysis

### Find Most Imported Modules

**Use case**: Identify core dependencies and potential coupling hotspots.

```cypher
MATCH (f:File)-[:IMPORTS]->(m:Module)
WHERE m.is_external = false
RETURN m.qualifiedName AS module, count(f) AS import_count
ORDER BY import_count DESC
LIMIT 20
```

**Interpretation**: Heavily imported internal modules are coupling hotspots. Changes to them impact many files.

### Find External Dependencies

**Use case**: Audit third-party library usage.

```cypher
MATCH (f:File)-[i:IMPORTS]->(m:Module)
WHERE m.is_external = true
RETURN m.qualifiedName AS external_module,
       count(DISTINCT f) AS files_using_it
ORDER BY files_using_it DESC
LIMIT 30
```

**Customization**: Add `WHERE m.qualifiedName STARTS WITH 'requests'` to focus on a specific library.

### Map Function Call Graph

**Use case**: Understand which functions call which.

```cypher
MATCH (caller:Function)-[c:CALLS]->(callee:Function)
WHERE caller.filePath = callee.filePath  // Same file calls
RETURN caller.name AS caller,
       callee.name AS callee,
       c.line_number AS call_line
ORDER BY caller.name
LIMIT 50
```

**Variation for cross-file calls**:

```cypher
MATCH (caller:Function)-[c:CALLS]->(callee:Function)
WHERE caller.filePath <> callee.filePath
RETURN caller.qualifiedName AS caller,
       callee.qualifiedName AS callee
LIMIT 50
```

### Find Functions With Most Callers

**Use case**: Identify critical utility functions.

```cypher
MATCH (caller:Function)-[:CALLS]->(f:Function)
WITH f, count(DISTINCT caller) AS caller_count
WHERE caller_count > 5
OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
RETURN f.name AS function,
       caller_count,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY caller_count DESC
LIMIT 20
```

**Interpretation**: Functions with many callers are critical infrastructure. Test them thoroughly and refactor carefully.

### Find Deeply Nested Imports

**Use case**: Trace import chains to understand dependencies.

```cypher
MATCH path = (f1:File)-[:IMPORTS*3..6]->(f2:File)
WHERE f1 <> f2
RETURN f1.filePath AS source,
       [n IN nodes(path) | n.filePath] AS import_chain,
       f2.filePath AS target,
       length(path) AS depth
ORDER BY depth DESC
LIMIT 10
```

### Classes That Use Other Classes Heavily (Feature Envy)

**Use case**: Find methods that should be moved to another class.

```cypher
MATCH (c:Class)-[:CONTAINS]->(m:Function)
WHERE m.is_method = true
OPTIONAL MATCH (m)-[r_internal:USES|CALLS]->()-[:CONTAINS*0..1]-(c)
WITH m, c, count(DISTINCT r_internal) AS internal_uses
OPTIONAL MATCH (m)-[r_external:USES|CALLS]->(target)
WHERE NOT (target)-[:CONTAINS*0..1]-(c)
WITH m, c, internal_uses, count(DISTINCT r_external) AS external_uses
WHERE external_uses > 10 AND external_uses > internal_uses * 2
RETURN m.name AS method,
       c.name AS owner_class,
       internal_uses,
       external_uses,
       external_uses * 1.0 / CASE WHEN internal_uses = 0 THEN 1 ELSE internal_uses END AS ratio
ORDER BY ratio DESC
LIMIT 15
```

---

## Code Smell Detection

### Find God Classes

**Use case**: Identify classes with too many responsibilities (>15 methods, >500 LOC, or >100 complexity).

```cypher
MATCH (file:File)-[:CONTAINS]->(c:Class)
OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
WITH c, file,
     count(m) AS method_count,
     sum(m.complexity) AS total_complexity,
     COALESCE(c.lineEnd, 0) - COALESCE(c.lineStart, 0) AS loc
WHERE method_count >= 15 OR total_complexity >= 100 OR loc >= 500
// Filter out test classes
WHERE NOT c.name STARTS WITH 'Test'
  AND NOT c.name ENDS WITH 'Test'
RETURN c.name AS class,
       method_count,
       total_complexity,
       loc,
       file.filePath AS file
ORDER BY method_count DESC, total_complexity DESC
LIMIT 20
```

**Interpretation**: Classes at the top need refactoring. Consider:
- Extract Class for separate responsibilities
- Strategy pattern for varying behaviors
- See [AUTO_FIX.md](AUTO_FIX.md) for AI-assisted refactoring

### Find Circular Dependencies

**Use case**: Detect import cycles that create tight coupling.

```cypher
MATCH (f1:File)
MATCH (f2:File)
WHERE elementId(f1) < elementId(f2) AND f1 <> f2
MATCH path = shortestPath((f1)-[:IMPORTS*1..10]->(f2))
MATCH cyclePath = shortestPath((f2)-[:IMPORTS*1..10]->(f1))
WITH DISTINCT [node IN nodes(path) + nodes(cyclePath) WHERE node:File | node.filePath] AS cycle
WHERE size(cycle) > 1
RETURN cycle, size(cycle) AS cycle_length
ORDER BY cycle_length ASC
LIMIT 15
```

**Interpretation**: Shorter cycles (2-3 files) are highest priority. Break them by:
- Extracting shared interfaces
- Using dependency injection
- Moving shared code to a third module

### Find Unused Functions (Dead Code)

**Use case**: Identify functions that may be safe to remove.

```cypher
MATCH (f:Function)
WHERE NOT (f)<-[:CALLS]-()
  AND NOT (f)<-[:USES]-()
  AND NOT f.name STARTS WITH 'test_'
  AND NOT f.name IN ['main', '__main__', '__init__', 'setUp', 'tearDown']
  AND NOT f.name STARTS WITH '__'  // Skip magic methods
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
WITH f, file, COALESCE(f.decorators, []) AS decorators
WHERE size(decorators) = 0  // No decorators (routes, etc.)
RETURN f.name AS function,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 30
```

**Caution**: Verify before removing - some functions are called dynamically (getattr, plugins).

### Find Long Methods

**Use case**: Identify methods that are too long to understand easily.

```cypher
MATCH (f:Function)
WHERE (f.lineEnd - f.lineStart) > 50
OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
RETURN f.name AS function,
       f.lineEnd - f.lineStart AS lines,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY lines DESC
LIMIT 20
```

**Threshold**: Methods over 50 lines are hard to understand. Consider extracting helper functions.

### Find Complex Functions

**Use case**: Identify functions with high cyclomatic complexity.

```cypher
MATCH (f:Function)
WHERE f.complexity >= 15
OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
RETURN f.name AS function,
       f.complexity AS complexity,
       f.lineEnd - f.lineStart AS lines,
       file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 20
```

**Interpretation**:
- Complexity 10-15: Consider refactoring
- Complexity 15-25: Should refactor
- Complexity >25: Definitely needs splitting

### Find Functions With Many Parameters

**Use case**: Identify functions that may need parameter objects.

```cypher
MATCH (f:Function)
WHERE size(f.parameters) > 5
OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
RETURN f.name AS function,
       size(f.parameters) AS param_count,
       f.parameters AS parameters,
       file.filePath AS file
ORDER BY param_count DESC
LIMIT 20
```

**Fix**: Use a dataclass or NamedTuple for related parameters.

---

## Architecture Analysis

### Find Architectural Bottlenecks

**Use case**: Identify files that are heavily depended upon (high in-degree).

```cypher
MATCH (f:File)
OPTIONAL MATCH (f)-[:IMPORTS]->(out:File)
OPTIONAL MATCH (f)<-[:IMPORTS]-(in:File)
WITH f,
     count(DISTINCT out) AS out_degree,
     count(DISTINCT in) AS in_degree,
     count(DISTINCT out) + count(DISTINCT in) AS total_degree
WHERE total_degree >= 10
RETURN f.filePath AS file,
       in_degree AS depended_on_by,
       out_degree AS depends_on,
       total_degree
ORDER BY in_degree DESC
LIMIT 20
```

**Interpretation**: Files with high in-degree are architectural bottlenecks. Changes to them affect many files.

### Find Layer Violations

**Use case**: Detect when lower layers import from higher layers.

```cypher
// Assumes layer naming convention: e.g., api/, services/, models/
MATCH (f1:File)-[:IMPORTS]->(f2:File)
WHERE (f1.filePath CONTAINS '/models/' AND f2.filePath CONTAINS '/services/')
   OR (f1.filePath CONTAINS '/models/' AND f2.filePath CONTAINS '/api/')
   OR (f1.filePath CONTAINS '/services/' AND f2.filePath CONTAINS '/api/')
RETURN f1.filePath AS lower_layer,
       f2.filePath AS higher_layer,
       'layer_violation' AS issue
```

**Customization**: Adjust path patterns to match your project structure.

### Calculate Module Coupling

**Use case**: Find modules with high inter-module coupling.

```cypher
MATCH (f1:File)-[:IMPORTS]->(f2:File)
WHERE f1 <> f2
WITH split(f1.filePath, '/')[0] AS module1,
     split(f2.filePath, '/')[0] AS module2
WHERE module1 <> module2
RETURN module1, module2, count(*) AS coupling_strength
ORDER BY coupling_strength DESC
LIMIT 20
```

### Find Orphan Files

**Use case**: Identify files with no connections (neither import nor are imported).

```cypher
MATCH (f:File)
WHERE NOT (f)-[:IMPORTS]->()
  AND NOT (f)<-[:IMPORTS]-()
  AND NOT f.filePath CONTAINS '__init__'
  AND NOT f.filePath CONTAINS 'test'
RETURN f.filePath AS orphan_file,
       f.loc AS lines_of_code
ORDER BY f.loc DESC
```

**Interpretation**: Orphan files may be dead code or entry points. Review if they serve a purpose.

### Find Hub Classes (High Connectivity)

**Use case**: Identify central classes that many other classes depend on.

```cypher
MATCH (c:Class)
OPTIONAL MATCH (c)<-[:INHERITS]-(child:Class)
OPTIONAL MATCH (c)<-[:USES]-(user:Function)
OPTIONAL MATCH (c)<-[:CALLS]-(caller:Function)
WITH c,
     count(DISTINCT child) AS inheritors,
     count(DISTINCT user) AS users,
     count(DISTINCT caller) AS callers,
     count(DISTINCT child) + count(DISTINCT user) + count(DISTINCT caller) AS total_connections
WHERE total_connections >= 5
RETURN c.name AS class,
       inheritors,
       users,
       callers,
       total_connections
ORDER BY total_connections DESC
LIMIT 20
```

---

## Test Coverage

### Find Functions Without Tests

**Use case**: Identify code that may lack test coverage.

```cypher
MATCH (f:Function)
WHERE NOT f.name STARTS WITH 'test_'
  AND NOT f.filePath CONTAINS 'test'
  AND f.complexity > 5  // Focus on non-trivial functions
OPTIONAL MATCH (test:Function)-[:CALLS]->(f)
WHERE test.name STARTS WITH 'test_'
WITH f, count(test) AS test_count
WHERE test_count = 0
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS untested_function,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 30
```

**Interpretation**: High-complexity functions without tests are risk areas.

### Find Test Files and Their Coverage

**Use case**: Inventory your test files.

```cypher
MATCH (f:File)
WHERE f.is_test = true OR f.filePath CONTAINS 'test'
OPTIONAL MATCH (f)-[:CONTAINS]->(test_fn:Function)
WHERE test_fn.name STARTS WITH 'test_'
RETURN f.filePath AS test_file,
       count(test_fn) AS test_count
ORDER BY test_count DESC
```

### Find Functions Called Only by Tests

**Use case**: Identify functions that exist only for testing (test utilities).

```cypher
MATCH (f:Function)
WHERE NOT f.name STARTS WITH 'test_'
  AND NOT f.filePath CONTAINS 'test'
OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
WITH f, collect(caller) AS callers
WHERE size(callers) > 0
  AND ALL(c IN callers WHERE c.name STARTS WITH 'test_' OR c.filePath CONTAINS 'test')
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       size(callers) AS test_callers,
       file.filePath AS file
ORDER BY test_callers DESC
LIMIT 20
```

**Interpretation**: These functions may be test utilities - consider moving to a test helpers module.

### Map Test to Production Code

**Use case**: See which production code each test file covers.

```cypher
MATCH (test_file:File)-[:CONTAINS]->(test_fn:Function)
WHERE test_file.is_test = true AND test_fn.name STARTS WITH 'test_'
MATCH (test_fn)-[:CALLS]->(prod_fn:Function)
WHERE NOT prod_fn.filePath CONTAINS 'test'
OPTIONAL MATCH (prod_file:File)-[:CONTAINS]->(prod_fn)
RETURN test_file.filePath AS test_file,
       collect(DISTINCT prod_file.filePath) AS covers_files
LIMIT 30
```

---

## Security Queries

### Find Functions With External Input

**Use case**: Identify potential injection points.

```cypher
MATCH (f:Function)
WHERE ANY(param IN f.parameters WHERE
    param CONTAINS 'input' OR
    param CONTAINS 'query' OR
    param CONTAINS 'request' OR
    param CONTAINS 'data' OR
    param CONTAINS 'user'
)
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       f.parameters AS parameters,
       file.filePath AS file
ORDER BY f.complexity DESC
LIMIT 30
```

**Next step**: Review these functions for input validation and sanitization.

### Find Database Query Functions

**Use case**: Audit functions that interact with databases.

```cypher
MATCH (f:Function)
WHERE f.name CONTAINS 'query' OR
      f.name CONTAINS 'execute' OR
      f.name CONTAINS 'sql' OR
      f.name CONTAINS 'db'
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       f.complexity AS complexity,
       file.filePath AS file
ORDER BY f.complexity DESC
```

### Find Functions Using Dangerous Modules

**Use case**: Identify code using modules that could be security risks.

```cypher
MATCH (f:File)-[:IMPORTS]->(m:Module)
WHERE m.qualifiedName IN ['subprocess', 'os', 'eval', 'exec', 'pickle', 'yaml']
RETURN f.filePath AS file,
       collect(m.qualifiedName) AS dangerous_imports
ORDER BY size(collect(m.qualifiedName)) DESC
```

### Find Password/Secret Handling

**Use case**: Locate code that handles sensitive data.

```cypher
MATCH (f:Function)
WHERE f.name CONTAINS 'password' OR
      f.name CONTAINS 'secret' OR
      f.name CONTAINS 'token' OR
      f.name CONTAINS 'credential' OR
      f.name CONTAINS 'auth'
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       f.parameters AS parameters,
       file.filePath AS file
ORDER BY f.name
```

---

## Advanced Patterns

### Calculate LCOM (Lack of Cohesion)

**Use case**: Measure class cohesion - how well methods share fields.

```cypher
MATCH (c:Class {qualifiedName: $qualified_name})
MATCH (file:File)-[:CONTAINS]->(c)
MATCH (file)-[:CONTAINS]->(m:Function)
WHERE m.qualifiedName STARTS WITH c.qualifiedName + '.'
OPTIONAL MATCH (m)-[:USES]->(field)
WHERE field:Variable OR field:Attribute
WITH m, collect(DISTINCT field.name) AS fields
RETURN collect({method: m.name, fields: fields}) AS method_field_pairs,
       count(m) AS method_count
```

**Parameters**: `{qualified_name: "module.py::MyClass"}`

### Find Inheritance Depth

**Use case**: Identify deep inheritance hierarchies (smell).

```cypher
MATCH path = (c:Class)-[:INHERITS*]->(root:Class)
WHERE NOT (root)-[:INHERITS]->()
RETURN c.name AS class,
       length(path) AS depth,
       [n IN nodes(path) | n.name] AS hierarchy
ORDER BY depth DESC
LIMIT 20
```

**Interpretation**: Depth > 3-4 levels may indicate over-use of inheritance. Consider composition.

### Find Diamond Inheritance

**Use case**: Detect multiple inheritance diamonds.

```cypher
MATCH (c:Class)-[:INHERITS]->(p1:Class)
MATCH (c)-[:INHERITS]->(p2:Class)
WHERE p1 <> p2
MATCH (p1)-[:INHERITS*]->(common:Class)
MATCH (p2)-[:INHERITS*]->(common)
RETURN c.name AS child,
       p1.name AS parent1,
       p2.name AS parent2,
       common.name AS common_ancestor
```

### Find Shotgun Surgery Candidates

**Use case**: Identify changes that require touching many files.

```cypher
MATCH (f:Function)
OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
WITH f, count(DISTINCT caller.filePath) AS calling_files
WHERE calling_files >= 5
OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
RETURN f.name AS function,
       calling_files,
       file.filePath AS defined_in
ORDER BY calling_files DESC
LIMIT 20
```

**Interpretation**: Changing these functions requires updating many files. Consider:
- More stable interfaces
- Versioned APIs
- Adapter pattern

### Find Classes Without Documentation

**Use case**: Identify classes lacking docstrings.

```cypher
MATCH (c:Class)
WHERE c.docstring IS NULL OR c.docstring = ''
OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
WITH c, count(m) AS method_count
WHERE method_count > 3  // Focus on non-trivial classes
OPTIONAL MATCH (file:File)-[:CONTAINS]->(c)
RETURN c.name AS class,
       method_count,
       file.filePath AS file
ORDER BY method_count DESC
LIMIT 30
```

---

## Performance Tips

1. **Use LIMIT**: Always add `LIMIT` to avoid returning too many results
2. **Use indexes**: Queries on `qualifiedName`, `filePath`, `name` are indexed
3. **Parameterize**: Use `$parameter` syntax to enable query caching
4. **Filter early**: Put `WHERE` clauses as early as possible
5. **Profile queries**: Use `PROFILE` prefix to analyze performance

```cypher
PROFILE
MATCH (f:Function)
WHERE f.complexity > 10
RETURN f.name, f.complexity
LIMIT 100
```

---

## Related Documentation

- [CLAUDE.md](../CLAUDE.md) - Full schema and architecture
- [RAG_API.md](RAG_API.md) - Natural language code queries
- [AUTO_FIX.md](AUTO_FIX.md) - AI-powered refactoring for detected issues
- [FALKORDB.md](FALKORDB.md) - FalkorDB-specific query syntax
