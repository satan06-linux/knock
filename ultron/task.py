"""
task.py - Workstream A: Structured Engineering Reasoning.
Task model, TaskRouter, status lifecycle, bounded retry policy.
"""
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskIntent(str, Enum):
    ASK        = "ask"
    ANALYZE    = "analyze"
    DEBUG      = "debug"
    FEATURE    = "feature"
    REFACTOR   = "refactor"
    TEST       = "test"
    REVIEW     = "review"
    SETUP      = "setup"
    UNKNOWN    = "unknown"


class TaskStatus(str, Enum):
    PLANNED    = "planned"
    INSPECTING = "inspecting"
    EDITING    = "editing"
    TESTING    = "testing"
    BLOCKED    = "blocked"
    VERIFIED   = "verified"
    CANCELLED  = "cancelled"


class EvidenceKind(str, Enum):
    OBSERVED     = "Observed from code"
    VERIFIED     = "Verified by command"
    INFERRED     = "Inferred"
    NOT_VERIFIED = "Not verified"


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------

@dataclass
class TaskEvidence:
    kind: EvidenceKind
    description: str
    source: str = ""        # file path or command


@dataclass
class Task:
    id: str                             = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt: str                         = ""
    intent: TaskIntent                  = TaskIntent.UNKNOWN
    mode: str                           = "build"
    status: TaskStatus                  = TaskStatus.PLANNED
    plan: str                           = ""
    expected_files: List[str]           = field(default_factory=list)
    actual_files: List[str]             = field(default_factory=list)
    risks: List[str]                    = field(default_factory=list)
    verification_steps: List[str]       = field(default_factory=list)
    evidence: List[TaskEvidence]        = field(default_factory=list)
    checkpoint_id: Optional[str]        = None

    # Budgets
    max_tool_calls: int                 = 12
    max_repair_attempts: int            = 3
    time_budget_seconds: float          = 300.0  # 5 min default

    # Runtime tracking
    tool_call_count: int                = 0
    repair_attempt_count: int           = 0
    start_time: float                   = field(default_factory=time.time)

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def is_over_budget(self) -> bool:
        return (
            self.tool_call_count >= self.max_tool_calls or
            self.elapsed() > self.time_budget_seconds
        )

    def budget_status(self) -> str:
        elapsed = self.elapsed()
        return (
            f"Tools: {self.tool_call_count}/{self.max_tool_calls} | "
            f"Time: {elapsed:.0f}s/{self.time_budget_seconds:.0f}s | "
            f"Repairs: {self.repair_attempt_count}/{self.max_repair_attempts}"
        )

    def add_evidence(self, kind: EvidenceKind, description: str, source: str = ""):
        self.evidence.append(TaskEvidence(kind=kind, description=description, source=source))

    def has_unverified(self) -> bool:
        return any(e.kind == EvidenceKind.NOT_VERIFIED for e in self.evidence)

    def summary_lines(self) -> List[str]:
        lines = [
            f"Task [{self.id}]: {self.prompt[:80]}",
            f"Intent: {self.intent.value}  Status: {self.status.value}  Mode: {self.mode}",
            f"Budget: {self.budget_status()}",
        ]
        if self.actual_files:
            lines.append(f"Files modified: {', '.join(self.actual_files)}")
        if self.evidence:
            lines.append("Evidence:")
            for e in self.evidence:
                lines.append(f"  [{e.kind.value}] {e.description}")
        return lines


# ---------------------------------------------------------------------------
# TaskRouter — classifies prompt into intent + workflow
# ---------------------------------------------------------------------------

# Keyword patterns per intent
_INTENT_PATTERNS: List[tuple] = [
    (TaskIntent.DEBUG,    re.compile(r"\b(fix|debug|error|bug|crash|fail|broken|exception|traceback|why\s+is|not\s+working)\b", re.I)),
    (TaskIntent.FEATURE,  re.compile(r"\b(add|implement|create|build|new\s+feature|scaffold|generate|write\s+a)\b", re.I)),
    (TaskIntent.REFACTOR, re.compile(r"\b(refactor|rename|move|extract|restructure|clean\s+up|reorganize|simplify)\b", re.I)),
    (TaskIntent.TEST,     re.compile(r"\b(test|spec|unit\s+test|coverage|pytest|jest|assertion)\b|write\s+\w*\s*tests?|add\s+\w*\s*tests?", re.I)),
    (TaskIntent.REVIEW,   re.compile(r"\b(review|check|audit|inspect|analyze\s+code|look\s+at|read)\b", re.I)),
    (TaskIntent.ANALYZE,  re.compile(r"\b(explain|understand|how\s+does|what\s+is|show\s+me|describe|map)\b", re.I)),
    (TaskIntent.SETUP,    re.compile(r"\b(setup|install|configure|init|bootstrap|onboard)\b", re.I)),
    (TaskIntent.ASK,      re.compile(r"\b(what|how|why|when|where|which|can\s+you|could\s+you|tell\s+me)\b", re.I)),
]

