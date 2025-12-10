"""Repository for Fix and FixComment database operations.

This module provides the repository pattern implementation for managing
fix proposals and comments in the database.
"""

from datetime import datetime
from typing import List, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.db.models.fix import Fix, FixComment, FixConfidence, FixStatus, FixType
from repotoire.db.models.analysis import AnalysisRun


class FixNotFoundError(Exception):
    """Raised when a fix is not found."""

    def __init__(self, fix_id: UUID):
        self.fix_id = fix_id
        super().__init__(f"Fix not found: {fix_id}")


class InvalidStatusTransitionError(Exception):
    """Raised when an invalid status transition is attempted."""

    def __init__(self, current_status: FixStatus, new_status: FixStatus):
        self.current_status = current_status
        self.new_status = new_status
        super().__init__(
            f"Invalid status transition: {current_status.value} -> {new_status.value}"
        )


# Valid status transitions
VALID_STATUS_TRANSITIONS = {
    FixStatus.PENDING: {FixStatus.APPROVED, FixStatus.REJECTED},
    FixStatus.APPROVED: {FixStatus.APPLIED, FixStatus.REJECTED, FixStatus.FAILED},
    FixStatus.REJECTED: {FixStatus.PENDING},  # Allow re-review
    FixStatus.APPLIED: set(),  # Terminal state
    FixStatus.FAILED: {FixStatus.PENDING, FixStatus.REJECTED},  # Allow retry
}


