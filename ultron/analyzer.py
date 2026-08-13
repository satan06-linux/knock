"""
analyzer.py - Phase 2 Project Intelligence: impact analysis, failure investigation,
convention finder, and workspace discovery helpers.
"""
import os
import re
import fnmatch
from typing import List, Dict, Any, Optional, Tuple

from ultron.repo_map import RepoMap


# ---------------------------------------------------------------------------
# Workspace / folder discovery
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".pytest_cache", ".mypy_cache", "build", "dist", "target", "vendor",
    "Windows", "System32", "Program Files", "Program Files (x86)",
    "$Recycle.Bin", "Recovery",
}


def find_folders(name: str, search_root: str, max_results: int = 20) -> List[str]:
    """
    Search for directories matching `name` (case-insensitive substring)
    under `search_root`, skipping ignored dirs.
    """
    name_lower = name.lower()
    results = []

    for root, dirs, _ in os.walk(search_root):
        # Prune ignored dirs
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for d in dirs:
            if name_lower in d.lower():
                results.append(os.path.join(root, d))
                if len(results) >= max_results:
                    return results
    return results


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------

class ImpactAnalyzer:
    def __init__(self, repo_map: RepoMap):
        self.repo_map = repo_map

    def analyze_file(self, rel_path: str) -> Dict[str, Any]:
        """Full impact report for a file."""
        rm = self.repo_map
        result: Dict[str, Any] = {
            "target": rel_path,
            "symbols_defined": rm.get_file_symbols(rel_path),
            "imports": rm.get_imports(rel_path),
            "imported_by": rm.who_imports(rel_path),
            "related_tests": rm.find_related_tests(rel_path),
            "callers": [],
            "risk": "low",
            "warnings": [],
        }

        # Find callers for each public symbol
        callers_set = []
        for sym in result["symbols_defined"]:
            if not sym["name"].startswith("_"):  # public only
                callers = rm.callers_of(sym["name"])
                for c in callers:
                    c["symbol"] = sym["name"]
                callers_set.extend(callers)
        result["callers"] = callers_set

        # Risk assessment
        imported_by_count = len(result["imported_by"])
        callers_count = len(callers_set)
        test_count = len(result["related_tests"])

        if imported_by_count > 5 or callers_count > 10:
            result["risk"] = "high"
            result["warnings"].append(
                f"This file is imported by {imported_by_count} modules and has {callers_count} known call sites."
            )
        elif imported_by_count > 2 or callers_count > 3:
            result["risk"] = "medium"

        if test_count == 0:
            result["warnings"].append("No related test files found — changes may go unverified.")

        return result

    def analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        """Impact report for a specific symbol."""
        rm = self.repo_map
        definitions = rm.find_symbol(symbol)
        callers = rm.callers_of(symbol)
        refs = rm.find_references(symbol)

        warnings = []
        risk = "low"

        if len(callers) > 10:
            risk = "high"
            warnings.append(f"Symbol '{symbol}' has {len(callers)} call sites — renaming/removal is high risk.")
        elif len(callers) > 3:
            risk = "medium"

        # Check if it's referenced in test files
        test_refs = [r for r in refs if self.repo_map.index.get(r["file"], {}).get("is_test")]
        if not test_refs and callers:
            warnings.append(f"No test coverage found for '{symbol}'.")

        return {
            "symbol": symbol,
            "definitions": definitions,
            "callers": callers,
            "total_references": len(refs),
            "test_references": test_refs,
            "risk": risk,
            "warnings": warnings,
        }


# ---------------------------------------------------------------------------
# Failure investigator
# ---------------------------------------------------------------------------

# Patterns to extract meaningful error lines from noisy logs
ERROR_PATTERNS = [
    # Python tracebacks
    re.compile(r"((?:File \".*?\", line \d+.*\n)+.*(?:Error|Exception|Warning)[^\n]*)", re.MULTILINE),
    # Pytest short failures
    re.compile(r"(FAILED .*? - .*)", re.MULTILINE),
    re.compile(r"(AssertionError[^\n]*)", re.MULTILINE),
    # Generic Error: lines
    re.compile(r"(?:^|\s)((?:Error|Exception|Fatal|FAIL|FAILED)[:\s][^\n]{10,})", re.MULTILINE),
    # Node/JS errors
    re.compile(r"((?:TypeError|ReferenceError|SyntaxError|RangeError):[^\n]+)", re.MULTILINE),
    # Go errors
    re.compile(r"(FAIL\s+\S+\s+\[.*?\])", re.MULTILINE),
    re.compile(r"(--- FAIL:[^\n]+)", re.MULTILINE),
    # Rust errors
    re.compile(r"(error\[E\d+\][^\n]+)", re.MULTILINE),
    # Compiler-style: file:line:col: error
    re.compile(r"(\S+\.(?:py|js|ts|go|rs|java|cpp|c):\d+(?::\d+)?[:\s]+(?:error|warning)[^\n]*)", re.MULTILINE),
]

