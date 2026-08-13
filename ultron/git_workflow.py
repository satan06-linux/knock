"""
git_workflow.py - Phase 4: Professional Git workflow.
Covers worktrees, PR summaries, commit quality checks, and decision log.
"""
import os
import re
import json
import hashlib
import subprocess
import tempfile
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_git(args: List[str], cwd: str, input_bytes: bytes = None) -> Tuple[int, str, str]:
    """Run a git command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_bytes,
            timeout=30,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace")
    except Exception as e:
        return -1, "", str(e)


def _is_git_repo(cwd: str) -> bool:
    code, _, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return code == 0


# ---------------------------------------------------------------------------
# Worktree manager
# ---------------------------------------------------------------------------

class WorktreeManager:
    """Manage Git worktrees for isolated risky work."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def list_worktrees(self) -> List[Dict[str, str]]:
        """Return list of {path, branch, commit} for all worktrees."""
        code, out, _ = _run_git(["worktree", "list", "--porcelain"], self.workspace_root)
        if code != 0:
            return []
        worktrees = []
        current: Dict[str, str] = {}
        for line in out.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["commit"] = line[5:13]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "bare":
                current["branch"] = "(bare)"
        if current:
            worktrees.append(current)
        return worktrees

    def create_worktree(self, branch_name: str, base_branch: str = "HEAD") -> Tuple[bool, str]:
        """Create a new worktree with a new branch."""
        if not _is_git_repo(self.workspace_root):
            return False, "Not a git repository."

        # Worktree path: sibling dir named after branch
        safe_branch = branch_name.replace("/", "-").replace(" ", "-")
        parent = os.path.dirname(self.workspace_root)
        worktree_path = os.path.join(parent, f"{os.path.basename(self.workspace_root)}-{safe_branch}")

        if os.path.exists(worktree_path):
            return False, f"Path already exists: {worktree_path}"

        code, out, err = _run_git(
            ["worktree", "add", "-b", branch_name, worktree_path, base_branch],
            self.workspace_root
        )
        if code != 0:
            return False, f"Failed to create worktree: {err.strip()}"
        return True, worktree_path

    def remove_worktree(self, worktree_path: str, force: bool = False) -> Tuple[bool, str]:
        """Remove a worktree."""
        args = ["worktree", "remove", worktree_path]
        if force:
            args.append("--force")
        code, _, err = _run_git(args, self.workspace_root)
        if code != 0:
            return False, f"Failed to remove worktree: {err.strip()}"
        return True, f"Removed worktree: {worktree_path}"


# ---------------------------------------------------------------------------
# PR Summary generator
# ---------------------------------------------------------------------------

