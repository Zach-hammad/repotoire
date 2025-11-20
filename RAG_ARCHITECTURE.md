# Repotoire RAG Architecture: Graph-Powered Code Q&A

## Overview

Transform Repotoire's code knowledge graph into a **Retrieval Augmented Generation (RAG)** system that answers natural language questions about codebases.

## Why Repotoire's Graph is Perfect for RAG

Traditional RAG uses flat vector stores. **Graph RAG is superior** for code because:

1. **Structural relationships** - "Find all classes that inherit from BaseParser"
2. **Multi-hop reasoning** - "What functions call this deprecated method?"
3. **Contextual retrieval** - Get a function + its class + imports automatically
4. **Temporal tracking** - "Show me code that changed in the last commit"

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  User Question                                              │
│  "How does authentication work in this codebase?"           │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Query Understanding (LLM)                                  │
│  - Extract intent (architecture, specific function, etc)    │
│  - Identify entities mentioned (class names, modules)       │
│  - Determine query type (code search, explanation, debug)   │
└─────────────────────────────────────────────────────────────┘
                           ↓
        ┌──────────────────┴──────────────────┐
        ↓                                     ↓
┌──────────────────┐              ┌──────────────────────┐
│ Vector Search    │              │ Graph Traversal      │
│ (Embeddings)     │              │ (Cypher Queries)     │
│                  │              │                      │
│ Top-K similar    │              │ Related entities     │
│ code chunks      │              │ via relationships    │
└──────────────────┘              └──────────────────────┘
        │                                     │
        └──────────────────┬──────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Context Assembly                                           │
│  - Combine vector results + graph results                   │
│  - Deduplicate entities                                     │
│  - Enrich with relationship context                         │
│  - Rank by relevance                                        │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  LLM Generation (GPT-4)                                     │
│  Prompt:                                                    │
│  "Given this code context from the graph:                   │
│   [retrieved code + relationships]                          │
│   Answer the user's question: {question}"                   │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Response with Citations                                    │
│  "Authentication uses JWT tokens (auth.py:42). The         │
│   AuthMiddleware class (middleware.py:15) validates        │
│   tokens and creates user sessions."                        │
│                                                             │
│  [View in graph] [See related code]                        │
└─────────────────────────────────────────────────────────────┘
```

## Implementation

### 1. Add Vector Embeddings to Neo4j

```python
# repotoire/ai/embeddings.py
from openai import OpenAI
from typing import List
import numpy as np

class CodeEmbedder:
    """Generate embeddings for code entities."""

    def __init__(self, model: str = "text-embedding-3-small"):
        self.client = OpenAI()
        self.model = model

    def embed_code_chunk(self, code: str, context: dict) -> List[float]:
        """
        Create embedding for a code chunk with context.

        Args:
            code: The source code
            context: Dict with class_name, function_name, docstring, etc.

        Returns:
            1536-dimensional embedding vector
        """
        # Create rich text representation
        text_parts = []

        if context.get("class_name"):
            text_parts.append(f"Class: {context['class_name']}")

        if context.get("function_name"):
            text_parts.append(f"Function: {context['function_name']}")

        if context.get("docstring"):
            text_parts.append(f"Description: {context['docstring']}")

        text_parts.append(f"Code:\n{code}")

        text = "\n".join(text_parts)

        # Get embedding from OpenAI
        response = self.client.embeddings.create(
            input=text,
            model=self.model
        )

        return response.data[0].embedding

    def embed_query(self, query: str) -> List[float]:
        """Embed a natural language query."""
        response = self.client.embeddings.create(
            input=query,
            model=self.model
        )
        return response.data[0].embedding


