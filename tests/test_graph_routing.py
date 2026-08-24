"""
tests/test_graph_routing.py — Unit tests for the supervisor graph's conditional routing logic.

WHAT WE'RE TESTING:
  - `route_after_synthesize` conditional routing:
    1. High confidence (>= CONFIDENCE_THRESHOLD) -> routes to "human_review"
    2. Low confidence (< CONFIDENCE_THRESHOLD) & iterations remaining -> routes to fan-out ["call_deploy_investigator", "call_log_investigator"]
    3. Low confidence (< CONFIDENCE_THRESHOLD) but max iterations reached (>= MAX_ITERATIONS) -> routes to "human_review" to prevent infinite loops
  - `human_review` node decision behavior:
    1. Decision "approve" -> leaves root_cause unchanged (returns {})
    2. Decision "override text" -> updates root_cause to the overridden text

WHY TEST GRAPH ROUTING IN ISOLATION?
  Graph routing is the brain of your multi-agent architecture. By testing `route_after_synthesize`
  and node transitions directly as pure Python functions, we verify state machine logic in
  milliseconds without making expensive or non-deterministic LLM API calls.
"""

import pytest
from graph import route_after_synthesize
from nodes import human_review
import config


class TestGraphRouting:
    """Tests for conditional branch routing after synthesis."""

    def test_routes_to_human_review_when_confidence_high(self, high_confidence_state):
        """
        When confidence >= 0.75 (default threshold), we have strong evidence
        and should immediately transition to human review.
        """
        assert high_confidence_state["confidence"] >= config.CONFIDENCE_THRESHOLD
        decision = route_after_synthesize(high_confidence_state)
        assert decision == "human_review"

    def test_routes_to_parallel_subagents_when_confidence_low_and_iterations_remain(
        self, low_confidence_state
    ):
        """
        When confidence < 0.75 and iteration count < 3, the supervisor should
        fan out to BOTH investigators again for another round of evidence gathering.
        Notice that LangGraph expects a list of node names for parallel fan-out!
        """
        assert low_confidence_state["confidence"] < config.CONFIDENCE_THRESHOLD
        assert low_confidence_state["iteration_count"] < config.MAX_ITERATIONS

        decision = route_after_synthesize(low_confidence_state)
        assert isinstance(decision, list)
        assert decision == ["call_deploy_investigator", "call_log_investigator"]

    def test_routes_to_human_review_when_max_iterations_reached_even_if_confidence_low(
        self, maxed_iterations_state
    ):
        """
        Safety guard: Even if confidence is still low (e.g. 0.45), once MAX_ITERATIONS
        is reached, we MUST break the loop and route to human_review.
        Otherwise, an unsolvable alert would cause infinite LLM looping and burn API budget.
        """
        assert maxed_iterations_state["confidence"] < config.CONFIDENCE_THRESHOLD
        assert maxed_iterations_state["iteration_count"] >= config.MAX_ITERATIONS

        decision = route_after_synthesize(maxed_iterations_state)
        assert decision == "human_review"


class TestHumanReviewNode:
    """Tests for human_review node update mechanics."""

    def test_human_review_approval_preserves_root_cause(self, mocker, high_confidence_state):
        """
        When human reviewer replies 'approve', the node should return an empty dict {}
        indicating no state overrides are applied.

        CONCEPT: MOCKING INTERRUPT
        ══════════════════════════
        `interrupt()` is a LangGraph runtime primitive. When testing nodes outside
        a compiled graph runner, we mock `interrupt()` using pytest-mock to simulate
        the human's response string.
        """
        # Patch interrupt in nodes.py to return "approve"
        mocker.patch("nodes.interrupt", return_value="approve")

        update = human_review(high_confidence_state)
        assert update == {}

    def test_human_review_override_updates_root_cause(self, mocker, sample_state):
        """
        When the human provides an override explanation, the node should return
        a partial update dict with the new 'root_cause'.
        """
        override_text = "Manual fix: Node OOM killed the pod due to memory leak in v2.4.1"
        mocker.patch("nodes.interrupt", return_value=override_text)

        update = human_review(sample_state)
        assert update == {"root_cause": override_text}
