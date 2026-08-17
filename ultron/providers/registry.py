"""
registry.py - Provider registry, interactive picker, health checks, fallback routing.
"""
import os
from typing import Optional, List, Dict, Any, Tuple

from ultron.providers.base import ModelProvider, ProviderCapabilities
from ultron.providers.credential_store import (
    get_key, store_key, delete_key, has_key, list_configured_providers, mask_key
)

# Provider catalog shown in /models picker
PROVIDER_CATALOG = [
    {
        "id": "ollama",
        "name": "Ollama",
        "description": "Local inference, no API key needed",
        "needs_key": False,
        "needs_url": False,
        "default_model": "qwen2.5-coder:7b",
    },
    {
        "id": "groq",
        "name": "Groq",
        "description": "Fast cloud inference, free tier available",
        "needs_key": True,
        "needs_url": False,
        "default_model": "llama-3.3-70b-versatile",
        "key_url": "https://console.groq.com/keys",
    },
    {
        "id": "anthropic",
        "name": "Anthropic / Claude",
        "description": "Claude Sonnet, Haiku, Opus",
        "needs_key": True,
        "needs_url": False,
        "default_model": "claude-sonnet-4-5",
        "key_url": "https://console.anthropic.com/",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "GPT-4o, GPT-4o-mini, o1",
        "needs_key": True,
        "needs_url": False,
        "default_model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "description": "Gemini 2.0 Flash, 1.5 Pro",
        "needs_key": True,
        "needs_url": False,
        "default_model": "gemini-2.0-flash",
        "key_url": "https://aistudio.google.com/app/apikey",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "200+ models via one API key",
        "needs_key": True,
        "needs_url": False,
        "default_model": "anthropic/claude-haiku-3-5",
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "id": "openai_compat",
        "name": "OpenAI-Compatible Server",
        "description": "LM Studio, vLLM, LocalAI, Jan",
        "needs_key": False,
        "needs_url": True,
        "default_model": "",
    },
]


def _build_provider(provider_id: str, model_name: str, base_url: str = "", api_key_override: Optional[str] = None) -> Optional[ModelProvider]:
    """Instantiate provider by ID."""
    key = api_key_override or (get_key(provider_id) if provider_id != "ollama" else None)

    if provider_id == "ollama":
        from ultron.providers.ollama import OllamaProvider
        url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return OllamaProvider(model_name=model_name, base_url=url)

    elif provider_id == "groq":
        if not key:
            return None
        from ultron.providers.groq import GroqProvider
        return GroqProvider(api_key=key, model_name=model_name)

    elif provider_id == "anthropic":
        if not key:
            return None
        from ultron.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=key, model_name=model_name)

    elif provider_id == "openai":
        if not key:
            return None
        from ultron.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key=key, model_name=model_name)

    elif provider_id == "gemini":
        if not key:
            return None
        from ultron.providers.gemini import GeminiProvider
        return GeminiProvider(api_key=key, model_name=model_name)

    elif provider_id == "openrouter":
        if not key:
            return None
        from ultron.providers.openrouter import OpenRouterProvider
        return OpenRouterProvider(api_key=key, model_name=model_name)

    elif provider_id == "openai_compat":
        from ultron.providers.openai_compat import OpenAICompatProvider
        api_key = api_key_override or get_key("openai_compat") or "local"
        return OpenAICompatProvider(base_url=base_url, model_name=model_name, api_key=api_key)

    return None


def _get_model_display_list(provider_id: str) -> List[Tuple[str, str]]:
    """Return [(model_id, display_name)] for a provider's model picker."""
    if provider_id == "groq":
        from ultron.providers.groq import GROQ_MODELS
        return [(m, m) for m in GROQ_MODELS]
    elif provider_id == "anthropic":
        from ultron.providers.anthropic import ANTHROPIC_DISPLAY
        return ANTHROPIC_DISPLAY
    elif provider_id == "openai":
        from ultron.providers.openai import OPENAI_DISPLAY
        return OPENAI_DISPLAY
    elif provider_id == "gemini":
        from ultron.providers.gemini import GEMINI_DISPLAY
        return GEMINI_DISPLAY
    elif provider_id == "openrouter":
        from ultron.providers.openrouter import OPENROUTER_CATALOG
        return OPENROUTER_CATALOG
    return []