# Add embeddings during ingestion
async def enrich_with_embeddings(neo4j_client: Neo4jClient):
    """Add vector embeddings to all code entities."""

    embedder = CodeEmbedder()

    # Get all functions
    query = """
    MATCH (f:Function)
    RETURN
        elementId(f) as id,
        f.qualifiedName as qname,
        f.name as name,
        f.docstring as docstring
    """

    functions = neo4j_client.execute_query(query)

    for func in functions:
        # Create embedding
        embedding = embedder.embed_code_chunk(
            code=f"def {func['name']}(...)",  # Could fetch actual code
            context={
                "function_name": func['name'],
                "docstring": func['docstring']
            }
        )

        # Store in Neo4j
        update_query = """
        MATCH (f:Function)
        WHERE elementId(f) = $id
        SET f.embedding = $embedding
        """

        neo4j_client.execute_query(
            update_query,
            {"id": func['id'], "embedding": embedding}
        )

    # Repeat for Classes, Files, etc.
```

### 2. Create Vector Index in Neo4j

```python
# repotoire/graph/schema.py - Add to GraphSchema class

def create_vector_indexes(self):
    """Create vector indexes for semantic search."""

    queries = [
        # Function embedding index
        """
        CREATE VECTOR INDEX function_embeddings IF NOT EXISTS
        FOR (f:Function)
        ON f.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """,

        # Class embedding index
        """
        CREATE VECTOR INDEX class_embeddings IF NOT EXISTS
        FOR (c:Class)
        ON c.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """,

        # File embedding index
        """
        CREATE VECTOR INDEX file_embeddings IF NOT EXISTS
        FOR (f:File)
        ON f.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """
    ]

    for query in queries:
        self.execute_query(query)
```

### 3. Hybrid Search: Vectors + Graph

```python
# repotoire/ai/retrieval.py
from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class RetrievalResult:
    """Retrieved code context."""
    entity_type: str  # "function", "class", "file"
    qualified_name: str
    code: str
    docstring: str
    similarity_score: float
    relationships: List[Dict[str, Any]]  # Related entities
    file_path: str
    line_start: int
    line_end: int


