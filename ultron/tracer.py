"""
tracer.py - Workstream E: /trace and /compare commands.
Trace feature flow and compare branches.
"""
import os
import re
import subprocess
from typing import List, Dict, Any, Optional, Tuple


class FeatureTracer:
    """
    Trace a feature's flow through the codebase:
    route → controller → service → domain → persistence → tests
    """

    # Layer detection patterns
    LAYER_PATTERNS = {
        "route":       [re.compile(r"@app\.(get|post|put|delete|patch)\s*\(", re.I),
                        re.compile(r"router\.(get|post|put|delete|patch)\s*\(", re.I),
                        re.compile(r"@(Get|Post|Put|Delete|Patch)\(", re.M),
                        re.compile(r'path\s*=\s*["\']/', re.I)],
        "controller":  [re.compile(r"class\s+\w*(Controller|Handler|View)\b", re.I)],
        "service":     [re.compile(r"class\s+\w*(Service|Manager|Facade|UseCase)\b", re.I)],
        "domain":      [re.compile(r"class\s+\w*(Entity|Aggregate|Domain|Model)\b", re.I),
                        re.compile(r"@dataclass", re.I)],
        "persistence": [re.compile(r"class\s+\w*(Repository|Store|Dao|Database)\b", re.I),
                        re.compile(r"\.(query|filter|find|save|insert|update|delete)\s*\(", re.I)],
        "test":        [re.compile(r"def\s+test_\w+", re.I),
                        re.compile(r"describe\s*\(", re.I),
                        re.compile(r"it\s*\(", re.I)],
    }

    def __init__(self, workspace_root: str, repo_map=None):
        self.workspace_root = workspace_root
        self.repo_map = repo_map

    def _detect_layer(self, content: str) -> Optional[str]:
        """Detect which architectural layer a file belongs to."""
        scores: Dict[str, int] = {}
        for layer, patterns in self.LAYER_PATTERNS.items():
            for pattern in patterns:
                matches = len(pattern.findall(content))
                if matches:
                    scores[layer] = scores.get(layer, 0) + matches
        if not scores:
            return None
        return max(scores, key=lambda k: scores[k])

    def trace(self, symbol: str) -> Dict[str, Any]:
        """
        Trace a symbol through architectural layers.
        Returns {symbol, layers: {layer_name: [files]}, flow_path, related_tests}
        """
        if not self.repo_map:
            return {"symbol": symbol, "error": "Run /analyze first to build the repo index."}

        # Find all references to the symbol
        refs = self.repo_map.find_references(symbol)
        defs = self.repo_map.find_symbol(symbol)

        # Group by layer
        layers: Dict[str, List[Dict]] = {}

        all_files = set(r["file"] for r in refs) | set(d["file"] for d in defs)

        for rel_path in all_files:
            abs_path = os.path.join(self.workspace_root, rel_path)
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                layer = self._detect_layer(content)
                if not layer:
                    # Guess from filename
                    name_lower = rel_path.lower()
                    if "test" in name_lower:
                        layer = "test"
                    elif "route" in name_lower or "view" in name_lower:
                        layer = "route"
                    elif "service" in name_lower:
                        layer = "service"
                    elif "repo" in name_lower or "store" in name_lower:
                        layer = "persistence"
                    else:
                        layer = "domain"

                if layer not in layers:
                    layers[layer] = []
                layers[layer].append({"file": rel_path})
            except Exception:
                continue

        # Build ordered flow path
        layer_order = ["route", "controller", "service", "domain", "persistence", "test"]
        flow_path = [l for l in layer_order if l in layers]

        # Related tests
        related_tests = []
        for d in defs:
            related_tests.extend(self.repo_map.find_related_tests(d["file"]))
        related_tests = list(set(related_tests))

        return {
            "symbol": symbol,
            "definitions": defs,
            "layers": layers,
            "flow_path": flow_path,
            "related_tests": related_tests,
            "total_references": len(refs),
        }


