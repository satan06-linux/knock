"""
completion.py - PromptToolkit autocompleter for Ultron REPL commands and workspace file paths.
"""
import os
from prompt_toolkit.completion import Completer, Completion


class UltronCompleter(Completer):
    def __init__(self, workspace_root: str, context_manager=None):
        self.workspace_root = workspace_root
        self.context = context_manager
        self.commands = [
            "/add", "/drop", "/files", "/run", "/diff", "/commit", "/undo",
            "/workspace", "/tree", "/refresh", "/status", "/logs", "/last-error",
            "/repeat", "/cancel", "/onboard", "/plan", "/tasks", "/test", "/lint",
            "/fix", "/clear", "/help", "/exit", "/quit",
            "/analyze", "/find-folder", "/open", "/find", "/symbol", "/references",
            "/flow", "/explain", "/impact", "/why", "/min-repro", "/init-project",
            "/mode", "/contract", "/verify", "/review", "/reproduce", "/bisect",
            "/worktree", "/pr-summary", "/commit-check", "/decisions",
            "/monorepo", "/recent", "/alias",
            "/feature", "/scaffold-audit", "/docs-check", "/handoff",
            "/doctor", "/health", "/release-check",
            "/models", "/model", "/model-info", "/provider", "/fallback",
            "/trace", "/compare", "/flaky-test", "/metrics",
            "/session-log", "/plugins", "/context-status",
            "/events", "/router", "/health-check", "/profile", "/replay",
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            word = text.split()[0] if " " in text else text
            for cmd in self.commands:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
        elif text.startswith("/add ") or text.startswith("/drop ") or text.startswith("/open "):
            prefix = text.split(maxsplit=1)[-1]
            try:
                for root, dirs, files in os.walk(self.workspace_root):
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), self.workspace_root)
                        if rel.startswith(prefix):
                            yield Completion(rel, start_position=-len(prefix))
            except Exception:
                pass
