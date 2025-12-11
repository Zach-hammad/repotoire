"""Pydantic models for RAG API requests and responses."""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class CodeSearchRequest(BaseModel):
    """Request model for code search."""

    query: str = Field(
        ...,
        description="Natural language query about the codebase",
        min_length=3,
        max_length=500,
        examples=["How does authentication work?", "Find all functions that parse JSON"]
    )
    top_k: int = Field(
        default=10,
        description="Number of results to return",
        ge=1,
        le=50
    )
    entity_types: Optional[List[Literal["Function", "Class", "File"]]] = Field(
        default=None,
        description="Filter by entity types (Function, Class, File). If None, search all types."
    )
    include_related: bool = Field(
        default=True,
        description="Whether to include related entities via graph traversal"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "How does authentication work?",
                "top_k": 10,
                "entity_types": ["Function", "Class"],
                "include_related": True
            }
        }
    )


class CodeEntity(BaseModel):
    """Code entity in search results."""

    entity_type: str = Field(..., description="Type of entity (Function, Class, File)")
    qualified_name: str = Field(..., description="Fully qualified unique name")
    name: str = Field(..., description="Simple entity name")
    code: str = Field(..., description="Source code with context")
    docstring: Optional[str] = Field(default=None, description="Documentation string")
    similarity_score: float = Field(..., description="Semantic similarity score (0-1)", ge=0, le=1)
    file_path: str = Field(..., description="Source file path")
    line_start: int = Field(..., description="Starting line number", ge=1)
    line_end: int = Field(..., description="Ending line number", ge=1)
    relationships: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Related entities via graph relationships"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional entity metadata"
    )


class CodeSearchResponse(BaseModel):
    """Response model for code search."""

    results: List[CodeEntity] = Field(..., description="Search results ordered by relevance")
    total: int = Field(..., description="Total number of results returned", ge=0)
    query: str = Field(..., description="Original query")
    search_strategy: str = Field(
        ...,
        description="Search strategy used (vector, graph, hybrid)"
    )
    execution_time_ms: float = Field(..., description="Query execution time in milliseconds", ge=0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "results": [
                    {
                        "entity_type": "Function",
                        "qualified_name": "auth.py::authenticate:10",
                        "name": "authenticate",
                        "code": "def authenticate(username, password):\n    ...",
                        "docstring": "Authenticate user with credentials",
                        "similarity_score": 0.95,
                        "file_path": "src/auth.py",
                        "line_start": 10,
                        "line_end": 25,
                        "relationships": [
                            {"entity": "auth.py::validate_token:30", "relationship": "CALLS"}
                        ],
                        "metadata": {"complexity": 5}
                    }
                ],
                "total": 1,
                "query": "How does authentication work?",
                "search_strategy": "hybrid",
                "execution_time_ms": 125.5
            }
        }
    )


class CodeAskRequest(BaseModel):
    """Request model for code Q&A with LLM."""

    question: str = Field(
        ...,
        description="Natural language question about the codebase",
        min_length=10,
        max_length=1000,
        examples=[
            "How does the authentication system work?",
            "What are the main classes for parsing Python code?",
            "How do I add a new detector?"
        ]
    )
    top_k: int = Field(
        default=10,
        description="Number of code chunks to retrieve for context",
        ge=1,
        le=50
    )
    include_related: bool = Field(
        default=True,
        description="Whether to include related entities in context"
    )
    conversation_history: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Previous conversation messages for context"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "How does the authentication system work in this codebase?",
                "top_k": 10,
                "include_related": True,
                "conversation_history": [
                    {"role": "user", "content": "What frameworks are used?"},
                    {"role": "assistant", "content": "This codebase uses FastAPI..."}
                ]
            }
        }
    )


class CodeAskResponse(BaseModel):
    """Response model for code Q&A."""

    answer: str = Field(..., description="LLM-generated answer to the question")
    sources: List[CodeEntity] = Field(
        ...,
        description="Source code entities used to generate the answer"
    )
    confidence: float = Field(
        ...,
        description="Confidence score for the answer (0-1)",
        ge=0,
        le=1
    )
    follow_up_questions: List[str] = Field(
        default_factory=list,
        description="Suggested follow-up questions"
    )
    execution_time_ms: float = Field(..., description="Total execution time in milliseconds", ge=0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "answer": "The authentication system uses JWT tokens...",
                "sources": [
                    {
                        "entity_type": "Function",
                        "qualified_name": "auth.py::authenticate:10",
                        "name": "authenticate",
                        "code": "def authenticate(username, password):\n    ...",
                        "docstring": "Authenticate user",
                        "similarity_score": 0.95,
                        "file_path": "src/auth.py",
                        "line_start": 10,
                        "line_end": 25,
                        "relationships": [],
                        "metadata": {}
                    }
                ],
                "confidence": 0.92,
                "follow_up_questions": [
                    "How are JWT tokens validated?",
                    "What happens when authentication fails?"
                ],
                "execution_time_ms": 1250.5
            }
        }
    )


class EmbeddingsStatusResponse(BaseModel):
    """Response model for embeddings status."""

    total_entities: int = Field(..., description="Total code entities in graph", ge=0)
    embedded_entities: int = Field(..., description="Entities with embeddings", ge=0)
    embedding_coverage: float = Field(
        ...,
        description="Percentage of entities with embeddings (0-100)",
        ge=0,
        le=100
    )
    functions_embedded: int = Field(..., description="Functions with embeddings", ge=0)
    classes_embedded: int = Field(..., description="Classes with embeddings", ge=0)
    files_embedded: int = Field(..., description="Files with embeddings", ge=0)
    last_generated: Optional[str] = Field(
        default=None,
        description="ISO timestamp of last embedding generation"
    )
    model_used: str = Field(..., description="Embedding model name")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_entities": 1500,
                "embedded_entities": 1450,
                "embedding_coverage": 96.67,
                "functions_embedded": 850,
                "classes_embedded": 400,
                "files_embedded": 200,
                "last_generated": "2025-11-21T10:30:00Z",
                "model_used": "text-embedding-3-small"
            }
        }
    )


