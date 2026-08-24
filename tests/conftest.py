"""
tests/conftest.py — Shared pytest fixtures used across all test files.

CONCEPT: PYTEST FIXTURES
══════════════════════════
A fixture is a function decorated with @pytest.fixture that provides
setup/teardown logic and return values to test functions.

Instead of copy-pasting setup code in every test:
    def test_something():
        state = {"alert": "...", "namespace": "default", ...}
        # ... actual test

You define it once as a fixture and inject it by parameter name:
    def test_something(sample_state):
        # sample_state is automatically provided by pytest
        # ...

Fixtures can:
  - Be "shared" across tests in a module, or across the whole session
  - Have scope: "function" (new per test), "module", "session"
  - Be parameterized (run each test with multiple values)
  - Be composed (fixtures can use other fixtures)

CONCEPT: CONFTEST.PY
═════════════════════
pytest automatically discovers and loads conftest.py files.
Any fixture defined here is available to ALL test files in the same
directory and subdirectories — no imports needed.
"""

import sys
import os
import pytest

# ── Make src/ importable ─────────────────────────────────────────────────────
# Tests run from the project root. The source code is in src/.
# We add src/ to sys.path so `import config`, `import graph`, etc. work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Set required env vars BEFORE importing modules that read them at import time.
# config.py reads os.getenv at module level, so env vars must exist before import.
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("DATA_PROVIDER", "fixture")


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def sample_state():
    """
    A minimal, valid InvestigationState dict for testing supervisor nodes.
    All required fields populated with safe defaults.
    """
    return {
        "alert": "Pod payments-api-7f8b9 in namespace prod is CrashLoopBackOff",
        "namespace": "prod",
        "pod_name": "payments-api-7f8b9",
        "deploy_finding": "",
        "log_finding": "",
        "iteration_count": 0,
        "confidence": 0.0,
        "root_cause": "",
    }


@pytest.fixture
def high_confidence_state(sample_state):
    """
    A state where confidence is already above threshold.
    Used to test that the router sends to human_review correctly.

    Fixtures CAN use other fixtures — just add them as parameters.
    pytest injects them automatically.
    """
    # dict | dict merges two dicts (Python 3.9+)
    return sample_state | {
        "confidence": 0.90,
        "root_cause": "Bad deploy introduced wrong DB hostname",
        "deploy_finding": "v2.4.1 changed database.yaml",
        "log_finding": "DNS resolution failed for db-primary-v2",
        "iteration_count": 1,
    }


@pytest.fixture
def low_confidence_state(sample_state):
    """A state with confidence below threshold — should loop again."""
    return sample_state | {
        "confidence": 0.45,
        "root_cause": "Possibly a config issue",
        "deploy_finding": "No suspicious deploys found",
        "log_finding": "Pod exited with code 1",
        "iteration_count": 1,
    }


@pytest.fixture
def maxed_iterations_state(low_confidence_state):
    """
    Low confidence but MAX iterations reached — should go to human_review
    even though confidence is low (prevent infinite loops).
    """
    return low_confidence_state | {"iteration_count": 3}


@pytest.fixture
def fixture_provider():
    """Returns a FixtureProvider instance for tool tests."""
    from providers.fixture import FixtureProvider
    return FixtureProvider()


@pytest.fixture
def fake_llm_response():
    """
    Factory fixture: call it with content to get a fake LLM response object.

    Usage in tests:
        def test_something(fake_llm_response):
            mock_response = fake_llm_response("CONFIDENCE: 0.9\\nROOT_CAUSE: Bad deploy")
    """
    from langchain_core.messages import AIMessage

    def _make(content: str) -> AIMessage:
        # AIMessage is what llm.invoke() returns.
        # We return a real AIMessage (not a Mock) so all .content, .tool_calls
        # attributes work correctly without additional mock setup.
        return AIMessage(content=content)

    return _make
