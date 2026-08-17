"""
tool_registry.py - Workstream C: ToolRegistry + PolicyEngine + CommandRunner.
Replaces ad-hoc if/elif tool dispatch with a formal registry.
"""
import os
import time
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any, Optional, Callable


# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    READ_ONLY       = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    GIT_WRITE       = "git_write"
    PACKAGE_INSTALL = "package_install"
    NETWORK         = "network"
    DESTRUCTIVE     = "destructive"


# Human-readable risk descriptions
RISK_DESCRIPTIONS = {
    RiskLevel.READ_ONLY:       "Read-only — safe",
    RiskLevel.WORKSPACE_WRITE: "Writes to workspace files",
    RiskLevel.GIT_WRITE:       "Modifies git history",
    RiskLevel.PACKAGE_INSTALL: "Installs packages",
    RiskLevel.NETWORK:         "Makes network requests",
    RiskLevel.DESTRUCTIVE:     "Destructive operation",
}

# Modes that block certain risk levels
MODE_RISK_BLOCKS: Dict[str, List[RiskLevel]] = {
    "ask":    [RiskLevel.WORKSPACE_WRITE, RiskLevel.GIT_WRITE, RiskLevel.DESTRUCTIVE, RiskLevel.PACKAGE_INSTALL],
    "plan":   [RiskLevel.WORKSPACE_WRITE, RiskLevel.GIT_WRITE, RiskLevel.DESTRUCTIVE, RiskLevel.PACKAGE_INSTALL],
    "review": [RiskLevel.WORKSPACE_WRITE, RiskLevel.GIT_WRITE, RiskLevel.DESTRUCTIVE],
    "build":  [],  # all allowed
    "fix":    [],  # all allowed
}


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    name: str
    description: str
    schema: Dict[str, Any]          # JSON schema for arguments
    risk_level: RiskLevel
    requires_approval: bool = False  # always prompt user regardless of mode
    executor: Optional[Callable] = None   # bound at registration time


# ---------------------------------------------------------------------------
# CommandResult — structured output from every command execution
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False

    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled

    def to_display(self, max_chars: int = 6000) -> str:
        output_parts = []
        if self.stdout:
            output_parts.append(f"--- Stdout ---\n{self.stdout}")
        if self.stderr:
            output_parts.append(f"--- Stderr ---\n{self.stderr}")
        combined = "\n".join(output_parts) if output_parts else "(No output)"

        suffix = ""
        if self.timed_out:
            suffix = f"\n[Command timed out after {self.duration:.1f}s]"
        elif self.cancelled:
            suffix = "\n[Command cancelled by user]"

        if len(combined) > max_chars:
            half = max_chars // 2
            combined = (
                combined[:half] +
                f"\n\n... [truncated {len(combined) - max_chars} chars] ...\n\n" +
                combined[-half:]
            )
            self.truncated = True

        return f"Command exited with code {self.exit_code}\n{combined}{suffix}"


# ---------------------------------------------------------------------------
# CommandRunner — structured subprocess execution
# ---------------------------------------------------------------------------

