"""
tools.py — LangChain tools backed by the pluggable provider.

CONCEPT: DEPENDENCY INJECTION INTO TOOLS
══════════════════════════════════════════
The tools do not reach out to global state. The data provider, target namespace,
and pod name are passed into `make_tools(provider, namespace, pod_name)`.
Each tool function captures these parameters in its closure scope.

DIAGNOSTIC & MULTI-CONTAINER EXTENSIONS:
  - `get_app_logs` accepts an optional `container_name` to pull logs from a specific
    init container, sidecar, or primary container.
  - `get_node_conditions` allows inspecting whether the host node is under MemoryPressure or DiskPressure.
  - `get_resource_limits` inspects container memory limits to diagnose OOMKilled root causes.
"""

from langchain_core.tools import tool
from providers.base import KubeDataProvider


def make_tools(provider: KubeDataProvider, namespace: str, pod_name: str) -> list:
    """
    Create the full investigation tool suite bound to a specific provider, namespace, and pod.
    """

    # ── Tool 1: Pod Events ─────────────────────────────────────────────────────
    @tool
    def get_pod_events() -> str:
        """Fetch recent Kubernetes events for the crashing pod — scheduling,
        container starts, restarts, BackOff warnings, and probe failures. Look here first
        to understand the pod's lifecycle and timeline of failure."""
        return provider.get_pod_events(namespace, pod_name)

    # ── Tool 2: App Logs (Multi-Container Aware) ───────────────────────────────
    @tool
    def get_app_logs(container_name: str = "") -> str:
        """Fetch the most recent 50 log lines from inside a pod container.
        Pass container_name (e.g. 'payments-api', 'envoy-sidecar', 'db-migration') to
        target a specific container or init-container. If empty, returns primary container logs."""
        target_container = container_name.strip() if container_name.strip() else None
        return provider.get_pod_logs(namespace, pod_name, container_name=target_container, tail_lines=50)

    # ── Tool 3: Pod Status (Init & Sidecar Breakdown) ──────────────────────────
    @tool
    def get_pod_status() -> str:
        """Get a detailed status breakdown of the crashing pod — phase, host node name,
        init-container completion states, app container states, and restart counts.
        Use this FIRST to identify WHICH specific container or init-container is failing."""
        return provider.get_pod_status(namespace, pod_name)

    # ── Tool 4: Related Pods ───────────────────────────────────────────────────
    @tool
    def get_related_pod_status(pod_id: str) -> str:
        """Check whether another pod replica in the same deployment shows the same
        symptom, to determine if the issue is isolated or deployment-wide.
        Call for one pod at a time using pod names discovered in the context."""
        app_label = f"app={pod_name.rsplit('-', 2)[0]}" if "-" in pod_name else f"app={pod_name}"
        all_pods = provider.get_related_pods(namespace, label_selector=app_label)
        if pod_id not in all_pods:
            valid = list(all_pods.keys())
            return f"Unknown pod_id: '{pod_id}'. Available pods: {valid}"
        return f"Pod {pod_id}: {all_pods[pod_id]}"

    # ── Tool 5: Node Conditions ────────────────────────────────────────────────
    @tool
    def get_node_conditions(node_name: str = "") -> str:
        """Check the health conditions of the Kubernetes node hosting the pod.
        Checks for MemoryPressure, DiskPressure, PIDPressure, and Kubelet Ready status.
        Useful when pods are evicted, failing to schedule, or node resources are constrained."""
        target_node = node_name.strip() if node_name.strip() else "node-3"
        return provider.get_node_conditions(target_node)

    # ── Tool 6: Resource Requests & Limits ─────────────────────────────────────
    @tool
    def get_resource_limits() -> str:
        """Fetch the configured CPU and Memory resource requests and limits for all containers
        in the pod. Essential for diagnosing OOMKilled (Out Of Memory) failures."""
        return provider.get_resource_limits(namespace, pod_name)

    return [
        get_pod_events,
        get_app_logs,
        get_pod_status,
        get_related_pod_status,
        get_node_conditions,
        get_resource_limits,
    ]


def make_deploy_tools(provider: KubeDataProvider, namespace: str) -> list:
    """
    Tool suite for the Deploy Investigator subgraph.
    """

    @tool
    def get_recent_deployments() -> str:
        """Fetch recent deployment rollout history for the namespace — what changed,
        which image tag was deployed, and whether any rollout is degraded or failing.
        Use this to check if a recent code or configuration release caused the incident."""
        return provider.get_recent_deployments(namespace)

    return [get_recent_deployments]