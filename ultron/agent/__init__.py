"""
ultron/agent - Agent Subpackage Facade & Exports.
"""
from ultron.models import OllamaModel
from ultron.tools import ToolManager
from ultron.context import ContextManager
from ultron.checkpoint import CheckpointManager
from ultron.onboard import ProjectMemoryManager
from ultron.repo_map import RepoMap
from ultron.providers.registry import ProviderRegistry
from ultron.task import TaskRouter, TaskStatus, TaskIntent
from ultron.tool_registry import ToolRegistry, PolicyEngine, CommandRunner, RiskLevel
from ultron.change_tracker import ChangeTracker
from ultron.eval_suite import MetricsCollector
from ultron.git_workflow import DecisionLog
from ultron.session_log import SessionLogger
from ultron.health_monitor import HealthMonitor
from ultron.model_router import ModelRouter
from ultron.project_profile import ProjectProfile
from ultron.task_replay import TaskReplay
from ultron.notifications import NotificationManager

from ultron.agent.core import UltronAgent, Agent, VALID_MODES, _REFACTOR_KEYWORDS, parse_fallback_tool_calls, EvidenceTag

__all__ = [
    "UltronAgent",
    "Agent",
    "OllamaModel",
    "ToolManager",
    "ContextManager",
    "CheckpointManager",
    "ProjectMemoryManager",
    "RepoMap",
    "ProviderRegistry",
    "TaskRouter",
    "TaskStatus",
    "TaskIntent",
    "ToolRegistry",
    "PolicyEngine",
    "CommandRunner",
    "RiskLevel",
    "ChangeTracker",
    "MetricsCollector",
    "DecisionLog",
    "SessionLogger",
    "HealthMonitor",
    "ModelRouter",
    "ProjectProfile",
    "TaskReplay",
    "NotificationManager",
    "VALID_MODES",
    "_REFACTOR_KEYWORDS",
    "EvidenceTag",
    "parse_fallback_tool_calls",
]
