"""
ollama.py - Ollama local provider.
Migrated from ultron/models.py, now implements ModelProvider interface.
"""
import json
import httpx
from typing import List, Dict, Any, Optional, Generator

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk


class OllamaProvider(ModelProvider):

    def __init__(
        self,
        model_name: str = "qwen2.5-coder:7b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        num_ctx: int = 16384,
        keep_alive: str = "5m",
    ):
        self._model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.client = httpx.Client(timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "Ollama"

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str):
        self._model_name = value

    def health_check(self) -> bool:
        return self.is_available()

    def is_available(self) -> bool:
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                for m in models:
                    if m == self._model_name:
                        return True
                    if ":" in m and ":" not in self._model_name:
                        if m.split(":")[0] == self._model_name:
                            return True
                    if ":" not in m and ":" in self._model_name:
                        if m == self._model_name.split(":")[0]:
                            return True
            return False
        except Exception:
            return False

    def list_models(self) -> List[str]:
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                return [m["name"] for m in response.json().get("models", [])]
        except Exception:
            pass
        return []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            context_window=self.num_ctx,
            streaming=True,
            native_tools=True,
            vision=False,
            max_output_tokens=self.num_ctx,
        )

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return tool schemas — kept for backward compatibility with agent.py."""
        return [
            {"type": "function", "function": {"name": "list_dir", "description": "List directory contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "grep_search", "description": "Search workspace files.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "view_file", "description": "Read file content.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "write_file", "description": "Write file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
            {"type": "function", "function": {"name": "patch_file", "description": "Patch file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "search_content": {"type": "string"}, "replacement_content": {"type": "string"}}, "required": ["path", "search_content", "replacement_content"]}}},
            {"type": "function", "function": {"name": "run_command", "description": "Run shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
            {"type": "function", "function": {"name": "git_status", "description": "Get git status.", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "git_commit", "description": "Git commit.", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
        ]

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = True,
    ) -> Generator[ChatChunk, None, Dict[str, Any]]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self._model_name,
            "messages": messages,
            "stream": stream,
            "tools": tools if tools is not None else self.get_tool_definitions(),
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
            "keep_alive": self.keep_alive,
        }

        if not stream:
            response = self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json().get("message", {})

        accumulated = {"role": "assistant", "content": ""}
        tool_calls_map = {}

        try:
            with self.client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    msg_chunk = chunk.get("message", {})

                    content = msg_chunk.get("content", "")
                    if content:
                        accumulated["content"] += content
                        yield ChatChunk(type="content", delta=content)

                    for tc in msg_chunk.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {
                                "id": tc.get("id", f"call_{idx}"),
                                "type": "function",
                                "function": {
                                    "name": tc["function"].get("name", ""),
                                    "arguments": tc["function"].get("arguments", ""),
                                },
                            }
                        else:
                            args = tc.get("function", {}).get("arguments", "")
                            if isinstance(args, str):
                                tool_calls_map[idx]["function"]["arguments"] += args
                            elif isinstance(args, dict):
                                tool_calls_map[idx]["function"]["arguments"] = args
        except KeyboardInterrupt:
            raise

        if tool_calls_map:
            final_calls = []
            for idx in sorted(tool_calls_map.keys()):
                call = tool_calls_map[idx]
                args = call["function"]["arguments"]
                if isinstance(args, str) and args.strip():
                    try:
                        call["function"]["arguments"] = json.loads(args)
                    except Exception:
                        call["function"]["arguments"] = {}
                elif not isinstance(args, dict):
                    call["function"]["arguments"] = {}
                final_calls.append(call)
            accumulated["tool_calls"] = final_calls
            yield ChatChunk(type="tool_calls", tool_calls=final_calls)

        return accumulated
