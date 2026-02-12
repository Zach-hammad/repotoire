# Graph Schema Reference

Repotoire builds a knowledge graph of your codebase using [Kuzu](https://kuzudb.com/), an embedded graph database. This document describes the node types, edge types, and how to query the graph.

## Overview

```
┌─────────┐     CONTAINS      ┌──────────┐     CONTAINS      ┌──────────┐
│  File   │──────────────────▶│  Class   │──────────────────▶│ Function │
└─────────┘                   └──────────┘                   └──────────┘
     │                             │                              │
     │ IMPORTS                     │ INHERITS                     │ CALLS
     ▼                             ▼                              ▼
┌─────────┐                   ┌──────────┐                   ┌──────────┐
│ Module  │                   │  Class   │                   │ Function │
└─────────┘                   └──────────┘                   └──────────┘
```

---

## Node Types

### File

Represents a source file in the repository.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Unique identifier (PRIMARY KEY) |
| `name` | STRING | File name without path |
| `filePath` | STRING | Full path to the file |
| `language` | STRING | Programming language (python, javascript, rust, etc.) |
| `loc` | INT64 | Lines of code |
| `hash` | STRING | Content hash for change detection |
| `repoId` | STRING | Repository identifier |
| `package` | STRING | Package/module this file belongs to |
| `churn` | INT64 | Total lines changed (git history) |
| `churnCount` | INT64 | Number of commits touching this file |
| `complexity` | INT64 | Aggregate complexity score |
| `codeHealth` | DOUBLE | Overall health score (0-100) |
| `lineCount` | INT64 | Total line count |
| `is_test` | BOOLEAN | Whether this is a test file |
| `docstring` | STRING | Module-level docstring |
| `semantic_context` | STRING | AI-generated semantic description |
| `embedding` | DOUBLE[] | Vector embedding for similarity search |

### Class

Represents a class definition.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Unique identifier (e.g., `module.ClassName`) |
| `name` | STRING | Class name |
| `filePath` | STRING | File containing the class |
| `lineStart` | INT64 | Starting line number |
| `lineEnd` | INT64 | Ending line number |
| `methodCount` | INT64 | Number of methods |
| `complexity` | INT64 | Cyclomatic complexity |
| `loc` | INT64 | Lines of code |
| `is_abstract` | BOOLEAN | Whether class is abstract |
| `nesting_level` | INT64 | Nesting depth within file |
| `decorators` | STRING[] | Applied decorators |
| `churn` | INT64 | Git churn (lines changed) |
| `num_authors` | INT64 | Number of contributors |
| `repoId` | STRING | Repository identifier |
| `docstring` | STRING | Class docstring |
| `semantic_context` | STRING | AI-generated context |
| `embedding` | DOUBLE[] | Vector embedding |
| `last_modified` | STRING | Last modification timestamp |
| `author` | STRING | Primary author |
| `commit_count` | INT64 | Number of commits |

### Function

Represents a function or method.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Unique identifier (e.g., `module.Class.method`) |
| `name` | STRING | Function name |
| `filePath` | STRING | File containing the function |
| `lineStart` | INT64 | Starting line number |
| `lineEnd` | INT64 | Ending line number |
| `complexity` | INT64 | Cyclomatic complexity |
| `loc` | INT64 | Lines of code |
| `is_async` | BOOLEAN | Whether function is async |
| `is_method` | BOOLEAN | Whether it's a class method |
| `is_public` | BOOLEAN | Whether it's public |
| `is_exported` | BOOLEAN | Whether it's exported |
| `has_yield` | BOOLEAN | Whether it's a generator |
| `yield_count` | INT64 | Number of yield statements |
| `max_chain_depth` | INT64 | Maximum method chain depth |
| `chain_example` | STRING | Example of longest chain |
| `parameters` | STRING[] | Parameter names |
| `parameter_types` | STRING | Parameter type annotations |
| `return_type` | STRING | Return type annotation |
| `decorators` | STRING[] | Applied decorators |
| `in_degree` | INT64 | Number of callers |
| `out_degree` | INT64 | Number of callees |
| `churn` | INT64 | Git churn |
| `num_authors` | INT64 | Number of contributors |
| `repoId` | STRING | Repository identifier |
| `docstring` | STRING | Function docstring |
| `semantic_context` | STRING | AI-generated context |
| `embedding` | DOUBLE[] | Vector embedding |

### Module

Represents an imported module (external or internal).

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Full module path |
| `name` | STRING | Module name |
| `is_external` | BOOLEAN | True if external package |
| `package` | STRING | Package name |
| `repoId` | STRING | Repository identifier |

### Variable

Represents a variable or attribute.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Unique identifier |
| `name` | STRING | Variable name |
| `filePath` | STRING | File location |
| `lineStart` | INT64 | Line number |
| `var_type` | STRING | Type annotation if available |
| `repoId` | STRING | Repository identifier |

### Type

Type information for function signatures.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Type identifier |
| `name` | STRING | Type name |
| `kind` | STRING | Type kind (class, generic, primitive) |
| `is_generic` | BOOLEAN | Whether type is generic |
| `type_args` | STRING[] | Generic type arguments |
| `repoId` | STRING | Repository identifier |

### Commit

Git commit information.

| Property | Type | Description |
|----------|------|-------------|
| `hash` | STRING | Commit SHA |
| `author` | STRING | Author name |
| `timestamp` | STRING | Commit timestamp |
| `message` | STRING | Commit message |
| `repoId` | STRING | Repository identifier |

### Component

Architectural component (directory-level grouping).

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Component identifier |
| `name` | STRING | Component name |
| `path_pattern` | STRING | Path pattern |
| `file_count` | INT64 | Number of files |
| `repoId` | STRING | Repository identifier |

### Domain

High-level architectural domain.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Domain identifier |
| `name` | STRING | Domain name |
| `description` | STRING | Domain description |
| `repoId` | STRING | Repository identifier |

### DetectorMetadata

Code smell detector results.

| Property | Type | Description |
|----------|------|-------------|
| `qualifiedName` | STRING | Metadata identifier |
| `detector` | STRING | Detector name |
| `metric_name` | STRING | Metric being measured |
| `metric_value` | DOUBLE | Metric value |
| `repoId` | STRING | Repository identifier |

### External Types

- **ExternalClass**: Class from an external library
- **ExternalFunction**: Function from an external library  
- **BuiltinFunction**: Built-in language function
- **Concept**: Semantic concept (for AI analysis)

---

## Edge Types (Relationships)

### Containment

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `CONTAINS_CLASS` | File | Class | File contains a class |
| `CONTAINS_FUNCTION` | File | Function | File contains a function |
| `CONTAINS_METHOD` | Class | Function | Class contains a method |

### Calls

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `CALLS` | Function | Function | `line`, `call_name`, `is_self_call`, `count`, `coupling_type` | Function calls another function |
| `CALLS_CLASS` | Function | Class | `line`, `call_name`, `is_self_call`, `count`, `coupling_type` | Function instantiates a class |
| `CALLS_EXT_FUNC` | Function | ExternalFunction | Same as CALLS | Calls external function |
| `CALLS_EXT_CLASS` | Function | ExternalClass | Same as CALLS | Calls external class |
| `CALLS_BUILTIN` | Function | BuiltinFunction | Same as CALLS | Calls builtin function |

### Usage

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `USES_VAR` | Function | Variable | Function uses a variable |
| `USES_FUNC` | Function | Function | Function references another function |
| `USES_CLASS` | Function | Class | Function references a class |

### Imports

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `IMPORTS` | File | Module | File imports a module |
| `IMPORTS_FILE` | File | File | File imports another file |
| `IMPORTS_EXT_CLASS` | File | ExternalClass | File imports external class |
| `IMPORTS_EXT_FUNC` | File | ExternalFunction | File imports external function |

### Inheritance

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `INHERITS` | Class | Class | Class inherits from another |
| `OVERRIDES` | Function | Function | Method overrides parent method |

### Definitions

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `DEFINES` | Class | Function | Class defines a method |
| `DEFINES_VAR` | Function | Variable | Function defines a variable |

### Type System

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `RETURNS` | Function | Type | | Function return type |
| `HAS_PARAMETER` | Function | Type | `name`, `position` | Function parameter type |
| `SUBTYPES` | Type | Type | | Type inheritance |
| `TYPE_OF_CLASS` | Type | Class | | Type refers to class |

### Data Flow

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `DATA_FLOWS_TO` | Function | Function | `tainted`, `via` | Data flow for taint tracking |
| `SIMILAR_TO` | Function | Function | `score`, `method` | Function similarity (clone detection) |

### Architecture

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `BELONGS_TO_COMPONENT` | File | Component | File belongs to component |
| `BELONGS_TO_DOMAIN` | Component | Domain | Component in domain |

### Testing

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `TESTS` | Function | Function | Test function tests target |
| `DECORATES` | Function | Function | Decorator applied to function |

### Detection

| Relationship | From | To | Description |
|--------------|------|-----|-------------|
| `FLAGGED_BY_FUNC` | Function | DetectorMetadata | Function flagged by detector |
| `FLAGGED_BY_CLASS` | Class | DetectorMetadata | Class flagged by detector |

### Git History

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `MODIFIED_IN_FUNC` | Function | Commit | `line_start`, `line_end` | Function modified in commit |
| `MODIFIED_IN_CLASS` | Class | Commit | `line_start`, `line_end` | Class modified in commit |

---

## Example Queries

### Find all functions in a file

```cypher
MATCH (f:File {filePath: 'src/main.py'})-[:CONTAINS_FUNCTION]->(fn:Function)
RETURN fn.name, fn.complexity, fn.loc
ORDER BY fn.complexity DESC
```

### Get the call graph

```cypher
MATCH (caller:Function)-[r:CALLS]->(callee:Function)
RETURN caller.name AS caller, callee.name AS callee, r.count AS call_count
```

### Find functions with high fan-out (many dependencies)

```cypher
MATCH (f:Function)-[r:CALLS]->(callee:Function)
WITH f, count(callee) AS fanOut
WHERE fanOut > 10
RETURN f.qualifiedName, f.name, fanOut
ORDER BY fanOut DESC
```

### Find class inheritance hierarchy

```cypher
MATCH (child:Class)-[:INHERITS]->(parent:Class)
RETURN child.name AS child, parent.name AS parent
```

### Find circular dependencies

```cypher
MATCH path = (f1:File)-[:IMPORTS_FILE*]->(f1)
RETURN [n IN nodes(path) | n.filePath] AS cycle
```

### Get external dependencies

```cypher
MATCH (f:File)-[:IMPORTS]->(m:Module)
WHERE m.is_external = true
RETURN m.name AS module, count(f) AS import_count
ORDER BY import_count DESC
```

### Find complex functions

```cypher
MATCH (f:Function)
WHERE f.complexity > 15
RETURN f.qualifiedName, f.name, f.filePath, f.complexity, f.loc
ORDER BY f.complexity DESC
LIMIT 20
```

### Get methods in a class

```cypher
MATCH (c:Class {name: 'UserService'})-[:CONTAINS_METHOD]->(m:Function)
RETURN m.name, m.complexity
ORDER BY m.name
```

### Find dead code (functions never called)

```cypher
MATCH (f:Function)
WHERE f.is_public = true 
  AND NOT f.name STARTS WITH '_'
  AND NOT exists((other:Function)-[:CALLS]->(f))
RETURN f.qualifiedName, f.filePath
```

### Component coupling analysis

```cypher
MATCH (f1:File)-[:BELONGS_TO_COMPONENT]->(c1:Component)
MATCH (f2:File)-[:BELONGS_TO_COMPONENT]->(c2:Component)
MATCH (fn1:Function {filePath: f1.filePath})-[:CALLS]->(fn2:Function {filePath: f2.filePath})
WHERE c1 <> c2
RETURN c1.name AS source, c2.name AS target, count(*) AS call_count
ORDER BY call_count DESC
```

---

## Using the Graph with `repotoire ask`

You can query the graph using natural language:

```bash
# Find what calls a specific function
repotoire ask "what functions call UserService.authenticate"

# Find dependencies
repotoire ask "what modules does auth.py import"

# Architectural questions
repotoire ask "which components have the most coupling"
```

The CLI translates natural language to Cypher queries and returns results.
