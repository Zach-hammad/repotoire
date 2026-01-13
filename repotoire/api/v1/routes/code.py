"""API routes for code Q&A and search."""

import asyncio
import os
import time
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from openai import AsyncOpenAI
from slowapi import Limiter
from slowapi.util import get_remote_address

from repotoire.api.models import (
    ArchitectureResponse,
    CodeSearchRequest,
    CodeSearchResponse,
    CodeAskRequest,
    CodeAskResponse,
    EmbeddingsStatusResponse,
    CodeEntity,
    ErrorResponse,
    ModuleStats,
)
from repotoire.api.shared.auth import ClerkUser, get_current_user_or_api_key
from repotoire.api.shared.middleware.usage import enforce_feature_for_api
from repotoire.ai.retrieval import GraphRAGRetriever, RetrievalResult
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.ai.compression import (
    EmbeddingCompressor,
    TenantCompressor,
    estimate_memory_savings,
    DEFAULT_TARGET_DIMS,
)
from repotoire.db.models import Organization
from repotoire.graph.base import DatabaseClient
from repotoire.graph.tenant_factory import get_factory
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Rate limiter for code AI endpoints (expensive operations)
# Pro tier: 60/minute, Free tier: 10/minute
code_ai_limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)


def _handle_background_task_error(task: asyncio.Task) -> None:
    """Handle exceptions from background tasks.

    This callback logs any exceptions that occur in fire-and-forget
    background tasks, preventing them from being silently swallowed.

    Args:
        task: The completed asyncio Task to check for exceptions.
    """
    try:
        # This raises the exception if the task failed
        exc = task.exception()
        if exc is not None:
            task_name = task.get_name()
            logger.error(
                f"Background task '{task_name}' failed with exception: {exc}",
                exc_info=exc,
            )
    except asyncio.CancelledError:
        # Task was cancelled, which is fine
        pass
    except asyncio.InvalidStateError:
        # Task is not done yet (shouldn't happen in done callback)
        pass

router = APIRouter(prefix="/code", tags=["code"])


def get_graph_client_for_org(org: Organization) -> DatabaseClient:
    """Get tenant-isolated graph client for the organization.

    Uses the tenant factory to connect to the correct FalkorDB instance
    with proper multi-tenant isolation.
    """
    factory = get_factory()
    return factory.get_client(org_id=org.id, org_slug=org.slug)


def get_embedder() -> CodeEmbedder:
    """Get CodeEmbedder instance.

    Respects REPOTOIRE_EMBEDDING_BACKEND env var to force a specific backend.
    """
    import os
    backend = os.getenv("REPOTOIRE_EMBEDDING_BACKEND", "auto")
    return CodeEmbedder(backend=backend)


