# CLAUDE.md

This file provides comprehensive guidance to Claude Code (claude.ai/code) and developers working with the Repotoire codebase.

## Project Overview

Repotoire is a graph-powered code health platform that analyzes codebases using knowledge graphs to detect code smells, architectural issues, and technical debt. Unlike traditional linters that examine files in isolation, Repotoire builds a Neo4j knowledge graph combining:
- **Structural analysis** (AST parsing)
- **Semantic understanding** (NLP + AI)
- **Relational patterns** (graph algorithms)

This multi-layered approach enables detection of complex issues that traditional tools miss, such as circular dependencies, architectural bottlenecks, and modularity problems.

## Development Setup

### Installation

```bash
# Install with development dependencies
pip install -e ".[dev]"

# Install with all optional dependencies (GDS, multi-language support)
pip install -e ".[dev,gds,all-languages,config]"

# Download spaCy model for NLP features
python -m spacy download en_core_web_lg
```

### Neo4j Setup

Neo4j is required for the graph database. Start with Docker:

```bash
docker run \
    --name repotoire-neo4j \
    -p 7474:7474 -p 7688:7687 \
    -d \
    -e NEO4J_AUTH=neo4j/your-password \
    -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
    neo4j:latest
```

**Note**: Port 7688 is used on the host to avoid conflicts with standard Neo4j installations. The container still uses 7687 internally.

Configure credentials:
```bash
export REPOTOIRE_NEO4J_URI=bolt://localhost:7688
export REPOTOIRE_NEO4J_PASSWORD=your-password
```

### Common Commands

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=repotoire --cov-report=term-missing --cov-report=html

# Format code
black repotoire tests

# Lint
ruff check repotoire tests

# Type check
mypy repotoire

# Ingest a codebase into Neo4j
repotoire ingest /path/to/repo

# Analyze codebase health
repotoire analyze /path/to/repo -o report.html --format html

# Validate configuration
repotoire validate
```

## Architecture

### System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           REPOTOIRE ARCHITECTURE                             │
└──────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  INPUT LAYER                                                             │
├─────────────────────────────────────────────────────────────────────────┤
│  • Source Code Repository                                               │
│  • Configuration Files (.repotoirerc, repotoire.toml)                         │
│  • Environment Variables (REPOTOIRE_*)                                     │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PARSING LAYER (repotoire/parsers/)                                        │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │  Python     │  │  TypeScript  │  │  Java        │                  │
│  │  Parser     │  │  Parser      │  │  Parser      │  ...             │
│  │  (AST)      │  │  (TreeSitter)│  │  (TreeSitter)│                  │
│  └─────────────┘  └──────────────┘  └──────────────┘                  │
│         │                  │                 │                          │
│         └──────────────────┴─────────────────┘                          │
│                            │                                             │
│                   CodeParser Interface                                  │
│                            │                                             │
│              parse() → Entities + Relationships                         │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  INGESTION PIPELINE (repotoire/pipeline/)                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Scan Repository (glob patterns, security validation)               │
│  2. Parse Files (extract entities & relationships)                     │
│  3. Batch Processing (100 entities per batch)                          │
│  4. Graph Construction (nodes + edges)                                 │
│  5. Validation & Error Handling                                        │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  GRAPH LAYER (repotoire/graph/)                                            │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                     NEO4J DATABASE                              │   │
│  ├────────────────────────────────────────────────────────────────┤   │
│  │  Nodes:                    Relationships:                      │   │
│  │  • File                    • IMPORTS                           │   │
│  │  • Module                  • CALLS                             │   │
│  │  • Class                   • CONTAINS                          │   │
│  │  • Function                • INHERITS                          │   │
│  │  • Variable                • USES                              │   │
│  │  • Attribute               • DEFINES                           │   │
│  │  • Concept (AI)            • DESCRIBES                         │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Neo4jClient: Connection pool, retry logic, batch operations          │
│  GraphSchema: Constraints, indexes, initialization                    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ANALYSIS ENGINE (repotoire/detectors/)                                    │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────┐  ┌──────────────────┐  ┌─────────────────────┐ │
│  │  Circular Dep     │  │  God Class       │  │  Dead Code          │ │
│  │  Detector         │  │  Detector        │  │  Detector           │ │
│  │  (Tarjan's)       │  │  (Metrics)       │  │  (Graph Traversal)  │ │
│  └───────────────────┘  └──────────────────┘  └─────────────────────┘ │
│           │                      │                       │              │
│           └──────────────────────┴───────────────────────┘              │
│                                  │                                      │
│                         AnalysisEngine                                  │
│                                  │                                      │
│                Aggregate Findings + Calculate Scores                   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SCORING SYSTEM                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Structure (40%):    Quality (30%):      Architecture (30%):           │
│  • Modularity        • Dead Code %       • Layer Violations            │
│  • Coupling          • Duplication %     • Boundary Violations         │
│  • Circular Deps     • God Class Count   • Abstraction Ratio           │
│  • Bottlenecks                                                         │
│                                                                         │
│  Weighted Score → Letter Grade (A-F)                                   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  REPORTING LAYER (repotoire/reporters/, repotoire/cli.py)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │  Terminal   │  │  JSON        │  │  HTML        │                  │
│  │  (Rich)     │  │  (CI/CD)     │  │  (Reports)   │                  │
│  │             │  │              │  │              │                  │
│  │  • Colors   │  │  • Structured│  │  • Code      │                  │
│  │  • Trees    │  │  • Machine   │  │    Snippets  │                  │
│  │  • Tables   │  │    Readable  │  │  • Charts    │                  │
│  └─────────────┘  └──────────────┘  └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
                    USER / CI/CD SYSTEM
```

### Core Pipeline Flow

```
┌──────────┐    ┌────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐
│ Codebase │───▶│ Parser │───▶│ Entities │───▶│  Neo4j  │───▶│ Detectors│
│          │    │  (AST) │    │   +Rels  │    │  Graph  │    │          │
└──────────┘    └────────┘    └──────────┘    └─────────┘    └──────────┘
                                                                     │
                                                                     ▼
┌──────────┐    ┌──────────┐    ┌────────────┐    ┌──────────────────┐
│  Reports │◀───│   CLI    │◀───│   Health   │◀───│   Analysis       │
│          │    │          │    │   Report   │    │   Engine         │
└──────────┘    └──────────┘    └────────────┘    └──────────────────┘
```

## Component Structure (Detailed)

### 1. Parsers (`repotoire/parsers/`)

**Purpose**: Extract structured information from source code files.

