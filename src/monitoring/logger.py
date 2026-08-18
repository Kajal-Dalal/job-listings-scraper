"""
Structured logging via structlog.
- JSON output in production (LOG_FORMAT=json)
- Pretty colored console output in development (LOG_FORMAT=console)
- Request ID is automatically propagated via contextvars
- All exceptions are formatted with full tracebacks
"""
import logging
import sys
from contextvars import ContextVar
from typing import Any, Optional

import structlog

# Context variable for request-ID propagation across async tasks
_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    """Bind a request ID to the current async context."""
    _request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """Retrieve the current request ID (or None)."""
    return _request_id_var.get()


def _add_request_id(
    logger: Any, method: str, event_dict: dict
) -> dict:
    """structlog processor: inject request_id into every log entry."""
    rid = _request_id_var.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def configure_logging(log_level: str = "INFO", log_format: str = "console") -> None:
    """
    Configure structlog and stdlib logging.
    Call once at application startup.

    Args:
        log_level:  Standard Python log level string (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_format: "json" for production JSON lines, "console" for human-readable
    """
    # ---- Shared processors (applied regardless of format) ----
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _add_request_id,
    ]

    if log_format == "json":
        # Production: JSON lines, machine-parseable
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: coloured, human-readable
        processors = shared_processors + [
            structlog.processors.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Wire up stdlib logging so that third-party libraries (SQLAlchemy, httpx, etc.)
    # also go through structlog.
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )

    # Quiet noisy libraries
    for noisy in ("httpx", "httpcore", "aiosqlite", "apscheduler", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """
    Return a bound structlog logger.

    Usage:
        log = get_logger(__name__)
        log.info("event", key="value")
    """
    return structlog.get_logger(name)
