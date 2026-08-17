"""
command_security.py - Structured Command Security Layer for Ultron.

Pipeline:
Raw shell command
       ↓
  ShellParser  ──► CommandIR (Stable Internal Contract)
       ↓
   Normalizer
       ↓
CapabilityClassifier
       ↓
 ResourceResolver
       ↓
   RiskAnalyzer
       ↓
CapabilityEvaluator ──► CommandDecision
"""
import os
import re
import shlex
import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple

from ultron.scope_manager import RiskLevel, ScopeDecisionResult
from ultron.security import validate_path, is_denied


class CommandCapability(str, Enum):
    READ_COMMAND        = "READ_COMMAND"        # cat, ls, grep, git status, head, tail
    WORKSPACE_COMMAND   = "WORKSPACE_COMMAND"   # mkdir, touch, cp, mv, echo > file
    NETWORK_COMMAND     = "NETWORK_COMMAND"     # curl, wget, ping, ssh, nc, http
    PACKAGE_COMMAND     = "PACKAGE_COMMAND"     # pip install, npm install, cargo add, go get
    PROCESS_COMMAND     = "PROCESS_COMMAND"     # python, node, bash, powershell, ruby, perl
    GIT_COMMAND         = "GIT_COMMAND"         # git commit, git checkout, git diff, git add
    DESTRUCTIVE_COMMAND = "DESTRUCTIVE_COMMAND" # rm -rf, del /s, git reset --hard, git clean -fd
    PRIVILEGED_COMMAND  = "PRIVILEGED_COMMAND"  # sudo, su, chmod 777, chown
    UNKNOWN_COMMAND     = "UNKNOWN_COMMAND"     # Unrecognized, complex inline scripts or unparseable commands


@dataclass
class CommandIR:
    """Stable internal representation of a parsed shell command."""
    raw_command: str
    tokens: List[str] = field(default_factory=list)
    executable: str = ""
    arguments: List[str] = field(default_factory=list)
    is_chained: bool = False
    chain_operators: List[str] = field(default_factory=list)
    subcommands: List[str] = field(default_factory=list)
    parse_error: Optional[str] = None


@dataclass
class CommandDecision:
    """Structured security verdict for a command execution request."""
    capability: CommandCapability
    resources: List[str]
    has_network: bool
    has_filesystem_escape: bool
    secrets_risk: RiskLevel
    risk_level: RiskLevel
    decision: ScopeDecisionResult  # ALLOW, ASK, BLOCK
    requires_explicit_approval: bool
    reason: str


# ---------------------------------------------------------------------------
# Shell Parsers
# ---------------------------------------------------------------------------

class ShellParser:
    """Parses shell command strings into a unified CommandIR."""

    @staticmethod
    def parse(command: str) -> CommandIR:
        cmd_str = command.strip()
        if not cmd_str:
            return CommandIR(raw_command=command, parse_error="Empty command string")

        # Detect chaining & subshell execution patterns
        chain_ops = []
        for op in ["&&", "||", ";", "|", "`", "$("]:
            if op in cmd_str:
                chain_ops.append(op)
        is_chained = len(chain_ops) > 0

        # Attempt shlex parsing first
        try:
            tokens = shlex.split(cmd_str, posix=(os.name != 'nt'))
        except Exception:
            # Fallback simple split on whitespace if quote parsing fails
            tokens = cmd_str.split()

        if not tokens:
            return CommandIR(raw_command=command, parse_error="Could not extract command tokens", is_chained=is_chained, chain_operators=chain_ops)

        executable = os.path.basename(tokens[0]).lower()
        if executable.endswith(".exe") or executable.endswith(".bat") or executable.endswith(".cmd"):
            executable = os.path.splitext(executable)[0]

        return CommandIR(
            raw_command=command,
            tokens=tokens,
            executable=executable,
            arguments=tokens[1:],
            is_chained=is_chained,
            chain_operators=chain_ops,
            parse_error=None,
        )


# ---------------------------------------------------------------------------
# Classifier & Resolver
# ---------------------------------------------------------------------------

