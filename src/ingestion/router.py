"""
src/ingestion/router.py — FastAPI Webhook endpoints for Alertmanager and external alert ingestion.

CONCEPT: ASYNCHRONOUS WEBHOOK INGESTION (202 ACCEPTED)
══════════════════════════════════════════════════════
Monitoring webhooks (Prometheus Alertmanager, Datadog, PagerDuty) expect HTTP responses
within a few hundred milliseconds. If an endpoint hangs for 30-60 seconds while an LLM agent
runs multi-step tool loops:
  1. Alertmanager times out (default timeout is 10s).
  2. Alertmanager marks the webhook receiver as DOWN.
  3. Alertmanager initiates retries, flooding the server with duplicate requests.

THE SOLUTION:
  1. Validate incoming JSON payload (<5ms).
  2. Check deduplicator cache by fingerprint to suppress duplicate alert storms (<1ms).
  3. Generate a unique `thread_id`.
  4. Schedule background investigation using FastAPI `BackgroundTasks`.
  5. Return HTTP `202 Accepted` IMMEDIATELY with the `thread_id` and status.

The background worker runs the supervisor graph to completion (or pause at `human_review`).
State is durably saved in SQLite (`SqliteSaver`).
"""

import uuid
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from ingestion.models import AlertmanagerWebhookPayload, GenericAlertPayload, AlertEvent
from ingestion.dedup import deduplicator
from graph import build_graph
from metrics import INVESTIGATIONS_TOTAL, SYNTHESIS_CONFIDENCE, INVESTIGATION_ITERATIONS

logger = logging.getLogger("k8s-copilot.ingestion")

router = APIRouter(prefix="/webhook", tags=["Alert Ingestion Webhooks"])

# Compile supervisor graph once for the worker
graph = build_graph()


def _run_background_investigation(event: AlertEvent, thread_id: str) -> None:
    """
    Background worker function executed by FastAPI BackgroundTasks.
    Runs the multi-agent graph until it either pauses at human_review or finishes.
    """
    logger.info(f"Starting background investigation for thread {thread_id} ({event.pod_name})")

    initial_state = {
        "alert": event.to_investigation_alert_text(),
        "namespace": event.namespace,
        "pod_name": event.pod_name,
        "deploy_finding": "",
        "log_finding": "",
        "iteration_count": 0,
        "confidence": 0.0,
        "root_cause": "",
    }

    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph.invoke(initial_state, config=config)
        snapshot = graph.get_state(config)

        if snapshot.next:
            # Paused at human_review — durable in SQLite
            pending = snapshot.tasks[0].interrupts[0].value
            confidence = float(pending.get("confidence", 0.0))
            SYNTHESIS_CONFIDENCE.observe(confidence)
            INVESTIGATION_ITERATIONS.observe(pending.get("iterations_run", 1))
            INVESTIGATIONS_TOTAL.labels(status="paused_for_review").inc()
            logger.info(f"Thread {thread_id} paused for human review with confidence {confidence:.2f}")
        else:
            INVESTIGATIONS_TOTAL.labels(status="completed").inc()
            logger.info(f"Thread {thread_id} completed investigation autonomously.")

    except Exception as e:
        INVESTIGATIONS_TOTAL.labels(status="failed").inc()
        logger.error(f"Background investigation {thread_id} failed: {str(e)}", exc_info=True)


@router.post("/alertmanager", status_code=status.HTTP_202_ACCEPTED)
def alertmanager_webhook(payload: AlertmanagerWebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook receiver for Prometheus Alertmanager.
    
    Accepts standard Alertmanager v4 JSON notifications:
      - Filters out resolved alerts (only processes 'firing' alerts)
      - Normalizes each alert into an AlertEvent
      - Deduplicates by alert fingerprint
      - Returns 202 Accepted immediately and runs investigations in background
    """
    events: List[AlertEvent] = payload.to_normalized_events()

    if not events:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "No active firing alerts in payload.", "processed": 0}
        )

    dispatched = []
    suppressed = []

    for event in events:
        new_thread_id = str(uuid.uuid4())
        is_duplicate, active_thread_id = deduplicator.check_and_register(
            event.fingerprint, new_thread_id
        )

        if is_duplicate:
            suppressed.append({
                "fingerprint": event.fingerprint,
                "pod": event.pod_name,
                "active_thread_id": active_thread_id,
                "reason": "Duplicate alert within cooldown window (circuit breaker active)",
            })
        else:
            # Dispatch async investigation
            background_tasks.add_task(_run_background_investigation, event, new_thread_id)
            dispatched.append({
                "fingerprint": event.fingerprint,
                "pod": event.pod_name,
                "namespace": event.namespace,
                "thread_id": new_thread_id,
                "status": "investigation_queued",
            })

    return {
        "status": "accepted",
        "total_firing_alerts": len(events),
        "dispatched_investigations": dispatched,
        "suppressed_duplicates": suppressed,
    }


@router.post("/generic", status_code=status.HTTP_202_ACCEPTED)
def generic_webhook(payload: GenericAlertPayload, background_tasks: BackgroundTasks):
    """
    Webhook receiver for custom monitoring systems, Grafana alerts, or CI pipelines.
    """
    event = payload.to_normalized_event()
    new_thread_id = str(uuid.uuid4())

    is_duplicate, active_thread_id = deduplicator.check_and_register(
        event.fingerprint, new_thread_id
    )

    if is_duplicate:
        return {
            "status": "duplicate_suppressed",
            "fingerprint": event.fingerprint,
            "active_thread_id": active_thread_id,
            "message": "An investigation for this alert is already in flight.",
        }

    background_tasks.add_task(_run_background_investigation, event, new_thread_id)

    return {
        "status": "accepted",
        "thread_id": new_thread_id,
        "fingerprint": event.fingerprint,
        "pod_name": event.pod_name,
        "namespace": event.namespace,
    }