class FixRepository:
    """Repository for Fix and FixComment operations."""

    def __init__(self, db: AsyncSession):
        """Initialize the repository.

        Args:
            db: Async database session
        """
        self.db = db

    async def create(
        self,
        analysis_run_id: UUID,
        file_path: str,
        original_code: str,
        fixed_code: str,
        title: str,
        description: str,
        explanation: str,
        fix_type: FixType,
        confidence: FixConfidence,
        confidence_score: float,
        finding_id: Optional[UUID] = None,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        evidence: Optional[dict] = None,
        validation_data: Optional[dict] = None,
    ) -> Fix:
        """Create a new fix proposal.

        Args:
            analysis_run_id: ID of the analysis run
            file_path: Path to the file being modified
            original_code: Original code being replaced
            fixed_code: Proposed fixed code
            title: Short title for the fix
            description: Detailed description
            explanation: AI-generated rationale
            fix_type: Type of fix
            confidence: Confidence level
            confidence_score: Numeric confidence score (0-1)
            finding_id: Optional ID of the finding being fixed
            line_start: Starting line number
            line_end: Ending line number
            evidence: Evidence supporting the fix
            validation_data: Validation results

        Returns:
            The created Fix instance
        """
        fix = Fix(
            analysis_run_id=analysis_run_id,
            finding_id=finding_id,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            original_code=original_code,
            fixed_code=fixed_code,
            title=title,
            description=description,
            explanation=explanation,
            fix_type=fix_type,
            confidence=confidence,
            confidence_score=confidence_score,
            status=FixStatus.PENDING,
            evidence=evidence,
            validation_data=validation_data,
        )
        self.db.add(fix)
        await self.db.commit()
        await self.db.refresh(fix)
        return fix

    async def get_by_id(
        self,
        fix_id: UUID,
        include_comments: bool = False,
    ) -> Optional[Fix]:
        """Get a fix by ID.

        Args:
            fix_id: The fix ID
            include_comments: Whether to eagerly load comments

        Returns:
            The Fix instance or None if not found
        """
        query = select(Fix).where(Fix.id == fix_id)
        if include_comments:
            query = query.options(selectinload(Fix.comments))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id_or_raise(
        self,
        fix_id: UUID,
        include_comments: bool = False,
    ) -> Fix:
        """Get a fix by ID or raise an error.

        Args:
            fix_id: The fix ID
            include_comments: Whether to eagerly load comments

        Returns:
            The Fix instance

        Raises:
            FixNotFoundError: If the fix is not found
        """
        fix = await self.get_by_id(fix_id, include_comments)
        if fix is None:
            raise FixNotFoundError(fix_id)
        return fix

    async def get_by_analysis_run(
        self,
        analysis_run_id: UUID,
        status: Optional[FixStatus] = None,
        include_comments: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Fix]:
        """Get fixes for an analysis run.

        Args:
            analysis_run_id: The analysis run ID
            status: Optional status filter
            include_comments: Whether to eagerly load comments
            limit: Maximum number of fixes to return
            offset: Number of fixes to skip

        Returns:
            List of Fix instances
        """
        query = (
            select(Fix)
            .where(Fix.analysis_run_id == analysis_run_id)
            .order_by(Fix.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            query = query.where(Fix.status == status)
        if include_comments:
            query = query.options(selectinload(Fix.comments))
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_by_finding(
        self,
        finding_id: UUID,
        include_comments: bool = False,
    ) -> Sequence[Fix]:
        """Get fixes for a specific finding.

        Args:
            finding_id: The finding ID
            include_comments: Whether to eagerly load comments

        Returns:
            List of Fix instances
        """
        query = (
            select(Fix)
            .where(Fix.finding_id == finding_id)
            .order_by(Fix.created_at.desc())
        )
        if include_comments:
            query = query.options(selectinload(Fix.comments))
        result = await self.db.execute(query)
        return result.scalars().all()

    async def search(
        self,
        analysis_run_id: Optional[UUID] = None,
        repository_id: Optional[UUID] = None,
        status: Optional[List[FixStatus]] = None,
        confidence: Optional[List[FixConfidence]] = None,
        fix_type: Optional[List[FixType]] = None,
        file_path: Optional[str] = None,
        search_text: Optional[str] = None,
        include_comments: bool = False,
        sort_by: str = "created_at",
        sort_direction: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[Sequence[Fix], int]:
        """Search fixes with filters and pagination.

        Args:
            analysis_run_id: Filter by analysis run
            repository_id: Filter by repository (via analysis_run)
            status: Filter by status(es)
            confidence: Filter by confidence level(s)
            fix_type: Filter by fix type(s)
            file_path: Filter by file path (partial match)
            search_text: Search in title and description
            include_comments: Whether to eagerly load comments
            sort_by: Field to sort by
            sort_direction: Sort direction (asc/desc)
            limit: Maximum results
            offset: Number to skip

        Returns:
            Tuple of (list of fixes, total count)
        """
        # Base query
        query = select(Fix)
        count_query = select(func.count()).select_from(Fix)

        # Apply repository filter (requires join with analysis_runs)
        if repository_id:
            query = query.join(AnalysisRun, Fix.analysis_run_id == AnalysisRun.id).where(
                AnalysisRun.repository_id == repository_id
            )
            count_query = count_query.join(AnalysisRun, Fix.analysis_run_id == AnalysisRun.id).where(
                AnalysisRun.repository_id == repository_id
            )

        # Apply filters
        if analysis_run_id:
            query = query.where(Fix.analysis_run_id == analysis_run_id)
            count_query = count_query.where(Fix.analysis_run_id == analysis_run_id)

        if status:
            query = query.where(Fix.status.in_(status))
            count_query = count_query.where(Fix.status.in_(status))

        if confidence:
            query = query.where(Fix.confidence.in_(confidence))
            count_query = count_query.where(Fix.confidence.in_(confidence))

        if fix_type:
            query = query.where(Fix.fix_type.in_(fix_type))
            count_query = count_query.where(Fix.fix_type.in_(fix_type))

        if file_path:
            query = query.where(Fix.file_path.ilike(f"%{file_path}%"))
            count_query = count_query.where(Fix.file_path.ilike(f"%{file_path}%"))

        if search_text:
            search_pattern = f"%{search_text}%"
            query = query.where(
                (Fix.title.ilike(search_pattern)) | (Fix.description.ilike(search_pattern))
            )
            count_query = count_query.where(
                (Fix.title.ilike(search_pattern)) | (Fix.description.ilike(search_pattern))
            )

        # Get total count
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # Apply sorting
        sort_column = getattr(Fix, sort_by, Fix.created_at)
        if sort_direction == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # Apply pagination
        query = query.limit(limit).offset(offset)

        # Load comments if requested
        if include_comments:
            query = query.options(selectinload(Fix.comments))

        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def update_status(
        self,
        fix_id: UUID,
        new_status: FixStatus,
        validate_transition: bool = True,
    ) -> Fix:
        """Update the status of a fix.

        Args:
            fix_id: The fix ID
            new_status: The new status
            validate_transition: Whether to validate the status transition

        Returns:
            The updated Fix instance

        Raises:
            FixNotFoundError: If the fix is not found
            InvalidStatusTransitionError: If the transition is invalid
        """
        fix = await self.get_by_id_or_raise(fix_id)

        if validate_transition:
            valid_transitions = VALID_STATUS_TRANSITIONS.get(fix.status, set())
            if new_status not in valid_transitions:
                raise InvalidStatusTransitionError(fix.status, new_status)

        fix.status = new_status
        if new_status == FixStatus.APPLIED:
            fix.applied_at = datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(fix)
        return fix

    async def update(
        self,
        fix_id: UUID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        fixed_code: Optional[str] = None,
        validation_data: Optional[dict] = None,
    ) -> Fix:
        """Update fix fields.

        Args:
            fix_id: The fix ID
            title: New title
            description: New description
            fixed_code: New fixed code
            validation_data: New validation data

        Returns:
            The updated Fix instance

        Raises:
            FixNotFoundError: If the fix is not found
        """
        fix = await self.get_by_id_or_raise(fix_id)

        if title is not None:
            fix.title = title
        if description is not None:
            fix.description = description
        if fixed_code is not None:
            fix.fixed_code = fixed_code
        if validation_data is not None:
            fix.validation_data = validation_data

        await self.db.commit()
        await self.db.refresh(fix)
        return fix

    async def delete(self, fix_id: UUID) -> bool:
        """Delete a fix.

        Args:
            fix_id: The fix ID

        Returns:
            True if the fix was deleted, False if not found
        """
        fix = await self.get_by_id(fix_id)
        if fix is None:
            return False
        await self.db.delete(fix)
        await self.db.commit()
        return True

    async def batch_update_status(
        self,
        fix_ids: List[UUID],
        new_status: FixStatus,
        validate_transition: bool = True,
    ) -> tuple[int, List[str]]:
        """Batch update the status of multiple fixes.

        Args:
            fix_ids: List of fix IDs
            new_status: The new status
            validate_transition: Whether to validate transitions

        Returns:
            Tuple of (number processed, list of error messages)
        """
        processed = 0
        errors = []

        for fix_id in fix_ids:
            try:
                await self.update_status(fix_id, new_status, validate_transition)
                processed += 1
            except FixNotFoundError:
                errors.append(f"Fix {fix_id} not found")
            except InvalidStatusTransitionError as e:
                errors.append(f"Fix {fix_id}: {e}")

        return processed, errors

    # Comment methods

    async def add_comment(
        self,
        fix_id: UUID,
        user_id: UUID,
        content: str,
    ) -> FixComment:
        """Add a comment to a fix.

        Args:
            fix_id: The fix ID
            user_id: The user ID
            content: Comment text

        Returns:
            The created FixComment instance

        Raises:
            FixNotFoundError: If the fix is not found
        """
        # Verify fix exists
        await self.get_by_id_or_raise(fix_id)

        comment = FixComment(
            fix_id=fix_id,
            user_id=user_id,
            content=content,
        )
        self.db.add(comment)
        await self.db.commit()
        await self.db.refresh(comment)
        return comment

    async def get_comments(
        self,
        fix_id: UUID,
        limit: int = 25,
        offset: int = 0,
    ) -> Sequence[FixComment]:
        """Get comments for a fix.

        Args:
            fix_id: The fix ID
            limit: Maximum comments to return
            offset: Number to skip

        Returns:
            List of FixComment instances

        Raises:
            FixNotFoundError: If the fix is not found
        """
        # Verify fix exists
        await self.get_by_id_or_raise(fix_id)

        query = (
            select(FixComment)
            .where(FixComment.fix_id == fix_id)
            .options(selectinload(FixComment.user))  # Eagerly load user
            .order_by(FixComment.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def delete_comment(
        self,
        comment_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> bool:
        """Delete a comment.

        Args:
            comment_id: The comment ID
            user_id: If provided, only delete if the comment belongs to this user

        Returns:
            True if deleted, False if not found or not authorized
        """
        query = select(FixComment).where(FixComment.id == comment_id)
        if user_id is not None:
            query = query.where(FixComment.user_id == user_id)

        result = await self.db.execute(query)
        comment = result.scalar_one_or_none()

        if comment is None:
            return False

        await self.db.delete(comment)
        await self.db.commit()
        return True

    # Statistics

    async def get_stats_by_analysis_run(
        self,
        analysis_run_id: UUID,
    ) -> dict:
        """Get fix statistics for an analysis run.

        Args:
            analysis_run_id: The analysis run ID

        Returns:
            Dictionary with statistics
        """
        # Count by status
        status_query = (
            select(Fix.status, func.count())
            .where(Fix.analysis_run_id == analysis_run_id)
            .group_by(Fix.status)
        )
        status_result = await self.db.execute(status_query)
        status_counts = dict(status_result.all())

        # Count by confidence
        confidence_query = (
            select(Fix.confidence, func.count())
            .where(Fix.analysis_run_id == analysis_run_id)
            .group_by(Fix.confidence)
        )
        confidence_result = await self.db.execute(confidence_query)
        confidence_counts = dict(confidence_result.all())

        # Total count
        total_query = (
            select(func.count())
            .select_from(Fix)
            .where(Fix.analysis_run_id == analysis_run_id)
        )
        total_result = await self.db.execute(total_query)
        total = total_result.scalar() or 0

        return {
            "total": total,
            "by_status": {
                status.value: status_counts.get(status, 0)
                for status in FixStatus
            },
            "by_confidence": {
                conf.value: confidence_counts.get(conf, 0)
                for conf in FixConfidence
            },
        }
