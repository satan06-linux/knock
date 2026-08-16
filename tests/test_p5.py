"""
test_p5.py - P5: Measurement + Continuous Maturity tests.
P5.1: Fixture expansion (Go, Rust).
P5.2: Metrics dashboard (historical rates).
P5.3: CI regression gate (eval harness).
P5.4: Release readiness script.
"""
import os
import shutil
import tempfile
import unittest

from ultron.eval_suite import (
    FixtureWorkspace, MetricsCollector, TaskMetrics, MockProvider, MockProviderResponse
)


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
# P5.1 — Fixture expansion
# ===========================================================================

class TestFixtureExpansion(unittest.TestCase):

    def test_go_basic_fixture_has_go_mod(self):
        with FixtureWorkspace("go_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "go.mod")))

    def test_go_basic_fixture_has_main(self):
        with FixtureWorkspace("go_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "main.go")))

    def test_go_basic_fixture_has_test(self):
        with FixtureWorkspace("go_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "main_test.go")))

    def test_go_main_contains_add_function(self):
        with FixtureWorkspace("go_basic") as ws:
            with open(os.path.join(ws, "main.go")) as f:
                content = f.read()
            self.assertIn("func add", content)

    def test_go_test_uses_testing_package(self):
        with FixtureWorkspace("go_basic") as ws:
            with open(os.path.join(ws, "main_test.go")) as f:
                content = f.read()
            self.assertIn('"testing"', content)

    def test_rust_basic_fixture_has_cargo_toml(self):
        with FixtureWorkspace("rust_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "Cargo.toml")))

    def test_rust_basic_fixture_has_lib_rs(self):
        with FixtureWorkspace("rust_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "src", "lib.rs")))

    def test_rust_basic_fixture_has_test(self):
        with FixtureWorkspace("rust_basic") as ws:
            with open(os.path.join(ws, "src", "lib.rs")) as f:
                content = f.read()
            self.assertIn("#[test]", content)

    def test_rust_lib_contains_pub_fn_add(self):
        with FixtureWorkspace("rust_basic") as ws:
            with open(os.path.join(ws, "src", "lib.rs")) as f:
                content = f.read()
            self.assertIn("pub fn add", content)

    def test_all_five_fixtures_exist(self):
        for scenario in ["python_basic", "node_basic", "monorepo", "go_basic", "rust_basic"]:
            with FixtureWorkspace(scenario) as ws:
                self.assertTrue(os.path.isdir(ws), f"{scenario} fixture not created")

    def test_go_fixture_git_initialized(self):
        import subprocess
        with FixtureWorkspace("go_basic") as ws:
            r = subprocess.run(["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)

    def test_rust_fixture_git_initialized(self):
        import subprocess
        with FixtureWorkspace("rust_basic") as ws:
            r = subprocess.run(["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)


# ===========================================================================
# P5.2 — Metrics dashboard (historical rates)
# ===========================================================================

class TestMetricsDashboard(Base):

    def _collector(self):
        return MetricsCollector(self.workspace)

    def _make_metrics(self, task_id, success=True, tool_calls=5, duration=10.0,
                      had_unverified=False, unsafe=0, approvals=0, overflows=0):
        return TaskMetrics(
            task_id=task_id, prompt=f"task {task_id}", intent="debug",
            success=success, files_changed=[], commands_run=[],
            tool_call_count=tool_calls, duration_seconds=duration,
            had_unverified=had_unverified, unsafe_actions=unsafe,
            approval_prompts=approvals, context_overflows=overflows,
        )

    def test_completion_rate_100_percent(self):
        c = self._collector()
        for i in range(5):
            c.record(self._make_metrics(f"t{i}", success=True))
        s = c.compute_summary()
        self.assertAlmostEqual(s["task_completion_rate"], 1.0)

    def test_completion_rate_partial(self):
        c = self._collector()
        c.record(self._make_metrics("t1", success=True))
        c.record(self._make_metrics("t2", success=False))
        s = c.compute_summary()
        self.assertAlmostEqual(s["task_completion_rate"], 0.5)

    def test_unverified_claim_rate(self):
        c = self._collector()
        c.record(self._make_metrics("t1", had_unverified=True))
        c.record(self._make_metrics("t2", had_unverified=False))
        s = c.compute_summary()
        self.assertAlmostEqual(s["unverified_claim_rate"], 0.5)

    def test_approval_friction(self):
        c = self._collector()
        c.record(self._make_metrics("t1", success=True, approvals=4))
        c.record(self._make_metrics("t2", success=True, approvals=2))
        s = c.compute_summary()
        self.assertAlmostEqual(s["approval_friction"], 3.0)  # 6 approvals / 2 succeeded

    def test_context_overflow_rate(self):
        c = self._collector()
        c.record(self._make_metrics("t1", overflows=1))
        c.record(self._make_metrics("t2", overflows=0))
        s = c.compute_summary()
        self.assertAlmostEqual(s["context_overflow_rate"], 0.5)

    def test_avg_tool_calls(self):
        c = self._collector()
        c.record(self._make_metrics("t1", tool_calls=4))
        c.record(self._make_metrics("t2", tool_calls=6))
        s = c.compute_summary()
        self.assertAlmostEqual(s["avg_tool_calls_per_task"], 5.0)

    def test_empty_returns_empty_dict(self):
        c = self._collector()
        s = c.compute_summary()
        self.assertEqual(s, {})

    def test_summary_has_all_required_keys(self):
        c = self._collector()
        c.record(self._make_metrics("t1"))
        s = c.compute_summary()
        for key in ["total_tasks", "task_completion_rate", "avg_tool_calls_per_task",
                    "avg_duration_seconds", "unverified_claim_rate", "total_unsafe_actions",
                    "approval_friction", "context_overflow_rate"]:
            self.assertIn(key, s, f"Missing key: {key}")

    def test_historical_data_loaded(self):
        c = self._collector()
        c.record(self._make_metrics("t1"))
        # Create new collector — should load from disk
        c2 = self._collector()
        s = c2.compute_summary()
        self.assertGreater(s.get("total_tasks", 0), 0)


# ===========================================================================
# P5.3 — Eval harness scenarios with new fixtures
# ===========================================================================

class TestEvalHarnessNewScenarios(unittest.TestCase):

    def _make_tool_call(self, name, arguments):
        return {
            "id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": arguments}
        }

    def test_go_fixture_creates_successfully(self):
        with FixtureWorkspace("go_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "main.go")))

    def test_rust_fixture_creates_successfully(self):
        with FixtureWorkspace("rust_basic") as ws:
            self.assertTrue(os.path.isfile(os.path.join(ws, "Cargo.toml")))

    def test_mock_provider_write_to_go_fixture(self):
        with FixtureWorkspace("go_basic") as ws:
            from scripts.run_eval import _make_tool_call, EvalAgent
            mock = MockProvider([
                MockProviderResponse(tool_calls=[_make_tool_call(
                    "write_file",
                    {"path": os.path.join("utils", "helper.go"),
                     "content": "package utils\nfunc Helper() {}\n"}
                )]),
                MockProviderResponse(content="Done."),
            ])
            agent = EvalAgent(ws, mock)
            result = agent.run("Add a helper function.")
            # Check agent ran without crash
            self.assertIn("status", result)

    def test_mock_provider_write_to_rust_fixture(self):
        with FixtureWorkspace("rust_basic") as ws:
            from scripts.run_eval import _make_tool_call, EvalAgent
            mock = MockProvider([
                MockProviderResponse(tool_calls=[_make_tool_call(
                    "write_file",
                    {"path": os.path.join("src", "utils.rs"),
                     "content": "pub fn helper() {}\n"}
                )]),
                MockProviderResponse(content="Done."),
            ])
            agent = EvalAgent(ws, mock)
            result = agent.run("Add a utility function.")
            self.assertIn("status", result)


# ===========================================================================
# P5.4 — Release readiness script
# ===========================================================================

class TestReleaseCheck(unittest.TestCase):

    def test_release_check_script_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "release_check",
            os.path.join(os.path.dirname(__file__), "..", "scripts", "release_check.py")
        )
        self.assertIsNotNone(spec)

    def test_release_check_script_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "release_check.py")
        self.assertTrue(os.path.isfile(path))

    def test_run_eval_script_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_eval.py")
        self.assertTrue(os.path.isfile(path))

    def test_generate_completions_script_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_completions.py")
        self.assertTrue(os.path.isfile(path))

    def test_ci_workflow_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", ".github", "workflows", "pytest.yml"
        )
        self.assertTrue(os.path.isfile(path))

    def test_ci_workflow_has_eval_step(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", ".github", "workflows", "pytest.yml"
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("run_eval.py", content)
        self.assertIn("eval-results.json", content)

    def test_ci_workflow_ignores_live_test(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", ".github", "workflows", "pytest.yml"
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("live_integration_test.py", content)

    def test_pytest_ini_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "pytest.ini")
        self.assertTrue(os.path.isfile(path))

    def test_readme_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        self.assertTrue(os.path.isfile(path))

    def test_architecture_md_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "ARCHITECTURE.md")
        self.assertTrue(os.path.isfile(path))

    def test_contributing_md_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "CONTRIBUTING.md")
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
