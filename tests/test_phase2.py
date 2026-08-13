"""
Phase 2 tests: RepoMap, ImpactAnalyzer, FailureInvestigator,
ConventionFinder, analyzer helpers, and REPL Phase 2 commands.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ultron.repo_map import RepoMap
from ultron.analyzer import (
    ImpactAnalyzer, FailureInvestigator, ConventionFinder,
    find_folders, load_project_instructions,
    create_ultron_md_template, create_ultron_toml_template,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class Phase2TestBase(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# RepoMap tests
# ---------------------------------------------------------------------------

class TestRepoMap(Phase2TestBase):

    def test_indexes_python_symbols(self):
        write(os.path.join(self.workspace, "app.py"), """
class UserService:
    def get_user(self, uid):
        pass

def create_app():
    pass
""")
        rm = RepoMap(self.workspace)
        rm.build()

        syms = rm.get_file_symbols("app.py")
        names = [s["name"] for s in syms]
        self.assertIn("UserService", names)
        self.assertIn("get_user", names)
        self.assertIn("create_app", names)

    def test_indexes_imports(self):
        write(os.path.join(self.workspace, "main.py"), """
import os
from collections import defaultdict
from app import UserService
""")
        rm = RepoMap(self.workspace)
        rm.build()
        imports = rm.get_imports("main.py")
        self.assertTrue(any("app" in i or "collections" in i or "os" in i for i in imports))

    def test_find_symbol(self):
        write(os.path.join(self.workspace, "utils.py"), "def helper_func():\n    pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        results = rm.find_symbol("helper")
        self.assertTrue(any(r["name"] == "helper_func" for r in results))

    def test_find_references(self):
        write(os.path.join(self.workspace, "a.py"), "def target():\n    pass\n")
        write(os.path.join(self.workspace, "b.py"), "from a import target\nresult = target()\n")
        rm = RepoMap(self.workspace)
        rm.build()
        refs = rm.find_references("target")
        files = [r["file"] for r in refs]
        self.assertIn("b.py", files)

    def test_find_text(self):
        write(os.path.join(self.workspace, "config.py"), "DATABASE_URL = 'sqlite:///db.sqlite3'\n")
        rm = RepoMap(self.workspace)
        rm.build()
        results = rm.find_text("DATABASE_URL")
        self.assertTrue(any(r["file"] == "config.py" for r in results))

    def test_test_file_detection(self):
        write(os.path.join(self.workspace, "test_utils.py"), "def test_something(): pass\n")
        write(os.path.join(self.workspace, "utils.py"), "def something(): pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        test_files = rm.get_test_files()
        self.assertIn("test_utils.py", test_files)
        self.assertNotIn("utils.py", test_files)

    def test_related_tests(self):
        write(os.path.join(self.workspace, "service.py"), "def run(): pass\n")
        write(os.path.join(self.workspace, "test_service.py"), "def test_run(): pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        related = rm.find_related_tests("service.py")
        self.assertIn("test_service.py", related)

    def test_callers_of(self):
        write(os.path.join(self.workspace, "core.py"), "def compute(): pass\n")
        write(os.path.join(self.workspace, "runner.py"), "from core import compute\ncompute()\n")
        rm = RepoMap(self.workspace)
        rm.build()
        callers = rm.callers_of("compute")
        caller_files = [c["file"] for c in callers]
        self.assertIn("runner.py", caller_files)

    def test_who_imports(self):
        write(os.path.join(self.workspace, "models.py"), "class User: pass\n")
        write(os.path.join(self.workspace, "views.py"), "from models import User\n")
        rm = RepoMap(self.workspace)
        rm.build()
        importers = rm.who_imports("models.py")
        self.assertIn("views.py", importers)

    def test_incremental_refresh(self):
        write(os.path.join(self.workspace, "a.py"), "def alpha(): pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        self.assertEqual(len(rm.index), 1)

        # Add a new file
        write(os.path.join(self.workspace, "b.py"), "def beta(): pass\n")
        rm.refresh()
        self.assertEqual(len(rm.index), 2)

    def test_summary(self):
        write(os.path.join(self.workspace, "a.py"), "def a(): pass\n")
        write(os.path.join(self.workspace, "test_a.py"), "def test_a(): pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        s = rm.get_summary()
        self.assertEqual(s["total_files"], 2)
        self.assertEqual(s["test_files"], 1)
        self.assertIn("python", s["by_language"])

    def test_ignores_pycache(self):
        cache_dir = os.path.join(self.workspace, "__pycache__")
        os.makedirs(cache_dir)
        write(os.path.join(cache_dir, "cached.py"), "x = 1\n")
        write(os.path.join(self.workspace, "real.py"), "y = 2\n")
        rm = RepoMap(self.workspace)
        rm.build()
        self.assertNotIn("__pycache__/cached.py", rm.index)
        self.assertIn("real.py", rm.index)


# ---------------------------------------------------------------------------
# ImpactAnalyzer tests
# ---------------------------------------------------------------------------

class TestImpactAnalyzer(Phase2TestBase):

    def _build_rm(self):
        rm = RepoMap(self.workspace)
        rm.build()
        return rm

    def test_analyze_file_basic(self):
        write(os.path.join(self.workspace, "auth.py"), "def login(user): pass\ndef logout(): pass\n")
        write(os.path.join(self.workspace, "views.py"), "from auth import login\nlogin('admin')\n")
        rm = self._build_rm()
        analyzer = ImpactAnalyzer(rm)
        report = analyzer.analyze_file("auth.py")
        self.assertIn("target", report)
        self.assertIn("symbols_defined", report)
        self.assertIn("risk", report)

    def test_high_risk_detection(self):
        # Create a file imported by many others
        write(os.path.join(self.workspace, "base.py"), "def core_func(): pass\n")
        for i in range(7):
            write(os.path.join(self.workspace, f"module_{i}.py"), f"from base import core_func\ncore_func()\n")
        rm = self._build_rm()
        analyzer = ImpactAnalyzer(rm)
        report = analyzer.analyze_file("base.py")
        self.assertIn(report["risk"], ["medium", "high"])

    def test_analyze_symbol(self):
        write(os.path.join(self.workspace, "svc.py"), "def process_order(order): pass\n")
        write(os.path.join(self.workspace, "api.py"), "from svc import process_order\nprocess_order({})\n")
        rm = self._build_rm()
        analyzer = ImpactAnalyzer(rm)
        report = analyzer.analyze_symbol("process_order")
        self.assertEqual(report["symbol"], "process_order")
        self.assertTrue(len(report["definitions"]) > 0)

    def test_no_test_warning(self):
        write(os.path.join(self.workspace, "utils.py"), "def helper(): pass\n")
        rm = self._build_rm()
        analyzer = ImpactAnalyzer(rm)
        report = analyzer.analyze_file("utils.py")
        self.assertTrue(any("test" in w.lower() for w in report["warnings"]))


# ---------------------------------------------------------------------------
# FailureInvestigator tests
# ---------------------------------------------------------------------------

class TestFailureInvestigator(Phase2TestBase):

    def _investigator(self):
        rm = RepoMap(self.workspace)
        return FailureInvestigator(self.workspace, rm)

    def test_extracts_python_traceback(self):
        log = """
