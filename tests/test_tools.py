"""
tests/test_tools.py — Unit tests for the tools factory and tool behavior.

WHAT WE'RE TESTING:
  - make_tools() creates 6 tools with the right names
  - Each tool is callable and returns a string
  - Tool docstrings are non-empty
  - get_app_logs accepts container_name argument
  - get_node_conditions and get_resource_limits work properly
"""

import pytest


class TestMakeTools:

    @pytest.fixture
    def tools(self, fixture_provider):
        from tools import make_tools
        return make_tools(
            provider=fixture_provider,
            namespace="prod",
            pod_name="payments-api-7f8b9",
        )

    def test_creates_six_tools(self, tools):
        """make_tools() must return exactly 6 tools."""
        assert len(tools) == 6

    def test_tool_names_are_correct(self, tools):
        names = {t.name for t in tools}
        assert names == {
            "get_pod_events",
            "get_app_logs",
            "get_pod_status",
            "get_related_pod_status",
            "get_node_conditions",
            "get_resource_limits",
        }

    def test_all_tools_have_descriptions(self, tools):
        for tool in tools:
            assert tool.description, f"Tool '{tool.name}' has no description"
            assert len(tool.description) > 20

    def test_get_pod_events_returns_string(self, tools):
        tool = next(t for t in tools if t.name == "get_pod_events")
        result = tool.invoke({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_app_logs_default_and_container(self, tools):
        tool = next(t for t in tools if t.name == "get_app_logs")
        result_default = tool.invoke({})
        assert isinstance(result_default, str)
        assert len(result_default) > 0

        # Specific container query (e.g. sidecar or init container)
        result_sidecar = tool.invoke({"container_name": "envoy-sidecar"})
        assert "Envoy" in result_sidecar or "envoy" in result_sidecar.lower()

    def test_get_pod_status_returns_init_and_app_containers(self, tools):
        tool = next(t for t in tools if t.name == "get_pod_status")
        result = tool.invoke({})
        assert isinstance(result, str)
        assert "payments-api" in result
        assert "Init Containers" in result
        assert "db-migration" in result

    def test_get_node_conditions_returns_string(self, tools):
        tool = next(t for t in tools if t.name == "get_node_conditions")
        result = tool.invoke({"node_name": "node-3"})
        assert isinstance(result, str)
        assert "node-3" in result
        assert "MemoryPressure" in result

    def test_get_resource_limits_returns_string(self, tools):
        tool = next(t for t in tools if t.name == "get_resource_limits")
        result = tool.invoke({})
        assert isinstance(result, str)
        assert "Requests" in result
        assert "Limits" in result

    def test_get_related_pod_status_valid_pod(self, tools):
        tool = next(t for t in tools if t.name == "get_related_pod_status")
        from fixtures.cluster_data import OTHER_PODS
        valid_pod_id = list(OTHER_PODS.keys())[0]
        result = tool.invoke({"pod_id": valid_pod_id})
        assert isinstance(result, str)
        assert valid_pod_id in result

    def test_get_related_pod_status_invalid_pod(self, tools):
        tool = next(t for t in tools if t.name == "get_related_pod_status")
        result = tool.invoke({"pod_id": "completely-made-up-pod-xyz"})
        assert isinstance(result, str)
        assert "Unknown pod_id" in result or "unknown" in result.lower()


class TestMakeDeployTools:

    def test_creates_one_tool(self, fixture_provider):
        from tools import make_deploy_tools
        tools = make_deploy_tools(fixture_provider, namespace="prod")
        assert len(tools) == 1

    def test_tool_name_is_get_recent_deployments(self, fixture_provider):
        from tools import make_deploy_tools
        tools = make_deploy_tools(fixture_provider, namespace="prod")
        assert tools[0].name == "get_recent_deployments"

    def test_tool_returns_deployment_data(self, fixture_provider):
        from tools import make_deploy_tools
        tools = make_deploy_tools(fixture_provider, namespace="prod")
        result = tools[0].invoke({})
        assert isinstance(result, str)
        assert len(result) > 0
