"""
project_profile.py - P2.5: ProjectProfile service.
Unified interface over onboard.py + repo_map.py + analyzer.py.
Answers: what files are relevant to this task? What are the entry points?
Context retrieval: fast first, deep when necessary.
"""
import os
import re
from typing import List, Dict, Any, Optional


class ProjectProfile:
    """
    Unified project intelligence service.
    Consolidates onboard, repo_map, and analyzer into one queryable interface.
    """

    def __init__(self, workspace_root: str, repo_map=None, project_memory: Dict = None):
        self.workspace_root = workspace_root
        self.repo_map = repo_map
        self.project_memory = project_memory or {}

    def get_entry_points(self) -> List[str]:
        """Return likely entry point files (main, app, server, cli, index)."""
        if not self.repo_map:
            return []
        entry_keywords = ["main", "app", "server", "index", "cli", "run", "start", "wsgi", "asgi"]
        candidates = []
        for rel_path in self.repo_map.index:
            base = os.path.splitext(os.path.basename(rel_path))[0].lower()
            if any(kw == base for kw in entry_keywords):
                candidates.append(rel_path)
        return candidates[:5]

    def get_relevant_files(self, task_description: str, max_files: int = 8) -> List[Dict[str, Any]]:
        """
        Return ranked relevant files for a task description.
        Fast first: filename/path match → symbol match → import match.
        """
        if not self.repo_map:
            return []

        from ultron.analyzer import ConventionFinder
        cf = ConventionFinder(self.workspace_root, self.repo_map)
        similar = cf.find_similar_files(task_description, max_results=max_files)

        results = []
        for rel_path in similar:
            entry = self.repo_map.index.get(rel_path, {})
            results.append({
                "file": rel_path,
                "lang": entry.get("lang", "unknown"),
                "is_test": entry.get("is_test", False),
                "symbols": [s["name"] for s in entry.get("symbols", [])[:5]],
            })
        return results

    def get_test_command(self) -> str:
        return self.project_memory.get("commands", {}).get("test", {}).get("cmd", "")

    def get_build_command(self) -> str:
        return self.project_memory.get("commands", {}).get("build", {}).get("cmd", "")

    def get_lint_command(self) -> str:
        return self.project_memory.get("commands", {}).get("lint", {}).get("cmd", "")

    def get_project_type(self) -> str:
        return self.project_memory.get("project_type", "Generic")

    def get_summary(self) -> Dict[str, Any]:
        rm_summary = self.repo_map.get_summary() if self.repo_map else {}
        return {
            "project_type": self.get_project_type(),
            "entry_points": self.get_entry_points(),
            "test_command": self.get_test_command(),
            "build_command": self.get_build_command(),
            "repo_map": rm_summary,
        }

    def invalidate_file(self, rel_path: str):
        """Called after a file is written — remove from repo_map cache for re-index."""
        if self.repo_map:
            norm = rel_path.replace(os.sep, "/")
            if norm in self.repo_map.index:
                del self.repo_map.index[norm]
