"""
test_p1_p2.py - P1 (Reliability) + P2 (Intelligence) tests.
P1: EventBus, HealthMonitor, RepairEngine, retry budget.
P2: ModelProfile, ModelRouter, ModelHealthTracker, ProjectProfile, ContextAssembler.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ultron.event_bus import EventBus, BusEvent, get_bus
from ultron.health_monitor import HealthMonitor, HealthCheck
from ultron.repair_engine import RepairEngine, MAX_REPAIR_ATTEMPTS
from ultron.task import Task, TaskStatus, TaskIntent
from ultron.model_profile import (
    ModelProfile, ModelProfileStore, CapabilityEntry, CapabilityStatus,
    CostClass, LatencyClass, BUILTIN_PROFILES
)
from ultron.model_router import (
    ModelRouter, ModelHealthTracker, TaskRequirements, get_health_tracker
)
from ultron.project_profile import ProjectProfile
from ultron.context_assembler import ContextAssembler


def write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class Base(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)


# ===========================================================================
# P1.4 — EventBus
# ===========================================================================

class TestEventBus(unittest.TestCase):

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda d: received.append(d))
        bus.publish("test.event", {"value": 42})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["value"], 42)

    def test_wildcard_subscription(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda d: received.append(d))
        bus.publish("task.started", {"id": "t1"})
        bus.publish("tool.executed", {"tool": "write_file"})
        self.assertEqual(len(received), 2)

    def test_no_subscribers_no_crash(self):
        bus = EventBus()
        bus.publish("nonexistent.event", {"x": 1})  # must not raise

    def test_handler_exception_not_propagated(self):
        bus = EventBus()
        bus.subscribe("test", lambda d: 1/0)  # always raises
        bus.publish("test", {})  # must not raise

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        handler = lambda d: received.append(d)
        bus.subscribe("ev", handler)
        bus.unsubscribe("ev", handler)
        bus.publish("ev", {})
        self.assertEqual(len(received), 0)

    def test_multiple_handlers_same_event(self):
        bus = EventBus()
        results = []
        bus.subscribe("ev", lambda d: results.append(1))
        bus.subscribe("ev", lambda d: results.append(2))
        bus.publish("ev", {})
        self.assertEqual(sorted(results), [1, 2])

    def test_event_type_added_to_data(self):
        bus = EventBus()
        received = []
        bus.subscribe("task.started", lambda d: received.append(d))
        bus.publish("task.started", {"id": "abc"})
        self.assertEqual(received[0]["_event_type"], "task.started")

    def test_clear_removes_all_handlers(self):
        bus = EventBus()
        received = []
        bus.subscribe("ev", lambda d: received.append(d))
        bus.clear()
        bus.publish("ev", {})
        self.assertEqual(received, [])

    def test_bus_event_constants_exist(self):
        for attr in ["TASK_STARTED", "TOOL_EXECUTED", "SECRET_DETECTED",
                     "REPAIR_STARTED", "MODEL_DEGRADED", "VERIFY_PASSED"]:
            self.assertTrue(hasattr(BusEvent, attr))


# ===========================================================================
# P1.5 — HealthMonitor
# ===========================================================================

class TestHealthMonitor(Base):

    def test_returns_list_of_health_checks(self):
        monitor = HealthMonitor(self.workspace)
        checks = monitor.check_all()
        self.assertIsInstance(checks, list)
        self.assertTrue(len(checks) > 0)

    def test_each_check_has_required_fields(self):
        monitor = HealthMonitor(self.workspace)
        checks = monitor.check_all()
        for c in checks:
            self.assertIsNotNone(c.component)
            self.assertIn(c.status, ["ok", "warn", "degraded", "error"])
            self.assertIsNotNone(c.detail)

    def test_python_version_check_ok(self):
        monitor = HealthMonitor(self.workspace)
        checks = monitor.check_all()
        py_check = next((c for c in checks if c.component == "python"), None)
        self.assertIsNotNone(py_check)
        self.assertIn(py_check.status, ["ok", "warn"])

    def test_tool_registry_check_ok(self):
        monitor = HealthMonitor(self.workspace)
        checks = monitor.check_all()
        tr_check = next((c for c in checks if c.component == "tool_registry"), None)
        self.assertIsNotNone(tr_check)
        self.assertEqual(tr_check.status, "ok")

    def test_policy_engine_check_ok(self):
        monitor = HealthMonitor(self.workspace)
        checks = monitor.check_all()
        pe_check = next((c for c in checks if c.component == "policy_engine"), None)
        self.assertIsNotNone(pe_check)
        self.assertEqual(pe_check.status, "ok")

    def test_overall_status_healthy(self):
        monitor = HealthMonitor(self.workspace)
        checks = [HealthCheck("a", "ok", ""), HealthCheck("b", "ok", "")]
        self.assertEqual(monitor.overall_status(checks), "HEALTHY")

    def test_overall_status_warn(self):
        monitor = HealthMonitor(self.workspace)
        checks = [HealthCheck("a", "ok", ""), HealthCheck("b", "warn", "")]
        self.assertEqual(monitor.overall_status(checks), "WARN")

    def test_overall_status_degraded(self):
        monitor = HealthMonitor(self.workspace)
        checks = [HealthCheck("a", "error", "", critical=True)]
        self.assertEqual(monitor.overall_status(checks), "DEGRADED")


# ===========================================================================
# P1.2 + P1.3 — RepairEngine + Retry Budget
# ===========================================================================

class TestRepairEngine(Base):

    def _task(self):
        t = Task(prompt="fix tests", intent=TaskIntent.DEBUG, max_tool_calls=20)
        return t

    def _passing_output(self):
        return "5 passed in 1.2s"

    def _failing_output(self):
        return "FAILED tests/test_auth.py::test_login - AssertionError: assert 1 == 2\n1 failed in 0.5s"

    def test_repair_succeeds_when_tests_pass_immediately(self):
        task = self._task()
        calls = [0]

        def run_cmd(cmd):
            calls[0] += 1
            return self._passing_output()

        engine = RepairEngine(
            task=task,
            run_command=run_cmd,
            ask_model=lambda p: "Fixed.",
            test_command="pytest",
        )
        result = engine.run()
        self.assertEqual(result.final_status, "resolved")
        self.assertEqual(result.total_attempts, 1)

    def test_repair_exhausted_after_max_attempts(self):
        task = self._task()

        def run_cmd(cmd):
            return self._failing_output()

        def ask_model(prompt):
            return "Attempting fix..."

        engine = RepairEngine(
            task=task,
            run_command=run_cmd,
            ask_model=ask_model,
            test_command="pytest",
            max_attempts=3,
        )
        result = engine.run()
        self.assertEqual(result.final_status, "exhausted")
        self.assertEqual(result.total_attempts, 3)
        self.assertEqual(task.status, TaskStatus.BLOCKED)

    def test_repair_stops_at_budget(self):
        task = self._task()
        task.max_tool_calls = 1   # extremely tight budget
        task.tool_call_count = 1  # already at limit

        def run_cmd(cmd):
            return self._failing_output()

        engine = RepairEngine(
            task=task,
            run_command=run_cmd,
            ask_model=lambda p: "",
            test_command="pytest",
        )
        result = engine.run()
        self.assertEqual(result.final_status, "blocked")

    def test_repair_attempts_recorded(self):
        task = self._task()
        attempt_count = [0]

        def run_cmd(cmd):
            attempt_count[0] += 1
            if attempt_count[0] >= 2:
                return self._passing_output()
            return self._failing_output()

        engine = RepairEngine(
            task=task,
            run_command=run_cmd,
            ask_model=lambda p: "fix applied",
            test_command="pytest",
        )
        result = engine.run()
        self.assertEqual(result.final_status, "resolved")
        self.assertGreaterEqual(len(result.attempts), 1)

    def test_max_repair_attempts_constant(self):
        self.assertEqual(MAX_REPAIR_ATTEMPTS, 3)

    def test_repair_emits_bus_events(self):
        task = self._task()
        events = []
        get_bus().subscribe("*", lambda d: events.append(d["_event_type"]))

        engine = RepairEngine(
            task=task,
            run_command=lambda cmd: self._passing_output(),
            ask_model=lambda p: "",
            test_command="pytest",
        )
        engine.run()
        self.assertIn(BusEvent.REPAIR_STARTED, events)
        self.assertIn(BusEvent.REPAIR_SUCCEEDED, events)
        get_bus().clear()


# ===========================================================================
# P2.1 — ModelProfile
# ===========================================================================

class TestModelProfile(unittest.TestCase):

    def test_builtin_profiles_exist(self):
        self.assertIn("ollama/qwen2.5-coder:7b", BUILTIN_PROFILES)
        self.assertIn("anthropic/claude-sonnet-4-5", BUILTIN_PROFILES)
        self.assertIn("groq/llama-3.3-70b-versatile", BUILTIN_PROFILES)

    def test_ollama_is_local(self):
        profile = BUILTIN_PROFILES["ollama/qwen2.5-coder:7b"]
        self.assertTrue(profile.is_local)
        self.assertEqual(profile.cost_class, CostClass.FREE)

    def test_claude_supports_vision(self):
        profile = BUILTIN_PROFILES["anthropic/claude-sonnet-4-5"]
        self.assertTrue(profile.supports_vision())

    def test_supports_tools_native(self):
        profile = BUILTIN_PROFILES["groq/llama-3.3-70b-versatile"]
        self.assertTrue(profile.supports_tools())

    def test_to_dict_has_all_sections(self):
        profile = BUILTIN_PROFILES["openai/gpt-4o"]
        d = profile.to_dict()
        for key in ["provider", "model", "input", "output", "reasoning", "context_window", "health"]:
            self.assertIn(key, d)

    def test_capability_entry_status(self):
        entry = CapabilityEntry(CapabilityStatus.VERIFIED, 0.95)
        self.assertEqual(entry.status, CapabilityStatus.VERIFIED)
        self.assertEqual(entry.reliability, 0.95)

    def test_profile_store_save_load(self):
        store = ModelProfileStore()
        profile = ModelProfile(provider="TestProv", model="test-model", context_window=8192)
        store.save(profile)
        loaded = store.load("TestProv", "test-model")
        self.assertIsNotNone(loaded)


# ===========================================================================
# P2.3 + P2.4 — ModelRouter + ModelHealthTracker
# ===========================================================================

class TestModelRouter(unittest.TestCase):

    def test_get_requirements_debug(self):
        router = ModelRouter()
        reqs = router.get_requirements("debug")
        self.assertTrue(reqs.requires_tools)
        self.assertTrue(reqs.requires_strong_coding)

    def test_get_requirements_ask_prefers_fast(self):
        router = ModelRouter()
        reqs = router.get_requirements("ask")
        self.assertFalse(reqs.requires_tools)
        self.assertTrue(reqs.prefers_fast)

    def test_route_returns_provider_from_list(self):
        router = ModelRouter()
        mock_prov = MagicMock()
        mock_prov.provider_name = "Ollama"
        mock_prov.model_name = "qwen2.5-coder:7b"
        result = router.route("debug", [mock_prov])
        self.assertEqual(result, mock_prov)

    def test_route_empty_list_returns_none(self):
        router = ModelRouter()
        result = router.route("debug", [])
        self.assertIsNone(result)

    def test_describe_routing_has_intent(self):
        router = ModelRouter()
        info = router.describe_routing("feature")
        self.assertEqual(info["intent"], "feature")
        self.assertIn("requires_tools", info)

    def test_all_intents_have_requirements(self):
        router = ModelRouter()
        for intent in ["ask", "analyze", "debug", "feature", "refactor", "test", "review", "setup", "unknown"]:
            reqs = router.get_requirements(intent)
            self.assertIsNotNone(reqs)


class TestModelHealthTracker(unittest.TestCase):

    def test_record_and_get_stats(self):
        tracker = ModelHealthTracker()
        tracker.record_call("Ollama", "qwen2.5-coder:7b", latency=1.2)
        stats = tracker.get_stats("Ollama", "qwen2.5-coder:7b")
        self.assertIsNotNone(stats)
        self.assertEqual(stats.total_calls, 1)

    def test_healthy_by_default(self):
        tracker = ModelHealthTracker()
        self.assertTrue(tracker.is_healthy("UnknownProv", "unknown-model"))

    def test_degraded_on_high_timeout_rate(self):
        tracker = ModelHealthTracker()
        for _ in range(5):
            tracker.record_call("BadProv", "bad-model", latency=30.0, timed_out=True)
        stats = tracker.get_stats("BadProv", "bad-model")
        self.assertEqual(stats.health_status, "degraded")

    def test_tool_failure_rate_calculation(self):
        tracker = ModelHealthTracker()
        tracker.record_call("P", "M", 1.0, tool_call_failed=True)
        tracker.record_call("P", "M", 1.0, tool_call_failed=True)
        tracker.record_call("P", "M", 1.0)
        stats = tracker.get_stats("P", "M")
        self.assertAlmostEqual(stats.tool_failure_rate, 2/3, places=2)

    def test_avg_latency(self):
        tracker = ModelHealthTracker()
        tracker.record_call("P", "M", 1.0)
        tracker.record_call("P", "M", 3.0)
        stats = tracker.get_stats("P", "M")
        self.assertAlmostEqual(stats.avg_latency, 2.0, places=1)


# ===========================================================================
# P2.5 — ProjectProfile
# ===========================================================================

class TestProjectProfile(Base):

    def _profile(self, rm=None):
        mem = {
            "project_type": "Python",
            "commands": {
                "test": {"cmd": "pytest", "status": "verified"},
                "build": {"cmd": "", "status": "unverified"},
                "lint": {"cmd": "flake8 .", "status": "verified"},
            }
        }
        return ProjectProfile(self.workspace, rm, mem)

    def test_get_test_command(self):
        p = self._profile()
        self.assertEqual(p.get_test_command(), "pytest")

    def test_get_lint_command(self):
        p = self._profile()
        self.assertEqual(p.get_lint_command(), "flake8 .")

    def test_get_project_type(self):
        p = self._profile()
        self.assertEqual(p.get_project_type(), "Python")

    def test_get_entry_points_no_repo_map(self):
        p = self._profile()
        self.assertEqual(p.get_entry_points(), [])

    def test_get_relevant_files_no_repo_map(self):
        p = self._profile()
        self.assertEqual(p.get_relevant_files("auth"), [])

    def test_get_summary_has_keys(self):
        p = self._profile()
        summary = p.get_summary()
        for key in ["project_type", "entry_points", "test_command", "build_command"]:
            self.assertIn(key, summary)

    def test_invalidate_file_no_crash_without_repo_map(self):
        p = self._profile()
        p.invalidate_file("app.py")  # must not raise

    def test_entry_points_detected_with_repo_map(self):
        from ultron.repo_map import RepoMap
        write(os.path.join(self.workspace, "main.py"), "if __name__ == '__main__': pass\n")
        write(os.path.join(self.workspace, "app.py"), "from flask import Flask\n")
        rm = RepoMap(self.workspace)
        rm.build()
        p = self._profile(rm)
        entries = p.get_entry_points()
        self.assertTrue(any("main" in e or "app" in e for e in entries))


# ===========================================================================
# P2.6 — ContextAssembler
# ===========================================================================

class TestContextAssembler(Base):

    def _assembler(self, intent="build"):
        from ultron.context import ContextManager
        ctx = ContextManager(self.workspace)
        return ContextAssembler(
            workspace_root=self.workspace,
            context_manager=ctx,
            provider_context_window=16384,
        )

    def test_assemble_returns_string(self):
        asm = self._assembler()
        result = asm.assemble("build")
        self.assertIsInstance(result, str)

    def test_assemble_includes_tree(self):
        asm = self._assembler()
        result = asm.assemble("ask")
        self.assertIn("WORKSPACE", result)

    def test_usage_ratio_zero_for_empty(self):
        asm = self._assembler()
        ratio = asm.usage_ratio("")
        self.assertEqual(ratio, 0.0)

    def test_usage_ratio_below_one_for_normal_content(self):
        asm = self._assembler()
        content = "x" * 1000
        ratio = asm.usage_ratio(content)
        self.assertLess(ratio, 1.0)

    def test_debug_strategy_includes_error_context(self):
        from ultron.context_assembler import _STRATEGY
        self.assertIn("error_context", _STRATEGY["debug"])

    def test_feature_strategy_includes_conventions(self):
        from ultron.context_assembler import _STRATEGY
        self.assertIn("conventions", _STRATEGY["feature"])

    def test_refactor_strategy_includes_callers(self):
        from ultron.context_assembler import _STRATEGY
        self.assertIn("callers", _STRATEGY["refactor"])

    def test_all_intents_have_strategy(self):
        from ultron.context_assembler import _STRATEGY
        from ultron.task import TaskIntent
        for intent in TaskIntent:
            self.assertIn(intent.value, _STRATEGY, f"{intent.value} missing from strategy")


# ===========================================================================
# REPL P1/P2 command smoke tests
# ===========================================================================

class TestReplP1P2Commands(Base):

    def setUp(self):
        super().setUp()
        import subprocess
        subprocess.run(["git", "init", self.workspace], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.name", "T"], capture_output=True)
        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    @patch("ultron.repl.PromptSession")
    def test_doctor_uses_health_monitor(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/doctor")

    @patch("ultron.repl.PromptSession")
    def test_route_info_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/route-info debug")

    @patch("ultron.repl.PromptSession")
    def test_probe_model_unavailable(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        # Model likely unavailable in test env — should print error gracefully
        with patch.object(self.agent.model, "is_available", return_value=False):
            repl.handle_slash_command("/probe")

    @patch("ultron.repl.PromptSession")
    def test_p1_p2_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/probe", "/route-info"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")

    @patch("ultron.repl.PromptSession")
    def test_session_log_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/session-log")

    @patch("ultron.repl.PromptSession")
    def test_context_status_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/context-status")


if __name__ == "__main__":
    unittest.main()
