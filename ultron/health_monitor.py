"""
health_monitor.py - P1.5: HealthMonitor.
Checks all Ultron subsystems at startup and on demand.
Delegates from /doctor command. Emits health events to EventBus.
"""
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Any


@dataclass
class HealthCheck:
    component: str
    status: str        # "ok" | "warn" | "degraded" | "error"
    detail: str
    critical: bool = False   # if True, Ultron cannot function without this


class HealthMonitor:
    """
    Checks all Ultron subsystems and reports their health.
    Used at startup (auto) and by /doctor command (on-demand).
    """

    def __init__(self, workspace_root: str, agent=None):
        self.workspace_root = workspace_root
        self.agent = agent

    def check_all(self) -> List[HealthCheck]:
        checks = []
        checks += self._check_imports()
        checks += self._check_filesystem()
        checks += self._check_tool_registry()
        checks += self._check_policy_engine()
        checks += self._check_provider()
        checks += self._check_git()
        checks += self._check_python()
        return checks

    def _check_imports(self) -> List[HealthCheck]:
        results = []
        required = [
            ("ultron.tool_registry",  "ToolRegistry"),
            ("ultron.tool_executor",  "ToolExecutor"),
            ("ultron.scope_manager",  "ScopeManager"),
            ("ultron.secret_redactor","SecretRedactor"),
            ("ultron.trust_boundary", "TrustBoundary"),
            ("ultron.audit",          "AuditLogger"),
            ("ultron.task",           "TaskRouter"),
        ]
        for module, symbol in required:
            try:
                mod = __import__(module, fromlist=[symbol])
                getattr(mod, symbol)
                results.append(HealthCheck(f"import:{module}", "ok", f"{symbol} importable"))
            except Exception as e:
                results.append(HealthCheck(f"import:{module}", "error", str(e), critical=True))
        return results

    def _check_filesystem(self) -> List[HealthCheck]:
        results = []
        # Workspace readable
        if os.path.isdir(self.workspace_root):
            results.append(HealthCheck("filesystem:workspace", "ok", self.workspace_root))
        else:
            results.append(HealthCheck("filesystem:workspace", "error", f"Not a directory: {self.workspace_root}", critical=True))

        # ~/.ultron writable
        ultron_dir = os.path.join(os.path.expanduser("~"), ".ultron")
        try:
            os.makedirs(ultron_dir, exist_ok=True)
            test_path = os.path.join(ultron_dir, ".write_test")
            with open(test_path, "w") as f:
                f.write("test")
            os.remove(test_path)
            results.append(HealthCheck("filesystem:ultron_dir", "ok", ultron_dir))
        except Exception as e:
            results.append(HealthCheck("filesystem:ultron_dir", "warn", str(e)))

        return results

    def _check_tool_registry(self) -> List[HealthCheck]:
        try:
            from ultron.tool_registry import ToolRegistry
            registry = ToolRegistry.build_default()
            count = len(registry.all_names())
            if count >= 8:
                return [HealthCheck("tool_registry", "ok", f"{count} tools registered")]
            return [HealthCheck("tool_registry", "warn", f"Only {count} tools found (expected 8+)")]
        except Exception as e:
            return [HealthCheck("tool_registry", "error", str(e), critical=True)]

    def _check_policy_engine(self) -> List[HealthCheck]:
        try:
            from ultron.tool_registry import PolicyEngine, RiskLevel
            engine = PolicyEngine(auto_approve=False)
            decision = engine.evaluate("view_file", RiskLevel.READ_ONLY, "build")
            if decision.decision.value == "allow":
                return [HealthCheck("policy_engine", "ok", "Responding correctly (read-only → allow)")]
            return [HealthCheck("policy_engine", "warn", f"Unexpected decision: {decision.decision}")]
        except Exception as e:
            return [HealthCheck("policy_engine", "error", str(e), critical=True)]

    def _check_provider(self) -> List[HealthCheck]:
        if self.agent:
            model = self.agent.model
            name = getattr(model, "provider_name", "Unknown")
            try:
                available = model.is_available()
                if available:
                    return [HealthCheck(f"provider:{name}", "ok", f"{name} / {model.model_name} reachable")]
                return [HealthCheck(f"provider:{name}", "warn", f"{name} not reachable (offline mode)")]
            except Exception as e:
                return [HealthCheck(f"provider:{name}", "warn", str(e))]
        return [HealthCheck("provider", "warn", "No agent reference — cannot check provider")]

    def _check_git(self) -> List[HealthCheck]:
        import subprocess
        try:
            r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return [HealthCheck("git", "ok", r.stdout.strip())]
            return [HealthCheck("git", "warn", "git not responding")]
        except Exception as e:
            return [HealthCheck("git", "warn", f"git not found: {e}")]

    def _check_python(self) -> List[HealthCheck]:
        ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if sys.version_info >= (3, 11):
            return [HealthCheck("python", "ok", f"Python {ver}")]
        return [HealthCheck("python", "warn", f"Python {ver} — recommend 3.11+")]

    def overall_status(self, checks: List[HealthCheck]) -> str:
        if any(c.status == "error" for c in checks):
            return "DEGRADED"
        if any(c.status in ("warn", "degraded") for c in checks):
            return "WARN"
        return "HEALTHY"