**Key Components**:
- `base.py`: `CodeParser` abstract base class
- `python_parser.py`: Python AST-based parser (current implementation)
- Future: TypeScript, Java, Go parsers using tree-sitter

**Design Decisions**:
- **Abstract interface**: Allows adding new language parsers without changing core logic
- **Entity extraction**: Converts code into graph-ready entities (files, classes, functions)
- **Relationship extraction**: Identifies connections (imports, calls, inheritance)
- **Qualified names**: Uses unique identifiers (e.g., `mymodule.MyClass.my_method`)

**Extension Point**: Implement `CodeParser` for new languages.

**Example**:
```python
class PythonParser(CodeParser):
    def parse(self, file_path: str) -> ast.Module:
        """Parse Python file into AST."""

    def extract_entities(self, ast_tree: ast.Module) -> List[Entity]:
        """Extract entities from AST."""

    def extract_relationships(self, ast_tree: ast.Module) -> List[Relationship]:
        """Extract relationships from AST."""
```

### 2. Graph Layer (`repotoire/graph/`)

**Purpose**: Manage Neo4j database connection and operations.

**Key Components**:
- `client.py`: `Neo4jClient` - connection, queries, batch operations
- `schema.py`: `GraphSchema` - constraints, indexes, initialization

**Design Decisions**:
- **Batch operations**: Load 100 entities at a time to optimize memory and performance
- **Retry logic**: Automatic retry with exponential backoff for transient failures
- **Connection pooling**: Neo4j driver manages connection pool automatically
- **Element IDs**: Use `elementId()` for Neo4j 5.0+ compatibility
- **Qualified names as IDs**: Ensures uniqueness and enables direct lookups

**Neo4j Schema**:

```cypher
// Constraints (enforce uniqueness)
CREATE CONSTRAINT file_path_unique IF NOT EXISTS
FOR (f:File) REQUIRE f.filePath IS UNIQUE;

CREATE CONSTRAINT class_qname_unique IF NOT EXISTS
FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE;

CREATE CONSTRAINT function_qname_unique IF NOT EXISTS
FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE;

// Indexes (optimize queries)
CREATE INDEX file_path_idx IF NOT EXISTS FOR (f:File) ON (f.filePath);
CREATE INDEX file_language_idx IF NOT EXISTS FOR (f:File) ON (f.language);
CREATE INDEX class_qname_idx IF NOT EXISTS FOR (c:Class) ON (c.qualifiedName);
CREATE INDEX function_qname_idx IF NOT EXISTS FOR (f:Function) ON (f.qualifiedName);

// Full-text search
CREATE FULLTEXT INDEX docstring_search IF NOT EXISTS
FOR (n:Class|Function) ON EACH [n.docstring];
```

**Retry Configuration**:
- Default: 3 retries with 2x exponential backoff, 1s base delay
- Configurable via config file or environment variables
- Only retries transient errors (connection failures, session expired)

### 3. Pipeline (`repotoire/pipeline/`)

**Purpose**: Orchestrate the complete ingestion process.

**Key Components**:
- `ingestion.py`: `IngestionPipeline` - main orchestration logic

**Design Decisions**:
- **Security first**: Validates all paths, rejects symlinks by default, enforces file size limits
- **Progressive processing**: Scans → Parses → Batches → Loads
- **Error resilience**: Continues processing even if individual files fail
- **Progress tracking**: Optional callback for UI progress updates
- **Relative paths**: Stores relative paths to avoid exposing system structure

**Security Features**:
- Path boundary validation (prevents directory traversal)
- Symlink rejection (configurable, disabled by default)
- File size limits (default 10MB, configurable)
- Skipped files reporting with reasons

**Batch Processing Flow**:
```
1. Scan repository (glob patterns)
2. Filter (security, size, symlinks)
3. Parse each file → entities + relationships
4. Accumulate until batch size reached (100)
5. Batch create nodes in Neo4j
6. Batch create relationships
7. Repeat until all files processed
```

### 4. Detectors (`repotoire/detectors/`)

**Purpose**: Analyze graph to detect code smells and issues.

**Key Components**:
- `base.py`: `CodeSmellDetector` abstract base class
- `circular_dependencies.py`: Detect import cycles using Tarjan's algorithm
- `god_class.py`: Detect classes with too many responsibilities
- `dead_code.py`: Identify unused functions and classes
- `engine.py`: `AnalysisEngine` - orchestrates all detectors

**Design Decisions**:
- **Graph-based detection**: Leverages Neo4j's Cypher for pattern matching
- **Severity levels**: CRITICAL, HIGH, MEDIUM, LOW, INFO
- **Configurable thresholds**: All detector thresholds in config file
- **Suggested fixes**: Each finding includes actionable fix suggestions

**Detector Template**:
```python
class CircularDependencyDetector(CodeSmellDetector):
    def detect(self, db: Neo4jClient) -> List[Finding]:
        """Detect circular dependencies using Cypher."""
        query = """
        MATCH cycle = (m1:Module)-[:IMPORTS*]->(m2:Module)-[:IMPORTS*]->(m1)
        WHERE m1.qualifiedName < m2.qualifiedName
        RETURN cycle
        """
        results = db.execute_query(query)
        return [self._create_finding(result) for result in results]
```

### 5. Models (`repotoire/models.py`)

**Purpose**: Define data structures for the entire system.

**Key Components**:
- `Entity` hierarchy: FileEntity, ClassEntity, FunctionEntity, etc.
- `Relationship`: Connections between entities
- `Finding`: Code smell detection result
- `CodebaseHealth`: Overall health report
- `MetricsBreakdown`: Detailed metrics
- `Severity`: Enum for finding severity levels

**Design Decisions**:
- **Dataclasses**: Simple, type-safe, no boilerplate
- **Pydantic validation**: Runtime type checking when needed
- **Enums for types**: NodeType, RelationshipType, Severity for type safety
- **Immutable where possible**: Prevents accidental modifications

### 6. CLI (`repotoire/cli.py`)

**Purpose**: Command-line interface for user interaction.

**Commands**:
1. **`repotoire ingest`**: Load codebase into graph
2. **`repotoire analyze`**: Run detectors and generate report
3. **`repotoire validate`**: Validate configuration
4. **`repotoire config`**: Generate config templates

**Design Decisions**:
- **Click framework**: Industry standard, easy to extend
- **Rich library**: Beautiful terminal output with colors, tables, trees
- **Progress bars**: Visual feedback for long operations
- **Error handling**: Helpful error messages with suggestions
- **Configuration priority**: CLI args > env vars > config file > defaults