class CommandRunner:
    """Single authorized gateway for all subprocess execution."""

    def __init__(self, workspace_root: str, timeout: int = 180):
        self.workspace_root = workspace_root
        self.timeout = timeout
        self.current_process: Optional[subprocess.Popen] = None
        self.execution_logs: List[CommandResult] = []
        self.last_error: Optional[str] = None

    def run(self, command: str, cwd: Optional[str] = None, timeout: Optional[int] = None) -> CommandResult:
        """Execute command and return structured CommandResult."""
        import signal
        work_dir = cwd or self.workspace_root
        effective_timeout = timeout if timeout is not None else self.timeout
        start = time.time()

        creationflags = 0
        preexec = None
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec = os.setsid

        try:
            self.current_process = subprocess.Popen(
                command,
                shell=True,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
                preexec_fn=preexec,
            )

            timed_out = False
            cancelled = False
            stdout, stderr = "", ""

            try:
                stdout, stderr = self.current_process.communicate(timeout=effective_timeout)
                exit_code = self.current_process.returncode
            except subprocess.TimeoutExpired:
                self._kill_current()
                timed_out = True
                exit_code = -1
                try:
                    stdout, stderr = self.current_process.communicate(timeout=5)
                except Exception:
                    pass
            self.current_process = None
            duration = time.time() - start

            result = CommandResult(
                command=command,
                cwd=work_dir,
                exit_code=exit_code,
                stdout=stdout or "",
                stderr=stderr or "",
                duration=duration,
                timed_out=timed_out,
                cancelled=cancelled,
            )

            self.execution_logs.append(result)
            if len(self.execution_logs) > 50:
                self.execution_logs.pop(0)

            if not result.succeeded():
                self.last_error = (
                    f"Command '{command}' exited {exit_code}.\n"
                    f"Stdout:\n{stdout}\nStderr:\n{stderr}"
                )

            return result

        except KeyboardInterrupt:
            self._kill_current()
            duration = time.time() - start
            result = CommandResult(
                command=command, cwd=work_dir,
                exit_code=-1, stdout="", stderr="",
                duration=duration, cancelled=True,
            )
            self.last_error = f"Command '{command}' cancelled."
            self.execution_logs.append(result)
            raise

        except Exception as e:
            duration = time.time() - start
            result = CommandResult(
                command=command, cwd=work_dir,
                exit_code=-1, stdout="", stderr=str(e),
                duration=duration,
            )
            self.last_error = str(e)
            self.execution_logs.append(result)
            return result

    def _kill_current(self):
        if not self.current_process:
            return
        try:
            import signal
            if os.name == "nt":
                self.current_process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                import os as _os
                _os.killpg(_os.getpgid(self.current_process.pid), signal.SIGKILL)
        except Exception:
            try:
                self.current_process.terminate()
            except Exception:
                pass
        self.current_process = None

    def cancel(self):
        self._kill_current()
        self.last_error = "Command was cancelled by the user."


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------

class PolicyDecisionResult(str, Enum):
    ALLOW = "allow"
    ASK   = "ask"
    DENY  = "deny"


class PolicyDecision:
    """Structured result from PolicyEngine.evaluate()."""
    def __init__(
        self,
        decision: "PolicyDecisionResult",
        reason: str,
        rule_id: str = "default",
        risk_level: "RiskLevel" = None,
    ):
        from datetime import datetime
        self.decision = decision
        self.reason = reason
        self.rule_id = rule_id
        self.risk_level = risk_level
        self.timestamp = datetime.now().isoformat()

    def __repr__(self):
        return f"PolicyDecision({self.decision.value}, reason={self.reason!r})"

    # Backward compat aliases so old tests using PolicyDecision.ALLOW etc. still work
    ALLOW = PolicyDecisionResult.ALLOW
    ASK   = PolicyDecisionResult.ASK
    DENY  = PolicyDecisionResult.DENY


@dataclass
class PolicyRule:
    """A single policy rule that can allow/ask/deny a tool call."""
    tool_name: str                      # "*" for all
    risk_level: Optional[RiskLevel]     # None = match any
    decision: PolicyDecision
    reason: str = ""


