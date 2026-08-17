"""
base.py - ModelProvider interface.
All providers implement this. Agent only talks to this interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Generator


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool = True
    vision: bool = False
    streaming: bool = True
    structured_output: bool = True
    context_window: Optional[int] = 128000
    max_output_tokens: Optional[int] = 4096


@dataclass
class ProviderCapabilities:
    context_window: int = 4096
    streaming: bool = True
    native_tools: bool = False
    vision: bool = False
    max_output_tokens: int = 4096


@dataclass
class ModelInfo:
    """Rich dynamic metadata for a model capability query."""
    name: str
    provider: str
    context_window: int = 4096
    max_output_tokens: int = 4096
    tool_calling: bool = True
    structured_output: bool = True
    streaming: bool = True
    vision: bool = False
    reasoning: bool = False
    availability: bool = True


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ChatChunk:
    type: str           # "content" | "tool_calls" | "done"
    delta: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class ModelProvider(ABC):
    """Base interface every provider must implement."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name e.g. 'Ollama', 'Groq'"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Currently active model name."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if provider is reachable and key is valid."""

    def is_available(self) -> bool:
        """Alias for health_check() — backward compat with OllamaModel."""
        return self.health_check()

    # Backward compat shims used by cli.py / headless.py
    @property
    def base_url(self) -> str:
        return getattr(self, "_base_url", "")

    @base_url.setter
    def base_url(self, value: str):
        self._base_url = value

    @abstractmethod
    def list_models(self) -> List[str]:
        """Return available model names. May require valid API key."""

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return capability metadata for the active model."""

    def get_capabilities(self, model_name: Optional[str] = None) -> ModelCapabilities:
        """Return normalized ModelCapabilities for specified or active model."""
        target_name = model_name or self.model_name
        caps = self.capabilities()
        return ModelCapabilities(
            tool_calling=caps.native_tools,
            vision=caps.vision,
            streaming=caps.streaming,
            structured_output=True,
            context_window=caps.context_window,
            max_output_tokens=caps.max_output_tokens,
        )

    def get_model_info(self, model_name: Optional[str] = None) -> ModelInfo:
        """Return rich ModelInfo metadata for specified or active model."""
        target_name = model_name or self.model_name
        caps = self.capabilities()
        return ModelInfo(
            name=target_name,
            provider=self.provider_name,
            context_window=caps.context_window,
            max_output_tokens=caps.max_output_tokens,
            tool_calling=caps.native_tools,
            streaming=caps.streaming,
            vision=caps.vision,
            availability=self.health_check(),
        )

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = True,
    ) -> Generator[ChatChunk, None, Dict[str, Any]]:
        """
        Send messages. Yields ChatChunk objects.
        Returns final assembled message dict on StopIteration.
        """

    def normalize_tool_calls(self, raw: List[Dict]) -> List[ToolCall]:
        """Normalize provider-specific tool call format to ToolCall objects."""
        result = []
        for tc in raw:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            result.append(ToolCall(
                id=tc.get("id", "call_0"),
                name=fn.get("name", ""),
                arguments=args,
            ))
        return result

    # Optional: count/estimate tokens
    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate token count. Override for accurate counting."""
        total = sum(len(str(m.get("content", ""))) // 4 for m in messages)
        return total
