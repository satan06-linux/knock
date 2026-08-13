"""
credential_store.py - Secure API key storage using OS keyring.
Keys NEVER touch files, logs, git, or .ultron.toml.
Falls back to environment variables as read-only alternative.
"""
import os
from typing import Optional

try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

# Keyring service name prefix
_SERVICE = "ultron_cli"

# Map provider name -> env var name (read-only fallback)
_ENV_VAR_MAP = {
    "openai":     "OPENAI_API_KEY",
    "anthropic":  "ANTHROPIC_API_KEY",
    "groq":       "GROQ_API_KEY",
    "gemini":     "GEMINI_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def store_key(provider: str, api_key: str) -> bool:
    """
    Store API key in OS keyring.
    Returns True on success, False if keyring unavailable.
    """
    if not _KEYRING_AVAILABLE:
        return False
    try:
        keyring.set_password(_SERVICE, provider.lower(), api_key)
        return True
    except Exception:
        return False


def get_key(provider: str) -> Optional[str]:
    """
    Retrieve API key. Priority:
      1. OS keyring
      2. Environment variable (read-only fallback)
    Returns None if not found.
    """
    provider_lower = provider.lower()

    # 1. Try keyring
    if _KEYRING_AVAILABLE:
        try:
            key = keyring.get_password(_SERVICE, provider_lower)
            if key:
                return key
        except Exception:
            pass

    # 2. Try environment variable
    env_var = _ENV_VAR_MAP.get(provider_lower)
    if env_var:
        val = os.environ.get(env_var)
        if val:
            return val

    return None


def delete_key(provider: str) -> bool:
    """Remove a stored API key from keyring."""
    if not _KEYRING_AVAILABLE:
        return False
    try:
        keyring.delete_password(_SERVICE, provider.lower())
        return True
    except Exception:
        return False


def has_key(provider: str) -> bool:
    """Check if a key exists without revealing it."""
    return get_key(provider) is not None


def list_configured_providers() -> list:
    """Return list of providers that have a key stored (keyring or env var)."""
    all_providers = ["ollama", "openai", "anthropic", "groq", "gemini", "openrouter", "openai_compat"]
    configured = []
    for p in all_providers:
        if p == "ollama":
            configured.append(p)  # Ollama never needs a key
        elif has_key(p):
            configured.append(p)
    return configured


def mask_key(key: str) -> str:
    """Return a masked version for display: sk-...abcd"""
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "..." + key[-4:]
