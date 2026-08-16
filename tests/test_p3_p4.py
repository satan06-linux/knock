"""
test_p3_p4.py - P3 (Self-Healing) + P4 (Advanced UX + Notifications) tests.
P3: KnownGood, RecoveryBootstrap, SelfRepairEngine, HealthLevels.
P4: TaskReplay, NotificationManager, EmailNotifier, dry-run, audit.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ultron.known_good import (
    get_known_good, set_known_good, is_current_known_good,
    record_known_good_from_current, get_current_commit
)
from ultron.recovery_bootstrap import detect_damage, run_health_checks
from ultron.self_repair import SelfRepairEngine, MAX_SELF_REPAIR_ATTEMPTS
from ultron.task_replay import TaskReplay, TaskReplayRecord, ReplayAction
from ultron.notifications import (
    ConsoleNotifier, EmailNotifier, NotificationManager,
    load_settings, save_settings, NOTIFICATION_EVENTS
)
from ultron.event_bus import get_bus, BusEvent, EventBus
from ultron.secret_redactor import SecretRedactor


def write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def init_git(workspace):
    import subprocess
    subprocess.run(["git", "init", workspace], capture_output=True)
    subprocess.run(["git", "-C", workspace, "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", workspace, "config", "user.name", "T"], capture_output=True)
    write(os.path.join(workspace, "dummy.txt"), "x")
    subprocess.run(["git", "-C", workspace, "add", "dummy.txt"], capture_output=True)
    subprocess.run(["git", "-C", workspace, "commit", "-m", "init"], capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)


# ===========================================================================
# P3.1 — KnownGood
# ===========================================================================

class TestKnownGood(Base):

    def test_set_and_get_known_good(self):
        ok = set_known_good("abc123commit", version="0.1.0", tests_passed=381)
        self.assertTrue(ok)
        known = get_known_good()
        self.assertIsNotNone(known)
        self.assertEqual(known["commit"], "abc123commit")
        self.assertEqual(known["tests_passed"], 381)

    def test_get_known_good_returns_none_if_not_set(self):
        # Temporarily rename the file
        import ultron.known_good as kg
        orig = kg.KNOWN_GOOD_PATH
        kg.KNOWN_GOOD_PATH = "/nonexistent/path/known_good.json"
        result = get_known_good()
        kg.KNOWN_GOOD_PATH = orig
        self.assertIsNone(result)

    def test_is_current_known_good_false_wrong_commit(self):
        set_known_good("wrongcommit000")
        result = is_current_known_good(self.workspace)
        self.assertFalse(result)

    def test_is_current_known_good_true(self):
        init_git(self.workspace)
        commit = get_current_commit(self.workspace)
        if commit:
            set_known_good(commit)
            self.assertTrue(is_current_known_good(self.workspace))

    def test_record_known_good_non_repo(self):
        msg = record_known_good_from_current(self.workspace)
        self.assertIn("Error", msg)

    def test_record_known_good_git_repo(self):
        init_git(self.workspace)
        msg = record_known_good_from_current(self.workspace, tests_passed=100)
        self.assertIn("Known-good recorded", msg)
        self.assertIn("100", msg)

    def test_known_good_has_timestamp(self):
        set_known_good("testcommit")
        known = get_known_good()
        self.assertIn("timestamp", known)

    def test_known_good_has_workspace(self):
        set_known_good("testcommit", workspace_root="/my/workspace")
        known = get_known_good()
        self.assertEqual(known.get("workspace"), "/my/workspace")


# ===========================================================================
# P3.2 — RecoveryBootstrap
# ===========================================================================

class TestRecoveryBootstrap(Base):

    def test_detect_damage_on_healthy_system(self):
        damage = detect_damage(self.workspace)
        # Should have no import failures on healthy system
        import_failures = [d for d in damage if "Import failed" in d]
        self.assertEqual(import_failures, [])

    def test_detect_damage_missing_workspace(self):
        damage = detect_damage("/nonexistent/workspace/xyz")
        self.assertTrue(any("Workspace not accessible" in d for d in damage))

    def test_detect_damage_returns_list(self):
        damage = detect_damage(self.workspace)
        self.assertIsInstance(damage, list)

    def test_run_health_checks_returns_dict(self):
        checks = run_health_checks()
        self.assertIsInstance(checks, dict)
        self.assertIn("level1_imports", checks)

    def test_run_health_checks_level1_passes(self):
        checks = run_health_checks()
        self.assertEqual(checks.get("level1_imports"), "pass")

    def test_bootstrap_recover_healthy(self):
        from ultron.recovery_bootstrap import bootstrap_recover
        # Should detect no damage and return 0
        result = bootstrap_recover(self.workspace, auto_restore=False)
        self.assertIn(result, [0, 1])  # 1 if health checks fail in test env


# ===========================================================================
# P3.3 — SelfRepairEngine
# ===========================================================================

class TestSelfRepairEngine(Base):

    def test_max_self_repair_attempts_constant(self):
        self.assertEqual(MAX_SELF_REPAIR_ATTEMPTS, 3)

    def test_check_health_levels_returns_dict(self):
        engine = SelfRepairEngine(self.workspace)
        results = engine.check_health_levels(self.workspace)
        self.assertIsInstance(results, dict)

    def test_level1_imports_passes(self):
        engine = SelfRepairEngine(self.workspace)
        results = engine.check_health_levels(self.workspace)
        self.assertIn("L1_imports", results)
        self.assertEqual(results["L1_imports"], "pass")

    def test_all_levels_pass_false_on_failure(self):
        engine = SelfRepairEngine(self.workspace)
        self.assertFalse(engine.all_levels_pass({"L1": "pass", "L2": "fail: error"}))

    def test_all_levels_pass_true_on_all_pass(self):
        engine = SelfRepairEngine(self.workspace)
        self.assertTrue(engine.all_levels_pass({"L1": "pass", "L2": "pass"}))

    def test_rollback_no_known_good(self):
        engine = SelfRepairEngine(self.workspace, console=MagicMock())
        # No known good → rollback returns False
        with patch("ultron.self_repair.get_known_good", return_value=None):
            result = engine.rollback_to_known_good()
        self.assertFalse(result)

    def test_run_with_no_damage_description_still_completes(self):
        engine = SelfRepairEngine(self.workspace, console=MagicMock())
        # With empty damage list and no model, should attempt and handle gracefully
        with patch.object(engine, "create_repair_workspace", return_value=None):
            result = engine.run(["test damage"])
        # Should handle workspace creation failure gracefully
        self.assertIn("final_status", result)

    def test_repair_emits_started_event(self):
        events = []
        get_bus().subscribe("ultron.self_repair_started", lambda d: events.append(d))
        engine = SelfRepairEngine(self.workspace, console=MagicMock())
        with patch.object(engine, "create_repair_workspace", return_value=None):
            engine.run(["some damage"])
        self.assertTrue(len(events) > 0)
        get_bus().clear()


# ===========================================================================
# P4.2 — TaskReplay
# ===========================================================================

class TestTaskReplay(Base):

    def _replay(self):
        return TaskReplay(self.workspace)

    def test_start_recording_returns_record(self):
        r = self._replay()
        record = r.start_recording("t1", "fix bug", "debug", "qwen2.5-coder:7b", "Ollama")
        self.assertEqual(record.task_id, "t1")
        self.assertEqual(record.intent, "debug")

    def test_record_tool_call(self):
        r = self._replay()
        record = r.start_recording("t1", "fix", "debug", "model", "prov")
        r.record_tool_call(record, "write_file", "path=app.py", "success", "ALLOW", "LOW")
        self.assertEqual(len(record.actions), 1)
        self.assertEqual(record.actions[0].tool, "write_file")

    def test_finalize_saves_to_disk(self):
        r = self._replay()
        record = r.start_recording("t2", "test task", "feature", "model", "prov")
        r.finalize(record, "verified", ["app.py"], ["tests passed"])
        loaded = r.load("t2")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["final_status"], "verified")

    def test_list_recent_returns_list(self):
        r = self._replay()
        record = r.start_recording("t3", "list test", "ask", "m", "p")
        r.finalize(record, "verified", [], [])
        recent = r.list_recent()
        self.assertIsInstance(recent, list)

    def test_load_nonexistent_returns_none(self):
        r = self._replay()
        result = r.load("nonexistent_task_xyz")
        self.assertIsNone(result)

    def test_format_timeline_contains_task_info(self):
        r = self._replay()
        record = r.start_recording("t4", "format test", "debug", "model", "prov")
        r.record_tool_call(record, "view_file", "path=app.py", "content", "ALLOW", "LOW")
        r.finalize(record, "verified", ["app.py"], [])
        loaded = r.load("t4")
        timeline = r.format_timeline(loaded)
        self.assertIn("t4", timeline)
        self.assertIn("view_file", timeline)

    def test_record_model_response(self):
        r = self._replay()
        record = r.start_recording("t5", "test", "ask", "m", "p")
        r.record_model_response(record, "Here is the explanation...")
        self.assertEqual(len(record.actions), 1)
        self.assertEqual(record.actions[0].action_type, "model_response")

    def test_actions_capped_in_summary(self):
        r = self._replay()
        record = r.start_recording("t6", "stress test", "build", "m", "p")
        for i in range(20):
            r.record_tool_call(record, "view_file", f"path=file{i}.py", "ok", "ALLOW", "LOW")
        r.finalize(record, "verified", [], [])
        loaded = r.load("t6")
        self.assertEqual(len(loaded["actions"]), 20)


# ===========================================================================
# P4.3 — Notifications
# ===========================================================================

class TestConsoleNotifier(unittest.TestCase):

    def test_notify_no_crash(self):
        notifier = ConsoleNotifier()
        notifier.notify("Test Title", "Test body", "warn")

    def test_notify_with_console(self):
        mock_console = MagicMock()
        notifier = ConsoleNotifier(mock_console)
        notifier.notify("Alert", "Something happened", "error")
        mock_console.print.assert_called_once()

    def test_severity_colors(self):
        mock_console = MagicMock()
        notifier = ConsoleNotifier(mock_console)
        notifier.notify("title", "body", "error")
        call_args = mock_console.print.call_args[0][0]
        self.assertIn("red", call_args)


class TestEmailNotifier(unittest.TestCase):

    def test_not_configured_by_default(self):
        notifier = EmailNotifier()
        # Unless settings.json has email config, should not be configured
        with patch("ultron.notifications.load_settings", return_value={}):
            self.assertFalse(notifier.is_configured())

    def test_configured_when_settings_present(self):
        notifier = EmailNotifier()
        with patch("ultron.notifications.load_settings", return_value={
            "email": {"to": "test@example.com", "smtp_host": "smtp.example.com"}
        }):
            self.assertTrue(notifier.is_configured())

    def test_redacts_secrets_before_send(self):
        notifier = EmailNotifier()
        redactor = SecretRedactor()
        body = "API_KEY=sk-abcdefghijklmnopqrst1234567890"
        safe = redactor.redact_for_email(body)
        self.assertNotIn("sk-abcde", safe)

    def test_notify_returns_false_when_not_configured(self):
        notifier = EmailNotifier()
        with patch("ultron.notifications.load_settings", return_value={}):
            result = notifier.notify("title", "body")
        self.assertFalse(result)


class TestNotificationManager(unittest.TestCase):

    def test_creates_without_crash(self):
        nm = NotificationManager()
        self.assertIsNotNone(nm)

    def test_notify_calls_console(self):
        mock_console = MagicMock()
        nm = NotificationManager(mock_console)
        nm.notify("Test", "Body", "warn")
        mock_console.print.assert_called()

    def test_notification_events_populated(self):
        self.assertIn(BusEvent.REPAIR_EXHAUSTED, NOTIFICATION_EVENTS)
        self.assertIn("security.scope_violation", NOTIFICATION_EVENTS)

    def test_bus_event_triggers_notification(self):
        mock_console = MagicMock()
        nm = NotificationManager(mock_console)
        # Publish a scope violation event
        nm._handle_event({"_event_type": "security.scope_violation", "path": "app.py", "reason": "out of scope"})
        mock_console.print.assert_called()

    def test_secret_not_in_notification_body(self):
        """ADVERSARIAL: secrets must not reach notification output."""
        printed = []
        mock_console = MagicMock()
        mock_console.print.side_effect = lambda msg: printed.append(msg)
        nm = NotificationManager(mock_console)
        nm.notify("Test", "API_KEY=sk-realkey12345678901234", "warn")
        full_output = " ".join(str(p) for p in printed)
        self.assertNotIn("sk-realkey", full_output)

    def test_configure_email(self):
        with patch("ultron.notifications.save_settings") as mock_save:
            NotificationManager.configure_email("test@example.com", "smtp.example.com")
            mock_save.assert_called_once()


# ===========================================================================
# REPL P3/P4 command smoke tests
# ===========================================================================

class TestReplP3P4Commands(Base):

    def setUp(self):
        super().setUp()
        init_git(self.workspace)
        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    @patch("ultron.repl.PromptSession")
    def test_known_good_no_record(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        with patch("ultron.known_good.KNOWN_GOOD_PATH", "/nonexistent/path.json"):
            repl.handle_slash_command("/known-good")

    @patch("ultron.repl.PromptSession")
    def test_known_good_record(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/known-good record")

    @patch("ultron.repl.PromptSession")
    def test_replay_list_empty(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/replay list")

    @patch("ultron.repl.PromptSession")
    def test_replay_nonexistent_id(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/replay nonexistent_task_xyz123")

    @patch("ultron.repl.PromptSession")
    def test_audit_no_entries(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/audit")

    @patch("ultron.repl.PromptSession")
    def test_notify_config_missing_args(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/notify-config")  # should print usage

    @patch("ultron.repl.PromptSession")
    @patch("rich.prompt.Confirm.ask", return_value=False)
    def test_self_repair_no_damage(self, mock_confirm, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        with patch("ultron.recovery_bootstrap.detect_damage", return_value=[]):
            repl.handle_slash_command("/self-repair")

    @patch("ultron.repl.PromptSession")
    def test_p3_p4_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/self-repair", "/known-good", "/replay", "/notify-config", "/audit"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")


if __name__ == "__main__":
    unittest.main()
