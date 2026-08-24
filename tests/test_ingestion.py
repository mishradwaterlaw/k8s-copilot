"""
tests/test_ingestion.py — Unit & Integration tests for Webhook Alert Ingestion & Deduplication.

WHAT WE'RE TESTING:
  1. `AlertmanagerWebhookPayload.to_normalized_events`:
     - Correctly extracts pod, namespace, container, and severity
     - Ignores 'resolved' alerts (only processes active 'firing' alerts)
     - Generates deterministic fingerprints for deduplication
  2. `AlertDeduplicator`:
     - First alert passes (is_duplicate=False)
     - Immediate repeat of same fingerprint is suppressed (is_duplicate=True)
     - Distinct fingerprints both pass
     - Expired entries after TTL pass again
  3. FastAPI Webhook Routes (/webhook/alertmanager and /webhook/generic):
     - Return HTTP 202 Accepted in milliseconds
     - Background task is queued properly
"""

import pytest
import time
from fastapi.testclient import TestClient
from main import app
from ingestion.models import AlertmanagerWebhookPayload, GenericAlertPayload, AlertEvent
from ingestion.dedup import AlertDeduplicator


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def dedup():
    return AlertDeduplicator(ttl_seconds=2)


@pytest.fixture
def sample_alertmanager_payload():
    return {
        "version": "4",
        "groupKey": "test-group",
        "status": "firing",
        "receiver": "k8s-copilot-webhook",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "prod",
                    "pod": "payments-api-7f8b9",
                    "container": "payments-api",
                    "node": "node-3",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Pod payments-api-7f8b9 is crashing repeatedly",
                    "description": "Back-off restarting failed container",
                },
                "fingerprint": "a1b2c3d4e5f6",
            },
            {
                "status": "resolved",
                "labels": {
                    "alertname": "KubePodNotReady",
                    "namespace": "prod",
                    "pod": "auth-service-xyz",
                },
                "annotations": {"summary": "Resolved alert"},
                "fingerprint": "resolved-1234",
            },
        ],
    }


class TestAlertNormalization:

    def test_alertmanager_payload_normalization(self, sample_alertmanager_payload):
        payload = AlertmanagerWebhookPayload(**sample_alertmanager_payload)
        events = payload.to_normalized_events()

        # Should only include the 1 FIRING alert, ignoring the 1 RESOLVED alert
        assert len(events) == 1
        event = events[0]

        assert isinstance(event, AlertEvent)
        assert event.alert_name == "KubePodCrashLooping"
        assert event.namespace == "prod"
        assert event.pod_name == "payments-api-7f8b9"
        assert event.container_name == "payments-api"
        assert event.node_name == "node-3"
        assert event.severity == "critical"
        assert event.fingerprint == "a1b2c3d4e5f6"
        assert "payments-api-7f8b9" in event.to_investigation_alert_text()

    def test_generic_alert_payload_normalization(self):
        payload = GenericAlertPayload(
            alert_name="PodOOMKilled",
            namespace="staging",
            pod_name="worker-pod-123",
            container_name="task-runner",
            severity="warning",
            summary="Container exceeded memory limit of 512Mi",
        )
        event = payload.to_normalized_event()
        assert event.alert_name == "PodOOMKilled"
        assert event.namespace == "staging"
        assert event.pod_name == "worker-pod-123"
        assert event.container_name == "task-runner"
        assert event.fingerprint is not None


class TestAlertDeduplicator:

    def test_deduplication_first_alert_passes(self, dedup):
        is_dup, tid = dedup.check_and_register("fp-001", "thread-1")
        assert is_dup is False
        assert tid == "thread-1"

    def test_deduplication_immediate_repeat_suppressed(self, dedup):
        dedup.check_and_register("fp-001", "thread-1")
        # Second call within TTL window
        is_dup, tid = dedup.check_and_register("fp-001", "thread-2")
        assert is_dup is True
        assert tid == "thread-1"  # Returns original thread_id

    def test_distinct_fingerprints_both_pass(self, dedup):
        is_dup1, _ = dedup.check_and_register("fp-001", "thread-1")
        is_dup2, _ = dedup.check_and_register("fp-002", "thread-2")
        assert is_dup1 is False
        assert is_dup2 is False

    def test_expired_fingerprint_passes_again(self, dedup):
        dedup.check_and_register("fp-001", "thread-1")
        time.sleep(2.1)  # Exceed TTL of 2s
        is_dup, tid = dedup.check_and_register("fp-001", "thread-3")
        assert is_dup is False
        assert tid == "thread-3"


class TestWebhookEndpoints:

    def test_alertmanager_webhook_returns_202_accepted(self, client, sample_alertmanager_payload, mocker):
        # Mock background execution so test doesn't run real graph
        mocker.patch("ingestion.router._run_background_investigation")

        response = client.post("/webhook/alertmanager", json=sample_alertmanager_payload)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["total_firing_alerts"] == 1
        assert len(data["dispatched_investigations"]) == 1
        assert data["dispatched_investigations"][0]["pod"] == "payments-api-7f8b9"

    def test_generic_webhook_returns_202_accepted(self, client, mocker):
        mocker.patch("ingestion.router._run_background_investigation")

        body = {
            "alert_name": "TestAlert",
            "namespace": "default",
            "pod_name": "test-pod-abc",
            "summary": "Manual test alert",
        }
        response = client.post("/webhook/generic", json=body)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["pod_name"] == "test-pod-abc"