**Output Enhancements** (FAL-60):
- Severity color coding (red, orange, yellow, blue, cyan)
- Emoji indicators for quick scanning
- Tree view for findings grouped by detector
- Progress bars with color gradients
- Status assessments (Excellent/Good/Poor)

### 7. Reporters (`repotoire/reporters/`)

**Purpose**: Generate analysis reports in various formats.

**Key Components**:
- `html_reporter.py`: Generate HTML reports with code snippets

**Design Decisions**:
- **Jinja2 templates**: Flexible, maintainable HTML generation
- **Code extraction**: Shows actual source code where issues occur
- **Syntax highlighting**: Monospace font, line numbers, highlighted problem lines
- **Responsive design**: Works on mobile, print-friendly
- **Static files**: No server needed, easy to share

**HTML Report Features** (FAL-62):
- Gradient header with professional styling
- Code snippets with 5 lines context before/after
- Highlighted problem lines in red
- Severity badges with emojis
- Multi-language syntax support
- Progress bars for metrics

### 8. Configuration (`repotoire/config.py`)

**Purpose**: Manage all configuration options.

**Configuration Sources** (priority order):
1. Command-line arguments
2. Environment variables (`REPOTOIRE_*`)
3. Config file (`.repotoirerc`, `repotoire.toml`)
4. Built-in defaults

**Design Decisions**:
- **Multiple formats**: YAML, JSON, TOML support
- **Hierarchical search**: Checks current dir, parents, home dir
- **Environment interpolation**: `${VAR_NAME}` syntax for secrets
- **Validation**: All config values validated with helpful error messages

**Security Best Practices**:
- Never commit passwords to version control
- Use environment variables for secrets
- Validate all paths and inputs
- Disable symlinks by default

### 9. Validation (`repotoire/validation.py`)

**Purpose**: Validate all user inputs with helpful error messages.

**Validation Functions**:
- `validate_repository_path()`: Checks path exists, readable, not empty
- `validate_neo4j_uri()`: Validates URI format and scheme
- `validate_neo4j_credentials()`: Checks username and password
- `validate_neo4j_connection()`: Tests actual connectivity
- `validate_output_path()`: Checks output directory is writable
- `validate_file_size_limit()`: Ensures reasonable limits
- `validate_batch_size()`: Validates batch size range
- `validate_retry_config()`: Checks retry parameters

**Design Decisions**:
- **Helpful messages**: Every error includes suggestion for fix
- **Early validation**: Fail fast before starting expensive operations
- **Custom exception**: `ValidationError` with message + suggestion fields

## Design Decisions & Rationale

### Why Neo4j?

**Decision**: Use Neo4j as the primary data store instead of relational database or in-memory graph.

**Rationale**:
1. **Native graph storage**: Optimized for traversals and pattern matching
2. **Cypher query language**: Expressive for complex patterns
3. **Graph algorithms**: Built-in GDS library for modularity, centrality, etc.
4. **Scalability**: Handles codebases with millions of nodes
5. **ACID transactions**: Reliable data consistency

**Trade-offs**:
- Requires external service (Docker recommended)
- Learning curve for Cypher queries
- Memory requirements for large graphs

### Why Batch Processing?

**Decision**: Load entities in batches of 100 instead of one-by-one or all-at-once.

**Rationale**:
1. **Memory efficiency**: Prevents loading entire codebase into memory
2. **Network optimization**: Reduces round-trips to Neo4j
3. **Progress tracking**: Natural checkpoint boundaries
4. **Error recovery**: Can resume from last successful batch

**Trade-offs**:
- More complex logic than naive approach
- Need to manage batch state
- Optimal batch size depends on codebase

### Why Qualified Names as Primary Keys?

**Decision**: Use qualified names (e.g., `module.Class.method`) as node identifiers.

**Rationale**:
1. **Human readable**: Easy to understand in queries and debugging
2. **Globally unique**: No collisions across codebase
3. **Direct lookups**: `MATCH (f:Function {qualifiedName: $name})` is fast
4. **Hierarchical**: Encodes structure in the name

**Trade-offs**:
- Refactoring changes IDs (need to handle updates)
- Long names for deeply nested code
- Language-specific naming conventions

### Why Three-Category Health Scoring?

**Decision**: Score Structure (40%), Quality (30%), Architecture (30%).

**Rationale**:
1. **Holistic view**: Covers different aspects of code health
2. **Weighted importance**: Structure matters most for maintainability
3. **Actionable**: Each category maps to specific improvements
4. **Industry standard**: Aligns with software engineering best practices

**Trade-offs**:
- Weights are somewhat arbitrary
- Different codebases may need different weights
- Single number may oversimplify

### Why Security-First Ingestion?

**Decision**: Validate paths, reject symlinks by default, enforce size limits.

**Rationale**:
1. **Prevent attacks**: Directory traversal, symlink exploits
2. **Resource protection**: Prevent DoS via huge files
3. **Predictable behavior**: Clear boundaries for analysis
4. **Audit trail**: Log all skipped files with reasons

**Trade-offs**:
- May need to enable symlinks for some projects
- Additional validation overhead
- More complex configuration

## RAG (Retrieval-Augmented Generation) Workflow

Repotoire includes a complete RAG system for natural language code intelligence, enabling developers to query codebases using plain English.

### Architecture Overview

```
┌──────────────┐
│ User Query   │  "How does authentication work?"
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  1. Query Embedding (OpenAI)                 │
│     text-embedding-3-small                   │
│     → 1536-dimensional vector                │
└──────────────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  2. Hybrid Retrieval                         │
│     • Vector similarity search (Neo4j)       │
│     • Graph traversal (relationships)        │
│     • Ranked by relevance                    │
└──────────────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  3. Context Assembly                         │
│     • Top-K code entities                    │
│     • Related entities (IMPORTS, CALLS)      │
│     • Code + docstrings + metadata           │
└──────────────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  4. LLM Generation (GPT-4o)                  │
│     • Context + query → answer               │
│     • Source citations                       │
│     • Confidence scoring                     │
└──────────────┬───────────────────────────────┘
       │
       ▼
┌──────────────┐
│   Answer     │  "The authentication system uses JWT..."
└──────────────┘
```

### Key Components

**1. CodeEmbedder** (`repotoire/ai/embeddings.py`)
- Generates semantic embeddings for code entities
- Enriches entities with context (docstrings, signatures, characteristics)
- Batch processing for efficiency
- Uses OpenAI text-embedding-3-small (1536 dimensions)

**2. GraphRAGRetriever** (`repotoire/ai/retrieval.py`)
- Hybrid search: vector similarity + graph traversal
- Entity filtering (Function, Class, File)
- Relationship traversal for context
- Ranked results with confidence scores

