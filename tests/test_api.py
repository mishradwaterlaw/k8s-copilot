"""
tests/test_api.py — Integration tests for FastAPI endpoints (/health, /metrics, /investigate).

WHAT WE'RE TESTING:
  - GET /health returns {"status": "ok", "version": "2.0.0"} (used by Kubernetes liveness/readiness probes)
  - GET /metrics returns standard Prometheus text format containing our custom counters & histograms
  - POST /investigate with mocked graph execution updates Prometheus metrics properly

CONCEPT: FASTAPI TESTCLIENT
═════════════════════════════
`TestClient` from `starlette.testclient` (or `httpx`) lets us make simulated HTTP requests
to the FastAPI application in-memory without starting a real TCP network server.
This makes API endpoint tests fast (<100ms) and fully deterministic.
"""

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    """Returns a FastAPI TestClient instance."""
    return TestClient(app)


class TestApiEndpoints:

    def test_health_probe_endpoint(self, client):
        """K8s liveness & readiness probe endpoint must return 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "2.0.0"

    def test_prometheus_metrics_endpoint(self, client):
        """
        Prometheus scraper endpoint must return 200 OK and text format with our custom metrics.
        Verifies that k8s_copilot_* metrics are exposed properly.
        """
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        text = response.text

        # Verify our custom metrics are registered and exposed
        assert "k8s_copilot_investigations_total" in text
        assert "k8s_copilot_investigation_duration_seconds" in text
        assert "k8s_copilot_synthesis_confidence_score" in text
        assert "k8s_copilot_investigation_iterations_total" in text
