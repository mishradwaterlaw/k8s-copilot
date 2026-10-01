"""
tests/test_graph_routing.py — Unit tests for the supervisor graph's conditional routing logic.

WHAT WE'RE TESTING:
  - `route_after_synthesize` conditional routing (confidence threshold & max iterations)
  - `route_after_human_review` conditional routing:
    1. 'approve' -> routes to END
    2. 'override' -> routes to END
    3. 'reinvestigate' (operator feedback) -> loops back to parallel sub-agents
  - `human_review` node decision behavior:
    1. Decision "approve" -> sets feedback_action="approve"
    2. Decision "override: <text>" -> updates root_cause and sets feedback_action="override"
    3. Decision "<guidance text>" -> sets human_feedback and feedback_action="reinvestigate"
"""

import pytest
from langgraph.graph import END
from graph import route_after_synthesize, route_after_human_review
from nodes import human_review
import config


class TestGraphRouting:

    def test_routes_to_human_review_when_confidence_high(self, high_confidence_state):
        assert high_confidence_state["confidence"] >= config.CONFIDENCE_THRESHOLD
        decision = route_after_synthesize(high_confidence_state)
        assert decision == "human_review"

    def test_routes_to_parallel_subagents_when_confidence_low_and_iterations_remain(
        self, low_confidence_state
    ):
        assert low_confidence_state["confidence"] < config.CONFIDENCE_THRESHOLD
        assert low_confidence_state["iteration_count"] < config.MAX_ITERATIONS

        decision = route_after_synthesize(low_confidence_state)
        assert isinstance(decision, list)
        assert decision == ["call_deploy_investigator", "call_log_investigator"]

    def test_routes_to_human_review_when_max_iterations_reached_even_if_confidence_low(
        self, maxed_iterations_state
    ):
        assert maxed_iterations_state["confidence"] < config.CONFIDENCE_THRESHOLD
        assert maxed_iterations_state["iteration_count"] >= config.MAX_ITERATIONS

        decision = route_after_synthesize(maxed_iterations_state)
        assert decision == "human_review"

    def test_routes_to_end_on_human_approval(self, sample_state):
        state = sample_state | {"feedback_action": "approve"}
        assert route_after_human_review(state) == END

    def test_routes_to_end_on_manual_override(self, sample_state):
        state = sample_state | {"feedback_action": "override"}
        assert route_after_human_review(state) == END

    def test_routes_to_subagents_on_human_feedback(self, sample_state):
        state = sample_state | {
            "feedback_action": "reinvestigate",
            "human_feedback": "check database host connection",
        }
        decision = route_after_human_review(state)
        assert isinstance(decision, list)
        assert decision == ["call_deploy_investigator", "call_log_investigator"]


class TestHumanReviewNode:

    def test_human_review_approval(self, mocker, high_confidence_state):
        mocker.patch("nodes.interrupt", return_value="approve")
        update = human_review(high_confidence_state)
        assert update == {"feedback_action": "approve"}

    def test_human_review_manual_override(self, mocker, sample_state):
        override_text = "override: Manual fix: Node OOM killed the pod due to memory leak in v2.4.1"
        mocker.patch("nodes.interrupt", return_value=override_text)
        update = human_review(sample_state)
        assert update["feedback_action"] == "override"
        assert "Node OOM killed" in update["root_cause"]

    def test_human_review_steering_feedback(self, mocker, sample_state):
        feedback_text = "check again, looks like db-primary-v2 was rotated"
        mocker.patch("nodes.interrupt", return_value=feedback_text)
        update = human_review(sample_state)
        assert update["feedback_action"] == "reinvestigate"
        assert update["human_feedback"] == feedback_text
        assert update["iteration_count"] == 0


class TestStateReducers:

    def test_investigation_state_findings_reducer_merges_parallel_outputs(self):
        """
        Verify that LangGraph uses operator.add to merge concurrent updates
        to 'findings' from the parallel investigator nodes without an InvalidUpdateError.
        """
        import operator
        from state import InvestigationState
        from langgraph.graph import StateGraph, START, END

        # Build a minimal 2-node parallel fan-out test graph
        builder = StateGraph(InvestigationState)

        def mock_deploy_investigator(state: InvestigationState) -> dict:
            return {"findings": ["[DEPLOY] Deploy v2.4 rolled out 5m ago."]}

        def mock_log_investigator(state: InvestigationState) -> dict:
            return {"findings": ["[LOGS] CrashLoop: Connection refused on port 5432."]}

        builder.add_node("deploy", mock_deploy_investigator)
        builder.add_node("log", mock_log_investigator)

        builder.add_edge(START, "deploy")
        builder.add_edge(START, "log")
        builder.add_edge("deploy", END)
        builder.add_edge("log", END)

        compiled_graph = builder.compile()

        initial_state = {
            "alert": "Pod test in namespace default is CrashLoopBackOff",
            "namespace": "default",
            "pod_name": "test-pod",
            "deploy_finding": "",
            "log_finding": "",
            "findings": [],
            "iteration_count": 0,
            "confidence": 0.0,
            "root_cause": "",
        }

        final_state = compiled_graph.invoke(initial_state)

        # Both parallel updates must be merged into state["findings"]
        assert len(final_state["findings"]) == 2
        assert "[DEPLOY] Deploy v2.4 rolled out 5m ago." in final_state["findings"]
        assert "[LOGS] CrashLoop: Connection refused on port 5432." in final_state["findings"]
