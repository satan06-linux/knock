"""
recovery_bootstrap.py - P3.2: Recovery Bootstrap.
Tiny, non-AI, deterministic bootstrap that can restore known-good code.
Does NOT use agent.py. Survives even if normal agent init fails.
CLI: ultron-recover
"""
import os
import sys
import json
import subprocess
from typing import Optional


def _run(cmd, cwd=None) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def _print(msg: str):
    print(f"[ultron-recover] {msg}", flush=True)


def detect_damage(workspace_root: str) -> list:
    """Detect common damage indicators. Returns list of issues found."""
    issues = []
    # 1. Core modules importable?
    critical_modules = [
        "ultron.tool_registry",
        "ultron.tool_executor",
        "ultron.scope_manager",
        "ultron.audit",
    ]
    for mod in critical_modules:
        try:
            __import__(mod)
        except Exception as e:
            issues.append(f"Import failed: {mod} — {e}")

    # 2. Workspace readable
    if not os.path.isdir(workspace_root):
        issues.append(f"Workspace not accessible: {workspace_root}")

    # 3. Pytest available
    code, _, _ = _run("python -m pytest --version")
    if code != 0:
        issues.append("pytest not available")

    return issues


def restore_known_good(workspace_root: str) -> bool:
    """Restore workspace to known-good commit via git checkout."""
    from ultron.known_good import get_known_good
    known = get_known_good()
    if not known:
        _print("No known-good record found. Cannot restore.")
        return False

    commit = known.get("commit")
    if not commit:
        _print("Known-good record has no commit hash.")
        return False

    _print(f"Restoring to known-good commit: {commit[:12]}")
    code, out, err = _run(f"git checkout {commit}", cwd=workspace_root)
    if code != 0:
        _print(f"Git checkout failed: {err}")
        return False

    _print("Restore complete.")
    return True


def run_health_checks() -> dict:
    """Run basic health checks. Returns {level: pass/fail}."""
    results = {}

    # Level 1: imports
    try:
        from ultron.tool_registry import ToolRegistry
        from ultron.tool_executor import ToolExecutor
        from ultron.scope_manager import ScopeManager
        results["level1_imports"] = "pass"
    except Exception as e:
        results["level1_imports"] = f"fail: {e}"
        return results  # Can't continue

    # Level 2: basic unit tests (fast only)
    code, out, err = _run("python -m pytest tests/test_p0.py -q --tb=line --timeout=30")
    results["level2_unit_tests"] = "pass" if code == 0 else f"fail (exit {code})"

    # Level 3: security tests
    code, out, err = _run("python -m pytest tests/test_p0.py -k 'traversal or injection or secret' -q --tb=line")
    results["level3_security"] = "pass" if code == 0 else f"fail (exit {code})"

    return results


def bootstrap_recover(workspace_root: str, auto_restore: bool = False) -> int:
    """
    Main recovery entry point.
    Returns 0 if healthy or recovered, 1 if still damaged.
    """
    _print("Starting recovery bootstrap...")
    _print(f"Workspace: {workspace_root}")

    # Step 1: Detect damage
    issues = detect_damage(workspace_root)
    if not issues:
        _print("No damage detected. Ultron appears healthy.")
        checks = run_health_checks()
        for level, status in checks.items():
            _print(f"  {level}: {status}")
        all_pass = all("pass" in str(v) for v in checks.values())
        return 0 if all_pass else 1

    _print(f"Damage detected ({len(issues)} issue(s)):")
    for issue in issues:
        _print(f"  - {issue}")

    if not auto_restore:
        _print("Run with --auto-restore to automatically restore known-good version.")
        return 1

    # Step 2: Restore
    restored = restore_known_good(workspace_root)
    if not restored:
        _print("Recovery failed — manual intervention required.")
        return 1

    # Step 3: Health checks
    checks = run_health_checks()
    for level, status in checks.items():
        _print(f"  {level}: {status}")

    all_pass = all("pass" in str(v) for v in checks.values())
    if all_pass:
        _print("Recovery successful. Ultron is healthy.")
        return 0
    else:
        _print("Health checks failed after restore. Manual intervention required.")
        return 1


def cli_main():
    """Entry point for ultron-recover command."""
    import argparse
    parser = argparse.ArgumentParser(description="Ultron Recovery Bootstrap")
    parser.add_argument("--workspace", default=".", help="Workspace path")
    parser.add_argument("--auto-restore", action="store_true", help="Auto-restore known-good on damage")
    args = parser.parse_args()
    workspace = os.path.realpath(os.path.abspath(args.workspace))
    sys.exit(bootstrap_recover(workspace, auto_restore=args.auto_restore))
