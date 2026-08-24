# K8s Copilot v2.0

[![CI Pipeline](https://github.com/mishradwaterlaw/k8s-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/mishradwaterlaw/k8s-copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker Image](https://img.shields.io/badge/ghcr.io-mishradwaterlaw%2Fk8s--copilot-blue)](https://github.com/mishradwaterlaw/k8s-copilot/pkgs/container/k8s-copilot)

> **Agentic Kubernetes Investigation Assistant** — autonomous multi-agent root cause analysis using LangGraph, FastAPI, Prometheus observability, and a Rich CLI.
>
> 
> ### 🎥 Watch Live Demo

https://github.com/user-attachments/assets/0f24f7e0-ab64-4fde-8da3-2f28389fc6f4

##  What Problem Does This Solve?


When a Kubernetes alert triggers at 3:00 AM (e.g. `CrashLoopBackOff` or `OOMKilled`), on-call SREs and platform engineers spend 15–45 minutes manually

 correlating:
- Pod lifecycle events (`kubectl get events`)
- Container stdout/stderr error logs (`kubectl logs --previous`)
- Recent deployment revisions & config diffs (`kubectl get deployments`)
- Replicaset health across neighboring pods

**K8s Copilot automates this entire triage loop:**
1. **Parallel Sub-Agents**: Spawns a Deploy Investigator and a tool-calling Log Investigator simultaneously.
2. **Autonomous Evidence Gathering**: Uses ReAct tool-calling loops to inspect cluster states, retrieve pod logs, and inspect replicas dynamically.
3. **Hypothesis Synthesis**: Correlates multi-source evidence to output a confidence-scored root cause hypothesis.
4. **Stateful Human-in-the-Loop (HITL)**: Cleanly pauses execution at `human_review` using LangGraph interrupts and SQLite checkpointing, allowing on-call engineers to approve or override via CLI or REST API.

---

##  Architecture

```
Supervisor Graph (graph.py)
├── [Parallel Fan-Out] ──► Deploy Investigator Subgraph (checks rollouts & diffs)
│                      └──► Log Investigator Subgraph (ReAct Tool-Calling Agent Loop)
│                             ├── get_pod_events
│                             ├── get_app_logs
│                             ├── get_pod_status
│                             └── get_related_pod_status
├── synthesize (confidence score + root cause hypothesis)
├── [Conditional Router] ──► (Low confidence & loops < 3) ──► Re-runs investigation loop
│                        └──► (High confidence OR max loops) ──► human_review
└── human_review (interrupt() ──► saves checkpoint to SQLite ──► waits for resume command)
```

---

##  Repository Structure

```
k8s-copilot/
├── .github/workflows/
│   ├── ci.yml                 # Automated Ruff lint, Pytest, Helm lint, Docker build check
│   └── release.yml            # Automated multi-tag container release to GHCR on git tags
├── helm/k8s-copilot/          # Production Helm 3 Chart
│   ├── Chart.yaml             # Chart metadata (v1.0.0, appVersion: 2.0.0)
│   ├── values.yaml            # Least-privilege RBAC defaults, existingSecret pattern
│   └── templates/             # Deployments, Services, RBAC, ConfigMaps, PVC, Ingress
├── tests/                     # 27 Unit & Integration tests (mocked LLMs & TestClient)
│   ├── conftest.py            # Pytest fixtures and mock states
│   ├── test_providers.py      # Provider interface & factory tests
│   ├── test_tools.py          # Closure-injected tool tests
│   ├── test_graph_routing.py  # LangGraph state machine routing tests
│   └── test_api.py            # FastAPI /health and /metrics endpoint tests
├── src/
│   ├── config.py              # 12-factor configuration (env variables)
│   ├── state.py               # TypedDict state schema with Annotated reducers
│   ├── graph.py               # Supervisor graph with SqliteSaver checkpointer
│   ├── nodes.py               # Node functions + interrupt() HITL logic
│   ├── subgraphs.py           # Deploy + Log subgraphs with ToolNode agent loops
│   ├── tools.py               # Tool factories injected with KubeDataProvider closures
│   ├── metrics.py             # Prometheus metrics (Counters, Histograms, /metrics)
│   ├── main.py                # FastAPI server with /investigate, /resume, /metrics
│   ├── providers/             # Pluggable Provider Pattern (Fixture vs real Kubernetes SDK)
│   └── cli/                   # Typer + Rich interactive terminal interface
├── Dockerfile                 # Multi-stage build (slim runtime, non-root user)
├── docker-compose.yml         # Local orchestration environment
└── pyproject.toml             # Modern Python package definition (PEP 517/621)
```

---

##  Quick Start (3 Ways to Run)

### Option 1: Interactive Terminal CLI (Recommended for Demos)

```bash
# 1. Clone repository
git clone https://github.com/mishradwaterlaw/k8s-copilot.git
cd k8s-copilot

# 2. Configure environment
cp .env.example .env
# Add your Gemini API key to .env (GOOGLE_API_KEY=xxx)

# 3. Install in virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
pip install -r requirements.txt

# 4. Launch investigation in terminal
cd src
python -m cli.app investigate \
  --alert "Pod payments-api-7f8b9 in namespace prod is CrashLoopBackOff" \
  --namespace prod \
  --pod-name payments-api-7f8b9
```

---

### Option 2: Docker / Docker Compose

```bash
docker-compose up --build
```
- API is live at `http://localhost:8000`
- Swagger UI documentation at `http://localhost:8000/docs`
- Prometheus metrics at `http://localhost:8000/metrics`

---

### Option 3: Deploy into Kubernetes via Helm

```bash
# 1. Create namespace
kubectl create namespace copilot-system

# 2. Create API key secret out-of-band (never leak keys in Helm history!)
kubectl create secret generic k8s-copilot-secrets \
  --from-literal=GOOGLE_API_KEY=your_gemini_api_key \
  -n copilot-system

# 3. Deploy Helm Chart (least-privilege namespace-scoped RBAC by default)
helm install k8s-copilot ./helm/k8s-copilot -n copilot-system

# Optional: Opt-in to cluster-wide investigation across all namespaces
# helm install k8s-copilot ./helm/k8s-copilot --set rbac.clusterWide=true -n copilot-system
```

---

##  Running Automated Tests

```bash
pytest tests/ -v
```
Runs all 27 unit and integration tests (mocked LLM responses, deterministic execution in <5s).

---

##  Observability & Metrics

Prometheus metrics are exposed on `GET /metrics`:
- `k8s_copilot_investigations_total{status="completed|paused_for_review|failed"}`
- `k8s_copilot_investigation_duration_seconds` (latency distribution histogram)
- `k8s_copilot_synthesis_confidence_score` (confidence distribution histogram)
- `k8s_copilot_investigation_iterations_total` (supervisor loop histogram)
- `k8s_copilot_llm_calls_total{node_name="..."}` (LLM call counter per node)

---

##  License

MIT License. Designed and built by [mishradwaterlaw](https://github.com/mishradwaterlaw).

