"""
subgraphs.py — Two independent investigator subgraphs.

WHAT CHANGED vs. the original:
  Before: subgraphs imported tools directly from tools.py which had
          hardcoded fixture imports. No way to swap providers.

  After:  Both build_*_subgraph() functions now accept a `provider`,
          `namespace`, and `pod_name`. They pass these to make_tools()
          to get a properly configured tool set.

  The graph STRUCTURE is identical — same nodes, same edges, same
  tools_condition routing. Only the tool construction changed.

CONCEPT: WHY SUBGRAPHS?
  Each "investigator" is an entire graph with its own state.
  The supervisor doesn't know or care how each investigator works internally.
  This is true encapsulation:
    - The Deploy subgraph could be rewritten completely (async, parallel,
      using a different LLM) without the supervisor graph changing at all.
    - Each subgraph's state (evidence, messages, etc.) is private to it.
      The supervisor only ever sees the final "finding" string.

  Compare this to having everything in one big flat graph:
    - Shared state between all nodes → harder to reason about
    - One node's intermediate state pollutes the supervisor's view
    - No reuse — the deploy checker is tied to this specific investigation flow
"""

from typing import TypedDict, Annotated

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from providers.base import KubeDataProvider
from tools import make_tools, make_deploy_tools


def _get_llm():
    """
    Lazy LLM loader — imports and creates the LLM only when called.

    WHY LAZY?
      We don't want to instantiate ChatGoogleGenerativeAI at module import time.
      If the module is imported in a test or CLI context where no API key is
      configured yet (e.g., before .env is loaded), a module-level LLM would
      crash on import instead of crashing at the point where it's actually used.
      Lazy loading makes the error happen at the right time.
    """
    import os
    from langchain_google_genai import ChatGoogleGenerativeAI
    import config
    return ChatGoogleGenerativeAI(
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOY INVESTIGATOR SUBGRAPH
# ═══════════════════════════════════════════════════════════════════════════════
# Simple, single-purpose: call get_recent_deployments, summarize the finding.
# No tool-calling loop — just one fixed step.
# This is intentional: deploy history is simple enough that we don't need
# the model to decide what to fetch. We always fetch everything.

class DeployState(TypedDict):
    """
    Private state for the Deploy Investigator.
    The supervisor only ever sees `finding` — everything else stays here.

    TypedDict is just a dict with type annotations.
    It's not enforced at runtime (Python doesn't check dict contents),
    but it tells your IDE and type checker what keys to expect,
    which catches typos and wrong key names before you even run the code.
    """
    alert: str
    finding: str  # ← the ONLY output the supervisor will read


def build_deploy_subgraph(
    provider: KubeDataProvider,
    namespace: str,
    pod_name: str,
):
    """
    Build and return the Deploy Investigator subgraph.

    The subgraph is built fresh per-investigation (not cached as a module-level
    singleton) because it needs the runtime provider/namespace/pod_name.
    This is a small overhead — graph compilation is fast.
    """
    llm = _get_llm()
    deploy_tools = make_deploy_tools(provider, namespace)

    def summarize_deploy(state: DeployState) -> dict:
        """
        Node: Call get_recent_deployments, then ask the LLM to summarize
        whether a recent deploy is implicated in the alert.
        """
        # Get the tool's output by calling it directly (no tool-calling loop needed).
        # Tools are LangChain Runnables — they have an .invoke() method.
        deploy_tool = deploy_tools[0]  # get_recent_deployments
        raw_data = deploy_tool.invoke({})
        # invoke({}) → no arguments needed (this tool takes no params from LLM)

        prompt = f"""Alert: {state['alert']}

Deployment history for namespace {namespace}:
{raw_data}

Summarize in ONE sentence: Is a recent deployment likely to have caused
this alert? If yes, what specifically changed that could explain it?
If no recent deploy is suspicious, say so clearly."""

        finding = llm.invoke(prompt).content.strip()
        return {"finding": finding}

    builder = StateGraph(DeployState)
    builder.add_node("summarize_deploy", summarize_deploy)
    builder.add_edge(START, "summarize_deploy")
    builder.add_edge("summarize_deploy", END)

    # No checkpointer for subgraphs — they run start-to-finish in one shot.
    # Only the SUPERVISOR graph needs a checkpointer (for human-in-the-loop
    # persistence across HTTP requests). Subgraphs don't pause.
    return builder.compile()


# ═══════════════════════════════════════════════════════════════════════════════
# LOG INVESTIGATOR SUBGRAPH — TOOL-CALLING AGENT
# ═══════════════════════════════════════════════════════════════════════════════
# More sophisticated: the LLM decides which tools to call and how many times.
# This is a REACT-style agent (Reason + Act):
#   1. Agent sees the alert → reasons about what to check
#   2. Calls a tool → gets data back
#   3. Reasons again with new data → maybe calls another tool
#   4. When it has enough, produces a final text answer (no more tool calls)

class LogState(TypedDict):
    alert: str
    messages: Annotated[list, add_messages]
    # add_messages is a REDUCER: instead of replacing the messages list,
    # new messages are APPENDED and tool results are matched to their
    # tool_call_id. This is required for the tool-calling agent loop to work.
    finding: str


def build_log_subgraph(
    provider: KubeDataProvider,
    namespace: str,
    pod_name: str,
):
    llm = _get_llm()
    investigation_tools = make_tools(provider, namespace, pod_name)

    # bind_tools() attaches tool schemas to the LLM.
    # The LLM can now OUTPUT tool calls (structured JSON: name + args)
    # instead of plain text. This is the mechanism that makes ReAct work.
    llm_with_tools = llm.bind_tools(investigation_tools)

    def agent(state: LogState) -> dict:
        """
        The agent's turn: look at everything so far (system prompt + alert +
        any tool results) and either call a tool or give a final answer.

        The key: we call llm_with_tools, not llm.
        If the model decides it needs more data → response contains tool_calls.
        If it has enough → response is plain text (no tool_calls).
        tools_condition checks which it is and routes accordingly.
        """
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}
        # We return a list with ONE message. add_messages (the reducer)
        # appends it to the existing list — not replaces it.

    def extract_finding(state: LogState) -> dict:
        """
        Once the agent produces its final answer (no tool calls),
        pull the text out of the last message and store it as `finding`.
        This is what the supervisor will read.
        """
        return {"finding": state["messages"][-1].content}
        # [-1] = last element of the list. After the agent loop finishes,
        # the last message is always the agent's final text response.

    builder = StateGraph(LogState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(investigation_tools))
    # ToolNode is PREBUILT — you don't write it. It:
    #   1. Reads tool_calls from the last AIMessage
    #   2. Runs the corresponding tool functions
    #   3. Returns ToolMessage results (matched by tool_call_id)
    builder.add_node("extract_finding", extract_finding)

    builder.add_edge(START, "agent")

    # tools_condition: prebuilt conditional router.
    #   If agent's last message has tool_calls → route to "tools"
    #   If agent's last message is plain text → route to END (mapped to extract_finding)
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: "extract_finding"},
    )
    builder.add_edge("tools", "agent")  # loop: tools → agent → tools → ... until done
    builder.add_edge("extract_finding", END)

    return builder.compile()