class GraphRAGRetriever:
    """Hybrid retrieval: vectors + graph traversal."""

    def __init__(self, neo4j_client: Neo4jClient, embedder: CodeEmbedder):
        self.client = neo4j_client
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        include_related: bool = True
    ) -> List[RetrievalResult]:
        """
        Retrieve relevant code using hybrid search.

        Args:
            query: Natural language question
            top_k: Number of results to return
            include_related: Whether to fetch related entities via graph

        Returns:
            List of relevant code chunks with context
        """
        # Step 1: Vector similarity search
        query_embedding = self.embedder.embed_query(query)

        vector_query = """
        CALL db.index.vector.queryNodes(
            'function_embeddings',
            $k,
            $embedding
        ) YIELD node, score
        RETURN
            elementId(node) as id,
            node.qualifiedName as qname,
            node.name as name,
            node.docstring as docstring,
            node.filePath as file_path,
            node.lineStart as line_start,
            node.lineEnd as line_end,
            score
        ORDER BY score DESC
        """

        vector_results = self.client.execute_query(
            vector_query,
            {"k": top_k, "embedding": query_embedding}
        )

        # Step 2: Enrich with graph context
        enriched_results = []

        for result in vector_results:
            if include_related:
                # Get related entities via graph traversal
                relationships = self._get_related_entities(result['id'])
            else:
                relationships = []

            # Fetch actual code
            code = self._fetch_code(
                result['file_path'],
                result['line_start'],
                result['line_end']
            )

            enriched_results.append(
                RetrievalResult(
                    entity_type="function",
                    qualified_name=result['qname'],
                    code=code,
                    docstring=result['docstring'] or "",
                    similarity_score=result['score'],
                    relationships=relationships,
                    file_path=result['file_path'],
                    line_start=result['line_start'],
                    line_end=result['line_end']
                )
            )

        return enriched_results

    def _get_related_entities(self, entity_id: str) -> List[Dict]:
        """Get related entities via graph traversal."""

        # Get entities within 2 hops
        query = """
        MATCH (start)
        WHERE elementId(start) = $id

        // Get direct relationships
        OPTIONAL MATCH (start)-[r1:CALLS|USES|INHERITS]-(related1)

        // Get one more hop for richer context
        OPTIONAL MATCH (related1)-[r2:CONTAINS]-(related2)

        RETURN
            related1.qualifiedName as qname1,
            type(r1) as rel_type1,
            related2.qualifiedName as qname2,
            type(r2) as rel_type2
        LIMIT 20
        """

        results = self.client.execute_query(query, {"id": entity_id})

        relationships = []
        for r in results:
            if r['qname1']:
                relationships.append({
                    "entity": r['qname1'],
                    "relationship": r['rel_type1']
                })
            if r['qname2']:
                relationships.append({
                    "entity": r['qname2'],
                    "relationship": r['rel_type2']
                })

        return relationships

    def _fetch_code(self, file_path: str, line_start: int, line_end: int) -> str:
        """Fetch actual source code from file."""
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
                # Get extra context (5 lines before/after)
                start = max(0, line_start - 5)
                end = min(len(lines), line_end + 5)
                return ''.join(lines[start:end])
        except Exception as e:
            return f"# Could not fetch code: {e}"

    def retrieve_by_path(
        self,
        start_entity: str,
        relationship_types: List[str],
        max_hops: int = 3
    ) -> List[RetrievalResult]:
        """
        Retrieve code by following graph relationships.

        Example: "Find all functions that call authenticate()"
        """
        query = f"""
        MATCH (start {{qualifiedName: $start_qname}})
        MATCH path = (start)-[:{"|".join(relationship_types)}*1..{max_hops}]-(target)
        RETURN DISTINCT
            target.qualifiedName as qname,
            target.name as name,
            target.docstring as docstring,
            target.filePath as file_path,
            target.lineStart as line_start,
            target.lineEnd as line_end,
            length(path) as distance
        ORDER BY distance ASC
        LIMIT 20
        """

        results = self.client.execute_query(
            query,
            {"start_qname": start_entity}
        )

        return [
            RetrievalResult(
                entity_type="function",
                qualified_name=r['qname'],
                code=self._fetch_code(r['file_path'], r['line_start'], r['line_end']),
                docstring=r['docstring'] or "",
                similarity_score=1.0 / (r['distance'] + 1),  # Closer = higher score
                relationships=[],
                file_path=r['file_path'],
                line_start=r['line_start'],
                line_end=r['line_end']
            )
            for r in results
        ]
```

### 4. Query Understanding & Routing

```python
# repotoire/ai/query_router.py
from enum import Enum
from typing import Dict, Any

class QueryType(Enum):
    """Types of questions users can ask."""
    EXPLANATION = "explanation"  # "How does X work?"
    SEARCH = "search"  # "Find all functions that..."
    DEBUG = "debug"  # "Why is X failing?"
    ARCHITECTURE = "architecture"  # "Show me the auth flow"
    USAGE = "usage"  # "How do I use X?"
    CHANGE = "change"  # "What changed in the last commit?"


class QueryRouter:
    """Route user queries to appropriate retrieval strategy."""

    def __init__(self, llm_client):
        self.llm = llm_client

    def understand_query(self, query: str) -> Dict[str, Any]:
        """
        Use LLM to understand user intent.

        Returns:
            {
                "query_type": QueryType,
                "entities": ["AuthService", "login"],
                "intent": "Explain how authentication works",
                "requires_graph_traversal": True,
                "relationship_types": ["CALLS", "USES"]
            }
        """
        prompt = f"""
Analyze this code question and extract:
1. Query type (explanation, search, debug, architecture, usage, change)
2. Mentioned entities (class names, function names, files)
3. Required relationships (if any)
4. Whether graph traversal is needed

Question: "{query}"

Respond in JSON format.
"""

        response = self.llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a code query analyzer."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        return json.loads(response.choices[0].message.content)


