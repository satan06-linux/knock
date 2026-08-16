"""
model_profile.py - P2.1: Model Capability System.
ModelProfile replaces flat ProviderCapabilities with rich capability metadata.
Distinguishes NATIVE (claimed) vs VERIFIED (tested) capabilities.
"""
import os
import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any
from enum import Enum


class CapabilityStatus(str, Enum):
    NATIVE   = "native"    # provider claims this capability
    VERIFIED = "verified"  # tested and confirmed
    UNKNOWN  = "unknown"   # not tested
    ABSENT   = "absent"    # tested and not present


class LatencyClass(str, Enum):
    LOCAL      = "local"       # Ollama / LM Studio
    FAST       = "fast"        # Groq, Haiku
    STANDARD   = "standard"    # GPT-4o, Sonnet
    SLOW       = "slow"        # o1, Opus


class CostClass(str, Enum):
    FREE       = "free"
    LOW        = "low"         # < $1/M tokens
    MEDIUM     = "medium"      # $1-10/M
    HIGH       = "high"        # > $10/M


@dataclass
class CapabilityEntry:
    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    reliability: float = 0.0   # 0.0 - 1.0, populated by CapabilityProbe


@dataclass
class ModelProfile:
    # Identity
    provider: str = ""
    model: str = ""
    version: str = ""

    # Input capabilities
    input_text: CapabilityEntry = field(default_factory=lambda: CapabilityEntry(CapabilityStatus.NATIVE, 1.0))
    input_image: CapabilityEntry = field(default_factory=CapabilityEntry)
    input_audio: CapabilityEntry = field(default_factory=CapabilityEntry)
    input_files: CapabilityEntry = field(default_factory=CapabilityEntry)

    # Output capabilities
    output_text: CapabilityEntry = field(default_factory=lambda: CapabilityEntry(CapabilityStatus.NATIVE, 1.0))
    output_structured: CapabilityEntry = field(default_factory=CapabilityEntry)
    output_tool_calls: CapabilityEntry = field(default_factory=CapabilityEntry)
    output_streaming: CapabilityEntry = field(default_factory=CapabilityEntry)

    # Reasoning
    coding: CapabilityEntry = field(default_factory=CapabilityEntry)
    planning: CapabilityEntry = field(default_factory=CapabilityEntry)
    debugging: CapabilityEntry = field(default_factory=CapabilityEntry)
    instruction_following: CapabilityEntry = field(default_factory=CapabilityEntry)

    # Context
    context_window: int = 4096
    max_output_tokens: int = 4096

    # Runtime
    latency_class: LatencyClass = LatencyClass.STANDARD
    cost_class: CostClass = CostClass.MEDIUM
    is_local: bool = False

    # Health (updated by ModelHealthTracker)
    health_status: str = "unknown"   # "healthy" | "degraded" | "unavailable"
    tool_call_failure_rate: float = 0.0
    timeout_rate: float = 0.0

    def supports_tools(self) -> bool:
        return self.output_tool_calls.status in (CapabilityStatus.NATIVE, CapabilityStatus.VERIFIED)

    def supports_vision(self) -> bool:
        return self.input_image.status in (CapabilityStatus.NATIVE, CapabilityStatus.VERIFIED)

    def is_healthy(self) -> bool:
        return self.health_status == "healthy" and self.tool_call_failure_rate < 0.3

    def to_dict(self) -> Dict[str, Any]:
        def _cap(c: CapabilityEntry) -> dict:
            return {"status": c.status.value, "reliability": c.reliability}
        return {
            "provider": self.provider, "model": self.model, "version": self.version,
            "input": {
                "text": _cap(self.input_text), "image": _cap(self.input_image),
                "audio": _cap(self.input_audio), "files": _cap(self.input_files),
            },
            "output": {
                "text": _cap(self.output_text), "structured": _cap(self.output_structured),
                "tool_calls": _cap(self.output_tool_calls), "streaming": _cap(self.output_streaming),
            },
            "reasoning": {
                "coding": _cap(self.coding), "planning": _cap(self.planning),
                "debugging": _cap(self.debugging), "instruction_following": _cap(self.instruction_following),
            },
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "latency_class": self.latency_class.value,
            "cost_class": self.cost_class.value,
            "is_local": self.is_local,
            "health": {
                "status": self.health_status,
                "tool_call_failure_rate": self.tool_call_failure_rate,
                "timeout_rate": self.timeout_rate,
            },
        }


# ---------------------------------------------------------------------------
# Built-in profiles (native capabilities only — reliability set by probe)
# ---------------------------------------------------------------------------

def _native(r: float = 0.0) -> CapabilityEntry:
    return CapabilityEntry(CapabilityStatus.NATIVE, r)

def _absent() -> CapabilityEntry:
    return CapabilityEntry(CapabilityStatus.ABSENT, 0.0)


