# Falkor Setup Guide

## Prerequisites

- Python 3.10+
- Neo4j 5.0+ (Docker recommended)
- OpenAI API key (for AI features)

## Quick Start

### 1. Install Neo4j with Docker

```bash
docker run \
    --name falkor-neo4j \
    -p 7474:7474 -p 7687:7687 \
    -d \
    -e NEO4J_AUTH=neo4j/your-password \
    -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
    neo4j:latest
```

Access Neo4j Browser at http://localhost:7474

### 2. Install Falkor

```bash
# Clone repository
git clone https://github.com/yourusername/falkor.git
cd falkor

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Download spaCy model
python -m spacy download en_core_web_lg
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 4. Run Your First Analysis

```bash
# Ingest a codebase
falkor ingest /path/to/your/repo

# Analyze health
falkor analyze /path/to/your/repo
```

## Development Setup

### Install with all dependencies

```bash
pip install -e ".[dev,gds,all-languages]"
```

### Run Tests

```bash
pytest
```

### Code Quality

```bash
# Format code
black falkor tests

# Lint
ruff check falkor tests

# Type check
mypy falkor
```

## Architecture

Falkor consists of several key components:

1. **Parsers** (`falkor/parsers/`) - Extract entities from source code
2. **Graph** (`falkor/graph/`) - Neo4j client and schema management
3. **Pipeline** (`falkor/pipeline/`) - Ingestion orchestration
4. **Detectors** (`falkor/detectors/`) - Code smell detection algorithms
5. **AI** (`falkor/ai/`) - Semantic analysis and fix generation
6. **CLI** (`falkor/cli.py`) - Command-line interface

## Extending Falkor

### Adding a New Language Parser

1. Create a new parser class inheriting from `CodeParser`
2. Implement `parse()`, `extract_entities()`, and `extract_relationships()`
3. Register the parser in the pipeline

Example:

```python
from falkor.parsers import CodeParser

class TypeScriptParser(CodeParser):
    def parse(self, file_path: str):
        # Use tree-sitter or typescript parser
        pass

    def extract_entities(self, ast, file_path):
        # Extract classes, functions, etc.
        pass

    def extract_relationships(self, ast, file_path, entities):
        # Extract imports, calls, etc.
        pass
```

### Adding a New Detector

1. Create a class inheriting from `CodeSmellDetector`
2. Implement `detect()` and `severity()` methods
3. Register in `AnalysisEngine`

Example:

```python
from falkor.detectors.base import CodeSmellDetector

class LongMethodDetector(CodeSmellDetector):
    def detect(self):
        query = '''
        MATCH (f:Function)
        WHERE f.lineEnd - f.lineStart > 50
        RETURN f
        '''
        # Process results and create findings
        pass

    def severity(self, finding):
        # Calculate severity
        pass
```

## Troubleshooting

### Neo4j Connection Issues

- Ensure Neo4j is running: `docker ps`
- Check connection URI in `.env`
- Verify credentials

### Parser Errors

- Check file encoding (should be UTF-8)
- Ensure syntax is valid
- Check parser logs for details

### Performance Tips

- Use incremental ingestion for large repos
- Batch operations where possible
- Create appropriate indexes
- Use Neo4j Graph Data Science for large graphs

## Next Steps

- See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design
- Check [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines
- Read [API.md](API.md) for programmatic usage