class PRSummaryGenerator:
    """Generate a PR-ready summary from the current diff and task context."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def get_diff_stats(self) -> Dict[str, Any]:
        """Get changed files and line counts."""
        code, out, _ = _run_git(["diff", "HEAD", "--stat"], self.workspace_root)
        if code != 0:
            # Try staged
            code, out, _ = _run_git(["diff", "--cached", "--stat"], self.workspace_root)
        files_changed = []
        for line in out.splitlines():
            m = re.match(r"^\s*([\w./\\-]+)\s*\|", line)
            if m:
                files_changed.append(m.group(1).strip())
        return {"stat_output": out.strip(), "files_changed": files_changed}

    def get_commits_since_base(self, base: str = "main") -> List[Dict[str, str]]:
        """Get commits since base branch."""
        code, out, _ = _run_git(
            ["log", f"{base}..HEAD", "--pretty=format:%h|%s|%an|%ad", "--date=short"],
            self.workspace_root
        )
        if code != 0:
            return []
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "subject": parts[1], "author": parts[2], "date": parts[3]})
        return commits

    def generate(
        self,
        title: str,
        description: str,
        test_evidence: str,
        risks: str,
        migration_notes: str,
        reviewer_checklist: List[str],
        base_branch: str = "main",
    ) -> str:
        """Produce a formatted PR summary markdown string."""
        stats = self.get_diff_stats()
        commits = self.get_commits_since_base(base_branch)

        lines = [
            f"# {title}",
            "",
            "## Summary",
            description,
            "",
            "## Changes",
            stats["stat_output"] or "_No diff detected._",
            "",
        ]

        if commits:
            lines += ["## Commits", ""]
            for c in commits[:20]:
                lines.append(f"- `{c['hash']}` {c['subject']} _{c['author']} {c['date']}_")
            lines.append("")

        lines += [
            "## Test Evidence",
            test_evidence or "_No test evidence provided._",
            "",
            "## Risks",
            risks or "_None identified._",
            "",
        ]

        if migration_notes:
            lines += ["## Migration Notes", migration_notes, ""]

        if reviewer_checklist:
            lines += ["## Reviewer Checklist", ""]
            for item in reviewer_checklist:
                lines.append(f"- [ ] {item}")
            lines.append("")

        return "\n".join(lines)

    def generate_with_ai(self, agent_model, base_branch: str = "main") -> str:
        """Use LLM to generate PR summary from diff."""
        stats = self.get_diff_stats()
        code, diff_text, _ = _run_git(["diff", f"{base_branch}..HEAD"], self.workspace_root)
        if code != 0 or not diff_text:
            code, diff_text, _ = _run_git(["diff", "HEAD"], self.workspace_root)

        diff_snippet = diff_text[:6000] if diff_text else "_No diff available._"

        prompt = (
            "You are an expert software engineer. Generate a professional pull request summary "
            "in markdown format with these sections: Summary, Changes, Risks, and Reviewer Checklist. "
            "Be concise. Base it on this diff:\n\n"
            f"Files changed:\n{stats['stat_output']}\n\nDiff (truncated):\n{diff_snippet}"
        )

        result = ""
        try:
            gen = agent_model.chat([{"role": "user", "content": prompt}], stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk["type"] == "content":
                        result += chunk["delta"]
                except StopIteration:
                    break
        except Exception:
            pass
        return result.strip() or self.generate(
            "Pull Request",
            stats["stat_output"],
            "", "", "", ["Tests pass", "No debug code", "Docs updated"]
        )


# ---------------------------------------------------------------------------
# Commit quality checker
# ---------------------------------------------------------------------------

class CommitQualityChecker:
    """Check commit quality: focused diff, no debug leftovers, conventional message."""

    # Conventional commit types
    CONVENTIONAL_TYPES = {
        "feat", "fix", "docs", "style", "refactor", "perf",
        "test", "build", "ci", "chore", "revert"
    }

    DEBUG_PATTERNS = [
        re.compile(r"^\+.*\bprint\s*\(", re.MULTILINE),
        re.compile(r"^\+.*\bconsole\.log\s*\(", re.MULTILINE),
        re.compile(r"^\+.*\bdebugger\b", re.MULTILINE),
        re.compile(r"^\+.*\bpdb\.set_trace\s*\(", re.MULTILINE),
        re.compile(r"^\+.*\bbreakpoint\s*\(", re.MULTILINE),
        re.compile(r"^\+.*#\s*TODO\b", re.MULTILINE),
        re.compile(r"^\+.*#\s*FIXME\b", re.MULTILINE),
        re.compile(r"^\+.*#\s*HACK\b", re.MULTILINE),
    ]

    def check_message(self, message: str) -> List[str]:
        """Return list of quality issues with the commit message."""
        issues = []
        first_line = message.strip().splitlines()[0] if message.strip() else ""

        if len(first_line) > 72:
            issues.append(f"Subject line too long ({len(first_line)} chars, max 72).")

        # Check conventional commit format: type(scope): description
        if not re.match(r"^(\w+)(\(\w+\))?!?:\s+\S", first_line):
            issues.append("Not conventional commit format. Expected: type(scope): description")
        else:
            commit_type = re.match(r"^(\w+)", first_line).group(1).lower()
            if commit_type not in self.CONVENTIONAL_TYPES:
                issues.append(f"Unknown commit type '{commit_type}'. Use: {', '.join(sorted(self.CONVENTIONAL_TYPES))}")

        if not first_line[0:1].islower() and ":" in first_line:
            # After the colon, should start lowercase
            after_colon = first_line.split(":", 1)[-1].strip()
            if after_colon and after_colon[0].isupper():
                issues.append("Description after colon should start with lowercase.")

        return issues

    def check_diff(self, diff_text: str) -> List[Dict[str, str]]:
        """Check diff for debug leftovers. Returns list of {issue, pattern}."""
        findings = []
        for pattern in self.DEBUG_PATTERNS:
            for m in pattern.finditer(diff_text):
                line = m.group(0).strip()
                findings.append({"issue": "Debug/temporary code in diff", "line": line[:100]})
        return findings

    def check_test_coverage(self, diff_text: str, repo_map=None) -> List[str]:
        """Check if changed source files have matching test changes."""
        warnings = []
        changed_files = re.findall(r"^diff --git a/(.*?) b/", diff_text, re.MULTILINE)
        src_files = [f for f in changed_files if not any(t in f for t in ["test_", "_test.", ".test.", ".spec."])]

        if src_files and repo_map:
            for f in src_files:
                related = repo_map.find_related_tests(f)
                test_touched = any(t in changed_files for t in related)
                if not test_touched and related:
                    warnings.append(f"Source '{f}' changed but no related test file touched.")
        return warnings

    def run_full_check(self, message: str, workspace_root: str, repo_map=None) -> Dict[str, Any]:
        """Run all commit quality checks. Returns structured report."""
        code, diff_text, _ = _run_git(["diff", "HEAD"], workspace_root)
        if not diff_text:
            code, diff_text, _ = _run_git(["diff", "--cached"], workspace_root)

        msg_issues = self.check_message(message)
        diff_issues = self.check_diff(diff_text)
        test_warnings = self.check_test_coverage(diff_text, repo_map)

        all_issues = msg_issues + [i["issue"] + ": " + i["line"] for i in diff_issues] + test_warnings
        return {
            "message_issues": msg_issues,
            "diff_issues": diff_issues,
            "test_warnings": test_warnings,
            "passed": len(all_issues) == 0,
            "all_issues": all_issues,
        }


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------

class DecisionLog:
    """
    Persistent log of task decisions: plan, tool actions, diffs, and evidence.
    Stored per workspace in ~/.ultron/decisions/<hash>/
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        path_hash = hashlib.md5(workspace_root.encode()).hexdigest()
        self.log_dir = os.path.join(os.path.expanduser("~"), ".ultron", "decisions", path_hash)
        os.makedirs(self.log_dir, exist_ok=True)

    def record(
        self,
        task_description: str,
        plan: str,
        files_changed: List[str],
        commands_run: List[str],
        evidence: List[str],
        diff_text: str = "",
    ) -> str:
        """Save a decision entry. Returns the log file path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        entry = {
            "timestamp": ts,
            "task": task_description,
            "plan": plan,
            "files_changed": files_changed,
            "commands_run": commands_run,
            "evidence": evidence,
            "diff_snippet": diff_text[:3000],
        }
        log_path = os.path.join(self.log_dir, f"{ts}.json")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)
        except Exception:
            pass
        return log_path

    def load_recent(self, count: int = 5) -> List[Dict[str, Any]]:
        """Load the N most recent decision entries."""
        try:
            files = sorted(
                [f for f in os.listdir(self.log_dir) if f.endswith(".json")],
                reverse=True
            )[:count]
            entries = []
            for fname in files:
                path = os.path.join(self.log_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        entries.append(json.load(f))
                except Exception:
                    pass
            return entries
        except Exception:
            return []

    def format_entry(self, entry: Dict[str, Any]) -> str:
        """Format a single decision log entry for display."""
        lines = [
            f"Timestamp : {entry.get('timestamp', 'unknown')}",
            f"Task      : {entry.get('task', '')}",
            f"Plan      : {entry.get('plan', '')[:200]}",
            f"Files     : {', '.join(entry.get('files_changed', []))}",
            f"Commands  : {', '.join(entry.get('commands_run', []))}",
            f"Evidence  : {'; '.join(entry.get('evidence', []))}",
        ]
        return "\n".join(lines)