class BranchComparer:
    """Compare working vs broken branch — /compare command."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def _run(self, args: List[str]) -> Tuple[int, str]:
        try:
            r = subprocess.run(
                ["git"] + args,
                cwd=self.workspace_root,
                capture_output=True, text=True, timeout=30
            )
            return r.returncode, r.stdout + r.stderr
        except Exception as e:
            return -1, str(e)

    def current_branch(self) -> str:
        _, out = self._run(["branch", "--show-current"])
        return out.strip() or "HEAD"

    def list_branches(self) -> List[str]:
        _, out = self._run(["branch", "-a", "--format=%(refname:short)"])
        return [b.strip() for b in out.splitlines() if b.strip()]

    def compare(self, base: str, target: str = "HEAD") -> Dict[str, Any]:
        """
        Compare base branch to target (default: current HEAD).
        Returns {changed_files, stat, commit_diff, diverged_commits}
        """
        # Files changed between branches
        _, changed = self._run(["diff", "--name-only", f"{base}...{target}"])
        changed_files = [f.strip() for f in changed.splitlines() if f.strip()]

        # Stat summary
        _, stat = self._run(["diff", "--stat", f"{base}...{target}"])

        # Commits in target not in base
        _, commits = self._run([
            "log", f"{base}..{target}",
            "--pretty=format:%h %s (%an, %ad)", "--date=short"
        ])
        diverged = [c.strip() for c in commits.splitlines() if c.strip()]

        # Diff text (capped)
        _, diff_text = self._run(["diff", f"{base}...{target}"])
        diff_snippet = diff_text[:8000] if len(diff_text) > 8000 else diff_text

        return {
            "base": base,
            "target": target,
            "changed_files": changed_files,
            "stat": stat.strip(),
            "diverged_commits": diverged,
            "diff_snippet": diff_snippet,
            "file_count": len(changed_files),
        }


class FlakyTestDetector:
    """
    /flaky-test: re-run a test N times, collect pass/fail variation,
    preserve reproduction data.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def run_multiple(
        self,
        test_command: str,
        runs: int = 5,
    ) -> Dict[str, Any]:
        """
        Run the test command `runs` times.
        Returns {passed, failed, results, flaky_detected, variation}
        """
        results = []
        passed = 0
        failed = 0

        for i in range(runs):
            try:
                r = subprocess.run(
                    test_command,
                    shell=True,
                    cwd=self.workspace_root,
                    capture_output=True, text=True, timeout=120
                )
                success = r.returncode == 0
                results.append({
                    "run": i + 1,
                    "exit_code": r.returncode,
                    "passed": success,
                    "stdout_tail": r.stdout[-500:] if r.stdout else "",
                    "stderr_tail": r.stderr[-200:] if r.stderr else "",
                })
                if success:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                results.append({"run": i + 1, "exit_code": -1, "passed": False, "error": str(e)})
                failed += 1

        flaky = passed > 0 and failed > 0
        variation = f"{passed}/{runs} passed"

        return {
            "command": test_command,
            "runs": runs,
            "passed": passed,
            "failed": failed,
            "flaky_detected": flaky,
            "variation": variation,
            "results": results,
            "recommendation": (
                "Test is FLAKY — passes and fails non-deterministically. "
                "Check for: time-dependent assertions, shared mutable state, "
                "random data without fixed seeds, network dependencies."
            ) if flaky else (
                f"Test is {'STABLE (always passes)' if failed == 0 else 'CONSISTENTLY FAILING'}."
            ),
        }


