"""
Internal event system — inspired by FreshMart's SharedModels/Events.cs pattern.

FreshMart uses RabbitMQ + MassTransit for decoupled inter-service communication.
For a single-process Python scraper, we implement an in-process async event bus
using the Observer pattern with asyncio.

Events flow:
  Scraper finishes → ScrapeCompletedEvent published
  Pipeline runs   → IngestionCompletedEvent published
  Job inserted    → NewJobsFoundEvent published

Consumers (registered via @event_bus.on) react asynchronously.
This decouples: logging, metrics, alerts, cache invalidation.

Usage:
    # Publisher
    await event_bus.publish(NewJobsFoundEvent(source="remoteok", count=12))

    # Subscriber
    @event_bus.on(NewJobsFoundEvent)
    async def handle_new_jobs(event: NewJobsFoundEvent):
        log.info("new_jobs", source=event.source, count=event.count)
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type

from src.monitoring.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Event Contracts  (immutable dataclasses — like C# records in FreshMart)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaseEvent:
    """Base class for all domain events."""
    event_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)
    occurred_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class ScrapeCompletedEvent(BaseEvent):
    """
    Published by BaseScraper when a single scraper finishes a run.
    Equivalent to FreshMart's OrderPlacedEvent — a domain event after a significant action.
    """
    source: str = ""
    jobs_found: int = 0
    success: bool = False
    duration_seconds: float = 0.0
    error: Optional[str] = None


@dataclass(frozen=True)
class NewJobsFoundEvent(BaseEvent):
    """
    Published by IngestionPipeline when new (non-duplicate) jobs are persisted.
    Downstream consumers can use this for alerting, cache invalidation, webhooks, etc.
    """
    source: str = ""
    count: int = 0
    total_in_db: int = 0


@dataclass(frozen=True)
class IngestionCompletedEvent(BaseEvent):
    """
    Published at the end of a full pipeline run (all sources combined).
    Equivalent to FreshMart's OrderStatusChangedEvent — a lifecycle event.
    """
    total_found: int = 0
    total_new: int = 0
    total_duplicate: int = 0
    total_error: int = 0
    sources_run: tuple = field(default_factory=tuple)
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class SourceHealthChangedEvent(BaseEvent):
    """
    Published when a source's circuit breaker state changes.
    Allows monitoring systems to react to source degradation.
    """
    source: str = ""
    old_state: str = ""
    new_state: str = ""


# ---------------------------------------------------------------------------
# Async Event Bus
# ---------------------------------------------------------------------------

EventHandler = Callable[[Any], Any]


class AsyncEventBus:
    """
    In-process async publish/subscribe event bus.

    - Handlers are registered per event type.
    - Publishing fires all handlers concurrently via asyncio.gather.
    - Exceptions in handlers are caught and logged — they never crash the publisher.
    """

    def __init__(self):
        self._handlers: Dict[Type[BaseEvent], List[EventHandler]] = {}

    def on(self, event_type: Type[BaseEvent]) -> Callable:
        """
        Decorator to register an async handler for an event type.

        Usage:
            @event_bus.on(NewJobsFoundEvent)
            async def handle(event: NewJobsFoundEvent):
                ...
        """
        def decorator(fn: EventHandler) -> EventHandler:
            self._handlers.setdefault(event_type, []).append(fn)
            log.debug(
                "event_handler_registered",
                event_type=event_type.__name__,
                handler=fn.__name__,
            )
            return fn
        return decorator

    def subscribe(self, event_type: Type[BaseEvent], handler: EventHandler) -> None:
        """Programmatic handler registration (alternative to decorator)."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: BaseEvent) -> None:
        """
        Publish an event to all registered handlers.

        Handlers run concurrently. Exceptions are caught per-handler so
        one failing handler doesn't block others.
        """
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return

        async def _safe_call(handler: EventHandler, ev: BaseEvent) -> None:
            try:
                result = handler(ev)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log.error(
                    "event_handler_error",
                    event_type=type(ev).__name__,
                    handler=handler.__name__,
                    error=str(exc),
                )

        await asyncio.gather(
            *[_safe_call(h, event) for h in handlers],
            return_exceptions=False,
        )

        log.debug(
            "event_published",
            event_type=type(event).__name__,
            handler_count=len(handlers),
        )

    def handler_count(self, event_type: Type[BaseEvent]) -> int:
        return len(self._handlers.get(event_type, []))


# ---------------------------------------------------------------------------
# Module-level singleton event bus
# ---------------------------------------------------------------------------

event_bus = AsyncEventBus()


# ---------------------------------------------------------------------------
# Built-in handlers: metrics + logging
# ---------------------------------------------------------------------------

@event_bus.on(ScrapeCompletedEvent)
async def _metrics_on_scrape_complete(event: ScrapeCompletedEvent) -> None:
    """Auto-record scrape metrics from events — decoupled from scraper code."""
    from src.monitoring.metrics import metrics
    status = "success" if event.success else "failed"
    metrics.record_scrape_run(event.source, status)


@event_bus.on(NewJobsFoundEvent)
async def _log_new_jobs(event: NewJobsFoundEvent) -> None:
    """Log every time new jobs land in the DB."""
    log.info(
        "new_jobs_persisted",
        source=event.source,
        new_count=event.count,
        total_in_db=event.total_in_db,
    )


@event_bus.on(IngestionCompletedEvent)
async def _log_ingestion_complete(event: IngestionCompletedEvent) -> None:
    """Summary log at end of full pipeline run."""
    log.info(
        "ingestion_cycle_complete",
        total_found=event.total_found,
        total_new=event.total_new,
        total_duplicate=event.total_duplicate,
        sources=list(event.sources_run),
        duration_seconds=round(event.duration_seconds, 2),
    )