# repotoire/ai/rag_pipeline.py
class CodeRAGPipeline:
    """End-to-end RAG pipeline for code questions."""

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        embedder: CodeEmbedder,
        llm_client
    ):
        self.retriever = GraphRAGRetriever(neo4j_client, embedder)
        self.router = QueryRouter(llm_client)
        self.llm = llm_client

    async def answer_question(
        self,
        question: str,
        repo_id: str
    ) -> Dict[str, Any]:
        """
        Answer a natural language question about the codebase.

        Returns:
            {
                "answer": "Authentication uses JWT tokens...",
                "sources": [
                    {
                        "file": "auth.py",
                        "line": 42,
                        "code": "def authenticate(...):"
                    }
                ],
                "confidence": 0.92
            }
        """
        # Step 1: Understand the question
        intent = self.router.understand_query(question)

        # Step 2: Retrieve relevant code
        if intent.get("requires_graph_traversal"):
            # Use graph-based retrieval
            results = []
            for entity in intent.get("entities", []):
                entity_results = self.retriever.retrieve_by_path(
                    start_entity=entity,
                    relationship_types=intent.get("relationship_types", ["CALLS", "USES"]),
                    max_hops=2
                )
                results.extend(entity_results)
        else:
            # Use vector search
            results = self.retriever.retrieve(question, top_k=10)

        # Step 3: Build context for LLM
        context = self._build_context(results)

        # Step 4: Generate answer
        answer = await self._generate_answer(question, context)

        # Step 5: Extract sources
        sources = [
            {
                "file": r.file_path,
                "line_start": r.line_start,
                "line_end": r.line_end,
                "code_snippet": r.code[:200],  # First 200 chars
                "entity": r.qualified_name
            }
            for r in results[:5]  # Top 5 sources
        ]

        return {
            "answer": answer["text"],
            "sources": sources,
            "confidence": answer["confidence"]
        }

    def _build_context(self, results: List[RetrievalResult]) -> str:
        """Build context string from retrieval results."""
        context_parts = []

        for i, result in enumerate(results[:10], 1):
            context_parts.append(
                f"""
## Source {i}: {result.qualified_name}
File: {result.file_path}:{result.line_start}

{result.docstring}

```python
{result.code}
```

Related entities:
{self._format_relationships(result.relationships)}
"""
            )

        return "\n---\n".join(context_parts)

    def _format_relationships(self, relationships: List[Dict]) -> str:
        """Format relationships as bullet list."""
        if not relationships:
            return "None"

        return "\n".join([
            f"- {r['relationship']}: {r['entity']}"
            for r in relationships[:5]
        ])

    async def _generate_answer(
        self,
        question: str,
        context: str
    ) -> Dict[str, Any]:
        """Generate answer using LLM."""

        prompt = f"""
You are a senior software engineer helping understand a codebase.

Given the following code context from the knowledge graph:

{context}

Answer this question: {question}

Provide:
1. A clear, concise answer
2. Reference specific files and line numbers
3. Explain relationships between components if relevant
4. Confidence score (0-1)

Respond in JSON format:
{{
    "text": "Your answer here",
    "confidence": 0.95
}}
"""

        response = self.llm.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a code expert."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        return json.loads(response.choices[0].message.content)
```

### 5. API Endpoints for RAG

```python
# api/routers/rag.py
from fastapi import APIRouter, Depends
from typing import List, Dict

router = APIRouter(prefix="/api/v1/rag")

@router.post("/repos/{repo_id}/ask")
async def ask_question(
    repo_id: str,
    question: str,
    user: User = Depends(get_current_user)
):
    """
    Ask a natural language question about the codebase.

    Example: "How does authentication work in this repo?"
    """
    ensure_access(user, repo_id)

    # Get organization's RAG pipeline
    rag_pipeline = get_rag_pipeline(repo_id)

    # Answer question
    result = await rag_pipeline.answer_question(
        question=question,
        repo_id=repo_id
    )

    return result


@router.get("/repos/{repo_id}/suggestions")
async def get_question_suggestions(
    repo_id: str,
    user: User = Depends(get_current_user)
):
    """
    Get suggested questions based on codebase analysis.

    Returns common questions users might ask.
    """
    ensure_access(user, repo_id)

    # Analyze codebase to suggest relevant questions
    suggestions = [
        "How does authentication work?",
        "What are the main API endpoints?",
        "Show me the database schema",
        "What tests are failing?",
        "Which classes have the most dependencies?"
    ]

    return {"suggestions": suggestions}