**3. FastAPI Endpoints** (`repotoire/api/`)
- `POST /api/v1/code/search`: Semantic code search
- `POST /api/v1/code/ask`: Q&A with GPT-4o
- `GET /api/v1/code/embeddings/status`: Check coverage

**4. Vector Indexes** (`repotoire/graph/schema.py`)
- Neo4j vector indexes for fast similarity search
- Cosine similarity for semantic matching
- Indexes on Function, Class, and File entities

### Usage

**Enable RAG during ingestion:**

```bash
# Set OpenAI API key
export OPENAI_API_KEY="sk-..."

# Ingest with embedding generation
repotoire ingest /path/to/repo --generate-embeddings

# Or use Python API
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.graph import Neo4jClient

client = Neo4jClient(uri="bolt://localhost:7688", password="password")
pipeline = IngestionPipeline(
    repo_path="/path/to/repo",
    neo4j_client=client,
    generate_embeddings=True  # Enable RAG
)
pipeline.ingest()
```

**Query via API:**

```bash
# Start API server
python -m repotoire.api.app

# Search for code
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication functions",
    "top_k": 5
  }'

# Ask questions
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How does authentication work?",
    "top_k": 5
  }'
```

**Use Python client:**

```python
from repotoire.ai import CodeEmbedder
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.graph import Neo4jClient

# Initialize
client = Neo4jClient(uri="bolt://localhost:7688", password="password")
embedder = CodeEmbedder()
retriever = GraphRAGRetriever(neo4j_client=client, embedder=embedder)

# Search
results = retriever.retrieve(
    query="How does authentication work?",
    top_k=5,
    include_related=True
)

for result in results:
    print(f"{result.qualified_name} (score: {result.similarity_score:.2f})")
    print(f"  {result.file_path}:{result.line_start}")
```

### Performance Characteristics

**Embedding Generation:**
- Time: ~10-20 entities/second (API rate limits)
- Cost: ~$0.13 per 1M tokens (~200k LOC)
- Storage: 1536 floats × 8 bytes = ~12KB per entity
- One-time cost during ingestion

**Query Performance:**
- Vector search: < 100ms (with indexes)
- Hybrid search: < 500ms (includes graph traversal)
- GPT-4o generation: ~1-2 seconds
- Total: < 2 seconds for typical queries

**Scalability:**
- Small codebase (<1k files): < 1 minute for embeddings
- Medium codebase (1k-10k files): 5-15 minutes
- Large codebase (10k+ files): 30-60 minutes
- Embeddings cached in Neo4j, no regeneration needed

### Cost Analysis

**OpenAI API Costs:**

| Component | Model | Cost | Usage |
|-----------|-------|------|-------|
| Embeddings | text-embedding-3-small | $0.13/1M tokens | One-time per entity |
| Search | (none) | $0 | Uses cached embeddings |
| Q&A | GPT-4o | ~$0.0075/query | Context + generation |

**Example Costs:**
- 1,000 files (~500k tokens): ~$0.065
- 10,000 files (~5M tokens): ~$0.65
- 100,000 files (~50M tokens): ~$6.50

**Recommendations:**
- Generate embeddings once during setup
- Use search endpoint (free) when possible
- Reserve Q&A for when explanations needed
- Embeddings are cached, no ongoing cost

### Design Decisions

**Why text-embedding-3-small?**
- Cost-effective ($0.13/1M vs $0.13/1M for ada-002)
- Fast (< 100ms per request)
- Good quality for code (1536 dimensions)
- Industry standard for RAG

**Why hybrid search?**
- Vector search alone misses relationships
- Graph traversal adds context (imports, calls)
- Combined approach = best of both
- Configurable: can disable for speed

**Why Neo4j vector indexes?**
- Native vector support in Neo4j 5.18+
- Fast cosine similarity search
- No separate vector database needed
- Unified storage for graph + vectors

**Why GPT-4o for Q&A?**
- Best code understanding
- Follows instructions well
- Handles technical context
- Good balance of cost/quality

### Example Queries

See [docs/RAG_API.md](docs/RAG_API.md) for comprehensive examples:

- **Understanding**: "How does the parser work?"
- **Finding**: "Where is JWT token generation?"
- **Refactoring**: "What would break if I change User class?"
- **Architecture**: "What design patterns are used?"

### Troubleshooting

**No results returned:**
- Check embedding coverage: `GET /api/v1/code/embeddings/status`
- Verify vector indexes exist
- Try broader queries

**Vector index errors:**
```python
from repotoire.graph.schema import GraphSchema
schema = GraphSchema(client)
schema.create_vector_indexes()
```

**Slow queries:**
- Reduce `top_k` parameter
- Disable `include_related` for speed
- Check Neo4j memory settings

