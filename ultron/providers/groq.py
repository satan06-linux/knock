"""
groq.py - Groq provider (fast inference, OpenAI-compatible API).
Models: llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b, gemma2-9b-it, etc.
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

GROQ_MODELS = {
    "llama-3.3-70b-versatile": ProviderCapabilities(context_window=128000, streaming=True, native_tools=True, vision=False),
    "llama-3.1-70b-versatile": ProviderCapabilities(context_window=131072, streaming=True, native_tools=True, vision=False),
    "llama-3.1-8b-instant":    ProviderCapabilities(context_window=131072, streaming=True, native_tools=True, vision=False),
    "mixtral-8x7b-32768":      ProviderCapabilities(context_window=32768,  streaming=True, native_tools=True, vision=False),
    "gemma2-9b-it":            ProviderCapabilities(context_window=8192,   streaming=True, native_tools=False, vision=False),
    "llama-3.2-90b-vision-preview": ProviderCapabilities(context_window=128000, streaming=True, native_tools=True, vision=True),
}


class GroqProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "llama-3.3-70b-versatile"):
        self._api_key = api_key
        self._model_name = model_name
        self.client = httpx.Client(timeout=60.0)

    @property
    def provider_name(self) -> str:
        return "Groq"

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
            r = self.client.get(f"{GROQ_BASE_URL}/models", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = self.client.get(f"{GROQ_BASE_URL}/models", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            pass
        return list(GROQ_MODELS.keys())

    def capabilities(self) -> ProviderCapabilities:
        return GROQ_MODELS.get(self._model_name, ProviderCapabilities(context_window=32768, streaming=True, native_tools=True))

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if not stream:
            r = self.client.post(f"{GROQ_BASE_URL}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]

        accumulated = {"role": "assistant", "content": ""}
        tool_calls_map: Dict[int, Dict] = {}

        with self.client.stream("POST", f"{GROQ_BASE_URL}/chat/completions", headers=self._headers(), json=payload) as r:
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
