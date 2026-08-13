"""
openrouter.py - OpenRouter provider (access 200+ models via one API key).
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Curated popular models shown before API key fetch
OPENROUTER_CATALOG = [
    ("anthropic/claude-sonnet-4-5",          "Claude Sonnet 4.5       (Anthropic)"),
    ("anthropic/claude-haiku-3-5",           "Claude Haiku 3.5        (Anthropic, fast)"),
    ("meta-llama/llama-3.3-70b-instruct",    "Llama 3.3 70B           (Meta, free tier)"),
    ("google/gemini-2.0-flash",              "Gemini 2.0 Flash        (Google)"),
    ("openai/gpt-4o-mini",                   "GPT-4o Mini             (OpenAI)"),
    ("openai/gpt-4o",                        "GPT-4o                  (OpenAI)"),
    ("mistralai/mistral-7b-instruct",        "Mistral 7B              (free tier)"),
    ("deepseek/deepseek-coder",              "DeepSeek Coder          (coding)"),
    ("qwen/qwen-2.5-coder-32b-instruct",     "Qwen 2.5 Coder 32B      (coding)"),
]


class OpenRouterProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "anthropic/claude-haiku-3-5"):
        self._api_key = api_key
        self._model_name = model_name
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "OpenRouter"

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str):
        self._model_name = value

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ultron-cli",
            "X-Title": "Ultron CLI",
        }

    def health_check(self) -> bool:
        try:
            r = self.client.get(f"{OPENROUTER_BASE_URL}/models", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = self.client.get(f"{OPENROUTER_BASE_URL}/models", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            pass
        return [m for m, _ in OPENROUTER_CATALOG]

    def capabilities(self) -> ProviderCapabilities:
        # OpenRouter forwards to underlying model — use conservative defaults
        return ProviderCapabilities(context_window=32768, streaming=True, native_tools=True, vision=False)

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        payload: Dict[str, Any] = {"model": self._model_name, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        accumulated = {"role": "assistant", "content": ""}

        if not stream:
            r = self.client.post(f"{OPENROUTER_BASE_URL}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]

        tool_calls_map: Dict[int, Dict] = {}
        with self.client.stream("POST", f"{OPENROUTER_BASE_URL}/chat/completions", headers=self._headers(), json=payload) as r:
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