Traceback (most recent call last):
  File "app.py", line 42, in run
    result = compute(x)
TypeError: unsupported operand type(s) for +: 'int' and 'str'
"""
        inv = self._investigator()
        errors = inv.extract_errors(log)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("TypeError" in e for e in errors))

    def test_classifies_test_failure(self):
        log = "FAILED tests/test_app.py::test_add - AssertionError: assert 1 == 2"
        inv = self._investigator()
        report = inv.investigate(log)
        self.assertEqual(report["error_type"], "test_failure")

    def test_classifies_import_error(self):
        log = "ModuleNotFoundError: No module named 'missing_lib'"
        inv = self._investigator()
        report = inv.investigate(log)
        self.assertEqual(report["error_type"], "import_error")

    def test_classifies_syntax_error(self):
        log = "SyntaxError: invalid syntax (app.py, line 10)"
        inv = self._investigator()
        report = inv.investigate(log)
        self.assertEqual(report["error_type"], "syntax_error")

    def test_repair_suggestions_present(self):
        log = "TypeError: 'NoneType' has no attribute 'name'"
        inv = self._investigator()
        report = inv.investigate(log)
        self.assertTrue(len(report["repair_suggestions"]) > 0)

    def test_min_repro_template_no_llm(self):
        log = "AssertionError: assert 1 == 2"
        inv = self._investigator()
        script = inv.generate_min_repro(log, model_callable=None)
        self.assertIn("def test_reproduction", script)
        self.assertIn("Minimal reproduction", script)

    def test_source_location_extraction(self):
        write(os.path.join(self.workspace, "app.py"), "x = 1\n")
        rm = RepoMap(self.workspace)
        rm.build()
        inv = FailureInvestigator(self.workspace, rm)
        log = 'File "app.py", line 5, in run'
        locs = inv.find_source_locations(log)
        self.assertTrue(any(l["file"] == "app.py" for l in locs))


# ---------------------------------------------------------------------------
# ConventionFinder tests
# ---------------------------------------------------------------------------

class TestConventionFinder(Phase2TestBase):

    def test_finds_similar_files(self):
        write(os.path.join(self.workspace, "user_service.py"), "class UserService: pass\n")
        write(os.path.join(self.workspace, "order_service.py"), "class OrderService: pass\n")
        write(os.path.join(self.workspace, "config.py"), "DEBUG = True\n")
        rm = RepoMap(self.workspace)
        rm.build()
        cf = ConventionFinder(self.workspace, rm)
        similar = cf.find_similar_files("user service")
        self.assertIn("user_service.py", similar)

    def test_reads_conventions(self):
        content = "class BaseModel:\n    pass\n"
        write(os.path.join(self.workspace, "base.py"), content)
        rm = RepoMap(self.workspace)
        rm.build()
        cf = ConventionFinder(self.workspace, rm)
        snippet = cf.read_conventions("base.py")
        self.assertIn("BaseModel", snippet)

    def test_get_project_conventions(self):
        write(os.path.join(self.workspace, "auth_service.py"), "def authenticate(): pass\n")
        rm = RepoMap(self.workspace)
        rm.build()
        cf = ConventionFinder(self.workspace, rm)
        result = cf.get_project_conventions("authentication service")
        self.assertIn("similar_files", result)
        self.assertIn("snippets", result)


# ---------------------------------------------------------------------------
# Analyzer helpers tests
# ---------------------------------------------------------------------------

class TestAnalyzerHelpers(Phase2TestBase):

    def test_find_folders(self):
        sub = os.path.join(self.workspace, "src", "services")
        os.makedirs(sub)
        results = find_folders("services", self.workspace)
        self.assertTrue(any("services" in r for r in results))

    def test_find_folders_skips_git(self):
        git_dir = os.path.join(self.workspace, ".git", "hooks")
        os.makedirs(git_dir)
        results = find_folders("hooks", self.workspace)
        self.assertEqual(len(results), 0)

    def test_load_project_instructions_ultron_md(self):
        write(os.path.join(self.workspace, "ULTRON.md"), "# My Project\nUse snake_case.\n")
        content = load_project_instructions(self.workspace)
        self.assertIn("PROJECT INSTRUCTIONS", content)
        self.assertIn("snake_case", content)

    def test_load_project_instructions_toml(self):
        write(os.path.join(self.workspace, ".ultron.toml"), '[project]\nname = "test"\n')
        content = load_project_instructions(self.workspace)
        self.assertIn("PROJECT SETTINGS", content)

    def test_load_project_instructions_empty(self):
        content = load_project_instructions(self.workspace)
        self.assertEqual(content, "")

    def test_create_ultron_md_template(self):
        result = create_ultron_md_template(self.workspace)
        self.assertIn("Created", result)
        self.assertTrue(os.path.isfile(os.path.join(self.workspace, "ULTRON.md")))

    def test_create_ultron_md_already_exists(self):
        write(os.path.join(self.workspace, "ULTRON.md"), "existing")
        result = create_ultron_md_template(self.workspace)
        self.assertIn("already exists", result)

    def test_create_ultron_toml_template(self):
        result = create_ultron_toml_template(self.workspace)
        self.assertIn("Created", result)
        self.assertTrue(os.path.isfile(os.path.join(self.workspace, ".ultron.toml")))


# ---------------------------------------------------------------------------
# REPL Phase 2 command routing smoke tests
# ---------------------------------------------------------------------------

class TestReplPhase2Commands(Phase2TestBase):

    def setUp(self):
        super().setUp()
        os.system(f'git init "{self.workspace}"')
        os.system(f'git -C "{self.workspace}" config user.email "test@ultron.ai"')
        os.system(f'git -C "{self.workspace}" config user.name "Ultron"')

        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    @patch("ultron.repl.PromptSession")
    def test_analyze_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "app.py"), "def main(): pass\n")
        repl = UltronREPL(self.agent)
        # Should not raise
        repl.handle_slash_command("/analyze")

    @patch("ultron.repl.PromptSession")
    def test_find_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "app.py"), "SECRET_KEY = 'abc'\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/find SECRET_KEY")

    @patch("ultron.repl.PromptSession")
    def test_symbol_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "svc.py"), "def my_service(): pass\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/symbol my_service")

    @patch("ultron.repl.PromptSession")
    def test_references_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "a.py"), "def target(): pass\n")
        write(os.path.join(self.workspace, "b.py"), "from a import target\ntarget()\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/references target")

    @patch("ultron.repl.PromptSession")
    def test_flow_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "core.py"), "def compute(): pass\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/flow compute")

    @patch("ultron.repl.PromptSession")
    def test_impact_file_command(self, mock_ps):
        from ultron.repl import UltronREPL
        write(os.path.join(self.workspace, "models.py"), "class User:\n    pass\n")
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/analyze")
        repl.handle_slash_command("/impact models.py")

    @patch("ultron.repl.PromptSession")
    def test_why_no_error(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        # No error logged, should print a warning gracefully
        repl.handle_slash_command("/why")

    @patch("ultron.repl.PromptSession")
    def test_why_with_log(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/why AssertionError: assert 1 == 2")

    @patch("ultron.repl.PromptSession")
    def test_init_project_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/init-project")
        self.assertTrue(os.path.isfile(os.path.join(self.workspace, "ULTRON.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.workspace, ".ultron.toml")))

    @patch("ultron.repl.PromptSession")
    def test_find_folder_no_match(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        with patch("rich.prompt.Confirm.ask", return_value=False):
            repl.handle_slash_command("/find-folder nonexistentxyz123")

    @patch("ultron.repl.PromptSession")
    def test_phase2_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/analyze", "/find", "/symbol", "/references", "/flow",
                    "/explain", "/impact", "/why", "/min-repro", "/init-project",
                    "/find-folder", "/open"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")


if __name__ == "__main__":
    unittest.main()
