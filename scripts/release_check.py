"""
release_check.py - P5.4: Release Readiness Automation.
Runs: full test suite + eval harness + security adversarial tests + records known-good.
Generates release report. Exits 0 only if everything passes.

Usage:
    python scripts/release_check.py
    python scripts/release_check.py --output release_report.json
    python scripts/release_check.py --record-known-good
"""
import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def _run(cmd: str, cwd: str = None) -> tuple:
    start = time.time()
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd or ".", capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout, r.stderr, time.time() - start
    except Exception as e:
        return -1, "", str(e), time.time() - start


def main():
    parser = argparse.ArgumentParser(description="Ultron Release Readiness Check")
    parser.add_argument("--output", default=None, help="Write JSON report to file")
    parser.add_argument("--record-known-good", action="store_true",
                        help="Record current commit as known-good if all checks pass")
    args = parser.parse_args()

    console = Console()
    console.print(Panel("[bold magenta]Ultron Release Readiness Check[/bold magenta]", expand=False))

    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    results = []
    all_passed = True

    checks = [
        {
            "name": "Full Test Suite",
            "cmd": "python -m pytest tests/ -q --tb=short --ignore=tests/live_integration_test.py",
            "critical": True,
        },
        {
            "name": "P0 Security Tests",
            "cmd": "python -m pytest tests/test_p0.py -v --tb=short",
            "critical": True,
        },
        {
            "name": "Adversarial: Path Traversal",
            "cmd": "python -m pytest tests/test_p0.py -k 'traversal or symlink' -v --tb=short",
            "critical": True,
        },
        {
            "name": "Adversarial: Secret Redaction",
            "cmd": "python -m pytest tests/test_p0.py -k 'secret' -v --tb=short",
            "critical": True,
        },
        {
            "name": "Adversarial: Injection Detection",
            "cmd": "python -m pytest tests/test_p0.py -k 'injection' -v --tb=short",
            "critical": True,
        },
        {
            "name": "Eval Harness",
            "cmd": "python scripts/run_eval.py --output /tmp/eval_release.json",
            "critical": False,
        },
    ]

    table = Table(show_header=True, header_style="bold white")
    table.add_column("Check", style="bold white")
    table.add_column("Result", width=10)
    table.add_column("Duration", width=10)
    table.add_column("Critical", width=10)

    for check in checks:
        code, stdout, stderr, duration = _run(check["cmd"], workspace)
        passed = code == 0
        if not passed and check["critical"]:
            all_passed = False

        icon = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        crit = "[red]YES[/red]" if check["critical"] else "[dim]no[/dim]"
        table.add_row(check["name"], icon, f"{duration:.1f}s", crit)

        results.append({
            "check": check["name"],
            "passed": passed,
            "critical": check["critical"],
            "exit_code": code,
            "duration": round(duration, 2),
            "stdout_tail": stdout[-300:] if stdout else "",
            "stderr_tail": stderr[-200:] if stderr else "",
        })

    console.print(table)

    # Test count from full suite output
    try:
        code, out, _, _ = _run(
            "python -m pytest tests/ -q --ignore=tests/live_integration_test.py --co -q 2>/dev/null | tail -1",
            workspace
        )
        test_count_line = out.strip().splitlines()[-1] if out.strip() else "unknown"
    except Exception:
        test_count_line = "unknown"

    # Build report
    report = {
        "timestamp": datetime.now().isoformat(),
        "workspace": workspace,
        "all_passed": all_passed,
        "test_count": test_count_line,
        "checks": results,
        "known_good_recorded": False,
    }

    if all_passed and args.record_known_good:
        from ultron.known_good import record_known_good_from_current
        msg = record_known_good_from_current(workspace)
        console.print(f"[green]* {msg}[/green]")
        report["known_good_recorded"] = True

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        console.print(f"[dim]Report saved to {args.output}[/dim]")

    if all_passed:
        console.print("\n[bold green]✓ RELEASE READY — All critical checks passed.[/bold green]")
    else:
        console.print("\n[bold red]✗ NOT RELEASE READY — Critical checks failed.[/bold red]")
        failed = [r["check"] for r in results if not r["passed"] and r["critical"]]
        for f in failed:
            console.print(f"  [red]- {f}[/red]")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
