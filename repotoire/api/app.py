"""FastAPI application for Repotoire RAG API."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from repotoire.api.routes import code, historical, fixes, analytics
from repotoire.api.models import ErrorResponse
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Repotoire RAG API")
    yield
    # Shutdown
    logger.info("Shutting down Repotoire RAG API")


# Create FastAPI app
app = FastAPI(
    title="Repotoire RAG API",
    description="""
    # Repotoire Code Intelligence API

    Graph-powered code question answering using Retrieval Augmented Generation (RAG).

    ## Features

    - **Semantic Code Search**: Find code using natural language queries
    - **Code Q&A**: Ask questions and get AI-powered answers with source citations
    - **Graph-Aware**: Leverages code relationships (imports, calls, inheritance)
    - **Hybrid Retrieval**: Combines vector embeddings + graph traversal

    ## Authentication

    Currently no authentication required (development mode).

    ## Rate Limits

    No rate limits currently enforced.
    """,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# CORS middleware for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(code.router)
app.include_router(historical.router)
app.include_router(fixes.router)
app.include_router(analytics.router)


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Repotoire RAG API",
        "version": "0.1.0",
        "description": "Graph-powered code intelligence with RAG",
        "docs": "/docs",
        "endpoints": {
            "search": "POST /api/v1/code/search",
            "ask": "POST /api/v1/code/ask",
            "embeddings_status": "GET /api/v1/code/embeddings/status",
            "ingest_git": "POST /api/v1/historical/ingest-git",
            "query_history": "POST /api/v1/historical/query",
            "entity_timeline": "POST /api/v1/historical/timeline",
            "fixes": "GET /api/v1/fixes",
            "analytics": "GET /api/v1/analytics/summary"
        }
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error",
            detail=str(exc),
            error_code="INTERNAL_ERROR"
        ).model_dump()
    )


if __name__ == "__main__":
    import uvicorn

    # Run with: python -m repotoire.api.app
    uvicorn.run(
        "repotoire.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
