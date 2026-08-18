"""Pipeline package: ingestion, normalization, deduplication, scheduling."""
from .ingestion import IngestionPipeline, IngestionResult
from .normalizer import Normalizer
from .deduplicator import Deduplicator
from .scheduler import Scheduler

__all__ = [
    "IngestionPipeline",
    "IngestionResult",
    "Normalizer",
    "Deduplicator",
    "Scheduler",
]
