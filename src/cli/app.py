"""
cli/app.py — The rich terminal interface for k8s-copilot.

HOW TO RUN:
  python -m cli.app investigate --namespace prod --pod-name payments-api-7f8b9 --alert "CrashLoopBackOff"
  
  Or after pip install -e . (editable install, see pyproject.toml):
  k8s-copilot investigate --namespace prod --pod-name payments-api-7f8b9 --alert "CrashLoopBackOff"

CONCEPT: TYPER
  Typer is a CLI framework built on top of Python type hints.
  Instead of argparse (verbose, imperative), you define a function
  with typed parameters and Typer auto-generates the CLI from it.

  @app.command()            → this function is a CLI subcommand
  name: str = typer.Option(...)  → becomes --name on the command line
  name: str = typer.Argument(...)→ becomes a positional argument

CONCEPT: RICH
  Rich is a terminal formatting library.
  It understands markup like "[bold green]text[/bold green]" and renders
  colors, tables, panels, spinners, progress bars, etc. in the terminal.

  The Console object is the main entry point — print via console.print()
  not Python's built-in print() to get formatting.

CONCEPT: STREAMING vs. INVOKE
  We use graph.stream() instead of graph.invoke() for the CLI.
  
  invoke() → runs everything, returns final state when done (or when interrupted)
  stream() → yields updates from each node AS THEY HAPPEN
  
  With stream_mode="updates", each yield is a dict like:
    {"call_deploy_investigator": {"deploy_finding": "..."}}
  This lets us show a spinner that updates per-node, giving the user
  live feedback instead of a blank terminal for 30 seconds.
"""

import uuid
import sys
# Add the src directory to sys.path so Python can find graph, nodes, etc.
# This is needed when running as `python -m cli.app` from the src/ directory.
sys.path.insert(0, ".")

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text

from graph import build_graph
from langgraph.types import Command

# ── App and Console setup ────────────────────────────────────────────────────

# typer.Typer() creates the CLI application.
# The name shown in --help is "k8s-copilot".
app = typer.Typer(
    name="k8s-copilot",
    help="[COPILOT] Agentic Kubernetes investigation assistant.",
    no_args_is_help=True,  # shows --help if no arguments given
)

# Console is the Rich output object.
# stderr=False means output goes to stdout (not stderr) —
# important if someone pipes the output to another command.
console = Console()

# Build graph once per CLI session (same as server startup)
graph = build_graph()

# Node names → human-friendly display names (ASCII-safe for Windows terminals)
NODE_DISPLAY = {
    "call_deploy_investigator": "[DEPLOY] Deploy Investigator",
    "call_log_investigator":    "[LOGS]   Log Investigator",
    "synthesize":               "[SYNTH]  Synthesizer",
    "human_review":             "[HUMAN]  Human Review",
    "__interrupt__":            "[PAUSE]  Waiting for Review",
}


# ── Commands ─────────────────────────────────────────────────────────────────

@app.command()
def investigate(
    alert: str = typer.Option(
        ...,  # ... means REQUIRED in typer — no default value
        "--alert", "-a",
        help="The alert text, e.g. 'Pod X is in CrashLoopBackOff'",
    ),
    namespace: str = typer.Option(
        "default",
        "--namespace", "-n",
        help="Kubernetes namespace to investigate",
    ),
    pod_name: str = typer.Option(
        ...,
        "--pod-name", "-p",
        help="The pod name that triggered the alert",
    ),
):
    """
    Start a new Kubernetes investigation.

    The agent will investigate the pod using available data sources,
    synthesize findings, and ask you to approve or override the root cause.
    """
    # ── Header ────────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold cyan]K8s Copilot -- Investigation[/bold cyan]\n"
        f"[dim]Pod:[/dim] [yellow]{pod_name}[/yellow]   "
        f"[dim]Namespace:[/dim] [yellow]{namespace}[/yellow]\n"
        f"[dim]Alert:[/dim] {alert}",
        border_style="cyan",
        padding=(1, 2),
    ))
    # Panel() draws a bordered box in the terminal.
    # Markup like [bold cyan] is Rich's inline formatting syntax.

    # ── Run the graph with streaming ──────────────────────────────────────────
    initial_state = {
        "alert": alert,
        "namespace": namespace,
        "pod_name": pod_name,
        "deploy_finding": "",
        "log_finding": "",
        "iteration_count": 0,
        "confidence": 0.0,
        "root_cause": "",
    }

    thread_id = str(uuid.uuid4())
    run_config = {"configurable": {"thread_id": thread_id}}

    console.print(f"\n[dim]Investigation ID:[/dim] [dim cyan]{thread_id}[/dim cyan]\n")

    # Stream the graph — we get updates node by node.
    # stream_mode="updates" gives us: {node_name: partial_state_update}
    with Live(console=console, refresh_per_second=4) as live:
        # Live context: Rich updates the terminal in place (like a progress bar)
        # refresh_per_second: how often the display updates

        for event in graph.stream(initial_state, config=run_config, stream_mode="updates"):
            for node_name, update in event.items():
                display_name = NODE_DISPLAY.get(node_name, node_name)

                if node_name == "__interrupt__":
                    # Graph paused — stop the live display and go to review
                    live.stop()
                    break
                else:
                    # Show which node just finished
                    live.update(
                        Text(f"  {display_name} [green]✓[/green]", style="bold")
                    )
                    console.print(f"  {display_name} [green]✓[/green]")
            else:
                continue
            break  # Break outer loop when we hit __interrupt__

    # ── Human Review ──────────────────────────────────────────────────────────
    snapshot = graph.get_state(run_config)

    if not snapshot.next:
        # Completed without human review (shouldn't happen normally)
        _print_final(snapshot.values, thread_id)
        return

    # Extract the interrupt payload — what the agent is asking us to review
    pending = snapshot.tasks[0].interrupts[0].value

    console.print()
    _print_review_panel(pending)
    console.print()

    # Rich Prompt for human input — styled, with a default option shown
    decision = Prompt.ask(
        "[bold yellow]  Decision[/bold yellow] [dim](type 'approve' or enter your own root cause)[/dim]",
        default="approve",
        console=console,
    )

    console.print()
    console.print("  [dim]Resuming investigation...[/dim]")

    # Resume the graph with the human's decision
    final_state = graph.invoke(Command(resume=decision), config=run_config)

    console.print()
    _print_final(final_state, thread_id)


