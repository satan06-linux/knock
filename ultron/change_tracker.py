"""
change_tracker.py - Workstream C: ChangeTracker.
Unified tracker combining checkpoint + contract + git state + user-dirty files.
"""
import os
import hashlib
from typing import Dict, List, Set, Optional, Any


class ChangeTracker:
    """
    Tracks the full state of a task's changes:
    - Expected files (from contract or plan)
    - Actually modified files
    - File hashes before/after
    - User-dirty files (pre-existing uncommitted changes)
    - Git state snapshot
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.expected_files: List[str] = []
        self.actual_files: Dict[str, Dict[str, Any]] = {}  # rel_path -> metadata
        self.user_dirty_files: Set[str] = set()
        self._git_state_before: Optional[str] = None

    def set_expected(self, files: List[str]):
        self.expected_files = [f.replace(os.sep, "/") for f in files]

    def snapshot_git_state(self):
        """Record git status before task starts."""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True, text=True, timeout=10
            )
            self._git_state_before = r.stdout.strip()
        except Exception:
            self._git_state_before = None

    def mark_user_dirty(self, rel_path: str):
        self.user_dirty_files.add(rel_path.replace(os.sep, "/"))

    def is_user_dirty(self, rel_path: str) -> bool:
        return rel_path.replace(os.sep, "/") in self.user_dirty_files

    def record_before(self, rel_path: str):
        """Record file state before editing."""
        norm = rel_path.replace(os.sep, "/")
        if norm in self.actual_files:
            return  # already recorded
        abs_path = os.path.join(self.workspace_root, rel_path)
        existed = os.path.isfile(abs_path)
        hash_before = self._sha256(abs_path) if existed else None
        self.actual_files[norm] = {
            "existed_before": existed,
            "hash_before": hash_before,
            "hash_after": None,
        }

    def record_after(self, rel_path: str):
        """Record file state after editing."""
        norm = rel_path.replace(os.sep, "/")
        abs_path = os.path.join(self.workspace_root, rel_path)
        if norm not in self.actual_files:
            self.actual_files[norm] = {
                "existed_before": False,
                "hash_before": None,
                "hash_after": None,
            }
        self.actual_files[norm]["hash_after"] = self._sha256(abs_path) if os.path.isfile(abs_path) else None

    def is_unplanned(self, rel_path: str) -> bool:
        """True if file is not in expected_files."""
        norm = rel_path.replace(os.sep, "/")
        if not self.expected_files:
            return False
        return norm not in self.expected_files

    def get_modified_files(self) -> List[str]:
        return list(self.actual_files.keys())

    def get_unplanned_files(self) -> List[str]:
        return [f for f in self.actual_files if self.is_unplanned(f)]

    def get_scope_delta(self) -> Dict[str, Any]:
        """
        Returns analysis of actual vs expected scope.
        Used to decide if task exceeded agreed change budget.
        """
        modified = set(self.actual_files.keys())
        expected = set(self.expected_files)
        unplanned = modified - expected
        missing = expected - modified

        return {
            "expected_count": len(expected),
            "actual_count": len(modified),
            "unplanned_files": list(unplanned),
            "unmodified_expected": list(missing),
            "scope_exceeded": len(unplanned) > 0 and len(expected) > 0,
        }

    def reset(self):
        self.expected_files.clear()
        self.actual_files.clear()
        self.user_dirty_files.clear()
        self._git_state_before = None

    @staticmethod
    def _sha256(path: str) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None
