# Contributing to Ultron CLI

Thank you for your interest in contributing to Ultron. This guide covers how to add new providers, tools, slash commands, and tests.

---

## Setup

```bash
git clone https://github.com/your-username/ultron.git
cd ultron
pip install -r requirements.txt
pip install -e .
python -m pytest tests/ -q   # should show 381+ passed
```

---

## Code Style

- Python 3.11+
- Type hints on all function signatures
- Dataclasses for structured data (not plain dicts)
- Enums for fixed value sets
- All error paths handled with meaningful messages (never silent failures)
- No bare `except:` — always `except Exception as e:`

---

## Adding a New Provider

1. Create `ultron/providers/myprovider.py`:

```python
from ultron.providers.base import ModelProvider, ProviderCapabilities, ChatChunk

class MyProvider(ModelProvider):

    def __init__(self, api_key: str, model_name: str = "my-default-model"):
        self._api_key = api_key
        self._model_name = model_name

    @property
    def provider_name(self) -> str:
        return "MyProvider"

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str):
        self._model_name = value

    def health_check(self) -> bool:
        # Check if API key is valid
        ...

    def list_models(self) -> list:
        # Return available model names
        ...

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(context_window=128000, streaming=True, native_tools=True)

    def chat(self, messages, tools=None, stream=True):
        # Yield ChatChunk objects, return final assembled message
        ...
```

2. Add to `PROVIDER_CATALOG` in `ultron/providers/registry.py`
3. Add `_build_provider()` case
4. Add tests in `tests/test_providers.py`

---

## Adding a New Tool

1. Register in `ToolRegistry.build_default()` in `ultron/tool_registry.py`:

```python
ToolDefinition(
    name="my_tool",
    description="What this tool does.",
    schema={
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "..."}
        },
        "required": ["param"]
    },
    risk_level=RiskLevel.READ_ONLY,  # or WORKSPACE_WRITE, GIT_WRITE, etc.
)
```

2. Add validation in `validate_tool_args()` in `ultron/agent.py`
3. Add executor in `agent._execute_tool_with_confirmation()`
4. Add to `get_tool_definitions()` in `ultron/models.py` (Ollama schema)
5. Add tests

---

## Adding a New Slash Command

1. Add to `UltronCompleter.commands` list in `ultron/repl.py`
2. Add routing:
```python
elif cmd == "/mycommand":
    self._cmd_mycommand(arg)
```
3. Implement handler before `start()`:
```python
def _cmd_mycommand(self, arg: str):
    """Brief description."""
    if not arg:
        self.console.print("[red]Usage: /mycommand <arg>[/red]")
        return
    # implementation
```
4. Add to help table in `print_help()`

---

## Adding a Plugin (user-level)

Place a `.py` file in `~/.ultron/plugins/`:

```python
# ~/.ultron/plugins/my_plugin.py
from ultron.tool_registry import ToolDefinition, RiskLevel

def _execute(args):
    return f"Result: {args.get('input', '')}"

ULTRON_TOOLS = [
    ToolDefinition(
        name="my_plugin_tool",
        description="My custom plugin tool.",
        schema={"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
        risk_level=RiskLevel.READ_ONLY,
        executor=_execute,
    )
]
```

---

## Test Requirements

Every PR must:
1. Pass all existing tests: `python -m pytest tests/ -q`
2. Add tests for new functionality
3. Not introduce new test failures

Test file conventions:
- `tests/test_phase*.py` — phase-specific integration tests
- `tests/test_providers.py` — provider tests (mock HTTP, no real API calls)
- `tests/test_maturity.py` — workstream A/C/E/F tests
- `tests/verify_agent.py` — mocked agent integration tests

For providers: mock all HTTP calls. No real API keys in tests.

---

## PR Checklist

- [ ] All 381+ tests pass
- [ ] New tests added for new functionality
- [ ] Type hints on all new functions
- [ ] No hardcoded API keys or secrets
- [ ] `plan.txt` updated if addressing a planned item
- [ ] README updated if adding user-visible feature
- [ ] ARCHITECTURE.md updated if adding new module

---

## Questions?

Open a GitHub Discussion or file an issue with the `question` label.
