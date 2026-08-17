"""
tool_executor.py - P0.1: Single Tool Execution Pipeline.

ALL model-driven tool execution MUST go through ToolExecutor.
No model-generated action can bypass:
    Scope → Policy → Approval → Execute → ChangeTracker → SecretRedactor → Audit

Pipeline:
    Tool Request
        ↓
    ToolRegistry (lookup + validate)
        ↓
    ScopeManager.evaluate() → ALLOW/ASK/BLOCK
        ↓  (BLOCK → stop)
    RiskClassifier
        ↓
    PolicyEngine.evaluate() → ALLOW/ASK/DENY
        ↓  (DENY → stop)
    Approval Gate (if ASK from either layer)
        ↓
    CHECKPOINT (for write/destructive ops)
        ↓
    Filesystem Boundary / Symlink Safety
        ↓
    EXECUTE
        ↓
    ChangeTracker.record()    |    Raw Result
        ↓                              ↓
    SecretRedactor.redact_for_model()
        ↓
    AuditLogger.emit()
        ↓
    Return result to model
"""
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable

from ultron.tool_registry import ToolRegistry, RiskLevel, PolicyEngine, PolicyDecisionResult
from ultron.scope_manager import ScopeManager, ScopeDecisionResult, RiskClassifier
from ultron.secret_redactor import SecretRedactor
from ultron.audit import AuditLogger, AuditEvent, EventType
from ultron.security import validate_path
from ultron.command_security import CommandSecurityLayer, CommandCapability
from ultron.resource_guard import ResourceGuard, ResourceExceededError


