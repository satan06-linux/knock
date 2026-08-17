import os
import re
import time
import subprocess
import fnmatch
import signal
from typing import List, Dict, Any, Tuple, Optional
from rich.prompt import Confirm

class ToolManager:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        # Standard folders to ignore during search/listing
        self.ignore_patterns = [
            ".git", "node_modules", "__pycache__", ".venv", "venv", 
            "env", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
            "build", "dist", "*.egg-info", "*.pyc", "*.o", "*.bin"
        ]
        self.timeout = 180
        from ultron.tool_registry import CommandRunner
        self.command_runner = CommandRunner(self.workspace_root, timeout=self.timeout)
        self.current_process = None
        self.execution_logs = []
        self.last_error = None

    def _is_ignored(self, path: str) -> bool:
        """Check if a path matches any ignore patterns."""
        parts = os.path.normpath(path).split(os.sep)
        for part in parts:
            for pattern in self.ignore_patterns:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def _resolve_safe_path(self, path: str) -> str:
        """Resolve path relative to workspace_root and prevent directory traversal/escapes."""
        from ultron.security import validate_path
        return validate_path(path, self.workspace_root)

    def list_dir(self, path: str = ".") -> str:
        """Lists files and folders inside a path."""
        try:
            target_dir = self._resolve_safe_path(path)
            if not os.path.isdir(target_dir):
                return f"Error: {path} is not a directory."
            
            output = []
            for root, dirs, files in os.walk(target_dir):
                # Filter directories in-place to respect ignore patterns
                dirs[:] = [d for d in dirs if not self._is_ignored(os.path.join(root, d))]
                
                rel_root = os.path.relpath(root, self.workspace_root)
                prefix = "" if rel_root == "." else rel_root + "/"
                
                for d in dirs:
                    output.append(f"[DIR]  {prefix}{d}")
                for f in files:
                    if not self._is_ignored(os.path.join(root, f)):
                        output.append(f"[FILE] {prefix}{f}")
                break # Only list top-level of target_dir
                
            if not output:
                return "Directory is empty."
            return "\n".join(sorted(output))
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    def view_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """Reads content of a file."""
        try:
            target_file = self._resolve_safe_path(path)
            if not os.path.isfile(target_file):
                return f"Error: File '{path}' does not exist."
            
            # Read file contents
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                
            total_lines = len(lines)
            
            # Line numbers are 1-indexed
            s_line = max(1, start_line) if start_line is not None else 1
            e_line = min(total_lines, end_line) if end_line is not None else total_lines
            
            if s_line > total_lines:
                return f"File '{path}' only has {total_lines} lines."
                
            selected_lines = lines[s_line - 1 : e_line]
            content = "".join(selected_lines)
            
            header = f"--- File: {path} (Lines {s_line}-{e_line} of {total_lines}) ---\n"
            return header + content
        except Exception as e:
            return f"Error reading file '{path}': {str(e)}"

    def write_file(self, path: str, content: str) -> str:
        """Writes whole file content."""
        try:
            target_file = self._resolve_safe_path(path)
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content.splitlines())} lines to '{path}'."
        except Exception as e:
            return f"Error writing file '{path}': {str(e)}"

    def patch_file(self, path: str, search_content: str, replacement_content: str) -> str:
        """Searches file for search_content and replaces it with replacement_content."""
        try:
            target_file = self._resolve_safe_path(path)
            if not os.path.isfile(target_file):
                return f"Error: File '{path}' does not exist."
            
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                
            # Count occurrences of search_content
            count = content.count(search_content)
            if count == 0:
                return f"Error: Search block not found in '{path}'. Please ensure matching indentation and content exactly."
            elif count > 1:
                return f"Error: Multiple ({count}) exact matches of search block found in '{path}'. Make your search block more specific."
                
            new_content = content.replace(search_content, replacement_content, 1)
            
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(new_content)
                
            return f"Successfully patched '{path}' (replaced 1 block)."
        except Exception as e:
            return f"Error patching file '{path}': {str(e)}"

    def grep_search(self, query: str, path: Optional[str] = None) -> str:
        """Searches files in the workspace for query regex."""
        try:
            search_dir = self._resolve_safe_path(path) if path else self.workspace_root
            pattern = re.compile(query, re.IGNORECASE)
            matches = []
            
            for root, _, files in os.walk(search_dir):
                if self._is_ignored(root):
                    continue
                for file in files:
                    file_path = os.path.join(root, file)
                    if self._is_ignored(file_path):
                        continue
                    
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            for idx, line in enumerate(f, 1):
                                if pattern.search(line):
                                    rel_path = os.path.relpath(file_path, self.workspace_root)
                                    matches.append(f"{rel_path}:{idx}: {line.strip()}")
                                    if len(matches) >= 100:  # Cap at 100 matches to prevent output flooding
                                        return "\n".join(matches) + "\n... (truncated after 100 matches)"
                    except Exception:
                        continue # Skip unreadable files
                        
            if not matches:
                return f"No matches found for query: '{query}'"
            return "\n".join(matches)
        except Exception as e:
            return f"Error performing search: {str(e)}"

    def terminate_current_process(self):
        """Kills the active process group cleanly."""
        if not self.current_process:
            return
        self.last_error = "Command was cancelled by the user."
        try:
            if os.name == 'nt':
                # Sending CTRL_BREAK_EVENT kills the process group on Windows
                self.current_process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                # Killing negative PGID kills the process group on Unix
                import os as posix_os
                posix_os.killpg(posix_os.getpgid(self.current_process.pid), signal.SIGKILL)
        except Exception:
            try:
                self.current_process.terminate()
            except Exception:
                pass

    def run_command(self, command: str, timeout: Optional[int] = None) -> str:
        """Executes terminal command using the single CommandRunner execution engine."""
        effective_timeout = timeout or getattr(self, "timeout", 180)
        cmd_result = self.command_runner.run(command, cwd=self.workspace_root, timeout=effective_timeout)
        self.last_error = self.command_runner.last_error

        if cmd_result.timed_out:
            return f"Error: Command timed out (exceeded {effective_timeout}s)."
        if cmd_result.cancelled:
            return f"Error: Command '{command}' was cancelled by the user."

        out_parts = []
        if cmd_result.stdout:
            out_parts.append(f"--- Stdout ---\n{cmd_result.stdout}")
        if cmd_result.stderr:
            out_parts.append(f"--- Stderr ---\n{cmd_result.stderr}")
        out_str = "\n".join(out_parts) if out_parts else "(No output)"

        log_entry = {
            "command": command,
            "exit_code": cmd_result.exit_code,
            "stdout": cmd_result.stdout,
            "stderr": cmd_result.stderr,
        }
        self.execution_logs.append(log_entry)
        if len(self.execution_logs) > 50:
            self.execution_logs.pop(0)

        if cmd_result.exit_code != 0:
            self.last_error = f"Command '{command}' exited with code {cmd_result.exit_code}.\nStdout:\n{cmd_result.stdout}\nStderr:\n{cmd_result.stderr}"

        return f"Command exited with code {cmd_result.exit_code}\n{out_str}"

    def execute_command_with_policy(
        self,
        command: str,
        *,
        require_approval: bool = True,
        context: str = "",
    ) -> dict:
        """
        Shared central command runner used by agent, REPL, and Verifier.

        All command execution in Ultron must go through this method so that
        approval, timeout, output-limiting, history logging, and cancellation
        are applied consistently and cannot be bypassed.

        Args:
            command:          The shell command string to execute.
            require_approval: If True (default), shows the command and asks
                              Confirm() before running. If the user declines,
                              returns exit_code=-1 without running anything.
            context:          Human-readable reason shown alongside the prompt
                              (e.g. "Verification: tests").

        Returns:
            dict with keys: stdout, stderr, exit_code, truncated (bool)
        """
        if require_approval:
            label = f" ({context})" if context else ""
            try:
                approved = Confirm.ask(
                    f"[bold yellow]Run command{label}:[/bold yellow] {command}"
                )
            except Exception:
                approved = True  # Non-interactive context — allow

            if not approved:
                return {
                    "stdout": "",
                    "stderr": "Declined by user.",
                    "exit_code": -1,
                    "truncated": False,
                }

        try:
            timeout = getattr(self, "timeout", 180)
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            import sys
            self.current_process = subprocess.Popen(
                command,
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
                preexec_fn=None if os.name == "nt" else os.setsid,
            )

            start_time = time.time()
            try:
                while self.current_process.poll() is None:
                    if time.time() - start_time > timeout:
                        self.terminate_current_process()
                        msg = f"Command '{command}' timed out ({timeout}s)."
                        self.last_error = msg
                        return {"stdout": "", "stderr": msg, "exit_code": -1, "truncated": False}
                    time.sleep(0.1)
                stdout, stderr = self.current_process.communicate()
            except KeyboardInterrupt:
                self.terminate_current_process()
                self.last_error = f"Command '{command}' cancelled by user."
                raise

            exit_code = self.current_process.returncode
            self.current_process = None

            def _trunc(text: str, limit: int = 3000) -> str:
                if not text or len(text) <= limit * 2:
                    return text or ""
                cut = len(text) - limit * 2
                return f"{text[:limit]}\n\n... [truncated {cut} chars] ...\n\n{text[-limit:]}"

            stdout_tr = _trunc(stdout)
            stderr_tr = _trunc(stderr)
            truncated = len(stdout or "") > 6000 or len(stderr or "") > 6000

            # History log
            log_entry = {"command": command, "exit_code": exit_code,
                         "stdout": stdout, "stderr": stderr}
            self.execution_logs.append(log_entry)
            if len(self.execution_logs) > 50:
                self.execution_logs.pop(0)

            if exit_code != 0:
                self.last_error = (
                    f"Command '{command}' exited with code {exit_code}.\n"
                    f"Stdout:\n{stdout}\nStderr:\n{stderr}"
                )

            return {
                "stdout": stdout_tr,
                "stderr": stderr_tr,
                "exit_code": exit_code,
                "truncated": truncated,
            }

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self.current_process = None
            return {"stdout": "", "stderr": str(exc), "exit_code": -1, "truncated": False}

    def git_status(self) -> str:
        """Returns the output of git status."""
        try:
            # Check if it is a git repo
            git_check = subprocess.run(
                "git rev-parse --is-inside-work-tree",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if git_check.returncode != 0:
                return "Not a git repository (or git is not installed)."
                
            result = subprocess.run(
                "git status --porcelain",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode != 0:
                return f"Error running git status: {result.stderr}"
            
            output = result.stdout.strip()
            if not output:
                return "Git repository is clean. No modifications."
            return output
        except Exception as e:
            return f"Error checking git status: {str(e)}"

    def git_commit(self, message: str, files: Optional[List[str]] = None) -> str:
        """Stages specified files (or tracked edits if None) and creates a git commit."""
        try:
            # Check if git repo
            git_check = subprocess.run(
                "git rev-parse --is-inside-work-tree",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if git_check.returncode != 0:
                return "Error: Not a git repository. Please initialize git first."
            
            # Stage files safely using pathspec limits
            if files:
                # Stage strictly the specified files
                cmd = ["git", "add", "--"] + files
                add_res = subprocess.run(
                    cmd,
                    cwd=self.workspace_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if add_res.returncode != 0:
                    return f"Error staging changes: {add_res.stderr}"
            else:
                # Stage all modified tracked files safely
                add_res = subprocess.run(
                    "git add -u -- .",
                    shell=True,
                    cwd=self.workspace_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if add_res.returncode != 0:
                    return f"Error staging changes: {add_res.stderr}"
                
            # Commit changes
            commit_res = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if commit_res.returncode != 0:
                if "nothing to commit" in commit_res.stdout or "nothing to commit" in commit_res.stderr:
                    return "Nothing to commit (no staged changes)."
                return f"Error creating commit: {commit_res.stderr or commit_res.stdout}"
                
            return f"Commit created successfully:\n{commit_res.stdout.strip()}"
        except Exception as e:
            return f"Error committing changes: {str(e)}"

    def is_file_dirty(self, path: str) -> bool:
        """Check if a specific file has pre-existing uncommitted changes."""
        try:
            # Run git status on a specific path limit
            res = subprocess.run(
                ["git", "status", "--porcelain", "--", path],
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return bool(res.stdout.strip())
        except Exception:
            return False

    def get_empty_tree_hash(self) -> str:
        """Dynamically resolve the Git empty tree hash (SHA-1 or SHA-256) by piping empty bytes to Git."""
        try:
            res = subprocess.run(
                ["git", "hash-object", "-t", "tree", "--stdin"],
                cwd=self.workspace_root,
                input=b"", # exact empty bytes
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )
            if res.returncode == 0:
                return res.stdout.decode("utf-8").strip()
        except Exception:
            pass
        return "4b825dc642cb6eb9a0ea0e47b6e3a393d6b2b2e9" # default SHA-1 empty tree fallback

    def get_git_info(self) -> Tuple[Optional[str], int]:
        """Returns (branch_name, modified_files_count)."""
        try:
            # Check if git repo
            git_check = subprocess.run(
                "git rev-parse --is-inside-work-tree",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if git_check.returncode != 0:
                return None, 0
                
            branch_res = subprocess.run(
                "git branch --show-current",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            branch = branch_res.stdout.strip() or "HEAD (detached)"
            
            status_res = subprocess.run(
                "git status --porcelain",
                shell=True,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            modified_count = len([line for line in status_res.stdout.splitlines() if line.strip()])
            return branch, modified_count
        except Exception:
            return None, 0

    def detect_project_type(self) -> str:
        """Returns detected project type based on config files."""
        signatures = {
            "package.json": "NodeJS / TypeScript",
            "Cargo.toml": "Rust",
            "go.mod": "Go",
            "requirements.txt": "Python",
            "pyproject.toml": "Python",
            "setup.py": "Python",
            "Makefile": "C / C++ (Makefile)",
            "CMakeLists.txt": "C / C++ (CMake)",
            "pom.xml": "Java (Maven)",
            "build.gradle": "Java (Gradle)"
        }
        detected = []
        seen = set()
        for file, desc in signatures.items():
            if desc not in seen and os.path.isfile(os.path.join(self.workspace_root, file)):
                detected.append(desc)
                seen.add(desc)
        if not detected:
            return "Generic / Unrecognized"
        return " & ".join(detected)
