# Kuzu Graph Schema

Repotoire uses [Kùzu](https://kuzudb.com/) as an embedded graph database. This document describes the complete schema.

## Node Types

### File
Source file in the repository.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key (e.g., `repo::path/to/file.py`) |
| name | STRING | Filename |
| filePath | STRING | Relative path from repo root |
| language | STRING | Programming language |
| loc | INT64 | Lines of code |
| hash | STRING | Content hash for change detection |
| package | STRING | Package name (e.g., `repotoire.graph`) |
| repoId | STRING | Repository identifier |
| churn | INT64 | Git churn score |
| churnCount | INT64 | Number of changes |
| complexity | DOUBLE | Aggregated complexity |
| codeHealth | DOUBLE | Computed health score |
| lineCount | INT64 | Total lines |
| is_test | BOOLEAN | Test file flag |
| docstring | STRING | Module docstring |
| semantic_context | STRING | Semantic summary |
| embedding | DOUBLE[] | Vector embedding |

### Class
Class definition.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key (e.g., `repo::module.ClassName`) |
| name | STRING | Class name |
| filePath | STRING | File containing the class |
| lineStart | INT64 | Start line |
| lineEnd | INT64 | End line |
| complexity | INT64 | Cyclomatic complexity |
| loc | INT64 | Lines of code |
| is_abstract | BOOLEAN | Abstract class flag |
| nesting_level | INT64 | Nesting depth |
| decorators | STRING[] | Applied decorators |
| churn | INT64 | Git churn score |
| num_authors | INT64 | Number of contributors |
| last_modified | STRING | ISO timestamp of last change |
| author | STRING | Primary author |
| commit_count | INT64 | Number of commits touching this |
| repoId | STRING | Repository identifier |
| docstring | STRING | Class docstring |
| semantic_context | STRING | Semantic summary |
| embedding | DOUBLE[] | Vector embedding |

### Function
Function or method definition.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Function name |
| filePath | STRING | File containing the function |
| lineStart | INT64 | Start line |
| lineEnd | INT64 | End line |
| complexity | INT64 | Cyclomatic complexity |
| loc | INT64 | Lines of code |
| is_async | BOOLEAN | Async function flag |
| is_method | BOOLEAN | Class method flag |
| is_public | BOOLEAN | Public visibility |
| is_exported | BOOLEAN | Exported from module |
| has_yield | BOOLEAN | Generator flag |
| yield_count | INT64 | Number of yield statements |
| max_chain_depth | INT64 | Max method chain depth |
| chain_example | STRING | Example chain expression |
| parameters | STRING[] | Parameter names |
| parameter_types | STRING | Type annotation string |
| return_type | STRING | Return type annotation |
| decorators | STRING[] | Applied decorators |
| in_degree | INT64 | Incoming call count |
| out_degree | INT64 | Outgoing call count |
| churn | INT64 | Git churn score |
| num_authors | INT64 | Number of contributors |
| last_modified | STRING | ISO timestamp of last change |
| author | STRING | Primary author |
| commit_count | INT64 | Number of commits touching this |
| repoId | STRING | Repository identifier |
| docstring | STRING | Function docstring |
| semantic_context | STRING | Semantic summary |
| embedding | DOUBLE[] | Vector embedding |

### Module
Import target (internal or external package).

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key (e.g., `numpy`) |
| name | STRING | Module name |
| is_external | BOOLEAN | External package flag |
| package | STRING | Package name |
| repoId | STRING | Repository identifier |

### Variable
Variable or attribute.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Variable name |
| filePath | STRING | File path |
| lineStart | INT64 | Definition line |
| var_type | STRING | Type annotation |
| repoId | STRING | Repository identifier |

### ExternalClass / ExternalFunction / BuiltinFunction
References to code outside the repository.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Name |
| module | STRING | Source module |
| repoId | STRING | Repository identifier |

### Type
Type annotation node for typed languages.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Type name |
| kind | STRING | `class`, `primitive`, `generic`, `union` |
| is_generic | BOOLEAN | Generic type flag |
| type_args | STRING[] | Generic type arguments |
| repoId | STRING | Repository identifier |

### Commit
Git commit for temporal tracking.

| Property | Type | Description |
|----------|------|-------------|
| hash | STRING | Primary key (commit SHA) |
| author | STRING | Commit author |
| timestamp | STRING | ISO timestamp |
| message | STRING | Commit message |
| repoId | STRING | Repository identifier |

### Component
Architecture component (auto-detected from directory structure).

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Component name |
| path_pattern | STRING | Glob pattern matching files |
| file_count | INT64 | Number of files |
| repoId | STRING | Repository identifier |

### Domain
Logical domain grouping components.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Domain name |
| description | STRING | Domain description |
| repoId | STRING | Repository identifier |

### Concept
Semantic concept for code clustering.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| name | STRING | Concept name |
| repoId | STRING | Repository identifier |

### DetectorMetadata
Code smell detector results.

| Property | Type | Description |
|----------|------|-------------|
| qualifiedName | STRING | Primary key |
| detector | STRING | Detector name |
| metric_name | STRING | Metric identifier |
| metric_value | DOUBLE | Metric value |
| repoId | STRING | Repository identifier |

---

## Edge Types

### Structural Relationships

| Edge | From | To | Properties | Description |
|------|------|----|------------|-------------|
| CONTAINS | File | Class, Function | — | File contains entity |
| CONTAINS | Class | Function | — | Class contains method |
| DEFINES | Class | Function | — | Class defines method |
| DEFINES_VAR | Function | Variable | — | Function defines variable |

### Call Relationships

| Edge | From | To | Properties | Description |
|------|------|----|------------|-------------|
| CALLS | Function | Function | line, call_name, is_self_call, **count**, **coupling_type** | Internal function call |
| CALLS_CLASS | Function | Class | line, call_name, is_self_call, count, coupling_type | Class instantiation |
| CALLS_EXT_FUNC | Function | ExternalFunction | line, call_name, is_self_call, count, coupling_type | External function call |
| CALLS_EXT_CLASS | Function | ExternalClass | line, call_name, is_self_call, count, coupling_type | External class instantiation |
| CALLS_BUILTIN | Function | BuiltinFunction | line, call_name, is_self_call, count, coupling_type | Builtin function call |

**New properties on CALLS edges:**
- `count` (INT64) — Number of times caller calls callee
- `coupling_type` (STRING) — `simple`, `data`, `control`, or `stamp`

### Import Relationships

| Edge | From | To | Description |
|------|------|----|-------------|
| IMPORTS | File | Module | Import statement |
| IMPORTS_FILE | File | File | File-to-file import |
| IMPORTS_EXT_CLASS | File | ExternalClass | External class import |
| IMPORTS_EXT_FUNC | File | ExternalFunction | External function import |

### Inheritance & Usage

| Edge | From | To | Description |
|------|------|----|-------------|
| INHERITS | Class | Class | Class inheritance |
| OVERRIDES | Function | Function | Method override |
| DECORATES | Function | Function | Decorator application |
| USES | Function | Variable, Function, Class | Reference/usage |
| TESTS | Function | Function | Test function relationship |

### Type Relationships

| Edge | From | To | Properties | Description |
|------|------|----|------------|-------------|
| RETURNS | Function | Type | — | Function return type |
| HAS_PARAMETER | Function | Type | name, position | Function parameter type |
| SUBTYPES | Type | Type | — | Type inheritance |
| TYPE_OF_CLASS | Type | Class | — | Type represents a class |

### Data Flow

| Edge | From | To | Properties | Description |
|------|------|----|------------|-------------|
| DATA_FLOWS_TO | Function | Function | tainted, via | Data flows between functions |

**Properties:**
- `tainted` (BOOLEAN) — True if source is user input (request, stdin, etc.)
- `via` (STRING) — How data flows: `return`, `argument`, or variable name

### Similarity

| Edge | From | To | Properties | Description |
|------|------|----|------------|-------------|
| SIMILAR_TO | Function | Function | score, method | Similar functions (potential clones) |

**Properties:**
- `score` (DOUBLE) — Similarity score 0.0-1.0
- `method` (STRING) — Detection method: `ast`, `token`, `semantic`

### Temporal Relationships

| Edge | From | To | Description |
|------|------|----|-------------|
| MODIFIED_IN_FUNC | Commit | Function | Commit modified function |
| MODIFIED_IN_CLASS | Commit | Class | Commit modified class |

### Architecture Relationships

| Edge | From | To | Description |
|------|------|----|-------------|
| BELONGS_TO_COMPONENT | File | Component | File belongs to component |
| BELONGS_TO_DOMAIN | Component | Domain | Component belongs to domain |

### Analysis Relationships

| Edge | From | To | Description |
|------|------|----|-------------|
| FLAGGED_BY | Function, Class | DetectorMetadata | Code smell detection |

---

## Example Queries

### Find all functions in a file
```cypher
MATCH (f:File {filePath: 'src/main.py'})-[:CONTAINS]->(fn:Function)
RETURN fn.name, fn.complexity, fn.loc
```

### Find call graph for a function
```cypher
MATCH (caller:Function {name: 'process'})-[:CALLS]->(callee:Function)
RETURN caller.name, callee.name
```

### Find hot call paths (high frequency)
```cypher
MATCH (a:Function)-[c:CALLS]->(b:Function)
WHERE c.count > 10
RETURN a.name, b.name, c.count
ORDER BY c.count DESC
```

### Find class hierarchy
```cypher
MATCH (child:Class)-[:INHERITS*]->(parent:Class)
WHERE child.name = 'MyClass'
RETURN child.name, parent.name
```

### Find most complex functions
```cypher
MATCH (f:Function)
WHERE f.complexity > 10
RETURN f.name, f.filePath, f.complexity
ORDER BY f.complexity DESC
LIMIT 10
```

### Find functions with high churn and complexity
```cypher
MATCH (f:Function)
WHERE f.churn > 5 AND f.complexity > 15
RETURN f.name, f.filePath, f.churn, f.complexity
```

### Find external dependencies
```cypher
MATCH (f:File)-[:IMPORTS]->(m:Module)
WHERE m.is_external = true
RETURN f.filePath, collect(m.name) AS dependencies
```

### Find unused functions (no callers)
```cypher
MATCH (f:Function)
WHERE NOT ()-[:CALLS]->(f) AND f.is_method = false AND f.is_public = true
RETURN f.name, f.filePath
```

### Find test coverage relationships
```cypher
MATCH (test:Function)-[:TESTS]->(impl:Function)
RETURN impl.name, collect(test.name) AS tests
```

### Find similar functions (clones)
```cypher
MATCH (a:Function)-[s:SIMILAR_TO]->(b:Function)
WHERE s.score > 0.85
RETURN a.name, b.name, s.score
ORDER BY s.score DESC
```

### Find tainted data flows (security)
```cypher
MATCH (source:Function)-[d:DATA_FLOWS_TO*]->(sink:Function)
WHERE d[0].tainted = true
RETURN source.name, sink.name, length(d) AS hops
```

### Find code ownership (who knows what)
```cypher
MATCH (f:Function)
WHERE f.commit_count > 10
RETURN f.name, f.author, f.commit_count
ORDER BY f.commit_count DESC
```

### Find functions by return type
```cypher
MATCH (f:Function)-[:RETURNS]->(t:Type)
WHERE t.name = 'Optional'
RETURN f.name, f.filePath
```

### Find architecture components
```cypher
MATCH (f:File)-[:BELONGS_TO_COMPONENT]->(c:Component)-[:BELONGS_TO_DOMAIN]->(d:Domain)
RETURN d.name, c.name, count(f) AS files
ORDER BY files DESC
```

### Find public API surface
```cypher
MATCH (f:Function)
WHERE f.is_public = true AND f.is_exported = true
RETURN f.name, f.filePath, f.return_type
```

---

## Notes

- All node types use `qualifiedName` as primary key
- REL TABLE GROUPs (CONTAINS, USES, FLAGGED_BY) enable polymorphic queries
- Embeddings stored as `DOUBLE[]` for vector similarity search
- Use `repoId` to filter multi-repo databases
- Temporal properties require git repository context
- Data flow analysis runs as optional post-processing step
- Similarity edges created for functions with score > 0.7
