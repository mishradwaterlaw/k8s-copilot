"""
providers/base.py — The abstract interface every data provider must implement.

CONCEPT: THE PROVIDER PATTERN (also called "Strategy Pattern")
═══════════════════════════════════════════════════════════════
The core idea: define WHAT data the agent needs (the interface),
but not HOW to get it (the implementation).

This lets you have multiple "providers" that all look identical
to the rest of the codebase:
  - FixtureProvider → returns hardcoded strings (for dev/testing/evals)
  - KubeAPIProvider → calls the real Kubernetes API (for production)
  - MockProvider    → returns controlled data (for unit tests)

MULTI-CONTAINER & DIAGNOSTIC EXTENSIONS:
  - Pod status returns per-container & init-container breakdowns.
  - Pod logs support specific container selection (critical for sidecars / init containers).
  - Node conditions allow checking if a node is under MemoryPressure / DiskPressure.
  - Resource limits allow comparing CPU/Memory requests & limits against pod failure modes.
"""

from abc import ABC, abstractmethod


class KubeDataProvider(ABC):
    """
    Abstract interface for all Kubernetes data sources.

    Any class that inherits from KubeDataProvider MUST implement
    every @abstractmethod below.
    """

    @abstractmethod
    def get_pod_events(self, namespace: str, pod_name: str) -> str:
        """
        Return recent Kubernetes events for a specific pod.
        Returns timestamp, type, reason, and message.
        """
        ...

    @abstractmethod
    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        container_name: str | None = None,
        tail_lines: int = 50,
    ) -> str:
        """
        Return the most recent log lines from a pod's container.
        
        container_name: Optional name of the specific container or init-container.
                        If None, returns logs from the primary/first failing container.
        tail_lines: How many lines from the end to return.
        """
        ...

    @abstractmethod
    def get_recent_deployments(self, namespace: str) -> str:
        """
        Return recent deployment history for the namespace.
        Includes what changed, when, and who triggered it.
        """
        ...

    @abstractmethod
    def get_pod_status(self, namespace: str, pod_name: str) -> str:
        """
        Return the full status of a pod — phase, conditions, and per-container
        breakdown for both init-containers and app containers/sidecars.
        """
        ...

    @abstractmethod
    def get_related_pods(self, namespace: str, label_selector: str) -> dict[str, str]:
        """
        Find all other pods matching a label selector to check if an issue
        is isolated or deployment-wide.
        """
        ...

    @abstractmethod
    def get_node_conditions(self, node_name: str) -> str:
        """
        Check the status and health conditions of a Kubernetes node hosting the pod.
        Returns Ready status, MemoryPressure, DiskPressure, and PIDPressure.
        """
        ...

    @abstractmethod
    def get_resource_limits(self, namespace: str, pod_name: str) -> str:
        """
        Return the configured CPU and Memory resource requests and limits for all
        containers inside the specified pod (crucial for diagnosing OOMKilled issues).
        """
        ...