# File path reference pattern
FILE_REF_PATTERN = re.compile(r'["\'`]?([\w./\\-]+\.(?:py|js|ts|go|rs|java|cpp|c|rb))[:"\'`]?(?::(\d+))?')


class FailureInvestigator:
    def __init__(self, workspace_root: str, repo_map: RepoMap):
        self.workspace_root = workspace_root
        self.repo_map = repo_map

    def extract_errors(self, log_text: str) -> List[str]:
        """Pull meaningful error lines out of a noisy log."""
        found = []
        for pattern in ERROR_PATTERNS:
            for m in pattern.finditer(log_text):
                snippet = m.group(1).strip()
                if snippet and snippet not in found:
                    found.append(snippet)
        # Deduplicate and cap
        return found[:10]

    def find_source_locations(self, log_text: str) -> List[Dict[str, Any]]:
        """Extract file:line references from a log and resolve them to workspace files."""
        locations = []
        seen = set()
        for m in FILE_REF_PATTERN.finditer(log_text):
            fpath = m.group(1).replace("\\", "/")
            line_no = int(m.group(2)) if m.group(2) else None

            # Try to resolve to a workspace file
            for rel in self.repo_map.index:
                if rel.endswith(fpath) or fpath.endswith(rel):
                    key = (rel, line_no)
                    if key not in seen:
                        seen.add(key)
                        locations.append({"file": rel, "line": line_no})
                    break
        return locations[:20]

    def investigate(self, log_text: str) -> Dict[str, Any]:
        """Full failure investigation report."""
        errors = self.extract_errors(log_text)
        locations = self.find_source_locations(log_text)

        # Classify error type
        error_type = "unknown"
        if re.search(r"AssertionError|FAILED|assert\s", log_text):
            error_type = "test_failure"
        elif re.search(r"SyntaxError|IndentationError|ParseError", log_text):
            error_type = "syntax_error"
        elif re.search(r"ImportError|ModuleNotFoundError|Cannot find module", log_text):
            error_type = "import_error"
        elif re.search(r"TypeError|AttributeError|NameError|ReferenceError", log_text):
            error_type = "runtime_error"
        elif re.search(r"error\[E\d+\]|cargo|rustc", log_text, re.IGNORECASE):
            error_type = "compile_error"
        elif re.search(r"FAIL.*\[build failed\]|go build", log_text):
            error_type = "build_error"

        # Suggest repair options based on type
        repair_suggestions = {
            "test_failure":  ["Check assertion values", "Run the specific failing test in isolation", "Add debug prints"],
            "syntax_error":  ["Fix the syntax at the reported line", "Check indentation"],
            "import_error":  ["Verify the module name", "Check if dependency is installed", "Check relative import paths"],
            "runtime_error": ["Add a try/except around the failing call", "Check for None values", "Inspect the traceback"],
            "compile_error": ["Read the compiler error code", "Check type annotations", "Run build command for details"],
            "build_error":   ["Run the build command manually", "Check dependencies are installed"],
            "unknown":       ["Run the failing command manually and inspect output"],
        }

        return {
            "error_type": error_type,
            "extracted_errors": errors,
            "source_locations": locations,
            "repair_suggestions": repair_suggestions.get(error_type, []),
        }

    def generate_min_repro(self, log_text: str, model_callable=None) -> str:
        """
        Generate a minimal reproduction script.
        If model_callable provided, uses LLM. Otherwise returns a template.
        """
        investigation = self.investigate(log_text)
        errors = "\n".join(investigation["extracted_errors"][:5])
        locations = investigation["source_locations"]

        if model_callable:
            prompt = (
                f"You are a debugging assistant. Based on the following error output, "
                f"write a minimal Python reproduction script that isolates the bug. "
                f"Keep it under 30 lines. Only output the script, no explanation.\n\n"
                f"Error:\n{errors}\n\n"
                f"Relevant files: {[l['file'] for l in locations[:3]]}"
            )
            return model_callable(prompt)

        # Fallback template
        lines = [
            "# Minimal reproduction script",
            "# Generated by Ultron /min-repro",
            "#",
            "# Error summary:",
        ]
        for e in investigation["extracted_errors"][:3]:
            lines.append(f"# {e[:120]}")
        lines += [
            "#",
            "# Source locations:",
        ]
        for loc in locations[:3]:
            lines.append(f"# {loc['file']}" + (f":{loc['line']}" if loc['line'] else ""))
        lines += [
            "",
            "# TODO: Add minimal reproduction steps below",
            "import pytest",
            "",
            "def test_reproduction():",
            "    # Reproduce the failing condition",
            "    pass",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convention finder
# ---------------------------------------------------------------------------

class ConventionFinder:
    """
    Finds existing conventions in the codebase before scaffolding new code,
    so generated code matches the repository style.
    """

    def __init__(self, workspace_root: str, repo_map: RepoMap):
        self.workspace_root = workspace_root
        self.repo_map = repo_map

    def find_similar_files(self, description: str, max_results: int = 5) -> List[str]:
        """Find files similar to a description by searching symbol names and file paths."""
        words = re.findall(r"\w+", description.lower())
        scores: Dict[str, int] = {}

        for rel_path, entry in self.repo_map.index.items():
            score = 0
            path_lower = rel_path.lower()
            for word in words:
                if word in path_lower:
                    score += 2
                for sym in entry.get("symbols", []):
                    if word in sym["name"].lower():
                        score += 1
            if score > 0:
                scores[rel_path] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [p for p, _ in ranked[:max_results]]

    def read_conventions(self, rel_path: str, lines: int = 40) -> str:
        """Read the first N lines of a file to understand its conventions."""
        abs_path = os.path.join(self.workspace_root, rel_path)
        if not os.path.isfile(abs_path):
            return ""
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content_lines = f.readlines()[:lines]
            return "".join(content_lines)
        except Exception:
            return ""

    def get_project_conventions(self, feature_description: str) -> Dict[str, Any]:
        """Return convention snippets for a new feature."""
        similar = self.find_similar_files(feature_description)
        snippets = {}
        for f in similar:
            snippets[f] = self.read_conventions(f)
        return {
            "similar_files": similar,
            "snippets": snippets,
            "advice": (
                f"Found {len(similar)} similar file(s). "
                "Study their structure, naming, imports, and error handling patterns "
                "before writing new code."
            ),
        }


# ---------------------------------------------------------------------------
# Refactor & regression safeguards (Phase 3)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as dc_field

@dataclass
class RefactorSafetyReport:
    files: List[str]
    public_symbols: Dict[str, List[str]]        # file -> [symbol names]
    external_callers: Dict[str, List[str]]       # symbol -> [file:line callers outside file]
    affected_tests: List[str]
    risk_level: str                              # "low" | "medium" | "high"
    warnings: List[str]


class RefactorGuard:
    """
    Pre-refactor safety analysis: public-symbol enumeration, external caller detection,
    test-gap detection, and flaky-test detection from test output.
    """

    def __init__(self, repo_map: RepoMap):
        self.repo_map = repo_map

    def check_refactor_safety(self, files: List[str]) -> RefactorSafetyReport:
        """
        For each file, enumerate public symbols and their external callers.
        Risk is HIGH if any public symbol has > 3 external callers.
        """
        rm = self.repo_map
        public_symbols: Dict[str, List[str]] = {}
        external_callers: Dict[str, List[str]] = {}
        affected_tests: List[str] = []
        warnings: List[str] = []

        for rel_path in files:
            syms = rm.get_file_symbols(rel_path)
            pub = [s["name"] for s in syms if not s["name"].startswith("_")]
            public_symbols[rel_path] = pub

            for sym_name in pub:
                callers = rm.callers_of(sym_name)
                # External callers: callers not in the same file
                ext = [
                    f"{c['file']}:{c['line']}"
                    for c in callers
                    if c.get("file") != rel_path
                ]
                if ext:
                    external_callers[sym_name] = ext

            # Related tests
            for t in rm.find_related_tests(rel_path):
                if t not in affected_tests:
                    affected_tests.append(t)

        # Risk assessment
        max_callers = max((len(v) for v in external_callers.values()), default=0)
        if max_callers > 3:
            risk_level = "high"
            warnings.append(
                f"One or more public symbols have {max_callers} external call sites. "
                "Renaming or removing them is high-risk — update all callers."
            )
        elif max_callers > 0:
            risk_level = "medium"
        else:
            risk_level = "low"

        if not affected_tests:
            warnings.append(
                "No related test files found for these files. "
                "Changes may go undetected by the test suite."
            )

        return RefactorSafetyReport(
            files=files,
            public_symbols=public_symbols,
            external_callers=external_callers,
            affected_tests=affected_tests,
            risk_level=risk_level,
            warnings=warnings,
        )

    def detect_test_gaps(self, changed_files: List[str]) -> List[str]:
        """
        Return files from changed_files that have no related test file.
        Uses RepoMap.find_related_tests() for the mapping.
        """
        gaps = []
        for rel_path in changed_files:
            related = self.repo_map.find_related_tests(rel_path)
            if not related:
                gaps.append(rel_path)
        return gaps

    def detect_flaky_tests(self, test_output: str) -> List[str]:
        """
        Scan test output for signs of flakiness.
        Matches: "FLAKY", "flaky", "intermittent", or the same test name
        appearing in both PASSED and FAILED lines.
        Returns a list of suspected flaky test names.
        """
        flaky: List[str] = []

        # Explicit flaky markers
        for line in test_output.splitlines():
            if re.search(r'\b(FLAKY|flaky|intermittent)\b', line):
                # Try to extract test name
                m = re.search(r'(test\w+)', line)
                if m and m.group(1) not in flaky:
                    flaky.append(m.group(1))

        # Same test appearing in both PASSED and FAILED
        passed = set(re.findall(r'PASSED\s+([\w:./]+)', test_output))
        failed = set(re.findall(r'FAILED\s+([\w:./]+)', test_output))
        for name in passed & failed:
            if name not in flaky:
                flaky.append(name)

        return flaky


# ---------------------------------------------------------------------------
# ULTRON.md and .ultron.toml reader
# ---------------------------------------------------------------------------

def load_project_instructions(workspace_root: str) -> str:
    """
    Load project-level instructions from ULTRON.md (human-editable)
    and .ultron.toml (structured settings).
    Returns a formatted string to inject into the system prompt.
    """
    lines = []

    # ULTRON.md
    md_path = os.path.join(workspace_root, "ULTRON.md")
    if os.path.isfile(md_path):
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                lines.append("=== PROJECT INSTRUCTIONS (ULTRON.md) ===")
                lines.append(content[:3000])  # cap at 3000 chars
                lines.append("=========================================")
        except Exception:
            pass

    # .ultron.toml
    toml_path = os.path.join(workspace_root, ".ultron.toml")
    if os.path.isfile(toml_path):
        try:
            with open(toml_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                lines.append("=== PROJECT SETTINGS (.ultron.toml) ===")
                lines.append(content[:2000])
                lines.append("========================================")
        except Exception:
            pass

    return "\n".join(lines)


def create_ultron_md_template(workspace_root: str) -> str:
    """Create a default ULTRON.md template if it doesn't exist."""
    md_path = os.path.join(workspace_root, "ULTRON.md")
    if os.path.isfile(md_path):
        return f"ULTRON.md already exists at {md_path}"

    template = """# Project Instructions for Ultron

## Project Overview
<!-- Describe what this project does -->

## Architecture Notes
<!-- Key components, folder structure, design decisions -->

## Conventions
<!-- Naming conventions, code style, patterns to follow -->

## Known Pitfalls
<!-- Common mistakes, things to avoid -->

## Verified Commands
<!-- Commands that are known to work -->

## Contact / Ownership
<!-- Who owns what, who to ask for help -->
"""
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(template)
        return f"Created ULTRON.md at {md_path}"
    except Exception as e:
        return f"Error creating ULTRON.md: {str(e)}"


def create_ultron_toml_template(workspace_root: str) -> str:
    """Create a default .ultron.toml template if it doesn't exist."""
    toml_path = os.path.join(workspace_root, ".ultron.toml")
    if os.path.isfile(toml_path):
        return f".ultron.toml already exists at {toml_path}"

    template = """# Ultron Project Settings
# See docs for full reference

[project]
name = ""
language = ""

[commands]
test = ""
lint = ""
build = ""
format = ""
run = ""

[context]
# Extra files to always include in context (relative to workspace root)
always_include = []

[permissions]
# Commands that require explicit approval even in auto-approve mode
require_approval = ["rm", "drop", "delete", "migrate"]

[contracts]
# Maximum number of files that a single task contract may touch (default: 8)
max_files = 8
# What to do when the model wants to write a file not in the contract:
#   "ask"   → ask user for approval before allowing (default)
#   "block" → silently reject the write
unplanned_file_policy = "ask"
"""
    try:
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write(template)
        return f"Created .ultron.toml at {toml_path}"
    except Exception as e:
        return f"Error creating .ultron.toml: {str(e)}"
