"""
eval_suite.py - Workstream F: Measurement and Continuous Maturity.
Evaluation suite, mock provider harness, and metrics collection.
"""
import os
import json
import time
import hashlib
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class TaskMetrics:
    task_id: str
    prompt: str
    intent: str
    success: bool
    files_changed: List[str]
    commands_run: List[str]
    tool_call_count: int
    duration_seconds: float
    had_unverified: bool
    unsafe_actions: int = 0
    approval_prompts: int = 0
    context_overflows: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class MetricsCollector:
    """
    Collects and persists task metrics locally.
    Source-code recording disabled by default.
    """

    def __init__(self, workspace_root: str):
        path_hash = hashlib.md5(workspace_root.encode()).hexdigest()
        self.store_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "metrics", path_hash
        )
        os.makedirs(self.store_dir, exist_ok=True)
        self.session_metrics: List[TaskMetrics] = []

    def record(self, metrics: TaskMetrics):
        self.session_metrics.append(metrics)
        self._persist(metrics)

    def _persist(self, metrics: TaskMetrics):
        ts = metrics.timestamp.replace(":", "-").replace(".", "-")
        path = os.path.join(self.store_dir, f"{ts}_{metrics.task_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metrics.__dict__, f, indent=2)
        except Exception:
            pass

    def load_history(self, count: int = 50) -> List[Dict]:
        try:
            files = sorted(
                [f for f in os.listdir(self.store_dir) if f.endswith(".json")],
                reverse=True
            )[:count]
            entries = []
            for fname in files:
                try:
                    with open(os.path.join(self.store_dir, fname), "r") as f:
                        entries.append(json.load(f))
                except Exception:
                    pass
            return entries
        except Exception:
            return []

    def compute_summary(self) -> Dict[str, Any]:
        """Compute aggregate metrics from session data."""
        if not self.session_metrics:
            return {}

        total = len(self.session_metrics)
        succeeded = sum(1 for m in self.session_metrics if m.success)
        avg_tools = sum(m.tool_call_count for m in self.session_metrics) / total
        avg_duration = sum(m.duration_seconds for m in self.session_metrics) / total
        unverified_rate = sum(1 for m in self.session_metrics if m.had_unverified) / total

        return {
            "total_tasks": total,
            "task_completion_rate": succeeded / total,
            "avg_tool_calls_per_task": round(avg_tools, 1),
            "avg_duration_seconds": round(avg_duration, 1),
            "unverified_claim_rate": round(unverified_rate, 3),
            "total_unsafe_actions": sum(m.unsafe_actions for m in self.session_metrics),
            "total_approval_prompts": sum(m.approval_prompts for m in self.session_metrics),
        }


# ---------------------------------------------------------------------------
# Mock provider harness
# ---------------------------------------------------------------------------

class MockProviderResponse:
    """Defines a scripted response for the mock provider."""

    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[List[Dict]] = None,
        needs_clarification: bool = False,
        question: str = "",
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.needs_clarification = needs_clarification
        self.question = question


class MockProvider:
    """
    Deterministic mock model provider for CI tests.
    Returns scripted responses in sequence.
    Implements ModelProvider interface (duck typing).
    """

    def __init__(self, responses: List[MockProviderResponse]):
        self._responses = list(responses)
        self._index = 0
        self._model_name = "mock-model"
        self.base_url = "mock://localhost"
        self.temperature = 0.0
        self.call_count = 0

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, v: str):
        self._model_name = v

    @property
    def provider_name(self) -> str:
        return "Mock"

    def is_available(self) -> bool:
        return True

    def health_check(self) -> bool:
        return True

    def capabilities(self):
        from ultron.providers.base import ProviderCapabilities
        return ProviderCapabilities(context_window=128000, streaming=True, native_tools=True)

    def list_models(self) -> List[str]:
        return ["mock-model"]

    def get_tool_definitions(self) -> List[Dict]:
        return []

    def chat(self, messages, tools=None, stream=True):
        """Yield scripted chunks then return final message."""
        import json as _json
        self.call_count += 1

        if self._index >= len(self._responses):
            # Default: empty response to end loop
            yield {"type": "content", "delta": "Task complete."}
            return {"role": "assistant", "content": "Task complete."}

        resp = self._responses[self._index]
        self._index += 1

        assembled = {"role": "assistant", "content": "", "tool_calls": None}

        if resp.needs_clarification and resp.question:
            content = _json.dumps({"needs_clarification": True, "question": resp.question})
            yield {"type": "content", "delta": content}
            assembled["content"] = content
            return assembled

        if resp.content:
            yield {"type": "content", "delta": resp.content}
            assembled["content"] = resp.content

        if resp.tool_calls:
            assembled["tool_calls"] = resp.tool_calls
            yield {"type": "tool_calls", "tool_calls": resp.tool_calls}

        return assembled


