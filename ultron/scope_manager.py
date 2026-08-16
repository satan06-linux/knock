"""
scope_manager.py - P0.3 + P0.4: Dynamic ScopeManager, RiskClassifier, ScopeMonitor.
Scope decisions are evidence-based, not directory/location-based.
"""
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScopeRelationship(str, Enum):
    DIRECT_DEPENDENCY  = "DIRECT_DEPENDENCY"   # found via imports/references
    TEST               = "TEST"                 # test file for a source file
    CONFIGURATION      = "CONFIGURATION"        # config referenced in source
    BUILD              = "BUILD"                # build artifact of changed source
    DOCUMENTATION      = "DOCUMENTATION"        # docs referencing changed symbol
    UNRELATED          = "UNRELATED"            # no evidence of relationship
    UNKNOWN            = "UNKNOWN"              # relationship unclear


class RiskLevel(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class ScopeDecisionResult(str, Enum):
    ALLOW = "ALLOW"
    ASK   = "ASK"
    BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# Hard-blocked resources (regardless of task)
# ---------------------------------------------------------------------------

_HARD_BLOCKED_PATTERNS = [
    re.compile(r"(?:^|[/\\])\.git[/\\](?:objects|index|HEAD|hooks|packed-refs)(?:[/\\]|$)", re.IGNORECASE),
    re.compile(r"^\.git[/\\](?:objects|index|HEAD|hooks|packed-refs)", re.IGNORECASE),
    re.compile(r"^/etc/", re.IGNORECASE),
    re.compile(r"[Cc]:\\[Ww]indows[/\\][Ss]ystem32"),
    re.compile(r"(?:^|[/\\])(?:id_rsa|id_ecdsa|id_ed25519)$"),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"[/\\]\.ssh[/\\]"),
    re.compile(r"^\.ssh[/\\]"),
]

# Resources that are SENSITIVE (need stronger controls but not hard-blocked)
_SENSITIVE_PATTERNS = [
    re.compile(r"\.env$", re.IGNORECASE),
    re.compile(r"\.env\.", re.IGNORECASE),
    re.compile(r"secrets?\.(yaml|yml|json|toml)", re.IGNORECASE),
    re.compile(r"[/\\]\.git[/\\]config$", re.IGNORECASE),
    re.compile(r"docker-compose.*\.(yaml|yml)$", re.IGNORECASE),
    re.compile(r"(?:prod|production).*\.(yaml|yml|json|toml|env)$", re.IGNORECASE),
    re.compile(r"[/\\]deployment[/\\]"),
    re.compile(r"[/\\]infrastructure[/\\]"),
    re.compile(r"[/\\]terraform[/\\]"),
]


# ---------------------------------------------------------------------------
# RiskClassifier
# ---------------------------------------------------------------------------

class RiskClassifier:
    """Classifies the risk level of a resource/operation combination."""

    def classify(self, path: str, operation: str) -> RiskLevel:
        norm = path.replace("\\", "/")

        # Hard-blocked → CRITICAL
        for pat in _HARD_BLOCKED_PATTERNS:
            if pat.search(norm):
                return RiskLevel.CRITICAL

        # Sensitive + destructive operation → HIGH
        for pat in _SENSITIVE_PATTERNS:
            if pat.search(norm):
                if operation in ("DELETE", "OVERWRITE", "MODIFY"):
                    return RiskLevel.HIGH
                return RiskLevel.MEDIUM

        # Destructive operations on normal files → MEDIUM
        if operation in ("DELETE", "GIT_RESET_HARD", "GIT_CLEAN"):
            return RiskLevel.MEDIUM

        # Git write operations → MEDIUM
        if operation in ("GIT_COMMIT", "GIT_PUSH", "GIT_MERGE"):
            return RiskLevel.MEDIUM

        # Normal workspace writes → LOW
        if operation in ("WRITE", "PATCH", "CREATE"):
            return RiskLevel.LOW

        # Read-only → LOW
        if operation in ("READ", "LIST", "GREP"):
            return RiskLevel.LOW

        return RiskLevel.LOW


# ---------------------------------------------------------------------------
# ScopeDecision dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScopeDecision:
    path: str
    operation: str
    reason: str
    relationship: ScopeRelationship
    evidence: str              # how relationship was determined
    risk: RiskLevel
    decision: ScopeDecisionResult
    approval_required: bool
    task_id: str = ""


