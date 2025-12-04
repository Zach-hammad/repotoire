"""Audit logging service for hybrid event tracking.

This module provides the AuditService class for logging both Clerk-sourced
authentication events and application-sourced business events. Includes
a decorator for easy instrumentation of async endpoint functions.

Usage:
    from repotoire.services.audit import get_audit_service, audit_action

    # Manual logging
    service = get_audit_service()
    await service.log(
        db=session,
        event_type="repo.connected",
        actor_id=user.id,
        organization_id=org.id,
        resource_type="repository",
        resource_id=str(repo.id),
    )

    # Decorator-based logging
    @audit_action(event_type="repo.connected", resource_type="repository")
    async def connect_repository(repo_id: str, request: Request, db: AsyncSession):
        ...
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, TypeVar
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models.audit import AuditLog, AuditStatus, EventSource
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger(__name__)

# Type variable for decorator
F = TypeVar("F", bound=Callable[..., Any])


# Mapping of Clerk event types to our event types
CLERK_EVENT_MAPPING: dict[str, tuple[str, str | None, str | None]] = {
    # User events: (event_type, resource_type, action)
    "user.created": ("user.signup", "user", "created"),
    "user.updated": ("user.profile_updated", "user", "updated"),
    "user.deleted": ("user.deleted", "user", "deleted"),
    # Session events
    "session.created": ("user.login", "session", "created"),
    "session.ended": ("user.logout", "session", "ended"),
    "session.revoked": ("user.session_revoked", "session", "revoked"),
    # Organization events
    "organization.created": ("org.created", "organization", "created"),
    "organization.updated": ("org.updated", "organization", "updated"),
    "organization.deleted": ("org.deleted", "organization", "deleted"),
    # Membership events
    "organizationMembership.created": ("org.member_added", "membership", "created"),
    "organizationMembership.updated": ("org.member_updated", "membership", "updated"),
    "organizationMembership.deleted": ("org.member_removed", "membership", "deleted"),
    # Invitation events
    "organizationInvitation.created": ("org.invitation_created", "invitation", "created"),
    "organizationInvitation.accepted": ("org.invitation_accepted", "invitation", "accepted"),
    "organizationInvitation.revoked": ("org.invitation_revoked", "invitation", "revoked"),
}


class AuditService:
    """Service for creating and querying audit log entries.

    This service provides methods for:
    - Creating audit log entries for application events
    - Parsing Clerk webhook payloads into audit entries
    - Querying audit logs with various filters
    - Extracting request context (IP, user agent) automatically

    Attributes:
        default_retention_days: Default retention period in days (2 years = 730)
    """

    default_retention_days: int = 730  # 2 years for SOC 2 compliance

    async def log(
        self,
        db: AsyncSession,
        event_type: str,
        *,
        event_source: EventSource = EventSource.APPLICATION,
        actor_id: UUID | None = None,
        actor_email: str | None = None,
        actor_ip: str | None = None,
        actor_user_agent: str | None = None,
        organization_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        status: AuditStatus = AuditStatus.SUCCESS,
        metadata: dict[str, Any] | None = None,
        clerk_event_id: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry.

        Args:
            db: Database session.
            event_type: Type of event (e.g., "user.login", "repo.connected").
            event_source: Whether event is from Clerk or application.
            actor_id: UUID of the user who performed the action.
            actor_email: Email of the actor (denormalized for retention).
            actor_ip: IP address of the actor.
            actor_user_agent: User agent string from the request.
            organization_id: Organization context for the action.
            resource_type: Type of resource affected (e.g., "repository").
            resource_id: ID of the affected resource.
            action: Action performed (e.g., "created", "deleted").
            status: Whether the action succeeded or failed.
            metadata: Additional context as a dictionary.
            clerk_event_id: Clerk event ID for deduplication.

        Returns:
            The created AuditLog instance.
        """
        audit_log = AuditLog(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            event_source=event_source,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_ip=actor_ip,
            actor_user_agent=actor_user_agent,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=status,
            event_metadata=metadata or {},
            clerk_event_id=clerk_event_id,
        )

        db.add(audit_log)
        await db.flush()

        logger.info(
            f"Audit log created: {event_type}",
            extra={
                "audit_log_id": str(audit_log.id),
                "event_type": event_type,
                "actor_email": actor_email,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )

        return audit_log

    async def log_from_request(
        self,
        db: AsyncSession,
        request: "Request",
        event_type: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        status: AuditStatus = AuditStatus.SUCCESS,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Create an audit log entry extracting context from a FastAPI request.

        Automatically extracts actor info, IP address, and user agent from
        the request object.

        Args:
            db: Database session.
            request: FastAPI Request object.
            event_type: Type of event.
            resource_type: Type of resource affected.
            resource_id: ID of the affected resource.
            action: Action performed.
            status: Whether the action succeeded or failed.
            metadata: Additional context.

        Returns:
            The created AuditLog instance.
        """
        # Extract actor info from request state (set by auth middleware)
        actor_id = None
        actor_email = None
        organization_id = None

        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            actor_id = getattr(user, "id", None)
            actor_email = getattr(user, "email", None)

        if hasattr(request.state, "organization") and request.state.organization:
            org = request.state.organization
            organization_id = getattr(org, "id", None)

        # Extract IP address (handles X-Forwarded-For for proxied requests)
        actor_ip = self._extract_client_ip(request)

        # Extract user agent
        actor_user_agent = request.headers.get("user-agent")

        return await self.log(
            db=db,
            event_type=event_type,
            event_source=EventSource.APPLICATION,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_ip=actor_ip,
            actor_user_agent=actor_user_agent,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=status,
            metadata=metadata,
        )

    async def log_clerk_event(
        self,
        db: AsyncSession,
        clerk_event_type: str,
        data: dict[str, Any],
        *,
        svix_id: str | None = None,
    ) -> AuditLog | None:
        """Parse a Clerk webhook payload and create an audit entry.

        Handles deduplication via the svix_id (Clerk event ID).

        Args:
            db: Database session.
            clerk_event_type: Clerk event type (e.g., "user.created").
            data: Clerk event data payload.
            svix_id: Svix event ID for deduplication.

        Returns:
            The created AuditLog instance, or None if duplicate.
        """
        # Check for duplicate (idempotency)
        if svix_id:
            existing = await db.execute(
                select(AuditLog).where(AuditLog.clerk_event_id == svix_id)
            )
            if existing.scalar_one_or_none():
                logger.debug(f"Duplicate Clerk event: {svix_id}")
                return None

        # Map Clerk event type to our event type
        mapping = CLERK_EVENT_MAPPING.get(clerk_event_type)
        if not mapping:
            logger.warning(f"Unmapped Clerk event type: {clerk_event_type}")
            event_type = f"clerk.{clerk_event_type}"
            resource_type = None
            action = None
        else:
            event_type, resource_type, action = mapping

        # Extract actor info from Clerk payload
        actor_email = None
        actor_id = None
        organization_id = None
        resource_id = None

        # For user events, the data contains user info
        if clerk_event_type.startswith("user."):
            resource_id = data.get("id")
            # Extract email from email_addresses array
            email_addresses = data.get("email_addresses", [])
            primary_email_id = data.get("primary_email_address_id")
            for email_obj in email_addresses:
                if email_obj.get("id") == primary_email_id:
                    actor_email = email_obj.get("email_address")
                    break
            if not actor_email and email_addresses:
                actor_email = email_addresses[0].get("email_address")

        # For session events
        elif clerk_event_type.startswith("session."):
            resource_id = data.get("id")
            # User ID is in the session data
            user_id = data.get("user_id")
            if user_id:
                # We could look up the user, but for now just store the Clerk ID
                pass

        # For organization events
        elif clerk_event_type.startswith("organization."):
            resource_id = data.get("id")
            # Look up our organization by Clerk org ID
            org_id = data.get("id")
            # We'll need to look this up in the webhook handler

        # For membership events
        elif clerk_event_type.startswith("organizationMembership."):
            resource_id = data.get("id")
            org_data = data.get("organization", {})
            if org_data:
                # Store org name in metadata
                pass
            public_user_data = data.get("public_user_data", {})
            if public_user_data:
                actor_email = public_user_data.get("email_address")

        # For invitation events
        elif clerk_event_type.startswith("organizationInvitation."):
            resource_id = data.get("id")
            actor_email = data.get("email_address")

        # Build metadata with relevant Clerk data
        metadata = {
            "clerk_event_type": clerk_event_type,
        }

        # Add relevant fields from data to metadata
        if "first_name" in data or "last_name" in data:
            metadata["name"] = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        if "organization" in data:
            org = data["organization"]
            metadata["organization_name"] = org.get("name")
            metadata["clerk_org_id"] = org.get("id")
        if "created_at" in data:
            metadata["clerk_created_at"] = data["created_at"]

        return await self.log(
            db=db,
            event_type=event_type,
            event_source=EventSource.CLERK,
            actor_email=actor_email,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=AuditStatus.SUCCESS,
            metadata=metadata,
            clerk_event_id=svix_id,
        )

    async def query(
        self,
        db: AsyncSession,
        *,
        organization_id: UUID | None = None,
        actor_id: UUID | None = None,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        status: AuditStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        """Query audit logs with various filters.

        Args:
            db: Database session.
            organization_id: Filter by organization.
            actor_id: Filter by actor.
            event_type: Filter by single event type.
            event_types: Filter by multiple event types.
            resource_type: Filter by resource type.
            resource_id: Filter by resource ID.
            start_date: Filter events after this date.
            end_date: Filter events before this date.
            status: Filter by status (success/failure).
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            Tuple of (list of AuditLog instances, total count).
        """
        # Build filter conditions
        conditions = []

        if organization_id:
            conditions.append(AuditLog.organization_id == organization_id)
        if actor_id:
            conditions.append(AuditLog.actor_id == actor_id)
        if event_type:
            conditions.append(AuditLog.event_type == event_type)
        if event_types:
            conditions.append(AuditLog.event_type.in_(event_types))
        if resource_type:
            conditions.append(AuditLog.resource_type == resource_type)
        if resource_id:
            conditions.append(AuditLog.resource_id == resource_id)
        if start_date:
            conditions.append(AuditLog.timestamp >= start_date)
        if end_date:
            conditions.append(AuditLog.timestamp <= end_date)
        if status:
            conditions.append(AuditLog.status == status)

        # Build query
        base_query = select(AuditLog)
        if conditions:
            base_query = base_query.where(and_(*conditions))

        # Get total count
        from sqlalchemy import func

        count_query = select(func.count()).select_from(
            base_query.subquery()
        )
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # Get paginated results
        query = (
            base_query
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(query)
        logs = list(result.scalars().all())

        return logs, total

    @staticmethod
    def _extract_client_ip(request: "Request") -> str | None:
        """Extract the client IP address from a request.

        Handles X-Forwarded-For header for proxied requests.

        Args:
            request: FastAPI Request object.

        Returns:
            Client IP address or None.
        """
        # Check X-Forwarded-For header (from load balancers/proxies)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Take the first IP (client IP)
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header (nginx)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return None


def audit_action(
    event_type: str,
    *,
    resource_type: str | None = None,
    action: str | None = None,
    get_resource_id: Callable[..., str | None] | None = None,
    get_metadata: Callable[..., dict[str, Any] | None] | None = None,
) -> Callable[[F], F]:
    """Decorator to automatically create audit logs for endpoint functions.

    The decorated function must have `request: Request` and `db: AsyncSession`
    parameters (directly or via dependency injection).

    Args:
        event_type: Type of event to log.
        resource_type: Type of resource being acted upon.
        action: Action being performed.
        get_resource_id: Optional callable to extract resource ID from function args.
        get_metadata: Optional callable to extract additional metadata from function args.

    Returns:
        Decorated function that creates audit logs on success/failure.

    Example:
        @router.post("/repos/{repo_id}/connect")
        @audit_action(
            event_type="repo.connected",
            resource_type="repository",
            action="created",
            get_resource_id=lambda repo_id, **_: repo_id,
        )
        async def connect_repository(
            repo_id: str,
            request: Request,
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Try to find request and db in kwargs or args
            request = kwargs.get("request")
            db = kwargs.get("db")

            # If not in kwargs, check positional args via function signature
            if request is None or db is None:
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())

                for i, arg in enumerate(args):
                    if i < len(params):
                        param_name = params[i]
                        if param_name == "request" and request is None:
                            request = arg
                        elif param_name == "db" and db is None:
                            db = arg

            status = AuditStatus.SUCCESS
            error_metadata: dict[str, Any] | None = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = AuditStatus.FAILURE
                error_metadata = {
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:500],
                }
                raise
            finally:
                # Create audit log
                if request and db:
                    try:
                        audit_service = get_audit_service()

                        # Get resource ID if extractor provided
                        resource_id = None
                        if get_resource_id:
                            try:
                                resource_id = get_resource_id(*args, **kwargs)
                            except Exception:
                                pass

                        # Get additional metadata if extractor provided
                        metadata = None
                        if get_metadata:
                            try:
                                metadata = get_metadata(*args, **kwargs)
                            except Exception:
                                pass

                        # Merge error metadata
                        if error_metadata:
                            metadata = {**(metadata or {}), **error_metadata}

                        await audit_service.log_from_request(
                            db=db,
                            request=request,
                            event_type=event_type,
                            resource_type=resource_type,
                            resource_id=resource_id,
                            action=action,
                            status=status,
                            metadata=metadata,
                        )
                        await db.commit()
                    except Exception as audit_error:
                        # Don't let audit logging errors affect the main operation
                        logger.error(
                            f"Failed to create audit log: {audit_error}",
                            exc_info=True,
                        )

        return wrapper  # type: ignore[return-value]

    return decorator


# Singleton instance for easy access
_audit_service: AuditService | None = None


def get_audit_service() -> AuditService:
    """Get the singleton AuditService instance.

    Returns:
        AuditService instance.
    """
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service
