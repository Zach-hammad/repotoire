# Repotoire RAG API Documentation

Complete guide to using Repotoire's RAG (Retrieval-Augmented Generation) API for natural language code intelligence.

## Table of Contents

- [Quick Start](#quick-start)
- [API Endpoints](#api-endpoints)
- [Usage Examples](#usage-examples)
- [Example Queries](#example-queries)
- [Python Client](#python-client)
- [Troubleshooting](#troubleshooting)
- [Performance Tips](#performance-tips)

## Quick Start

### 1. Start the API Server

```bash
# Set required environment variables
export OPENAI_API_KEY="your-api-key-here"
export REPOTOIRE_NEO4J_URI="bolt://localhost:7688"
export REPOTOIRE_NEO4J_PASSWORD="your-password"

# Start the server
python -m repotoire.api.app
```

The API will be available at `http://localhost:8000`

Interactive documentation: `http://localhost:8000/docs`

### 2. Ingest Your Codebase with Embeddings

```bash
# Ingest codebase and generate embeddings
OPENAI_API_KEY="your-key" repotoire ingest /path/to/repo --generate-embeddings
```

**Note**: Embedding generation requires an OpenAI API key and will cost approximately $0.13 per 1M tokens (~200k lines of code).

### 3. Query Your Code

```bash
# Search for code
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does authentication work?",
    "top_k": 5
  }'

# Ask questions
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I add a new API endpoint?",
    "top_k": 5
  }'
```

## API Endpoints

### POST /api/v1/code/search

Semantic code search using vector similarity and graph traversal.

**Request Body:**
```json
{
  "query": "string (3-500 chars, required)",
  "top_k": 10,
  "entity_types": ["Function", "Class", "File"],
  "include_related": true
}
```

**Response:**
```json
{
  "results": [
    {
      "entity_type": "Function",
      "qualified_name": "auth.authenticate_user",
      "name": "authenticate_user",
      "code": "def authenticate_user(username, password): ...",
      "docstring": "Authenticate user with credentials",
      "similarity_score": 0.89,
      "file_path": "auth.py",
      "line_start": 10,
      "line_end": 25,
      "relationships": [],
      "metadata": {}
    }
  ],
  "total": 5,
  "query": "How does authentication work?",
  "search_strategy": "hybrid",
  "execution_time_ms": 245.3
}
```

**Parameters:**
- `query` (required): Natural language search query
- `top_k` (optional, default: 10): Number of results to return (max 50)
- `entity_types` (optional): Filter by entity type (Function, Class, File)
- `include_related` (optional, default: true): Include graph-related entities for context

**Search Strategies:**
- `vector`: Pure semantic search using embeddings
- `hybrid`: Combines vector search + graph traversal (recommended)

### POST /api/v1/code/ask

Ask natural language questions about your codebase and get AI-powered answers.

**Request Body:**
```json
{
  "question": "string (10-1000 chars, required)",
  "top_k": 10,
  "include_related": true,
  "conversation_history": [
    {"role": "user", "content": "previous question"},
    {"role": "assistant", "content": "previous answer"}
  ]
}
```

**Response:**
```json
{
  "answer": "The authentication system uses JWT tokens...",
  "sources": [
    {
      "entity_type": "Function",
      "qualified_name": "auth.authenticate_user",
      "similarity_score": 0.92,
      "file_path": "auth.py",
      "line_start": 10,
      "line_end": 25
    }
  ],
  "confidence": 0.87,
  "follow_up_questions": [
    "How are JWT tokens verified?",
    "What happens when authentication fails?"
  ],
  "execution_time_ms": 1523.4
}
```

**Parameters:**
- `question` (required): Natural language question about the codebase
- `top_k` (optional, default: 10): Number of code entities to retrieve
- `include_related` (optional, default: true): Include related entities for context
- `conversation_history` (optional): Previous conversation for context

**How it works:**
1. Retrieve relevant code using hybrid search (vector + graph)
2. Assemble context with code snippets and relationships
3. Generate answer using GPT-4o
4. Return answer with source citations and confidence score

### GET /api/v1/code/embeddings/status

Check embedding coverage and status.

**Response:**
```json
{
  "total_entities": 1250,
  "embedded_entities": 1250,
  "embedding_coverage": 100.0,
  "functions_embedded": 850,
  "classes_embedded": 300,
  "files_embedded": 100,
  "last_generated": "2025-11-21T12:00:00Z",
  "model_used": "text-embedding-3-small"
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy"
}
```

### GET /

API information and available endpoints.

## Usage Examples

### Semantic Code Search

Find code by describing what it does:

```bash
# Find authentication functions
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "functions that validate user credentials",
    "top_k": 5,
    "entity_types": ["Function"]
  }'

# Find database connection classes
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "classes that manage database connections",
    "entity_types": ["Class"],
    "include_related": true
  }'

# Find error handling code
curl -X POST "http://localhost:8000/api/v1/code/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "exception handling and error recovery",
    "top_k": 10
  }'
```

### Question Answering

Ask questions and get detailed answers:

```bash
# How does a feature work?
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How does the authentication system work?",
    "top_k": 5
  }'

# Find usage examples
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I create a new API endpoint?",
    "top_k": 10
  }'

# Understanding architecture
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the overall architecture of the system?",
    "include_related": true
  }'
```

### Conversational Context

Maintain context across multiple questions:

```bash
# First question
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How is user authentication implemented?"
  }'

# Follow-up question with context
curl -X POST "http://localhost:8000/api/v1/code/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How would I add two-factor authentication?",
    "conversation_history": [
      {"role": "user", "content": "How is user authentication implemented?"},
      {"role": "assistant", "content": "The authentication system uses JWT tokens..."}
    ]
  }'
```

## Example Queries

### Understanding Code

**"How does the parser extract function signatures?"**
- Retrieves parser implementation code
- Shows AST traversal logic
- Explains signature extraction process

**"What design patterns are used in the codebase?"**
- Finds classes with pattern names (Factory, Builder, etc.)
- Shows implementation examples
- Explains pattern usage

**"How are errors handled in the API layer?"**
- Finds exception handlers
- Shows error response formatting
- Explains error propagation

### Finding Code

**"Where is JWT token generation implemented?"**
- Locates token creation functions
- Shows token encoding logic
- Finds related validation code

**"Show me all database query functions"**
- Finds functions that execute SQL
- Groups by table/entity
- Shows query patterns

**"Find code that handles file uploads"**
- Locates upload handlers
- Shows file validation
- Finds storage logic

### Refactoring & Maintenance

**"Which functions call the deprecated login() method?"**
- Finds all call sites
- Shows call context
- Suggests migration paths

**"What would break if I change the User class?"**
- Finds all usage locations
- Shows dependencies
- Identifies integration points

**"Find duplicate code in the authentication module"**
- Detects similar functions
- Shows code patterns
- Suggests refactoring opportunities

## Python Client

Use the API from Python code:

```python
import requests

class RepotoireClient:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url

    def search(self, query: str, top_k: int = 10, entity_types: list = None):
        """Search for code semantically."""
        response = requests.post(
            f"{self.base_url}/api/v1/code/search",
            json={
                "query": query,
                "top_k": top_k,
                "entity_types": entity_types,
                "include_related": True
            }
        )
        response.raise_for_status()
        return response.json()

    def ask(self, question: str, top_k: int = 10, conversation_history: list = None):
        """Ask a question about the codebase."""
        response = requests.post(
            f"{self.base_url}/api/v1/code/ask",
            json={
                "question": question,
                "top_k": top_k,
                "conversation_history": conversation_history or []
            }
        )
        response.raise_for_status()
        return response.json()

    def embedding_status(self):
        """Check embedding coverage."""
        response = requests.get(f"{self.base_url}/api/v1/code/embeddings/status")
        response.raise_for_status()
        return response.json()

# Usage
client = RepotoireClient()

# Search for code
results = client.search("authentication functions", top_k=5)
for result in results["results"]:
    print(f"{result['qualified_name']} (score: {result['similarity_score']:.2f})")
    print(f"  {result['file_path']}:{result['line_start']}")

# Ask a question
answer = client.ask("How does authentication work?")
print(f"Answer: {answer['answer']}")
print(f"Confidence: {answer['confidence']:.2%}")
print(f"Sources: {len(answer['sources'])}")
```

## Troubleshooting

### No Results Returned

**Symptom**: Search/ask returns empty results

**Solutions**:
1. Check embeddings were generated: `GET /api/v1/code/embeddings/status`
2. Verify embedding coverage is > 0%
3. Re-run ingestion with `--generate-embeddings` flag
4. Check Neo4j connection is working
5. Try broader queries (more general terms)

```bash
# Check embedding status
curl http://localhost:8000/api/v1/code/embeddings/status

# Re-generate embeddings
repotoire ingest /path/to/repo --generate-embeddings
```

### Vector Index Errors

**Symptom**: `There is no such vector schema index: function_embeddings`

**Solution**: Vector indexes weren't created during schema initialization.

```python
from repotoire.graph import Neo4jClient
from repotoire.graph.schema import GraphSchema

client = Neo4jClient(uri="bolt://localhost:7688", password="your-password")
schema = GraphSchema(client)
schema.create_vector_indexes()
```

### OpenAI API Errors

**Symptom**: `OpenAI API key not set` or rate limit errors

**Solutions**:
1. Set `OPENAI_API_KEY` environment variable
2. Check API key is valid and has credits
3. Reduce batch size for embeddings
4. Add retry logic with backoff

```bash
# Set API key
export OPENAI_API_KEY="sk-..."

# Check API key works
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

### Slow Response Times

**Symptom**: Queries take > 5 seconds

**Solutions**:
1. Reduce `top_k` parameter (try 5 instead of 10)
2. Disable `include_related` for faster vector-only search
3. Check Neo4j vector indexes exist
4. Optimize Neo4j memory settings
5. Use caching for frequently asked questions

```json
{
  "query": "your search",
  "top_k": 5,
  "include_related": false
}
```

### Low Confidence Scores

**Symptom**: Answers have confidence < 0.5

**Possible causes**:
1. Query is too vague or broad
2. No relevant code found in codebase
3. Embedding quality issues
4. Insufficient context provided

**Solutions**:
- Make queries more specific
- Include relevant file/module names
- Increase `top_k` to retrieve more context
- Check embeddings were generated correctly

## Performance Tips

### Optimize Embedding Generation

```bash
# Generate embeddings in smaller batches
# Default batch size: 50 entities
repotoire ingest /path/to/repo --generate-embeddings --batch-size 25

# Only generate embeddings for new entities
# Ingestion is idempotent - won't regenerate existing embeddings
repotoire ingest /path/to/repo --generate-embeddings
```

### Optimize Search Performance

1. **Use entity type filters** when you know what you're looking for:
   ```json
   {"query": "authentication", "entity_types": ["Function"]}
   ```

2. **Reduce top_k** for faster results:
   ```json
   {"query": "authentication", "top_k": 5}
   ```

3. **Disable related entities** for pure vector search:
   ```json
   {"query": "authentication", "include_related": false}
   ```

4. **Cache frequent queries** at the application level

### Cost Optimization

**Embedding Generation Costs** (OpenAI text-embedding-3-small):
- ~$0.13 per 1M tokens
- Average code file: ~500 tokens
- 1000 files ≈ 500k tokens ≈ $0.065
- Large codebase (10k files) ≈ $0.65

**Query Costs** (GPT-4o for Q&A):
- Search only: No LLM cost (just embeddings)
- Ask endpoint: ~$0.0075 per query (500 tokens context + 200 tokens response)

**Recommendations**:
- Generate embeddings once during ingestion
- Embeddings are cached in Neo4j (no regeneration needed)
- Use search endpoint (free) when possible
- Reserve ask endpoint for when you need explanations

### Neo4j Configuration

For optimal performance with vector search:

```bash
# Increase Neo4j heap memory
docker run \
  -e NEO4J_server_memory_heap_max__size=4G \
  -e NEO4J_server_memory_heap_initial__size=2G \
  ...
```

## API Limits

- **Query length**: 3-500 characters for search, 10-1000 for ask
- **top_k**: Max 50 results
- **Conversation history**: Last 5 messages used
- **Rate limiting**: None (add at reverse proxy level for production)

## Next Steps

- [Architecture Overview](../RAG_ARCHITECTURE.md)
- [API Reference](http://localhost:8000/docs)
- [Main Documentation](../CLAUDE.md)
- [Configuration Guide](../CONFIG.md)
