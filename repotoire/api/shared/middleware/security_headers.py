"""Security headers middleware for FastAPI.

This middleware adds standard security headers to all responses:
- X-Content-Type-Options: Prevents MIME type sniffing
- X-Frame-Options: Prevents clickjacking
- X-XSS-Protection: Legacy XSS filter (for older browsers)
- Strict-Transport-Security: Enforces HTTPS (HSTS)
- Content-Security-Policy: Controls allowed content sources
- Referrer-Policy: Controls referrer information
- Permissions-Policy: Controls browser features

These headers protect against common web vulnerabilities:
- Clickjacking (X-Frame-Options, CSP frame-ancestors)
- MIME sniffing attacks (X-Content-Type-Options)
- XSS attacks (CSP, X-XSS-Protection)
- Protocol downgrade attacks (HSTS)
- Information leakage (Referrer-Policy)
"""

import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware that adds security headers to all responses.

    Configuration via environment variables:
    - ENVIRONMENT: If "development", relaxes some headers for local testing
    - CSP_REPORT_URI: Optional URI for CSP violation reports
    - CORS_ORIGINS: Used to set frame-ancestors in CSP

    Example:
        app.add_middleware(SecurityHeadersMiddleware)
    """

    def __init__(
        self,
        app,
        hsts_max_age: int = 31536000,  # 1 year in seconds
        include_subdomains: bool = True,
        hsts_preload: bool = False,
        frame_options: str = "DENY",
        content_type_options: str = "nosniff",
        xss_protection: str = "1; mode=block",
        referrer_policy: str = "strict-origin-when-cross-origin",
        csp_report_uri: Optional[str] = None,
    ):
        """Initialize security headers middleware.

        Args:
            app: The ASGI application
            hsts_max_age: Max age for HSTS header in seconds (default: 1 year)
            include_subdomains: Include subdomains in HSTS
            hsts_preload: Add preload directive to HSTS
            frame_options: X-Frame-Options value (DENY, SAMEORIGIN)
            content_type_options: X-Content-Type-Options value
            xss_protection: X-XSS-Protection value
            referrer_policy: Referrer-Policy value
            csp_report_uri: Optional URI for CSP violation reports
        """
        super().__init__(app)
        self.hsts_max_age = hsts_max_age
        self.include_subdomains = include_subdomains
        self.hsts_preload = hsts_preload
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.xss_protection = xss_protection
        self.referrer_policy = referrer_policy
        self.csp_report_uri = csp_report_uri or os.getenv("CSP_REPORT_URI")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Add security headers to the response."""
        response = await call_next(request)

        # Determine environment
        is_production = os.getenv("ENVIRONMENT", "development") == "production"

        # X-Content-Type-Options: Prevent MIME sniffing
        response.headers["X-Content-Type-Options"] = self.content_type_options

        # X-Frame-Options: Prevent clickjacking
        # Note: CSP frame-ancestors is preferred but X-Frame-Options
        # is still needed for older browsers
        response.headers["X-Frame-Options"] = self.frame_options

        # X-XSS-Protection: Legacy XSS filter
        # Modern browsers use CSP, but this helps older browsers
        response.headers["X-XSS-Protection"] = self.xss_protection

        # Referrer-Policy: Control referrer information
        response.headers["Referrer-Policy"] = self.referrer_policy

        # Strict-Transport-Security (HSTS)
        # Only add in production to avoid issues with local development
        if is_production:
            hsts_value = f"max-age={self.hsts_max_age}"
            if self.include_subdomains:
                hsts_value += "; includeSubDomains"
            if self.hsts_preload:
                hsts_value += "; preload"
            response.headers["Strict-Transport-Security"] = hsts_value

        # Content-Security-Policy
        csp = self._build_csp(is_production)
        response.headers["Content-Security-Policy"] = csp

        # Permissions-Policy: Control browser features
        # Disable potentially dangerous features by default
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), "
            "camera=(), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "payment=(), "
            "usb=()"
        )

        # Cache-Control for API responses
        # Prevent caching of sensitive data
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, max-age=0"

        return response

    def _build_csp(self, is_production: bool) -> str:
        """Build Content-Security-Policy header value.

        Args:
            is_production: Whether running in production environment

        Returns:
            CSP header value string
        """
        # Get allowed origins for frame-ancestors
        cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
        cors_origins = [o.strip() for o in cors_origins if o.strip()]

        # Build frame-ancestors directive
        if cors_origins:
            frame_ancestors = f"frame-ancestors 'self' {' '.join(cors_origins)}"
        else:
            frame_ancestors = "frame-ancestors 'none'"

        # Base CSP directives for an API
        directives = [
            "default-src 'none'",  # Deny everything by default
            "script-src 'self'",  # Only allow scripts from same origin
            "style-src 'self' 'unsafe-inline'",  # Allow inline styles for Swagger UI
            "img-src 'self' data: https:",  # Allow images from self, data URIs, HTTPS
            "font-src 'self'",  # Fonts from same origin
            "connect-src 'self'",  # AJAX/WebSocket to same origin
            "base-uri 'self'",  # Restrict <base> tag
            "form-action 'self'",  # Restrict form submissions
            frame_ancestors,  # Control who can embed this page
        ]

        # Add report-uri if configured
        if self.csp_report_uri:
            directives.append(f"report-uri {self.csp_report_uri}")

        # In development, allow more for debugging tools
        if not is_production:
            # Allow eval for some dev tools
            directives = [
                d.replace("script-src 'self'", "script-src 'self' 'unsafe-inline' 'unsafe-eval'")
                for d in directives
            ]
            # Allow connections to any HTTPS for local testing
            directives = [
                d.replace("connect-src 'self'", "connect-src 'self' https: http://localhost:*")
                for d in directives
            ]

        return "; ".join(directives)


# Export for use in app
__all__ = ["SecurityHeadersMiddleware"]