class ProviderRegistry:
    """
    Manages the active provider, fallback provider,
    interactive picker, and connection status.
    """

    def __init__(self):
        self._active: Optional[ModelProvider] = None
        self._fallback: Optional[ModelProvider] = None
        self._active_provider_id: str = "ollama"
        self._fallback_provider_id: Optional[str] = None

        # Load last-used provider from env var (non-secret, just provider ID + model)
        saved_id = os.environ.get("ULTRON_PROVIDER", "ollama")
        saved_model = os.environ.get("ULTRON_MODEL", "qwen2.5-coder:7b")
        saved_url = os.environ.get("ULTRON_BASE_URL", "")
        self._active = _build_provider(saved_id, saved_model, saved_url)
        if self._active:
            self._active_provider_id = saved_id

        # Always keep Ollama as fallback if active is not Ollama
        if saved_id != "ollama":
            from ultron.providers.ollama import OllamaProvider
            self._fallback = OllamaProvider()
        self._status_cache: Optional[List[Dict[str, Any]]] = None
        self._status_cache_time: float = 0.0

    @property
    def active(self) -> Optional[ModelProvider]:
        return self._active

    def set_active(self, provider: ModelProvider, provider_id: str):
        self._active = provider
        self._active_provider_id = provider_id

    def set_fallback(self, provider: ModelProvider, provider_id: str):
        self._fallback = provider
        self._fallback_provider_id = provider_id

    def list_models(self, provider_id: Optional[str] = None) -> List[str]:
        """List model names for specified provider ID or active provider."""
        if provider_id and provider_id != self._active_provider_id:
            prov = _build_provider(provider_id, "default")
            return prov.list_models() if prov else []
        return self._active.list_models() if self._active else []

    def get_capabilities(self, provider_id: Optional[str] = None, model_name: Optional[str] = None) -> Any:
        """Get normalized ModelCapabilities for specified provider and model via ProviderRegistry."""
        from ultron.providers.base import ModelCapabilities
        if provider_id and provider_id != self._active_provider_id:
            prov = _build_provider(provider_id, model_name or "default")
            return prov.get_capabilities(model_name) if prov else ModelCapabilities()
        return self._active.get_capabilities(model_name) if self._active else ModelCapabilities()

    def connection_status(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Return status of all configured providers (with 30s TTL cache)."""
        import time
        now = time.time()
        if not force_refresh and self._status_cache and (now - self._status_cache_time < 30.0):
            # Update active flag in cached results
            for item in self._status_cache:
                item["active"] = (self._active_provider_id == item["id"])
            return self._status_cache

        statuses = []
        for p in PROVIDER_CATALOG:
            pid = p["id"]
            if pid == "ollama":
                from ultron.providers.ollama import OllamaProvider
                provider = OllamaProvider()
                alive = provider.health_check()
                models = provider.list_models() if alive else []
                statuses.append({
                    "id": pid, "name": p["name"],
                    "connected": alive,
                    "has_key": True,
                    "active": self._active_provider_id == pid,
                    "model_count": len(models),
                })
            elif has_key(pid):
                prov = _build_provider(pid, p["default_model"])
                alive = prov.health_check() if prov else False
                statuses.append({
                    "id": pid, "name": p["name"],
                    "connected": alive,
                    "has_key": True,
                    "active": self._active_provider_id == pid,
                    "key_masked": mask_key(get_key(pid) or ""),
                })
            else:
                statuses.append({
                    "id": pid, "name": p["name"],
                    "connected": False,
                    "has_key": False,
                    "active": False,
                })
        self._status_cache = statuses
        self._status_cache_time = now
        return statuses

    def interactive_pick(self, console) -> Optional[ModelProvider]:
        """
        Full interactive provider + model picker via Rich console.
        Returns selected provider or None if cancelled.
        """
        from rich.panel import Panel
        from rich.prompt import Prompt
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.formatted_text import HTML

        # ── Step 1: Pick provider ──
        console.print("\n[bold magenta]╔══ Ultron Model Hub ══╗[/bold magenta]\n")
        for i, p in enumerate(PROVIDER_CATALOG, 1):
            configured = has_key(p["id"]) or p["id"] in ("ollama", "openai_compat")
            tag = "[green]✓[/green]" if configured else "[dim]○[/dim]"
            active_tag = " [bold yellow]← active[/bold yellow]" if self._active_provider_id == p["id"] else ""
            console.print(f"  {tag} [cyan]{i}.[/cyan] [bold]{p['name']}[/bold]  [dim]{p['description']}[/dim]{active_tag}")

        console.print("\n  [dim]0. Cancel[/dim]")
        choice = Prompt.ask("\n[bold yellow]Select provider[/bold yellow]", default="0")

        if not choice.isdigit() or int(choice) == 0:
            console.print("[yellow]Cancelled.[/yellow]")
            return None

        idx = int(choice) - 1
        if idx < 0 or idx >= len(PROVIDER_CATALOG):
            console.print("[red]Invalid choice.[/red]")
            return None

        selected_provider = PROVIDER_CATALOG[idx]
        pid = selected_provider["id"]

        # ── Step 2: API key (if needed) ──
        base_url = ""
        if selected_provider["needs_url"]:
            base_url = Prompt.ask("[bold yellow]Enter server URL (e.g. http://localhost:1234/v1)[/bold yellow]")
            if not base_url:
                console.print("[red]URL required.[/red]")
                return None

        if selected_provider["needs_key"]:
            existing = has_key(pid)
            if existing:
                console.print(f"[green]✓ API key already stored for {selected_provider['name']}.[/green]")
                reenter = Prompt.ask("[bold yellow]Enter new key? (press Enter to keep existing)[/bold yellow]", default="")
                if reenter.strip():
                    store_key(pid, reenter.strip())
                    console.print("[green]* Key updated.[/green]")
            else:
                key_url = selected_provider.get("key_url", "")
                if key_url:
                    console.print(f"[dim]Get your API key at: {key_url}[/dim]")
                # Hidden input
                try:
                    api_key = pt_prompt(HTML("<ansiyellow>Paste API key (hidden): </ansiyellow>"), is_password=True).strip()
                except Exception:
                    import getpass
                    api_key = getpass.getpass("Paste API key (hidden): ").strip()

                if not api_key:
                    console.print("[red]No key entered. Cancelled.[/red]")
                    return None

                # Test key
                console.print("[cyan]Testing key...[/cyan]")
                tmp = _build_provider(pid, selected_provider["default_model"], base_url, api_key_override=api_key)
                if tmp and tmp.health_check():
                    store_key(pid, api_key)
                    console.print(f"[green]✓ Connected. Key saved securely to OS keyring.[/green]")
                else:
                    console.print(f"[red]✗ Connection failed. Key not saved.[/red]")
                    return None

        # ── Step 3: Pick model ──
        model_list = _get_model_display_list(pid)

        if pid == "ollama":
            from ultron.providers.ollama import OllamaProvider
            tmp_ollama = OllamaProvider(base_url=base_url or "http://localhost:11434")
            model_list = [(m, m) for m in tmp_ollama.list_models()]
            if not model_list:
                console.print("[yellow]No Ollama models found. Pull one with: ollama pull qwen2.5-coder:7b[/yellow]")
                return None
        elif pid == "openai_compat":
            tmp_compat = _build_provider(pid, "", base_url)
            fetched = tmp_compat.list_models() if tmp_compat else []
            model_list = [(m, m) for m in fetched] if fetched else []
            if not model_list:
                manual = Prompt.ask("[bold yellow]Enter model name manually[/bold yellow]")
                if manual:
                    model_list = [(manual, manual)]
                else:
                    console.print("[red]No models found.[/red]")
                    return None

        if model_list:
            console.print(f"\n[bold white]Available {selected_provider['name']} models:[/bold white]")
            for i, (mid, mdisplay) in enumerate(model_list, 1):
                console.print(f"  [cyan]{i}.[/cyan] {mdisplay}")
            console.print()
            mchoice = Prompt.ask("[bold yellow]Select model[/bold yellow]", default="1")
            if not mchoice.isdigit() or int(mchoice) < 1 or int(mchoice) > len(model_list):
                console.print("[red]Invalid model choice.[/red]")
                return None
            model_name = model_list[int(mchoice) - 1][0]
        else:
            model_name = selected_provider["default_model"]

        # ── Step 4: Build + verify ──
        provider = _build_provider(pid, model_name, base_url)
        if not provider:
            console.print(f"[red]Failed to initialize provider.[/red]")
            return None

        console.print(f"\n[bold green]✓ Active model: [cyan]{provider.provider_name}[/cyan] / [cyan]{model_name}[/cyan][/bold green]\n")

        self.set_active(provider, pid)

        # Set Ollama as fallback if switching away from it
        if pid != "ollama":
            from ultron.providers.ollama import OllamaProvider
            fallback = OllamaProvider()
            if fallback.health_check():
                self.set_fallback(fallback, "ollama")
                console.print("[dim]Ollama set as fallback.[/dim]")

        return provider

    def handle_failure(self, error_msg: str, console) -> Optional[ModelProvider]:
        """
        Called when the active provider fails mid-session.
        Shows recovery options and returns a working provider or None.
        """
        from rich.prompt import Prompt

        console.print(f"\n[bold red]Provider failure: {error_msg}[/bold red]")
        console.print("\n[bold white]Recovery options:[/bold white]")
        console.print("  [cyan]1.[/cyan] Retry current provider")
        console.print("  [cyan]2.[/cyan] Switch provider (/models)")
        console.print("  [cyan]3.[/cyan] Use local Ollama fallback")
        console.print("  [cyan]4.[/cyan] Continue in read-only mode (ask/plan only)")

        choice = Prompt.ask("[bold yellow]Choose[/bold yellow]", default="3")

        if choice == "1":
            if self._active and self._active.health_check():
                console.print("[green]✓ Provider reconnected.[/green]")
                return self._active
            console.print("[red]Still unavailable.[/red]")
            return None

        elif choice == "2":
            return self.interactive_pick(console)

        elif choice == "3":
            from ultron.providers.ollama import OllamaProvider
            ollama = OllamaProvider()
            if ollama.health_check():
                self.set_active(ollama, "ollama")
                console.print("[green]✓ Switched to local Ollama.[/green]")
                return ollama
            console.print("[red]Ollama not available locally.[/red]")
            return None

        elif choice == "4":
            console.print("[yellow]Continuing in read-only mode. Use /models to reconnect.[/yellow]")
            return None

        return None


# ---------------------------------------------------------------------------
# Smart routing policy
# ---------------------------------------------------------------------------

# Task types that can use a fast/cheap model
FAST_TASK_TYPES = {
    "ask", "analyze", "review",          # read-only
    "commit_message", "changelog",        # lightweight text generation
    "log_summary", "classification",      # cheap ops
}

# Task types that need the strong model
STRONG_TASK_TYPES = {
    "feature", "refactor", "debug",       # complex edits
    "test",                               # test writing needs context
    "setup",                              # project setup
}


def get_model_for_task(task_type: str, registry: "ProviderRegistry") -> Optional[ModelProvider]:
    """
    Returns the appropriate provider for a given task type.
    Fast tasks → use active provider if it's Groq/Haiku, or fallback light model.
    Strong tasks → always use active provider.
    If only one provider configured, always return it.
    """
    active = registry.active
    if not active:
        return registry._fallback

    # If task is fast and active is a strong expensive model,
    # check if fallback is faster
    if task_type in FAST_TASK_TYPES:
        fallback = registry._fallback
        if fallback and fallback.provider_name in ("Ollama", "Groq"):
            if fallback.health_check():
                return fallback

    # Default: use active provider for everything
    return active