# Workflow templates per intent — ordered steps
WORKFLOW_TEMPLATES: Dict[TaskIntent, List[str]] = {
    TaskIntent.ASK:      ["inspect", "answer"],
    TaskIntent.ANALYZE:  ["inspect", "explain"],
    TaskIntent.DEBUG:    ["inspect", "reproduce", "plan", "execute", "verify"],
    TaskIntent.FEATURE:  ["inspect", "convention_check", "plan", "impact", "execute", "verify", "review"],
    TaskIntent.REFACTOR: ["inspect", "impact", "plan", "execute", "verify", "review"],
    TaskIntent.TEST:     ["inspect", "plan", "execute", "verify"],
    TaskIntent.REVIEW:   ["inspect", "review"],
    TaskIntent.SETUP:    ["inspect", "plan", "execute", "verify"],
    TaskIntent.UNKNOWN:  ["inspect", "plan", "execute", "verify"],
}

# Mode override per intent (if user hasn't explicitly set mode)
INTENT_DEFAULT_MODE: Dict[TaskIntent, str] = {
    TaskIntent.ASK:      "ask",
    TaskIntent.ANALYZE:  "ask",
    TaskIntent.REVIEW:   "review",
    TaskIntent.DEBUG:    "fix",
    TaskIntent.FEATURE:  "build",
    TaskIntent.REFACTOR: "build",
    TaskIntent.TEST:     "build",
    TaskIntent.SETUP:    "build",
    TaskIntent.UNKNOWN:  "build",
}


class TaskRouter:
    """Classifies a user prompt into intent and returns a workflow template."""

    def classify(self, prompt: str) -> TaskIntent:
        """Return best-match intent for a prompt."""
        scores: Dict[TaskIntent, int] = {}
        for intent, pattern in _INTENT_PATTERNS:
            matches = len(pattern.findall(prompt))
            if matches > 0:
                scores[intent] = matches

        if not scores:
            return TaskIntent.UNKNOWN

        # Return highest score, prefer more specific intents over ASK
        ranked = sorted(scores.items(), key=lambda x: (x[1], x[0] != TaskIntent.ASK), reverse=True)
        return ranked[0][0]

    def create_task(
        self,
        prompt: str,
        current_mode: str = "build",
        max_tool_calls: int = 12,
        max_repair_attempts: int = 3,
        time_budget_seconds: float = 300.0,
    ) -> Task:
        """Create a Task with classified intent and appropriate defaults."""
        intent = self.classify(prompt)
        mode = current_mode  # respect user's explicit mode

        task = Task(
            prompt=prompt,
            intent=intent,
            mode=mode,
            status=TaskStatus.PLANNED,
            max_tool_calls=max_tool_calls,
            max_repair_attempts=max_repair_attempts,
            time_budget_seconds=time_budget_seconds,
        )
        return task

    def get_workflow(self, intent: TaskIntent) -> List[str]:
        """Return ordered workflow steps for an intent."""
        return WORKFLOW_TEMPLATES.get(intent, WORKFLOW_TEMPLATES[TaskIntent.UNKNOWN])

    def get_system_hint(self, task: Task) -> str:
        """Return a system prompt hint describing the task lifecycle to the model."""
        workflow = self.get_workflow(task.intent)
        steps_str = " → ".join(s.upper() for s in workflow)
        return (
            f"\n\n=== CURRENT TASK ===\n"
            f"Task ID: {task.id}\n"
            f"Intent: {task.intent.value}\n"
            f"Workflow: {steps_str}\n"
            f"Status: {task.status.value}\n"
            f"Budget: {task.budget_status()}\n"
            f"===================\n"
        )


# ---------------------------------------------------------------------------
# Budget enforcer
# ---------------------------------------------------------------------------

class BudgetEnforcer:
    """Checks task budgets and decides whether to continue, warn, or stop."""

    WARN_TOOL_THRESHOLD = 0.75   # warn at 75% tool call budget
    WARN_TIME_THRESHOLD = 0.75   # warn at 75% time budget

    def check(self, task: Task) -> Dict[str, Any]:
        """
        Returns:
          action: "continue" | "warn" | "stop"
          reason: human-readable explanation
        """
        tool_ratio = task.tool_call_count / max(task.max_tool_calls, 1)
        time_ratio = task.elapsed() / max(task.time_budget_seconds, 1)

        # Hard stop conditions
        if task.tool_call_count >= task.max_tool_calls:
            return {"action": "stop", "reason": f"tool call limit reached ({task.tool_call_count}/{task.max_tool_calls})"}

        if task.elapsed() > task.time_budget_seconds:
            return {"action": "stop", "reason": f"time budget exceeded ({task.elapsed():.0f}s/{task.time_budget_seconds:.0f}s)"}

        if task.repair_attempt_count >= task.max_repair_attempts:
            return {"action": "stop", "reason": f"repair attempt limit reached ({task.repair_attempt_count}/{task.max_repair_attempts})"}

        # Warn at 75%
        if tool_ratio >= self.WARN_TOOL_THRESHOLD or time_ratio >= self.WARN_TIME_THRESHOLD:
            return {"action": "warn", "reason": f"approaching budget limit — {task.budget_status()}"}

        return {"action": "continue", "reason": ""}
