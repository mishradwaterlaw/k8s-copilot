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
