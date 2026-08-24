"""
nodes.py — Node functions for the SUPERVISOR graph.

WHAT CHANGED:
  Before: call_deploy_investigator and call_log_investigator built
          subgraphs with no provider injection — they relied on
          module-level fixture imports.

  After:  Both supervisor nodes get the provider, namespace, and pod_name
          from the investigation state, then pass them into the subgraph builders.

  All other nodes (synthesize, human_review) are unchanged — they only
  work with state data (strings, floats) and the LLM, so provider injection
  doesn't apply to them.

IMPORTANT PATTERN — STATE AS THE COMMUNICATION CHANNEL:
  Notice how provider, namespace, and pod_name are stored in the state.
  LangGraph nodes communicate ONLY through state — not through function
  arguments (those are always just the state dict), not through global
  variables (fragile, not thread-safe), not through class instances.

  State is the single source of truth for everything an investigation
  knows. This is what makes investigations resumable: if the graph
  pauses (interrupt), the state is saved to the checkpointer. When
  it resumes, everything needed is right there in the state.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.types import interrupt

from state import InvestigationState
import config


def _get_llm():
    """Lazy LLM instantiation — same pattern as subgraphs.py."""
    return ChatGoogleGenerativeAI(
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE,
    )


def call_deploy_investigator(state: InvestigationState) -> dict:
    """
    SUPERVISOR NODE: Delegate to the Deploy Investigator subgraph.

    Reads:  alert, namespace, pod_name from state
    Writes: deploy_finding back to state

    The supervisor's job here is translation:
      1. Pull what the subgraph needs from supervisor state
      2. Run the subgraph (it's a black box)
      3. Put its output back into supervisor state

    The supervisor doesn't know or care what happens INSIDE the subgraph.
    That's the entire point of composition.
    """
    from subgraphs import build_deploy_subgraph
    from providers import get_provider
    # Local imports here (not top-level) to avoid circular imports.
    # nodes.py ← imports ← subgraphs.py (which imports tools.py, providers/).
    # If we imported at the top level, Python's import system could get
    # into a circular dependency issue. Local imports break the cycle.

    provider = get_provider()
    subgraph = build_deploy_subgraph(
        provider=provider,
        namespace=state["namespace"],
        pod_name=state["pod_name"],
    )
    result = subgraph.invoke({"alert": state["alert"]})
    return {"deploy_finding": result["finding"]}


def call_log_investigator(state: InvestigationState) -> dict:
    """
    SUPERVISOR NODE: Delegate to the Log Investigator subgraph.

    The Log Investigator is an autonomous tool-calling agent.
    We seed it with a system prompt (its instructions) and a human
    message (the alert). From there, it decides what to check.

    Reads:  alert, namespace, pod_name from state
    Writes: log_finding back to state
    """
    from subgraphs import build_log_subgraph
    from providers import get_provider
    from langchain_core.messages import SystemMessage, HumanMessage

    provider = get_provider()
    subgraph = build_log_subgraph(
        provider=provider,
        namespace=state["namespace"],
        pod_name=state["pod_name"],
    )

    # The system message gives the agent its persona and rules.
    # It's injected HERE (in the supervisor node) rather than hardcoded
    # inside the subgraph — this keeps the subgraph generic and reusable.
    system = SystemMessage(content=(
        f"You are an expert Kubernetes on-call triage agent investigating an alert in namespace '{state['namespace']}'. "
        "Use the available tools to gather concrete evidence:\n"
        "1. Check `get_pod_status` and `get_pod_events` to identify WHICH specific container (app, sidecar, or init-container) is failing.\n"
        "2. Call `get_app_logs(container_name=...)` on the failing container to retrieve the exact error or stack trace.\n"
        "3. Check `get_node_conditions` if node pressure/eviction is possible, and `get_resource_limits` if an OOMKilled crash occurred.\n"
        "4. Check related pods if needed to verify whether the symptom is isolated or deployment-wide.\n"
        "Once you have gathered sufficient evidence, respond with ONE precise sentence summarizing "
        "what you found and the evidence — do not call any more tools after you have enough information."
    ))
    human = HumanMessage(content=f"Alert: {state['alert']}")

    result = subgraph.invoke({
        "alert": state["alert"],
        "messages": [system, human],
    })
    return {"log_finding": result["finding"]}


def synthesize(state: InvestigationState) -> dict:
    """
    SUPERVISOR NODE: Read both investigators' findings and produce
    a confidence score + root cause hypothesis.

    This is the "judge" that decides whether we have enough evidence
    (high confidence) or need another investigation loop.

    The output format is strict (CONFIDENCE: / ROOT_CAUSE:) so we
    can parse it reliably. We use a simple line-by-line parser
    rather than JSON because the LLM can hallucinate JSON structure
    but rarely hallucinates a two-line "KEY: value" response when
    prompted to use exactly that format.
    """
    llm = _get_llm()

    prompt = f"""You are investigating this Kubernetes alert:
{state['alert']}

Pod: {state['pod_name']} | Namespace: {state['namespace']}

Deploy Investigator's finding:
{state['deploy_finding']}

Log Investigator's finding:
{state['log_finding']}

Be SKEPTICAL. A symptom (pod crashed) is not a root cause.
Only give HIGH confidence (0.8+) if the findings together trace back
to an actual underlying cause: a config change, missing dependency,
bad image, resource limit, DNS issue, etc.

Respond in EXACTLY this format — no other text:
CONFIDENCE: <a number between 0.0 and 1.0>
ROOT_CAUSE: <one sentence best-guess hypothesis, grounded ONLY in the findings above>"""

    response = llm.invoke(prompt).content.strip()

    # Parse the structured response.
    # We parse manually (not with JSON) because:
    #   1. The format is simple — only two lines to extract
    #   2. LLMs are reliable at "KEY: value" format, less so at JSON
    #   3. No import needed, no schema needed, easy to debug
    confidence = 0.0
    root_cause = "Unable to determine root cause."

    for line in response.splitlines():
        line = line.strip()
        if line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.replace("CONFIDENCE:", "").strip())
                # Clamp to valid range [0.0, 1.0] in case the LLM hallucinates
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                confidence = 0.0
        elif line.startswith("ROOT_CAUSE:"):
            root_cause = line.replace("ROOT_CAUSE:", "").strip()

    return {
        "confidence": confidence,
        "root_cause": root_cause,
        "iteration_count": state["iteration_count"] + 1,
    }


def human_review(state: InvestigationState) -> dict:
    """
    SUPERVISOR NODE: Pause the graph and wait for a human decision.

    interrupt() is a LangGraph primitive that:
      1. Saves the current state to the checkpointer (SQLite)
      2. Raises a special exception that the graph runner catches
      3. Returns control to the caller (FastAPI endpoint / CLI)
      4. The graph is now PAUSED — it will not advance until resumed

    When the human provides a decision (via API or CLI), the graph
    is resumed with Command(resume=decision) and this node continues
    from where it left off, with `decision` as the return value of interrupt().

    This is a coroutine-like pattern: interrupt() is like yield —
    it yields control to the caller and resumes later with a value.
    """
    decision = interrupt({
        "question": "Review the proposed root cause. Type 'approve' to accept, or enter an override.",
        "proposed_root_cause": state["root_cause"],
        "confidence": state["confidence"],
        "deploy_finding": state["deploy_finding"],
        "log_finding": state["log_finding"],
        "pod_name": state["pod_name"],
        "namespace": state["namespace"],
        "iterations_run": state["iteration_count"],
    })

    if decision.strip().lower() == "approve":
        # Human approved — no state change needed.
        # The root_cause and confidence already in state are correct.
        return {}
    else:
        # Human provided an override root cause.
        # Replace the AI's hypothesis with the human's correction.
        # This is important for the audit trail — the final root_cause
        # reflects what a human verified, not just what the AI guessed.
        return {"root_cause": decision.strip()}