# FalkorDB Integration Guide

FalkorDB is a Redis-based graph database that provides a cost-effective alternative to Neo4j for development, testing, and smaller deployments.

## Quick Start

### Start FalkorDB with Docker

```bash
docker run \
    --name repotoire-falkordb \
    -p 6379:6379 \
    -d \
    falkordb/falkordb:latest
```

### Configure Repotoire

```bash
# Set environment variables
export REPOTOIRE_NEO4J_URI=bolt://localhost:6379
export REPOTOIRE_NEO4J_PASSWORD=falkor-password  # or empty if no auth
export REPOTOIRE_DATABASE_BACKEND=falkordb

# Or use CLI flags
repotoire ingest /path/to/repo --backend falkordb
```

## Why FalkorDB?

| Feature | Neo4j | FalkorDB |
|---------|-------|----------|
| **Cost** | Enterprise license required for production | Open source (BSD-3) |
| **Memory** | High (JVM-based) | Low (Redis-based) |
| **Setup** | Complex (plugins, GDS) | Simple (single container) |
| **Speed** | Fast (optimized for graphs) | Very fast (Redis + Cypher) |
| **Vector Search** | Native (5.18+) | Native support |
| **Temporal Types** | `datetime()`, `duration()` | UNIX timestamps only |
| **GDS Algorithms** | Full library | Not available |

### When to Use FalkorDB

- **Development/Testing**: Fast iteration with minimal resource usage
- **Small-Medium Codebases**: <50k files, <1M relationships
- **CI/CD Pipelines**: Quick setup, disposable instances
- **Cost-Sensitive Deployments**: No licensing costs

### When to Use Neo4j

- **Enterprise Deployments**: Need GDS algorithms, APOC procedures
- **Large Codebases**: >100k files, complex graph traversals
- **Advanced Analytics**: Community detection, graph ML features
- **Temporal Queries**: Native datetime operations

## Cypher Compatibility

FalkorDB supports most Cypher queries with some limitations:

### Supported Features

- Basic MATCH, CREATE, MERGE, DELETE, SET
- Path queries: `MATCH (a)-[*1..3]->(b)`
- Aggregations: `count()`, `collect()`, `sum()`, `avg()`
- UNWIND, WITH, WHERE, RETURN
- Index creation: `CREATE INDEX ON :Label(property)`
- Vector indexes and similarity search

### Unsupported Features

| Feature | Neo4j Syntax | FalkorDB Alternative |
|---------|--------------|---------------------|
| Temporal types | `datetime()` | UNIX timestamp (integer) |
| Duration | `duration({days: 7})` | Calculate in Python |
| Regex matching | `WHERE n.name =~ 'pattern'` | Use `CONTAINS` or `STARTS WITH` |
| APOC procedures | `CALL apoc.*` | Not available |
| GDS algorithms | `CALL gds.*` | Use Rust algorithms |
| REMOVE keyword | `REMOVE n.prop` | `SET n.prop = NULL` |

### Temporal Data Handling

FalkorDB doesn't support Neo4j's temporal types. Repotoire automatically uses UNIX timestamps when connected to FalkorDB:

```python
# Neo4j
SET r.lastUsed = datetime()
WHERE s.committedAt >= datetime() - duration({days: 7})

# FalkorDB (automatic conversion)
SET r.lastUsed = 1732780800  # UNIX timestamp
WHERE s.committedAt >= 1732176000
```

## Vector Search

FalkorDB supports vector similarity search with some syntax differences:

### Creating Vector Indexes

```cypher
-- FalkorDB syntax
CREATE VECTOR INDEX FOR (n:Function) ON (n.embedding)
OPTIONS {dimension: 1536, similarityFunction: 'cosine'}
```

### Storing Embeddings

```cypher
-- FalkorDB requires vecf32() wrapper
SET n.embedding = vecf32([0.1, 0.2, ...])
```

### Querying Vectors

```cypher
-- FalkorDB uses db.idx.vector.queryNodes
CALL db.idx.vector.queryNodes('vector_idx_Function_embedding', 10, vecf32($embedding))
YIELD node, score
RETURN node.name, score
```

Repotoire's `GraphRAGRetriever` automatically handles these differences.

## Graph Algorithms

FalkorDB doesn't include Neo4j's Graph Data Science (GDS) library. Repotoire uses Rust-based algorithms as a replacement:

