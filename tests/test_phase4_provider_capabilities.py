"""
test_phase4_provider_capabilities.py - Unit tests for Phase 4 Provider Capability Discovery.
"""
import pytest
from ultron.providers.base import ModelCapabilities, ModelInfo
from ultron.providers.registry import ProviderRegistry
from ultron.providers.ollama import OllamaProvider


def test_model_capabilities_dataclass_frozen():
    caps = ModelCapabilities(tool_calling=True, vision=False, streaming=True)
    assert caps.tool_calling is True
    assert caps.vision is False
    with pytest.raises(AttributeError):
        caps.tool_calling = False  # Frozen dataclass immutability


def test_ollama_provider_get_capabilities():
    provider = OllamaProvider(model_name="qwen2.5-coder:7b")
    caps = provider.get_capabilities()
    assert isinstance(caps, ModelCapabilities)
    assert caps.streaming is True


def test_provider_registry_capability_discovery():
    registry = ProviderRegistry()
    caps = registry.get_capabilities()
    assert isinstance(caps, ModelCapabilities)
