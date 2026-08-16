"""
capability_probe.py - P2.2: CapabilityProbe.
Tests model capabilities empirically rather than trusting declarations.
Produces reliability scores 0.0-1.0 per capability.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

from ultron.model_profile import (
    ModelProfile, ModelProfileStore, CapabilityEntry, CapabilityStatus
)


@dataclass
class ProbeResult:
    capability: str
    passed: bool
    reliability: float
    latency: float
    detail: str = ""


class CapabilityProbe:
    """
    Probes a model provider for actual capabilities.
    Tests: tool schema following, structured JSON, tool selection,
    instruction following.
    Results stored to ModelProfileStore.
    """

    TOOL_SCHEMA_PROMPT = (
        "Call the tool named 'echo_test' with argument input='hello_probe'."
    )
    STRUCTURED_JSON_PROMPT = (
        'Respond with ONLY valid JSON: {"status": "ok", "value": 42}'
    )
    INSTRUCTION_PROMPT = (
        "Reply with exactly the word: PROBE_OK — nothing else."
    )

    def __init__(self, provider, console=None):
        self.provider = provider
        self.console = console
        self._store = ModelProfileStore()

    def _log(self, msg: str):
        if self.console:
            self.console.print(f"[dim]{msg}[/dim]")

    def _chat_simple(self, prompt: str, tools: Optional[List[Dict]] = None) -> tuple:
        """Returns (response_text, tool_calls, latency_seconds)."""
        messages = [{"role": "user", "content": prompt}]
        start = time.time()
        response_text = ""
        tool_calls = []
        try:
            gen = self.provider.chat(messages, tools=tools, stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk.get("type") == "content":
                        response_text += chunk.get("delta", "")
                    elif chunk.get("type") == "tool_calls":
                        tool_calls = chunk.get("tool_calls", [])
                except StopIteration:
                    break
        except Exception as e:
            return str(e), [], time.time() - start
        return response_text, tool_calls, time.time() - start

    def probe_tool_calling(self) -> ProbeResult:
        """Test if model follows tool schemas and issues valid tool calls."""
        tool_def = [{
            "type": "function",
            "function": {
                "name": "echo_test",
                "description": "Echo the input string.",
                "parameters": {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                },
            }
        }]
        text, tool_calls, latency = self._chat_simple(self.TOOL_SCHEMA_PROMPT, tools=tool_def)
        passed = bool(tool_calls and tool_calls[0].get("function", {}).get("name") == "echo_test")
        return ProbeResult(
            capability="output_tool_calls",
            passed=passed,
            reliability=0.9 if passed else 0.0,
            latency=latency,
            detail=f"tool_calls_count={len(tool_calls)}",
        )

    def probe_structured_json(self) -> ProbeResult:
        """Test if model can output valid JSON on demand."""
        text, _, latency = self._chat_simple(self.STRUCTURED_JSON_PROMPT)
        passed = False
        try:
            data = json.loads(text.strip())
            passed = data.get("status") == "ok" and data.get("value") == 42
        except Exception:
            # Try extracting JSON from response
            import re
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                    passed = data.get("status") == "ok"
                except Exception:
                    pass
        return ProbeResult(
            capability="output_structured",
            passed=passed,
            reliability=0.9 if passed else 0.3,
            latency=latency,
            detail=f"response={text[:50]}",
        )

    def probe_instruction_following(self) -> ProbeResult:
        """Test basic instruction following."""
        text, _, latency = self._chat_simple(self.INSTRUCTION_PROMPT)
        passed = "PROBE_OK" in text.strip()
        return ProbeResult(
            capability="instruction_following",
            passed=passed,
            reliability=0.95 if passed else 0.4,
            latency=latency,
            detail=f"response={text[:30]}",
        )

    def probe_all(self) -> Dict[str, ProbeResult]:
        """Run all probes. Returns dict of capability → ProbeResult."""
        results = {}
        probes = [
            ("output_tool_calls",      self.probe_tool_calling),
            ("output_structured",      self.probe_structured_json),
            ("instruction_following",  self.probe_instruction_following),
        ]
        for cap_name, probe_fn in probes:
            self._log(f"Probing {cap_name}...")
            try:
                result = probe_fn()
            except Exception as e:
                result = ProbeResult(cap_name, False, 0.0, 0.0, str(e))
            results[cap_name] = result
            status = "✓" if result.passed else "✗"
            self._log(f"  {status} {cap_name}: reliability={result.reliability:.2f} latency={result.latency:.2f}s")
        return results

    def update_profile(self, probe_results: Dict[str, ProbeResult]) -> ModelProfile:
        """Update and persist ModelProfile with probe results."""
        pname = getattr(self.provider, "provider_name", "Unknown")
        mname = getattr(self.provider, "model_name", "")
        profile = self._store.get_or_default(pname, mname)

        for cap_name, result in probe_results.items():
            status = CapabilityStatus.VERIFIED if result.passed else CapabilityStatus.NATIVE
            entry = CapabilityEntry(status=status, reliability=result.reliability)
            if hasattr(profile, cap_name):
                setattr(profile, cap_name, entry)

        profile.health_status = "healthy"
        self._store.save(profile)
        return profile