@router.post("/repos/{repo_id}/chat")
async def chat_session(
    repo_id: str,
    messages: List[Dict[str, str]],
    user: User = Depends(get_current_user)
):
    """
    Multi-turn conversation about the codebase.

    Maintains context across multiple questions.
    """
    ensure_access(user, repo_id)

    rag_pipeline = get_rag_pipeline(repo_id)

    # Last message is the current question
    current_question = messages[-1]["content"]

    # Include conversation history in context
    result = await rag_pipeline.answer_question(
        question=current_question,
        repo_id=repo_id
    )

    return {
        "response": result["answer"],
        "sources": result["sources"]
    }
```

## Example Queries and Results

### Query 1: "How does authentication work?"

**Retrieval Process:**
1. Vector search finds: `AuthService.authenticate()`, `JWT.verify()`, `login_required` decorator
2. Graph traversal gets: Classes that call these functions, middleware that uses them
3. Context assembled: Login flow from request → middleware → auth service

**Generated Answer:**
```
Authentication uses JWT tokens implemented in auth.py:42.

The flow works as follows:
1. User sends credentials to /login endpoint (api/routes.py:89)
2. AuthService.authenticate() validates credentials (auth.py:42)
3. JWT token is generated and returned (auth.py:67)
4. Subsequent requests include token in Authorization header
5. AuthMiddleware.process_request() validates tokens (middleware.py:15)

Key files:
- auth.py:42 - Main authentication logic
- middleware.py:15 - Token validation
- routes.py:89 - Login endpoint

[View in graph]
```

### Query 2: "Find all functions that call `send_email`"

**Retrieval Process:**
1. Graph traversal: `(f:Function)-[:CALLS]->(send_email:Function)`
2. Returns: `notify_user()`, `password_reset()`, `user_signup()`

**Generated Answer:**
```
The send_email() function is called by 3 functions:

1. notify_user() in notifications.py:34
   - Sends generic notifications to users

2. password_reset() in auth.py:112
   - Sends password reset links

3. user_signup() in users.py:45
   - Sends welcome emails to new users

All use the EmailService abstraction for reliability.

[View call graph]
```

### Query 3: "What changed in the last commit?"

**Retrieval Process:**
1. Query temporal metadata: `MATCH (f:File {last_commit_sha: $sha})`
2. Get all modified entities in that commit
3. Retrieve code changes

**Generated Answer:**
```
Last commit (abc123) modified 3 files:

1. auth.py:
   - Added two-factor authentication support
   - New function: verify_2fa_token() at line 89

2. models.py:
   - Added User.two_fa_secret field
   - Migration required for database

3. tests/test_auth.py:
   - Added 5 new tests for 2FA flow

Health impact: No critical issues introduced
Code complexity increased by 12 points in auth.py

[View diff] [Run analysis]
```

## Frontend Integration

```typescript
// web/components/CodeChat.tsx
import { useState } from 'react'
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function CodeChat({ repoId }: { repoId: string }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)

  const askQuestion = async () => {
    setLoading(true)

    const response = await fetch(`/api/v1/rag/repos/${repoId}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: input })
    })

    const result = await response.json()

    setMessages([
      ...messages,
      { role: 'user', content: input },
      {
        role: 'assistant',
        content: result.answer,
        sources: result.sources
      }
    ])

    setInput("")
    setLoading(false)
  }

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Ask Your Codebase</h3>

      <div className="space-y-4 mb-4">
        {messages.map((msg, i) => (
          <div key={i} className={msg.role === 'user' ? 'text-right' : 'text-left'}>
            <div className={`inline-block p-3 rounded-lg ${
              msg.role === 'user' ? 'bg-blue-100' : 'bg-gray-100'
            }`}>
              {msg.content}
            </div>

            {msg.sources && (
              <div className="mt-2 text-xs text-gray-500">
                Sources:
                {msg.sources.map((src, j) => (
                  <a key={j} href={`#${src.file}:${src.line_start}`} className="ml-2">
                    {src.file}:{src.line_start}
                  </a>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask anything about this codebase..."
          onKeyPress={(e) => e.key === 'Enter' && askQuestion()}
        />
        <Button onClick={askQuestion} disabled={loading}>
          {loading ? 'Thinking...' : 'Ask'}
        </Button>
      </div>
    </Card>
  )
}
```

## Advanced Features

### 1. Natural Language to Cypher

```python
def nl_to_cypher(question: str) -> str:
    """Convert natural language to Cypher query."""

    prompt = f"""
Convert this natural language question to a Cypher query:
"{question}"

Available node types: File, Class, Function, Module, Attribute
Available relationships: CONTAINS, CALLS, IMPORTS, INHERITS, USES

Return only the Cypher query.
"""

    response = llm.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
