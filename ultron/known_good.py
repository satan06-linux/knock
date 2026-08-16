"""
known_good.py - P3.1: Known-Good Version Manager.
Records a verified commit + test results as the safe rollback baseline.
"""
import os
import json
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any


KNOWN_GOOD_PATH = os.path.join(os.path.expanduser("~"), ".ultron", "known_good.json")


def _git(args, cwd=None) -> tuple:
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def get_current_commit(workspace_root: str) -> Optional[str]:
    code, out, _ = _git(["rev-parse", "HEAD"], workspace_root)
    return out if code == 0 else None


def get_known_good() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(KNOWN_GOOD_PATH):
        return None
    try:
        with open(KNOWN_GOOD_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def set_known_good(
    commit: str,
    version: str = "unknown",
    tests_passed: int = 0,
    workspace_root: str = "",
) -> bool:
    data = {
        "commit": commit,
        "version": version,
        "tests_passed": tests_passed,
        "timestamp": datetime.now().isoformat(),
        "workspace": workspace_root,
    }
    try:
        os.makedirs(os.path.dirname(KNOWN_GOOD_PATH), exist_ok=True)
        tmp = KNOWN_GOOD_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, KNOWN_GOOD_PATH)
        return True
    except Exception:
        return False


def is_current_known_good(workspace_root: str) -> bool:
    known = get_known_good()
    if not known:
        return False
    current = get_current_commit(workspace_root)
    return current == known.get("commit")


def record_known_good_from_current(workspace_root: str, tests_passed: int = 0) -> str:
    """Record current HEAD as known-good. Returns status message."""
    commit = get_current_commit(workspace_root)
    if not commit:
        return "Error: Not a git repository or no commits."
    ok = set_known_good(commit, tests_passed=tests_passed, workspace_root=workspace_root)
    if ok:
        return f"Known-good recorded: {commit[:12]} ({tests_passed} tests passed)"
    return "Error: Failed to save known-good record."