class ErrorResponse(BaseModel):
    """Standard error response model.

    All API errors follow this consistent format for easy client-side handling.
    The `error_code` field provides machine-readable codes for programmatic error handling.
    """

    error: str = Field(..., description="Error type/category (e.g., 'validation_error', 'not_found')")
    detail: str = Field(..., description="Human-readable error description")
    error_code: str = Field(..., description="Machine-readable error code for client handling")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "validation_error",
                "detail": "Query must be at least 3 characters long",
                "error_code": "VALIDATION_ERROR"
            }
        }
    )


class NotFoundError(BaseModel):
    """Error response for resources that don't exist."""

    error: str = Field(default="not_found", description="Error type")
    detail: str = Field(..., description="Description of what was not found")
    error_code: str = Field(default="NOT_FOUND", description="Error code")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "not_found",
                "detail": "Repository with ID '550e8400-e29b-41d4-a716-446655440000' not found",
                "error_code": "NOT_FOUND"
            }
        }
    )


class ForbiddenError(BaseModel):
    """Error response for permission denied scenarios."""

    error: str = Field(default="forbidden", description="Error type")
    detail: str = Field(..., description="Description of why access was denied")
    error_code: str = Field(default="FORBIDDEN", description="Error code")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "forbidden",
                "detail": "You do not have permission to access this repository",
                "error_code": "FORBIDDEN"
            }
        }
    )


class UnauthorizedError(BaseModel):
    """Error response for authentication failures."""

    error: str = Field(default="unauthorized", description="Error type")
    detail: str = Field(..., description="Description of authentication failure")
    error_code: str = Field(default="UNAUTHORIZED", description="Error code")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "unauthorized",
                "detail": "Invalid or expired authentication token",
                "error_code": "UNAUTHORIZED"
            }
        }
    )


class RateLimitError(BaseModel):
    """Error response for rate limit exceeded scenarios."""

    error: str = Field(default="rate_limit_exceeded", description="Error type")
    detail: str = Field(..., description="Description of rate limit")
    error_code: str = Field(default="RATE_LIMIT_EXCEEDED", description="Error code")
    retry_after: int = Field(..., description="Seconds until rate limit resets", ge=0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "rate_limit_exceeded",
                "detail": "API rate limit exceeded. Try again in 60 seconds.",
                "error_code": "RATE_LIMIT_EXCEEDED",
                "retry_after": 60
            }
        }
    )


class ValidationErrorDetail(BaseModel):
    """Individual validation error detail."""

    loc: List[str | int] = Field(..., description="Location of the error (path to field)")
    msg: str = Field(..., description="Human-readable error message")
    type: str = Field(..., description="Error type identifier")


class ValidationErrorResponse(BaseModel):
    """Validation error response (HTTP 422).

    Returned when request body fails Pydantic validation.
    """

    detail: List[ValidationErrorDetail] = Field(..., description="List of validation errors")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "detail": [
                    {
                        "loc": ["body", "repository_id"],
                        "msg": "field required",
                        "type": "value_error.missing"
                    },
                    {
                        "loc": ["body", "top_k"],
                        "msg": "ensure this value is less than or equal to 50",
                        "type": "value_error.number.not_le"
                    }
                ]
            }
        }
    )


class ConflictError(BaseModel):
    """Error response for resource conflicts."""

    error: str = Field(default="conflict", description="Error type")
    detail: str = Field(..., description="Description of the conflict")
    error_code: str = Field(default="CONFLICT", description="Error code")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "conflict",
                "detail": "An analysis is already in progress for this repository",
                "error_code": "ANALYSIS_IN_PROGRESS"
            }
        }
    )


class PreviewCheck(BaseModel):
    """Individual check result from fix preview execution."""

    name: str = Field(..., description="Check name: 'syntax', 'import', 'type', 'tests'")
    passed: bool = Field(..., description="Whether the check passed")
    message: str = Field(..., description="Human-readable result message")
    duration_ms: int = Field(..., description="Check execution time in milliseconds", ge=0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "import",
                "passed": False,
                "message": "ImportError: No module named 'utilz'. Did you mean 'utils'?",
                "duration_ms": 150
            }
        }
    )


class PreviewResult(BaseModel):
    """Result of running fix preview in sandbox."""

    success: bool = Field(..., description="Overall preview success (all checks passed)")
    stdout: str = Field(default="", description="Standard output from execution")
    stderr: str = Field(default="", description="Standard error from execution")
    duration_ms: int = Field(..., description="Total execution time in milliseconds", ge=0)
    checks: List[PreviewCheck] = Field(
        default_factory=list,
        description="Individual check results"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if preview failed to run"
    )
    cached_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp if result is from cache"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": False,
                "stdout": "",
                "stderr": "ImportError: No module named 'utilz'",
                "duration_ms": 850,
                "checks": [
                    {
                        "name": "syntax",
                        "passed": True,
                        "message": "Syntax valid",
                        "duration_ms": 5
                    },
                    {
                        "name": "import",
                        "passed": False,
                        "message": "ImportError: No module named 'utilz'. Did you mean 'utils'?",
                        "duration_ms": 150
                    }
                ],
                "error": None,
                "cached_at": None
            }
        }
    )