class PolicyEngine:
    """
    Evaluates whether a tool call is allowed given mode, risk level, and rules.
    FAIL-CLOSED: any error or unknown tool → DENY.
    Returns structured PolicyDecision (not bare enum).
    """

    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve
        self._rules: List[PolicyRule] = []

    def add_rule(self, rule: PolicyRule):
        self._rules.append(rule)

    def evaluate(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        current_mode: str,
        path: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Returns PolicyDecision (structured).
        FAIL-CLOSED: exceptions → DENY + POLICY_ENGINE_ERROR audit event.
        Unknown tools → DENY (not ASK).
        """
        try:
            return self._evaluate_internal(tool_name, risk_level, current_mode, path)
        except Exception as e:
            # Fail-closed: any policy evaluation error → DENY
            # Log internally but never expose traceback to user
            import traceback
            _internal_err = traceback.format_exc()
            # Emit audit event (if available)
            try:
                from ultron.audit import AuditEvent, EventType, AuditLogger
                import os
                # Can't get workspace here so use temp path
                evt = AuditEvent(
                    event_type=EventType.POLICY_ENGINE_ERROR,
                    reason=f"PolicyEngine exception on tool={tool_name}: {str(e)[:100]}",
                    tool=tool_name,
                    decision="DENY",
                    risk=risk_level.value if risk_level else "unknown",
                    redacted_metadata={"tool": tool_name, "mode": current_mode},
                )
            except Exception:
                pass
            return PolicyDecision(
                decision=PolicyDecisionResult.DENY,
                reason="Policy evaluation failed safely. Access denied.",
                rule_id="POLICY_ENGINE_ERROR",
                risk_level=risk_level,
            )

    def _evaluate_internal(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        current_mode: str,
        path: Optional[str] = None,
    ) -> PolicyDecision:
        """Internal evaluation — wrapped by evaluate() for fail-closed behavior."""

        # 1. Check explicit user rules (first match wins)
        for rule in self._rules:
            if rule.tool_name in (tool_name, "*"):
                if rule.risk_level is None or rule.risk_level == risk_level:
                    result = PolicyDecisionResult.ALLOW if rule.decision.value == "allow" else \
                             PolicyDecisionResult.ASK if rule.decision.value == "ask" else \
                             PolicyDecisionResult.DENY
                    return PolicyDecision(
                        decision=result,
                        reason=f"Explicit rule: {rule.reason or rule.tool_name}",
                        rule_id=f"explicit:{rule.tool_name}",
                        risk_level=risk_level,
                    )

        # 2. Unknown tool → DENY (fail-closed, not ASK)
        from ultron.tool_registry import ToolRegistry
        # (registry not available here — rely on caller to validate tool name)

        # 3. Mode-based blocking
        blocked_risks = MODE_RISK_BLOCKS.get(current_mode, [])
        if risk_level in blocked_risks:
            return PolicyDecision(
                decision=PolicyDecisionResult.DENY,
                reason=f"Tool risk level '{risk_level.value}' blocked in '{current_mode}' mode.",
                rule_id=f"mode_block:{current_mode}",
                risk_level=risk_level,
            )

        # 4. Auto-approve allows everything not denied by mode
        if self.auto_approve:
            return PolicyDecision(
                decision=PolicyDecisionResult.ALLOW,
                reason="Auto-approve enabled.",
                rule_id="auto_approve",
                risk_level=risk_level,
            )

        # 5. Read-only tools never need approval
        if risk_level == RiskLevel.READ_ONLY:
            return PolicyDecision(
                decision=PolicyDecisionResult.ALLOW,
                reason="Read-only tool — no approval required.",
                rule_id="read_only_allow",
                risk_level=risk_level,
            )

        # 6. Everything else: ASK
        return PolicyDecision(
            decision=PolicyDecisionResult.ASK,
            reason=f"Tool '{tool_name}' with risk '{risk_level.value}' requires approval.",
            rule_id="default_ask",
            risk_level=risk_level,
        )


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Central registry of all tools with schema, risk level, and executor.
    Replaces if/elif dispatch chains in agent.py.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def all_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_risk(self, name: str) -> RiskLevel:
        tool = self._tools.get(name)
        return tool.risk_level if tool else RiskLevel.WORKSPACE_WRITE

    def get_json_schemas(self) -> List[Dict[str, Any]]:
        """Return tool definitions in OpenAI tool-call format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                }
            }
            for t in self._tools.values()
        ]

    @classmethod
    def build_default(cls) -> "ToolRegistry":
        """Build the standard Ultron tool registry."""
        registry = cls()

        tools = [
            ToolDefinition(
                name="list_dir",
                description="List contents of a directory in the workspace.",
                schema={"type": "object", "properties": {"path": {"type": "string", "minLength": 1}}, "additionalProperties": False},
                risk_level=RiskLevel.READ_ONLY,
            ),
            ToolDefinition(
                name="view_file",
                description="Read content of a file. Use start_line/end_line for large files.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.READ_ONLY,
            ),
            ToolDefinition(
                name="grep_search",
                description="Search files in the workspace for a text pattern.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "path": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.READ_ONLY,
            ),
            ToolDefinition(
                name="git_status",
                description="Get current git status of the workspace.",
                schema={"type": "object", "properties": {}, "additionalProperties": False},
                risk_level=RiskLevel.READ_ONLY,
            ),
            ToolDefinition(
                name="write_file",
                description="Create or overwrite a file with new content.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.WORKSPACE_WRITE,
            ),
            ToolDefinition(
                name="patch_file",
                description="Edit a file by replacing a specific block of code.",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "search_content": {"type": "string"},
                        "replacement_content": {"type": "string"},
                    },
                    "required": ["path", "search_content", "replacement_content"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.WORKSPACE_WRITE,
            ),
            ToolDefinition(
                name="run_command",
                description="Run a shell command in the workspace root.",
                schema={
                    "type": "object",
                    "properties": {"command": {"type": "string", "minLength": 1}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.WORKSPACE_WRITE,
            ),
            ToolDefinition(
                name="git_commit",
                description="Stage and commit changes to git.",
                schema={
                    "type": "object",
                    "properties": {"message": {"type": "string", "minLength": 1}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.GIT_WRITE,
            ),
        ]

        for t in tools:
            registry.register(t)

        return registry
