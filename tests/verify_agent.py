import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from rich.console import Console

from ultron.agent import UltronAgent
from ultron.security import validate_path
from ultron.repl import UltronREPL

class TestUltronMockedSuite(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        
        # Setup git repo with double quotes for cross-platform Windows compatibility
        os.system(f"git init \"{self.workspace}\"")
        os.system(f"git -C \"{self.workspace}\" config user.email \"test@ultron.ai\"")
        os.system(f"git -C \"{self.workspace}\" config user.name \"Ultron Test\"")
        
        # Commit a dummy file so we have HEAD
        self.dummy_file = os.path.join(self.workspace, "dummy.txt")
        with open(self.dummy_file, "w") as f:
            f.write("initial content")
        os.system(f"git -C \"{self.workspace}\" add dummy.txt")
        os.system(f"git -C \"{self.workspace}\" commit -m \"Initial commit\"")
        
        self.agent = UltronAgent(
            workspace_root=self.workspace,
            model_name="qwen2.5-coder:7b",
            auto_approve=True,  # Bypass prompts inside agent code (we mock prompts manually)
            auto_commit=True
        )
        self.console = Console(width=80)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_workspace_containment_escapes(self):
        # 1. Traversal escape
        with self.assertRaises(PermissionError):
            validate_path("../outside.txt", self.workspace)
            
        # 2. Windows drive escape (mock path drive mismatch)
        with patch('os.path.commonpath') as mock_common:
            mock_common.side_effect = ValueError("Different drives")
            with self.assertRaises(PermissionError):
                validate_path("C:/some/file.txt", self.workspace)

        # 3. Deny list escape (.env)
        with self.assertRaises(PermissionError):
            validate_path(".env", self.workspace)
        with self.assertRaises(PermissionError):
            validate_path("config/credentials.json", self.workspace)

    @patch('rich.prompt.Confirm.ask')
    def test_dirty_file_pre_existing_changes_blocking(self, mock_confirm):
        # User has uncommitted changes in dummy.txt
        with open(self.dummy_file, "w") as f:
            f.write("user modified content")
            
        # Ultron now wants to edit dummy.txt
        # Mock user confirmation prompt: first prompt (dirty check approve), second prompt (apply diff approve)
        mock_confirm.side_effect = [True, True]
        
        # We mock Ollama response to edit dummy.txt using a real generator helper
        def mock_chat_generator(messages, stream=True):
            yield {
                "type": "tool_calls",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": {"path": "dummy.txt", "content": "ultron modified content"}
                    }
                }]
            }
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": {"path": "dummy.txt", "content": "ultron modified content"}
                    }
                }]
            }
            
        mock_chat = MagicMock()
        mock_chat.chat.side_effect = mock_chat_generator
        
        # Mock models connector
        with patch.object(self.agent, 'model', mock_chat):
            self.agent.run("Modify dummy.txt please.")
            
        # Verify file contains Ultron's modification
        with open(self.dummy_file, "r") as f:
            content = f.read()
        self.assertEqual(content, "ultron modified content")
        
        # Verify auto-commit was disabled because it was dirty at task start
        # The git status should still list dummy.txt as modified (not committed)
        status = self.agent.tools.git_status()
        self.assertIn("M dummy.txt", status)

    @patch('rich.prompt.Confirm.ask')
    def test_conflict_aware_undo_prevention(self, mock_confirm):
        # 1. Ultron writes a file
        self.agent.checkpoint.start_task()
        self.agent.tools.write_file("new_file.txt", "original ultron content")
        self.agent.checkpoint.record_before_edit("new_file.txt")
        self.agent.checkpoint.record_after_edit("new_file.txt")
        self.agent.checkpoint.save_task_checkpoint()
        
        # 2. User modifies new_file.txt afterward
        new_file_path = os.path.join(self.workspace, "new_file.txt")
        with open(new_file_path, "w") as f:
            f.write("user edited afterward")
            
        # 3. Running undo should detect conflict and ask for confirmation
        # Mock user chooses 'False' to decline overwriting their changes
        mock_confirm.return_value = False
        
        success = self.agent.checkpoint.undo(self.console)
        self.assertFalse(success, "Undo should fail because user declined force restore!")
        
        # Content remains user edited
        with open(new_file_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "user edited afterward")

    def test_malformed_tool_call_never_executes(self):
        # Mock Ollama outputting bad args type (e.g. string instead of dict)
        def mock_chat_generator(messages, stream=True):
            yield {
                "type": "tool_calls",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": "bad_arguments_not_dict"
                    }
                }]
            }
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": "bad_arguments_not_dict"
                    }
                }]
            }
            
        mock_chat = MagicMock()
        mock_chat.chat.side_effect = mock_chat_generator
        
        with patch.object(self.agent, 'model', mock_chat):
            self.agent.run("Write bad file.")
            
        # Verify no file is written since validation failed
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "bad_arguments_not_dict")))

    @patch('ultron.repl.PromptSession')
    def test_repl_slash_commands_routing(self, mock_prompt_session):
        # We mock PromptSession inside repl to prevent Win32 console screen buffer errors in headless test runners
        repl = UltronREPL(self.agent)
        
        # Verify command registrations directly in Completer
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.agent.workspace_root, self.agent.context)
        self.assertIn("/help", completer.commands)
        self.assertIn("/diff", completer.commands)
        self.assertIn("/commit", completer.commands)
        self.assertIn("/undo", completer.commands)
        self.assertIn("/workspace", completer.commands)
        self.assertIn("/onboard", completer.commands)
        self.assertIn("/cancel", completer.commands)
        
        # Smoke test help printing
        try:
            repl.print_help()
        except Exception as e:
            self.fail(f"print_help raised exception: {e}")

    @patch('ultron.repl.PromptSession')
    def test_workspace_command_disabled_switching(self, mock_prompt_session):
        repl = UltronREPL(self.agent)
        with patch.object(repl.console, 'print') as mock_print:
            repl.handle_slash_command("/workspace other/path")
            # Verify it printed a path switching disabled warning
            printed = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("disabled" in str(p) for p in printed))

    @patch('ultron.repl.PromptSession')
    @patch('rich.prompt.Confirm.ask')
    def test_repeat_requires_confirmation_on_mutation(self, mock_confirm, mock_prompt_session):
        repl = UltronREPL(self.agent)
        repl.last_user_prompt = "Write some code"
        repl.last_task_mutated = True
        
        # User declines repeat
        mock_confirm.return_value = False
        repl.handle_slash_command("/repeat")
        # Ensure agent.run was not called
        with patch.object(self.agent, 'run') as mock_run:
            repl.handle_slash_command("/repeat")
            mock_run.assert_not_called()

    @patch('ultron.repl.PromptSession')
    def test_onboard_works_when_ollama_offline(self, mock_prompt_session):
        repl = UltronREPL(self.agent)
        
        # Write dummy package.json manifest
        pkg_json = os.path.join(self.workspace, "package.json")
        with open(pkg_json, "w") as f:
            f.write('{"name": "test-project"}')
            
        # Mock agent model availability to False (Ollama offline)
        with patch.object(self.agent.model, 'is_available', return_value=False):
            repl.handle_slash_command("/onboard")
            
        # Verify project memory cache was still created globally and detected NodeJS
        mem = repl.memory_manager.load_memory()
        self.assertEqual(mem["project_type"], "NodeJS / TypeScript")
        self.assertEqual(mem["commands"]["test"]["cmd"], "npm test")
        self.assertEqual(mem["commands"]["test"]["status"], "unverified")

    @patch('ultron.repl.PromptSession')
    @patch('rich.prompt.Confirm.ask', return_value=True)
    def test_command_status_unverified_to_verified_on_success(self, mock_confirm, mock_prompt_session):
        repl = UltronREPL(self.agent)
        
        # Setup unverified test command in memory
        mem = repl.memory_manager.load_memory()
        mem["commands"]["test"] = {"cmd": "echo test_success", "status": "unverified"}
        repl.memory_manager.save_memory(mem)
        
        # Run test command
        repl.handle_slash_command("/test")
        
        # Status should now be promoted to verified
        updated_mem = repl.memory_manager.load_memory()
        self.assertEqual(updated_mem["commands"]["test"]["status"], "verified")

    def test_project_runtime_data_git_clean(self):
        # Memory is saved globally outside the workspace in ~/.ultron
        # Verify git status is completely clean of any .ultron settings files
        status = self.agent.tools.git_status()
        self.assertNotIn(".ultron", status)

    def test_cancel_long_running_process(self):
        # We start a command that runs, then we cancel it
        import threading
        import time
        
        # Let's run a sleep command in a background thread or verify Popen terminates group
        # Windows uses ping or timeout, Unix uses sleep
        cmd = "ping 127.0.0.1 -n 10" if os.name == 'nt' else "sleep 10"
        
        def run_thread():
            self.agent.tools.run_command(cmd)
            
        t = threading.Thread(target=run_thread)
        t.start()
        
        # Give it a brief moment to start
        time.sleep(0.5)
        self.assertIsNotNone(self.agent.tools.current_process)
        
        # Cancel command
        self.agent.tools.terminate_current_process()
        t.join()
        
        # Process should be terminated and last_error populated
        self.assertIsNone(self.agent.tools.current_process)
        self.assertIn("cancelled", self.agent.tools.last_error.lower())

if __name__ == "__main__":
    unittest.main()
