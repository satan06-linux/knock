"""
tests/test_eval_harness.py — Tests for the Ultron evaluation harness (scripts/run_eval.py).

Covers all 5 scenarios using MockProvider + FixtureWorkspace from ultron.eval_suite.
These tests are fully deterministic and never call Ollama or any live provider.
"""
import os
import sys
import json
import unittest
import tempfile
import shutil

# Make sure the package root is importable when run directly
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ultron.eval_suite import (
    MockProvider,
    MockProviderResponse,
    FixtureWorkspace,
    MetricsCollector,
    TaskMetrics,
)
from ultron.task import TaskStatus

# ---------------------------------------------------------------------------
# Shared helpers (mirror what run_eval.py provides)
# ---------------------------------------------------------------------------

def _make_tool_call(name: str, arguments: dict) -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class _EvalAgent:
    """Same lightweight wrapper as in run_eval.py, reproduced here to keep the
    test file self-contained and not import from scripts/."""

    def __init__(
        self,
        workspace_root: str,
        mock_provider: MockProvider,
        intent_mode: str = "build",
        max_tool_calls: int = 12,
    ):
        from ultron.agent import UltronAgent

        self.agent = UltronAgent(
            workspace_root=workspace_root,
            auto_approve=True,
            auto_commit=False,
        )
        self.agent.model = mock_provider
        self.agent.intent_mode = intent_mode
        self._max_tool_calls = max_tool_calls

    def run(self, prompt: str) -> dict:
        import time

        self.agent.max_iterations = self._max_tool_calls
        start = time.time()
        try:
            self.agent.run(prompt)
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "status": "error", "elapsed": time.time() - start}

        task = self.agent.current_task
        status = task.status.value if task else "unknown"
        return {
            "status": status,
            "elapsed": time.time() - start,
            "files_modified": list(self.agent.checkpoint.current_task_files.keys()),
        }


# ---------------------------------------------------------------------------
# Scenario 1: Write a file
# ---------------------------------------------------------------------------

class TestScenario1WriteFile(unittest.TestCase):
    """Scenario 1 — MockProvider returns write_file tool call; app/new.py must exist."""

    def test_file_created(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "new.py"),
                                    "content": "def hello(): pass\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(content="Done."),
                ]
            )
            agent = _EvalAgent(ws, mock)
            result = agent.run("Create app/new.py with a hello function.")

            target = os.path.join(ws, "app", "new.py")
            self.assertTrue(
                os.path.isfile(target),
                f"Expected app/new.py to exist in {ws}, but it does not.",
            )

    def test_file_content_correct(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "new.py"),
                                    "content": "def hello(): pass\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(content="Done."),
                ]
            )
            agent = _EvalAgent(ws, mock)
            agent.run("Create app/new.py with a hello function.")

            target = os.path.join(ws, "app", "new.py")
            if os.path.isfile(target):
                with open(target, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("hello", content)


# ---------------------------------------------------------------------------
# Scenario 2: Read then explain
# ---------------------------------------------------------------------------

class TestScenario2ReadExplain(unittest.TestCase):
    """Scenario 2 — Agent returns a text response, no tool calls, no files written."""

    def test_task_completes_without_error(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        content=(
                            "UserService handles user operations "
                            "including get_user and create_user."
                        )
                    ),
                ]
            )
            agent = _EvalAgent(ws, mock)
            result = agent.run("Explain what UserService does.")
            self.assertNotEqual(result.get("status"), "error")

    def test_no_files_modified(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        content=(
                            "UserService handles user operations "
                            "including get_user and create_user."
                        )
                    ),
                ]
            )
            agent = _EvalAgent(ws, mock)
            result = agent.run("Explain what UserService does.")
            self.assertEqual(
                result.get("files_modified", []),
                [],
                "No files should be modified for a read/explain task.",
            )


# ---------------------------------------------------------------------------
# Scenario 3: Respect ask mode (no writes)
# ---------------------------------------------------------------------------

class TestScenario3AskModeNoWrites(unittest.TestCase):
    """Scenario 3 — ask mode must block write_file tool calls."""

    def test_write_blocked_in_ask_mode(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "blocked.py"),
                                    "content": "# blocked\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(content="I cannot write files in ask mode."),
                ]
            )
            agent = _EvalAgent(ws, mock, intent_mode="ask")
            agent.run("Write a new file app/blocked.py.")

            target = os.path.join(ws, "app", "blocked.py")
            self.assertFalse(
                os.path.isfile(target),
                "write_file must be blocked in ask mode; the file must not exist.",
            )

    def test_no_files_written_in_ask_mode(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {"path": os.path.join("app", "x.py"), "content": "x = 1\n"},
                            )
                        ]
                    ),
                    MockProviderResponse(content="Cannot write."),
                ]
            )
            agent = _EvalAgent(ws, mock, intent_mode="ask")
            result = agent.run("Write x.py.")
            self.assertEqual(
                result.get("files_modified", []),
                [],
                "No files should be written in ask mode.",
            )


