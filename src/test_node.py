"""
test_node.py — Sanity-check a single node in isolation, no graph required.

This is possible ONLY because nodes are plain functions: state in, dict out.
No LangGraph machinery needed to call one directly. This is one of the
underrated benefits of the "node = function" design — you can unit test
your investigation logic without ever touching StateGraph.

Run from inside src/:  python test_node.py
"""

from nodes import gather_evidence

# Build a fake starting state by hand, matching InvestigationState's shape.
state = {
    "alert": "Pod payments-api-7f8b9 in namespace prod is CrashLoopBackOff",
    "evidence": [],
    "iteration_count": 0,
    "confidence": 0.0,
    "root_cause": "",
}

print("--- Calling gather_evidence() once ---")
update = gather_evidence(state)
print(update)

# Simulate what LangGraph would do: merge the update into state.
# (Manually here, since we're not running the graph yet.)
state["evidence"] += update["evidence"]
state["iteration_count"] = update["iteration_count"]

print("\n--- State after merge ---")
print(state)

print("\n--- Calling gather_evidence() a second time (should query a DIFFERENT source) ---")
update2 = gather_evidence(state)
print(update2)