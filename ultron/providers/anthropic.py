"""
anthropic.py - Anthropic / Claude provider.
Uses Anthropic Messages API directly (not OpenAI-compat).
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

ANTHROPIC_MODELS = {
    "claude-opus-4-5":         ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True,  max_output_tokens=8192),
    "claude-sonnet-4-5":       ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True,  max_output_tokens=8192),
    "claude-haiku-3-5":        ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True,  max_output_tokens=8192),
    "claude-3-5-sonnet-20241022": ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "claude-3-5-haiku-20241022":  ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "claude-3-opus-20240229":     ProviderCapabilities(context_window=200000, streaming=True, native_tools=True, vision=True, max_output_tokens=4096),
}

# Display names for the picker
ANTHROPIC_DISPLAY = [
    ("claude-sonnet-4-5",          "Claude Sonnet 4.5  (fast, smart, recommended)"),
    ("claude-opus-4-5",            "Claude Opus 4.5    (most capable, slower)"),
    ("claude-haiku-3-5",           "Claude Haiku 3.5   (fastest, cheapest)"),
    ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet  (previous gen)"),
    ("claude-3-opus-20240229",     "Claude 3 Opus      (previous gen)"),
]


def _convert_messages_to_anthropic(messages: List[Dict]) -> tuple:
    """Split system prompt and convert messages to Anthropic format."""
    system = ""
    converted = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            system = content
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": content}],
            })
        elif role == "assistant":
            if msg.get("tool_calls"):
                parts = []
                if content:
                    parts.append({"type": "text", "text": content})
                for tc in msg["tool_calls"]:
                    parts.append({
                        "type": "tool_use",
                        "id": tc.get("id", "call_0"),
                        "name": tc["function"]["name"],
                        "input": tc["function"].get("arguments", {}),
                    })
                converted.append({"role": "assistant", "content": parts})
            else:
                converted.append({"role": "assistant", "content": content})
        else:
            converted.append({"role": "user", "content": content})
    return system, converted


def _convert_tools_to_anthropic(tools: List[Dict]) -> List[Dict]:
    """Convert OpenAI tool format to Anthropic tool format."""
    result = []
    for t in tools:
        fn = t.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


class AnthropicProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "claude-sonnet-4-5"):
        self._api_key = api_key
        self._model_name = model_name
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "Anthropic"

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str):
        self._model_name = value

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def health_check(self) -> bool:
        # Anthropic has no /models endpoint — send a minimal message
        try:
            system, msgs = _convert_messages_to_anthropic([
                {"role": "user", "content": "ping"}
            ])
            payload = {
                "model": self._model_name,
                "max_tokens": 5,
                "messages": msgs,
            }
            if system:
                payload["system"] = system
            r = self.client.post(f"{ANTHROPIC_BASE_URL}/messages", headers=self._headers(), json=payload, timeout=15)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        return [m for m, _ in ANTHROPIC_DISPLAY]

    def capabilities(self) -> ProviderCapabilities:
        return ANTHROPIC_MODELS.get(self._model_name, ProviderCapabilities(context_window=200000, streaming=True, native_tools=True))

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        system, converted = _convert_messages_to_anthropic(messages)
        caps = self.capabilities()

        payload: Dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": caps.max_output_tokens,
            "messages": converted,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _convert_tools_to_anthropic(tools)

        accumulated = {"role": "assistant", "content": ""}

        if not stream:
            r = self.client.post(f"{ANTHROPIC_BASE_URL}/messages", headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
            text = ""
            tool_calls = []
            for block in data.get("content", []):
                if block["type"] == "text":
                    text += block["text"]
                elif block["type"] == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {"name": block["name"], "arguments": block.get("input", {})},
                    })
            accumulated["content"] = text
            if tool_calls:
                accumulated["tool_calls"] = tool_calls
            return accumulated

        # Streaming
        current_tool: Dict[str, Any] = {}
        tool_calls_list = []
        current_tool_idx = -1

        with self.client.stream("POST", f"{ANTHROPIC_BASE_URL}/messages", headers=self._headers(), json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except Exception:
                    continue

                etype = event.get("type", "")

                if etype == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool_idx += 1
                        current_tool = {
                            "id": block.get("id", f"call_{current_tool_idx}"),
                            "type": "function",
                            "function": {"name": block.get("name", ""), "arguments": ""},
                        }

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        accumulated["content"] += text
                        yield ChatChunk(type="content", delta=text)
                    elif delta.get("type") == "input_json_delta":
                        current_tool["function"]["arguments"] += delta.get("partial_json", "")

                elif etype == "content_block_stop":
                    if current_tool:
                        try:
                            current_tool["function"]["arguments"] = json.loads(current_tool["function"]["arguments"])
                        except Exception:
                            current_tool["function"]["arguments"] = {}
                        tool_calls_list.append(current_tool)
                        current_tool = {}

        if tool_calls_list:
            accumulated["tool_calls"] = tool_calls_list
            yield ChatChunk(type="tool_calls", tool_calls=tool_calls_list)

        return accumulated
