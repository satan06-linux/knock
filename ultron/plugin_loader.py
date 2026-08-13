"""
plugin_loader.py - Dynamic plugin system for Ultron.
Scans ~/.ultron/plugins/*.py and registers custom tools and providers.
"""
import os
import sys
import importlib.util
from typing import List, Any


PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".ultron", "plugins")


def discover_plugins() -> List[str]:
    """Return list of plugin file paths found in ~/.ultron/plugins/"""
    if not os.path.isdir(PLUGIN_DIR):
        return []
    return [
        os.path.join(PLUGIN_DIR, f)
        for f in os.listdir(PLUGIN_DIR)
        if f.endswith(".py") and not f.startswith("_")
    ]


def load_plugin(path: str) -> Any:
    """Load a single plugin module from file path."""
    module_name = f"ultron_plugin_{os.path.splitext(os.path.basename(path))[0]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        print(f"[plugin] Failed to load {path}: {e}")
        return None


def register_all_plugins(tool_registry=None, provider_registry=None) -> List[str]:
    """
    Discover and register all plugins.
    Returns list of successfully loaded plugin names.

    Plugin API:
      To register a custom tool, define in your plugin file:
        ULTRON_TOOLS = [ToolDefinition(...), ...]

      To register a custom provider, define:
        ULTRON_PROVIDERS = [("my_provider", MyProviderClass), ...]
    """
    loaded = []
    paths = discover_plugins()

    for path in paths:
        module = load_plugin(path)
        if not module:
            continue

        name = os.path.splitext(os.path.basename(path))[0]

        # Register custom tools
        if tool_registry and hasattr(module, "ULTRON_TOOLS"):
            for tool_def in module.ULTRON_TOOLS:
                try:
                    tool_registry.register(tool_def)
                    print(f"[plugin] Registered tool: {tool_def.name} (from {name})")
                except Exception as e:
                    print(f"[plugin] Failed to register tool from {name}: {e}")

        # Register custom providers
        if provider_registry and hasattr(module, "ULTRON_PROVIDERS"):
            for provider_id, provider_cls in module.ULTRON_PROVIDERS:
                try:
                    # Add to registry catalog as custom entry
                    from ultron.providers.registry import PROVIDER_CATALOG
                    PROVIDER_CATALOG.append({
                        "id": provider_id,
                        "name": provider_cls.__name__,
                        "description": f"Plugin provider: {name}",
                        "needs_key": getattr(provider_cls, "needs_key", True),
                        "needs_url": getattr(provider_cls, "needs_url", False),
                        "default_model": getattr(provider_cls, "default_model", ""),
                    })
                    print(f"[plugin] Registered provider: {provider_id} (from {name})")
                except Exception as e:
                    print(f"[plugin] Failed to register provider from {name}: {e}")

        loaded.append(name)

    return loaded


def create_plugin_template(name: str) -> str:
    """Generate a template plugin file."""
    return f'''"""
{name}.py - Ultron Plugin
Place this file in ~/.ultron/plugins/{name}.py
"""
from ultron.tool_registry import ToolDefinition, RiskLevel

# Define custom tools
ULTRON_TOOLS = [
    ToolDefinition(
        name="{name}_tool",
        description="My custom tool description.",
        schema={{
            "type": "object",
            "properties": {{
                "input": {{"type": "string", "description": "Tool input"}}
            }},
            "required": ["input"]
        }},
        risk_level=RiskLevel.READ_ONLY,
        executor=None,  # Set to a callable: executor(args) -> str
    )
]

# Optionally define custom providers
# ULTRON_PROVIDERS = [("my_provider", MyProviderClass)]
'''