class TestOutputParser:
    """
    Parse structured output from pytest, unittest, npm, cargo, go test.
    Returns {framework, total, passed, failed, errors, failures: [{name, message}]}
    """

    @staticmethod
    def parse(output: str, framework: str = "auto") -> Dict[str, Any]:
        if framework == "auto":
            framework = TestOutputParser._detect_framework(output)

        parser = {
            "pytest":   TestOutputParser._parse_pytest,
            "unittest": TestOutputParser._parse_unittest,
            "npm":      TestOutputParser._parse_npm,
            "cargo":    TestOutputParser._parse_cargo,
            "go":       TestOutputParser._parse_go,
        }.get(framework, TestOutputParser._parse_generic)

        result = parser(output)
        result["framework"] = framework
        return result

    @staticmethod
    def _detect_framework(output: str) -> str:
        if re.search(r"\d+ passed", output) or "PASSED" in output or "FAILED" in output:
            return "pytest"
        if "unittest" in output and "Ran " in output:
            return "unittest"
        if "npm test" in output or "jest" in output.lower() or "PASS " in output:
            return "npm"
        if "cargo test" in output or "test result:" in output:
            return "cargo"
        if "--- FAIL:" in output or "--- PASS:" in output or "go test" in output:
            return "go"
        return "generic"

    @staticmethod
    def _parse_pytest(output: str) -> Dict[str, Any]:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
        # Summary line: "5 passed, 2 failed, 1 error in 3.2s"
        m = re.search(r"(\d+) passed", output)
        if m:
            result["passed"] = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            result["failed"] = int(m.group(1))
        m = re.search(r"(\d+) error", output)
        if m:
            result["errors"] = int(m.group(1))
        m = re.search(r"(\d+) skipped", output)
        if m:
            result["skipped"] = int(m.group(1))

        # Individual failures
        for m in re.finditer(r"FAILED ([\w/.::-]+)", output):
            result["failures"].append({"name": m.group(1), "message": ""})

        result["total"] = result["passed"] + result["failed"] + result["errors"]
        result["overall"] = "passed" if result["failed"] == 0 and result["errors"] == 0 else "failed"
        return result

    @staticmethod
    def _parse_unittest(output: str) -> Dict[str, Any]:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
        m = re.search(r"Ran (\d+) tests?", output)
        total = int(m.group(1)) if m else 0
        m = re.search(r"failures=(\d+)", output)
        result["failed"] = int(m.group(1)) if m else 0
        m = re.search(r"errors=(\d+)", output)
        result["errors"] = int(m.group(1)) if m else 0
        result["passed"] = total - result["failed"] - result["errors"]
        result["total"] = total
        result["overall"] = "passed" if "OK" in output else "failed"
        return result

    @staticmethod
    def _parse_npm(output: str) -> Dict[str, Any]:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
        m = re.search(r"Tests:\s+(?:(\d+) failed,\s+)?(?:(\d+) skipped,\s+)?(\d+) passed", output)
        if m:
            result["failed"] = int(m.group(1) or 0)
            result["skipped"] = int(m.group(2) or 0)
            result["passed"] = int(m.group(3) or 0)
        result["total"] = result["passed"] + result["failed"]
        result["overall"] = "passed" if result["failed"] == 0 else "failed"
        return result

    @staticmethod
    def _parse_cargo(output: str) -> Dict[str, Any]:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
        # "test result: ok. 5 passed; 0 failed; 0 ignored"
        m = re.search(r"(\d+) passed;\s*(\d+) failed;\s*(\d+) ignored", output)
        if m:
            result["passed"] = int(m.group(1))
            result["failed"] = int(m.group(2))
            result["skipped"] = int(m.group(3))
        for m in re.finditer(r"FAILED\s+([\w:]+)", output):
            result["failures"].append({"name": m.group(1), "message": ""})
        result["total"] = result["passed"] + result["failed"]
        result["overall"] = "passed" if result["failed"] == 0 else "failed"
        return result

    @staticmethod
    def _parse_go(output: str) -> Dict[str, Any]:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
        result["passed"] = len(re.findall(r"--- PASS:", output))
        result["failed"] = len(re.findall(r"--- FAIL:", output))
        for m in re.finditer(r"--- FAIL:\s+([\w/]+)", output):
            result["failures"].append({"name": m.group(1), "message": ""})
        result["total"] = result["passed"] + result["failed"]
        result["overall"] = "passed" if result["failed"] == 0 else "failed"
        return result

    @staticmethod
    def _parse_generic(output: str) -> Dict[str, Any]:
        passed = len(re.findall(r"\b(PASS|passed|ok)\b", output, re.I))
        failed = len(re.findall(r"\b(FAIL|failed|error)\b", output, re.I))
        return {
            "passed": passed, "failed": failed,
            "errors": 0, "skipped": 0,
            "total": passed + failed,
            "failures": [],
            "overall": "passed" if failed == 0 else "failed",
        }


class VerificationPlanner:
    """
    Selects which verification checks to run based on changed files + project type.
    Impact-aware — only runs checks relevant to what changed.
    """

    def plan(
        self,
        changed_files: List[str],
        project_memory: Dict,
        repo_map=None,
    ) -> List[str]:
        """
        Returns ordered list of check categories to run.
        e.g. ["tests", "lint", "typecheck", "build"]
        """
        checks = []
        cmds = project_memory.get("commands", {})

        # Always include tests if test command available
        if cmds.get("test", {}).get("cmd"):
            checks.append("tests")

        # Add lint if source files changed
        src_changed = [f for f in changed_files if not "test" in f.lower()]
        if src_changed and cmds.get("lint", {}).get("cmd"):
            checks.append("lint")

        # Add format check if style files changed
        if any(f.endswith((".py", ".ts", ".js", ".rs")) for f in changed_files):
            if cmds.get("format", {}).get("cmd"):
                checks.append("format_check")

        # Add build if non-test source files changed
        if src_changed and cmds.get("build", {}).get("cmd"):
            checks.append("build")

        # Add secret scan always
        checks.append("secrets")

        return checks
