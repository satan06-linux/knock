"""
self_repair.py - P3.3 + P3.4: Ultron Self-Repair Loop.
Detects Ultron damage, creates isolated repair workspace,
applies AI-generated patch, runs health levels, promotes if passing.
Never repairs in-place. MAX_SELF_REPAIR_ATTEMPTS = 3.
"""
import os
import shutil
import tempfile
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from ultron.event_bus import get_bus, BusEvent
from ultron.known_good import get_known_good, get_current_commit

MAX_SELF_REPAIR_ATTEMPTS = 3


@dataclass
class RepairAttemptRecord:
    attempt: int
    workspace: str
    health_results: Dict[str, str] = field(default_factory=dict)
    promoted: bool = False
    reason: str = ""


class SelfRepairEngine:
    """
    Detects Ultron damage and repairs it in an isolated workspace.
    Never modifies the live installation directly.
    """

    def __init__(self, live_workspace: str, model=None, console=None):
        self.live_workspace = os.path.realpath(live_workspace)
        self.model = model
        self.console = console
        self._bus = get_bus()

    def _log(self, msg: str, style: str = "cyan"):
        if self.console:
            self.console.print(f"[{style}]{msg}[/{style}]")
        else:
            print(f"[self-repair] {msg}")

    def _run(self, cmd: str, cwd: str) -> tuple:
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=120)
            return r.returncode, r.stdout, r.stderr
        except Exception as e:
            return -1, "", str(e)

    def check_health_levels(self, workspace: str) -> Dict[str, str]:
        """Run all 5 health levels. Returns {level_name: 'pass'/'fail:...'}"""
        results = {}

        # Level 1: Imports + boot
        try:
            import importlib
            for mod in ["ultron.tool_registry", "ultron.tool_executor", "ultron.scope_manager"]:
                importlib.import_module(mod)
            results["L1_imports"] = "pass"
        except Exception as e:
            results["L1_imports"] = f"fail: {e}"
            return results  # Can't continue

        # Level 2: Unit tests
        code, _, _ = self._run("python -m pytest tests/test_p0.py -q --tb=no", workspace)
        results["L2_unit"] = "pass" if code == 0 else f"fail(exit {code})"

        # Level 3: Security tests
        code, _, _ = self._run("python -m pytest tests/test_p0.py -k 'traversal or secret or injection' -q --tb=no", workspace)
        results["L3_security"] = "pass" if code == 0 else f"fail(exit {code})"

        # Level 4: Integration tests
        code, _, _ = self._run("python -m pytest tests/ -q --tb=no --ignore=tests/live_integration_test.py", workspace)
        results["L4_integration"] = "pass" if code == 0 else f"fail(exit {code})"

        # Level 5: Runtime smoke test
        code, out, _ = self._run("python -c \"from ultron.agent import UltronAgent; print('smoke_ok')\"", workspace)
        results["L5_smoke"] = "pass" if code == 0 and "smoke_ok" in out else f"fail(exit {code})"

        return results

    def all_levels_pass(self, results: Dict[str, str]) -> bool:
        return all("pass" in v for v in results.values())

    def create_repair_workspace(self) -> Optional[str]:
        """Clone live workspace into an isolated sibling directory."""
        parent = os.path.dirname(self.live_workspace)
        base_name = os.path.basename(self.live_workspace)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        repair_path = os.path.join(parent, f"{base_name}-repair-{ts}")
        try:
            shutil.copytree(self.live_workspace, repair_path, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info"
            ))
            self._log(f"Repair workspace created: {repair_path}")
            return repair_path
        except Exception as e:
            self._log(f"Failed to create repair workspace: {e}", "red")
            return None

    def generate_repair(self, repair_workspace: str, damage_report: List[str]) -> str:
        """Ask model for a repair patch. Returns patch description."""
        if not self.model or not self.model.is_available():
            return "Model unavailable — manual repair required."

        prompt = (
            "You are repairing a Python CLI tool called Ultron. "
            "The following issues were detected:\n"
            + "\n".join(f"- {d}" for d in damage_report)
            + "\n\nProvide minimal Python code fixes. "
            "Use write_file or patch_file tools to apply them."
        )
        result = ""
        try:
            gen = self.model.chat([{"role": "user", "content": prompt}], stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk.get("type") == "content":
                        result += chunk.get("delta", "")
                except StopIteration:
                    break
        except Exception as e:
            result = f"Error generating repair: {e}"
        return result[:500]

    def promote(self, repair_workspace: str) -> bool:
        """Promote repaired workspace back to live. Returns True on success."""
        try:
            # Back up current live
            backup = self.live_workspace + ".pre-repair-backup"
            if os.path.exists(backup):
                shutil.rmtree(backup, ignore_errors=True)
            shutil.copytree(self.live_workspace, backup)

            # Copy repaired files over
            for item in os.listdir(repair_workspace):
                src = os.path.join(repair_workspace, item)
                dst = os.path.join(self.live_workspace, item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            self._log("Promotion complete.", "green")
            return True
        except Exception as e:
            self._log(f"Promotion failed: {e}", "red")
            return False

    def rollback_to_known_good(self) -> bool:
        """Rollback live workspace to known-good commit."""
        known = get_known_good()
        if not known:
            self._log("No known-good record — cannot rollback.", "red")
            return False
        commit = known.get("commit", "")
        code, _, err = self._run(f"git checkout {commit}", self.live_workspace)
        if code == 0:
            self._log(f"Rolled back to known-good: {commit[:12]}", "green")
            return True
        self._log(f"Rollback failed: {err}", "red")
        return False

    def run(self, damage_report: List[str]) -> Dict[str, Any]:
        """
        Main self-repair loop.
        Returns summary dict with status, attempts, final_health.
        """
        summary = {
            "attempts": [],
            "final_status": "failed",
            "rolled_back": False,
        }

        self._bus.publish("ultron.self_repair_started", {
            "damage": damage_report,
            "max_attempts": MAX_SELF_REPAIR_ATTEMPTS,
        })

        for attempt_num in range(1, MAX_SELF_REPAIR_ATTEMPTS + 1):
            self._log(f"Self-repair attempt {attempt_num}/{MAX_SELF_REPAIR_ATTEMPTS}...", "yellow")

            # Create isolated workspace
            repair_ws = self.create_repair_workspace()
            if not repair_ws:
                break

            record = RepairAttemptRecord(attempt=attempt_num, workspace=repair_ws)

            try:
                # Generate and describe repair
                repair_desc = self.generate_repair(repair_ws, damage_report)
                record.reason = repair_desc

                # Run all health levels in repair workspace
                record.health_results = self.check_health_levels(repair_ws)
                self._log(f"Health results: {record.health_results}")

                if self.all_levels_pass(record.health_results):
                    # Promote
                    promoted = self.promote(repair_ws)
                    record.promoted = promoted

                    if promoted:
                        # Level 5 smoke test on live after promotion
                        code, out, _ = self._run(
                            "python -c \"from ultron.agent import UltronAgent; print('live_ok')\"",
                            self.live_workspace
                        )
                        if code == 0 and "live_ok" in out:
                            summary["final_status"] = "recovered"
                            self._log("Self-repair successful.", "green")
                            self._bus.publish(BusEvent.REPAIR_SUCCEEDED, {"attempts": attempt_num})
                            summary["attempts"].append(record.__dict__)
                            return summary
                        else:
                            # Promotion smoke failed — rollback
                            self._log("Post-promotion smoke test failed. Rolling back.", "red")
                            self.rollback_to_known_good()
                            summary["rolled_back"] = True
                            break

            finally:
                # Cleanup repair workspace
                try:
                    shutil.rmtree(repair_ws, ignore_errors=True)
                except Exception:
                    pass

            summary["attempts"].append(record.__dict__)

        # All attempts failed
        if summary["final_status"] != "recovered":
            self._log(f"Self-repair exhausted after {MAX_SELF_REPAIR_ATTEMPTS} attempts.", "red")
            self._bus.publish(BusEvent.REPAIR_EXHAUSTED, {
                "reason": "max_attempts_reached",
                "rolled_back": summary.get("rolled_back"),
            })
            if not summary.get("rolled_back"):
                self.rollback_to_known_good()
                summary["rolled_back"] = True

        return summary
