"""
ultron/repl - REPL Subpackage Facade & Exports.
"""
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from ultron.agent import UltronAgent
from ultron.onboard import ProjectMemoryManager
from ultron.repl.core import UltronREPL, UltronCompleter, main

_REPL_MUTATION_COMMANDS = {
    "/run":    "run_command",
    "/commit": "git_commit",
    "/test":   "run_command",
    "/lint":   "run_command",
    "/fix":    "run_command",
}

__all__ = [
    "UltronREPL",
    "UltronCompleter",
    "main",
    "PromptSession",
    "FileHistory",
    "UltronAgent",
    "ProjectMemoryManager",
    "_REPL_MUTATION_COMMANDS",
]
