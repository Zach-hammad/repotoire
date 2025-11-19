# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Falkor is a graph-powered code health platform that analyzes codebases using knowledge graphs to detect code smells, architectural issues, and technical debt. Unlike traditional linters that examine files in isolation, Falkor builds a Neo4j knowledge graph combining structural analysis (AST parsing), semantic understanding (NLP + AI), and relational patterns (graph algorithms).

## Development Setup

### Installation

```bash
# Install with development dependencies
pip install -e ".[dev]"

# Install with all optional dependencies (GDS, multi-language support)
pip install -e ".[dev,gds,all-languages]"

# Download spaCy model for NLP features
python -m spacy download en_core_web_lg
```

### Neo4j Setup

Neo4j is required for the graph database. Start with Docker:

```bash
docker run \
    --name falkor-neo4j \
    -p 7474:7474 -p 7687:7687 \
    -d \
    -e NEO4J_AUTH=neo4j/your-password \
    -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
    neo4j:latest
```

Copy `.env.example` to `.env` and configure Neo4j credentials and OpenAI API key.

### Common Commands

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=falkor --cov-report=term-missing

# Format code
black falkor tests

# Lint
ruff check falkor tests

# Type check
mypy falkor

# Ingest a codebase into Neo4j
falkor ingest /path/to/repo --neo4j-password <password>

# Analyze codebase health
falkor analyze /path/to/repo --neo4j-password <password> -o report.json
```

## Architecture

### Core Pipeline Flow

```
Codebase → Parser → Entities/Relationships → Neo4j Graph → Detectors → Health Report
```

### Component Structure

**1. Parsers (`falkor/parsers/`)**
- Extract entities (files, classes, functions) and relationships (imports, calls) from source code
- Base interface: `CodeParser` with `parse()`, `extract_entities()`, `extract_relationships()` methods
- Currently implemented: `PythonParser` (uses Python AST)
- Extensible for multi-language support (TypeScript, Java, Go, etc.)

**2. Graph Layer (`falkor/graph/`)**
- `Neo4jClient`: Database connection, CRUD operations, batching
- `GraphSchema`: Schema initialization with constraints and indexes
- Node types: File, Module, Class, Function, Concept, Import
- Relationship types: IMPORTS, CALLS, USES, CONTAINS
- Uses `elementId()` for Neo4j 5.0+ compatibility

**3. Pipeline (`falkor/pipeline/`)**
- `IngestionPipeline`: Orchestrates scanning, parsing, and loading into Neo4j
- Scans repository with glob patterns (default: `**/*.py`)
- Filters out common ignored directories (.git, __pycache__, node_modules, .venv, build, dist)
- Batches entities (100 at a time) for performance
- Language detection from file extension

**4. Detectors (`falkor/detectors/`)**
- `CodeSmellDetector`: Base interface for detection algorithms
- `AnalysisEngine`: Orchestrates detectors and calculates health scores
- Planned detectors: circular dependencies (Tarjan's), god classes, dead code, tight coupling
- Health scoring: Structure (40%), Quality (30%), Architecture (30%)
- Letter grades A-F based on weighted scores

**5. Models (`falkor/models.py`)**
- Data classes using `@dataclass` and Pydantic
- `Entity` hierarchy: FileEntity, ClassEntity, FunctionEntity
- `Relationship`, `Finding`, `FixSuggestion`, `CodebaseHealth`
- `MetricsBreakdown`: modularity, coupling, circular deps, god classes, dead code, etc.

**6. CLI (`falkor/cli.py`)**
- Click-based interface with Rich output formatting
- Commands: `ingest`, `analyze`
- Interactive password prompts for Neo4j
- Pretty-printed tables and panels for health reports

**7. AI Layer (`falkor/ai/`)**
- Planned: Semantic concept extraction, embeddings, fix suggestions
- Integration points: spaCy for NLP, OpenAI for GPT-4o and embeddings

## Key Implementation Details

### Neo4j Schema

Constraints ensure uniqueness:
- `File.filePath` must be unique
- `Class.qualifiedName` must be unique
- `Function.qualifiedName` must be unique

Indexes for performance:
- File path, language
- Class/Function qualified names
- Full-text search on docstrings

### Batch Processing

The ingestion pipeline batches operations for performance:
- Loads entities every 100 nodes to reduce memory usage
- Groups entities by type for efficient `UNWIND` queries
- Transaction boundaries around batch operations

### Health Scoring Algorithm

Three category scores weighted and combined:
- **Structure (40%)**: modularity, coupling, circular deps, bottlenecks
- **Quality (30%)**: dead code %, duplication %, god class count
- **Architecture (30%)**: layer violations, boundary violations, abstraction ratio (optimal: 0.3-0.7)

Letter grades: A (90-100), B (80-89), C (70-79), D (60-69), F (0-59)

## Extension Points

### Adding a New Language Parser

1. Create class inheriting from `CodeParser` in `falkor/parsers/`
2. Implement `parse()`, `extract_entities()`, `extract_relationships()`
3. Register in `IngestionPipeline.__init__()` with `register_parser()`
4. Add file extension mapping in `_detect_language()`

Example structure:
```python
class TypeScriptParser(CodeParser):
    def parse(self, file_path: str):
        # Use tree-sitter or typescript parser
        pass
```

### Adding a New Detector

1. Create class inheriting from `CodeSmellDetector` in `falkor/detectors/`
2. Implement `detect()` returning `List[Finding]`
3. Implement `severity()` for finding assessment
4. Register in `AnalysisEngine.__init__()` (TODO in current code)

Detectors use Cypher queries to identify patterns in the graph.

## Current Status (MVP)

- ✅ Core architecture and models defined
- ✅ Neo4j client and schema management
- ✅ Ingestion pipeline structure
- ✅ CLI interface with rich output
- ✅ Health scoring framework
- 🚧 Python parser implementation (partial)
- 🚧 Detector implementations (framework ready)
- 🚧 AI layer integration

## Configuration

Environment variables (see `.env.example`):
- `NEO4J_URI`: Database connection (default: bolt://localhost:7687)
- `NEO4J_USER`: Username (default: neo4j)
- `NEO4J_PASSWORD`: Password (required)
- `OPENAI_API_KEY`: For AI features (optional)
- `LOG_LEVEL`: Logging verbosity (default: INFO)

## Testing

Test structure:
- `tests/` directory with `test_*.py` files
- Uses pytest with coverage reporting
- Coverage reports in terminal and HTML format

## Dependencies

Core:
- neo4j (>=5.14.0): Graph database driver
- click (>=8.1.0): CLI framework
- rich (>=13.0.0): Terminal formatting
- pydantic (>=2.0.0): Data validation
- networkx (>=3.2.0): Graph algorithms

AI/NLP:
- spacy (>=3.7.0): Natural language processing
- openai (>=1.0.0): GPT-4o and embeddings

Optional:
- graphdatascience: Neo4j GDS for advanced graph algorithms
- tree-sitter: Multi-language parsing support
