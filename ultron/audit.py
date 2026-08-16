"""
audit.py - P0.9: Centralized AuditEvent schema and AuditSanitizer.
Every security-relevant decision in Ultron emits an AuditEvent.
AuditSanitizer ensures no sensitive data reaches audit storage.
"""
import os
import re
import json
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Task lifecycle
    TASK_STARTED        = "TASK_STARTED"
    TASK_COMPLETED      = "TASK_COMPLETED"
    TASK_BLOCKED        = "TASK_BLOCKED"
    TASK_CANCELLED      = "TASK_CANCELLED"

    # Tool execution pipeline
    TOOL_REQUESTED      = "TOOL_REQUESTED"
    TOOL_ALLOWED        = "TOOL_ALLOWED"
    TOOL_DENIED         = "TOOL_DENIED"
    TOOL_EXECUTED       = "TOOL_EXECUTED"
    TOOL_FAILED         = "TOOL_FAILED"

    # Scope
    SCOPE_EXPANDED      = "SCOPE_EXPANDED"
    SCOPE_VIOLATION     = "SCOPE_VIOLATION"
    SCOPE_BLOCKED       = "SCOPE_BLOCKED"
    SCOPE_ASKED         = "SCOPE_ASKED"

    # Security
    SECRET_DETECTED     = "SECRET_DETECTED"
    INJECTION_DETECTED  = "INJECTION_DETECTED"
    PATH_VIOLATION      = "PATH_VIOLATION"

    # Policy
    POLICY_ENGINE_ERROR = "POLICY_ENGINE_ERROR"
    POLICY_DENIED       = "POLICY_DENIED"
    POLICY_ASKED        = "POLICY_ASKED"
    POLICY_ALLOWED      = "POLICY_ALLOWED"

    # Checkpoint / rollback
    CHECKPOINT_CREATED    = "CHECKPOINT_CREATED"
    ROLLBACK_STARTED      = "ROLLBACK_STARTED"
    ROLLBACK_COMPLETED    = "ROLLBACK_COMPLETED"
    ROLLBACK_CONFLICT     = "ROLLBACK_CONFLICT"

    # Provider
    PROVIDER_FALLBACK   = "PROVIDER_FALLBACK"
    PROVIDER_ERROR      = "PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# AuditEvent dataclass
# ---------------------------------------------------------------------------

@dataclass
class AuditEvent:
    event_type: EventType
    reason: str
    task_id: str = ""
    transaction_id: str = ""
    actor: str = "system"           # "model" | "user" | "system"
    tool: Optional[str] = None
    resource: Optional[str] = None
    operation: Optional[str] = None
    decision: Optional[str] = None
    risk: Optional[str] = None
    redacted_metadata: Dict[str, Any] = field(default_factory=dict)

    # Auto-populated
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "event_type": self.event_type.value,
            "tool": self.tool,
            "resource": self.resource,
            "operation": self.operation,
            "decision": self.decision,
            "risk": self.risk,
            "reason": self.reason,
            "redacted_metadata": self.redacted_metadata,
        }


# ---------------------------------------------------------------------------
# AuditSanitizer — strip sensitive data before storage
# ---------------------------------------------------------------------------

# Patterns for values that must never appear in audit storage
_SENSITIVE_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|auth|credential|private[_-]?key|access[_-]?key|bearer)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),          # OpenAI keys
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),         # JWTs
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),          # Groq keys
    re.compile(r"claude-[A-Za-z0-9-]{5,}"),       # Anthropic model IDs (not keys, but safe)
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),         # Google API keys
    re.compile(r"-----BEGIN [A-Z ]+-----"),        # PEM headers
]


def _redact_value(value: str) -> str:
    return "[REDACTED_IN_AUDIT]"


def _sanitize_dict(data: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """Recursively sanitize a dict, redacting sensitive keys and values."""
    if depth > 5:
        return {"_truncated": "max_depth"}
    result = {}
    for k, v in data.items():
        str_k = str(k)
        if _SENSITIVE_KEY_PATTERNS.search(str_k):
            result[str_k] = "[REDACTED_KEY]"
        elif isinstance(v, dict):
            result[str_k] = _sanitize_dict(v, depth + 1)
        elif isinstance(v, str):
            redacted = v
            for pat in _SENSITIVE_VALUE_PATTERNS:
                redacted = pat.sub("[REDACTED_VALUE]", redacted)
            result[str_k] = redacted
        elif isinstance(v, list):
            result[str_k] = [
                _sanitize_dict(item, depth + 1) if isinstance(item, dict)
                else ("[REDACTED_VALUE]" if isinstance(item, str) and
                      any(p.search(item) for p in _SENSITIVE_VALUE_PATTERNS) else item)
                for item in v
            ]
        else:
            result[str_k] = v
    return result


class AuditSanitizer:
    """
    Sanitizes AuditEvents before storage.
    No caller is trusted to redact — sanitization is mandatory in the pipeline.
    AuditEvent → AuditSanitizer → AuditLogger
    """

    @staticmethod
    def sanitize(event: AuditEvent) -> AuditEvent:
        """Return a new AuditEvent with all sensitive data redacted."""
        clean_meta = _sanitize_dict(event.redacted_metadata)

        # Also sanitize resource path (may contain username in home dir)
        resource = event.resource
        if resource:
            # Normalize home dir to ~
            home = os.path.expanduser("~")
            if resource.startswith(home):
                resource = "~" + resource[len(home):]

        return AuditEvent(
            event_id=event.event_id,
            task_id=event.task_id,
            transaction_id=event.transaction_id,
            timestamp=event.timestamp,
            actor=event.actor,
            event_type=event.event_type,
            tool=event.tool,
            resource=resource,
            operation=event.operation,
            decision=event.decision,
            risk=event.risk,
            reason=event.reason,
            redacted_metadata=clean_meta,
        )


# ---------------------------------------------------------------------------
# AuditLogger — persists sanitized events
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Persists AuditEvents to ~/.ultron/audit/<workspace_hash>/audit.jsonl
    Always sanitizes before writing. Never writes unsanitized events.
    """

    def __init__(self, workspace_root: str):
        path_hash = hashlib.md5(
            os.path.realpath(os.path.abspath(workspace_root)).encode()
        ).hexdigest()
        self.log_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "audit", path_hash
        )
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(
            self.log_dir,
            f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )
        self._sanitizer = AuditSanitizer()

    def emit(self, event: AuditEvent):
        """Sanitize then persist an audit event. Never raises."""
        try:
            clean = AuditSanitizer.sanitize(event)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(clean.to_dict()) + "\n")
        except Exception:
            pass  # Audit logging must never crash the agent

    def load_today(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.log_path):
            return []
        events = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        return events

    def load_recent(self, days: int = 7) -> List[Dict[str, Any]]:
        events = []
        try:
            files = sorted(
                [f for f in os.listdir(self.log_dir) if f.endswith(".jsonl")],
                reverse=True
            )[:days]
            for fname in files:
                fpath = os.path.join(self.log_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    events.append(json.loads(line))
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass
        return events