# ---------------------------------------------------------------------------
# Fixture workspace builder
# ---------------------------------------------------------------------------

class FixtureWorkspace:
    """
    Builds deterministic fixture repositories for evaluation.
    Supports Python, Node, Go, Rust, monorepo scenarios.
    """

    FIXTURES = {
        "python_basic": {
            "setup.py": 'from setuptools import setup\nsetup(name="app", version="0.1.0")\n',
            "app/__init__.py": "",
            "app/service.py": (
                "class UserService:\n"
                "    def get_user(self, uid: int):\n"
                "        return {'id': uid, 'name': 'test'}\n"
                "\n"
                "    def create_user(self, name: str):\n"
                "        return {'id': 1, 'name': name}\n"
            ),
            "app/routes.py": (
                "from app.service import UserService\n"
                "\n"
                "svc = UserService()\n"
                "\n"
                "def get_user_handler(uid):\n"
                "    return svc.get_user(uid)\n"
            ),
            "tests/__init__.py": "",
            "tests/test_service.py": (
                "from app.service import UserService\n"
                "\n"
                "def test_get_user():\n"
                "    svc = UserService()\n"
                "    result = svc.get_user(1)\n"
                "    assert result['id'] == 1\n"
            ),
            "requirements.txt": "pytest>=7.0.0\n",
        },
        "node_basic": {
            "package.json": '{"name":"app","version":"1.0.0","scripts":{"test":"jest","build":"tsc"}}\n',
            "src/index.ts": "export const hello = () => 'world';\n",
            "src/__tests__/index.test.ts": (
                "import { hello } from '../index';\n"
                "test('hello returns world', () => {\n"
                "  expect(hello()).toBe('world');\n"
                "});\n"
            ),
        },
        "monorepo": {
            "packages/api/package.json": '{"name":"api","version":"1.0.0"}\n',
            "packages/api/src/index.ts": "export const api = () => 'api';\n",
            "packages/worker/setup.py": 'from setuptools import setup\nsetup(name="worker")\n',
            "packages/worker/worker.py": "def run(): pass\n",
            "packages/worker/tests/test_worker.py": "def test_run(): pass\n",
        },
    }

    def __init__(self, scenario: str = "python_basic"):
        self.scenario = scenario
        self._tmpdir: Optional[str] = None

    def __enter__(self) -> str:
        self._tmpdir = tempfile.mkdtemp(prefix=f"ultron_fixture_{self.scenario}_")
        files = self.FIXTURES.get(self.scenario, {})
        for rel_path, content in files.items():
            abs_path = os.path.join(self._tmpdir, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Init git
        import subprocess
        subprocess.run(["git", "init", self._tmpdir], capture_output=True)
        subprocess.run(["git", "-C", self._tmpdir, "config", "user.email", "eval@ultron.ai"], capture_output=True)
        subprocess.run(["git", "-C", self._tmpdir, "config", "user.name", "Ultron Eval"], capture_output=True)
        subprocess.run(["git", "-C", self._tmpdir, "add", "."], capture_output=True)
        subprocess.run(["git", "-C", self._tmpdir, "commit", "-m", "initial fixture"], capture_output=True)

        return self._tmpdir

    def __exit__(self, *args):
        if self._tmpdir and os.path.exists(self._tmpdir):
            # On Windows, git objects may be read-only — force remove
            def _force_remove(func, path, exc):
                try:
                    os.chmod(path, 0o777)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(self._tmpdir, onerror=_force_remove)
        self._tmpdir = None