class CapabilityClassifier:
    """Classifies a CommandIR into a primary CommandCapability."""

    _READ_EXECS = {"cat", "type", "head", "tail", "grep", "rg", "find", "ls", "dir", "wc", "stat", "diff", "less", "more"}
    _WORKSPACE_EXECS = {"mkdir", "touch", "cp", "copy", "mv", "move", "rmdir"}
    _NETWORK_EXECS = {"curl", "wget", "ping", "ssh", "scp", "sftp", "nc", "netcat", "telnet", "ftp", "nmap"}
    _PACKAGE_EXECS = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "cargo", "go", "gem", "poetry", "uv", "conda"}
    _PROCESS_EXECS = {"python", "python3", "node", "bash", "sh", "zsh", "powershell", "pwsh", "cmd", "ruby", "perl"}
    _GIT_EXECS = {"git"}
    _PRIVILEGED_EXECS = {"sudo", "su", "runas", "chmod", "chown", "chgrp"}
    _DESTRUCTIVE_PATTERNS = [
        re.compile(r"\brm\s+-[a-zA-r]*r[a-zA-r]*f?", re.IGNORECASE),
        re.compile(r"\brmdir\s+/s", re.IGNORECASE),
        re.compile(r"\bdel\s+/[sfq]", re.IGNORECASE),
        re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
        re.compile(r"\bgit\s+clean\s+-[a-zA-r]*f", re.IGNORECASE),
        re.compile(r"\bshred\b", re.IGNORECASE),
        re.compile(r"\bformat\b", re.IGNORECASE),
    ]

    def classify(self, ir: CommandIR) -> CommandCapability:
        if ir.parse_error or not ir.executable:
            return CommandCapability.UNKNOWN_COMMAND

        raw_lower = ir.raw_command.lower()

        # 1. Privileged commands
        if ir.executable in self._PRIVILEGED_EXECS:
            return CommandCapability.PRIVILEGED_COMMAND

        # 2. Destructive patterns
        for pat in self._DESTRUCTIVE_PATTERNS:
            if pat.search(raw_lower):
                return CommandCapability.DESTRUCTIVE_COMMAND

        # 3. Git specific
        if ir.executable in self._GIT_EXECS:
            # Check for destructive git actions
            if any(sub in ir.arguments for sub in ["reset", "clean"]):
                if "--hard" in ir.arguments or "-f" in ir.arguments or "-df" in ir.arguments:
                    return CommandCapability.DESTRUCTIVE_COMMAND
            return CommandCapability.GIT_COMMAND

        # 4. Package management
        if ir.executable in self._PACKAGE_EXECS:
            if any(sub in ir.arguments for sub in ["install", "add", "get", "update", "upgrade"]):
                return CommandCapability.PACKAGE_COMMAND

        # 5. Network tools
        if ir.executable in self._NETWORK_EXECS:
            return CommandCapability.NETWORK_COMMAND

        # 6. Process execution / Script interpreter
        if ir.executable in self._PROCESS_EXECS:
            # If executing inline code like python -c or bash -c, treat as process script execution
            if "-c" in ir.arguments or "-e" in ir.arguments:
                return CommandCapability.PROCESS_COMMAND
            return CommandCapability.PROCESS_COMMAND

        # 7. Workspace filesystem write
        if ir.executable in self._WORKSPACE_EXECS:
            return CommandCapability.WORKSPACE_COMMAND

        # 8. Read operations
        if ir.executable in self._READ_EXECS:
            return CommandCapability.READ_COMMAND

        # Unrecognized executable
        return CommandCapability.UNKNOWN_COMMAND


class ResourceResolver:
    """Extracts target files/paths and checks for filesystem escapes or secret exfiltration."""

    def resolve(self, ir: CommandIR, workspace_root: str) -> Tuple[List[str], bool, RiskLevel]:
        workspace_abs = os.path.realpath(os.path.abspath(workspace_root))
        resources = []
        has_escape = False
        secrets_risk = RiskLevel.LOW

        for arg in ir.arguments:
            # Skip flags
            if arg.startswith("-") or arg.startswith("/"):
                continue

            # Look for path-like arguments
            if "/" in arg or "\\" in arg or "." in arg:
                # Check for explicit parent directory traversal
                if ".." in arg:
                    has_escape = True

                try:
                    resolved = validate_path(arg, workspace_abs)
                    rel_path = os.path.relpath(resolved, workspace_abs)
                    resources.append(rel_path)
                    if is_denied(rel_path):
                        secrets_risk = RiskLevel.CRITICAL
                except PermissionError:
                    has_escape = True
                    resources.append(arg)
                    secrets_risk = RiskLevel.CRITICAL

        return resources, has_escape, secrets_risk


# ---------------------------------------------------------------------------
# Security Evaluator Main Entry Point
# ---------------------------------------------------------------------------

