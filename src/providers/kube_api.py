"""
providers/kube_api.py — The REAL Kubernetes provider using the official Python SDK.

CONCEPT: SAME INTERFACE, REAL IMPLEMENTATION
════════════════════════════════════════════
Talks to a real Kubernetes cluster via the official `kubernetes` Python client.
Supports multi-container and init-container log extraction, pod status breakdowns,
node health condition queries, and container resource limits.
"""

from kubernetes import client, config as kube_config
from providers.base import KubeDataProvider


class KubeAPIProvider(KubeDataProvider):
    """
    Talks to a real Kubernetes cluster via the kubernetes Python SDK.
    """

    def __init__(self, kubeconfig_path: str | None = None):
        try:
            kube_config.load_incluster_config()
        except kube_config.ConfigException:
            kube_config.load_kube_config(config_file=kubeconfig_path)

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def get_pod_events(self, namespace: str, pod_name: str) -> str:
        events = self.core_v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )

        if not events.items:
            return f"No events found for pod {pod_name} in namespace {namespace}."

        lines = []
        for event in events.items:
            ts = event.last_timestamp or event.first_timestamp or "unknown"
            lines.append(
                f"{ts}  {event.type:<8}  {event.reason:<20}  {event.message}"
            )
        return "\n".join(lines)

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        container_name: str | None = None,
        tail_lines: int = 50,
    ) -> str:
        """
        Fetches container logs. If container_name is provided, fetches for that container specifically
        (supports both app containers, sidecars, and init containers).
        """
        try:
            kwargs = {
                "name": pod_name,
                "namespace": namespace,
                "tail_lines": tail_lines,
            }
            if container_name:
                kwargs["container"] = container_name

            logs = self.core_v1.read_namespaced_pod_log(**kwargs)
            return logs or f"No log output found for container '{container_name or 'primary'}'."
        except Exception as e:
            return f"Could not fetch logs for {pod_name} (container: {container_name}): {e}"

    def get_recent_deployments(self, namespace: str) -> str:
        deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)

        if not deployments.items:
            return f"No deployments found in namespace {namespace}."

        lines = []
        for deploy in deployments.items:
            name = deploy.metadata.name
            generation = deploy.metadata.generation
            ready = deploy.status.ready_replicas or 0
            desired = deploy.spec.replicas or 0
            containers = deploy.spec.template.spec.containers
            image = containers[0].image if containers else "unknown"

            status = "OK" if ready == desired else f"DEGRADED ({ready}/{desired} ready)"
            lines.append(f"  {name}  image={image}  generation={generation}  {status}")

        return f"Deployments in namespace {namespace}:\n" + "\n".join(lines)

    def get_pod_status(self, namespace: str, pod_name: str) -> str:
        """
        Returns full pod status with breakdown of:
          1. Init containers (status, exit code, waiting reason)
          2. App containers & sidecars (phase, restarts, waiting reason)
          3. Pod conditions and assigned node
        """
        try:
            pod = self.core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception as e:
            return f"Could not fetch pod {pod_name}: {e}"

        phase = pod.status.phase or "Unknown"
        node_name = pod.spec.node_name or "unassigned"

        # 1. Parse Init Containers
        init_lines = []
        for cs in (pod.status.init_container_statuses or []):
            if cs.state.waiting:
                init_lines.append(f"  - {cs.name}: Waiting ({cs.state.waiting.reason})")
            elif cs.state.terminated:
                init_lines.append(
                    f"  - {cs.name}: Terminated (exit code {cs.state.terminated.exit_code}, "
                    f"reason: {cs.state.terminated.reason})"
                )
            elif cs.state.running:
                init_lines.append(f"  - {cs.name}: Running")

        # 2. Parse App Containers / Sidecars
        container_lines = []
        for cs in (pod.status.container_statuses or []):
            state_str = "Unknown"
            if cs.state.waiting:
                state_str = f"Waiting ({cs.state.waiting.reason})"
            elif cs.state.terminated:
                state_str = f"Terminated (exit code {cs.state.terminated.exit_code}, reason: {cs.state.terminated.reason})"
            elif cs.state.running:
                state_str = f"Running (ready: {cs.ready})"

            container_lines.append(
                f"  - {cs.name}: State={state_str}, Restarts={cs.restart_count}, Ready={cs.ready}"
            )

        # 3. Pod Conditions
        conditions = [f"{c.type}={c.status}" for c in (pod.status.conditions or [])]

        init_section = "\n".join(init_lines) if init_lines else "  None"
        container_section = "\n".join(container_lines) if container_lines else "  None"

        return (
            f"Pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Phase: {phase}\n"
            f"Host Node: {node_name}\n\n"
            f"Init Containers:\n{init_section}\n\n"
            f"App Containers & Sidecars:\n{container_section}\n\n"
            f"Conditions: {', '.join(conditions)}"
        )

    def get_related_pods(self, namespace: str, label_selector: str) -> dict[str, str]:
        pods = self.core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )

        result = {}
        for pod in pods.items:
            pod_name = pod.metadata.name
            phase = pod.status.phase or "Unknown"
            if pod.status.container_statuses:
                cs = pod.status.container_statuses[0]
                if cs.state.waiting:
                    summary = f"{cs.state.waiting.reason} (restarts: {cs.restart_count})"
                elif cs.state.running:
                    summary = "Running"
                else:
                    summary = f"Terminated (exit {cs.state.terminated.exit_code if cs.state.terminated else '?'})"
            else:
                summary = phase
            result[pod_name] = summary

        return result

    def get_node_conditions(self, node_name: str) -> str:
        """Queries the Kubernetes API for the health conditions of the node."""
        try:
            node = self.core_v1.read_node_status(name=node_name)
        except Exception as e:
            return f"Could not fetch node '{node_name}': {e}"

        conditions = []
        for c in (node.status.conditions or []):
            conditions.append(f"  {c.type}: {c.status} ({c.message or c.reason})")

        allocatable = node.status.allocatable or {}
        cpu = allocatable.get("cpu", "unknown")
        memory = allocatable.get("memory", "unknown")

        return (
            f"Node: {node_name}\n"
            f"Allocatable Capacity: CPU={cpu}, Memory={memory}\n"
            f"Conditions:\n" + "\n".join(conditions)
        )

    def get_resource_limits(self, namespace: str, pod_name: str) -> str:
        """Fetches resource requests and limits for all containers in the pod."""
        try:
            pod = self.core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception as e:
            return f"Could not fetch resource limits for pod '{pod_name}': {e}"

        lines = [f"Resource Spec for Pod {pod_name}:"]
        for c in pod.spec.containers:
            reqs = c.resources.requests if c.resources else {}
            limits = c.resources.limits if c.resources else {}
            lines.append(
                f"  Container '{c.name}':\n"
                f"    Requests: CPU={reqs.get('cpu', 'unspecified')}, Memory={reqs.get('memory', 'unspecified')}\n"
                f"    Limits:   CPU={limits.get('cpu', 'unspecified')}, Memory={limits.get('memory', 'unspecified')}"
            )
        return "\n".join(lines)
