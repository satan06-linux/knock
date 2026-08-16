"""
event_bus.py - P1.4: Lightweight pub/sub EventBus.
Decouples subsystems: AuditLogger, MetricsCollector, SessionLogger,
future NotificationManager all subscribe to events instead of being called directly.
"""
import threading
from typing import Callable, Dict, List, Any


class EventBus:
    """
    Simple synchronous pub/sub bus.
    publish() calls all subscribers for that event_type in registration order.
    Thread-safe subscription, synchronous dispatch.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]):
        """Register a handler for an event type. Use '*' to subscribe to all events."""
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        with self._lock:
            if event_type in self._handlers:
                self._handlers[event_type] = [
                    h for h in self._handlers[event_type] if h != handler
                ]

    def publish(self, event_type: str, data: Dict[str, Any] = None):
        """
        Dispatch event to all registered handlers.
        Never raises — handler exceptions are silently swallowed to protect the agent.
        """
        data = data or {}
        data["_event_type"] = event_type

        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
            wildcard = list(self._handlers.get("*", []))

        for handler in handlers + wildcard:
            try:
                handler(data)
            except Exception:
                pass  # EventBus must never crash the agent

    def clear(self):
        with self._lock:
            self._handlers.clear()


# ---------------------------------------------------------------------------
# Standard event type constants
# ---------------------------------------------------------------------------

class BusEvent:
    # Task lifecycle
    TASK_STARTED       = "task.started"
    TASK_COMPLETED     = "task.completed"
    TASK_BLOCKED       = "task.blocked"
    TASK_CANCELLED     = "task.cancelled"

    # Tool execution
    TOOL_EXECUTED      = "tool.executed"
    TOOL_DENIED        = "tool.denied"
    TOOL_FAILED        = "tool.failed"

    # Security
    SECRET_DETECTED    = "security.secret_detected"
    INJECTION_DETECTED = "security.injection_detected"
    SCOPE_VIOLATION    = "security.scope_violation"

    # Model
    MODEL_CALL         = "model.call"
    MODEL_ERROR        = "model.error"
    MODEL_FALLBACK     = "model.fallback"
    MODEL_DEGRADED     = "model.degraded"

    # Verification
    VERIFY_PASSED      = "verify.passed"
    VERIFY_FAILED      = "verify.failed"

    # Repair
    REPAIR_STARTED     = "repair.started"
    REPAIR_SUCCEEDED   = "repair.succeeded"
    REPAIR_FAILED      = "repair.failed"
    REPAIR_EXHAUSTED   = "repair.exhausted"

    # Health
    HEALTH_CHECK       = "health.check"
    HEALTH_DEGRADED    = "health.degraded"


# Global singleton bus — wire into agent and tool_executor
_global_bus = EventBus()


def get_bus() -> EventBus:
    return _global_bus