class CommandSecurityLayer:
    """
    Main evaluation gateway for all shell command security checks.
    Uses strict fail-closed policies for unknown or dangerous commands.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.parser = ShellParser()
        self.classifier = CapabilityClassifier()
        self.resolver = ResourceResolver()

    def evaluate(self, command: str, is_interactive: bool = True) -> CommandDecision:
        ir = self.parser.parse(command)
        capability = self.classifier.classify(ir)
        resources, has_escape, secrets_risk = self.resolver.resolve(ir, self.workspace_root)

        has_network = capability == CommandCapability.NETWORK_COMMAND or any(
            kw in command.lower() for kw in ["http://", "https://", "curl", "wget"]
        )

        # ── Policy Enforcement Rules ──

        # 1. Privileged commands -> Always BLOCK
        if capability == CommandCapability.PRIVILEGED_COMMAND:
            return CommandDecision(
                capability=capability,
                resources=resources,
                has_network=has_network,
                has_filesystem_escape=has_escape,
                secrets_risk=RiskLevel.CRITICAL,
                risk_level=RiskLevel.CRITICAL,
                decision=ScopeDecisionResult.BLOCK,
                requires_explicit_approval=True,
                reason="Privileged/super-user shell commands are strictly blocked.",
            )

        # 2. Filesystem escape or sensitive file access -> BLOCK
        if has_escape or secrets_risk == RiskLevel.CRITICAL:
            return CommandDecision(
                capability=capability,
                resources=resources,
                has_network=has_network,
                has_filesystem_escape=has_escape,
                secrets_risk=secrets_risk,
                risk_level=RiskLevel.CRITICAL,
                decision=ScopeDecisionResult.BLOCK,
                requires_explicit_approval=True,
                reason="Command targets paths outside workspace or sensitive files (.env / .git / credentials).",
            )

        # 3. UNKNOWN commands -> FAIL CLOSED
        if capability == CommandCapability.UNKNOWN_COMMAND or ir.parse_error:
            if not is_interactive:
                # Non-interactive / CI / Autonomous mode -> STRICT BLOCK
                return CommandDecision(
                    capability=CommandCapability.UNKNOWN_COMMAND,
                    resources=resources,
                    has_network=has_network,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.HIGH,
                    decision=ScopeDecisionResult.BLOCK,
                    requires_explicit_approval=True,
                    reason=f"Unknown/unparseable command '{command}' blocked by fail-closed security policy in non-interactive mode.",
                )
            else:
                # Interactive mode -> ASK for explicit approval
                return CommandDecision(
                    capability=CommandCapability.UNKNOWN_COMMAND,
                    resources=resources,
                    has_network=has_network,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.HIGH,
                    decision=ScopeDecisionResult.ASK,
                    requires_explicit_approval=True,
                    reason=f"Unrecognized shell command '{command}' requires explicit user confirmation.",
                )

        # 4. Destructive commands -> Mandatory explicit approval (never downgraded)
        if capability == CommandCapability.DESTRUCTIVE_COMMAND:
            return CommandDecision(
                capability=capability,
                resources=resources,
                has_network=has_network,
                has_filesystem_escape=has_escape,
                secrets_risk=secrets_risk,
                risk_level=RiskLevel.HIGH,
                decision=ScopeDecisionResult.ASK,
                requires_explicit_approval=True,
                reason="Destructive command requires mandatory explicit approval.",
            )

        # 5. Network / Package commands -> ASK in interactive mode, policy check in non-interactive
        if capability in (CommandCapability.NETWORK_COMMAND, CommandCapability.PACKAGE_COMMAND):
            if is_interactive:
                return CommandDecision(
                    capability=capability,
                    resources=resources,
                    has_network=has_network,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.MEDIUM,
                    decision=ScopeDecisionResult.ASK,
                    requires_explicit_approval=True,
                    reason=f"{capability.value} requires user approval.",
                )
            else:
                return CommandDecision(
                    capability=capability,
                    resources=resources,
                    has_network=has_network,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.MEDIUM,
                    decision=ScopeDecisionResult.ALLOW,
                    requires_explicit_approval=False,
                    reason="Package/network command permitted by policy in autonomous mode.",
                )

        # 6. READ / WORKSPACE / GIT commands within workspace -> ALLOW
        return CommandDecision(
            capability=capability,
            resources=resources,
            has_network=has_network,
            has_filesystem_escape=False,
            secrets_risk=RiskLevel.LOW,
            risk_level=RiskLevel.LOW,
            decision=ScopeDecisionResult.ALLOW,
            requires_explicit_approval=False,
            reason="Command authorized within workspace scope.",
        )