# ---------------------------------------------------------------------------
# ScopeManager
# ---------------------------------------------------------------------------

class ScopeManager:
    """
    Manages dynamic task scope with evidence-based expansion.

    Scope starts from initial_files (from contract/plan).
    Expansions require evidence of relationship — never trust LLM declaration alone.
    """

    def __init__(self, workspace_root: str, repo_map=None):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.repo_map = repo_map
        self._initial_scope: List[str] = []
        self._expanded_scope: List[str] = []
        self._decisions: List[ScopeDecision] = []
        self._classifier = RiskClassifier()
        self._task_id: str = ""

    def set_initial_scope(self, files: List[str], task_id: str = ""):
        """Set the initial scope from contract/plan."""
        self._initial_scope = [self._norm(f) for f in files]
        self._expanded_scope = list(self._initial_scope)
        self._task_id = task_id

    def _norm(self, path: str) -> str:
        """Normalize path to forward slashes relative to workspace."""
        abs_path = os.path.realpath(os.path.join(self.workspace_root, path))
        try:
            rel = os.path.relpath(abs_path, self.workspace_root)
            return rel.replace("\\", "/")
        except ValueError:
            return path.replace("\\", "/")

    def _is_hard_blocked(self, path: str) -> bool:
        norm = path.replace("\\", "/")
        # Handle paths that start with .git/ directly (no leading slash)
        if re.match(r"^\.git/(?:objects|index|HEAD|hooks|packed-refs|config)", norm, re.IGNORECASE):
            return True
        for pat in _HARD_BLOCKED_PATTERNS:
            if pat.search(norm):
                return True
        return False

    def _derive_relationship(self, path: str) -> tuple:
        """
        Derive relationship from evidence (repo_map), not LLM declaration.
        Returns (ScopeRelationship, evidence_string)
        """
        norm = self._norm(path)
        base = os.path.splitext(os.path.basename(norm))[0].lower()

        # Test file pattern
        if re.search(r"test_|_test\.|\.test\.|\.spec\.", norm, re.IGNORECASE):
            # Check if any initial scope file matches
            for init in self._initial_scope:
                init_base = os.path.splitext(os.path.basename(init))[0].lower()
                if init_base in base or base in init_base:
                    return ScopeRelationship.TEST, f"Test file for {init}"
            return ScopeRelationship.TEST, "Test file pattern detected"

        # Configuration file pattern
        if re.search(r"\.(yaml|yml|toml|ini|cfg|conf|json)$", norm, re.IGNORECASE):
            if self.repo_map:
                # Check if any initial scope file imports/references this config
                for init in self._initial_scope:
                    importers = self.repo_map.who_imports(norm)
                    if init in importers or any(init in imp for imp in importers):
                        return ScopeRelationship.CONFIGURATION, f"Referenced by {init}"
            return ScopeRelationship.CONFIGURATION, "Configuration file pattern"

        # Direct dependency via repo_map
        if self.repo_map:
            for init in self._initial_scope:
                importers = self.repo_map.who_imports(norm)
                if init in importers:
                    return ScopeRelationship.DIRECT_DEPENDENCY, f"Imported by {init}"
                # Reverse: does initial file import this?
                init_imports = self.repo_map.get_imports(init)
                if any(base in imp for imp in init_imports):
                    return ScopeRelationship.DIRECT_DEPENDENCY, f"Imported in {init}"

        # Documentation
        if re.search(r"\.(md|rst|txt|adoc)$", norm, re.IGNORECASE):
            return ScopeRelationship.DOCUMENTATION, "Documentation file pattern"

        return ScopeRelationship.UNKNOWN, "No evidence found"

    def evaluate(self, path: str, operation: str, reason: str = "") -> ScopeDecision:
        """
        Evaluate whether a path/operation is within scope.
        Returns ScopeDecision with ALLOW/ASK/BLOCK.
        """
        norm = self._norm(path)
        risk = self._classifier.classify(norm, operation)

        # Hard block — CRITICAL resources
        if self._is_hard_blocked(path):
            decision = ScopeDecision(
                path=norm, operation=operation,
                reason="Hard-blocked resource (system file, git internal, private key)",
                relationship=ScopeRelationship.UNRELATED,
                evidence="Hard-blocked pattern match",
                risk=RiskLevel.CRITICAL,
                decision=ScopeDecisionResult.BLOCK,
                approval_required=False,
                task_id=self._task_id,
            )
            self._decisions.append(decision)
            return decision

        # Already in scope
        if norm in self._expanded_scope:
            decision = ScopeDecision(
                path=norm, operation=operation,
                reason="File is within established task scope",
                relationship=ScopeRelationship.DIRECT_DEPENDENCY,
                evidence="In initial or approved scope",
                risk=risk,
                decision=ScopeDecisionResult.ALLOW,
                approval_required=False,
                task_id=self._task_id,
            )
            self._decisions.append(decision)
            return decision

        # New file — derive relationship from evidence
        relationship, evidence = self._derive_relationship(path)

        # No scope set yet (no contract) → use risk to decide
        if not self._initial_scope:
            if risk == RiskLevel.CRITICAL:
                result = ScopeDecisionResult.BLOCK
            elif risk == RiskLevel.HIGH:
                result = ScopeDecisionResult.ASK
            else:
                result = ScopeDecisionResult.ALLOW
            approval = result == ScopeDecisionResult.ASK
            decision = ScopeDecision(
                path=norm, operation=operation, reason=reason or "No scope constraint active",
                relationship=relationship, evidence=evidence, risk=risk,
                decision=result, approval_required=approval, task_id=self._task_id,
            )
            self._decisions.append(decision)
            return decision

        # Has initial scope — relationship-based decision
        if relationship in (ScopeRelationship.DIRECT_DEPENDENCY, ScopeRelationship.TEST):
            if risk in (RiskLevel.LOW, RiskLevel.MEDIUM):
                result = ScopeDecisionResult.ASK  # expand but ask
            else:
                result = ScopeDecisionResult.ASK
        elif relationship == ScopeRelationship.CONFIGURATION:
            result = ScopeDecisionResult.ASK
        elif relationship == ScopeRelationship.DOCUMENTATION:
            result = ScopeDecisionResult.ASK
        elif relationship == ScopeRelationship.UNKNOWN:
            result = ScopeDecisionResult.ASK if risk != RiskLevel.CRITICAL else ScopeDecisionResult.BLOCK
        else:
            result = ScopeDecisionResult.ASK

        if risk == RiskLevel.CRITICAL:
            result = ScopeDecisionResult.BLOCK
        elif risk == RiskLevel.HIGH:
            result = ScopeDecisionResult.ASK

        decision = ScopeDecision(
            path=norm, operation=operation,
            reason=reason or f"File outside initial scope. Relationship: {relationship.value}",
            relationship=relationship, evidence=evidence, risk=risk,
            decision=result, approval_required=(result == ScopeDecisionResult.ASK),
            task_id=self._task_id,
        )
        self._decisions.append(decision)
        return decision

    def approve_expansion(self, path: str):
        """User approved expanding scope to include this path."""
        norm = self._norm(path)
        if norm not in self._expanded_scope:
            self._expanded_scope.append(norm)

    def get_decisions(self) -> List[ScopeDecision]:
        return list(self._decisions)

    def reset(self):
        self._initial_scope.clear()
        self._expanded_scope.clear()
        self._decisions.clear()
        self._task_id = ""


# ---------------------------------------------------------------------------
# ScopeMonitor — observes execution after the fact
# ---------------------------------------------------------------------------

class ScopeMonitor:
    """
    Observes completed operations and detects scope violations.
    Separate from ScopeManager (which evaluates before execution).
    ScopeMonitor.observe() is called AFTER execution.
    """

    def __init__(self):
        self._observations: List[Dict[str, Any]] = []

    def observe(self, path: str, operation: str, decision: ScopeDecision, actual_result: str):
        """Record an observation after a tool execution."""
        was_violation = (
            decision.decision == ScopeDecisionResult.BLOCK and
            "success" in actual_result.lower()
        )
        self._observations.append({
            "path": path,
            "operation": operation,
            "scope_decision": decision.decision.value,
            "risk": decision.risk.value,
            "relationship": decision.relationship.value,
            "was_violation": was_violation,
            "result_summary": actual_result[:100],
        })
        return was_violation

    def get_violations(self) -> List[Dict[str, Any]]:
        return [o for o in self._observations if o["was_violation"]]

    def get_all(self) -> List[Dict[str, Any]]:
        return list(self._observations)

    def reset(self):
        self._observations.clear()
