"""
config.py — Central configuration for k8s-copilot.

WHY THIS FILE EXISTS:
  Before, settings were scattered — the LLM was instantiated in nodes.py,
  the provider type was hardcoded in tools.py, etc. That makes it hard to
  change behavior (e.g., switch from fixture data to a real cluster) without
  editing multiple files.

  A single config module fixes this. It reads from environment variables so
  you can change behavior without touching code — the standard "12-factor app"
  pattern used in real production systems.

ENVIRONMENT VARIABLES (set in .env or docker-compose.yml):
  GOOGLE_API_KEY     — required, your Gemini API key
  LLM_MODEL          — optional, defaults to gemini-2.5-flash
  DATA_PROVIDER      — "fixture" (default) or "kube_api"
  KUBECONFIG         — path to kube config file (only needed for kube_api)
  KUBE_NAMESPACE     — Kubernetes namespace to investigate (default: "default")
  CHECKPOINT_DB_PATH — where to store SQLite checkpoint file
"""

import os
from dotenv import load_dotenv

# load_dotenv() reads a .env file in the current directory and injects its
# key=value pairs into os.environ. This is a development convenience —
# in production (Docker / K8s), you set env vars directly, not via .env.
load_dotenv()


# ─────────────────────────────────────────────────────────────
# LLM SETTINGS
# ─────────────────────────────────────────────────────────────

# os.getenv(key, default) reads an environment variable.
# If not set, it falls back to the default value.
# This is how you configure behavior without changing code.
LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# Temperature controls LLM randomness.
#   0.0 = fully deterministic (same input → same output, every time)
#   1.0 = very creative/varied
# For an analysis tool that needs consistent reasoning, 0 is correct.
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))


# ─────────────────────────────────────────────────────────────
# DATA PROVIDER — the key architectural decision
# ─────────────────────────────────────────────────────────────
# This controls WHETHER the tools return fake data (fixtures) or
# call the real Kubernetes API.
#
# "fixture" → uses hardcoded strings from fixtures/cluster_data.py
# "kube_api" → uses the kubernetes Python SDK to query a real cluster
#
# By making this an env var, you can switch modes without touching code:
#   DATA_PROVIDER=kube_api python -m cli.app investigate ...
DATA_PROVIDER: str = os.getenv("DATA_PROVIDER", "fixture")


# ─────────────────────────────────────────────────────────────
# KUBERNETES SETTINGS (only relevant when DATA_PROVIDER=kube_api)
# ─────────────────────────────────────────────────────────────

# KUBECONFIG: path to your kubectl config file.
# If None, the kubernetes SDK will try the default location: ~/.kube/config
# When running INSIDE a cluster (as a K8s Pod), it auto-detects via
# the ServiceAccount token mounted at /var/run/secrets/... — we handle
# both cases in the kube_api provider.
KUBECONFIG_PATH: str | None = os.getenv("KUBECONFIG", None)

# The namespace to investigate. In real usage, this would be passed
# per-investigation (not global), but having a default simplifies demos.
KUBE_NAMESPACE: str = os.getenv("KUBE_NAMESPACE", "default")


# ─────────────────────────────────────────────────────────────
# GRAPH / PERSISTENCE SETTINGS
# ─────────────────────────────────────────────────────────────

# Where the SQLite checkpoint database lives.
# SQLite is a single-file database — great for local dev/demos.
# For production on K8s, you'd back this with a PersistentVolumeClaim
# or swap to PostgreSQL. The graph code doesn't need to change — only
# this path (or the checkpointer class).
CHECKPOINT_DB_PATH: str = os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db")

# How many investigation loops before we force a human review,
# regardless of confidence. Prevents infinite loops.
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "3"))

# Confidence level (0.0 to 1.0) above which we trust the AI's answer
# and send it to human review (rather than looping for more evidence).
CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))


# ─────────────────────────────────────────────────────────────
# OBSERVABILITY & TRACING (LangSmith)
# ─────────────────────────────────────────────────────────────
# LangSmith provides production tracing for LLM pipelines and LangGraph agents.
# When LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY is provided, LangChain
# automatically streams run spans, inputs, outputs, token counts, and tool calls
# to https://smith.langchain.com without requiring manual code decorators.
LANGSMITH_TRACING: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() in ("true", "1")
LANGSMITH_API_KEY: str | None = os.getenv("LANGCHAIN_API_KEY", None)
LANGSMITH_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "k8s-copilot")

