"""
reviewer.py - Phase 3: Senior Code Reviewer

Implements three-tier structured diff analysis:
  Tier 1 - Verified match:  regex matched a specific file:line location.
                            Does NOT mean it is a confirmed bug.
  Tier 2 - Heuristic:       pattern-based; may have false positives.
  Tier 3 - AI suggestion:   model-generated; prefixed "Suggestion:" in output;
                            never stored or displayed as a confirmed defect.

All secret-pattern scanning is done in Python against the collected diff text.
No shell grep/pipe commands are used (Windows portability requirement).

Diff source priority:
  1. agent._changed_files + CheckpointManager records  -> unified diff
  2. git diff --staged, git diff, git status --short   -> supplement
  Merged and deduplicated; works with no HEAD commit.
"""

import os
import re
import json
import subprocess
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Literal, Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

FindingTier = Literal["verified_match", "heuristic", "ai_suggestion"]


@dataclass
class ReviewFinding:
    tier: FindingTier
    severity: str          # "critical" | "high" | "medium" | "low" | "info"
    category: str          # "security" | "bug" | "style" | "api_break" | "missing_test" | "performance" | ...
    file: str
    line: Optional[int]
    description: str
    suggested_fix: Optional[str] = None


@dataclass
class ReviewReport:
    findings: List[ReviewFinding]
    summary: str
    diff_source: str       # "task_diff" | "git_staged" | "git_unstaged" | "combined" | "none"
    reviewed_files: List[str]


# ---------------------------------------------------------------------------
# Static check definitions (all Python regex — no subprocess grep)
# ---------------------------------------------------------------------------

# Each entry: (category, pattern_str, severity, tier)
_VERIFIED_CHECKS = [
    # Hardcoded secrets / credentials
    ("security",
     r'(?i)(password|passwd|token|secret|api_key|apikey|auth_key|private_key)\s*=\s*["\'][^"\']{6,}',
     "critical", "verified_match"),
    # Debug prints left in code (added lines only — we check +prefix)
    ("style",
     r'\bprint\s*\(',
     "low", "verified_match"),
    ("style",
     r'\bconsole\.log\s*\(',
     "low", "verified_match"),
    # TODOs / FIXMEs
    ("style",
     r'\b(TODO|FIXME|HACK|XXX)\b',
     "info", "verified_match"),
]

# Heuristic checks operate on the whole diff text (no line anchor)
_HEURISTIC_CHECKS = [
    # Deleted lines containing test assertions
    ("missing_test",
     r'^-.*\bassert\b',
     "medium", "heuristic"),
]

# Severity ordering for sorting
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workspace_hash(workspace_root: str) -> str:
    return hashlib.md5(os.path.abspath(workspace_root).encode()).hexdigest()[:12]


def _reviews_dir(workspace_root: str) -> str:
    base = os.path.join(os.path.expanduser("~"), ".ultron", "reviews",
                        _workspace_hash(workspace_root))
    os.makedirs(base, exist_ok=True)
    return base


