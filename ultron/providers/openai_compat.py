"""
openai_compat.py - OpenAI-compatible provider for LM Studio, vLLM, Ollama OpenAI shim, etc.
User provides base URL + optional API key.
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk


class OpenAICompatProvider(ModelProvider):
    """
    Works with any OpenAI-compatible server:
    - LM Studio (http://localhost:1234/v1)
    - vLLM
    - LocalAI
    - Ollama OpenAI shim (http://localhost:11434/v1)
    - Jan
    """

    def __init__(self, base_url: str, model_name: str = "", api_key: str = "local"):
        self.base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._api_key = api_key or "local"
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "OpenAI-Compatible"

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
            return r.status_code in (200, 401)  # 401 = server alive but key wrong
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            r = self.client.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                if "data" in data:
                    return [m["id"] for m in data["data"]]
                elif "models" in data:
                    return [m.get("id") or m.get("name", "") for m in data["models"]]
        except Exception:
            pass
        return [self._model_name] if self._model_name else []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(context_window=32768, streaming=True, native_tools=True)

    def chat(self, messages, tools=None, stream=True) -> Generator[ChatChunk, None, Dict[str, Any]]:
        payload: Dict[str, Any] = {"model": self._model_name, "messages": messages, "stream": stream}
        if tools:
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
