"""
providers/fixture.py — The "fake data" provider for development, demos, and deterministic evaluation.

CONCEPT: CONCRETE IMPLEMENTATION OF AN ABSTRACT INTERFACE
══════════════════════════════════════════════════════════
This class implements the KubeDataProvider interface using deterministic fixture data.
Provides multi-container breakdowns, init-container logs, node conditions, and resource limit queries.
"""

from providers.base import KubeDataProvider
from fixtures.cluster_data import (
    POD_EVENTS,
    RECENT_DEPLOYS,
    APP_LOGS,
    SOURCES,
    OTHER_PODS,
)

# Simulated Node Conditions
FIXTURE_NODE_CONDITIONS = """
Node: node-3
Conditions:
  Ready: True (Kubelet is posting ready status)
  MemoryPressure: False (Node has sufficient memory available)
  DiskPressure: False (Node has sufficient disk space)
  PIDPressure: False (Node has sufficient process IDs available)
Allocatable Capacity:
  CPU: 4 cores
  Memory: 16Gi
Current Node Allocations:
  Allocated CPU: 2.1 cores (52%)
  Allocated Memory: 8.4Gi (52%)
""".strip()

# Simulated Container Resource Limits
FIXTURE_RESOURCE_LIMITS = """
Pod: payments-api-7f8b9 (Namespace: prod)
Containers:
  - Container: payments-api (App Primary)
    Requests:
      CPU: 250m
      Memory: 256Mi
    Limits:
      CPU: 500m
      Memory: 512Mi
    Current Memory Usage: 180Mi (Normal)
  - Container: envoy-sidecar (Service Mesh Proxy)
    Requests:
      CPU: 50m
      Memory: 64Mi
    Limits:
      CPU: 100m
      Memory: 128Mi
    Current Memory Usage: 42Mi (Normal)
Init Containers:
  - Init Container: db-migration
    Status: Completed (Exit code: 0)
""".strip()


class FixtureProvider(KubeDataProvider):
    """
    Returns pre-defined fixture data.
    Offline, deterministic, and supports multi-container / init-container reasoning.
    """

    def get_pod_events(self, namespace: str, pod_name: str) -> str:
        _ = namespace, pod_name
        return POD_EVENTS

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        container_name: str | None = None,
        tail_lines: int = 50,
    ) -> str:
        _ = namespace, pod_name, tail_lines
        if container_name and container_name.lower() in ("envoy", "envoy-sidecar"):
            return "2026-08-19T10:02:16.120Z [info] Envoy proxy initialized successfully on port 15001\n2026-08-19T10:02:16.500Z [info] Forwarding traffic to 127.0.0.1:8080"
        elif container_name and container_name.lower() in ("db-migration", "init"):
            return "2026-08-19T10:02:15.000Z [info] Running database schema migration v24...\n2026-08-19T10:02:15.800Z [info] Schema migration complete. 0 errors."
        return APP_LOGS

    def get_recent_deployments(self, namespace: str) -> str:
        _ = namespace
        return RECENT_DEPLOYS

    def get_pod_status(self, namespace: str, pod_name: str) -> str:
        _ = namespace
        return f"""
Pod: {pod_name}
Namespace: {namespace}
Phase: Running (Containers Failing)
Host Node: node-3

Init Containers:
  - db-migration: Terminated (Completed, exit code: 0)

Containers:
  - payments-api (Primary Application):
      State: Waiting (CrashLoopBackOff)
      Last State: Terminated (Error, exit code: 1)
      Restart Count: 5
      Ready: False
  - envoy-sidecar (Service Mesh Proxy):
      State: Running
      Restart Count: 0
      Ready: True

Conditions:
  Initialized: True
  Ready: False
  ContainersReady: False
  PodScheduled: True
""".strip()

    def get_related_pods(self, namespace: str, label_selector: str) -> dict[str, str]:
        _ = namespace, label_selector
        return dict(OTHER_PODS)

    def get_node_conditions(self, node_name: str) -> str:
        _ = node_name
        return FIXTURE_NODE_CONDITIONS

    def get_resource_limits(self, namespace: str, pod_name: str) -> str:
        _ = namespace, pod_name
        return FIXTURE_RESOURCE_LIMITS
