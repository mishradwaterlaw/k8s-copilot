"""
main.py — FastAPI server that exposes the investigation graph as a REST API.

WHAT CHANGED:
  - /investigate now accepts namespace and pod_name alongside alert
  - initial_state includes the new fields
  - Everything else is structurally the same

CONCEPT: WHY TWO ENDPOINTS INSTEAD OF ONE?
  A naive approach: POST /investigate → runs graph → BLOCKS until human approves.
  Problem: the HTTP request would hang for however long it takes a human
  to review. Could be minutes, hours, or never. HTTP clients time out.

  The correct approach:
    POST /investigate      → starts investigation, returns immediately when paused
    POST /investigate/{id}/resume → human sends their decision, graph finishes

  The thread_id ties the two requests together.
  The SqliteSaver checkpoint persists state between them.
  The server process could restart between the two calls — it doesn't matter.
  This is how you build human-in-the-loop into a real async system.

CONCEPT: WHY THREAD_ID?
  LangGraph's checkpointer is keyed by "thread_id" — a string you choose.
  Think of it as a conversation ID. One investigation = one thread.
  
  We generate a random UUID for each new investigation so IDs don't collide.
  In production you might use a more meaningful ID:
    - Alert ID from your monitoring system
    - Pod name + timestamp
  As long as it's unique, any string works.
"""

import uuid
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langgraph.types import Command

from graph import build_graph
from metrics import (
    INVESTIGATIONS_TOTAL,
    INVESTIGATION_DURATION_SECONDS,
    SYNTHESIS_CONFIDENCE,
    INVESTIGATION_ITERATIONS,
    metrics_endpoint_response,
)
from ingestion import webhook_router

app = FastAPI(
    title="K8s Copilot",
    description="Agentic Kubernetes investigation assistant with human-in-the-loop review.",
    version="2.0.0",
)

# Register Webhook Ingestion Routes (/webhook/alertmanager, /webhook/generic)
app.include_router(webhook_router)

# Build the graph ONCE at startup.
# This opens the SQLite connection and compiles the graph.
# Reusing across requests is important — don't rebuild per request.
graph = build_graph()


# ── Request/Response Schemas ─────────────────────────────────────────────────
# Pydantic BaseModel validates incoming JSON automatically.
# If the request body doesn't match, FastAPI returns a 422 error with details.

class InvestigateRequest(BaseModel):
    alert: str              # e.g. "Pod payments-api-7f8b9 is in CrashLoopBackOff"
    namespace: str = "default"   # Kubernetes namespace, defaults to "default"
    pod_name: str           # e.g. "payments-api-7f8b9"


class ResumeRequest(BaseModel):
    decision: str           # "approve" or override text


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/investigate")
def investigate(req: InvestigateRequest):
    """
    Start a new investigation.
    
    Runs the graph until it pauses at human_review (the expected path).
    Returns immediately once paused — does NOT block waiting for the human.
    """
    start_time = time.time()
    initial_state = {
        "alert": req.alert,
        "namespace": req.namespace,
        "pod_name": req.pod_name,
        "deploy_finding": "",
        "log_finding": "",
        "iteration_count": 0,
        "confidence": 0.0,
        "root_cause": "",
    }

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    try:
        # invoke() runs the graph synchronously until it hits an interrupt() or reaches END
        graph.invoke(initial_state, config=config)
    except Exception as e:
        INVESTIGATIONS_TOTAL.labels(status="failed").inc()
        raise HTTPException(status_code=500, detail=f"Investigation failed: {str(e)}")

    elapsed = time.time() - start_time
    INVESTIGATION_DURATION_SECONDS.observe(elapsed)

    snapshot = graph.get_state(config)

    if snapshot.next:
        # Paused at human_review
        pending = snapshot.tasks[0].interrupts[0].value
        confidence = float(pending.get("confidence", 0.0))
        SYNTHESIS_CONFIDENCE.observe(confidence)
        INVESTIGATION_ITERATIONS.observe(pending.get("iterations_run", 1))
        INVESTIGATIONS_TOTAL.labels(status="paused_for_review").inc()

        return {
            "thread_id": thread_id,
            "status": "paused_for_review",
            "proposed_root_cause": pending["proposed_root_cause"],
            "confidence": pending["confidence"],
            "deploy_finding": pending["deploy_finding"],
            "log_finding": pending["log_finding"],
            "pod_name": pending["pod_name"],
            "namespace": pending["namespace"],
            "iterations_run": pending["iterations_run"],
        }
    else:
        final = snapshot.values
        INVESTIGATIONS_TOTAL.labels(status="completed").inc()
        return {
            "thread_id": thread_id,
            "status": "completed",
            "root_cause": final["root_cause"],
            "confidence": final["confidence"],
        }


@app.post("/investigate/{thread_id}/resume")
def resume(thread_id: str, req: ResumeRequest):
    """
    Resume a paused investigation with a human decision.
    """
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=404,
            detail=f"No paused investigation found for thread_id '{thread_id}'. "
                   "It may have already completed or never existed."
        )

    try:
        final_state = graph.invoke(Command(resume=req.decision), config=config)
        INVESTIGATIONS_TOTAL.labels(status="completed").inc()
    except Exception as e:
        INVESTIGATIONS_TOTAL.labels(status="failed").inc()
        raise HTTPException(status_code=500, detail=f"Failed to resume investigation: {str(e)}")

    return {
        "thread_id": thread_id,
        "status": "completed",
        "root_cause": final_state["root_cause"],
        "confidence": final_state["confidence"],
        "iterations": final_state["iteration_count"],
    }


@app.get("/investigate/{thread_id}/status")
def status(thread_id: str):
    """
    Check the status of any investigation (active, paused, or completed).
    Useful for polling from a UI or CI pipeline.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)

    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Thread not found.")

    if snapshot.next:
        # Paused at human_review
        pending = snapshot.tasks[0].interrupts[0].value if snapshot.tasks else {}
        return {
            "thread_id": thread_id,
            "status": "paused_for_review",
            "proposed_root_cause": pending.get("proposed_root_cause", ""),
            "confidence": pending.get("confidence", 0.0),
        }
    else:
        return {
            "thread_id": thread_id,
            "status": "completed",
            "root_cause": snapshot.values.get("root_cause", ""),
            "confidence": snapshot.values.get("confidence", 0.0),
        }


@app.get("/health")
def health():
    """Health check endpoint for Docker/K8s liveness probes."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/metrics")
def metrics():
    """
    Prometheus metrics exposition endpoint.
    Scraped periodically by Prometheus server. Returns plain-text metrics.
    NOTE: Deliberately unauthenticated so standard Prometheus scrapers can query without OAuth.
    """
    return metrics_endpoint_response()