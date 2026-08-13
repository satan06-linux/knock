"""
contract.py - Phase 3: Change Contract Engine

Provides a formal change contract that is created before any multi-file build
or fix task.  The contract records:
  - The goal and behavioural invariants
  - The expected list of files that may be edited
  - Verification steps that must be satisfied before closing the task

Every file write is validated against the contract BEFORE the write occurs.
Unplanned files require explicit user approval. A configurable hard ceiling
(max_files) applies to the total contract file list.

Storage: ~/.ultron/contracts/<workspace_hash>/active.json
         (outside the workspace so git trees are kept clean)
"""

import os
import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Literal, Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workspace_hash(workspace_root: str) -> str:
    return hashlib.md5(os.path.abspath(workspace_root).encode()).hexdigest()[:12]


def _contracts_dir(workspace_root: str) -> str:
    base = os.path.join(os.path.expanduser("~"), ".ultron", "contracts",
                        _workspace_hash(workspace_root))
    os.makedirs(base, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ChangeContract:
    goal: str
    preserved_behaviors: List[str]
    new_behaviors: List[str]
    expected_files: List[str]        # Relative workspace paths; grows on user approval
    verification_steps: List[str]
    completed_touches: List[str] = field(default_factory=list)
    status: str = "active"           # "draft" | "active" | "completed" | "abandoned"
    evidence: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ChangeContractManager:
    """
    Manages the lifecycle of a ChangeContract:
      - Validates plan schemas before contract creation
      - Enforces file-write scope BEFORE any write occurs
      - Persists the active contract to disk for inspection via /contract
    """

    DEFAULT_POLICY = "ask"   # "ask" | "block"
    DEFAULT_MAX_FILES = 8

    def __init__(self, workspace_root: str, toml_config: Optional[Dict[str, Any]] = None):
        self.workspace_root = os.path.abspath(workspace_root)
        self._contracts_dir = _contracts_dir(workspace_root)
        self._active_path = os.path.join(self._contracts_dir, "active.json")
        self._contract: Optional[ChangeContract] = None

        # Read [contracts] section from toml_config if provided
        cfg = (toml_config or {}).get("contracts", {})
        self.policy: str = cfg.get("unplanned_file_policy", self.DEFAULT_POLICY)
        self.max_files: int = int(cfg.get("max_files", self.DEFAULT_MAX_FILES))

    # ------------------------------------------------------------------
    # Schema validation
    # ------------------------------------------------------------------

    def validate_plan(self, plan: Dict[str, Any]) -> tuple:
        """
        Returns (is_valid: bool, error_message: str).
        A plan must have: goal (non-empty str), expected_files (non-empty list),
        verification_steps (non-empty list), new_behaviors (non-empty list).
        """
        if not isinstance(plan.get("goal"), str) or not plan["goal"].strip():
            return False, "Plan is missing a non-empty 'goal'."
        if not isinstance(plan.get("expected_files"), list) or len(plan["expected_files"]) == 0:
            return False, "Plan must list at least one file in 'expected_files'."
        if not isinstance(plan.get("verification_steps"), list) or len(plan["verification_steps"]) == 0:
            return False, "Plan must include at least one 'verification_steps' entry."
        if not isinstance(plan.get("new_behaviors"), list) or len(plan["new_behaviors"]) == 0:
            return False, "Plan must describe at least one 'new_behaviors' entry."
        return True, ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def new_contract(self, plan: Dict[str, Any]) -> ChangeContract:
        """
        Create and persist a new active contract from a validated plan dict.
        Raises ValueError if plan is invalid — caller must request a corrected plan first.
        """
        valid, err = self.validate_plan(plan)
        if not valid:
            raise ValueError(f"Cannot start contract — invalid plan: {err}")

        self._contract = ChangeContract(
            goal=plan["goal"],
            preserved_behaviors=plan.get("preserved_behaviors", []),
            new_behaviors=plan["new_behaviors"],
            expected_files=list(plan["expected_files"]),
            verification_steps=plan["verification_steps"],
        )
        self._persist()
        return self._contract

    def load_active(self) -> Optional[ChangeContract]:
        """Load the persisted active contract from disk (if any)."""
        if self._contract is not None:
            return self._contract
        if os.path.isfile(self._active_path):
            try:
                with open(self._active_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._contract = ChangeContract(**data)
                return self._contract
            except Exception:
                pass
        return None

    def clear(self) -> None:
        """Abandon the active contract and remove from disk."""
        self._contract = None
        if os.path.isfile(self._active_path):
            try:
                os.remove(self._active_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Pre-write scope enforcement
    # ------------------------------------------------------------------

    def check_before_write(self, path: str) -> Literal["allow", "ask", "block"]:
        """
        Must be called BEFORE any write_file or patch_file.

        Returns:
          "allow"  → path is already in expected_files
          "ask"    → path is new, policy == "ask", and total files < max_files
          "block"  → max_files reached, or policy == "block" for new files,
                     or no active contract (callers should still check mode)
        """
        contract = self.load_active()
        if contract is None:
            # No contract active — no scope restriction (single-file tasks in build mode)
            return "allow"

        # Normalise path to forward slashes for consistent comparison
        norm = path.replace(os.sep, "/").lstrip("./")
        norm_expected = [e.replace(os.sep, "/").lstrip("./") for e in contract.expected_files]

        if norm in norm_expected:
            return "allow"

        # New file — check ceiling first (ceiling applies regardless of policy)
        total = len(contract.expected_files)
        if total >= self.max_files:
            return "block"

        if self.policy == "block":
            return "block"

        return "ask"

    def approve_unplanned_file(self, path: str) -> None:
        """Add a user-approved unplanned file to expected_files and persist."""
        contract = self.load_active()
        if contract is None:
            return
        if path not in contract.expected_files:
            contract.expected_files.append(path)
            self._persist()

    def record_completed_touch(self, path: str) -> None:
        """Record a file that was successfully written (post-write confirmation)."""
        contract = self.load_active()
        if contract is None:
            return
        if path not in contract.completed_touches:
            contract.completed_touches.append(path)
            self._persist()

    def complete_contract(self, evidence: Dict[str, Any]) -> None:
        """Mark the contract as completed and attach verification evidence."""
        contract = self.load_active()
        if contract is None:
            return
        contract.status = "completed"
        contract.evidence = evidence
        self._persist()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display(self, console) -> None:
        """Print the active contract as a Rich panel."""
        from rich.panel import Panel
        from rich.table import Table

        contract = self.load_active()
        if contract is None:
            console.print("[yellow]No active change contract.[/yellow]")
            return

        status_color = {
            "active": "cyan",
            "completed": "green",
            "abandoned": "red",
            "draft": "yellow",
        }.get(contract.status, "white")

        table = Table(box=None, show_header=False)
        table.add_row("[bold white]Goal:[/bold white]", contract.goal)
        table.add_row("[bold white]Status:[/bold white]",
                      f"[{status_color}]{contract.status.upper()}[/{status_color}]")
        table.add_row("[bold white]Policy:[/bold white]",
                      f"{self.policy} | max_files={self.max_files}")
        table.add_row("[bold white]Expected files:[/bold white]",
                      "\n".join(f"  [cyan]{f}[/cyan]" for f in contract.expected_files) or "  (none)")
        table.add_row("[bold white]Written so far:[/bold white]",
                      "\n".join(f"  [green]✓ {f}[/green]" for f in contract.completed_touches) or "  (none)")
        table.add_row("[bold white]Verification steps:[/bold white]",
                      "\n".join(f"  • {s}" for s in contract.verification_steps) or "  (none)")
        if contract.preserved_behaviors:
            table.add_row("[bold white]Preserved behaviors:[/bold white]",
                          "\n".join(f"  • {b}" for b in contract.preserved_behaviors))
        if contract.evidence:
            table.add_row("[bold white]Evidence:[/bold white]",
                          str(contract.evidence)[:400])

        console.print(Panel(
            table,
            title="[bold magenta]Active Change Contract[/bold magenta]",
            border_style="magenta",
            expand=False,
        ))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if self._contract is None:
            return
        try:
            with open(self._active_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self._contract), f, indent=2)
        except Exception:
            pass
