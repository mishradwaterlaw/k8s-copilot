"""
tests/test_providers.py — Unit tests for the provider layer.

WHAT WE'RE TESTING:
  - FixtureProvider implements KubeDataProvider interface completely
  - Provider returns multi-container status, init container logs, node conditions, resource limits
  - Factory function (get_provider) handles valid & invalid DATA_PROVIDER settings
"""

import pytest
import os


class TestFixtureProvider:

    def test_implements_interface(self, fixture_provider):
        from providers.base import KubeDataProvider
        assert isinstance(fixture_provider, KubeDataProvider)

    def test_get_pod_events_returns_string(self, fixture_provider):
        result = fixture_provider.get_pod_events("default", "test-pod")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_pod_logs_default_and_container(self, fixture_provider):
        default_logs = fixture_provider.get_pod_logs("default", "test-pod")
        assert isinstance(default_logs, str)
        assert len(default_logs) > 0

        sidecar_logs = fixture_provider.get_pod_logs("default", "test-pod", container_name="envoy-sidecar")
        assert "Envoy" in sidecar_logs or "envoy" in sidecar_logs.lower()

        init_logs = fixture_provider.get_pod_logs("default", "test-pod", container_name="db-migration")
        assert "migration" in init_logs.lower()

    def test_get_recent_deployments_returns_string(self, fixture_provider):
        result = fixture_provider.get_recent_deployments("default")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_pod_status_contains_init_and_app_containers(self, fixture_provider):
        pod_name = "payments-api-7f8b9"
        result = fixture_provider.get_pod_status("prod", pod_name)
        assert pod_name in result
        assert "Init Containers" in result
        assert "db-migration" in result
        assert "envoy-sidecar" in result

    def test_get_node_conditions(self, fixture_provider):
        result = fixture_provider.get_node_conditions("node-3")
        assert "node-3" in result
        assert "MemoryPressure" in result
        assert "DiskPressure" in result

    def test_get_resource_limits(self, fixture_provider):
        result = fixture_provider.get_resource_limits("prod", "payments-api-7f8b9")
        assert "Requests" in result
        assert "Limits" in result
        assert "payments-api" in result

    def test_get_related_pods_returns_dict(self, fixture_provider):
        result = fixture_provider.get_related_pods("prod", "app=payments-api")
        assert isinstance(result, dict)
        for key, value in result.items():
            assert isinstance(key, str)
            assert isinstance(value, str)


class TestProviderFactory:

    def test_fixture_provider_returned_by_default(self, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "fixture")

        import importlib
        import config
        importlib.reload(config)

        from providers import get_provider
        from providers.fixture import FixtureProvider

        provider = get_provider()
        assert isinstance(provider, FixtureProvider)

    def test_unknown_provider_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "nonexistent_provider")

        import importlib
        import config
        importlib.reload(config)

        from providers import get_provider

        with pytest.raises(ValueError, match="nonexistent_provider"):
            get_provider()
