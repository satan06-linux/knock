"""
run_eval.py - Ultron Evaluation Harness
Runs 5 scripted scenarios against fixture workspaces using MockProvider.
Reports pass/fail with Rich table + JSON output.

Usage:
    python scripts/run_eval.py
    python scripts/run_eval.py --scenario 1
    python scripts/run_eval.py --output eval_results.json
"""
import os
import sys
import json
import time
import argparse
import tempfile

# Ensure ultron package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ultron.eval_suite import MockProvider, MockProviderResponse, FixtureWorkspace, TaskMetrics
from ultron.task import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call(name: str, arguments: dict) -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class EvalAgent:
    """Thin wrapper that drives UltronAgent with a MockProvider."""

    def __init__(self, workspace_root: str, mock_provider, intent_mode: str = "build", max_tool_calls: int = 12):
        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=workspace_root, auto_approve=True, auto_commit=False)
        self.agent.model = mock_provider
        self.agent.intent_mode = intent_mode
        self.agent.max_iterations = max_tool_calls

    def run(self, prompt: str) -> dict:
        start = time.time()
        try:
            self.agent.run(prompt)
        except SystemExit:
            pass
        except Exception as e:
            return {"status": "error", "error": str(e), "elapsed": time.time() - start}

        task = self.agent.current_task
        return {
            "status": task.status.value if task else "unknown",
            "elapsed": round(time.time() - start, 2),
            "files_modified": list(self.agent.checkpoint.current_task_files.keys()),
        }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_1_write_file() -> dict:
    """MockProvider returns write_file → app/new.py must exist."""
    with FixtureWorkspace("python_basic") as ws:
        mock = MockProvider([
            MockProviderResponse(tool_calls=[_make_tool_call(
                "write_file", {"path": os.path.join("app", "new.py"), "content": "def hello(): pass\n"}
            )]),
            MockProviderResponse(content="Done. File written."),
        ])
        agent = EvalAgent(ws, mock)
        result = agent.run("Create app/new.py with a hello function.")

        created = os.path.isfile(os.path.join(ws, "app", "new.py"))
        return {
            "scenario": 1,
            "name": "Write a file",
            "passed": created,
            "detail": "app/new.py created" if created else "app/new.py NOT found",
            "elapsed": result["elapsed"],
        }


def scenario_2_read_explain() -> dict:
    """MockProvider returns explanation — no files should be modified."""
    with FixtureWorkspace("python_basic") as ws:
        mock = MockProvider([
            MockProviderResponse(content="UserService handles user operations including get_user and create_user."),
        ])
        agent = EvalAgent(ws, mock)
        result = agent.run("Explain what UserService does.")

        no_files_modified = len(result.get("files_modified", [])) == 0
        no_error = result.get("status") != "error"
        passed = no_files_modified and no_error
        return {
            "scenario": 2,
            "name": "Read then explain (no mutations)",
            "passed": passed,
            "detail": "No files modified" if passed else f"Unexpected files: {result.get('files_modified')}",
            "elapsed": result["elapsed"],
        }


def scenario_3_ask_mode_no_writes() -> dict:
    """In ask mode, write_file tool call must be blocked."""
    with FixtureWorkspace("python_basic") as ws:
        mock = MockProvider([
            MockProviderResponse(tool_calls=[_make_tool_call(
                "write_file", {"path": "blocked.py", "content": "x = 1\n"}
            )]),
            MockProviderResponse(content="I cannot write files in ask mode."),
        ])
        agent = EvalAgent(ws, mock, intent_mode="ask")
        agent.run("Write a new file called blocked.py.")

        blocked_file = os.path.join(ws, "blocked.py")
        write_blocked = not os.path.isfile(blocked_file)
        return {
            "scenario": 3,
            "name": "Respect ask mode (no writes)",
            "passed": write_blocked,
            "detail": "Write correctly blocked" if write_blocked else "blocked.py was written (FAIL)",
            "elapsed": 0.0,
        }