BUILTIN_PROFILES: Dict[str, ModelProfile] = {
    "ollama/qwen2.5-coder:7b": ModelProfile(
        provider="Ollama", model="qwen2.5-coder:7b",
        input_text=_native(1.0), output_text=_native(1.0),
        output_tool_calls=_native(0.8), output_streaming=_native(1.0),
        coding=_native(0.85), debugging=_native(0.75),
        planning=_native(0.65), instruction_following=_native(0.80),
        context_window=16384, max_output_tokens=8192,
        latency_class=LatencyClass.LOCAL, cost_class=CostClass.FREE, is_local=True,
    ),
    "groq/llama-3.3-70b-versatile": ModelProfile(
        provider="Groq", model="llama-3.3-70b-versatile",
        input_text=_native(1.0), output_text=_native(1.0),
        output_tool_calls=_native(0.9), output_streaming=_native(1.0),
        coding=_native(0.88), debugging=_native(0.85),
        planning=_native(0.83), instruction_following=_native(0.90),
        context_window=131072, max_output_tokens=8192,
        latency_class=LatencyClass.FAST, cost_class=CostClass.LOW, is_local=False,
    ),
    "anthropic/claude-sonnet-4-5": ModelProfile(
        provider="Anthropic", model="claude-sonnet-4-5",
        input_text=_native(1.0), input_image=_native(0.95), output_text=_native(1.0),
        output_tool_calls=_native(0.95), output_streaming=_native(1.0),
        output_structured=_native(0.93),
        coding=_native(0.93), debugging=_native(0.91),
        planning=_native(0.90), instruction_following=_native(0.95),
        context_window=200000, max_output_tokens=8192,
        latency_class=LatencyClass.STANDARD, cost_class=CostClass.MEDIUM, is_local=False,
    ),
    "openai/gpt-4o": ModelProfile(
        provider="OpenAI", model="gpt-4o",
        input_text=_native(1.0), input_image=_native(0.95), output_text=_native(1.0),
        output_tool_calls=_native(0.95), output_streaming=_native(1.0),
        coding=_native(0.92), debugging=_native(0.90),
        planning=_native(0.88), instruction_following=_native(0.93),
        context_window=128000, max_output_tokens=16384,
        latency_class=LatencyClass.STANDARD, cost_class=CostClass.MEDIUM, is_local=False,
    ),
    "openai/gpt-4o-mini": ModelProfile(
        provider="OpenAI", model="gpt-4o-mini",
        input_text=_native(1.0), output_text=_native(1.0),
        output_tool_calls=_native(0.90), output_streaming=_native(1.0),
        coding=_native(0.82), debugging=_native(0.78),
        planning=_native(0.76), instruction_following=_native(0.85),
        context_window=128000, max_output_tokens=16384,
        latency_class=LatencyClass.FAST, cost_class=CostClass.LOW, is_local=False,
    ),
    "gemini/gemini-2.0-flash": ModelProfile(
        provider="Google", model="gemini-2.0-flash",
        input_text=_native(1.0), input_image=_native(0.9), output_text=_native(1.0),
        output_tool_calls=_native(0.88), output_streaming=_native(1.0),
        coding=_native(0.85), debugging=_native(0.82),
        planning=_native(0.80), instruction_following=_native(0.87),
        context_window=1048576, max_output_tokens=8192,
        latency_class=LatencyClass.FAST, cost_class=CostClass.LOW, is_local=False,
    ),
}


# ---------------------------------------------------------------------------
# Profile store — persists probe results per workspace/model
# ---------------------------------------------------------------------------

class ModelProfileStore:
    """Loads and saves model profiles to ~/.ultron/model_profiles/"""

    def __init__(self):
        self.store_dir = os.path.join(os.path.expanduser("~"), ".ultron", "model_profiles")
        os.makedirs(self.store_dir, exist_ok=True)

    def _key(self, provider: str, model: str) -> str:
        raw = f"{provider.lower()}/{model.lower()}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def load(self, provider: str, model: str) -> Optional[ModelProfile]:
        """Load profile from disk, falling back to built-in catalog."""
        key = f"{provider.lower()}/{model.lower()}"

        # Try disk first
        path = os.path.join(self.store_dir, f"{self._key(provider, model)}.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                profile = ModelProfile(
                    provider=data.get("provider", provider),
                    model=data.get("model", model),
                    context_window=data.get("context_window", 4096),
                    max_output_tokens=data.get("max_output_tokens", 4096),
                    is_local=data.get("is_local", False),
                    health_status=data.get("health", {}).get("status", "unknown"),
                    tool_call_failure_rate=data.get("health", {}).get("tool_call_failure_rate", 0.0),
                )
                return profile
            except Exception:
                pass

        # Fall back to built-in catalog
        return BUILTIN_PROFILES.get(key)

    def save(self, profile: ModelProfile):
        path = os.path.join(self.store_dir, f"{self._key(profile.provider, profile.model)}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def get_or_default(self, provider: str, model: str) -> ModelProfile:
        profile = self.load(provider, model)
        if profile:
            return profile
        return ModelProfile(provider=provider, model=model)
