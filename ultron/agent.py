"""
agent.py - Backward-compatible facade for ultron.agent package.
"""
from ultron.agent.core import UltronAgent, Agent
from ultron.agent.conversation import VALID_MODES, _REFACTOR_KEYWORDS, parse_fallback_tool_calls
from ultron.agent.task_context import EvidenceTag, TaskContext

__all__ = [
    "UltronAgent",
    "Agent",
    "VALID_MODES",
    "_REFACTOR_KEYWORDS",
    "EvidenceTag",
    "TaskContext",
    "parse_fallback_tool_calls",
]
