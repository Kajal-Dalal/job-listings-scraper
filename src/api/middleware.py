"""
FastAPI middleware stack — FreshMart-inspired production patterns.

Middleware (applied in order, outermost first):
1. CORS
2. Correlation ID  — FreshMart's CorrelationIdMiddleware pattern exactly
3. Security headers — HSTS, X-Frame-Options, X-Content-Type-Options
4. Request/Response logging with timing
5. API metrics (Prometheus)

Key FreshMart patterns adopted:
- X-Correlation-ID reuse: if the caller sends one, we reuse it (distributed tracing)
- Correlation ID in every log line via context var
- Response headers expose the correlation ID to callers for end-to-end tracing
"""
import time
import uuid
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.settings import get_settings
from src.monitoring.logger import get_logger, set_request_id
from src.monitoring.metrics import metrics

log = get_logger(__name__)

CORRELATION_ID_HEADER = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique Correlation ID to every incoming request.

    Mirrors FreshMart's ApiGateway/CorrelationIdMiddleware.cs exactly:
    - If the caller already sent X-Correlation-ID, reuse it (distributed tracing)
    - Otherwise generate a new UUID
    - Add it to the response headers so callers can trace requests end-to-end
    - Bind it to the structured log context so it appears in every log line

    This is the single most important observability pattern from FreshMart.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Reuse existing correlation ID (from upstream service/gateway) or generate new
        correlation_id = (
            request.headers.get(CORRELATION_ID_HEADER)
            or request.headers.get(REQUEST_ID_HEADER)
            or str(uuid.uuid4())
        )

        # Bind to async context so every log line within this request includes it
        set_request_id(correlation_id)

        # Store on request.state for access in route handlers
        request.state.request_id = correlation_id
        request.state.correlation_id = correlation_id

        response = await call_next(request)

        # Expose in response headers for end-to-end distributed tracing
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[REQUEST_ID_HEADER] = correlation_id

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to every response.

    OWASP-recommended headers that prevent common web attacks.
    FreshMart uses Nginx for this — in Python we add it at the app layer.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Only add HSTS if we're on HTTPS (avoid breaking HTTP dev)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every incoming request and outgoing response with timing.

    Skips /health/live (liveness probe) to avoid log noise from k8s probes.
    """

    _SKIP_PATHS = {"/health/live", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip noisy probe endpoints
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        start = time.monotonic()
        correlation_id = getattr(request.state, "correlation_id", "unknown")

        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query) or None,
            client_ip=_get_client_ip(request),
            correlation_id=correlation_id,
        )

        response = await call_next(request)
        elapsed = time.monotonic() - start

        log.info(
            "http_response",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
            correlation_id=correlation_id,
        )

        # Prometheus metrics
        metrics.record_api_request(
            endpoint=request.url.path,
            method=request.method,
            status_code=response.status_code,
        )
        metrics.api_request_duration_seconds.labels(
            endpoint=request.url.path,
            method=request.method,
        ).observe(elapsed)

        # Add server timing header for performance visibility
        response.headers["Server-Timing"] = f"total;dur={elapsed * 1000:.1f}"

        return response


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, checking X-Forwarded-For first (proxy-aware)."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def setup_middleware(app: FastAPI) -> None:
    """
    Apply the full middleware stack to the FastAPI app.

    Order matters — middleware is applied as a stack (last added = outermost).
    We want: CORS → CorrelationId → Security → Logging
    So we add them in reverse order.
    """
    settings = get_settings()

    # 4. Logging (innermost — runs last, sees everything)
    app.add_middleware(LoggingMiddleware)

    # 3. Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # 2. Correlation ID (FreshMart pattern)
    app.add_middleware(CorrelationIdMiddleware)

    # 1. CORS (outermost — handles preflight before anything else)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=[
            CORRELATION_ID_HEADER,
            REQUEST_ID_HEADER,
            "X-Total-Count",
            "Server-Timing",
        ],
    )
