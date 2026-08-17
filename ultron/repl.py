"""
repl.py - Backward-compatible facade for ultron.repl package.
"""
from prompt_toolkit import PromptSession
from ultron.repl.core import UltronREPL, UltronCompleter, main

_REPL_MUTATION_COMMANDS = {
    "/run":    "run_command",
    "/commit": "git_commit",
    "/test":   "run_command",
    "/lint":   "run_command",
    "/fix":    "run_command",
}

__all__ = ["UltronREPL", "UltronCompleter", "main", "PromptSession", "_REPL_MUTATION_COMMANDS"]