# ── Display Helpers ───────────────────────────────────────────────────────────

def _print_review_panel(pending: dict):
    """Print the human review panel with findings and proposed root cause."""
    # Rich Table for the findings
    table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    table.add_column("Agent", style="cyan", no_wrap=True)
    table.add_column("Finding")

    table.add_row("⚙️  Deploy Investigator", pending.get("deploy_finding", ""))
    table.add_row("📋 Log Investigator", pending.get("log_finding", ""))

    console.print(table)
    console.print()

    # Confidence bar — visual representation of 0.0 to 1.0
    confidence = pending.get("confidence", 0.0)
    bar_width = 30
    filled = int(confidence * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    conf_color = "green" if confidence >= 0.75 else "yellow" if confidence >= 0.5 else "red"

    console.print(
        Panel(
            f"[bold white]Proposed Root Cause:[/bold white]\n"
            f"[italic]{pending.get('proposed_root_cause', '')}[/italic]\n\n"
            f"[dim]Confidence:[/dim] [{conf_color}]{bar}[/{conf_color}] [bold]{confidence:.0%}[/bold]  "
            f"[dim]({pending.get('iterations_run', 0)} iteration(s))[/dim]",
            title="[bold yellow]>> Awaiting Human Review[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def _print_final(state: dict, thread_id: str):
    """Print the final investigation result."""
    confidence = state.get("confidence", 0.0)
    conf_color = "green" if confidence >= 0.75 else "yellow" if confidence >= 0.5 else "red"

    console.print(
        Panel(
            f"[bold white]Root Cause:[/bold white]\n"
            f"[italic green]{state.get('root_cause', 'Unknown')}[/italic green]\n\n"
            f"[dim]Confidence:[/dim] [{conf_color}]{confidence:.0%}[/{conf_color}]   "
            f"[dim]Iterations:[/dim] {state.get('iteration_count', 0)}   "
            f"[dim]Thread:[/dim] [dim cyan]{thread_id[:8]}...[/dim cyan]",
            title="[bold green][DONE] Investigation Complete[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )
    console.print()


@app.command()
def resume_investigation(
    thread_id: str = typer.Argument(..., help="Thread ID of a paused investigation"),
):
    """
    Resume a previously paused investigation by its thread ID.
    Use this if you started an investigation in a previous session.
    """
    run_config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(run_config)

    if not snapshot.next:
        console.print(f"[red]No paused investigation found for thread ID: {thread_id}[/red]")
        raise typer.Exit(code=1)

    pending = snapshot.tasks[0].interrupts[0].value

    console.print(Panel(
        f"[bold cyan][RESUME] Resuming Investigation[/bold cyan]\n"
        f"[dim]Thread:[/dim] [dim cyan]{thread_id}[/dim cyan]",
        border_style="cyan",
    ))
    console.print()

    _print_review_panel(pending)
    console.print()

    decision = Prompt.ask(
        "[bold yellow]  Decision[/bold yellow]",
        default="approve",
        console=console,
    )

    final_state = graph.invoke(Command(resume=decision), config=run_config)
    _print_final(final_state, thread_id)


if __name__ == "__main__":
    # When run as `python cli/app.py`, hand off to typer.
    # When run as `python -m cli.app`, same thing.
    app()