| Algorithm | Neo4j GDS | Repotoire Rust |
|-----------|-----------|----------------|
| PageRank | `gds.pageRank` | `graph_algorithms.pagerank()` |
| Betweenness Centrality | `gds.betweenness` | `graph_algorithms.betweenness_centrality()` |
| Community Detection | `gds.louvain` | `graph_algorithms.leiden()` |
| SCC (Cycles) | `gds.scc` | `graph_algorithms.strongly_connected_components()` |
| Harmonic Centrality | `gds.closeness.harmonic` | `graph_algorithms.harmonic_centrality()` |

These algorithms are automatically used when FalkorDB is detected.

## Performance Comparison

Based on integration tests:

| Operation | Neo4j | FalkorDB |
|-----------|-------|----------|
| Connection | ~500ms | ~50ms |
| Bulk Insert (1k nodes) | ~2s | ~1s |
| Simple Query | ~10ms | ~5ms |
| Graph Traversal | ~50ms | ~30ms |
| Vector Search | ~100ms | ~80ms |

FalkorDB is generally faster for simple operations due to Redis's in-memory architecture.

## Configuration Reference

### Environment Variables

```bash
# Required
REPOTOIRE_NEO4J_URI=bolt://localhost:6379
REPOTOIRE_NEO4J_PASSWORD=your-password  # Can be empty

# Optional
REPOTOIRE_DATABASE_BACKEND=falkordb  # or 'neo4j' (default)
```

### Client Factory

```python
from repotoire.graph.factory import create_database_client

# Automatic detection from URI
client = create_database_client(
    uri="bolt://localhost:6379",
    password="your-password"
)

# Explicit backend
client = create_database_client(
    uri="bolt://localhost:6379",
    password="your-password",
    backend="falkordb"
)
```

### Docker Compose

```yaml
version: '3.8'
services:
  falkordb:
    image: falkordb/falkordb:latest
    ports:
      - "6379:6379"
    volumes:
      - falkordb_data:/data
    environment:
      - FALKORDB_ARGS=--requirepass your-password

volumes:
  falkordb_data:
```

## Migration from Neo4j

### Export from Neo4j

```bash
# Export nodes and relationships to JSON
repotoire migrate export --output backup.json
```

### Import to FalkorDB

```bash
# Start FalkorDB
docker run -d -p 6379:6379 falkordb/falkordb:latest

# Import data
export REPOTOIRE_NEO4J_URI=bolt://localhost:6379
repotoire migrate import --input backup.json
```

### Validate Migration

```bash
repotoire migrate validate
```

## Troubleshooting

### Connection Issues

```bash
# Test FalkorDB connection
redis-cli -h localhost -p 6379 PING
# Expected: PONG

# Check graph exists
redis-cli -h localhost -p 6379 GRAPH.LIST
```

### Query Errors

**Error**: `datetime is not a procedure`
- **Cause**: FalkorDB doesn't support `datetime()` function
- **Fix**: Repotoire automatically converts to UNIX timestamps

**Error**: `gds.graph.exists is not registered`
- **Cause**: FalkorDB doesn't have GDS library
- **Fix**: Repotoire automatically uses Rust algorithms

**Error**: `Vector index not found`
- **Cause**: Index created after data insertion
- **Fix**: Create vector indexes before ingesting data with embeddings

### Performance Issues

1. **Slow queries**: Ensure indexes exist on frequently queried properties
2. **High memory**: Redis is in-memory; consider pagination for large results
3. **Connection timeouts**: FalkorDB has lower default timeouts than Neo4j

## Testing

Run FalkorDB integration tests:

```bash
# Start FalkorDB
docker run -d -p 6379:6379 --name test-falkordb falkordb/falkordb:latest

# Run tests
REPOTOIRE_NEO4J_URI=bolt://localhost:6379 \
REPOTOIRE_NEO4J_PASSWORD= \
pytest tests/integration/test_falkordb.py -v

# Cleanup
docker rm -f test-falkordb
```

## Compatibility Matrix

| Feature | Status | Notes |
|---------|--------|-------|
| Basic Ingestion | Supported | Full Python parser support |
| Incremental Analysis | Supported | Hash-based change detection |
| Graph Detectors | Supported | All Cypher-based detectors |
| Hybrid Detectors | Supported | Ruff, Mypy, Bandit, etc. |
| Vector Search/RAG | Supported | With syntax adaptations |
| Rules Engine | Supported | With UNIX timestamp fallback |
| Temporal Metrics | Supported | With UNIX timestamp fallback |
| Auto-Fix | Supported | GPT-4o + RAG works normally |
| GDS Algorithms | Rust fallback | Leiden, PageRank, etc. |
| APOC Procedures | Not supported | Use alternative queries |
