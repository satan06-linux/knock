"""
model_router.py - P2.3 + P2.4: ModelRouter + ModelHealthTracker.
Routes tasks to the best available model based on capability requirements.
Tracks real-time model health: latency, timeout rate, tool-call failures.
"""
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from ultron.model_profile import (
    ModelProfile, ModelProfileStore, CapabilityStatus, BUILTIN_PROFILES
)
from ultron.task import TaskIntent
from ultron.event_bus import get_bus, BusEvent


# ---------------------------------------------------------------------------
# Task requirements
# ---------------------------------------------------------------------------

@dataclass
class TaskRequirements:
    task_type: str
    requires_tools: bool = True
    requires_vision: bool = False
    requires_long_context: bool = False    # > 32k tokens
    requires_strong_coding: bool = False   # complex multi-file
    prefers_fast: bool = False             # latency-sensitive
    context_budget: int = 16000


# Default requirements per TaskIntent
_INTENT_REQUIREMENTS: Dict[str, TaskRequirements] = {
    TaskIntent.ASK.value:      TaskRequirements("ask",      requires_tools=False, prefers_fast=True),
    TaskIntent.ANALYZE.value:  TaskRequirements("analyze",  requires_tools=False, prefers_fast=True),
    TaskIntent.REVIEW.value:   TaskRequirements("review",   requires_tools=False),
    TaskIntent.DEBUG.value:    TaskRequirements("debug",    requires_tools=True, requires_strong_coding=True),
    TaskIntent.FEATURE.value:  TaskRequirements("feature",  requires_tools=True, requires_strong_coding=True),
    TaskIntent.REFACTOR.value: TaskRequirements("refactor", requires_tools=True, requires_strong_coding=True),
    TaskIntent.TEST.value:     TaskRequirements("test",     requires_tools=True),
    TaskIntent.SETUP.value:    TaskRequirements("setup",    requires_tools=True),
    TaskIntent.UNKNOWN.value:  TaskRequirements("unknown",  requires_tools=True),
}


# ---------------------------------------------------------------------------
# ModelHealthTracker
# ---------------------------------------------------------------------------

@dataclass
class ModelStats:
    provider: str
    model: str
    total_calls: int = 0
    timeouts: int = 0
    tool_call_failures: int = 0
    malformed_outputs: int = 0
    total_latency: float = 0.0
    last_call_time: float = 0.0

    @property
    def timeout_rate(self) -> float:
        return self.timeouts / max(self.total_calls, 1)

    @property
    def tool_failure_rate(self) -> float:
        return self.tool_call_failures / max(self.total_calls, 1)

    @property
    def avg_latency(self) -> float:
        return self.total_latency / max(self.total_calls, 1)

    @property
    def health_status(self) -> str:
        if self.timeout_rate > 0.3 or self.tool_failure_rate > 0.4:
            return "degraded"
        if self.total_calls == 0:
            return "unknown"
        return "healthy"


class ModelHealthTracker:
    """
    Tracks real-time health metrics per model.
    Populated automatically from every model call in agent loop.
    """

    def __init__(self):
        self._stats: Dict[str, ModelStats] = {}

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record_call(self, provider: str, model: str, latency: float,
                    timed_out: bool = False, tool_call_failed: bool = False,
                    malformed: bool = False):
        key = self._key(provider, model)
        if key not in self._stats:
            self._stats[key] = ModelStats(provider=provider, model=model)
        s = self._stats[key]
        s.total_calls += 1
        s.total_latency += latency
        s.last_call_time = time.time()
        if timed_out:
            s.timeouts += 1
        if tool_call_failed:
            s.tool_call_failures += 1
        if malformed:
            s.malformed_outputs += 1

        if s.health_status == "degraded":
            get_bus().publish(BusEvent.MODEL_DEGRADED, {
                "provider": provider, "model": model,
                "timeout_rate": s.timeout_rate,
                "tool_failure_rate": s.tool_failure_rate,
            })

    def get_stats(self, provider: str, model: str) -> Optional[ModelStats]:
        return self._stats.get(self._key(provider, model))

    def is_healthy(self, provider: str, model: str) -> bool:
        s = self.get_stats(provider, model)
        if s is None:
            return True  # no data yet = assume healthy
        return s.health_status == "healthy"


# Global health tracker
_health_tracker = ModelHealthTracker()


def get_health_tracker() -> ModelHealthTracker:
    return _health_tracker


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class ModelRouter:
    """
    Routes tasks to the best available provider/model based on:
    1. Task capability requirements
    2. Model profile (capabilities + reliability)
    3. Current health status
    4. Context fit
    5. Latency/cost preference
    """

    def __init__(self, registry=None):
        self.registry = registry  # ProviderRegistry
        self._store = ModelProfileStore()
        self._tracker = _health_tracker

    def get_requirements(self, intent: str) -> TaskRequirements:
        return _INTENT_REQUIREMENTS.get(intent, _INTENT_REQUIREMENTS["unknown"])

    def route(self, intent: str, available_providers: List[Any]) -> Optional[Any]:
        """
        Select best provider from available_providers list for the given intent.
        Returns the provider object or None if no suitable provider found.
        """
        if not available_providers:
            return None

        reqs = self.get_requirements(intent)
        scored = []

        for provider in available_providers:
            pname = getattr(provider, "provider_name", "Unknown")
            mname = getattr(provider, "model_name", "")
            profile = self._store.get_or_default(pname, mname)
            score = self._score(provider, profile, reqs)
            scored.append((score, provider))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score(self, provider: Any, profile: ModelProfile, reqs: TaskRequirements) -> float:
        score = 0.0

        # Health check — unhealthy providers scored down heavily
        pname = getattr(provider, "provider_name", "")
        mname = getattr(provider, "model_name", "")
        if not self._tracker.is_healthy(pname, mname):
            score -= 50.0

        # Tool support
        if reqs.requires_tools and not profile.supports_tools():
            score -= 30.0
        elif reqs.requires_tools and profile.supports_tools():
            score += profile.output_tool_calls.reliability * 20

        # Strong coding
        if reqs.requires_strong_coding:
            score += profile.coding.reliability * 25

        # Fast preference
        from ultron.model_profile import LatencyClass
        if reqs.prefers_fast and profile.latency_class in (LatencyClass.LOCAL, LatencyClass.FAST):
            score += 15.0

        # Context fit
        if reqs.context_budget <= profile.context_window:
            score += 10.0
        else:
            score -= 20.0  # can't fit context

        # Local preference (privacy)
        if profile.is_local:
            score += 5.0

        return score

    def describe_routing(self, intent: str) -> Dict[str, str]:
        """Return human-readable routing rationale for a given intent."""
        reqs = self.get_requirements(intent)
        return {
            "intent": intent,
            "requires_tools": str(reqs.requires_tools),
            "requires_strong_coding": str(reqs.requires_strong_coding),
            "prefers_fast": str(reqs.prefers_fast),
        }
