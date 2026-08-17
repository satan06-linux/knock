"""
task_context.py - Task Context & Evidence Tagging for Ultron Agent.
"""
from enum import Enum
from typing import Dict, Any, List, Optional


class EvidenceTag(str, Enum):
    OBSERVED     = "Observed from code"
    VERIFIED     = "Verified by command"
    INFERRED     = "Inferred"
    NOT_VERIFIED = "Not verified"


class TaskContext:
    """Manages evidence tracking and structured task context state."""

    def __init__(self):
        self.evidence: List[Dict[str, Any]] = []

    def record_evidence(self, kind: str, content: str, tag: EvidenceTag = EvidenceTag.OBSERVED):
        self.evidence.append({
            "kind": kind,
            "content": content,
            "tag": tag.value
        })

    def get_summary(self) -> str:
        if not self.evidence:
            return "No evidence recorded."
        lines = []
        for item in self.evidence:
            lines.append(f"[{item['tag']}] {item['kind']}: {item['content']}")
        return "\n".join(lines)