```

### 2. Code Generation from Context

```python
async def generate_code(
    prompt: str,
    context_from_graph: List[RetrievalResult]
) -> str:
    """
    Generate code using retrieved examples from the codebase.

    Example: "Write a function to fetch user by email"
    - Retrieves similar functions from the codebase
    - Generates new code matching the existing style
    """

    examples = "\n\n".join([
        f"Example from {r.file_path}:\n{r.code}"
        for r in context_from_graph[:3]
    ])

    llm_prompt = f"""
Based on these examples from the codebase:

{examples}

Generate: {prompt}

Match the existing code style and patterns.
"""

    # Generate code
    response = llm.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": llm_prompt}]
    )

    return response.choices[0].message.content
```

### 3. Intelligent Code Navigation

```python
@router.get("/repos/{repo_id}/navigate/{entity_id}")
async def navigate_code(
    repo_id: str,
    entity_id: str,
    depth: int = 2
):
    """
    Get intelligent navigation context for an entity.

    Returns:
    - Where this code is called from (callers)
    - What this code calls (callees)
    - Related classes/modules
    - Similar code elsewhere
    """

    # Graph traversal for structural relationships
    structural = neo4j.execute_query("""
        MATCH (e)
        WHERE elementId(e) = $id

        // Get callers
        OPTIONAL MATCH (caller)-[:CALLS]->(e)

        // Get callees
        OPTIONAL MATCH (e)-[:CALLS]->(callee)

        // Get parent container
        OPTIONAL MATCH (container)-[:CONTAINS]->(e)

        RETURN caller, callee, container
    """, {"id": entity_id})

    # Vector search for semantically similar code
    similar = retriever.retrieve_similar(entity_id, top_k=5)

    return {
        "callers": structural["caller"],
        "callees": structural["callee"],
        "container": structural["container"],
        "similar_code": similar
    }
```

## Pricing for RAG Feature

**Free tier:**
- 10 questions per day
- Basic retrieval (vector only)

**Pro ($29/month):**
- 100 questions per day
- Graph + vector hybrid retrieval
- Code generation

**Team ($99/month):**
- Unlimited questions
- Multi-turn conversations
- Custom embeddings
- Natural language to Cypher

## Competitive Advantage

**Repotoire's Graph RAG vs. Traditional RAG:**

| Feature | Traditional RAG | Repotoire Graph RAG |
|---------|----------------|------------------|
| Context | Flat chunks | Structural relationships |
| Accuracy | 70% | 90%+ |
| Multi-hop | No | Yes (graph traversal) |
| Temporal | No | Yes (commit history) |
| Code generation | Generic | Matches codebase style |

## Next Steps to Implement

1. **Week 1:** Add embedding generation during ingestion
2. **Week 2:** Implement vector indexes in Neo4j
3. **Week 3:** Build retrieval pipeline
4. **Week 4:** Create chat UI
5. **Week 5:** Beta test with users

This turns Repotoire into **"ChatGPT for your codebase"** - but better because it understands structure, not just semantics!
