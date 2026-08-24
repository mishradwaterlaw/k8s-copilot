"""
state.py — The supervisor graph's shared "case file."

CONCEPT: STATE IN MULTI-AGENT SUPERVISOR ARCHITECTURES
══════════════════════════════════════════════════════
LangGraph states are TypedDicts passed between graph nodes.
Subgraphs have their own private states (DeployState, LogState) and only expose
distilled findings to the supervisor state.

HUMAN-STEERING EXTENSIONS:
  - `human_feedback`: Stores operator feedback/guidance (e.g. "check again", "look at sidecar")
  - `feedback_action`: "approve" | "reinvestigate" | "override"
"""

from typing import TypedDict


class InvestigationState(TypedDict):
    """
    The supervisor's case file passed across graph nodes and persisted in SQLite.
    """

    # ── Investigation target ────────────────────────────────────────────────
    alert: str             # Alert summary: "Pod X in namespace Y is CrashLoopBackOff"
    namespace: str         # Kubernetes namespace: "prod", "staging", "default"
    pod_name: str          # Target pod name: "payments-api-7f8b9"

    # ── Subagent findings ───────────────────────────────────────────────────
    deploy_finding: str    # Distilled summary from Deploy Investigator
    log_finding: str       # Distilled summary from Log Investigator

    # ── Loop & Confidence control ───────────────────────────────────────────
    iteration_count: int   # Number of synthesis rounds executed
    confidence: float      # Hypothesis confidence score (0.0 to 1.0)

    # ── Human Review & Steering ─────────────────────────────────────────────
    human_feedback: str    # Steering notes or instructions from human reviewer
    feedback_action: str   # "approve" | "reinvestigate" | "override"

    # ── Final Output ────────────────────────────────────────────────────────
    root_cause: str        # The verified or synthesized root cause