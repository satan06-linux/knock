"""
gemini.py - Google Gemini provider via OpenAI-compatible endpoint.
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

GEMINI_MODELS = {
    "gemini-2.0-flash":         ProviderCapabilities(context_window=1048576, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "gemini-2.0-flash-lite":    ProviderCapabilities(context_window=1048576, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "gemini-1.5-pro":           ProviderCapabilities(context_window=2097152, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "gemini-1.5-flash":         ProviderCapabilities(context_window=1048576, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
    "gemini-1.5-flash-8b":      ProviderCapabilities(context_window=1048576, streaming=True, native_tools=True, vision=True, max_output_tokens=8192),
}

GEMINI_DISPLAY = [
    ("gemini-2.0-flash",      "Gemini 2.0 Flash      (fast, recommended)"),
    ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite (cheapest)"),
    ("gemini-1.5-pro",        "Gemini 1.5 Pro        (2M context)"),
    ("gemini-1.5-flash",      "Gemini 1.5 Flash      (1M context, fast)"),
]


class GeminiProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self._api_key = api_key
        self._model_name = model_name
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "Google Gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str):
        self._model_name = value

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def health_check(self) -> bool:
        try:
            r = self.client.get(f"{GEMINI_BASE_URL}/models", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = self.client.get(f"{GEMINI_BASE_URL}/models", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            pass
        return list(GEMINI_MODELS.keys())

    def capabilities(self) -> ProviderCapabilities:
        return GEMINI_MODELS.get(self._model_name, ProviderCapabilities(context_window=1048576, streaming=True, native_tools=True))

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        payload: Dict[str, Any] = {"model": self._model_name, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools

        accumulated = {"role": "assistant", "content": ""}

        if not stream:
            r = self.client.post(f"{GEMINI_BASE_URL}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]

        tool_calls_map: Dict[int, Dict] = {}
        with self.client.stream("POST", f"{GEMINI_BASE_URL}/chat/completions", headers=self._headers(), json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content") or ""
                    if content:
                        accumulated["content"] += content
                        yield ChatChunk(type="content", delta=content)
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": tc.get("id", f"call_{idx}"), "type": "function", "function": {"name": tc.get("function", {}).get("name", ""), "arguments": ""}}
                        tool_calls_map[idx]["function"]["arguments"] += tc.get("function", {}).get("arguments", "")
                except Exception:
                    continue

        if tool_calls_map:
            final_calls = []
            for idx in sorted(tool_calls_map.keys()):
                call = tool_calls_map[idx]
                try:
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
                except Exception:
                    call["function"]["arguments"] = {}
                final_calls.append(call)
            accumulated["tool_calls"] = final_calls
            yield ChatChunk(type="tool_calls", tool_calls=final_calls)

        return accumulated
