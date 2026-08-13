"""
verifier.py - Phase 3: Verification Gate

Provides a structured /verify command that runs read-only checks and reports
results in four categories: passed, failed, not_run, skipped_by_user, skipped_mode.

Design constraints:
  - ALL commands run via tools.execute_command_with_policy() — the shared central
    runner. No direct subprocess calls.
  - Format checks use --check / --dry-run variants only. No file mutations here.
  - Secret scanning is done via Python regex (CodeReviewer.run_static_checks),
    not shell grep/pipe — Windows portable.
  - In ask/plan modes: only READONLY_CATEGORIES are available. Others get
    status=skipped_mode.
  - Never prints "✓ Verified" unless overall == "passed".
"""

import os
import json
import hashlib
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Literal, Dict, Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CheckStatus = Literal["passed", "failed", "not_run", "skipped_by_user", "skipped_mode"]

READONLY_CATEGORIES = {"tests", "lint", "format_check", "typecheck"}


@dataclass
class VerificationCheck:
    name: str
    category: str
    status: CheckStatus
    command: Optional[str] = None
    output: Optional[str] = None       # Truncated to 2000 chars
    evidence: Optional[str] = None     # One-line summary for task summary block


@dataclass
class VerificationReport:
    timestamp: str
    checks: List[VerificationCheck]
    overall: Literal["passed", "failed", "partial", "not_run"]
    # "passed" only when ≥1 check passed and 0 failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workspace_hash(workspace_root: str) -> str:
    return hashlib.md5(os.path.abspath(workspace_root).encode()).hexdigest()[:12]


def _verify_dir(workspace_root: str) -> str:
    base = os.path.join(os.path.expanduser("~"), ".ultron", "verify",
                        _workspace_hash(workspace_root))
    os.makedirs(base, exist_ok=True)
    return base


