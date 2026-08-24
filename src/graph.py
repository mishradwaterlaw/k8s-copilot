"""
graph.py — The SUPERVISOR graph: assembles nodes, edges, and the checkpointer.

WHAT CHANGED:
  - graph now accepts namespace and pod_name instead of just alert
  - Uses config.py for MAX_ITERATIONS and CONFIDENCE_THRESHOLD
    (instead of hardcoded module-level constants)
  - CHECKPOINT_DB_PATH comes from config too (for Docker volume mounting)

WHAT STAYED THE SAME:
  The STRUCTURE of the graph is identical:
    START → [deploy_investigator, log_investigator] (parallel)
          → synthesize
          → (conditional) human_review or loop back
          → END

  This is intentional. The provider pattern is an INTERNAL implementation
  detail — the graph's behavior from the outside is identical.
  Same inputs, same outputs, same human-in-the-loop point.
  Only the data source changed.

CONCEPT: WHY SQLITE AND NOT MEMORY?
  SqliteSaver writes each checkpoint (state snapshot) to a .db file.
  This means:
    - The FastAPI server can restart and investigations survive.
    - A human can take hours to review — the paused investigation waits.
    - Multiple investigations (different thread_ids) can run concurrently.
    - You can query the DB directly to see all past investigation states.

  MemorySaver (in-process dict) loses everything if the process dies.
  For a server that might restart (K8s restarts containers!), you MUST
  use a persistent checkpointer. SQLite on a PersistentVolumeClaim is
  the simplest production-ready option.

CONCEPT: check_same_thread=False
  SQLite by default only allows the thread that created the connection to use it.
  FastAPI uses a thread pool for request handlers — different requests may
  run on different threads. Setting check_same_thread=False lets SQLite
  work safely across threads. This is safe here because our application-level
  locking (one investigation per thread_id at a time) prevents concurrent
  writes to the same checkpoint.
"""

import sqlite3
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from state import InvestigationState
from nodes import call_deploy_investigator, call_log_investigator, synthesize, human_review
import config


def route_after_synthesize(state: InvestigationState) -> str | list[str]:
    """
    Conditional router: after synthesize(), decide what happens next.

    Returns:
      - "human_review" string → go to single node
      - ["call_deploy_investigator", "call_log_investigator"] list → fan out to BOTH
        (LangGraph interprets a list return from a conditional edge as "run all of these next")

    This is the loop control:
      High confidence OR max iterations hit → done, send to human review
      Low confidence AND iterations remaining → loop back for another round
    """
    if state["confidence"] >= config.CONFIDENCE_THRESHOLD:
        return "human_review"
    if state["iteration_count"] >= config.MAX_ITERATIONS:
        return "human_review"  # Force review even with low confidence — don't loop forever
    # Return a LIST → LangGraph fans out to both nodes in parallel
    return ["call_deploy_investigator", "call_log_investigator"]


def build_graph():
    """
    Assemble and compile the supervisor graph.

    Called ONCE at startup (in main.py and cli/app.py) and reused
    across all requests. Graph compilation is not cheap — it validates
    the graph structure, resolves node names, etc.

    Returns a compiled CompiledStateGraph that has an .invoke() and .stream() method.
    """
    builder = StateGraph(InvestigationState)

    # Add nodes — each is a Python function that takes state → returns partial update
    builder.add_node("call_deploy_investigator", call_deploy_investigator)
    builder.add_node("call_log_investigator", call_log_investigator)
    builder.add_node("synthesize", synthesize)
    builder.add_node("human_review", human_review)

    # START → BOTH investigators in parallel.
    # When you add_edge(START, two different nodes), LangGraph runs them
    # concurrently (as threads). Their results are merged into state
    # using each field's reducer before the next node runs.
    builder.add_edge(START, "call_deploy_investigator")
    builder.add_edge(START, "call_log_investigator")

    # Both investigators → synthesize.
    # LangGraph waits for BOTH parallel branches to finish before synthesize runs.
    # This is an implicit "join" — you don't have to code it. LangGraph handles it.
    builder.add_edge("call_deploy_investigator", "synthesize")
    builder.add_edge("call_log_investigator", "synthesize")

    # Conditional routing after synthesize
    builder.add_conditional_edges("synthesize", route_after_synthesize)

    builder.add_edge("human_review", END)

    # Set up the persistent checkpointer
    conn = sqlite3.connect(config.CHECKPOINT_DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    # compile() validates the graph (no orphan nodes, all edges valid)
    # and returns a runnable object.
    # interrupt_before is NOT set here — the interrupt() call inside human_review
    # handles pausing. This gives the node itself control over WHEN to pause,
    # which is more flexible than graph-level interrupts.
    return builder.compile(checkpointer=checkpointer)