# ---------------------------------------------------------------------------
# Scenario 4: Detect and stop over-budget task
# ---------------------------------------------------------------------------

class TestScenario4OverBudget(unittest.TestCase):
    """Scenario 4 — with max_tool_calls=3 and 5 tool calls queued, task must be BLOCKED."""

    def _make_repeated_calls(self, count: int, ws: str) -> MockProvider:
        repeated = _make_tool_call(
            "view_file",
            {"path": os.path.join("app", "service.py")},
        )
        return MockProvider(
            responses=[MockProviderResponse(tool_calls=[repeated]) for _ in range(count)]
        )

    def test_task_stops_at_budget(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = self._make_repeated_calls(5, ws)
            agent = _EvalAgent(ws, mock, max_tool_calls=3)
            result = agent.run("Keep reading app/service.py repeatedly.")

            self.assertEqual(
                result.get("status"),
                TaskStatus.BLOCKED.value,
                f"Expected status='blocked', got {result.get('status')!r}",
            )

    def test_task_does_not_exceed_budget(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = self._make_repeated_calls(10, ws)
            agent = _EvalAgent(ws, mock, max_tool_calls=3)
            # Should stop at 3, never consuming all 10 responses
            agent.run("Keep reading app/service.py repeatedly.")
            # Mock call count should be <= max_tool_calls (3) plus any final
            # termination response
            self.assertLessEqual(
                mock.call_count,
                4,  # at most 3 tool iterations + 1 possible extra call
                "Agent must stop calling the provider after budget is exhausted.",
            )


# ---------------------------------------------------------------------------
# Scenario 5: Multi-file task requires plan
# ---------------------------------------------------------------------------

class TestScenario5MultiFileRequiresPlan(unittest.TestCase):
    """Scenario 5 — second write_file without plan must be blocked."""

    def test_first_write_succeeds(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "file_a.py"),
                                    "content": "# file a\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "file_b.py"),
                                    "content": "# file b\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(content="Done."),
                ]
            )
            agent = _EvalAgent(ws, mock)
            agent.agent.last_plan_task = None
            agent.run("Write two files: app/file_a.py and app/file_b.py.")

            file_a = os.path.join(ws, "app", "file_a.py")
            self.assertTrue(
                os.path.isfile(file_a),
                "The first file (file_a.py) should be written successfully.",
            )

    def test_second_write_blocked_without_plan(self):
        with FixtureWorkspace("python_basic") as ws:
            mock = MockProvider(
                responses=[
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "file_a.py"),
                                    "content": "# file a\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(
                        tool_calls=[
                            _make_tool_call(
                                "write_file",
                                {
                                    "path": os.path.join("app", "file_b.py"),
                                    "content": "# file b\n",
                                },
                            )
                        ]
                    ),
                    MockProviderResponse(content="Done."),
                ]
            )
            agent = _EvalAgent(ws, mock)
            agent.agent.last_plan_task = None
            agent.run("Write two files: app/file_a.py and app/file_b.py.")

            file_b = os.path.join(ws, "app", "file_b.py")
            self.assertFalse(
                os.path.isfile(file_b),
                "The second file (file_b.py) must be blocked without a plan.",
            )


# ---------------------------------------------------------------------------
# Bonus: MetricsCollector smoke test
# ---------------------------------------------------------------------------

class TestMetricsCollectorSmoke(unittest.TestCase):
    """Quick smoke-test for MetricsCollector to ensure it doesn't break eval runs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_summary(self):
        mc = MetricsCollector(self.tmpdir)
        m = TaskMetrics(
            task_id="test-001",
            prompt="add a feature",
            intent="feature",
            success=True,
            files_changed=["app/new.py"],
            commands_run=[],
            tool_call_count=2,
            duration_seconds=1.5,
            had_unverified=False,
        )
        mc.record(m)
        summary = mc.compute_summary()
        self.assertEqual(summary["total_tasks"], 2)
        self.assertEqual(summary["task_completion_rate"], 1.0)
    def test_empty_summary(self):
        mc = MetricsCollector(self.tmpdir)
        self.assertEqual(mc.compute_summary(), {})


if __name__ == "__main__":
    unittest.main()
