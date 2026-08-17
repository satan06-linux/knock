"""
command_security.py - Structured Command Security Layer for Ultron.

Pipeline:
Raw shell command
       ↓
  ShellParser  ──► CommandIR (Stable Internal Contract & Subcommand Splitting)
       ↓
   Normalizer
       ↓
CapabilityClassifier (Takes highest-risk capability across subcommands)
       ↓
 ResourceResolver (Extracts paths & secret files across all subcommands)
       ↓
   RiskAnalyzer
       ↓
CapabilityEvaluator ──► CommandDecision (Strict Fail-Closed Policies)
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
    GIT_COMMAND         = "GIT_COMMAND"         # git commit, git checkout, git diff, git add
    PROCESS_COMMAND     = "PROCESS_COMMAND"     # python, node, bash, powershell, ruby, perl
    PACKAGE_COMMAND     = "PACKAGE_COMMAND"     # pip install, npm install, cargo add, go get
    NETWORK_COMMAND     = "NETWORK_COMMAND"     # curl, wget, ping, ssh, nc, http
    UNKNOWN_COMMAND     = "UNKNOWN_COMMAND"     # Unrecognized, complex inline scripts or unparseable commands
    DESTRUCTIVE_COMMAND = "DESTRUCTIVE_COMMAND" # rm -rf, del /s, git reset --hard, git clean -fd
    PRIVILEGED_COMMAND  = "PRIVILEGED_COMMAND"  # sudo, su, chmod 777, chown


# Severity ordering for capability combining (higher index = higher risk)
_CAPABILITY_SEVERITY = {
    CommandCapability.READ_COMMAND: 1,
    CommandCapability.GIT_COMMAND: 2,
    CommandCapability.WORKSPACE_COMMAND: 3,
    CommandCapability.PROCESS_COMMAND: 4,
    CommandCapability.PACKAGE_COMMAND: 5,
    CommandCapability.NETWORK_COMMAND: 6,
    CommandCapability.UNKNOWN_COMMAND: 7,
    CommandCapability.DESTRUCTIVE_COMMAND: 8,
    CommandCapability.PRIVILEGED_COMMAND: 9,
}


@dataclass
class CommandIR:
    """Stable internal representation of a parsed shell command and its subcommands."""
    raw_command: str
    tokens: List[str] = field(default_factory=list)
    executable: str = ""
    arguments: List[str] = field(default_factory=list)
    is_chained: bool = False
    chain_operators: List[str] = field(default_factory=list)
    subcommands: List[str] = field(default_factory=list)
    sub_irs: List["CommandIR"] = field(default_factory=list)
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
    """Parses shell command strings into a unified CommandIR with subcommand splitting."""

    @staticmethod
    def parse_single(cmd_str: str) -> CommandIR:
        """Parse a single unchained command segment using shlex."""
        cmd_str = cmd_str.strip()
        if not cmd_str:
            return CommandIR(raw_command=cmd_str, parse_error="Empty command string")

        try:
            tokens = shlex.split(cmd_str, posix=(os.name != 'nt'))
        except Exception as e:
            # FAIL CLOSED: Never fall back to loose split(). Mark parse_error.
            return CommandIR(raw_command=cmd_str, parse_error=f"Shell parse error: {e}")

        if not tokens:
            return CommandIR(raw_command=cmd_str, parse_error="Empty tokens extracted")

        executable = os.path.basename(tokens[0]).lower()
        if executable.endswith(".exe") or executable.endswith(".bat") or executable.endswith(".cmd"):
            executable = os.path.splitext(executable)[0]

        return CommandIR(
            raw_command=cmd_str,
            tokens=tokens,
            executable=executable,
            arguments=tokens[1:],
            parse_error=None,
        )

    @classmethod
    def parse(cls, command: str) -> CommandIR:
        cmd_str = command.strip()
        if not cmd_str:
            return CommandIR(raw_command=command, parse_error="Empty command string")

        # Detect chaining operators: &&, ||, ;, |, $(, `
        chain_ops = []
        for op in ["&&", "||", ";", "|", "`", "$("]:
            if op in cmd_str:
                chain_ops.append(op)
        is_chained = len(chain_ops) > 0

        if not is_chained:
            return cls.parse_single(cmd_str)

        # Split chained command by pipes, semicolons, &&, ||
        # Regex splits on ;, &&, ||, | while keeping delimiters or splitting clean segments
        segments = re.split(r";|&&|\|\||\|", cmd_str)
        sub_irs = []
        all_tokens = []
        has_error = None

        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            sub_ir = cls.parse_single(seg)
            sub_irs.append(sub_ir)
            all_tokens.extend(sub_ir.tokens)
            if sub_ir.parse_error and not has_error:
                has_error = sub_ir.parse_error

        primary_exec = sub_irs[0].executable if sub_irs else ""

        return CommandIR(
            raw_command=command,
            tokens=all_tokens,
            executable=primary_exec,
            arguments=all_tokens[1:] if all_tokens else [],
            is_chained=is_chained,
            chain_operators=chain_ops,
            subcommands=[s.strip() for s in segments if s.strip()],
            sub_irs=sub_irs,
            parse_error=has_error,
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

    def classify_single(self, ir: CommandIR) -> CommandCapability:
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
            if any(sub in ir.arguments for sub in ["reset", "clean"]):
                if "--hard" in ir.arguments or "-f" in ir.arguments or "-df" in ir.arguments:
                    return CommandCapability.DESTRUCTIVE_COMMAND
            return CommandCapability.GIT_COMMAND

        # 4. Package management
        if ir.executable in self._PACKAGE_EXECS:
            if any(sub in ir.arguments for sub in ["install", "add", "get", "update", "upgrade", "exec"]):
                return CommandCapability.PACKAGE_COMMAND

        # 5. Network tools
        if ir.executable in self._NETWORK_EXECS or any(kw in raw_lower for kw in ["http://", "https://", "curl", "wget"]):
            return CommandCapability.NETWORK_COMMAND

        # 6. Process execution / Script interpreter
        if ir.executable in self._PROCESS_EXECS:
            return CommandCapability.PROCESS_COMMAND

        # 7. Workspace filesystem write
        if ir.executable in self._WORKSPACE_EXECS:
            return CommandCapability.WORKSPACE_COMMAND

        # 8. Read operations
        if ir.executable in self._READ_EXECS:
            return CommandCapability.READ_COMMAND

        return CommandCapability.UNKNOWN_COMMAND

    def classify(self, ir: CommandIR) -> CommandCapability:
        """Classify command IR, combining subcommands to take the highest severity capability."""
        if ir.parse_error:
            return CommandCapability.UNKNOWN_COMMAND

        if not ir.is_chained or not ir.sub_irs:
            return self.classify_single(ir)

        # For chained commands, classify every subcommand and take the highest severity
        capabilities = [self.classify_single(sub_ir) for sub_ir in ir.sub_irs]
        highest = max(capabilities, key=lambda c: _CAPABILITY_SEVERITY.get(c, 0))
        return highest


class ResourceResolver:
    """Extracts target files/paths across all subcommands and checks for filesystem escapes or secret exfiltration."""

    def resolve(self, ir: CommandIR, workspace_root: str) -> Tuple[List[str], bool, RiskLevel]:
        workspace_abs = os.path.realpath(os.path.abspath(workspace_root))
        resources = []
        has_escape = False
        secrets_risk = RiskLevel.LOW

        tokens_to_check = ir.tokens
        for arg in tokens_to_check:
            # Skip flags
            if arg.startswith("-") or arg.startswith("/"):
                continue

            # Look for path-like arguments
            if "/" in arg or "\\" in arg or "." in arg:
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
    Uses strict fail-closed policies for unknown, chained, or dangerous commands.
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

        # Detect network usage across entire command string or subcommands
        cmd_lower = command.lower()
        has_network = (
            capability == CommandCapability.NETWORK_COMMAND
            or any(kw in cmd_lower for kw in ["http://", "https://", "curl ", "wget ", "ping ", "nc ", "ssh "])
        )

        # If exfiltration pattern detected (e.g. cat/grep sensitive file piped to network tool)
        if has_network and secrets_risk == RiskLevel.CRITICAL:
            return CommandDecision(
                capability=CommandCapability.NETWORK_COMMAND,
                resources=resources,
                has_network=True,
                has_filesystem_escape=has_escape,
                secrets_risk=RiskLevel.CRITICAL,
                risk_level=RiskLevel.CRITICAL,
                decision=ScopeDecisionResult.BLOCK,
                requires_explicit_approval=True,
                reason="Credential exfiltration vector detected: sensitive resource targeted with network connectivity.",
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

        # 3. UNKNOWN / Unparseable commands -> FAIL CLOSED
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

        # 4. Destructive commands -> BLOCK in autonomous mode, ASK in interactive mode
        if capability == CommandCapability.DESTRUCTIVE_COMMAND:
            if not is_interactive:
                return CommandDecision(
                    capability=capability,
                    resources=resources,
                    has_network=has_network,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.HIGH,
                    decision=ScopeDecisionResult.BLOCK,
                    requires_explicit_approval=True,
                    reason="Destructive command blocked in autonomous mode.",
                )
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

        # 5. Network / Package commands -> BLOCK in autonomous/non-interactive mode, ASK in interactive mode
        if capability in (CommandCapability.NETWORK_COMMAND, CommandCapability.PACKAGE_COMMAND) or has_network:
            if not is_interactive:
                return CommandDecision(
                    capability=capability,
                    resources=resources,
                    has_network=True,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.HIGH,
                    decision=ScopeDecisionResult.BLOCK,
                    requires_explicit_approval=True,
                    reason=f"{capability.value} blocked by default policy in autonomous/non-interactive mode.",
                )
            else:
                return CommandDecision(
                    capability=capability,
                    resources=resources,
                    has_network=True,
                    has_filesystem_escape=has_escape,
                    secrets_risk=secrets_risk,
                    risk_level=RiskLevel.MEDIUM,
                    decision=ScopeDecisionResult.ASK,
                    requires_explicit_approval=True,
                    reason=f"{capability.value} requires user approval.",
                )

        # 6. READ / WORKSPACE / GIT commands within workspace -> ALLOW
        return CommandDecision(
            capability=capability,
            resources=resources,
            has_network=False,
            has_filesystem_escape=False,
            secrets_risk=RiskLevel.LOW,
            risk_level=RiskLevel.LOW,
            decision=ScopeDecisionResult.ALLOW,
            requires_explicit_approval=False,
            reason="Command authorized within workspace scope.",
        )