def get_retriever_for_org(
    org: Organization,
    embedder: CodeEmbedder,
) -> GraphRAGRetriever:
    """Get GraphRAGRetriever instance for an organization.

    Creates a tenant-isolated retriever using the org's graph client.
    """
    graph_client = get_graph_client_for_org(org)
    return GraphRAGRetriever(
        client=graph_client,
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
    description="Search for code entities using hybrid vector + graph search. Requires Pro or Enterprise subscription.",
    responses={
        200: {"description": "Search results returned successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@code_ai_limiter.limit("60/minute;500/hour")
async def search_code(
    request: CodeSearchRequest,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
    embedder: CodeEmbedder = Depends(get_embedder),
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

    # Create org-isolated retriever
    retriever = get_retriever_for_org(org, embedder)

    try:
        logger.info(f"Code search request: {request.query}", extra={"org_id": str(org.id)})

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
            detail="Code search failed. Please try again."
        )


@router.post(
    "/ask",
    response_model=CodeAskResponse,
    summary="Ask questions about codebase",
    description="Get AI-powered answers to questions about the codebase using RAG. Requires Pro or Enterprise subscription.",
    responses={
        200: {"description": "Answer generated successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@code_ai_limiter.limit("30/minute;200/hour")
async def ask_code_question(
    request: CodeAskRequest,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
    embedder: CodeEmbedder = Depends(get_embedder),
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

    # Create org-isolated retriever
    retriever = get_retriever_for_org(org, embedder)

    try:
        logger.info(f"Code Q&A request: {request.question}", extra={"org_id": str(org.id)})

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

        # Call OpenAI - run main answer and follow-up questions in parallel
        client = AsyncOpenAI()

        async def generate_answer():
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3,
                max_tokens=1000
            )
            return response.choices[0].message.content

        async def generate_follow_ups():
            # Base follow-ups on question + context (not answer) to enable parallelism
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Based on the code context, suggest 2-3 follow-up questions the user might want to ask. Just list the questions, one per line."},
                    {"role": "user", "content": f"Question: {request.question}\n\nCode context summary: {context[:1000]}"}
                ],
                temperature=0.5,
                max_tokens=100
            )
            text = response.choices[0].message.content
            return [q.strip("- ").strip() for q in text.split("\n") if q.strip()]

        # Run both in parallel
        answer, follow_up_questions = await asyncio.gather(
            generate_answer(),
            generate_follow_ups()
        )

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
            detail="Question answering failed. Please try again."
        )


@router.get(
    "/embeddings/status",
    response_model=EmbeddingsStatusResponse,
    summary="Get embeddings status",
    description="Check how many entities have vector embeddings. Requires Pro or Enterprise subscription.",
    responses={
        200: {"description": "Status retrieved successfully"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_embeddings_status(
    org: Organization = Depends(enforce_feature_for_api("api_access")),
) -> EmbeddingsStatusResponse:
    """
    Get status of vector embeddings in the knowledge graph.

    Returns counts of total entities and how many have embeddings generated.
    """
    # Get org-isolated graph client
    client = get_graph_client_for_org(org)

    try:
        logger.info("Fetching embeddings status", extra={"org_id": str(org.id)})

        # Count total entities
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        total_query = """
        MATCH (n)
        WHERE 'Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)
        RETURN
            count(n) as total,
            count(CASE WHEN 'Function' IN labels(n) THEN 1 END) as functions,
            count(CASE WHEN 'Class' IN labels(n) THEN 1 END) as classes,
            count(CASE WHEN 'File' IN labels(n) THEN 1 END) as files
        """
        total_results = client.execute_query(total_query)
        if not total_results:
            # No results - return empty status
            return EmbeddingsStatusResponse(
                total_entities=0,
                embedded_entities=0,
                embedding_coverage=0.0,
                functions_embedded=0,
                classes_embedded=0,
                files_embedded=0,
                last_generated=None,
                model_used="text-embedding-3-small"
            )
        total_result = total_results[0]

        # Count entities with embeddings
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        embedded_query = """
        MATCH (n)
        WHERE ('Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)) AND n.embedding IS NOT NULL
        RETURN
            count(n) as embedded,
            count(CASE WHEN 'Function' IN labels(n) THEN 1 END) as functions_embedded,
            count(CASE WHEN 'Class' IN labels(n) THEN 1 END) as classes_embedded,
            count(CASE WHEN 'File' IN labels(n) THEN 1 END) as files_embedded
        """
        embedded_results = client.execute_query(embedded_query)
        if not embedded_results:
            embedded_result = {"embedded": 0, "functions_embedded": 0, "classes_embedded": 0, "files_embedded": 0}
        else:
            embedded_result = embedded_results[0]

        total_entities = total_result.get("total", 0)
        embedded_entities = embedded_result.get("embedded", 0)

        coverage = (embedded_entities / total_entities * 100) if total_entities > 0 else 0.0

        return EmbeddingsStatusResponse(
            total_entities=total_entities,
            embedded_entities=embedded_entities,
            embedding_coverage=round(coverage, 2),
            functions_embedded=embedded_result.get("functions_embedded", 0),
            classes_embedded=embedded_result.get("classes_embedded", 0),
            files_embedded=embedded_result.get("files_embedded", 0),
            last_generated=None,  # TODO: Track in metadata
            model_used="text-embedding-3-small"
        )

    except Exception as e:
        logger.error(f"Embeddings status error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve embeddings status."
        )
    finally:
        client.close()


async def _regenerate_embeddings_task(org_id: UUID, org_slug: str, batch_size: int = 500):
    """Background task to regenerate embeddings."""
    import os
    import sys
    import traceback

    print(f"[EMBED] Starting background embedding regeneration for org {org_id}", flush=True)

    factory = get_factory()
    client = factory.get_client(org_id=org_id, org_slug=org_slug)

    try:
        # Initialize DeepInfra embedder
        print("[EMBED] Initializing DeepInfra embedder...", flush=True)
        embedder = CodeEmbedder(backend="deepinfra")
        print(f"[EMBED] Embedder initialized: {embedder.resolved_backend}, {embedder.dimensions} dims", flush=True)

        # Get all entities that need embeddings
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        print("[EMBED] Querying entities...", flush=True)
        entities = client.execute_query("""
            MATCH (n)
            WHERE 'Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)
            RETURN n.qualified_name as qname, n.name as name,
                   n.code as code, n.docstring as docstring
        """)

        total = len(entities)
        processed = 0
        print(f"[EMBED] Found {total} entities to embed", flush=True)

        # Process in batches
        for i in range(0, total, batch_size):
            batch = entities[i:i+batch_size]
            texts = []
            qnames = []

            for e in batch:
                # Build embedding text from entity
                text_parts = [e.get("name", "")]
                if e.get("docstring"):
                    text_parts.append(e["docstring"])
                if e.get("code"):
                    text_parts.append(e["code"][:1000])  # Limit code length
                texts.append("\n".join(text_parts))
                qnames.append(e["qname"])

            # Generate embeddings
            embeddings = embedder.embed_batch(texts)

            # Update in graph using batch UNWIND operation (50-100x faster)
            # Use UNWIND to batch all updates in a single query
            updates_data = [
                {
                    "qname": qname,
                    "embedding": emb,
                    "dims": len(emb),
                }
                for qname, emb in zip(qnames, embeddings)
            ]

            try:
                # Single UNWIND query for all entity types
                client.execute_query("""
                    UNWIND $updates AS u
                    MATCH (n {qualifiedName: u.qname})
                    SET n.embedding = vecf32(u.embedding),
                        n.embedding_backend = 'deepinfra',
                        n.embedding_dims = u.dims,
                        n.embedding_model = 'Qwen/Qwen3-Embedding-8B'
                """, {"updates": updates_data})
            except Exception as batch_err:
                # Fallback to individual updates if batch fails
                print(f"[EMBED] Batch update failed, falling back to individual: {batch_err}", flush=True)
                for qname, emb in zip(qnames, embeddings):
                    client.execute_query("""
                        MATCH (n {qualifiedName: $qname})
                        SET n.embedding = vecf32($embedding),
                            n.embedding_backend = 'deepinfra',
                            n.embedding_dims = $dims,
                            n.embedding_model = 'Qwen/Qwen3-Embedding-8B'
                    """, {"qname": qname, "embedding": emb, "dims": len(emb)})

            processed += len(batch)
            print(f"[EMBED] Processed {processed}/{total} embeddings", flush=True)

            # Yield to event loop to allow health checks
            await asyncio.sleep(0.1)

        print(f"[EMBED] Completed embedding regeneration: {processed} entities", flush=True)

    except Exception as e:
        print(f"[EMBED] ERROR: {e}", flush=True)
        traceback.print_exc()
    finally:
        client.close()


@router.post(
    "/embeddings/regenerate",
    summary="Regenerate embeddings with Qwen3",
    description="Regenerate all embeddings using DeepInfra Qwen3-Embedding-8B. Returns immediately and runs in background.",
    responses={
        200: {"description": "Embedding regeneration started"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        503: {"model": ErrorResponse, "description": "DeepInfra API key not configured"}
    }
)
async def regenerate_embeddings(
    batch_size: int = 500,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
):
    """
    Regenerate all embeddings using DeepInfra Qwen3-Embedding-8B (4096 dims).

    This starts the regeneration in the background and returns immediately.
    Check /embeddings/status to monitor progress.
    """
    import os

    # Only allow if DEEPINFRA_API_KEY is set
    if not os.getenv("DEEPINFRA_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPINFRA_API_KEY not configured"
        )

    # Start background task with error handling callback
    task = asyncio.create_task(
        _regenerate_embeddings_task(org.id, org.slug, batch_size),
        name=f"regenerate_embeddings_{org.slug}",
    )
    task.add_done_callback(_handle_background_task_error)

    return {
        "status": "started",
        "message": "Embedding regeneration started in background. Check /embeddings/status to monitor progress.",
        "backend": "deepinfra",
        "model": "Qwen/Qwen3-Embedding-8B",
        "dimensions": 4096
    }


async def _compress_embeddings_task(
    org_id: UUID,
    org_slug: str,
    target_dims: int = DEFAULT_TARGET_DIMS,
    sample_size: int = 5000,
):
    """Background task to fit PCA and compress embeddings."""
    import traceback
    from pathlib import Path
    import numpy as np

    print(f"[COMPRESS] Starting embedding compression for org {org_id}", flush=True)
    print(f"[COMPRESS] Target dimensions: {target_dims}", flush=True)

    factory = get_factory()
    client = factory.get_client(org_id=org_id, org_slug=org_slug)

    try:
        # Step 1: Sample embeddings for PCA fitting
        print(f"[COMPRESS] Sampling up to {sample_size} embeddings for PCA fitting...", flush=True)

        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        sample_query = """
        MATCH (n)
        WHERE ('Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)) AND n.embedding IS NOT NULL
        RETURN n.qualified_name as qname, n.embedding as embedding
        LIMIT $limit
        """
        samples = client.execute_query(sample_query, {"limit": sample_size})

        if len(samples) < 100:
            print(f"[COMPRESS] ERROR: Only {len(samples)} embeddings found. Need at least 100.", flush=True)
            return

        embeddings = [s["embedding"] for s in samples]
        source_dims = len(embeddings[0])
        print(f"[COMPRESS] Found {len(embeddings)} embeddings with {source_dims} dimensions", flush=True)

        # Step 2: Fit PCA compressor
        print("[COMPRESS] Fitting PCA model...", flush=True)
        model_dir = Path.home() / ".repotoire" / "compression_models"
        model_path = model_dir / f"{org_slug}_pca.pkl"

        compressor = EmbeddingCompressor(
            target_dims=target_dims,
            model_path=model_path,
        )
        compressor.fit(embeddings, save=True)
        print(f"[COMPRESS] PCA model fitted. Compression ratio: {compressor.compression_ratio:.1f}x", flush=True)

        # Step 3: Compress all embeddings and update graph
        print("[COMPRESS] Compressing and updating all embeddings...", flush=True)

        # Get all embeddings (not just sample)
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        all_query = """
        MATCH (n)
        WHERE ('Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)) AND n.embedding IS NOT NULL
        RETURN n.qualified_name as qname, n.embedding as embedding
        """
        all_entities = client.execute_query(all_query)
        total = len(all_entities)
        print(f"[COMPRESS] Processing {total} embeddings...", flush=True)

        # Process in batches
        batch_size = 500  # Increased for throughput
        processed = 0

        for i in range(0, total, batch_size):
            batch = all_entities[i:i+batch_size]

            # Get reduced embeddings (PCA only, keep as float for vector search)
            batch_embeddings = [e["embedding"] for e in batch]
            reduced_embeddings = compressor.get_reduced_embeddings_batch(batch_embeddings)

            # Update in graph using batch UNWIND operation (50-100x faster)
            updates_data = [
                {
                    "qname": entity["qname"],
                    "embedding": reduced,
                    "dims": target_dims,
                    "orig_dims": source_dims,
                }
                for entity, reduced in zip(batch, reduced_embeddings)
            ]
            try:
                client.execute_query("""
                    UNWIND $updates AS u
                    MATCH (n {qualified_name: u.qname})
                    SET n.embedding = vecf32(u.embedding),
                        n.embedding_compressed = true,
                        n.embedding_dims = u.dims,
                        n.embedding_original_dims = u.orig_dims
                """, {"updates": updates_data})
            except Exception as batch_err:
                # Fallback to individual updates if UNWIND fails
                print(f"[COMPRESS] Batch update failed, falling back to individual: {batch_err}", flush=True)
                for entity, reduced in zip(batch, reduced_embeddings):
                    client.execute_query("""
                        MATCH (n {qualified_name: $qname})
                        SET n.embedding = vecf32($embedding),
                            n.embedding_compressed = true,
                            n.embedding_dims = $dims,
                            n.embedding_original_dims = $orig_dims
                    """, {
                        "qname": entity["qname"],
                        "embedding": reduced,
                        "dims": target_dims,
                        "orig_dims": source_dims,
                    })

            processed += len(batch)
            if processed % 500 == 0:
                print(f"[COMPRESS] Processed {processed}/{total} embeddings", flush=True)

            # Yield to event loop
            await asyncio.sleep(0.05)

        # Step 4: Calculate and log savings
        savings = estimate_memory_savings(total, source_dims, target_dims)
        print(f"[COMPRESS] Compression complete!", flush=True)
        print(f"[COMPRESS] Entities: {total}", flush=True)
        print(f"[COMPRESS] Original: {savings['original_mb']:.1f} MB", flush=True)
        print(f"[COMPRESS] Compressed: {savings['reduced_only_mb']:.1f} MB", flush=True)
        print(f"[COMPRESS] Savings: {savings['savings_mb']:.1f} MB ({savings['savings_percent']:.1f}%)", flush=True)

    except Exception as e:
        print(f"[COMPRESS] ERROR: {e}", flush=True)
        traceback.print_exc()
    finally:
        client.close()


@router.post(
    "/embeddings/compress",
    summary="Compress embeddings with PCA",
    description="Fit PCA model on existing embeddings and compress them for memory savings. Returns immediately and runs in background.",
    responses={
        200: {"description": "Compression started"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
    }
)
async def compress_embeddings(
    target_dims: int = DEFAULT_TARGET_DIMS,
    sample_size: int = 5000,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
):
    """
    Compress embeddings using PCA dimensionality reduction.

    **What this does:**
    1. Samples existing embeddings to fit a PCA model
    2. Reduces dimensions from 4096 → 2048 (2x compression)
    3. Updates all embeddings in the graph

    **Memory savings:** ~50% reduction in embedding storage.

    The compression runs in the background. Check /embeddings/compression/status
    to monitor progress.
    """
    # Start background task with error handling callback
    task = asyncio.create_task(
        _compress_embeddings_task(org.id, org.slug, target_dims, sample_size),
        name=f"compress_embeddings_{org.slug}",
    )
    task.add_done_callback(_handle_background_task_error)

    # Calculate expected savings
    client = get_graph_client_for_org(org)
    try:
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        count_result = client.execute_query("""
            MATCH (n)
            WHERE ('Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)) AND n.embedding IS NOT NULL
            RETURN count(n) as count,
                   CASE WHEN n.embedding IS NOT NULL THEN size(n.embedding) ELSE 4096 END as dims
            LIMIT 1
        """)
        entity_count = count_result[0]["count"] if count_result else 0
        source_dims = count_result[0].get("dims", 4096) if count_result else 4096
    except Exception:
        entity_count = 0
        source_dims = 4096
    finally:
        client.close()

    savings = estimate_memory_savings(entity_count, source_dims, target_dims)

    return {
        "status": "started",
        "message": "Embedding compression started in background. Check /embeddings/compression/status to monitor progress.",
        "target_dims": target_dims,
        "source_dims": source_dims,
        "entity_count": entity_count,
        "expected_savings_mb": round(savings["savings_mb"], 2),
        "expected_savings_percent": round(savings["savings_percent"], 1),
    }


@router.get(
    "/embeddings/compression/status",
    summary="Get compression status",
    description="Check the status of embedding compression including memory savings.",
    responses={
        200: {"description": "Compression status retrieved successfully"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
    }
)
async def get_compression_status(
    org: Organization = Depends(enforce_feature_for_api("api_access")),
):
    """
    Get the current status of embedding compression.

    Returns information about:
    - How many embeddings are compressed
    - Current vs original dimensions
    - Estimated memory savings
    """
    client = get_graph_client_for_org(org)

    try:
        # Count compressed vs uncompressed
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        status_query = """
        MATCH (n)
        WHERE ('Function' IN labels(n) OR 'Class' IN labels(n) OR 'File' IN labels(n)) AND n.embedding IS NOT NULL
        RETURN
            count(n) as total,
            count(CASE WHEN n.embedding_compressed = true THEN 1 END) as compressed,
            avg(CASE WHEN n.embedding IS NOT NULL THEN size(n.embedding) ELSE null END) as avg_dims,
            max(n.embedding_original_dims) as original_dims
        """
        result = client.execute_query(status_query)

        if not result:
            return {
                "total_embeddings": 0,
                "compressed_embeddings": 0,
                "compression_coverage": 0.0,
                "current_dims": None,
                "original_dims": None,
                "savings_mb": 0,
                "savings_percent": 0,
            }

        row = result[0]
        total = row.get("total", 0)
        compressed = row.get("compressed", 0)
        avg_dims = row.get("avg_dims")
        original_dims = row.get("original_dims", 4096)

        # Calculate current dimensions (may be float from avg)
        current_dims = int(avg_dims) if avg_dims else None

        # Calculate savings if we have compressed embeddings
        if compressed > 0 and original_dims and current_dims:
            savings = estimate_memory_savings(compressed, original_dims, current_dims)
            savings_mb = savings["savings_mb"]
            savings_percent = savings["savings_percent"]
        else:
            savings_mb = 0
            savings_percent = 0

        return {
            "total_embeddings": total,
            "compressed_embeddings": compressed,
            "compression_coverage": round((compressed / total * 100) if total > 0 else 0, 1),
            "current_dims": current_dims,
            "original_dims": original_dims,
            "savings_mb": round(savings_mb, 2),
            "savings_percent": round(savings_percent, 1),
            "compression_ratio": f"{original_dims / current_dims:.1f}x" if current_dims and original_dims else None,
        }

    except Exception as e:
        logger.error(f"Compression status error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve compression status."
        )
    finally:
        client.close()


@router.get(
    "/architecture",
    response_model=ArchitectureResponse,
    summary="Get codebase architecture",
    description="Get an overview of the codebase architecture including modules, dependencies, and patterns. Requires Pro or Enterprise subscription.",
    responses={
        200: {"description": "Architecture overview retrieved successfully"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_architecture(
    depth: int = 2,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
) -> ArchitectureResponse:
    """
    Get codebase architecture overview.

    Returns module statistics, detected patterns, and dependencies.
    The depth parameter controls how deep into the directory structure to analyze.
    """
    # Get org-isolated graph client
    client = get_graph_client_for_org(org)

    try:
        logger.info("Fetching architecture overview", extra={"org_id": str(org.id), "depth": depth})

        # Query module/directory statistics from the graph
        # Group by directory path at the specified depth
        module_query = """
        MATCH (f:File)
        WITH f,
             split(f.path, '/') as parts
        WITH f,
             CASE WHEN size(parts) > $depth THEN
                 reduce(s='', i IN range(0, $depth - 1) | s + '/' + parts[i])
             ELSE
                 '/' + reduce(s='', p IN parts[0..-1] | s + '/' + p)
             END as module_path
        WITH module_path,
             count(f) as file_count,
             sum(CASE WHEN f.function_count IS NOT NULL THEN f.function_count ELSE 0 END) as total_functions,
             sum(CASE WHEN f.class_count IS NOT NULL THEN f.class_count ELSE 0 END) as total_classes
        WHERE module_path <> ''
        RETURN module_path, file_count, total_functions, total_classes
        ORDER BY file_count DESC
        LIMIT 50
        """

        module_results = client.execute_query(module_query, {"depth": depth})

        # Build modules dict
        modules: dict[str, ModuleStats] = {}
        for row in module_results:
            path = row.get("module_path", "unknown").lstrip("/")
            if path:
                modules[path] = ModuleStats(
                    file_count=row.get("file_count", 0),
                    functions=row.get("total_functions", 0),
                    classes=row.get("total_classes", 0),
                )

        # Query top-level dependencies (IMPORTS relationships)
        dep_query = """
        MATCH (f:File)-[:IMPORTS]->(m:Module)
        WHERE NOT m.name STARTS WITH '.'
        RETURN DISTINCT m.name as dependency
        ORDER BY m.name
        LIMIT 30
        """

        dep_results = client.execute_query(dep_query)
        dependencies = [row["dependency"] for row in dep_results if row.get("dependency")]

        # Detect patterns based on graph structure
        patterns: list[str] = []

        # Check for common patterns
        pattern_checks = [
            ("MATCH (c:Class)-[:INHERITS]->(:Class {name: 'BaseModel'}) RETURN count(c) as cnt",
             "Pydantic Models", 3),
            ("MATCH (f:File) WHERE f.path CONTAINS '/routes/' OR f.path CONTAINS '/api/' RETURN count(f) as cnt",
             "REST API", 5),
            ("MATCH (c:Class) WHERE c.name CONTAINS 'Repository' OR c.name CONTAINS 'DAO' RETURN count(c) as cnt",
             "Repository Pattern", 2),
            ("MATCH (f:File) WHERE f.path CONTAINS '/tests/' RETURN count(f) as cnt",
             "Test Suite", 5),
        ]

        for query, pattern_name, threshold in pattern_checks:
            try:
                result = client.execute_query(query)
                if result and result[0].get("cnt", 0) >= threshold:
                    patterns.append(pattern_name)
            except Exception:
                pass  # Skip pattern detection on query errors

        return ArchitectureResponse(
            modules=modules,
            patterns=patterns if patterns else None,
            dependencies=dependencies if dependencies else None,
        )

    except Exception as e:
        logger.error(f"Architecture retrieval error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve architecture overview."
        )
    finally:
        client.close()
