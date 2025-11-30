"""API routes for code Q&A and search."""

import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from openai import OpenAI

from repotoire.api.models import (
    CodeSearchRequest,
    CodeSearchResponse,
    CodeAskRequest,
    CodeAskResponse,
    EmbeddingsStatusResponse,
    CodeEntity,
    ErrorResponse
)
from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.ai.retrieval import GraphRAGRetriever, RetrievalResult
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/code", tags=["Code Q&A"])


# Dependency injection for Neo4j client
def get_neo4j_client() -> Neo4jClient:
    """Get Neo4j client instance."""
    # TODO: Read from config
    return Neo4jClient(
        uri="bolt://localhost:7688",
        password="falkor-password"
    )


def get_embedder() -> CodeEmbedder:
    """Get CodeEmbedder instance."""
    return CodeEmbedder()


def get_retriever(
    client: Neo4jClient = Depends(get_neo4j_client),
    embedder: CodeEmbedder = Depends(get_embedder)
) -> GraphRAGRetriever:
    """Get GraphRAGRetriever instance."""
    return GraphRAGRetriever(
        neo4j_client=client,
        embedder=embedder
    )


def _retrieval_result_to_code_entity(result: RetrievalResult) -> CodeEntity:
    """Convert RetrievalResult to CodeEntity API model."""
    return CodeEntity(
        entity_type=result.entity_type,
        qualified_name=result.qualified_name,
        name=result.name,
        code=result.code,
        docstring=result.docstring,
        similarity_score=result.similarity_score,
        file_path=result.file_path,
        line_start=result.line_start,
        line_end=result.line_end,
        relationships=result.relationships,
        metadata=result.metadata
    )