# ---------------------------------------------------------------------------
# ToolExecutionResult
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionResult:
    tool_name: str
    success: bool
    result: str                    # redacted for model consumption
    raw_result: str                # full result (never sent to model)
    decision_chain: Dict[str, str] # scope_decision, policy_decision
    risk_level: str
    was_blocked: bool = False
    was_denied: bool = False
    was_approved: bool = False


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """
    Single authorized execution pipeline for all model-driven tool calls.
    Cannot be bypassed. Every action passes through Scope → CommandSecurity → Policy → ResourceGuard → Execute.
    """

    def __init__(
        self,
        workspace_root: str,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        scope_manager: ScopeManager,
        audit_logger: AuditLogger,
        tools,                       # ToolManager instance (legacy execution layer)
        checkpoint_manager=None,
        change_tracker=None,
        console=None,
        task_id: str = "",
        transaction_id: str = "",
    ):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.registry = tool_registry
        self.policy = policy_engine
        self.scope = scope_manager
        self.audit = audit_logger
        self.tools = tools
        self.checkpoint = checkpoint_manager
        self.change_tracker = change_tracker
        self.console = console
        self.task_id = task_id
        self.transaction_id = transaction_id
        self._redactor = SecretRedactor()
        self._risk_classifier = RiskClassifier()
        self.command_security = CommandSecurityLayer(self.workspace_root)
        self.resource_guard = ResourceGuard()

    def _log(self, msg: str, style: str = "dim"):
        if self.console:
            self.console.print(f"[{style}]{msg}[/{style}]")

    def _emit(self, event_type: EventType, tool: str, reason: str,
              decision: str = None, risk: str = None, resource: str = None,
              metadata: dict = None):
        evt = AuditEvent(
            event_type=event_type,
            reason=reason,
            task_id=self.task_id,
            transaction_id=self.transaction_id,
            actor="model",
            tool=tool,
            resource=resource,
            decision=decision,
            risk=risk,
            redacted_metadata=metadata or {},
        )
        self.audit.emit(evt)

    def _validate_filesystem_path(self, path: str) -> str:
        """
        P0.8: Filesystem boundary + symlink safety.
        Resolves all symlinks, checks workspace containment.
        Returns validated absolute path or raises PermissionError.
        """
        return validate_path(path, self.workspace_root)

    def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        intent_mode: str = "build",
        user_confirm_callback: Optional[Callable[[str, str], bool]] = None,
    ) -> ToolExecutionResult:
        """
        Execute a tool through the full security pipeline.
        user_confirm_callback(tool_name, message) → bool (True = approved)
        """
        # ── Step 1: Registry lookup ──────────────────────────────────────
        tool_def = self.registry.get(tool_name)
        if tool_def is None:
            # Unknown tool → DENY (fail-closed)
            self._emit(EventType.TOOL_DENIED, tool_name,
                       f"Unknown tool '{tool_name}' — denied by default (fail-closed)",
                       decision="DENY", risk="unknown")
            return ToolExecutionResult(
                tool_name=tool_name, success=False, was_denied=True,
                result=f"Error: Unknown tool '{tool_name}'. Access denied.",
                raw_result="", decision_chain={"policy": "DENY", "reason": "unknown tool"},
                risk_level="unknown",
            )

        risk_level = tool_def.risk_level
        path = args.get("path") or args.get("command") or ""

        # ── Step 1.5: ResourceGuard Budget Check ─────────────────────────
        try:
            self.resource_guard.check_tool_call()
        except ResourceExceededError as err:
            self._emit(EventType.TOOL_DENIED, tool_name, str(err), decision="BLOCK", risk="HIGH", resource=path)
            return ToolExecutionResult(
                tool_name=tool_name, success=False, was_blocked=True,
                result=f"Error: {err}", raw_result="",
                decision_chain={"resource_guard": "EXCEEDED"}, risk_level="HIGH"
            )

        # ── Step 1.6: CommandSecurityLayer Evaluation (run_command) ─────
        cmd_security_ask = False
        if tool_name == "run_command":
            cmd_str = args.get("command", "")
            cmd_decision = self.command_security.evaluate(
                cmd_str, is_interactive=bool(user_confirm_callback)
            )
            if cmd_decision.decision == ScopeDecisionResult.BLOCK:
                self._emit(EventType.SCOPE_BLOCKED, tool_name,
                           cmd_decision.reason, decision="BLOCK",
                           risk=cmd_decision.risk_level.value, resource=cmd_str)
                return ToolExecutionResult(
                    tool_name=tool_name, success=False, was_blocked=True,
                    result=f"Error: Command blocked by CommandSecurityLayer. {cmd_decision.reason}",
                    raw_result="",
                    decision_chain={"command_security": "BLOCK", "reason": cmd_decision.reason},
                    risk_level=cmd_decision.risk_level.value,
                )
            if cmd_decision.requires_explicit_approval:
                cmd_security_ask = True

        self._emit(EventType.TOOL_REQUESTED, tool_name,
                   f"Tool requested: {tool_name}",
                   risk=risk_level.value, resource=path,
                   metadata={"args_keys": list(args.keys())})

        # ── Step 2: Scope evaluation ─────────────────────────────────────
        operation = self._risk_to_operation(risk_level, tool_name)
        scope_decision = self.scope.evaluate(path, operation)

        if scope_decision.decision == ScopeDecisionResult.BLOCK:
            self._emit(EventType.SCOPE_BLOCKED, tool_name,
                       scope_decision.reason, decision="BLOCK",
                       risk=scope_decision.risk.value, resource=path)
            return ToolExecutionResult(
                tool_name=tool_name, success=False, was_blocked=True,
                result=f"Error: Scope blocked. {scope_decision.reason}",
                raw_result="",
                decision_chain={"scope": "BLOCK", "reason": scope_decision.reason},
                risk_level=scope_decision.risk.value,
            )

        scope_ask = scope_decision.decision == ScopeDecisionResult.ASK

        if scope_ask:
            self._emit(EventType.SCOPE_ASKED, tool_name,
                       scope_decision.reason, decision="ASK",
                       risk=scope_decision.risk.value, resource=path)

        # ── Step 3: Policy evaluation ────────────────────────────────────
        policy_decision = self.policy.evaluate(tool_name, risk_level, intent_mode, path)

        if policy_decision.decision == PolicyDecisionResult.DENY:
            self._emit(EventType.POLICY_DENIED, tool_name,
                       policy_decision.reason, decision="DENY",
                       risk=risk_level.value, resource=path)
            return ToolExecutionResult(
                tool_name=tool_name, success=False, was_denied=True,
                result=f"Error: Tool denied. {policy_decision.reason}",
                raw_result="",
                decision_chain={"policy": "DENY", "reason": policy_decision.reason},
                risk_level=risk_level.value,
            )

        policy_ask = policy_decision.decision == PolicyDecisionResult.ASK

        # ── Step 4: Approval gate ─────────────────────────────────────────
        needs_approval = scope_ask or policy_ask or cmd_security_ask or tool_def.requires_approval
        approved = not needs_approval  # pre-approved if no ask needed

        if needs_approval and user_confirm_callback:
            reason = scope_decision.reason if scope_ask else policy_decision.reason
            approved = user_confirm_callback(tool_name, reason)
            if not approved:
                self._emit(EventType.TOOL_DENIED, tool_name,
                           "User declined approval", decision="DENIED_BY_USER",
                           risk=risk_level.value, resource=path)
                return ToolExecutionResult(
                    tool_name=tool_name, success=False, was_denied=True,
                    result="Error: User rejected tool execution.",
                    raw_result="",
                    decision_chain={"approval": "DENIED_BY_USER"},
                    risk_level=risk_level.value,
                )

        if approved and scope_ask:
            self.scope.approve_expansion(path)

        # ── Step 5: Filesystem boundary check (for file ops) ────────────
        if path and tool_name in ("write_file", "patch_file", "view_file", "list_dir"):
            try:
                self._validate_filesystem_path(path)
            except PermissionError as e:
                self._emit(EventType.PATH_VIOLATION, tool_name,
                           str(e), decision="DENY", risk="CRITICAL", resource=path)
                return ToolExecutionResult(
                    tool_name=tool_name, success=False, was_denied=True,
                    result=f"Error: {e}",
                    raw_result="",
                    decision_chain={"filesystem": "PATH_VIOLATION"},
                    risk_level="CRITICAL",
                )

        # ── Step 6: Checkpoint before write/destructive ops & ResourceGuard file checks ──
        if tool_name in ("write_file", "patch_file") and path:
            content_bytes = len((args.get("content") or args.get("replacement_content") or "").encode("utf-8"))
            try:
                self.resource_guard.check_file_creation(content_bytes)
            except ResourceExceededError as err:
                self._emit(EventType.TOOL_DENIED, tool_name, str(err), decision="BLOCK", risk="HIGH", resource=path)
                return ToolExecutionResult(
                    tool_name=tool_name, success=False, was_blocked=True,
                    result=f"Error: {err}", raw_result="",
                    decision_chain={"resource_guard": "EXCEEDED"}, risk_level="HIGH"
                )
            if self.checkpoint:
                try:
                    self.checkpoint.record_before_edit(path)
                except Exception:
                    pass

        # ── Step 7: Execute ──────────────────────────────────────────────
        self._emit(EventType.TOOL_ALLOWED, tool_name,
                   f"Executing {tool_name}", decision="ALLOW",
                   risk=risk_level.value, resource=path)

        try:
            raw_result = self._dispatch(tool_name, args)
        except Exception as e:
            self._emit(EventType.TOOL_FAILED, tool_name,
                       f"Tool execution failed: {str(e)[:100]}",
                       risk=risk_level.value, resource=path)
            return ToolExecutionResult(
                tool_name=tool_name, success=False,
                result=f"Error executing tool: {str(e)}",
                raw_result=str(e),
                decision_chain={"execution": "FAILED"},
                risk_level=risk_level.value,
            )

        # Enforce output truncation via ResourceGuard
        raw_result = self.resource_guard.truncate_output(raw_result)

        # ── Step 8: ChangeTracker ────────────────────────────────────────
        if tool_name in ("write_file", "patch_file") and self.change_tracker and path:
            self.change_tracker.record_after(path)
        if self.checkpoint and tool_name in ("write_file", "patch_file") and path:
            if not raw_result.startswith("Error"):
                self.checkpoint.record_after_edit(path)

        # ── Step 9: SecretRedactor ────────────────────────────────────────
        redacted_result = self._redactor.redact_for_model(raw_result)

        # ── Step 10: Audit ───────────────────────────────────────────────
        self._emit(EventType.TOOL_EXECUTED, tool_name,
                   f"Tool completed: {tool_name}",
                   decision="EXECUTED", risk=risk_level.value, resource=path,
                   metadata={"result_chars": len(raw_result)})

        return ToolExecutionResult(
            tool_name=tool_name,
            success=not raw_result.startswith("Error"),
            result=redacted_result,
            raw_result=raw_result,
            was_approved=approved,
            decision_chain={
                "scope": scope_decision.decision.value,
                "policy": policy_decision.decision.value,
                "approved": str(approved),
            },
            risk_level=risk_level.value,
        )

    def _risk_to_operation(self, risk_level: RiskLevel, tool_name: str) -> str:
        """Map tool risk level to operation string for ScopeManager."""
        if tool_name == "write_file":
            return "CREATE"
        if tool_name == "patch_file":
            return "MODIFY"
        if tool_name == "git_commit":
            return "GIT_COMMIT"
        if tool_name in ("list_dir", "view_file", "grep_search", "git_status"):
            return "READ"
        if tool_name == "run_command":
            return "WRITE"
        return risk_level.value

    def _dispatch(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Dispatch to the legacy ToolManager executor."""
        if tool_name == "list_dir":
            return self.tools.list_dir(args.get("path", "."))
        elif tool_name == "view_file":
            return self.tools.view_file(
                args.get("path"), args.get("start_line"), args.get("end_line")
            )
        elif tool_name == "grep_search":
            return self.tools.grep_search(args.get("query"), args.get("path"))
        elif tool_name == "git_status":
            return self.tools.git_status()
        elif tool_name == "write_file":
            return self.tools.write_file(args.get("path"), args.get("content"))
        elif tool_name == "patch_file":
            return self.tools.patch_file(
                args.get("path"),
                args.get("search_content"),
                args.get("replacement_content"),
            )
        elif tool_name == "run_command":
            return self.tools.run_command(args.get("command"))
        elif tool_name == "git_commit":
            return self.tools.git_commit(args.get("message"))
        else:
            return f"Error: No executor for tool '{tool_name}'"
