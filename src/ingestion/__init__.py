"""
src/ingestion/__init__.py — Package exports for alert ingestion and webhook routing.
"""

from ingestion.models import AlertEvent, AlertmanagerWebhookPayload, GenericAlertPayload
from ingestion.dedup import AlertDeduplicator, deduplicator
from ingestion.router import router as webhook_router

__all__ = [
    "AlertEvent",
    "AlertmanagerWebhookPayload",
    "GenericAlertPayload",
    "AlertDeduplicator",
    "deduplicator",
    "webhook_router",
]
