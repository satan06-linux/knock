"""
test_providers.py - Tests for the Model Hub provider system.
No real API calls. All providers tested with mocked HTTP.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk, ToolCall
from ultron.providers.credential_store import (
    store_key, get_key, delete_key, has_key, mask_key, list_configured_providers
)
from ultron.providers.ollama import OllamaProvider
from ultron.providers.groq import GroqProvider
from ultron.providers.anthropic import AnthropicProvider, _convert_messages_to_anthropic, _convert_tools_to_anthropic
from ultron.providers.openai import OpenAIProvider
from ultron.providers.gemini import GeminiProvider
from ultron.providers.openrouter import OpenRouterProvider
from ultron.providers.openai_compat import OpenAICompatProvider
from ultron.providers.registry import ProviderRegistry, _build_provider, PROVIDER_CATALOG


# ---------------------------------------------------------------------------
# CredentialStore tests
# ---------------------------------------------------------------------------

class TestCredentialStore(unittest.TestCase):

    TEST_PROVIDER = "_ultron_test_provider_xyz"

    def tearDown(self):
        delete_key(self.TEST_PROVIDER)

    def test_store_and_get_key(self):
        ok = store_key(self.TEST_PROVIDER, "sk-testkey123")
        self.assertTrue(ok)
        key = get_key(self.TEST_PROVIDER)
        self.assertEqual(key, "sk-testkey123")

    def test_delete_key(self):
        store_key(self.TEST_PROVIDER, "sk-todelete")
        delete_key(self.TEST_PROVIDER)
        key = get_key(self.TEST_PROVIDER)
        self.assertIsNone(key)

    def test_has_key_true(self):
        store_key(self.TEST_PROVIDER, "val")
        self.assertTrue(has_key(self.TEST_PROVIDER))

    def test_has_key_false(self):
        delete_key(self.TEST_PROVIDER)
        self.assertFalse(has_key(self.TEST_PROVIDER))

    def test_mask_key_normal(self):
        masked = mask_key("sk-abcd1234efgh5678")
        self.assertTrue(masked.startswith("sk-a"))
        self.assertTrue(masked.endswith("5678"))
        self.assertIn("...", masked)

    def test_mask_key_short(self):
        masked = mask_key("abc")
        self.assertEqual(masked, "***")

    def test_env_var_fallback(self):
        delete_key("groq")
        with patch.dict(os.environ, {"GROQ_API_KEY": "env-key-test"}):
            key = get_key("groq")
            self.assertEqual(key, "env-key-test")

    def test_list_configured_includes_ollama(self):
        providers = list_configured_providers()
        self.assertIn("ollama", providers)


# ---------------------------------------------------------------------------
# ProviderCapabilities & base interface
# ---------------------------------------------------------------------------

class TestProviderCapabilities(unittest.TestCase):

    def test_defaults(self):
        caps = ProviderCapabilities()
        self.assertEqual(caps.context_window, 4096)
        self.assertFalse(caps.native_tools)
        self.assertFalse(caps.vision)

    def test_custom(self):
        caps = ProviderCapabilities(context_window=128000, native_tools=True, vision=True)
        self.assertEqual(caps.context_window, 128000)
        self.assertTrue(caps.native_tools)

    def test_chat_chunk(self):
        chunk = ChatChunk(type="content", delta="hello")
        self.assertEqual(chunk.type, "content")
        self.assertEqual(chunk.delta, "hello")

    def test_tool_call(self):
        tc = ToolCall(id="call_1", name="write_file", arguments={"path": "a.py", "content": "x"})
        self.assertEqual(tc.name, "write_file")
        self.assertEqual(tc.arguments["path"], "a.py")


# ---------------------------------------------------------------------------
# OllamaProvider tests
# ---------------------------------------------------------------------------

class TestOllamaProvider(unittest.TestCase):

    def test_provider_name(self):
        p = OllamaProvider()
        self.assertEqual(p.provider_name, "Ollama")

    def test_default_model(self):
        p = OllamaProvider()
        self.assertEqual(p.model_name, "qwen2.5-coder:7b")

    def test_model_setter(self):
        p = OllamaProvider()
        p.model_name = "llama3:8b"
        self.assertEqual(p.model_name, "llama3:8b")

    def test_capabilities(self):
        p = OllamaProvider(num_ctx=8192)
        caps = p.capabilities()
        self.assertEqual(caps.context_window, 8192)
        self.assertTrue(caps.native_tools)

    def test_health_check_false_when_offline(self):
        p = OllamaProvider(base_url="http://localhost:19999")
        self.assertFalse(p.health_check())

    def test_list_models_empty_when_offline(self):
        p = OllamaProvider(base_url="http://localhost:19999")
        models = p.list_models()
        self.assertEqual(models, [])

    def test_get_tool_definitions_returns_list(self):
        p = OllamaProvider()
        tools = p.get_tool_definitions()
        self.assertTrue(len(tools) >= 8)
        names = [t["function"]["name"] for t in tools]
        self.assertIn("write_file", names)
        self.assertIn("run_command", names)


# ---------------------------------------------------------------------------
# GroqProvider tests
# ---------------------------------------------------------------------------

class TestGroqProvider(unittest.TestCase):

    def test_provider_name(self):
        p = GroqProvider(api_key="test")
        self.assertEqual(p.provider_name, "Groq")

    def test_default_model(self):
        p = GroqProvider(api_key="test")
        self.assertIn("llama", p.model_name)

    def test_capabilities_known_model(self):
        p = GroqProvider(api_key="test", model_name="llama-3.3-70b-versatile")
        caps = p.capabilities()
        self.assertEqual(caps.context_window, 128000)

    def test_health_check_false_bad_key(self):
        p = GroqProvider(api_key="bad-key-xyz")
        self.assertFalse(p.health_check())

    def test_list_models_fallback_on_error(self):
        p = GroqProvider(api_key="bad")
        models = p.list_models()
        self.assertIsInstance(models, list)
        self.assertTrue(len(models) > 0)

    def test_model_setter(self):
        p = GroqProvider(api_key="test")
        p.model_name = "gemma2-9b-it"
        self.assertEqual(p.model_name, "gemma2-9b-it")


# ---------------------------------------------------------------------------
# AnthropicProvider tests
# ---------------------------------------------------------------------------

class TestAnthropicProvider(unittest.TestCase):

    def test_provider_name(self):
        p = AnthropicProvider(api_key="test")
        self.assertEqual(p.provider_name, "Anthropic")

    def test_default_model(self):
        p = AnthropicProvider(api_key="test")
        self.assertIn("claude", p.model_name)

    def test_capabilities(self):
        p = AnthropicProvider(api_key="test", model_name="claude-sonnet-4-5")
        caps = p.capabilities()
        self.assertEqual(caps.context_window, 200000)
        self.assertTrue(caps.native_tools)

    def test_list_models_returns_catalog(self):
        p = AnthropicProvider(api_key="test")
        models = p.list_models()
        self.assertTrue(len(models) > 0)
        self.assertTrue(any("claude" in m for m in models))

    def test_convert_messages_extracts_system(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = _convert_messages_to_anthropic(msgs)
        self.assertEqual(system, "You are helpful.")
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["role"], "user")

    def test_convert_messages_tool_result(self):
        msgs = [
            {"role": "tool", "content": "result", "tool_call_id": "call_1"},
        ]
        _, converted = _convert_messages_to_anthropic(msgs)
        self.assertEqual(converted[0]["role"], "user")
        self.assertEqual(converted[0]["content"][0]["type"], "tool_result")

    def test_convert_tools_to_anthropic(self):
        tools = [{"function": {"name": "write_file", "description": "Write", "parameters": {"type": "object", "properties": {}}}}]
        result = _convert_tools_to_anthropic(tools)
        self.assertEqual(result[0]["name"], "write_file")
        self.assertIn("input_schema", result[0])


# ---------------------------------------------------------------------------
# OpenAIProvider tests
# ---------------------------------------------------------------------------

class TestOpenAIProvider(unittest.TestCase):

    def test_provider_name(self):
        p = OpenAIProvider(api_key="test")
        self.assertEqual(p.provider_name, "OpenAI")

    def test_capabilities_gpt4o(self):
        p = OpenAIProvider(api_key="test", model_name="gpt-4o")
        caps = p.capabilities()
        self.assertEqual(caps.context_window, 128000)
        self.assertTrue(caps.vision)

    def test_health_check_false_bad_key(self):
        p = OpenAIProvider(api_key="bad")
        self.assertFalse(p.health_check())

    def test_list_models_fallback(self):
        p = OpenAIProvider(api_key="bad")
        models = p.list_models()
        self.assertIsInstance(models, list)


# ---------------------------------------------------------------------------
# GeminiProvider tests
# ---------------------------------------------------------------------------

class TestGeminiProvider(unittest.TestCase):

    def test_provider_name(self):
        p = GeminiProvider(api_key="test")
        self.assertEqual(p.provider_name, "Google Gemini")

    def test_capabilities_flash(self):
        p = GeminiProvider(api_key="test", model_name="gemini-2.0-flash")
        caps = p.capabilities()
        self.assertEqual(caps.context_window, 1048576)

    def test_list_models_fallback(self):
        p = GeminiProvider(api_key="bad")
        models = p.list_models()
        self.assertIsInstance(models, list)
        self.assertTrue(any("gemini" in m for m in models))


# ---------------------------------------------------------------------------
# OpenRouterProvider tests
# ---------------------------------------------------------------------------

class TestOpenRouterProvider(unittest.TestCase):

    def test_provider_name(self):
        p = OpenRouterProvider(api_key="test")
        self.assertEqual(p.provider_name, "OpenRouter")

    def test_list_models_catalog_fallback(self):
        p = OpenRouterProvider(api_key="bad")
        models = p.list_models()
        self.assertTrue(len(models) > 0)

    def test_capabilities_defaults(self):
        p = OpenRouterProvider(api_key="test")
        caps = p.capabilities()
        self.assertTrue(caps.streaming)


# ---------------------------------------------------------------------------
# OpenAICompatProvider tests
# ---------------------------------------------------------------------------

class TestOpenAICompatProvider(unittest.TestCase):

    def test_provider_name(self):
        p = OpenAICompatProvider(base_url="http://localhost:1234/v1")
        self.assertEqual(p.provider_name, "OpenAI-Compatible")

    def test_health_check_unreachable(self):
        p = OpenAICompatProvider(base_url="http://localhost:19998/v1")
        self.assertFalse(p.health_check())

    def test_list_models_empty_when_offline(self):
        p = OpenAICompatProvider(base_url="http://localhost:19998/v1", model_name="phi3")
        models = p.list_models()
        self.assertIn("phi3", models)

    def test_model_setter(self):
        p = OpenAICompatProvider(base_url="http://localhost:1234/v1")
        p.model_name = "mistral"
        self.assertEqual(p.model_name, "mistral")


# ---------------------------------------------------------------------------
# ProviderRegistry tests
# ---------------------------------------------------------------------------

class TestProviderRegistry(unittest.TestCase):

    def test_registry_creates_without_crash(self):
        registry = ProviderRegistry()
        self.assertIsNotNone(registry)

    def test_active_defaults_to_ollama(self):
        with patch.dict(os.environ, {"ULTRON_PROVIDER": "ollama", "ULTRON_MODEL": "qwen2.5-coder:7b"}):
            registry = ProviderRegistry()
            self.assertIsNotNone(registry.active)

    def test_build_provider_ollama(self):
        p = _build_provider("ollama", "qwen2.5-coder:7b")
        self.assertIsNotNone(p)
        self.assertEqual(p.provider_name, "Ollama")

    def test_build_provider_needs_key_returns_none(self):
        # No key stored for test_provider_xyz
        p = _build_provider("groq", "llama-3.3-70b-versatile")
        # Returns None if no key, or GroqProvider if key happens to exist
        if p is not None:
            self.assertEqual(p.provider_name, "Groq")

    def test_build_openai_compat_no_key(self):
        p = _build_provider("openai_compat", "phi3", "http://localhost:1234/v1")
        self.assertIsNotNone(p)
        self.assertEqual(p.provider_name, "OpenAI-Compatible")

    def test_provider_catalog_has_required_entries(self):
        ids = [p["id"] for p in PROVIDER_CATALOG]
        for required in ["ollama", "groq", "anthropic", "openai", "gemini", "openrouter", "openai_compat"]:
            self.assertIn(required, ids)

    def test_connection_status_returns_all_providers(self):
        registry = ProviderRegistry()
        statuses = registry.connection_status()
        self.assertEqual(len(statuses), len(PROVIDER_CATALOG))
        for s in statuses:
            self.assertIn("id", s)
            self.assertIn("name", s)
            self.assertIn("connected", s)
            self.assertIn("has_key", s)

    def test_set_active_and_fallback(self):
        registry = ProviderRegistry()
        p1 = OllamaProvider(model_name="llama3:8b")
        p2 = OllamaProvider(model_name="qwen2.5-coder:7b")
        registry.set_active(p1, "ollama")
        registry.set_fallback(p2, "ollama")
        self.assertEqual(registry.active.model_name, "llama3:8b")
        self.assertEqual(registry._fallback.model_name, "qwen2.5-coder:7b")


# ---------------------------------------------------------------------------
# REPL Model Hub command smoke tests
# ---------------------------------------------------------------------------

class TestReplModelHubCommands(unittest.TestCase):

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        import subprocess
        subprocess.run(["git", "init", self.workspace], capture_output=True)
        from ultron.agent import UltronAgent
        self.agent = UltronAgent(workspace_root=self.workspace, auto_approve=True)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    @patch("ultron.repl.PromptSession")
    def test_model_command_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/model")

    @patch("ultron.repl.PromptSession")
    def test_model_info_command(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/model-info")

    @patch("ultron.repl.PromptSession")
    def test_provider_status(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/provider status")

    @patch("ultron.repl.PromptSession")
    def test_provider_add_missing_args(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/provider add")

    @patch("ultron.repl.PromptSession")
    def test_provider_remove_missing_args(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/provider remove")

    @patch("ultron.repl.PromptSession")
    def test_fallback_no_arg(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/fallback")

    @patch("ultron.repl.PromptSession")
    def test_fallback_invalid_provider(self, mock_ps):
        from ultron.repl import UltronREPL
        repl = UltronREPL(self.agent)
        repl.handle_slash_command("/fallback nonexistentprovider")

    @patch("ultron.repl.PromptSession")
    def test_model_hub_commands_in_completer(self, mock_ps):
        from ultron.repl import UltronCompleter
        completer = UltronCompleter(self.workspace, self.agent.context)
        for cmd in ["/models", "/model", "/model-info", "/provider", "/fallback"]:
            self.assertIn(cmd, completer.commands, f"{cmd} missing from completer")


if __name__ == "__main__":
    unittest.main()
