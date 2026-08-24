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
    # into a circular dependency issue. Local imports br    provider = get_provider()
    subgraph = build_deploy_subgraph(
        provider=provider,
        namespace=state["namespace"],
        pod_name=state["pod_name"],
    )

    alert_text = state["alert"]
    if state.get("human_feedback"):
        alert_text += f"\n[Human Reviewer Feedback/Guidance: '{state['human_feedback']}']"

    result = subgraph.invoke({"alert": alert_text})
    return {"deploy_finding": result["finding"]}


def call_log_investigator(state: InvestigationState) -> dict:
    """
    SUPERVISOR NODE: Delegate to the Log Investigator subgraph.
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

    feedback_prompt = ""
    if state.get("human_feedback"):
        feedback_prompt = (
            f"\n\nIMPORTANT OPERATOR FEEDBACK: The human on-call engineer reviewed previous findings "
            f"and requested: '{state['human_feedback']}'. Specifically focus your tools and reasoning on this guidance."
        )

    system = SystemMessage(content=(
        f"You are an expert Kubernetes on-call triage agent investigating an alert in namespace '{state['namespace']}'. "
        "Use the available tools to gather concrete evidence:\n"
        "1. Check `get_pod_status` and `get_pod_events` to identify WHICH specific container (app, sidecar, or init-container) is failing.\n"
        "2. Call `get_app_logs(container_name=...)` on the failing container to retrieve the exact error or stack trace.\n"
        "3. Check `get_node_conditions` if node pressure/eviction is possible, and `get_resource_limits` if an OOMKilled crash occurred.\n"
        "4. Check related pods if needed to verify whether the symptom is isolated or deployment-wide."
        f"{feedback_prompt}\n"
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
    """
    llm = _get_llm()

    feedback_context = ""
    if state.get("human_feedback"):
        feedback_context = f"\nHuman Reviewer Guidance: {state['human_feedback']}"

    prompt = f"""You are investigating this Kubernetes alert:
{state['alert']}

Pod: {state['pod_name']} | Namespace: {state['namespace']}{feedback_context}

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

    confidence = 0.0
    root_cause = "Unable to determine root cause."

    for line in response.splitlines():
        line = line.strip()
        if line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.replace("CONFIDENCE:", "").strip())
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
    SUPERVISOR NODE: Pause the graph and wait for a human decision or steering feedback.

    Three response types:
      1. 'approve' / 'ok' / 'yes' -> Accepts proposed root cause -> completes graph
      2. 'override: <text>'       -> Manually overrides root cause -> completes graph
      3. Any other text            -> Re-investigate! Feeds human instruction back to agents
    """
    decision_raw = interrupt({
        "question": "Review the proposed root cause. Type 'approve' to accept, 'override: <text>' to set manually, or enter feedback to re-investigate.",
        "proposed_root_cause": state["root_cause"],
        "confidence": state["confidence"],
        "deploy_finding": state["deploy_finding"],
        "log_finding": state["log_finding"],
        "pod_name": state["pod_name"],
        "namespace": state["namespace"],
        "iterations_run": state["iteration_count"],
    })

    decision = str(decision_raw).strip()
    decision_lower = decision.lower()

    if decision_lower in ("approve", "ok", "yes", "y", "accept"):
        return {"feedback_action": "approve"}
    elif decision_lower.startswith("override:"):
        manual_root_cause = decision.split("override:", 1)[1].strip()
        return {
            "root_cause": manual_root_cause,
            "feedback_action": "override",
        }
    else:
        # Operator entered steering feedback (e.g. "check again", "look at db host")
        return {
            "human_feedback": decision,
            "feedback_action": "reinvestigate",
            "iteration_count": 0,  # Reset loop budget so agents have full rounds with human guidance
        }