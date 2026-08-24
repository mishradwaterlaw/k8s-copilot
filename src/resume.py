"""
resume.py — Resume a paused investigation from a PREVIOUS, now-dead
process. This is the real proof that persistence matters: this script
has never seen the original graph.invoke() call. It only knows about
checkpoints.db.

Run: python resume.py
(after you've run graph.py, hit the interrupt, and killed the process
with Ctrl+C instead of answering the prompt)
"""

from graph import build_graph
from langgraph.types import Command

graph = build_graph()  # rebuilds the graph structure fresh — this is fine,
                        # graph STRUCTURE isn't what needed to survive, only
                        # the STATE does, and that lives in checkpoints.db

config = {"configurable": {"thread_id": "investigation-1"}}

# get_state() reads the last saved checkpoint for this thread_id back from
# checkpoints.db. If the thread was paused on an interrupt(), it shows up
# here in .tasks — this is how you discover "oh, there's unfinished work
# waiting" without having kept anything in memory yourself.
snapshot = graph.get_state(config)

if not snapshot.next:
    print("No pending work for this thread — nothing to resume.")
else:
    # Find the interrupt payload inside the snapshot's pending tasks.
    pending_interrupt = None
    for task in snapshot.tasks:
        if task.interrupts:
            pending_interrupt = task.interrupts[0].value
            break

    if pending_interrupt:
        print("=== FOUND A PAUSED INVESTIGATION (from a previous process) ===")
        print(f"Proposed root cause: {pending_interrupt['proposed_root_cause']}")
        print(f"Confidence: {pending_interrupt['confidence']}")

        human_input = input("\nType 'approve' or enter an override root cause: ")

        final_state = graph.invoke(Command(resume=human_input), config=config)

        print("\n=== FINAL STATE (resumed after a full restart) ===")
        print(f"Root cause: {final_state['root_cause']}")
    else:
        print("Thread has pending work but no interrupt found — unexpected state.")