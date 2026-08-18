"""Utility helpers — circuit breaker, events, pagination, retry, validators."""
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, get_circuit_breaker
from src.utils.events import (
    AsyncEventBus,
    IngestionCompletedEvent,
    NewJobsFoundEvent,
    ScrapeCompletedEvent,
    SourceHealthChangedEvent,
    event_bus,
)
from src.utils.pagination import CursorPage, OffsetPage, decode_cursor, encode_cursor
from src.utils.retry import RetryConfig, is_retryable_exception
from src.utils.validators import is_valid_url, parse_salary, strip_html, truncate

__all__ = [
    # circuit breaker
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "get_circuit_breaker",
    # events
    "event_bus",
    "AsyncEventBus",
    "ScrapeCompletedEvent",
    "NewJobsFoundEvent",
    "IngestionCompletedEvent",
    "SourceHealthChangedEvent",
    # pagination
    "encode_cursor",
    "decode_cursor",
    "CursorPage",
    "OffsetPage",
    # retry
    "RetryConfig",
    "is_retryable_exception",
    # validators
    "strip_html",
    "truncate",
    "parse_salary",
    "is_valid_url",
]
