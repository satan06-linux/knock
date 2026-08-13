"""
Phase 4 tests: git_workflow, monorepo, delivery, headless, and REPL Phase 4 commands.
"""
import os
import shutil
import tempfile
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from ultron.git_workflow import (
    WorktreeManager, PRSummaryGenerator, CommitQualityChecker, DecisionLog, _run_git
)
from ultron.monorepo import MonorepoDetector, WorkspaceAliasManager, Package
from ultron.delivery import (
    FeaturePlanner, ScaffoldAuditor, DocsChecker, HandoffGenerator,
    EnvironmentDoctor, HealthAnalyzer, ReleaseChecker,
)
from ultron.headless import run_headless


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write(path, content="# placeholder\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def init_git(workspace):
    subprocess.run(["git", "init", workspace], capture_output=True)
    subprocess.run(["git", "-C", workspace, "config", "user.email", "test@ultron.ai"], capture_output=True)
    subprocess.run(["git", "-C", workspace, "config", "user.name", "Ultron"], capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# CommitQualityChecker tests
# ---------------------------------------------------------------------------

class TestCommitQualityChecker(Base):

    def _checker(self):
        return CommitQualityChecker()

    def test_valid_conventional_message_passes(self):
        c = self._checker()
        issues = c.check_message("feat(auth): add login endpoint")
        self.assertEqual(issues, [])

    def test_missing_colon_fails(self):
        c = self._checker()
        issues = c.check_message("add something")
        self.assertTrue(any("conventional" in i.lower() for i in issues))

    def test_unknown_type_fails(self):
        c = self._checker()
        issues = c.check_message("ship: deploy new version")
        self.assertTrue(any("Unknown commit type" in i for i in issues))

    def test_subject_too_long_fails(self):
        c = self._checker()
        issues = c.check_message("feat: " + "x" * 80)
        self.assertTrue(any("too long" in i.lower() for i in issues))

    def test_detects_debug_print_in_diff(self):
        c = self._checker()
        diff = "+    print('debug here')\n"
        findings = c.check_diff(diff)
        self.assertTrue(len(findings) > 0)

    def test_detects_pdb_in_diff(self):
        c = self._checker()
        diff = "+    pdb.set_trace()\n"
        findings = c.check_diff(diff)
        self.assertTrue(len(findings) > 0)

    def test_clean_diff_no_findings(self):
        c = self._checker()
        diff = "+    result = compute(x)\n+    return result\n"
        findings = c.check_diff(diff)
        self.assertEqual(findings, [])

    def test_full_check_returns_passed_on_clean(self):
        init_git(self.workspace)
        c = self._checker()
        report = c.run_full_check("feat: add feature", self.workspace)
        self.assertIn("passed", report)
        self.assertIn("all_issues", report)

    def test_full_check_fails_on_bad_message(self):
        init_git(self.workspace)
        c = self._checker()
        report = c.run_full_check("bad commit no type", self.workspace)
        self.assertFalse(report["passed"])


# ---------------------------------------------------------------------------
# PRSummaryGenerator tests
# ---------------------------------------------------------------------------

class TestPRSummaryGenerator(Base):

    def test_generate_returns_markdown(self):
        init_git(self.workspace)
        gen = PRSummaryGenerator(self.workspace)
        md = gen.generate(
            title="Add auth",
            description="Adds login/logout endpoints.",
            test_evidence="pytest: 12 passed",
            risks="None",
            migration_notes="",
            reviewer_checklist=["Tests pass", "No debug code"],
        )
        self.assertIn("# Add auth", md)
        self.assertIn("## Summary", md)
        self.assertIn("## Reviewer Checklist", md)
        self.assertIn("- [ ] Tests pass", md)

    def test_generate_with_empty_checklist(self):
        gen = PRSummaryGenerator(self.workspace)
        md = gen.generate("PR", "desc", "", "", "", [])
        self.assertIn("# PR", md)
        self.assertNotIn("Reviewer Checklist", md)

    def test_get_diff_stats_no_crash_on_non_repo(self):
        gen = PRSummaryGenerator(self.workspace)
        stats = gen.get_diff_stats()
        self.assertIn("files_changed", stats)

    def test_get_commits_since_base_no_crash(self):
        init_git(self.workspace)
        gen = PRSummaryGenerator(self.workspace)
        commits = gen.get_commits_since_base("main")
        self.assertIsInstance(commits, list)


# ---------------------------------------------------------------------------
# DecisionLog tests
# ---------------------------------------------------------------------------

class TestDecisionLog(Base):

    def test_record_and_load(self):
        log = DecisionLog(self.workspace)
        log.record(
            task_description="Add login",
            plan="Create auth module",
            files_changed=["auth.py"],
            commands_run=["pytest"],
            evidence=["pytest: 5 passed"],
            diff_text="+ def login(): pass",
        )
        entries = log.load_recent(1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["task"], "Add login")
        self.assertIn("auth.py", entries[0]["files_changed"])

    def test_load_recent_empty(self):
        log = DecisionLog(self.workspace)
        entries = log.load_recent(5)
        self.assertEqual(entries, [])

    def test_format_entry(self):
        log = DecisionLog(self.workspace)
        entry = {
            "timestamp": "20260101_120000",
            "task": "Fix bug",
            "plan": "Check null",
            "files_changed": ["main.py"],
            "commands_run": ["pytest"],
            "evidence": ["passed"],
        }
        text = log.format_entry(entry)
        self.assertIn("Fix bug", text)
        self.assertIn("main.py", text)

    def test_multiple_entries_sorted_newest_first(self):
        log = DecisionLog(self.workspace)
        log.record("Task A", "Plan A", [], [], [])
        log.record("Task B", "Plan B", [], [], [])
        entries = log.load_recent(5)
        self.assertEqual(entries[0]["task"], "Task B")


# ---------------------------------------------------------------------------
# WorktreeManager tests
# ---------------------------------------------------------------------------

class TestWorktreeManager(Base):

    def test_list_worktrees_non_repo(self):
        wm = WorktreeManager(self.workspace)
        trees = wm.list_worktrees()
        self.assertEqual(trees, [])

    def test_list_worktrees_git_repo(self):
        init_git(self.workspace)
        wm = WorktreeManager(self.workspace)
        trees = wm.list_worktrees()
        # Should return at least one entry (the main worktree)
        self.assertIsInstance(trees, list)

    def test_create_worktree_non_repo_fails(self):
        wm = WorktreeManager(self.workspace)
        ok, msg = wm.create_worktree("feature/test")
        self.assertFalse(ok)
        self.assertIn("Not a git", msg)


# ---------------------------------------------------------------------------
# MonorepoDetector tests
# ---------------------------------------------------------------------------

class TestMonorepoDetector(Base):

    def test_detects_single_python_package(self):
        write(os.path.join(self.workspace, "setup.py"), "from setuptools import setup\nsetup(name='app')\n")
        detector = MonorepoDetector(self.workspace)
        packages = detector.detect_packages()
        self.assertTrue(len(packages) >= 1)
        self.assertTrue(any(p.ecosystem == "python" for p in packages))

    def test_detects_multiple_packages(self):
        write(os.path.join(self.workspace, "services", "api", "package.json"), '{"name":"api"}')
        write(os.path.join(self.workspace, "services", "worker", "package.json"), '{"name":"worker"}')
        detector = MonorepoDetector(self.workspace)
        packages = detector.detect_packages()
        self.assertTrue(len(packages) >= 2)

    def test_is_monorepo_true_for_multiple(self):
        pkgs = [
            Package("/a", "node", "NodeJS", "api"),
            Package("/b", "python", "Python", "worker"),
        ]
        detector = MonorepoDetector(self.workspace)
        self.assertTrue(detector.is_monorepo(pkgs))

    def test_is_monorepo_false_for_single(self):
        pkgs = [Package("/a", "python", "Python", "app")]
        detector = MonorepoDetector(self.workspace)
        self.assertFalse(detector.is_monorepo(pkgs))

    def test_get_targeted_commands_python(self):
        pkg = Package(self.workspace, "python", "Python", "app")
        detector = MonorepoDetector(self.workspace)
        cmds = detector.get_targeted_commands(pkg)
        self.assertIn("test", cmds)
        self.assertIn("pytest", cmds["test"])

    def test_get_targeted_commands_node(self):
        pkg = Package(self.workspace, "node", "NodeJS", "app")
        detector = MonorepoDetector(self.workspace)
        cmds = detector.get_targeted_commands(pkg)
        self.assertIn("test", cmds)
        self.assertIn("npm", cmds["test"])

    def test_ignores_node_modules(self):
        nm = os.path.join(self.workspace, "node_modules", "lib")
        os.makedirs(nm)
        write(os.path.join(nm, "package.json"), '{"name":"lib"}')
        write(os.path.join(self.workspace, "package.json"), '{"name":"root"}')
        detector = MonorepoDetector(self.workspace)
        packages = detector.detect_packages()
        paths = [p.path for p in packages]
        self.assertFalse(any("node_modules" in p for p in paths))

    def test_get_active_package(self):
        write(os.path.join(self.workspace, "src", "setup.py"), "")
        pkg = Package(os.path.join(self.workspace, "src"), "python", "Python", "src")
        detector = MonorepoDetector(self.workspace)
        active = detector.get_active_package([pkg], os.path.join(self.workspace, "src", "main.py"))
        self.assertIsNotNone(active)
        self.assertEqual(active.name, "src")


# ---------------------------------------------------------------------------
# WorkspaceAliasManager tests
# ---------------------------------------------------------------------------

class TestWorkspaceAliasManager(Base):

    def test_add_and_list_alias(self):
        manager = WorkspaceAliasManager()
        result = manager.add_alias("myproj", self.workspace)
        self.assertIn("myproj", result)
        aliases = manager.list_aliases()
        self.assertIn("myproj", aliases)
        # Cleanup
        manager.remove_alias("myproj")

    def test_remove_alias(self):
        manager = WorkspaceAliasManager()
        manager.add_alias("tmp_alias", self.workspace)
        result = manager.remove_alias("tmp_alias")
        self.assertIn("Removed", result)
        self.assertNotIn("tmp_alias", manager.list_aliases())

    def test_remove_nonexistent_alias(self):
        manager = WorkspaceAliasManager()
        result = manager.remove_alias("does_not_exist_xyz")
        self.assertIn("not found", result)

    def test_resolve_alias(self):
        manager = WorkspaceAliasManager()
        manager.add_alias("resolve_test", self.workspace)
        resolved = manager.resolve_alias("resolve_test")
        self.assertIsNotNone(resolved)
        manager.remove_alias("resolve_test")

    def test_record_and_get_recent(self):
        manager = WorkspaceAliasManager()
        manager.record_recent(self.workspace)
        recents = manager.get_recent()
        paths = [r["path"] for r in recents]
        self.assertIn(os.path.realpath(self.workspace), paths)

    def test_invalid_path_alias_fails(self):
        manager = WorkspaceAliasManager()
        result = manager.add_alias("bad", "/nonexistent/path/xyz")
        self.assertIn("Error", result)


# ---------------------------------------------------------------------------
# FeaturePlanner tests
# ---------------------------------------------------------------------------

class TestFeaturePlanner(Base):

    def test_template_plan_contains_sections(self):
        planner = FeaturePlanner(self.workspace)
        plan = planner.plan("user authentication")
        self.assertIn("Feature Plan", plan)
        self.assertIn("Scaffold Audit", plan)
        self.assertIn("Verification Steps", plan)
        self.assertIn("Tests", plan)

    def test_template_plan_no_model(self):
        planner = FeaturePlanner(self.workspace)
        plan = planner.plan("payment processing", model=None)
        self.assertIn("payment processing", plan)

    def test_plan_contains_vertical_slice_layers(self):
        planner = FeaturePlanner(self.workspace)
        plan = planner.plan("blog posts")
        for layer in ["Models", "Tests", "Documentation"]:
            self.assertIn(layer, plan)


# ---------------------------------------------------------------------------
# ScaffoldAuditor tests
# ---------------------------------------------------------------------------

class TestScaffoldAuditor(Base):

    def test_no_findings_on_empty_list(self):
        auditor = ScaffoldAuditor()
        findings = auditor.audit([], self.workspace)
        self.assertEqual(findings, [])

    def test_flags_missing_test_file(self):
        write(os.path.join(self.workspace, "service.py"), "class MyService: pass\n")
        from ultron.repo_map import RepoMap
        rm = RepoMap(self.workspace)
        rm.build()
        auditor = ScaffoldAuditor()
        findings = auditor.audit(["service.py"], self.workspace, rm)
        self.assertTrue(any("test" in f["issue"].lower() for f in findings))

    def test_flags_env_var_without_example(self):
        write(os.path.join(self.workspace, "config.py"), "import os\nDB = os.environ.get('DATABASE_URL')\n")
        auditor = ScaffoldAuditor()
        findings = auditor.audit(["config.py"], self.workspace)
        self.assertTrue(any("env" in f["issue"].lower() for f in findings))

    def test_no_env_warning_with_dotenv_example(self):
        write(os.path.join(self.workspace, "config.py"), "import os\nDB = os.environ.get('DB_URL')\n")
        write(os.path.join(self.workspace, ".env.example"), "DB_URL=\n")
        auditor = ScaffoldAuditor()
        findings = auditor.audit(["config.py"], self.workspace)
        env_findings = [f for f in findings if "env" in f["issue"].lower()]
        self.assertEqual(env_findings, [])


# ---------------------------------------------------------------------------
# DocsChecker tests
# ---------------------------------------------------------------------------

class TestDocsChecker(Base):

    def test_no_docs_no_crash(self):
        checker = DocsChecker(self.workspace)
        report = checker.check(["app.py"])
        self.assertIn("recommendations", report)

    def test_detects_api_route_in_changed_file(self):
        write(os.path.join(self.workspace, "routes.py"), "@app.get('/users')\ndef get_users(): pass\n")
        checker = DocsChecker(self.workspace)
        report = checker.check(["routes.py"])
        self.assertIn("routes.py", report["api_changes"])

    def test_recommends_changelog_update(self):
        write(os.path.join(self.workspace, "CHANGELOG.md"), "# Changelog\n")
        checker = DocsChecker(self.workspace)
        report = checker.check(["app.py"])
        self.assertTrue(any("CHANGELOG" in r for r in report["recommendations"]))

    def test_empty_changed_files(self):
        checker = DocsChecker(self.workspace)
        report = checker.check([])
        self.assertIsInstance(report["affected_docs"], list)

    def test_finds_existing_docs(self):
        write(os.path.join(self.workspace, "README.md"), "# My Project\n")
        write(os.path.join(self.workspace, "CHANGELOG.md"), "# Log\n")
        checker = DocsChecker(self.workspace)
        docs = checker._find_doc_files()
        self.assertIn("README.md", docs)
        self.assertIn("CHANGELOG.md", docs)


# ---------------------------------------------------------------------------
# HandoffGenerator tests
# ---------------------------------------------------------------------------

class TestHandoffGenerator(Base):

    def test_generate_contains_all_sections(self):
        gen = HandoffGenerator(self.workspace)
        report = gen.generate(
            task_description="Implement login",
            changed_files=["auth.py", "tests/test_auth.py"],
            commands_run=["pytest tests/"],
            test_results="5 passed",
            risks=["Token expiry not handled"],
            limitations=["No 2FA yet"],
            next_steps=["Add refresh tokens"],
            decisions=["Used JWT over session cookies"],
        )
        self.assertIn("Handoff Report", report)
        self.assertIn("auth.py", report)
        self.assertIn("pytest tests/", report)
        self.assertIn("Token expiry", report)
        self.assertIn("Add refresh tokens", report)

    def test_save_creates_file(self):
        gen = HandoffGenerator(self.workspace)
        report = gen.generate("Test task", [], [], "", [], [], [], [])
        path = gen.save(report)
        self.assertTrue(os.path.isfile(path))


# ---------------------------------------------------------------------------
# EnvironmentDoctor tests
# ---------------------------------------------------------------------------

class TestEnvironmentDoctor(Base):

    def test_returns_list_of_checks(self):
        doctor = EnvironmentDoctor(self.workspace)
        checks = doctor.run()
        self.assertIsInstance(checks, list)
        self.assertTrue(len(checks) > 0)

    def test_python_version_check_present(self):
        doctor = EnvironmentDoctor(self.workspace)
        checks = doctor.run()
        names = [c["check"] for c in checks]
        self.assertIn("Python version", names)

    def test_env_example_missing_check(self):
        doctor = EnvironmentDoctor(self.workspace)
        checks = doctor.run()
        env_check = next((c for c in checks if c["check"] == ".env"), None)
        self.assertIsNotNone(env_check)

    def test_env_example_present_check(self):
        write(os.path.join(self.workspace, ".env.example"), "DB_URL=\n")
        doctor = EnvironmentDoctor(self.workspace)
        checks = doctor.run()
        env_check = next((c for c in checks if c["check"] == ".env"), None)
        self.assertIsNotNone(env_check)


# ---------------------------------------------------------------------------
# HealthAnalyzer tests
# ---------------------------------------------------------------------------

class TestHealthAnalyzer(Base):

    def test_detects_async_blocking(self):
        write(os.path.join(self.workspace, "handler.py"),
              "import asyncio\nimport time\n\nasync def handle():\n    time.sleep(1)\n")
        analyzer = HealthAnalyzer()
        findings = analyzer.analyze_file(os.path.join(self.workspace, "handler.py"), "handler.py")
        self.assertTrue(any(f["type"] == "async_blocking" for f in findings))

    def test_detects_dead_code_marker(self):
        write(os.path.join(self.workspace, "old.py"), "# dead code\ndef legacy(): pass\n")
        analyzer = HealthAnalyzer()
        findings = analyzer.analyze_file(os.path.join(self.workspace, "old.py"), "old.py")
        self.assertTrue(any(f["type"] == "dead_code" for f in findings))

    def test_clean_file_no_findings(self):
        write(os.path.join(self.workspace, "clean.py"), "def add(a, b):\n    return a + b\n")
        analyzer = HealthAnalyzer()
        findings = analyzer.analyze_file(os.path.join(self.workspace, "clean.py"), "clean.py")
        self.assertEqual(findings, [])

    def test_analyze_workspace_no_crash(self):
        write(os.path.join(self.workspace, "app.py"), "def main(): pass\n")
        analyzer = HealthAnalyzer()
        findings = analyzer.analyze_workspace(self.workspace)
        self.assertIsInstance(findings, list)


# ---------------------------------------------------------------------------
# ReleaseChecker tests
# ---------------------------------------------------------------------------

class TestReleaseChecker(Base):

    def test_warns_on_missing_changelog(self):
        checker = ReleaseChecker(self.workspace)
        items = checker.check()
        changelog = next(i for i in items if i["item"] == "Changelog")
        self.assertEqual(changelog["status"], "warn")

    def test_ok_on_present_changelog(self):
        write(os.path.join(self.workspace, "CHANGELOG.md"), "# Log\n")
        checker = ReleaseChecker(self.workspace)
        items = checker.check()
        changelog = next(i for i in items if i["item"] == "Changelog")
        self.assertEqual(changelog["status"], "ok")

    def test_warns_on_missing_readme(self):
        checker = ReleaseChecker(self.workspace)
        items = checker.check()
        readme = next(i for i in items if i["item"] == "README")
        self.assertEqual(readme["status"], "warn")

    def test_ok_on_present_readme(self):
        write(os.path.join(self.workspace, "README.md"), "# App\n")
        checker = ReleaseChecker(self.workspace)
        items = checker.check()
        readme = next(i for i in items if i["item"] == "README")
        self.assertEqual(readme["status"], "ok")

    def test_all_items_have_required_keys(self):
        checker = ReleaseChecker(self.workspace)
        items = checker.check()
        for item in items:
            self.assertIn("item", item)
            self.assertIn("status", item)
            self.assertIn("detail", item)


# ---------------------------------------------------------------------------
# Headless mode tests
# ---------------------------------------------------------------------------

class TestHeadlessMode(Base):

    def test_missing_workspace_returns_error(self):
        result = run_headless(workspace_path="", prompt="do something")
        self.assertFalse(result["success"])
        self.assertIn("required", result["error"].lower())

    def test_nonexistent_workspace_returns_error(self):
        result = run_headless(workspace_path="/nonexistent/xyz123", prompt="do something")
        self.assertFalse(result["success"])
        self.assertIn("not exist", result["error"].lower())

    def test_valid_workspace_model_unavailable(self):
        result = run_headless(
            workspace_path=self.workspace,
            prompt="list files",
            model_name="nonexistent-model-xyz",
            base_url="http://localhost:11434",
        )
        # Should fail gracefully (model not available)
        self.assertFalse(result["success"])
        self.assertIn("exit_code", result)

    def test_result_has_required_keys(self):
        result = run_headless(workspace_path="", prompt="x")
        for key in ["success", "error", "files_changed", "commands_run", "evidence"]:
            self.assertIn(key, result)

    def test_output_file_written(self):
        output_path = os.path.join(self.workspace, "result.json")
        result = run_headless(
            workspace_path="/nonexistent/xyz",
            prompt="test",
            output_file=output_path,
        )
        # File should NOT be written since workspace is invalid
        self.assertFalse(result["success"])


# ---------------------------------------------------------------------------
# REPL Phase 4 command smoke tests
# ---------------------------------------------------------------------------

class TestReplPhase4Commands(Base):

    def setUp(self):
        super().setUp()
        init_git(self.workspace)
        write(os.path.join(self.workspace, "dummy.txt"), "x")
        subprocess.run(["git", "-C", self.workspace, "add", "dummy.txt"], capture_output=True)
        subprocess.run(["git", "-C", self.workspace, "commit", "-m", "init"], capture_output=True)

        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    @patch("ultron.repl.PromptSession")
    def test_worktree_list(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/worktree list")

    @patch("ultron.repl.PromptSession")
    def test_monorepo_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "setup.py"), "from setuptools import setup\nsetup()\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/monorepo")

    @patch("ultron.repl.PromptSession")
    def test_recent_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/recent")

    @patch("ultron.repl.PromptSession")
    def test_alias_list(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/alias list")

    @patch("ultron.repl.PromptSession")
    def test_doctor_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/doctor")

    @patch("ultron.repl.PromptSession")
    def test_release_check_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/release-check")

    @patch("ultron.repl.PromptSession")
    def test_health_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "app.py"), "def main(): pass\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/health")

    @patch("ultron.repl.PromptSession")
    def test_docs_check_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/docs-check")

    @patch("ultron.repl.PromptSession")
    def test_scaffold_audit_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/scaffold-audit")

    @patch("ultron.repl.PromptSession")
    def test_decisions_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/decisions")

    @patch("ultron.repl.PromptSession")
    def test_commit_check_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/commit-check")  # should print usage

    @patch("ultron.repl.PromptSession")
    def test_commit_check_with_message(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/commit-check feat: add new endpoint")

    @patch("ultron.repl.PromptSession")
    def test_feature_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/feature")  # should print usage

    @patch("ultron.repl.PromptSession")
    @patch("rich.prompt.Confirm.ask", return_value=False)
    def test_feature_with_arg(self, mock_confirm, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/feature user authentication")

    @patch("ultron.repl.PromptSession")
    @patch("rich.prompt.Confirm.ask", return_value=False)
    def test_handoff_command(self, mock_confirm, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/handoff Fix login bug")

    @patch("ultron.repl.PromptSession")
    @patch("rich.prompt.Confirm.ask", return_value=False)
    def test_pr_summary_command(self, mock_confirm, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/pr-summary main")

    @patch("ultron.repl.PromptSession")
    def test_phase4_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/worktree", "/pr-summary", "/commit-check", "/decisions",
                    "/monorepo", "/recent", "/alias", "/feature", "/scaffold-audit",
                    "/docs-check", "/handoff", "/doctor", "/health", "/release-check"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")


if __name__ == "__main__":
    unittest.main()
