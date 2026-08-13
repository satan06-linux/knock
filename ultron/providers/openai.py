"""
openai.py - OpenAI provider.
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

OPENAI_BASE_URL = "https://api.openai.com/v1"

OPENAI_MODELS = {
    "gpt-4o":               ProviderCapabilities(context_window=128000, streaming=True, native_tools=True, vision=True,  max_output_tokens=16384),
    "gpt-4o-mini":          ProviderCapabilities(context_window=128000, streaming=True, native_tools=True, vision=True,  max_output_tokens=16384),
    "gpt-4-turbo":          ProviderCapabilities(context_window=128000, streaming=True, native_tools=True, vision=True,  max_output_tokens=4096),
    "gpt-3.5-turbo":        ProviderCapabilities(context_window=16385,  streaming=True, native_tools=True, vision=False, max_output_tokens=4096),
    "o1-preview":           ProviderCapabilities(context_window=128000, streaming=False, native_tools=False, vision=False, max_output_tokens=32768),
    "o1-mini":              ProviderCapabilities(context_window=128000, streaming=False, native_tools=False, vision=False, max_output_tokens=65536),
}

OPENAI_DISPLAY = [
    ("gpt-4o",         "GPT-4o          (flagship, vision, tools)"),
    ("gpt-4o-mini",    "GPT-4o Mini     (fast, cheap, tools)"),
    ("gpt-4-turbo",    "GPT-4 Turbo     (128k context)"),
    ("gpt-3.5-turbo",  "GPT-3.5 Turbo   (cheapest)"),
    ("o1-preview",     "o1 Preview      (reasoning model)"),
    ("o1-mini",        "o1 Mini         (fast reasoning)"),
]


class OpenAIProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini", base_url: str = OPENAI_BASE_URL):
        self._api_key = api_key
        self._model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "OpenAI"

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
            r = self.client.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = self.client.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", []) if "gpt" in m["id"] or "o1" in m["id"]]
        except Exception:
            pass
        return list(OPENAI_MODELS.keys())

    def capabilities(self) -> ProviderCapabilities:
        return OPENAI_MODELS.get(self._model_name, ProviderCapabilities(context_window=16385, streaming=True, native_tools=True))

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        payload: Dict[str, Any] = {"model": self._model_name, "messages": messages, "stream": stream}
        caps = self.capabilities()
        if tools and caps.native_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        accumulated = {"role": "assistant", "content": ""}

        if not stream:
            r = self.client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]

        tool_calls_map: Dict[int, Dict] = {}
        with self.client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=payload) as r:
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
