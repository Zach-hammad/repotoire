"""API routes for fix management."""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from repotoire.autofix.models import (
    FixProposal,
    FixStatus,
    FixConfidence,
    FixType,
)

router = APIRouter(prefix="/fixes", tags=["fixes"])

# In-memory storage for fixes (replace with database in production)
_fixes_store: dict[str, FixProposal] = {}
_comments_store: dict[str, list] = {}


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: List[dict]
    total: int
    page: int
    page_size: int
    has_more: bool


class FixComment(BaseModel):
    """A comment on a fix."""
    id: str
    fix_id: str
    author: str
    content: str
    created_at: datetime


class CommentCreate(BaseModel):
    """Request to create a comment."""
    content: str


class RejectRequest(BaseModel):
    """Request to reject a fix."""
    reason: str


class BatchRequest(BaseModel):
    """Request for batch operations."""
    ids: List[str]


class BatchRejectRequest(BatchRequest):
    """Request for batch reject."""
    reason: str


@router.get("")
async def list_fixes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[List[FixStatus]] = Query(None),
    confidence: Optional[List[FixConfidence]] = Query(None),
    fix_type: Optional[List[FixType]] = Query(None),
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_direction: str = "desc",
) -> PaginatedResponse:
    """List fixes with filters and pagination."""
    # Filter fixes
    fixes = list(_fixes_store.values())

    if status:
        fixes = [f for f in fixes if f.status in status]
    if confidence:
        fixes = [f for f in fixes if f.confidence in confidence]
    if fix_type:
        fixes = [f for f in fixes if f.fix_type in fix_type]
    if search:
        search_lower = search.lower()
        fixes = [
            f for f in fixes
            if search_lower in f.title.lower() or search_lower in f.description.lower()
        ]

    # Sort
    reverse = sort_direction == "desc"
    if sort_by == "created_at":
        fixes.sort(key=lambda f: f.created_at, reverse=reverse)
    elif sort_by == "confidence":
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        fixes.sort(key=lambda f: confidence_order.get(f.confidence.value, 0), reverse=reverse)
    elif sort_by == "status":
        fixes.sort(key=lambda f: f.status.value, reverse=reverse)

    # Paginate
    total = len(fixes)
    start = (page - 1) * page_size
    end = start + page_size
    items = fixes[start:end]

    return PaginatedResponse(
        items=[f.to_dict() for f in items],
        total=total,
        page=page,
        page_size=page_size,
        has_more=end < total,
    )


@router.get("/{fix_id}")
async def get_fix(fix_id: str) -> dict:
    """Get a specific fix by ID."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")
    return _fixes_store[fix_id].to_dict()


@router.post("/{fix_id}/approve")
async def approve_fix(fix_id: str) -> dict:
    """Approve a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix.status = FixStatus.APPROVED
    return {"data": fix.to_dict(), "success": True}


@router.post("/{fix_id}/reject")
async def reject_fix(fix_id: str, request: RejectRequest) -> dict:
    """Reject a fix with a reason."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix.status = FixStatus.REJECTED
    # Store rejection reason in comments
    comment_id = f"reject-{fix_id}-{datetime.utcnow().timestamp()}"
    if fix_id not in _comments_store:
        _comments_store[fix_id] = []
    _comments_store[fix_id].append({
        "id": comment_id,
        "fix_id": fix_id,
        "author": "System",
        "content": f"Rejected: {request.reason}",
        "created_at": datetime.utcnow().isoformat(),
    })

    return {"data": fix.to_dict(), "success": True}


@router.post("/{fix_id}/apply")
async def apply_fix(fix_id: str) -> dict:
    """Apply an approved fix to the codebase."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Fix must be approved before applying")

    # TODO: Actually apply the fix using the applicator
    # For now, just mark it as applied
    fix.status = FixStatus.APPLIED
    fix.applied_at = datetime.utcnow()

    return {"data": fix.to_dict(), "success": True}


@router.post("/{fix_id}/comment")
async def add_comment(fix_id: str, request: CommentCreate) -> dict:
    """Add a comment to a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    comment_id = f"comment-{fix_id}-{datetime.utcnow().timestamp()}"
    comment = {
        "id": comment_id,
        "fix_id": fix_id,
        "author": "User",  # TODO: Get from auth
        "content": request.content,
        "created_at": datetime.utcnow().isoformat(),
    }

    if fix_id not in _comments_store:
        _comments_store[fix_id] = []
    _comments_store[fix_id].append(comment)

    return {"data": comment, "success": True}


@router.get("/{fix_id}/comments")
async def get_comments(fix_id: str, limit: int = Query(25, ge=1, le=100)) -> List[dict]:
    """Get comments for a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    comments = _comments_store.get(fix_id, [])
    return comments[:limit]


@router.post("/batch/approve")
async def batch_approve(request: BatchRequest) -> dict:
    """Batch approve multiple fixes."""
    approved = 0
    for fix_id in request.ids:
        if fix_id in _fixes_store:
            fix = _fixes_store[fix_id]
            if fix.status == FixStatus.PENDING:
                fix.status = FixStatus.APPROVED
                approved += 1

    return {"data": {"approved": approved}, "success": True}


@router.post("/batch/reject")
async def batch_reject(request: BatchRejectRequest) -> dict:
    """Batch reject multiple fixes."""
    rejected = 0
    for fix_id in request.ids:
        if fix_id in _fixes_store:
            fix = _fixes_store[fix_id]
            if fix.status == FixStatus.PENDING:
                fix.status = FixStatus.REJECTED
                rejected += 1
                # Add rejection comment
                comment_id = f"reject-{fix_id}-{datetime.utcnow().timestamp()}"
                if fix_id not in _comments_store:
                    _comments_store[fix_id] = []
                _comments_store[fix_id].append({
                    "id": comment_id,
                    "fix_id": fix_id,
                    "author": "System",
                    "content": f"Batch rejected: {request.reason}",
                    "created_at": datetime.utcnow().isoformat(),
                })

    return {"data": {"rejected": rejected}, "success": True}


# Helper function to add fixes from the engine
def add_fix_to_store(fix: FixProposal) -> None:
    """Add a fix to the in-memory store."""
    _fixes_store[fix.id] = fix


def get_all_fixes() -> List[FixProposal]:
    """Get all fixes from the store."""
    return list(_fixes_store.values())


def clear_fixes_store() -> None:
    """Clear the fixes store (for testing)."""
    _fixes_store.clear()
    _comments_store.clear()