For detailed troubleshooting, see [docs/RAG_API.md](docs/RAG_API.md#troubleshooting).

## Extension Points

### Adding a New Language Parser

**Steps**:
1. Create new file in `repotoire/parsers/` (e.g., `typescript_parser.py`)
2. Inherit from `CodeParser` base class
3. Implement required methods:
   - `parse(file_path)` → AST or parse tree
   - `extract_entities(parse_tree)` → List[Entity]
   - `extract_relationships(parse_tree)` → List[Relationship]
4. Register parser in `IngestionPipeline.__init__()`
5. Add file extension mapping in `_detect_language()`
6. Add tests in `tests/unit/parsers/`

**Example**:
```python
from repotoire.parsers.base import CodeParser
from repotoire.models import Entity, Relationship

class TypeScriptParser(CodeParser):
    def parse(self, file_path: str):
        import tree_sitter_typescript as ts
        # Implementation here

    def extract_entities(self, tree) -> List[Entity]:
        # Walk tree and create entities

    def extract_relationships(self, tree) -> List[Relationship]:
        # Identify imports, calls, etc.
```

### Adding a New Detector

**Steps**:
1. Create new file in `repotoire/detectors/` (e.g., `feature_envy.py`)
2. Inherit from `CodeSmellDetector` base class
3. Implement `detect(db: Neo4jClient) → List[Finding]`
4. Write Cypher query to find pattern
5. Create Finding objects with severity and suggestions
6. Register in `AnalysisEngine.detectors` list
7. Add configuration thresholds to config schema
8. Add tests in `tests/unit/detectors/`

**Example**:
```python
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity

class FeatureEnvyDetector(CodeSmellDetector):
    def detect(self, db: Neo4jClient) -> List[Finding]:
        query = """
        MATCH (f:Function)-[r:USES]->(c:Class)
        WHERE f.parent <> c.qualifiedName
        WITH f, count(r) as external_uses
        WHERE external_uses > 5
        RETURN f.qualifiedName as function, external_uses
        """
        results = db.execute_query(query)

        findings = []
        for result in results:
            finding = Finding(
                id=f"feature_envy_{result['function']}",
                detector="FeatureEnvyDetector",
                severity=Severity.MEDIUM,
                title=f"Feature envy in {result['function']}",
                description=f"Function uses {result['external_uses']} methods from other classes",
                affected_nodes=[result['function']],
                affected_files=[self._get_file(result['function'])],
                suggested_fix="Consider moving this method to the class it uses most"
            )
            findings.append(finding)

        return findings
```

### Adding a Hybrid Detector (External Tool + Graph)

**Hybrid detectors** combine external linting tools with graph enrichment for optimal accuracy and context.

**Architecture**:
```
┌──────────────┐
│External Tool │ ──> Accurate Detection (e.g., ruff, pylint, mypy)
└──────────────┘
       │
       ▼
┌──────────────┐
│  Parse JSON  │ ──> Extract findings with metadata
└──────────────┘
       │
       ▼
┌──────────────┐
│ Graph Query  │ ──> Enrich with file context (LOC, complexity)
└──────────────┘
       │
       ▼
┌──────────────┐
│   Finding    │ ──> Unified reporting with severity & fixes
└──────────────┘
```

**Example: RuffImportDetector**

**Step 1**: Create detector file in `repotoire/detectors/`

```python
"""Ruff-based unused import detector with graph enrichment."""
import subprocess
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph.client import Neo4jClient

class RuffImportDetector(CodeSmellDetector):
    """Detects unused imports using ruff with graph-based enrichment."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.repository_path = config.get("repository_path", ".")

    def detect(self) -> List[Finding]:
        """Run ruff F401 check and enrich with graph context."""
        # 1. Run external tool
        ruff_findings = self._run_ruff()

        if not ruff_findings:
            return []

        # 2. Group by file
        findings_by_file: Dict[str, List[Dict]] = {}
        for ruff_finding in ruff_findings:
            file_path = ruff_finding["filename"]
            if file_path not in findings_by_file:
                findings_by_file[file_path] = []
            findings_by_file[file_path].append(ruff_finding)

        # 3. Enrich with graph context
        findings = []
        for file_path, file_findings in findings_by_file.items():
            graph_context = self._get_file_context(file_path)

            # Build finding with combined context
            finding = Finding(
                id=f"ruff_imports_{file_path.replace('/', '_')}",
                detector=self.__class__.__name__,
                severity=Severity.MEDIUM if len(file_findings) >= 5 else Severity.LOW,
                title=f"Unused imports in {Path(file_path).name}",
                description=f"Found {len(file_findings)} unused imports",
                affected_files=[file_path],
                suggested_fix=f"Run: ruff check --select F401 --fix {file_path}",
                graph_context={
                    "tool": "ruff",
                    "rule": "F401",
                    "import_count": len(file_findings),
                    "file_loc": graph_context.get("loc") if graph_context else None,
                }
            )
            findings.append(finding)

        return findings

    def _run_ruff(self) -> List[Dict[str, Any]]:
        """Execute ruff and parse JSON output."""
        result = subprocess.run(
            ["ruff", "check", "--select", "F401", "--output-format", "json",
             str(self.repository_path)],
            capture_output=True, text=True, check=False
        )

        if result.stdout:
            findings = json.loads(result.stdout)
            return [f for f in findings if f["code"] == "F401"]
        return []

    def _get_file_context(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Query graph for file metadata."""
        query = """
        MATCH (f:File {filePath: $file_path})
        RETURN f.loc as loc, f.complexity as complexity
        LIMIT 1
        """
        results = self.db.execute_query(query, {"file_path": file_path})
        return results[0] if results else None
```

**Step 2**: Update `AnalysisEngine` to support repository_path

```python
class AnalysisEngine:
    def __init__(self, neo4j_client: Neo4jClient, detector_config: Dict = None,
                 repository_path: str = "."):
        """Initialize with repository path for hybrid detectors."""
        self.repository_path = repository_path

        self.detectors = [
            # ... other detectors ...
            # Hybrid detectors
            RuffImportDetector(neo4j_client, detector_config={
                "repository_path": repository_path
            }),
        ]
```

**Step 3**: Register in `__init__.py`

```python
from repotoire.detectors.ruff_import_detector import RuffImportDetector

__all__ = [
    # ... other exports ...
    "RuffImportDetector",
]
```

**Benefits of Hybrid Approach**:
- **Accuracy**: External tools like ruff have 0% false positive rate using AST
- **Context**: Graph enrichment adds file LOC, complexity, relationships
- **Actionability**: Auto-fix suggestions from external tool
- **Consistency**: Same Finding model as pure graph detectors
- **Performance**: External tools are often faster than pure graph queries

**Complete Hybrid Detector Suite** (Implemented):

The following hybrid detectors are fully implemented and optimized for production use:

| Detector | Tool | Purpose | Coverage | Performance |
|----------|------|---------|----------|-------------|
| **RuffLintDetector** | ruff | General linting | 90% of Pylint (400+ rules) | ~1s |
| **PylintDetector** | pylint | Specialized checks | 10% unique rules (11 checks) | ~1min (22 cores) |
| **MypyDetector** | mypy | Static type checking | Type violations | ~10s |
| **BanditDetector** | bandit | Basic security | Common vulnerabilities | ~5s |
| **RadonDetector** | radon | Complexity metrics | CC + MI analysis | ~5s |
| **JscpdDetector** | jscpd | Duplicate code | Token-based detection | ~5-10s |
| **VultureDetector** | vulture | Dead code detection | Unused code (AST) | ~2-5s |
| **SemgrepDetector** | semgrep | Advanced security | OWASP Top 10 + patterns | ~5-15s |

**Total Analysis Time**: ~3-4 minutes for comprehensive coverage

**Example: VultureDetector** (Accurate Dead Code Detection)

```python
"""Vulture-based unused code detector with Neo4j graph enrichment."""
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph.client import Neo4jClient

class VultureDetector(CodeSmellDetector):
    """Detects unused code using vulture with graph enrichment.

    Vulture provides AST-based unused code detection with configurable
    confidence thresholds. More accurate than graph-based approaches as
    it understands Python semantics (decorators, magic methods, etc.).

    Configuration:
        repository_path: Path to repository root (required)
        min_confidence: Minimum confidence level (0-100, default: 80)
        max_findings: Maximum findings to report (default: 100)
        exclude: List of patterns to exclude (default: tests, migrations)
    """

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.min_confidence = config.get("min_confidence", 80)
        self.max_findings = config.get("max_findings", 100)
        self.exclude = config.get("exclude", [
            "tests/", "test_*.py", "*_test.py",
            "migrations/", "scripts/", "setup.py", "conftest.py"
        ])

    def detect(self) -> List[Finding]:
        """Run vulture and enrich findings with graph data."""
        # 1. Run external tool
        vulture_findings = self._run_vulture()

        # 2. Group by file
        findings_by_file = self._group_by_file(vulture_findings)

        # 3. Enrich with graph context
        findings = []
        for file_path, file_findings in findings_by_file.items():
            graph_context = self._get_file_context(file_path)

            for vf in file_findings:
                finding = self._create_finding(vf, graph_context)
                findings.append(finding)

        return findings

    def _run_vulture(self) -> List[Dict[str, Any]]:
        """Execute vulture and parse output."""
        cmd = ["vulture", str(self.repository_path),
               f"--min-confidence={self.min_confidence}"]

        for pattern in self.exclude:
            cmd.extend(["--exclude", pattern])

        result = subprocess.run(cmd, capture_output=True, text=True)

        # Parse vulture output format:
        # <file>:<line>: unused <type> '<name>' (confidence%)
        return self._parse_vulture_output(result.stdout)

    def _get_file_context(self, file_path: str) -> Dict[str, Any]:
        """Query Neo4j for file metadata."""
        query = """
        MATCH (file:File {filePath: $file_path})
        RETURN file.loc as file_loc
        LIMIT 1
        """
        results = self.db.execute_query(query, {"file_path": file_path})
        return results[0] if results else {"file_loc": 0}
```

**Key Features**:
- **High accuracy**: 80%+ confidence threshold filters false positives
- **Semantic understanding**: Handles decorators, magic methods, dynamic usage
- **Fast execution**: ~2-5 seconds even on large codebases
- **Graph enrichment**: Adds file LOC, complexity context
- **Smart filtering**: Excludes tests, migrations by default

**Example: SemgrepDetector** (Advanced Security Scanning)

```python
"""Semgrep-based advanced security detector with Neo4j graph enrichment."""
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph.client import Neo4jClient

class SemgrepDetector(CodeSmellDetector):
    """Detects security vulnerabilities using Semgrep with graph enrichment.

    Semgrep provides semantic pattern matching for security issues beyond
    basic static analysis. Supports OWASP Top 10, CWE mapping, and custom
    rules with low false positive rate.

    Configuration:
        repository_path: Path to repository root (required)
        config: Semgrep ruleset (default: "auto" - OWASP + lang-specific)
        max_findings: Maximum findings to report (default: 50)
        severity_threshold: Minimum severity (ERROR, WARNING, INFO)
        exclude: List of patterns to exclude (default: tests, migrations)
    """

    SEVERITY_MAP = {
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "INFO": Severity.LOW,
    }

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.config = config.get("config", "auto")  # "auto", "p/security-audit", "p/owasp-top-ten"
        self.max_findings = config.get("max_findings", 50)
        self.severity_threshold = config.get("severity_threshold", "INFO")
        self.exclude = config.get("exclude", [
            "tests/", "test_*.py", "*_test.py", "migrations/",
            ".venv/", "venv/", "node_modules/", "__pycache__/"
        ])

    def detect(self) -> List[Finding]:
        """Run Semgrep and enrich findings with graph data."""
        # 1. Run external tool
        semgrep_findings = self._run_semgrep()

        # 2. Group by file
        findings_by_file = self._group_by_file(semgrep_findings)

        # 3. Enrich with graph context
        findings = []
        for file_path, file_findings in findings_by_file.items():
            graph_context = self._get_file_context(file_path)

            for sf in file_findings:
                finding = self._create_finding(sf, graph_context)
                findings.append(finding)

        return findings

    def _run_semgrep(self) -> List[Dict[str, Any]]:
        """Execute Semgrep and parse JSON output."""
        cmd = [
            "semgrep", "scan", "--json", "--quiet",
            f"--config={self.config}"
        ]

        for pattern in self.exclude:
            cmd.extend(["--exclude", pattern])

        cmd.append(str(self.repository_path))

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.stdout:
            output = json.loads(result.stdout)
            results = output.get("results", [])

            # Filter by severity threshold
            return [
                r for r in results
                if self._meets_severity_threshold(r.get("extra", {}).get("severity", "INFO"))
            ]

        return []

    def _create_finding(self, semgrep_finding: Dict, graph_context: Dict) -> Finding:
        """Create enriched finding from Semgrep result."""
        metadata = semgrep_finding.get("extra", {}).get("metadata", {})
        severity_str = semgrep_finding.get("extra", {}).get("severity", "INFO")

        description = f"{semgrep_finding.get('extra', {}).get('message', '')}\n\n"

        # Add CWE/OWASP metadata
        if metadata.get("cwe"):
            description += f"**CWE**: {', '.join(metadata['cwe'])}\n"
        if metadata.get("owasp"):
            description += f"**OWASP**: {', '.join(metadata['owasp'])}\n"
        if graph_context.get("file_loc"):
            description += f"**File Size**: {graph_context['file_loc']} LOC\n"

        return Finding(
            id=str(uuid.uuid4()),
            detector="SemgrepDetector",
            severity=self.SEVERITY_MAP.get(severity_str, Severity.LOW),
            title=f"Security issue: {semgrep_finding.get('check_id', '').split('.')[-1]}",
            description=description,
            affected_files=[semgrep_finding.get("path", "")],
            graph_context={
                "tool": "semgrep",
                "check_id": semgrep_finding.get("check_id", ""),
                "cwe": metadata.get("cwe", []),
                "owasp": metadata.get("owasp", []),
                "file_loc": graph_context.get("file_loc", 0),
            },
            suggested_fix=self._suggest_fix(metadata, semgrep_finding.get("extra", {}).get("message", ""))
        )
```

**Key Features**:
- **Semantic analysis**: Pattern-based detection (not just regex)
- **OWASP coverage**: Maps to OWASP Top 10 and CWE standards
- **Low false positives**: Context-aware pattern matching
- **Custom rules**: Support for organization-specific security policies
- **Fast execution**: ~5-15 seconds with parallel scanning

**Performance Benchmarks** (Repotoire Codebase - 86 files, ~12k LOC):

| Phase | Time | Details |
|-------|------|---------|
| **Graph Detectors** | ~1s | Circular deps, god classes, bottlenecks |
| **Fast Hybrid** | ~2s | Ruff (1s) + Jscpd (1s) |
| **Type Checking** | ~10s | Mypy static analysis |
| **Quality Analysis** | ~1-1.5min | Pylint (22 cores, 11 checks) |
| **Security & Complexity** | ~15-20s | Bandit + Radon + Semgrep + Vulture |
| **Total** | ~3-4min | **6x faster than original 12+ minutes** |

**Optimization History**:
- **Before**: 12+ minutes (Pylint single-threaded, R0801 O(n²))
- **After Ruff**: ~2 minutes (Ruff replaces 90% of Pylint)
- **After Parallelization**: ~2 minutes (Pylint 22 cores for 10% checks)
- **After jscpd**: ~2 minutes (Replaced Pylint R0801 O(n²) with O(n))
- **Current**: ~3-4 minutes (Added Vulture + Semgrep for comprehensive coverage)

**Trade-offs**:
- **Accuracy vs Speed**: Hybrid approach achieves both
  - External tools: High accuracy (AST/semantic based)
  - Graph enrichment: Rich context (relationships, metrics)
- **Coverage vs Time**:
  - 100% linting + security + complexity coverage
  - <5 minute total analysis time
  - Acceptable for CI/CD pipelines

### Adding a New Report Format

**Steps**:
1. Create new file in `repotoire/reporters/` (e.g., `pdf_reporter.py`)
2. Implement `generate(health: CodebaseHealth, output_path: Path)`
3. Use appropriate library (e.g., ReportLab for PDF)
4. Add to CLI's format choices in `analyze` command
5. Update documentation

### Customizing HTML Report Template

**Current**: Template embedded in `html_reporter.py` as string constant.

**Future**: External template file for customization.

**Steps to customize**:
1. Find `HTML_TEMPLATE` variable in `repotoire/reporters/html_reporter.py`
2. Modify HTML structure or CSS styling
3. Template uses Jinja2 syntax: `{{ variable }}`, `{% for ... %}`
4. Variables available: `health`, `findings`, `generated_at`, etc.

## Troubleshooting Guide

### Common Issues and Solutions

#### 1. Neo4j Connection Failures

**Symptoms**:
- `Cannot connect to Neo4j`
- `ServiceUnavailable`
- `ConnectionRefusedError`

**Solutions**:
1. Check Neo4j is running: `docker ps | grep neo4j`
2. Verify port 7688 is accessible: `telnet localhost 7688`
3. Check URI uses Bolt protocol: `bolt://` not `http://`
4. Test with `repotoire validate`
5. Check firewall rules
6. Verify credentials: `echo $REPOTOIRE_NEO4J_URI $REPOTOIRE_NEO4J_PASSWORD`
7. Ensure environment variables are set correctly (port 7688, not 7687)

#### 2. Ingestion Performance Issues

**Symptoms**:
- Slow file processing
- High memory usage
- Timeout errors

**Solutions**:
1. Increase batch size: `batch_size: 500` in config
2. Filter out test files: `patterns: ["**/*.py", "!**/tests/**"]`
3. Increase Neo4j heap: `-e NEO4J_server_memory_heap_max__size=4G`
4. Use `--quiet` to disable progress bars
5. Check Neo4j query performance with EXPLAIN

#### 3. Parser Errors

**Symptoms**:
- `Failed to parse file`
- `SyntaxError`
- Files being skipped

**Solutions**:
1. Check Python version compatibility
2. Look for syntax errors in source files
3. Check file encoding (UTF-8 expected)
4. Increase max file size if files are large
5. Review skipped files summary in output

#### 4. Missing Findings

**Symptoms**:
- Expected issues not detected
- Empty or minimal findings

**Solutions**:
1. Verify data was ingested: Check Neo4j Browser
2. Run detectors individually to debug
3. Check detector thresholds in config
4. Verify relationships were created correctly
5. Check Cypher queries in detectors

#### 5. Configuration Not Loading

**Symptoms**:
- Default values used instead of config
- Config file not found

**Solutions**:
1. Check file name: `.repotoirerc` or `repotoire.toml`
2. Check file location: Current dir, parents, or `~/.config/`
3. Validate syntax: Run `repotoire validate`
4. Check file permissions: Must be readable
5. Use `--config` flag for explicit path

#### 6. Import Errors

**Symptoms**:
- `ModuleNotFoundError`
- `ImportError`

**Solutions**:
1. Install all dependencies: `pip install -e ".[dev,config]"`
2. Check Python version >= 3.10
3. Activate virtual environment
4. Clear Python cache: `find . -type d -name __pycache__ -exec rm -r {} +`

#### 7. Memory Issues with Large Codebases

**Symptoms**:
- Out of memory errors
- Neo4j crashes
- System slowdown

**Solutions**:
1. Increase Docker memory limit: `--memory 8g`
2. Reduce batch size: `batch_size: 50`
3. Process in chunks (analyze subdirectories separately)
4. Increase Neo4j heap size
5. Close other applications

## Testing Strategy

### Test Organization

```
tests/
├── unit/               # Unit tests for individual components
│   ├── parsers/        # Parser tests
│   ├── detectors/      # Detector tests
│   ├── graph/          # Graph layer tests
│   └── test_*.py       # Other unit tests
├── integration/        # Integration tests
│   ├── fixtures/       # Test fixtures and sample code
│   └── test_*.py       # End-to-end tests
└── conftest.py         # Pytest configuration
```

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=repotoire --cov-report=html

# Specific test file
pytest tests/unit/test_validation.py

# Specific test
pytest tests/unit/test_validation.py::TestRepositoryPathValidation::test_valid_directory_path

# Watch mode (requires pytest-watch)
ptw

# Parallel execution (requires pytest-xdist)
pytest -n auto
```

### Writing Tests

**Unit Test Example**:
```python
def test_circular_dependency_detection():
    # Arrange
    mock_client = create_mock_neo4j_client()
    detector = CircularDependencyDetector()

    # Act
    findings = detector.detect(mock_client)

    # Assert
    assert len(findings) == 2
    assert findings[0].severity == Severity.HIGH
```

**Integration Test Example**:
```python
@pytest.fixture
def test_repo(tmp_path):
    # Create test repository structure
    (tmp_path / "module_a.py").write_text("import module_b")
    (tmp_path / "module_b.py").write_text("import module_a")
    return tmp_path

def test_end_to_end_analysis(test_repo, neo4j_client):
    # Ingest
    pipeline = IngestionPipeline(test_repo, neo4j_client)
    pipeline.ingest()

    # Analyze
    engine = AnalysisEngine(neo4j_client)
    health = engine.analyze()

    # Assert
    assert health.grade in ["A", "B", "C", "D", "F"]
    assert len(health.findings) > 0
```

## Current Status

### Completed Features ✅
- Core architecture and models
- Neo4j client with retry logic
- Ingestion pipeline with security
- CLI interface with Rich formatting
- Health scoring framework
- Configuration management (YAML, JSON, TOML)
- Input validation with helpful errors
- Progress bars for long operations
- HTML report generation with code snippets
- Comprehensive documentation

### In Progress 🚧
- Python parser (partial implementation)
- Additional detectors (god class, dead code, etc.)
- AI layer integration (concept extraction, fix suggestions)

### Planned Features 📋
- Multi-language support (TypeScript, Java, Go)
- Incremental analysis
- Web dashboard
- IDE plugins
- GitHub Actions integration
- Custom rule engine
- Team analytics

## Dependencies

### Core
- **neo4j** (>=5.14.0): Graph database driver
- **click** (>=8.1.0): CLI framework
- **rich** (>=13.0.0): Terminal formatting
- **pydantic** (>=2.0.0): Data validation
- **networkx** (>=3.2.0): Graph algorithms
- **jinja2** (>=3.1.0): HTML template engine

### AI/NLP
- **spacy** (>=3.7.0): Natural language processing
- **openai** (>=1.0.0): GPT-4o and embeddings

### Configuration
- **pyyaml** (>=6.0): YAML config file support
- **tomli** (>=2.0.0): TOML support (Python <3.11)

### Optional
- **graphdatascience** (>=1.9.0): Neo4j GDS for advanced algorithms
- **tree-sitter** (>=0.20.0): Multi-language parsing

### Development
- **pytest** (>=7.4.0): Testing framework
- **pytest-cov** (>=4.1.0): Coverage reporting
- **black** (>=23.0.0): Code formatting
- **ruff** (>=0.1.0): Linting
- **mypy** (>=1.7.0): Type checking

## Performance Considerations

### Memory Usage
- **Batch size**: Larger batches (500) = faster but more memory
- **Neo4j heap**: Default 512MB, increase for large codebases
- **Python process**: ~100-500MB depending on batch size

### Query Optimization
- **Use indexes**: All qualified names and file paths indexed
- **Limit results**: Use `LIMIT` in Cypher queries where appropriate
- **Profile queries**: Use `EXPLAIN` to identify slow queries

### Scalability Limits
- **Small projects** (<1k files): Sub-second analysis
- **Medium projects** (1k-10k files): 10-60 seconds
- **Large projects** (10k-100k files): 1-10 minutes
- **Very large** (>100k files): May need chunking or incremental analysis

### Neo4j Connection Pool Configuration

The Neo4jClient supports advanced connection pooling for production deployments. Configure via environment variables or constructor parameters:

#### Environment Variables

```bash
# Connection pool size (default: 50)
NEO4J_MAX_POOL_SIZE=50

# Connection acquisition timeout in seconds (default: 30.0)
NEO4J_CONNECTION_TIMEOUT=30.0

# Maximum connection lifetime in seconds (default: 3600)
NEO4J_MAX_CONNECTION_LIFETIME=3600

# Query timeout in seconds (default: 60.0)
NEO4J_QUERY_TIMEOUT=60.0

# Enable TLS encryption (default: false for local dev)
NEO4J_ENCRYPTED=false
```

#### Configuration Guidelines by Environment

**MVP / Development (single user)**
```python
client = Neo4jClient(
    max_connection_pool_size=20,      # Small pool for single user
    query_timeout=60.0,                # Generous timeout for debugging
    connection_timeout=30.0,           # Standard timeout
    encrypted=False                    # Local development
)
```

**v1.0 / Staging (multi-user)**
```python
client = Neo4jClient(
    max_connection_pool_size=100,     # Support concurrent users
    query_timeout=30.0,                # Shorter timeout for responsiveness
    connection_timeout=15.0,           # Fail fast on connection issues
    encrypted=True                     # Enable encryption
)
```

**Production (high availability)**
```python
client = Neo4jClient(
    max_connection_pool_size=200,     # Handle peak load
    query_timeout=15.0,                # Prevent runaway queries
    connection_timeout=10.0,           # Quick failure detection
    max_connection_lifetime=1800,      # 30 min lifetime for load balancing
    encrypted=True,                    # Always encrypted
    max_retries=5,                     # More retries for transient errors
    retry_base_delay=0.5               # Faster retry cadence
)
```

#### Query Timeout Best Practices

1. **Set appropriate defaults**: Use conservative timeouts (60s for dev, 15-30s for prod)
2. **Override for long operations**: Pass custom timeout for known-slow queries
   ```python
   client.execute_query(expensive_query, timeout=120.0)
   ```
3. **Monitor timeout errors**: Log and investigate queries that frequently timeout
4. **Optimize slow queries**: Use `EXPLAIN` to identify bottlenecks

#### Connection Pool Monitoring

Monitor pool health using `get_pool_metrics()`:

```python
metrics = client.get_pool_metrics()
logger.info(f"Pool: {metrics['in_use']}/{metrics['max_size']} connections in use")

# Alert if pool is nearly exhausted
if metrics.get('in_use', 0) > metrics['max_size'] * 0.8:
    logger.warning("Connection pool is 80% utilized")
```

#### Write Transactions

Batch operations automatically use write transactions for atomicity:
- `batch_create_nodes()` - Creates nodes in single transaction
- `batch_create_relationships()` - Creates relationships in single transaction

This ensures:
- **Atomicity**: All-or-nothing commits
- **Consistency**: Transaction boundaries prevent partial updates
- **Performance**: Reduced network round-trips
- **Retry safety**: Transient failures are automatically retried

## Security Considerations

### Input Validation
- All file paths validated before access
- Symlinks rejected by default
- File size limits enforced
- Repository boundary checks

### Credential Management
- Never commit passwords
- Use environment variables: `${NEO4J_PASSWORD}`
- Restrict config file permissions: `chmod 600 .repotoirerc`
- Use secure Neo4j connections in production: `bolt+s://`

### Neo4j Access Control
- Use dedicated Neo4j user for Repotoire
- Limit permissions to necessary operations
- Use authentication always
- Enable encryption for production

## References

- [Neo4j Documentation](https://neo4j.com/docs/)
- [Python AST Documentation](https://docs.python.org/3/library/ast.html)
- [Cypher Query Language](https://neo4j.com/docs/cypher-manual/)
- [Click Framework](https://click.palletsprojects.com/)
- [Rich Terminal Library](https://rich.readthedocs.io/)
- [Tree-sitter](https://tree-sitter.github.io/)

---

**For user-facing documentation**, see [README.md](README.md) and [CONFIG.md](CONFIG.md).