def scenario_4_budget_enforcement() -> dict:
    """Task with max_tool_calls=3 and 5 tool calls queued → must stop at BLOCKED."""
    with FixtureWorkspace("python_basic") as ws:
        repeated = _make_tool_call("view_file", {"path": os.path.join("app", "service.py")})
        mock = MockProvider([MockProviderResponse(tool_calls=[repeated]) for _ in range(5)])
        agent = EvalAgent(ws, mock, max_tool_calls=3)
        result = agent.run("Keep reading app/service.py.")

        status_blocked = result.get("status") == TaskStatus.BLOCKED.value
        calls_ok = mock.call_count <= 4
        passed = status_blocked and calls_ok
        return {
            "scenario": 4,
            "name": "Over-budget task stops at BLOCKED",
            "passed": passed,
            "detail": (
                f"status={result.get('status')}, provider_calls={mock.call_count}"
                + ("" if passed else " (FAIL)")
            ),
            "elapsed": result["elapsed"],
        }


def scenario_5_multifile_requires_plan() -> dict:
    """Without an active plan, second file write must be blocked."""
    with FixtureWorkspace("python_basic") as ws:
        mock = MockProvider([
            # First write — allowed (single file)
            MockProviderResponse(tool_calls=[_make_tool_call(
                "write_file", {"path": os.path.join("app", "file1.py"), "content": "x = 1\n"}
            )]),
            # Second write — should be blocked (no plan)
            MockProviderResponse(tool_calls=[_make_tool_call(
                "write_file", {"path": os.path.join("app", "file2.py"), "content": "y = 2\n"}
            )]),
            MockProviderResponse(content="Done."),
        ])
        agent = EvalAgent(ws, mock)
        # Ensure no plan is active
        agent.agent.last_plan_task = None
        agent.run("Write file1.py and file2.py without a plan.")

        file1 = os.path.isfile(os.path.join(ws, "app", "file1.py"))
        file2 = os.path.isfile(os.path.join(ws, "app", "file2.py"))
        # file1 allowed, file2 should be blocked
        passed = file1 and not file2
        return {
            "scenario": 5,
            "name": "Multi-file requires plan (second write blocked)",
            "passed": passed,
            "detail": (
                f"file1={'✓' if file1 else '✗'} file2={'✓' if file2 else '✗ (blocked)' }"
                + ("" if passed else " (FAIL)")
            ),
            "elapsed": 0.0,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_SCENARIOS = {
    1: scenario_1_write_file,
    2: scenario_2_read_explain,
    3: scenario_3_ask_mode_no_writes,
    4: scenario_4_budget_enforcement,
    5: scenario_5_multifile_requires_plan,
}


def run_all(selected: int = None) -> list:
    scenarios = ALL_SCENARIOS if selected is None else {selected: ALL_SCENARIOS[selected]}
    results = []
    for num, fn in scenarios.items():
        try:
            result = fn()
        except Exception as e:
            result = {"scenario": num, "name": str(fn.__name__), "passed": False,
                      "detail": f"Exception: {e}", "elapsed": 0.0}
        results.append(result)
    return results


def display_results(results: list, console: Console):
    table = Table(title="Ultron Eval Harness", show_header=True, header_style="bold white")
    table.add_column("#", width=3, style="cyan")
    table.add_column("Scenario", style="bold white")
    table.add_column("Result", width=8)
    table.add_column("Detail")
    table.add_column("Time", width=7)

    passed = 0
    for r in results:
        ok = r["passed"]
        passed += int(ok)
        icon = "[bold green]PASS[/bold green]" if ok else "[bold red]FAIL[/bold red]"
        table.add_row(
            str(r["scenario"]),
            r["name"],
            icon,
            r["detail"],
            f"{r['elapsed']:.2f}s",
        )

    console.print(table)
    total = len(results)
    color = "green" if passed == total else "red"
    console.print(f"\n[{color}]{passed}/{total} scenarios passed.[/{color}]\n")
    return passed == total


def main():
    parser = argparse.ArgumentParser(description="Ultron Evaluation Harness")
    parser.add_argument("--scenario", type=int, default=None, help="Run a single scenario (1-5)")
    parser.add_argument("--output", type=str, default=None, help="Write JSON results to file")
    args = parser.parse_args()

    console = Console()
    console.print(Panel("[bold magenta]Ultron Eval Harness[/bold magenta]", expand=False))

    results = run_all(args.scenario)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        console.print(f"[dim]Results saved to {args.output}[/dim]")

    all_passed = display_results(results, console)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
