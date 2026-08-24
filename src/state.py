"""
state.py — The supervisor graph's shared "case file."

WHAT CHANGED:
  Added `namespace` and `pod_name` fields — the target of the investigation.
  Before, these were baked into the fixtures. Now they're runtime inputs
  that get passed through the graph to every node that needs them.

  Removed `evidence` — it was a leftover from the single-graph version.
  In the multi-agent version, each subgraph has its own private evidence list.
  The supervisor only sees the final distilled `finding` from each subagent.

CONCEPT: WHY TYPEDDICT?
  LangGraph states are TypedDicts (not Pydantic models, not dataclasses).
  
  TypedDict is a pure Python type annotation construct — at runtime, it's
  just a plain dict. There's no runtime validation, no __init__, no methods.
  
  Why not Pydantic? LangGraph chose TypedDict because:
    1. LangGraph needs to MERGE partial updates into state. Pydantic models
       don't support partial updates natively — you'd need to convert to dict
       and back on every node update.
    2. TypedDict is transparent — no hidden logic, no coercions. What you put
       in is what you get out. Important for debugging agentic systems.
    3. Reducers (via Annotated) integrate cleanly: LangGraph reads the
       annotations at compile time to know how to merge each field.

CONCEPT: ANNOTATED AND REDUCERS
  Python's Annotated[T, metadata] attaches extra information to a type.
  LangGraph reads the second argument as a REDUCER function.
  
  Annotated[list[str], operator.add] means:
    "This field is a list[str], and when two values need to be merged,
    use operator.add (which for lists means concatenate)."
  
  Without a reducer, the default behavior is REPLACE (new value overwrites old).
  That's correct for strings like root_cause and log_finding — each loop
  produces a fresh hypothesis that supersedes the previous one.
"""

import operator
from typing import TypedDict, Annotated


class InvestigationState(TypedDict):
    """
    The supervisor's "case file" — passed to every supervisor node.
    Subgraphs have their own private state (DeployState, LogState)
    and only expose their final `finding` string to the supervisor.
    """

    # ── Investigation target (set at start, never modified) ─────────────────
    alert: str         # The alert text: "Pod X in namespace Y is CrashLoopBackOff"
    namespace: str     # Kubernetes namespace: "prod", "staging", "default"
    pod_name: str      # The specific pod: "payments-api-7f8b9"

    # ── Each subagent's one-sentence conclusion ──────────────────────────────
    # Plain REPLACE reducer (default). Each investigation loop overwrites the
    # previous finding with a fresher one — we don't accumulate findings.
    deploy_finding: str
    log_finding: str

    # ── Loop control ─────────────────────────────────────────────────────────
    iteration_count: int   # How many synthesize() rounds we've run
    confidence: float      # Current confidence in the root_cause (0.0 to 1.0)

    # ── Final output ─────────────────────────────────────────────────────────
    root_cause: str        # The best-guess root cause (AI's or human's override)