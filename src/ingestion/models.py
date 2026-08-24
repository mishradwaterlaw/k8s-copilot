"""
src/ingestion/models.py — Normalized alert schemas and provider-specific webhook models.

CONCEPT: INGESTION BOUNDARY NORMALIZATION
═════════════════════════════════════════
Every monitoring system formats alerts differently:
  - Prometheus Alertmanager: { "alerts": [ { "labels": { "alertname": "...", "pod": "..." }, "fingerprint": "..." } ] }
  - Datadog: { "event_title": "...", "tags": ["pod:...", "namespace:..."], "id": 12345 }
  - PagerDuty: { "event": { "data": { "title": "...", "custom_details": {...} } } }

If your LangGraph agent directly consumed Alertmanager payloads, adding Datadog later
would require modifying graph nodes and prompts.

SOLUTION:
  Normalize all external payloads into ONE internal `AlertEvent` Pydantic model at the HTTP boundary.
  The rest of the system (graph, supervisors, subagents) ONLY speaks `AlertEvent`.
  Adding a new alert source is simply writing a parser function, without touching graph logic.

INTERVIEW TALKING POINT:
  "I decoupled alert ingestion from graph execution by normalizing incoming webhooks
  (Alertmanager, Datadog, Grafana) into an internal `AlertEvent` Pydantic schema.
  This enforces a clean boundary and allows adding new alert sources without changing the core graph."
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import hashlib
import time


class AlertEvent(BaseModel):
    """
    Internal normalized alert schema.
    Every external alert source (Alertmanager, Datadog, Grafana) is converted to this format.
    """
    fingerprint: str = Field(
        ...,
        description="Unique deterministic hash of the alert (used for deduplication & circuit breaking)."
    )
    alert_name: str = Field(
        ...,
        description="Name of the alert rule, e.g. KubePodCrashLooping or KubePodOOMKilled."
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace where the alert occurred."
    )
    pod_name: str = Field(
        ...,
        description="The specific pod name targeted by the alert."
    )
    container_name: Optional[str] = Field(
        default=None,
        description="Specific container or init-container name if specified in alert labels."
    )
    node_name: Optional[str] = Field(
        default=None,
        description="Kubernetes node name hosting the pod if present in labels."
    )
    severity: str = Field(
        default="warning",
        description="Alert severity: critical, warning, or info."
    )
    summary: str = Field(
        ...,
        description="Human-readable alert summary or description."
    )
    raw_labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Original key-value labels attached to the alert."
    )
    received_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp when the alert arrived at our webhook listener."
    )

    def to_investigation_alert_text(self) -> str:
        """Constructs the prompt string passed into the LangGraph supervisor."""
        container_info = f" (container: {self.container_name})" if self.container_name else ""
        return (
            f"Alert: {self.alert_name} — Pod {self.pod_name}{container_info} in namespace {self.namespace} "
            f"is firing with severity '{self.severity}'. Summary: {self.summary}"
        )


# ── Prometheus Alertmanager Webhook Payload Schemas ──────────────────────────

class AlertmanagerAlert(BaseModel):
    """Single alert object within an Alertmanager webhook notification."""
    status: str  # "firing" or "resolved"
    labels: Dict[str, str]
    annotations: Dict[str, str] = Field(default_factory=dict)
    startsAt: Optional[str] = None
    endsAt: Optional[str] = None
    generatorURL: Optional[str] = None
    fingerprint: Optional[str] = None


class AlertmanagerWebhookPayload(BaseModel):
    """
    Standard Alertmanager HTTP webhook POST body.
    See: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    """
    version: str = "4"
    groupKey: Optional[str] = None
    truncatedAlerts: int = 0
    status: str  # "firing" or "resolved"
    receiver: str
    groupLabels: Dict[str, str] = Field(default_factory=dict)
    commonLabels: Dict[str, str] = Field(default_factory=dict)
    commonAnnotations: Dict[str, str] = Field(default_factory=dict)
    externalURL: Optional[str] = None
    alerts: List[AlertmanagerAlert]

    def to_normalized_events(self) -> List[AlertEvent]:
        """
        Parses all 'firing' alerts in the payload into normalized AlertEvent objects.
        Filters out 'resolved' alerts.
        """
        events = []
        for alert in self.alerts:
            # We only investigate actively FIRING alerts, not resolved ones
            if alert.status.lower() != "firing":
                continue

            labels = alert.labels
            annotations = alert.annotations

            # Extract pod, namespace, and alert name with fallbacks
            pod_name = labels.get("pod") or labels.get("pod_name") or labels.get("instance") or "unknown-pod"
            namespace = labels.get("namespace") or "default"
            alert_name = labels.get("alertname") or labels.get("alert_name") or "KubernetesAlert"
            container_name = labels.get("container") or labels.get("container_name")
            node_name = labels.get("node") or labels.get("node_name")
            severity = labels.get("severity", "warning").lower()

            summary = (
                annotations.get("summary")
                or annotations.get("description")
                or annotations.get("message")
                or f"{alert_name} firing on pod {pod_name}"
            )

            # Determine fingerprint: use Alertmanager's or compute sha256 of key labels
            if alert.fingerprint:
                fp = alert.fingerprint
            else:
                raw_fp_str = f"{alert_name}:{namespace}:{pod_name}:{container_name}"
                fp = hashlib.sha256(raw_fp_str.encode("utf-8")).hexdigest()[:16]

            events.append(
                AlertEvent(
                    fingerprint=fp,
                    alert_name=alert_name,
                    namespace=namespace,
                    pod_name=pod_name,
                    container_name=container_name,
                    node_name=node_name,
                    severity=severity,
                    summary=summary,
                    raw_labels=labels,
                )
            )
        return events


# ── Generic Webhook Payload ──────────────────────────────────────────────────

class GenericAlertPayload(BaseModel):
    """
    Convenient simplified webhook schema for custom scripts, CI pipelines, or Grafana alerts.
    """
    alert_name: str
    namespace: str = "default"
    pod_name: str
    container_name: Optional[str] = None
    node_name: Optional[str] = None
    severity: str = "warning"
    summary: str
    fingerprint: Optional[str] = None

    def to_normalized_event(self) -> AlertEvent:
        if self.fingerprint:
            fp = self.fingerprint
        else:
            raw = f"{self.alert_name}:{self.namespace}:{self.pod_name}:{self.container_name}"
            fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

        return AlertEvent(
            fingerprint=fp,
            alert_name=self.alert_name,
            namespace=self.namespace,
            pod_name=self.pod_name,
            container_name=self.container_name,
            node_name=self.node_name,
            severity=self.severity,
            summary=self.summary,
        )