def _truncate(text: str, limit: int = 2000) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [truncated {len(text) - limit} chars] ...\n" + text[-half:]


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class Verifier:
    """
    Runs structured verification checks and returns a VerificationReport.
    All commands are dispatched through tools.execute_command_with_policy().
    """

    # Fallback command table by ecosystem detection order.
    # Each entry is a list of (candidate_command, file_indicator).
    ECOSYSTEM_FALLBACKS: Dict[str, List[tuple]] = {
        "tests": [
            ("pytest", "pytest.ini,setup.cfg,pyproject.toml,requirements.txt"),
            ("python -m unittest discover", "setup.py"),
            ("npm test", "package.json"),
            ("cargo test", "Cargo.toml"),
            ("go test ./...", "go.mod"),
        ],
        "lint": [
            ("ruff check .", "pyproject.toml,ruff.toml,.ruff.toml"),
            ("flake8", "setup.cfg,.flake8"),
            ("eslint .", "package.json,.eslintrc"),
            ("golangci-lint run", "go.mod"),
        ],
        "format_check": [
            ("ruff format --check .", "pyproject.toml,ruff.toml"),
            ("black --check .", "pyproject.toml,.black"),
            ("prettier --check .", "package.json,.prettierrc"),
        ],
        "typecheck": [
            ("mypy .", "mypy.ini,setup.cfg,pyproject.toml"),
            ("pyright", "pyrightconfig.json"),
            ("tsc --noEmit", "tsconfig.json"),
        ],
        "build": [
            ("python -m build", "pyproject.toml,setup.py"),
            ("npm run build", "package.json"),
            ("cargo build", "Cargo.toml"),
            ("go build ./...", "go.mod"),
        ],
    }

    DEFAULT_CHECKS = ["tests", "lint", "format_check", "typecheck"]

    def __init__(self, tools, project_memory: Dict[str, Any],
                 workspace_root: str, intent_mode: str = "build"):
        self.tools = tools
        self.project_memory = project_memory
        self.workspace_root = os.path.abspath(workspace_root)
        self.intent_mode = intent_mode

    # ------------------------------------------------------------------
    # Command resolution
    # ------------------------------------------------------------------

    def resolve_command(self, category: str) -> Optional[str]:
        """
        1. Look in project_memory for a verified command for this category.
        2. Fall back to ecosystem detection.
        3. Return None if nothing found.
        """
        # ProjectMemory stores commands under project_memory["commands"][category]["cmd"]
        cmds = self.project_memory.get("commands", {})
        mem_entry = cmds.get(category, {})
        if isinstance(mem_entry, dict) and mem_entry.get("cmd"):
            cmd = mem_entry["cmd"]
            # For format_check, ensure it's read-only
            if category == "format_check":
                cmd = self._ensure_check_flag(cmd)
            return cmd

        # Ecosystem fallback
        for candidate_cmd, indicators in self.ECOSYSTEM_FALLBACKS.get(category, []):
            for indicator in indicators.split(","):
                if os.path.isfile(os.path.join(self.workspace_root, indicator.strip())):
                    return candidate_cmd

        return None

    def _ensure_check_flag(self, cmd: str) -> str:
        """Ensure a format command runs in check-only mode."""
        if "ruff format" in cmd and "--check" not in cmd:
            return cmd.rstrip() + " --check"
        if "black" in cmd and "--check" not in cmd:
            return cmd.rstrip() + " --check"
        if "prettier" in cmd and "--check" not in cmd:
            return cmd.rstrip() + " --check"
        return cmd

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, checks: Optional[List[str]] = None,
            auto_approve: bool = False,
            changed_files: Optional[List[str]] = None,
            use_planner: bool = True) -> VerificationReport:
        """
        Run each requested check category.
        If checks is None and use_planner=True, uses VerificationPlanner to
        select relevant checks based on changed files.
        """
        from rich.prompt import Confirm
        from ultron.tracer import VerificationPlanner, TestOutputParser

        # Use VerificationPlanner if no explicit checks provided
        if checks is None:
            if use_planner and changed_files is not None:
                planner = VerificationPlanner()
                checks = planner.plan(changed_files, self.project_memory)
            else:
                checks = self.DEFAULT_CHECKS

        timestamp = datetime.datetime.now().isoformat()
        results: List[VerificationCheck] = []

        for category in checks:
            check = VerificationCheck(name=category, category=category, status="not_run")

            # Mode restriction
            if self.intent_mode in ("ask", "plan") and category not in READONLY_CATEGORIES:
                check.status = "skipped_mode"
                check.evidence = f"Skipped — {category} not allowed in {self.intent_mode} mode."
                results.append(check)
                continue

            # Secrets check: Python regex against current diff (no subprocess)
            if category == "secrets":
                self._run_secrets_check(check)
                results.append(check)
                continue

            # Resolve command
            cmd = self.resolve_command(category)
            if cmd is None:
                check.status = "not_run"
                check.evidence = f"No command found for '{category}'. Run /onboard to detect commands."
                results.append(check)
                continue

            check.command = cmd

            # Approval (unless auto_approve)
            if not auto_approve:
                try:
                    approved = Confirm.ask(
                        f"[bold yellow]Verify [{category}]: Run '{cmd}'?[/bold yellow]"
                    )
                    if not approved:
                        check.status = "skipped_by_user"
                        check.evidence = f"Skipped by user: {cmd}"
                        results.append(check)
                        continue
                except Exception:
                    pass

            # Execute via shared central runner
            raw_result = self.tools.execute_command_with_policy(
                cmd,
                require_approval=False,
                context=f"Verification: {category}",
            )

            exit_code = raw_result.get("exit_code", -1)
            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "")
            combined = (stdout + "\n" + stderr).strip()
            check.output = _truncate(combined, 2000)

            if exit_code == -1 and raw_result.get("stderr") == "Declined by user.":
                check.status = "skipped_by_user"
                check.evidence = f"Declined: {cmd}"
            elif exit_code == 0:
                check.status = "passed"
                # Use TestOutputParser for structured summary
                parsed = TestOutputParser.parse(combined)
                if parsed.get("total", 0) > 0:
                    check.evidence = (
                        f"{category}: PASSED — "
                        f"{parsed['passed']} passed"
                        + (f", {parsed['skipped']} skipped" if parsed.get('skipped') else "")
                        + f" ({parsed['framework']})"
                    )
                else:
                    check.evidence = f"{category}: PASSED (exit 0)"
            else:
                check.status = "failed"
                parsed = TestOutputParser.parse(combined)
                if parsed.get("failed", 0) > 0:
                    check.evidence = (
                        f"{category}: FAILED — "
                        f"{parsed['failed']} failed, {parsed['passed']} passed"
                        + (f" ({parsed['framework']})" if parsed.get("framework") != "generic" else "")
                    )
                    # Attach failure names if available
                    if parsed.get("failures"):
                        names = ", ".join(f["name"] for f in parsed["failures"][:3])
                        check.evidence += f" [{names}]"
                else:
                    check.evidence = f"{category}: FAILED (exit {exit_code})"

            results.append(check)

        overall = self._compute_overall(results)
        return VerificationReport(timestamp=timestamp, checks=results, overall=overall)

    def _run_secrets_check(self, check: VerificationCheck) -> None:
        """
        Scan the workspace's current diff for secret patterns using Python regex.
        No subprocess / grep involved.
        """
        from ultron.reviewer import CodeReviewer
        check.command = "(Python regex scan — no subprocess)"

        try:
            reviewer = CodeReviewer(self.workspace_root)
            diff_text, _ = reviewer.collect_diff(agent=None)

            # Run static checks; filter to security findings only
            findings = reviewer.run_static_checks(diff_text)
            sec_findings = [f for f in findings if f.category == "security"]

            if sec_findings:
                check.status = "failed"
                descriptions = "; ".join(f.description for f in sec_findings[:3])
                check.output = f"Potential secrets detected:\n{descriptions}"
                check.evidence = f"secrets: FAILED — {len(sec_findings)} potential credential pattern(s)"
            else:
                check.status = "passed"
                check.output = "No secret patterns matched in current diff."
                check.evidence = "secrets: PASSED — no credential patterns found"
        except Exception as exc:
            check.status = "not_run"
            check.evidence = f"secrets: could not scan — {str(exc)}"

    def _compute_overall(self, checks: List[VerificationCheck]) -> str:
        statuses = {c.status for c in checks}
        if "failed" in statuses:
            return "failed"
        if "passed" in statuses:
            if "not_run" in statuses or "skipped_by_user" in statuses:
                return "partial"
            return "passed"
        return "not_run"

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display_report(self, report: VerificationReport, console) -> None:
        """Print the VerificationReport as a Rich table."""
        from rich.table import Table
        from rich.panel import Panel

        table = Table(show_header=True, header_style="bold white")
        table.add_column("Category", width=14)
        table.add_column("Command", width=35)
        table.add_column("Status", width=14)
        table.add_column("Evidence")

        status_colors = {
            "passed": "green",
            "failed": "red",
            "not_run": "dim",
            "skipped_by_user": "yellow",
            "skipped_mode": "dim",
        }

        for check in report.checks:
            color = status_colors.get(check.status, "white")
            table.add_row(
                check.category,
                (check.command or "—")[:35],
                f"[{color}]{check.status}[/{color}]",
                (check.evidence or "")[:80],
            )

        overall_color = {
            "passed": "green",
            "failed": "red",
            "partial": "yellow",
            "not_run": "dim",
        }.get(report.overall, "white")

        overall_text = f"[{overall_color}]{report.overall.upper()}[/{overall_color}]"

        if report.overall == "passed":
            footer = f"\n[green]✓ Verified[/green] — overall: {overall_text}"
        elif report.overall == "failed":
            footer = f"\n[red]✗ Verification failed[/red] — overall: {overall_text}"
        else:
            footer = f"\n[yellow]⚠ Task partially verified[/yellow] — overall: {overall_text}"

        console.print(Panel(
            table,
            title=f"[bold magenta]Verification Report — {report.timestamp[:19]}[/bold magenta]",
            border_style="magenta",
            expand=False,
        ))
        console.print(footer)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_report(self, report: VerificationReport) -> str:
        verify_dir = _verify_dir(self.workspace_root)
        ts = report.timestamp.replace(":", "").replace(".", "")[:15]
        path = os.path.join(verify_dir, f"{ts}.json")
        try:
            data = {
                "timestamp": report.timestamp,
                "overall": report.overall,
                "checks": [
                    {
                        "name": c.name,
                        "category": c.category,
                        "status": c.status,
                        "command": c.command,
                        "evidence": c.evidence,
                    }
                    for c in report.checks
                ],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
        return path

    def load_last_report(self) -> Optional[VerificationReport]:
        verify_dir = _verify_dir(self.workspace_root)
        try:
            files = sorted(
                [f for f in os.listdir(verify_dir) if f.endswith(".json")],
                reverse=True,
            )
            if not files:
                return None
            with open(os.path.join(verify_dir, files[0]), "r", encoding="utf-8") as f:
                data = json.load(f)
            checks = [
                VerificationCheck(
                    name=c["name"],
                    category=c["category"],
                    status=c["status"],
                    command=c.get("command"),
                    evidence=c.get("evidence"),
                )
                for c in data.get("checks", [])
            ]
            return VerificationReport(
                timestamp=data["timestamp"],
                checks=checks,
                overall=data["overall"],
            )
        except Exception:
            return None
