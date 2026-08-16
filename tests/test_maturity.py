"""
test_maturity.py - Tests for Workstream A (Task/TaskRouter),
Workstream C (ToolRegistry/PolicyEngine/CommandRunner/ChangeTracker),
Workstream E (tracer/compare/flaky/parsers/verif planner),
and Workstream F (eval suite/metrics/mock provider).
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ultron.task import (
    Task, TaskRouter, TaskStatus, TaskIntent, EvidenceKind,
    BudgetEnforcer, WORKFLOW_TEMPLATES, INTENT_DEFAULT_MODE
)
from ultron.tool_registry import (
    ToolRegistry, ToolDefinition, RiskLevel, PolicyEngine,
    PolicyDecision, CommandRunner, CommandResult, MODE_RISK_BLOCKS
)
from ultron.change_tracker import ChangeTracker
from ultron.tracer import (
    FeatureTracer, BranchComparer, FlakyTestDetector,
    TestOutputParser, VerificationPlanner
)
from ultron.eval_suite import (
    MockProvider, MockProviderResponse, MetricsCollector,
    TaskMetrics, FixtureWorkspace
)


def write(path, content="# placeholder\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class Base(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)


# ===========================================================================
# Workstream A — Task model + TaskRouter
# ===========================================================================

class TestTaskModel(unittest.TestCase):

    def test_task_creation_defaults(self):
        t = Task(prompt="add login feature")
        self.assertEqual(t.status, TaskStatus.PLANNED)
        self.assertEqual(t.tool_call_count, 0)
        self.assertIsNotNone(t.id)

    def test_task_over_budget_tool_calls(self):
        t = Task(max_tool_calls=3)
        t.tool_call_count = 3
        self.assertTrue(t.is_over_budget())

    def test_task_not_over_budget(self):
        t = Task(max_tool_calls=12)
        t.tool_call_count = 5
        self.assertFalse(t.is_over_budget())

    def test_add_evidence(self):
        t = Task()
        t.add_evidence(EvidenceKind.VERIFIED, "pytest passed", "pytest tests/")
        self.assertEqual(len(t.evidence), 1)
        self.assertEqual(t.evidence[0].kind, EvidenceKind.VERIFIED)

    def test_has_unverified_true(self):
        t = Task()
        t.add_evidence(EvidenceKind.NOT_VERIFIED, "assumed no side effects")
        self.assertTrue(t.has_unverified())

    def test_has_unverified_false(self):
        t = Task()
        t.add_evidence(EvidenceKind.VERIFIED, "tests passed")
        self.assertFalse(t.has_unverified())

    def test_budget_status_string(self):
        t = Task(max_tool_calls=12)
        t.tool_call_count = 5
        status = t.budget_status()
        self.assertIn("5/12", status)

    def test_summary_lines(self):
        t = Task(prompt="fix the bug")
        t.actual_files = ["app.py"]
        t.add_evidence(EvidenceKind.VERIFIED, "tests passed")
        lines = t.summary_lines()
        self.assertTrue(any("app.py" in l for l in lines))
        self.assertTrue(any("Verified" in l for l in lines))


class TestTaskRouter(unittest.TestCase):

    def setUp(self):
        self.router = TaskRouter()

    def test_classify_debug_intent(self):
        intent = self.router.classify("fix the error in auth.py")
        self.assertEqual(intent, TaskIntent.DEBUG)

    def test_classify_feature_intent(self):
        intent = self.router.classify("add user registration endpoint")
        self.assertEqual(intent, TaskIntent.FEATURE)

    def test_classify_refactor_intent(self):
        intent = self.router.classify("refactor the payment service")
        self.assertEqual(intent, TaskIntent.REFACTOR)

    def test_classify_test_intent(self):
        intent = self.router.classify("write unit tests for UserService")
        self.assertEqual(intent, TaskIntent.TEST)

    def test_classify_review_intent(self):
        intent = self.router.classify("review the auth module code")
        self.assertIn(intent, [TaskIntent.REVIEW, TaskIntent.ANALYZE])

    def test_classify_analyze_intent(self):
        intent = self.router.classify("explain how the payment flow works")
        self.assertEqual(intent, TaskIntent.ANALYZE)

    def test_classify_unknown(self):
        intent = self.router.classify("zxqwerty nonsense input")
        self.assertEqual(intent, TaskIntent.UNKNOWN)

    def test_create_task_sets_intent(self):
        task = self.router.create_task("fix the crash in login")
        self.assertEqual(task.intent, TaskIntent.DEBUG)
        self.assertEqual(task.status, TaskStatus.PLANNED)

    def test_get_workflow_feature(self):
        workflow = self.router.get_workflow(TaskIntent.FEATURE)
        self.assertIn("inspect", workflow)
        self.assertIn("execute", workflow)
        self.assertIn("verify", workflow)

    def test_get_workflow_ask(self):
        workflow = self.router.get_workflow(TaskIntent.ASK)
        self.assertNotIn("execute", workflow)

    def test_get_system_hint_contains_task_info(self):
        task = self.router.create_task("add login")
        hint = self.router.get_system_hint(task)
        self.assertIn("CURRENT TASK", hint)
        self.assertIn(task.id, hint)

    def test_all_intents_have_workflows(self):
        for intent in TaskIntent:
            workflow = self.router.get_workflow(intent)
            self.assertIsInstance(workflow, list)
            self.assertTrue(len(workflow) > 0)


class TestBudgetEnforcer(unittest.TestCase):

    def test_continue_when_under_budget(self):
        t = Task(max_tool_calls=12)
        t.tool_call_count = 3
        enforcer = BudgetEnforcer()
        result = enforcer.check(t)
        self.assertEqual(result["action"], "continue")

    def test_stop_when_over_tool_budget(self):
        t = Task(max_tool_calls=5)
        t.tool_call_count = 5
        enforcer = BudgetEnforcer()
        result = enforcer.check(t)
        self.assertEqual(result["action"], "stop")

    def test_stop_when_repair_limit_reached(self):
        t = Task(max_repair_attempts=3)
        t.repair_attempt_count = 3
        enforcer = BudgetEnforcer()
        result = enforcer.check(t)
        self.assertEqual(result["action"], "stop")

    def test_warn_at_75_percent(self):
        t = Task(max_tool_calls=12)
        t.tool_call_count = 10  # 83%
        enforcer = BudgetEnforcer()
        result = enforcer.check(t)
        self.assertIn(result["action"], ["warn", "stop"])


# ===========================================================================
# Workstream C — ToolRegistry + PolicyEngine + CommandRunner + ChangeTracker
# ===========================================================================

class TestToolRegistry(unittest.TestCase):

    def test_build_default_has_all_tools(self):
        reg = ToolRegistry.build_default()
        for name in ["list_dir", "view_file", "grep_search", "write_file",
                     "patch_file", "run_command", "git_status", "git_commit"]:
            self.assertIsNotNone(reg.get(name), f"{name} missing from registry")

    def test_get_risk_read_only(self):
        reg = ToolRegistry.build_default()
        self.assertEqual(reg.get_risk("view_file"), RiskLevel.READ_ONLY)

    def test_get_risk_workspace_write(self):
        reg = ToolRegistry.build_default()
        self.assertEqual(reg.get_risk("write_file"), RiskLevel.WORKSPACE_WRITE)

    def test_get_risk_git_write(self):
        reg = ToolRegistry.build_default()
        self.assertEqual(reg.get_risk("git_commit"), RiskLevel.GIT_WRITE)

    def test_get_json_schemas_format(self):
        reg = ToolRegistry.build_default()
        schemas = reg.get_json_schemas()
        self.assertTrue(len(schemas) >= 8)
        for s in schemas:
            self.assertEqual(s["type"], "function")
            self.assertIn("name", s["function"])

    def test_register_custom_tool(self):
        reg = ToolRegistry()
        tool = ToolDefinition(
            name="custom_tool",
            description="test",
            schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.READ_ONLY,
        )
        reg.register(tool)
        self.assertIsNotNone(reg.get("custom_tool"))

    def test_unknown_tool_returns_workspace_write_risk(self):
        reg = ToolRegistry.build_default()
        self.assertEqual(reg.get_risk("nonexistent_tool"), RiskLevel.WORKSPACE_WRITE)


class TestPolicyEngine(unittest.TestCase):

    def test_allow_read_only_always(self):
        engine = PolicyEngine(auto_approve=False)
        decision = engine.evaluate("view_file", RiskLevel.READ_ONLY, "build")
        self.assertEqual(decision.decision, PolicyDecision.ALLOW)

    def test_deny_write_in_ask_mode(self):
        engine = PolicyEngine(auto_approve=False)
        decision = engine.evaluate("write_file", RiskLevel.WORKSPACE_WRITE, "ask")
        self.assertEqual(decision.decision, PolicyDecision.DENY)

    def test_deny_write_in_plan_mode(self):
        engine = PolicyEngine(auto_approve=False)
        decision = engine.evaluate("write_file", RiskLevel.WORKSPACE_WRITE, "plan")
        self.assertEqual(decision.decision, PolicyDecision.DENY)

    def test_allow_write_in_build_mode_with_auto_approve(self):
        engine = PolicyEngine(auto_approve=True)
        decision = engine.evaluate("write_file", RiskLevel.WORKSPACE_WRITE, "build")
        self.assertEqual(decision.decision, PolicyDecision.ALLOW)

    def test_ask_write_in_build_mode_no_auto(self):
        engine = PolicyEngine(auto_approve=False)
        decision = engine.evaluate("write_file", RiskLevel.WORKSPACE_WRITE, "build")
        self.assertEqual(decision.decision, PolicyDecision.ASK)

    def test_deny_git_commit_in_review_mode(self):
        engine = PolicyEngine(auto_approve=True)
        decision = engine.evaluate("git_commit", RiskLevel.GIT_WRITE, "review")
        self.assertEqual(decision.decision, PolicyDecision.DENY)

    def test_explicit_rule_overrides_mode(self):
        from ultron.tool_registry import PolicyRule
        engine = PolicyEngine(auto_approve=False)
        engine.add_rule(PolicyRule(tool_name="write_file", risk_level=None, decision=PolicyDecision.ALLOW, reason="test"))
        decision = engine.evaluate("write_file", RiskLevel.WORKSPACE_WRITE, "ask")
        self.assertEqual(decision.decision, PolicyDecision.ALLOW)

    def test_mode_blocks_dict_completeness(self):
        for mode in ["ask", "plan", "review", "build", "fix"]:
            self.assertIn(mode, MODE_RISK_BLOCKS)


class TestCommandRunner(Base):

    def test_run_simple_command(self):
        runner = CommandRunner(self.workspace)
        result = runner.run("echo hello")
        self.assertIsInstance(result, CommandResult)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)

    def test_run_failing_command(self):
        runner = CommandRunner(self.workspace)
        result = runner.run("exit 1" if os.name == "nt" else "false")
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(result.succeeded())

    def test_result_logged(self):
        runner = CommandRunner(self.workspace)
        runner.run("echo test")
        self.assertEqual(len(runner.execution_logs), 1)

    def test_result_has_duration(self):
        runner = CommandRunner(self.workspace)
        result = runner.run("echo hi")
        self.assertGreater(result.duration, 0)

    def test_to_display_includes_exit_code(self):
        runner = CommandRunner(self.workspace)
        result = runner.run("echo hi")
        display = result.to_display()
        self.assertIn("0", display)

    def test_last_error_set_on_failure(self):
        runner = CommandRunner(self.workspace)
        runner.run("exit 1" if os.name == "nt" else "false")
        self.assertIsNotNone(runner.last_error)

    def test_succeeded_true_on_zero_exit(self):
        result = CommandResult("echo", self.workspace, 0, "out", "", 0.1)
        self.assertTrue(result.succeeded())

    def test_succeeded_false_on_nonzero(self):
        result = CommandResult("bad", self.workspace, 1, "", "err", 0.1)
        self.assertFalse(result.succeeded())


class TestChangeTracker(Base):

    def test_record_before_and_after(self):
        write(os.path.join(self.workspace, "app.py"), "x = 1\n")
        tracker = ChangeTracker(self.workspace)
        tracker.record_before("app.py")
        write(os.path.join(self.workspace, "app.py"), "x = 2\n")
        tracker.record_after("app.py")
        self.assertIn("app.py", tracker.actual_files)
        self.assertIsNotNone(tracker.actual_files["app.py"]["hash_after"])

    def test_set_expected_and_is_unplanned(self):
        tracker = ChangeTracker(self.workspace)
        tracker.set_expected(["app.py"])
        tracker.actual_files["unexpected.py"] = {}
        self.assertTrue(tracker.is_unplanned("unexpected.py"))
        self.assertFalse(tracker.is_unplanned("app.py"))

    def test_get_scope_delta(self):
        tracker = ChangeTracker(self.workspace)
        tracker.set_expected(["app.py"])
        tracker.actual_files["app.py"] = {}
        tracker.actual_files["extra.py"] = {}
        delta = tracker.get_scope_delta()
        self.assertTrue(delta["scope_exceeded"])
        self.assertIn("extra.py", delta["unplanned_files"])

    def test_mark_and_check_user_dirty(self):
        tracker = ChangeTracker(self.workspace)
        tracker.mark_user_dirty("dirty.py")
        self.assertTrue(tracker.is_user_dirty("dirty.py"))
        self.assertFalse(tracker.is_user_dirty("clean.py"))

    def test_reset_clears_all(self):
        tracker = ChangeTracker(self.workspace)
        tracker.set_expected(["a.py"])
        tracker.actual_files["a.py"] = {}
        tracker.mark_user_dirty("b.py")
        tracker.reset()
        self.assertEqual(len(tracker.expected_files), 0)
        self.assertEqual(len(tracker.actual_files), 0)
        self.assertEqual(len(tracker.user_dirty_files), 0)

    def test_get_modified_files(self):
        tracker = ChangeTracker(self.workspace)
        tracker.actual_files["a.py"] = {}
        tracker.actual_files["b.py"] = {}
        files = tracker.get_modified_files()
        self.assertIn("a.py", files)
        self.assertIn("b.py", files)


# ===========================================================================
# Workstream E — Tracer, BranchComparer, FlakyTestDetector, parsers
# ===========================================================================

class TestFeatureTracer(Base):

    def _build_rm(self):
        from ultron.repo_map import RepoMap
        rm = RepoMap(self.workspace)
        rm.build()
        return rm

    def test_trace_no_repo_map(self):
        tracer = FeatureTracer(self.workspace, None)
        result = tracer.trace("UserService")
        self.assertIn("error", result)

    def test_trace_finds_definitions(self):
        write(os.path.join(self.workspace, "service.py"),
              "class UserService:\n    def login(self): pass\n")
        write(os.path.join(self.workspace, "routes.py"),
              "from service import UserService\nUserService().login()\n")
        rm = self._build_rm()
        tracer = FeatureTracer(self.workspace, rm)
        result = tracer.trace("UserService")
        self.assertEqual(result["symbol"], "UserService")
        self.assertIsInstance(result["layers"], dict)
        self.assertIsInstance(result["flow_path"], list)

    def test_trace_detects_service_layer(self):
        write(os.path.join(self.workspace, "auth_service.py"),
              "class AuthService:\n    def authenticate(self): pass\n")
        rm = self._build_rm()
        tracer = FeatureTracer(self.workspace, rm)
        result = tracer.trace("AuthService")
        self.assertIsNotNone(result)

    def test_trace_detects_test_layer(self):
        write(os.path.join(self.workspace, "service.py"), "def process(): pass\n")
        write(os.path.join(self.workspace, "test_service.py"),
              "def test_process():\n    from service import process\n    process()\n")
        rm = self._build_rm()
        tracer = FeatureTracer(self.workspace, rm)
        result = tracer.trace("process")
        self.assertIn("test", result.get("flow_path", []) + list(result.get("layers", {}).keys()))


class TestBranchComparer(Base):

    def _init_git(self):
        import subprocess
        subprocess.run(["git", "init", self.workspace], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.name", "T"], capture_output=True)

    def test_current_branch_non_repo(self):
        comparer = BranchComparer(self.workspace)
        branch = comparer.current_branch()
        self.assertIsInstance(branch, str)

    def test_list_branches_empty_repo(self):
        self._init_git()
        comparer = BranchComparer(self.workspace)
        branches = comparer.list_branches()
        self.assertIsInstance(branches, list)

    def test_compare_invalid_base_no_crash(self):
        self._init_git()
        comparer = BranchComparer(self.workspace)
        result = comparer.compare("nonexistent-branch-xyz")
        self.assertIn("changed_files", result)
        self.assertIn("stat", result)

    def test_compare_result_has_required_keys(self):
        self._init_git()
        comparer = BranchComparer(self.workspace)
        result = comparer.compare("main")
        for key in ["base", "target", "changed_files", "stat", "file_count"]:
            self.assertIn(key, result)


class TestFlakyTestDetector(Base):

    def test_stable_passing_command(self):
        detector = FlakyTestDetector(self.workspace)
        result = detector.run_multiple("echo stable", runs=3)
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertFalse(result["flaky_detected"])

    def test_consistently_failing_command(self):
        detector = FlakyTestDetector(self.workspace)
        cmd = "exit 1" if os.name == "nt" else "false"
        result = detector.run_multiple(cmd, runs=3)
        self.assertEqual(result["failed"], 3)
        self.assertFalse(result["flaky_detected"])

    def test_result_has_required_keys(self):
        detector = FlakyTestDetector(self.workspace)
        result = detector.run_multiple("echo x", runs=2)
        for key in ["command", "runs", "passed", "failed", "flaky_detected", "variation", "results", "recommendation"]:
            self.assertIn(key, result)

    def test_runs_count_matches(self):
        detector = FlakyTestDetector(self.workspace)
        result = detector.run_multiple("echo x", runs=4)
        self.assertEqual(result["runs"], 4)
        self.assertEqual(len(result["results"]), 4)


class TestTestOutputParser(unittest.TestCase):

    def test_parse_pytest_passing(self):
        output = "collected 5 items\n...\n5 passed in 1.23s"
        result = TestOutputParser.parse(output, "pytest")
        self.assertEqual(result["passed"], 5)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["overall"], "passed")

    def test_parse_pytest_with_failures(self):
        output = "FAILED tests/test_auth.py::test_login - AssertionError\n2 failed, 3 passed in 2s"
        result = TestOutputParser.parse(output, "pytest")
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["overall"], "failed")

    def test_parse_unittest(self):
        output = "Ran 10 tests in 0.5s\n\nOK"
        result = TestOutputParser.parse(output, "unittest")
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["overall"], "passed")

    def test_parse_unittest_failure(self):
        output = "Ran 5 tests in 0.3s\n\nFAILED (failures=2)"
        result = TestOutputParser.parse(output, "unittest")
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["overall"], "failed")

    def test_parse_cargo(self):
        output = "test result: ok. 8 passed; 0 failed; 2 ignored"
        result = TestOutputParser.parse(output, "cargo")
        self.assertEqual(result["passed"], 8)
        self.assertEqual(result["failed"], 0)

    def test_parse_go(self):
        output = "--- PASS: TestAdd (0.00s)\n--- PASS: TestSub (0.00s)\n--- FAIL: TestMul (0.01s)"
        result = TestOutputParser.parse(output, "go")
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["failed"], 1)

    def test_parse_npm(self):
        output = "Tests: 1 failed, 4 passed, 5 total"
        result = TestOutputParser.parse(output, "npm")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["passed"], 4)

    def test_auto_detect_pytest(self):
        output = "5 passed in 1.2s"
        result = TestOutputParser.parse(output)
        self.assertEqual(result["framework"], "pytest")

    def test_auto_detect_go(self):
        output = "--- PASS: TestFoo (0.01s)\nok  mypackage  0.01s"
        result = TestOutputParser.parse(output)
        self.assertEqual(result["framework"], "go")

    def test_result_always_has_framework_key(self):
        result = TestOutputParser.parse("some output")
        self.assertIn("framework", result)


class TestVerificationPlanner(unittest.TestCase):

    def _mem(self, test_cmd="pytest", lint_cmd="flake8 .", fmt_cmd="black .", build_cmd=""):
        return {
            "commands": {
                "test":   {"cmd": test_cmd,  "status": "verified"},
                "lint":   {"cmd": lint_cmd,  "status": "verified"},
                "format": {"cmd": fmt_cmd,   "status": "verified"},
                "build":  {"cmd": build_cmd, "status": "unverified"},
            }
        }

    def test_includes_tests_when_command_available(self):
        planner = VerificationPlanner()
        checks = planner.plan(["app.py"], self._mem())
        self.assertIn("tests", checks)

    def test_includes_lint_for_source_changes(self):
        planner = VerificationPlanner()
        checks = planner.plan(["service.py"], self._mem())
        self.assertIn("lint", checks)

    def test_excludes_build_when_no_command(self):
        planner = VerificationPlanner()
        checks = planner.plan(["app.py"], self._mem(build_cmd=""))
        self.assertNotIn("build", checks)

    def test_always_includes_secrets(self):
        planner = VerificationPlanner()
        checks = planner.plan([], self._mem())
        self.assertIn("secrets", checks)

    def test_empty_changed_files(self):
        planner = VerificationPlanner()
        checks = planner.plan([], self._mem())
        self.assertIsInstance(checks, list)


# ===========================================================================
# Workstream F — MockProvider + MetricsCollector + FixtureWorkspace
# ===========================================================================

class TestMockProvider(unittest.TestCase):

    def test_returns_scripted_content(self):
        provider = MockProvider([
            MockProviderResponse(content="Hello world"),
        ])
        chunks = []
        gen = provider.chat([{"role": "user", "content": "hi"}])
        while True:
            try:
                chunk = next(gen)
                chunks.append(chunk)
            except StopIteration:
                break
        self.assertTrue(any(c.get("delta") == "Hello world" for c in chunks))

    def test_returns_tool_calls(self):
        tool_call = [{
            "id": "call_1", "type": "function",
            "function": {"name": "write_file", "arguments": {"path": "a.py", "content": "x"}}
        }]
        provider = MockProvider([
            MockProviderResponse(tool_calls=tool_call),
        ])
        chunks = []
        gen = provider.chat([{"role": "user", "content": "write file"}])
        while True:
            try:
                chunk = next(gen)
                chunks.append(chunk)
            except StopIteration:
                break
        self.assertTrue(any(c.get("type") == "tool_calls" for c in chunks))

    def test_is_available_always_true(self):
        provider = MockProvider([])
        self.assertTrue(provider.is_available())

    def test_call_count_increments(self):
        provider = MockProvider([MockProviderResponse(content="a"), MockProviderResponse(content="b")])
        for _ in range(2):
            gen = provider.chat([])
            while True:
                try:
                    next(gen)
                except StopIteration:
                    break
        self.assertEqual(provider.call_count, 2)

    def test_clarification_response(self):
        provider = MockProvider([
            MockProviderResponse(needs_clarification=True, question="Which endpoint?"),
        ])
        chunks = []
        gen = provider.chat([{"role": "user", "content": "add endpoint"}])
        while True:
            try:
                chunk = next(gen)
                chunks.append(chunk)
            except StopIteration:
                break
        content = "".join(c.get("delta", "") for c in chunks)
        self.assertIn("clarification", content)

    def test_exhausted_responses_returns_default(self):
        provider = MockProvider([])
        chunks = []
        gen = provider.chat([{"role": "user", "content": "hi"}])
        while True:
            try:
                chunk = next(gen)
                chunks.append(chunk)
            except StopIteration:
                break
        # Should not crash
        self.assertIsInstance(chunks, list)


class TestMetricsCollector(Base):

    def test_record_and_load(self):
        collector = MetricsCollector(self.workspace)
        m = TaskMetrics(
            task_id="t1", prompt="fix bug", intent="debug",
            success=True, files_changed=["app.py"],
            commands_run=["pytest"], tool_call_count=5,
            duration_seconds=12.3, had_unverified=False,
        )
        collector.record(m)
        history = collector.load_history(10)
        self.assertTrue(any(h["task_id"] == "t1" for h in history))

    def test_compute_summary(self):
        collector = MetricsCollector(self.workspace)
        for i in range(3):
            collector.record(TaskMetrics(
                task_id=f"t{i}", prompt="test", intent="feature",
                success=i < 2, files_changed=[], commands_run=[],
                tool_call_count=i+1, duration_seconds=5.0,
                had_unverified=i == 2,
            ))
        summary = collector.compute_summary()
        self.assertIn("task_completion_rate", summary)
        self.assertAlmostEqual(summary["task_completion_rate"], 2/3, places=2)

    def test_empty_summary(self):
        collector = MetricsCollector(self.workspace)
        summary = collector.compute_summary()
        self.assertEqual(summary, {})


class TestFixtureWorkspace(unittest.TestCase):

    def test_python_basic_fixture(self):
        with FixtureWorkspace("python_basic") as workspace:
            self.assertTrue(os.path.isdir(workspace))
            self.assertTrue(os.path.isfile(os.path.join(workspace, "setup.py")))
            self.assertTrue(os.path.isfile(os.path.join(workspace, "app", "service.py")))
            self.assertTrue(os.path.isfile(os.path.join(workspace, "tests", "test_service.py")))

    def test_node_basic_fixture(self):
        with FixtureWorkspace("node_basic") as workspace:
            self.assertTrue(os.path.isfile(os.path.join(workspace, "package.json")))
            self.assertTrue(os.path.isfile(os.path.join(workspace, "src", "index.ts")))

    def test_monorepo_fixture(self):
        with FixtureWorkspace("monorepo") as workspace:
            self.assertTrue(os.path.isdir(os.path.join(workspace, "packages", "api")))
            self.assertTrue(os.path.isdir(os.path.join(workspace, "packages", "worker")))

    def test_git_initialized(self):
        import subprocess
        with FixtureWorkspace("python_basic") as workspace:
            r = subprocess.run(["git", "log", "--oneline"], cwd=workspace, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("initial fixture", r.stdout)

    def test_cleanup_on_exit(self):
        path = None
        with FixtureWorkspace("python_basic") as workspace:
            path = workspace
        self.assertFalse(os.path.exists(path))


# ===========================================================================
# REPL smoke tests for new commands
# ===========================================================================

class TestReplMaturityCommands(Base):

    def setUp(self):
        super().setUp()
        import subprocess
        subprocess.run(["git", "init", self.workspace], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "config", "user.name", "T"], capture_output=True)
        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    @patch("ultron.repl.PromptSession")
    def test_trace_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/trace")  # should print usage

    @patch("ultron.repl.PromptSession")
    def test_trace_with_symbol(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "svc.py"), "def compute(): pass\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/trace compute")

    @patch("ultron.repl.PromptSession")
    def test_compare_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/compare")

    @patch("ultron.repl.PromptSession")
    def test_compare_with_branch(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/compare main")

    @patch("ultron.repl.PromptSession")
    @patch("rich.prompt.Prompt.ask", return_value="2")
    def test_flaky_test_with_arg(self, mock_prompt, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/flaky-test echo stable")

    @patch("ultron.repl.PromptSession")
    def test_new_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/trace", "/compare", "/flaky-test"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")


if __name__ == "__main__":
    unittest.main()