@router.post(
    "/search",
    response_model=CodeSearchResponse,
    summary="Search codebase semantically",
    description="Search for code entities using hybrid vector + graph search",
    responses={
        200: {"description": "Search results returned successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def search_code(
    request: CodeSearchRequest,
    user: ClerkUser = Depends(get_current_user),
    retriever: GraphRAGRetriever = Depends(get_retriever)
) -> CodeSearchResponse:
    """
    Search codebase using hybrid vector + graph retrieval.

    **Search Strategy**:
    - Vector similarity search for semantic matching
    - Graph traversal for related entities
    - Ranked by relevance score

    **Example Queries**:
    - "How does authentication work?"
    - "Find all functions that parse JSON"
    - "Classes that handle database connections"
    """
    start_time = time.time()

    try:
        logger.info(f"Code search request: {request.query}")

        # Perform hybrid retrieval
        results = retriever.retrieve(
            query=request.query,
            top_k=request.top_k,
            entity_types=request.entity_types,
            include_related=request.include_related
        )

        # Convert to API models
        code_entities = [_retrieval_result_to_code_entity(r) for r in results]

        execution_time_ms = (time.time() - start_time) * 1000

        return CodeSearchResponse(
            results=code_entities,
            total=len(code_entities),
            query=request.query,
            search_strategy="hybrid" if request.include_related else "vector",
            execution_time_ms=execution_time_ms
        )

    except Exception as e:
        logger.error(f"Code search error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@router.post(
    "/ask",
    response_model=CodeAskResponse,
    summary="Ask questions about codebase",
    description="Get AI-powered answers to questions about the codebase using RAG",
    responses={
        200: {"description": "Answer generated successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def ask_code_question(
    request: CodeAskRequest,
    user: ClerkUser = Depends(get_current_user),
    retriever: GraphRAGRetriever = Depends(get_retriever)
) -> CodeAskResponse:
    """
    Ask natural language questions about the codebase.

    **How it works**:
    1. Retrieve relevant code using hybrid search
    2. Assemble context from retrieved code + graph relationships
    3. Generate answer using OpenAI GPT-4o
    4. Return answer with source citations

    **Example Questions**:
    - "How does the authentication system work?"
    - "What are the main classes for parsing Python code?"
    - "How do I add a new detector to the system?"
    """
    start_time = time.time()

    try:
        logger.info(f"Code Q&A request: {request.question}")

        # Step 1: Retrieve relevant code
        retrieval_results = retriever.retrieve(
            query=request.question,
            top_k=request.top_k,
            include_related=request.include_related
        )

        if not retrieval_results:
            return CodeAskResponse(
                answer="I couldn't find any relevant code to answer your question. Please try rephrasing or ask about different aspects of the codebase.",
                sources=[],
                confidence=0.0,
                follow_up_questions=[],
                execution_time_ms=(time.time() - start_time) * 1000
            )

        # Step 2: Assemble context for LLM
        context_parts = []
        for i, result in enumerate(retrieval_results[:5], 1):  # Use top 5 for context
            context_parts.append(f"**Source {i}: {result.qualified_name}** (relevance: {result.similarity_score:.2f})")
            if result.docstring:
                context_parts.append(f"Description: {result.docstring}")
            context_parts.append(f"```python\n{result.code}\n```")
            if result.relationships:
                rel_summary = ", ".join([f"{r['relationship']} {r['entity']}" for r in result.relationships[:3]])
                context_parts.append(f"Related: {rel_summary}")
            context_parts.append("")  # Blank line

        context = "\n".join(context_parts)

        # Step 2.5: Get hot rules context (REPO-125 Phase 4)
        hot_rules_context = retriever.get_hot_rules_context(top_k=5)

        # Step 3: Generate answer with GPT-4o
        client = OpenAI()

        # Build conversation messages
        messages = []

        # Add conversation history if provided
        if request.conversation_history:
            for msg in request.conversation_history[-5:]:  # Last 5 messages
                messages.append(msg)

        # Add system message with context (including hot rules)
        system_message_parts = [
            "You are an expert code assistant helping developers understand a codebase.",
            "",
            "Use the following code snippets retrieved from the knowledge graph to answer the question accurately and concisely.",
            "",
            "**Retrieved Code Context:**",
            context,
        ]

        # Include hot rules if available
        if hot_rules_context:
            system_message_parts.extend([
                "",
                hot_rules_context,
            ])

        system_message_parts.extend([
            "",
            "**Instructions:**",
            "- Base your answer ONLY on the provided code context",
            "- Cite specific source numbers (e.g., \"As shown in Source 1...\")",
            "- If the context doesn't contain enough information, say so",
            "- Provide code examples from the sources when relevant",
            "- When suggesting improvements, consider the active code quality rules",
            "- Be concise but thorough",
            "- Format code using markdown code blocks",
        ])

        system_message = "\n".join(system_message_parts)

        messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": request.question})

        # Call OpenAI
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3,
            max_tokens=1000
        )

        answer = response.choices[0].message.content

        # Step 4: Generate follow-up questions
        follow_up_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Generate 2-3 relevant follow-up questions based on the conversation."},
                {"role": "user", "content": f"Question: {request.question}\nAnswer: {answer}"}
            ],
            temperature=0.5,
            max_tokens=150
        )

        follow_up_text = follow_up_response.choices[0].message.content
        follow_up_questions = [q.strip("- ").strip() for q in follow_up_text.split("\n") if q.strip()]

        # Convert sources
        sources = [_retrieval_result_to_code_entity(r) for r in retrieval_results[:5]]

        # Calculate confidence based on top similarity scores
        avg_similarity = sum(r.similarity_score for r in retrieval_results[:3]) / min(3, len(retrieval_results))
        confidence = min(avg_similarity + 0.1, 1.0)  # Boost slightly, cap at 1.0

        execution_time_ms = (time.time() - start_time) * 1000

        return CodeAskResponse(
            answer=answer,
            sources=sources,
            confidence=confidence,
            follow_up_questions=follow_up_questions[:3],
            execution_time_ms=execution_time_ms
        )

    except Exception as e:
        logger.error(f"Code Q&A error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Question answering failed: {str(e)}"
        )


@router.get(
    "/embeddings/status",
    response_model=EmbeddingsStatusResponse,
    summary="Get embeddings status",
    description="Check how many entities have vector embeddings",
    responses={
        200: {"description": "Status retrieved successfully"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_embeddings_status(
    user: ClerkUser = Depends(get_current_user),
    client: Neo4jClient = Depends(get_neo4j_client)
) -> EmbeddingsStatusResponse:
    """
    Get status of vector embeddings in the knowledge graph.

    Returns counts of total entities and how many have embeddings generated.
    """
    try:
        logger.info("Fetching embeddings status")

        # Count total entities
        total_query = """
        MATCH (n)
        WHERE n:Function OR n:Class OR n:File
        RETURN
            count(n) as total,
            count(CASE WHEN n:Function THEN 1 END) as functions,
            count(CASE WHEN n:Class THEN 1 END) as classes,
            count(CASE WHEN n:File THEN 1 END) as files
        """
        total_result = client.execute_query(total_query)[0]

        # Count entities with embeddings
        embedded_query = """
        MATCH (n)
        WHERE (n:Function OR n:Class OR n:File) AND n.embedding IS NOT NULL
        RETURN
            count(n) as embedded,
            count(CASE WHEN n:Function THEN 1 END) as functions_embedded,
            count(CASE WHEN n:Class THEN 1 END) as classes_embedded,
            count(CASE WHEN n:File THEN 1 END) as files_embedded
        """
        embedded_result = client.execute_query(embedded_query)[0]

        total_entities = total_result["total"]
        embedded_entities = embedded_result["embedded"]

        coverage = (embedded_entities / total_entities * 100) if total_entities > 0 else 0.0

        return EmbeddingsStatusResponse(
            total_entities=total_entities,
            embedded_entities=embedded_entities,
            embedding_coverage=round(coverage, 2),
            functions_embedded=embedded_result["functions_embedded"],
            classes_embedded=embedded_result["classes_embedded"],
            files_embedded=embedded_result["files_embedded"],
            last_generated=None,  # TODO: Track in metadata
            model_used="text-embedding-3-small"
        )

    except Exception as e:
        logger.error(f"Embeddings status error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve embeddings status: {str(e)}"
        )