def _run_git(args: List[str], cwd: str) -> str:
    """Run a git command and return stdout (empty string on error)."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# CodeReviewer
# ---------------------------------------------------------------------------

class CodeReviewer:
    """
    Analyses a diff and returns a ReviewReport with three-tier findings.
    No file edits are performed anywhere in this class.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)

    # ------------------------------------------------------------------
    # Diff collection
    # ------------------------------------------------------------------

    def collect_diff(self, agent=None) -> tuple:
        """
        Returns (diff_text: str, diff_source_label: str).

        Priority:
          1. agent._changed_files + checkpoint records -> synthesised diff header
          2. git diff --staged
          3. git diff (unstaged)
          4. (untracked new files listed via git status)
        Results are merged and deduplicated.
        """
        parts = []
        source_labels = []

        # --- Source 1: agent task diff ----------------------------------
        if agent is not None:
            task_files = getattr(agent.checkpoint, "current_task_files", {})
            if task_files:
                source_labels.append("task_diff")
                for rel_path in task_files:
                    abs_path = os.path.join(self.workspace_root, rel_path)
                    if os.path.isfile(abs_path):
                        try:
                            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                                current = f.read()
                        except Exception:
                            current = ""
                        # Try to get the pre-edit content from checkpoint
                        orig = ""
                        cp_data = task_files.get(rel_path, {})
                        orig_content = cp_data.get("original_content", "")
                        if isinstance(orig_content, str):
                            orig = orig_content
                        # Produce a minimal unified diff header
                        if orig != current:
                            parts.append(f"--- a/{rel_path}\n+++ b/{rel_path}\n")
                            # Add changed lines (simple line diff)
                            orig_lines = orig.splitlines(keepends=True)
                            curr_lines = current.splitlines(keepends=True)
                            for i, line in enumerate(curr_lines):
                                parts.append(f"+{line}")
                            for i, line in enumerate(orig_lines):
                                parts.append(f"-{line}")

        # --- Source 2: git staged / unstaged ----------------------------
        staged = _run_git(["diff", "--staged"], self.workspace_root)
        unstaged = _run_git(["diff"], self.workspace_root)

        if staged:
            parts.append(staged)
            source_labels.append("git_staged")
        if unstaged:
            parts.append(unstaged)
            source_labels.append("git_unstaged")

        # --- Source 3: untracked new files (show filename only) ---------
        status_out = _run_git(["status", "--porcelain"], self.workspace_root)
        for line in status_out.splitlines():
            if line.startswith("?? "):
                fname = line[3:].strip()
                parts.append(f"[untracked] {fname}\n")

        diff_text = "\n".join(parts)
        source_label = "combined" if len(source_labels) > 1 else (source_labels[0] if source_labels else "none")
        return diff_text, source_label

    # ------------------------------------------------------------------
    # Static checks (Python regex only — no subprocess grep)
    # ------------------------------------------------------------------

    def run_static_checks(self, diff_text: str) -> List[ReviewFinding]:
        """Run all static pattern checks against the diff text."""
        findings: List[ReviewFinding] = []

        # Parse diff hunks to get file context for verified_match findings
        current_file = "unknown"
        current_line = None
        file_pattern = re.compile(r'^\+\+\+ b/(.+)$')
        hunk_pattern = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')

        lines = diff_text.splitlines()

        for raw_line in lines:
            # Track file context
            fm = file_pattern.match(raw_line)
            if fm:
                current_file = fm.group(1)
                current_line = None
                continue
            hm = hunk_pattern.match(raw_line)
            if hm:
                current_line = int(hm.group(1))
                continue
            if current_line is not None and raw_line.startswith("+") and not raw_line.startswith("+++"):
                current_line += 1

            # Verified match checks (scan added lines only for style/security)
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                content = raw_line[1:]
                for category, pattern_str, severity, tier in _VERIFIED_CHECKS:
                    if re.search(pattern_str, content):
                        findings.append(ReviewFinding(
                            tier="verified_match",
                            severity=severity,
                            category=category,
                            file=current_file,
                            line=current_line,
                            description=self._describe_verified(category, pattern_str, content),
                            suggested_fix=self._suggest_fix(category),
                        ))

        # Heuristic checks (whole diff text, no file:line anchor guaranteed)
        for category, pattern_str, severity, tier in _HEURISTIC_CHECKS:
            if re.search(pattern_str, diff_text, re.MULTILINE):
                findings.append(ReviewFinding(
                    tier="heuristic",
                    severity=severity,
                    category=category,
                    file="(see diff)",
                    line=None,
                    description=self._describe_heuristic(category),
                    suggested_fix=self._suggest_fix(category),
                ))

        # Heuristic: detect large functions introduced (> 60 lines in a hunk)
        added_lines_in_block = 0
        in_function = False
        for raw_line in lines:
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                content = raw_line[1:]
                if re.match(r'^\s*def |^\s*function |^\s*func ', content):
                    in_function = True
                    added_lines_in_block = 0
                if in_function:
                    added_lines_in_block += 1
                    if added_lines_in_block > 60:
                        findings.append(ReviewFinding(
                            tier="heuristic",
                            severity="low",
                            category="performance",
                            file=current_file,
                            line=None,
                            description="A newly introduced function appears to exceed 60 lines. Consider splitting it.",
                            suggested_fix="Extract sub-functions or helper methods to keep functions focused.",
                        ))
                        in_function = False
            elif not raw_line.startswith("+") and not raw_line.startswith("-"):
                in_function = False
                added_lines_in_block = 0

        # Deduplicate by (file, line, category)
        seen = set()
        unique_findings = []
        for f in findings:
            key = (f.file, f.line, f.category)
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        return unique_findings

    def _describe_verified(self, category: str, pattern: str, content: str) -> str:
        if category == "security":
            return f"Pattern matched: possible hardcoded credential in this line."
        if category == "style" and "print" in pattern:
            return "Debug print() statement found in added code."
        if category == "style" and "console.log" in pattern:
            return "Debug console.log() found in added code."
        if category == "style":
            return "TODO/FIXME/HACK marker left in code."
        return f"Pattern matched in added line."

    def _describe_heuristic(self, category: str) -> str:
        if category == "missing_test":
            return "An assertion was deleted from this diff — a test may have been weakened or removed."
        return f"Heuristic pattern matched for category: {category}"

    def _suggest_fix(self, category: str) -> Optional[str]:
        fixes = {
            "security": "Store credentials in environment variables or a secrets manager.",
            "style": "Remove before committing to production.",
            "missing_test": "Verify the deletion was intentional; add a replacement assertion if needed.",
            "performance": "Split into smaller, focused functions.",
            "api_break": "Update callers or add a deprecation shim.",
        }
        return fixes.get(category)

    # ------------------------------------------------------------------
    # Model review (online only)
    # ------------------------------------------------------------------

    def run_model_review(self, diff_text: str, model) -> List[ReviewFinding]:
        """
        Ask the LLM to review the diff.
        All findings are tier='ai_suggestion' — never labelled as confirmed bugs.
        Falls back to [] if offline or parse fails.
        """
        if not diff_text.strip():
            return []
        try:
            if not model.is_available():
                return []
        except Exception:
            return []

        prompt = (
            "You are a senior code reviewer. Review the following diff and return a JSON array "
            "of findings. Each finding must have: severity (critical/high/medium/low/info), "
            "category (bug/security/performance/api_break/missing_test/style/duplication), "
            "file (string), line (integer or null), description (string), fix (string or null). "
            "Respond with ONLY a JSON array — no markdown, no explanation.\n\n"
            f"Diff (truncated to 8000 chars):\n{diff_text[:8000]}"
        )

        findings = []
        raw = ""
        try:
            gen = model.chat([{"role": "user", "content": prompt}], stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk["type"] == "content":
                        raw += chunk["delta"]
                except StopIteration:
                    break
        except Exception:
            return []

        # Parse JSON
        try:
            # Strip markdown fences if present
            clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
            start = clean.find("[")
            end = clean.rfind("]")
            if start != -1 and end != -1:
                data = json.loads(clean[start:end+1])
                for item in data[:20]:  # cap at 20 AI findings
                    if not isinstance(item, dict):
                        continue
                    findings.append(ReviewFinding(
                        tier="ai_suggestion",
                        severity=item.get("severity", "info"),
                        category=item.get("category", "style"),
                        file=str(item.get("file", "unknown")),
                        line=item.get("line"),
                        description="Suggestion: " + str(item.get("description", "")),
                        suggested_fix=item.get("fix"),
                    ))
        except Exception:
            pass

        return findings

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def review(self, agent=None, model=None) -> ReviewReport:
        """
        Collect the diff, run static checks, optionally run model review,
        merge and sort findings, return a ReviewReport.
        """
        diff_text, diff_source = self.collect_diff(agent)

        static_findings = self.run_static_checks(diff_text)
        model_findings = []
        if model is not None:
            model_findings = self.run_model_review(diff_text, model)

        all_findings = static_findings + model_findings

        # Sort by severity (critical first)
        all_findings.sort(key=lambda f: _SEV_ORDER.get(f.severity, 99))

        # Collect reviewed files
        reviewed_files = list({f.file for f in all_findings if f.file != "unknown" and f.file != "(see diff)"})

        summary_parts = []
        if all_findings:
            counts = {}
            for f in all_findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            summary_parts = [f"{v} {k}" for k, v in counts.items()]
            summary = "Findings: " + ", ".join(summary_parts)
        else:
            summary = "No issues found in the reviewed diff."

        return ReviewReport(
            findings=all_findings,
            summary=summary,
            diff_source=diff_source,
            reviewed_files=reviewed_files,
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display_report(self, report: ReviewReport, console) -> None:
        """Print the ReviewReport as a Rich table with tier and severity columns."""
        from rich.table import Table
        from rich.panel import Panel

        if not report.findings:
            console.print(Panel(
                f"[green]No issues found.[/green]\nDiff source: {report.diff_source}",
                title="[bold green]Code Review Report[/bold green]",
                border_style="green",
                expand=False,
            ))
            return

        table = Table(show_header=True, header_style="bold white")
        table.add_column("Tier", style="dim", width=16)
        table.add_column("Sev", width=8)
        table.add_column("Category", width=12)
        table.add_column("Location", width=25)
        table.add_column("Description")

        sev_colors = {
            "critical": "red",
            "high": "red",
            "medium": "yellow",
            "low": "dim",
            "info": "dim",
        }
        tier_labels = {
            "verified_match": "[cyan]Verified match[/cyan]",
            "heuristic": "[yellow]Heuristic[/yellow]",
            "ai_suggestion": "[dim]AI suggestion[/dim]",
        }

        for f in report.findings:
            loc = f.file
            if f.line:
                loc += f":{f.line}"
            sev_color = sev_colors.get(f.severity, "white")
            table.add_row(
                tier_labels.get(f.tier, f.tier),
                f"[{sev_color}]{f.severity}[/{sev_color}]",
                f.category,
                loc,
                f.description[:100],
            )

        body = table
        footer = (
            "\n[dim]Tier legend:[/dim] "
            "[cyan]Verified match[/cyan] = pattern found at location  "
            "[yellow]Heuristic[/yellow] = approximate pattern  "
            "[dim]AI suggestion[/dim] = model opinion, not confirmed"
        )
        console.print(Panel(
            body,
            title=f"[bold magenta]Code Review — {len(report.findings)} finding(s) | {report.diff_source}[/bold magenta]",
            border_style="magenta",
            expand=False,
        ))
        console.print(footer)
        console.print(f"[dim]Summary: {report.summary}[/dim]")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_report(self, report: ReviewReport, workspace_root: str) -> str:
        """Save report JSON to ~/.ultron/reviews/<hash>/<timestamp>.json."""
        import datetime
        reviews_dir = _reviews_dir(workspace_root)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(reviews_dir, f"{ts}.json")
        try:
            data = {
                "summary": report.summary,
                "diff_source": report.diff_source,
                "reviewed_files": report.reviewed_files,
                "findings": [
                    {
                        "tier": f.tier,
                        "severity": f.severity,
                        "category": f.category,
                        "file": f.file,
                        "line": f.line,
                        "description": f.description,
                        "suggested_fix": f.suggested_fix,
                    }
                    for f in report.findings
                ],
            }
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2)
        except Exception:
            pass
        return path
