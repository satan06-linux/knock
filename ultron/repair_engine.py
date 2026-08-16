"""
repair_engine.py - P1.2 + P1.3: Autonomous Repair Loop + Retry Budget.
Bounded repair cycle: run tests → parse failures → targeted fix → re-verify.
Never loops indefinitely. MAX_REPAIR_ATTEMPTS = 3.
"""
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from ultron.task import Task, TaskStatus, EvidenceKind
from ultron.tracer import TestOutputParser
from ultron.event_bus import get_bus, BusEvent

MAX_REPAIR_ATTEMPTS = 3


@dataclass
class RepairAttempt:
    attempt_number: int
    test_command: str
    exit_code: int
    failures: List[str]         # failed test names
    fix_applied: str            # brief description of fix
    outcome: str                # "passed" | "failed" | "unchanged"
    duration: float


@dataclass
class RepairResult:
    task_id: str
    total_attempts: int
    final_status: str           # "resolved" | "exhausted" | "blocked"
    attempts: List[RepairAttempt] = field(default_factory=list)
    blocker_summary: str = ""


class RepairEngine:
    """
    Drives a bounded repair loop for a failing task.

    Lifecycle:
        run_tests() → parse failures → ask model for targeted fix →
        apply fix → run_tests() → repeat up to MAX_REPAIR_ATTEMPTS

    Stops at:
        - All tests pass (resolved)
        - MAX_REPAIR_ATTEMPTS reached (exhausted)
        - Task budget exceeded (blocked)
    """

    def __init__(
        self,
        task: Task,
        run_command: Callable[[str], str],    # tools.run_command
        ask_model: Callable[[str], str],      # sends prompt, returns text response
        test_command: str = "",
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        console=None,
    ):
        self.task = task
        self.run_command = run_command
        self.ask_model = ask_model
        self.test_command = test_command
        self.max_attempts = max_attempts
        self.console = console
        self._parser = TestOutputParser()
        self._bus = get_bus()

    def _log(self, msg: str, style: str = "cyan"):
        if self.console:
            self.console.print(f"[{style}]{msg}[/{style}]")

    def run(self) -> RepairResult:
        """Execute the bounded repair loop."""
        result = RepairResult(task_id=self.task.id, total_attempts=0, final_status="blocked")

        self._bus.publish(BusEvent.REPAIR_STARTED, {
            "task_id": self.task.id,
            "test_command": self.test_command,
            "max_attempts": self.max_attempts,
        })

        for attempt_num in range(1, self.max_attempts + 1):
            if self.task.is_over_budget():
                self._log(f"Repair stopped: task budget exceeded after {attempt_num-1} attempts.", "red")
                result.final_status = "blocked"
                result.blocker_summary = f"Task budget exceeded at attempt {attempt_num}"
                self.task.status = TaskStatus.BLOCKED
                self._bus.publish(BusEvent.REPAIR_FAILED, {"task_id": self.task.id, "reason": "budget_exceeded"})
                break

            self._log(f"Repair attempt {attempt_num}/{self.max_attempts}...", "cyan")
            self.task.repair_attempt_count = attempt_num
            start = time.time()

            # Step 1: Run tests
            raw_output = self.run_command(self.test_command)
            parsed = self._parser.parse(raw_output)
            duration = time.time() - start

            failures = [f["name"] for f in parsed.get("failures", [])]
            exit_code = 0 if parsed.get("overall") == "passed" else 1

            # Tests passed — repair successful
            if parsed.get("overall") == "passed":
                self._log(f"✓ Tests passed on attempt {attempt_num}.", "green")
                attempt = RepairAttempt(
                    attempt_number=attempt_num,
                    test_command=self.test_command,
                    exit_code=0,
                    failures=[],
                    fix_applied="(tests already passing)",
                    outcome="passed",
                    duration=duration,
                )
                result.attempts.append(attempt)
                result.total_attempts = attempt_num
                result.final_status = "resolved"
                self.task.add_evidence(EvidenceKind.VERIFIED, f"Tests passed after {attempt_num} repair attempt(s)", self.test_command)
                self._bus.publish(BusEvent.REPAIR_SUCCEEDED, {"task_id": self.task.id, "attempts": attempt_num})
                break

            # Step 2: Build targeted fix prompt
            failure_summary = "\n".join(failures[:5]) if failures else raw_output[-500:]
            fix_prompt = (
                f"The following tests are failing:\n{failure_summary}\n\n"
                f"Full test output (last 1000 chars):\n{raw_output[-1000:]}\n\n"
                "Identify the root cause and provide the minimal fix. "
                "Use write_file or patch_file to apply it."
            )

            # Step 3: Ask model for fix (model will call tools via agent)
            self._log(f"Asking model to fix {len(failures)} failure(s)...", "yellow")
            fix_description = "model-driven fix"
            try:
                fix_description = self.ask_model(fix_prompt)
            except Exception as e:
                fix_description = f"model error: {e}"

            attempt = RepairAttempt(
                attempt_number=attempt_num,
                test_command=self.test_command,
                exit_code=exit_code,
                failures=failures,
                fix_applied=fix_description[:200],
                outcome="failed",
                duration=duration,
            )
            result.attempts.append(attempt)

        else:
            # Loop exhausted without breaking
            result.total_attempts = self.max_attempts
            result.final_status = "exhausted"
            result.blocker_summary = (
                f"Could not resolve failures after {self.max_attempts} attempts. "
                f"Last failures: {', '.join(result.attempts[-1].failures[:3]) if result.attempts else 'unknown'}"
            )
            self.task.status = TaskStatus.BLOCKED
            self._log(f"Repair exhausted after {self.max_attempts} attempts. Task blocked.", "red")
            self._log(f"Blocker: {result.blocker_summary}", "yellow")
            self._bus.publish(BusEvent.REPAIR_EXHAUSTED, {
                "task_id": self.task.id,
                "attempts": self.max_attempts,
                "blocker": result.blocker_summary,
            })

        return result
