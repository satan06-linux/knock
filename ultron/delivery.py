"""
delivery.py - Phase 4: Project delivery assistant.
Covers /feature planner, scaffold auditor, /docs-check, /handoff,
/doctor diagnostics, health analysis, and release checklist.
"""
import os
import re
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple


# ---------------------------------------------------------------------------
# Feature planner — vertical slice
# ---------------------------------------------------------------------------

VERTICAL_SLICE_LAYERS = [
    "models",
    "migrations",
    "services / business logic",
    "API routes / controllers",
    "UI components",
    "tests",
    "documentation",
    "feature flags",
    "configuration",
]


class FeaturePlanner:
    """Produce a vertical-slice plan for a new feature."""

    def __init__(self, workspace_root: str, repo_map=None):
        self.workspace_root = workspace_root
        self.repo_map = repo_map

    def plan(self, description: str, model=None) -> str:
        """
        Generate a vertical-slice feature plan.
        Uses LLM if available, else produces a structured template.
        """
        if model and model.is_available():
            return self._plan_with_ai(description, model)
        return self._plan_template(description)

    def _plan_template(self, description: str) -> str:
        lines = [
            f"# Feature Plan: {description}",
            "",
            "## Vertical Slice Checklist",
            "",
        ]
        for layer in VERTICAL_SLICE_LAYERS:
            lines.append(f"### {layer.title()}")
            lines.append(f"- [ ] Define {layer} for this feature")
            lines.append("")

        lines += [
            "## Scaffold Audit (verify before closing)",
            "- [ ] All new symbols exported where needed",
            "- [ ] Registered in DI / factory / router",
            "- [ ] Tests added for happy path and edge cases",
            "- [ ] Environment variables documented",
            "- [ ] Migration created and tested",
            "- [ ] README / API docs updated",
            "- [ ] Feature flag added if applicable",
            "",
            "## Verification Steps",
            "- [ ] Run test suite: `pytest` / `npm test` / `cargo test`",
            "- [ ] Run lint: `flake8` / `eslint` / `cargo clippy`",
            "- [ ] Run build: confirm no compile errors",
            "- [ ] Smoke test manually in dev environment",
        ]
        return "\n".join(lines)

    def _plan_with_ai(self, description: str, model) -> str:
        # Gather existing conventions as context
        similar_files = []
        if self.repo_map:
            from ultron.analyzer import ConventionFinder
            cf = ConventionFinder(self.workspace_root, self.repo_map)
            similar_files = cf.find_similar_files(description)

        prompt = (
            f"You are a senior software engineer. Produce a detailed vertical-slice implementation plan "
            f"for the following feature in markdown format.\n\n"
            f"Feature: {description}\n\n"
            f"Include sections for: Models, Migrations, Services, API Routes, UI (if applicable), "
            f"Tests, Documentation, Feature Flags, and a Scaffold Audit checklist.\n\n"
            f"Similar existing files to study for conventions: {similar_files[:5]}\n\n"
            f"Be specific about file names and what each file should contain."
        )

        result = ""
        try:
            gen = model.chat([{"role": "user", "content": prompt}], stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk["type"] == "content":
                        result += chunk["delta"]
                except StopIteration:
                    break
        except Exception:
            pass
        return result.strip() or self._plan_template(description)


# ---------------------------------------------------------------------------
# Scaffold auditor
# ---------------------------------------------------------------------------

class ScaffoldAuditor:
    """
    Audits a set of changed files for common scaffolding gaps:
    missing exports, registrations, tests, config, migrations, docs.
    """

    def audit(self, changed_files: List[str], workspace_root: str, repo_map=None) -> List[Dict[str, str]]:
        """Return list of {file, issue, severity} findings."""
        findings = []

        for rel_path in changed_files:
            abs_path = os.path.join(workspace_root, rel_path)
            if not os.path.isfile(abs_path):
                continue

            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            ext = os.path.splitext(rel_path)[1].lower()

            # Check: new class/function defined but no matching test
            if repo_map and not rel_path.startswith("test"):
                related = repo_map.find_related_tests(rel_path)
                if not related:
                    findings.append({
                        "file": rel_path,
                        "issue": "No test file found for this module.",
                        "severity": "medium",
                    })

            # Python: new class defined but not exported in __init__.py
            if ext == ".py" and "class " in content:
                parent_dir = os.path.dirname(abs_path)
                init_path = os.path.join(parent_dir, "__init__.py")
                if os.path.isfile(init_path):
                    with open(init_path, "r", encoding="utf-8", errors="replace") as f:
                        init_content = f.read()
                    classes = re.findall(r"^class\s+(\w+)", content, re.MULTILINE)
                    for cls in classes:
                        if cls not in init_content:
                            findings.append({
                                "file": rel_path,
                                "issue": f"Class '{cls}' may not be exported in __init__.py.",
                                "severity": "low",
                            })

            # Check: migration-related files
            if any(kw in rel_path.lower() for kw in ["model", "schema", "entity"]):
                migrations_dir = os.path.join(workspace_root, "migrations")
                alembic_dir = os.path.join(workspace_root, "alembic")
                if not os.path.isdir(migrations_dir) and not os.path.isdir(alembic_dir):
                    findings.append({
                        "file": rel_path,
                        "issue": "Model/schema changed but no migrations directory found.",
                        "severity": "high",
                    })

            # Check: env variables referenced but not documented
            env_vars = re.findall(r'os\.environ\.get\(["\'](\w+)["\']', content)
            env_vars += re.findall(r'os\.getenv\(["\'](\w+)["\']', content)
            env_vars += re.findall(r'process\.env\.(\w+)', content)
            if env_vars:
                dotenv_example = os.path.join(workspace_root, ".env.example")
                readme = os.path.join(workspace_root, "README.md")
                has_doc = os.path.isfile(dotenv_example) or os.path.isfile(readme)
                if not has_doc:
                    findings.append({
                        "file": rel_path,
                        "issue": f"Uses env vars {env_vars[:3]} but no .env.example or README found.",
                        "severity": "medium",
                    })

        return findings


# ---------------------------------------------------------------------------
# Docs checker
# ---------------------------------------------------------------------------

DOC_FILE_PATTERNS = [
    "README.md", "README.rst", "README.txt",
    "CHANGELOG.md", "CHANGELOG.rst",
    "CONTRIBUTING.md",
    "docs/**/*.md", "docs/**/*.rst",
]

API_DOC_PATTERNS = [
    re.compile(r'@app\.(get|post|put|delete|patch)\s*\(', re.IGNORECASE),  # Flask/FastAPI
    re.compile(r'router\.(get|post|put|delete|patch)\s*\(', re.IGNORECASE),  # Express
    re.compile(r'#\[(?:get|post|put|delete|patch)\]', re.IGNORECASE),  # Actix/Axum
]


class DocsChecker:
    """Identify documentation files that may need updating after code changes."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def check(self, changed_files: List[str]) -> Dict[str, Any]:
        """
        Returns:
          affected_docs: doc files that reference changed modules
          api_changes: changed files that expose API routes
          missing_docs: source files with no adjacent doc reference
          recommendations: list of action strings
        """
        affected_docs = []
        api_changes = []
        recommendations = []

        # Find existing doc files
        existing_docs = self._find_doc_files()

        for rel_path in changed_files:
            abs_path = os.path.join(self.workspace_root, rel_path)
            if not os.path.isfile(abs_path):
                continue

            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            # Check if this file defines API routes
            for pattern in API_DOC_PATTERNS:
                if pattern.search(content):
                    api_changes.append(rel_path)
                    recommendations.append(f"'{rel_path}' has API routes — update API docs.")
                    break

            # Check README references
            module_name = os.path.splitext(os.path.basename(rel_path))[0]
            for doc in existing_docs:
                try:
                    with open(os.path.join(self.workspace_root, doc), "r", encoding="utf-8", errors="replace") as f:
                        doc_content = f.read()
                    if module_name.lower() in doc_content.lower():
                        if doc not in affected_docs:
                            affected_docs.append(doc)
                except Exception:
                    pass

        # Check changelog
        changelog = next((d for d in existing_docs if "changelog" in d.lower()), None)
        if changed_files and not changelog:
            recommendations.append("No CHANGELOG found — consider adding one.")
        elif changelog:
            recommendations.append(f"Update '{changelog}' with these changes.")

        # Check .env.example
        if any(".env" in f for f in changed_files):
            recommendations.append("'.env' file changed — update .env.example if needed.")

        return {
            "affected_docs": affected_docs,
            "api_changes": api_changes,
            "recommendations": recommendations,
            "existing_docs": existing_docs,
        }

    def _find_doc_files(self) -> List[str]:
        """Find documentation files in the workspace."""
        docs = []
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.workspace_root).replace(os.sep, "/")
                if any(re.match(pat.replace("**", ".*").replace("*", "[^/]*"), rel)
                       for pat in DOC_FILE_PATTERNS):
                    docs.append(rel)
        return docs


# ---------------------------------------------------------------------------
# Handoff report generator
# ---------------------------------------------------------------------------

class HandoffGenerator:
    """Generate a developer-ready handoff report."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        path_hash = hashlib.md5(workspace_root.encode()).hexdigest()
        self.handoff_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "handoffs", path_hash
        )
        os.makedirs(self.handoff_dir, exist_ok=True)

    def generate(
        self,
        task_description: str,
        changed_files: List[str],
        commands_run: List[str],
        test_results: str,
        risks: List[str],
        limitations: List[str],
        next_steps: List[str],
        decisions: List[str],
    ) -> str:
        """Return formatted markdown handoff report."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"# Handoff Report — {ts}",
            "",
            f"## Task",
            task_description,
            "",
            "## Changed Files",
        ]
        for f in changed_files:
            lines.append(f"- `{f}`")

        lines += ["", "## Commands Run"]
        for cmd in commands_run:
            lines.append(f"- `{cmd}`")

        lines += ["", "## Test Results"]
        lines.append(test_results or "_No test results recorded._")

        if decisions:
            lines += ["", "## Decisions Made"]
            for d in decisions:
                lines.append(f"- {d}")

        if risks:
            lines += ["", "## Risks"]
            for r in risks:
                lines.append(f"- ⚠ {r}")

        if limitations:
            lines += ["", "## Known Limitations"]
            for lim in limitations:
                lines.append(f"- {lim}")

        if next_steps:
            lines += ["", "## Next Steps"]
            for step in next_steps:
                lines.append(f"- [ ] {step}")

        lines += ["", "---", f"_Generated by Ultron at {ts}_"]
        return "\n".join(lines)

    def save(self, content: str) -> str:
        """Save handoff report to disk and return path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.handoff_dir, f"handoff_{ts}.md")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            return f"Error saving handoff: {e}"
        return path


# ---------------------------------------------------------------------------
# Environment doctor
# ---------------------------------------------------------------------------

class EnvironmentDoctor:
    """Validate runtime environment, dependencies, and project startup assumptions."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def run(self) -> List[Dict[str, str]]:
        """Run all checks. Returns list of {check, status, detail}."""
        checks = []

        # Python version
        import sys
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        checks.append({"check": "Python version", "status": "ok", "detail": py_ver})

        # Git available
        import subprocess
        try:
            r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                checks.append({"check": "Git", "status": "ok", "detail": r.stdout.strip()})
            else:
                checks.append({"check": "Git", "status": "error", "detail": "git not found"})
        except Exception:
            checks.append({"check": "Git", "status": "error", "detail": "git not found in PATH"})

        # Check required files
        for fname in ["requirements.txt", "package.json", "Cargo.toml", "go.mod"]:
            fpath = os.path.join(self.workspace_root, fname)
            if os.path.isfile(fpath):
                checks.append({"check": f"{fname} exists", "status": "ok", "detail": fpath})

        # .env file
        env_path = os.path.join(self.workspace_root, ".env")
        env_example = os.path.join(self.workspace_root, ".env.example")
        if os.path.isfile(env_path):
            checks.append({"check": ".env", "status": "ok", "detail": "Present"})
        elif os.path.isfile(env_example):
            checks.append({"check": ".env", "status": "warn", "detail": ".env missing, .env.example found — copy it"})
        else:
            checks.append({"check": ".env", "status": "info", "detail": "No .env or .env.example found"})

        # Ollama
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=3)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                checks.append({"check": "Ollama", "status": "ok", "detail": f"{len(models)} model(s) available"})
            else:
                checks.append({"check": "Ollama", "status": "warn", "detail": "Ollama responded but status not OK"})
        except Exception:
            checks.append({"check": "Ollama", "status": "warn", "detail": "Ollama not reachable at localhost:11434"})

        # Disk space (workspace drive)
        try:
            import shutil as _shutil
            total, used, free = _shutil.disk_usage(self.workspace_root)
            free_gb = free / (1024 ** 3)
            status = "ok" if free_gb > 1.0 else "warn"
            checks.append({"check": "Disk space", "status": status, "detail": f"{free_gb:.1f} GB free"})
        except Exception:
            pass

        return checks


# ---------------------------------------------------------------------------
# Health analyzer
# ---------------------------------------------------------------------------

class HealthAnalyzer:
    """
    Detect code health issues: unused imports, dead code candidates,
    duplicate logic, N+1 patterns, blocking async calls.
    """

    UNUSED_IMPORT_PY = re.compile(r"^import\s+(\w+)", re.MULTILINE)
    ASYNC_BLOCKING = [
        re.compile(r"async def.*:\n.*\b(time\.sleep|requests\.get|requests\.post|open\()\b", re.DOTALL),
        re.compile(r"^\s+(?:time\.sleep|requests\.get|requests\.post)\s*\(", re.MULTILINE),
    ]
    N_PLUS_ONE = re.compile(r"for\s+\w+\s+in\s+\w+.*:\n\s+.*\.(filter|get|find|query)\s*\(", re.DOTALL)
    DEAD_CODE = [
        re.compile(r"^\s*#\s*dead\s*code", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*#\s*unused", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*#\s*TODO.*remove", re.IGNORECASE | re.MULTILINE),
    ]

    def analyze_file(self, abs_path: str, rel_path: str) -> List[Dict[str, Any]]:
        findings = []
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return findings

        ext = os.path.splitext(abs_path)[1].lower()

        # Dead code markers
        for pat in self.DEAD_CODE:
            for m in pat.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                findings.append({"file": rel_path, "line": line_no, "type": "dead_code",
                                  "detail": m.group(0).strip()[:80], "severity": "low"})

        if ext == ".py":
            # Async blocking calls
            for pat in self.ASYNC_BLOCKING:
                for m in pat.finditer(content):
                    line_no = content[:m.start()].count("\n") + 1
                    findings.append({"file": rel_path, "line": line_no, "type": "async_blocking",
                                      "detail": m.group(0).strip()[:80], "severity": "high"})

            # N+1 query pattern
            for m in self.N_PLUS_ONE.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                findings.append({"file": rel_path, "line": line_no, "type": "n_plus_one",
                                  "detail": "Possible N+1 query pattern in loop", "severity": "medium"})

        return findings

    def analyze_workspace(self, workspace_root: str, repo_map=None) -> List[Dict[str, Any]]:
        """Analyze all indexed files."""
        findings = []
        if repo_map:
            files = list(repo_map.index.keys())
        else:
            files = []
            for root, dirs, fs in os.walk(workspace_root):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
                for f in fs:
                    if os.path.splitext(f)[1].lower() in {".py", ".js", ".ts"}:
                        files.append(os.path.relpath(os.path.join(root, f), workspace_root).replace(os.sep, "/"))

        for rel in files:
            abs_path = os.path.join(workspace_root, rel)
            findings.extend(self.analyze_file(abs_path, rel))

        return findings


# ---------------------------------------------------------------------------
# Release readiness checklist
# ---------------------------------------------------------------------------

class ReleaseChecker:
    """Generate a release-readiness checklist."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def check(self) -> List[Dict[str, str]]:
        """Return list of {item, status, detail} release checks."""
        items = []

        # Version file
        for vfile in ["VERSION", "version.txt", "pyproject.toml", "package.json", "Cargo.toml"]:
            vpath = os.path.join(self.workspace_root, vfile)
            if os.path.isfile(vpath):
                items.append({"item": "Version file", "status": "ok", "detail": f"Found: {vfile}"})
                break
        else:
            items.append({"item": "Version file", "status": "warn", "detail": "No version file found"})

        # Changelog
        for cfile in ["CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt"]:
            cpath = os.path.join(self.workspace_root, cfile)
            if os.path.isfile(cpath):
                items.append({"item": "Changelog", "status": "ok", "detail": f"Found: {cfile}"})
                break
        else:
            items.append({"item": "Changelog", "status": "warn", "detail": "No CHANGELOG file found"})

        # Tests dir
        test_dirs = ["tests", "test", "__tests__", "spec"]
        if any(os.path.isdir(os.path.join(self.workspace_root, d)) for d in test_dirs):
            items.append({"item": "Tests directory", "status": "ok", "detail": "Found"})
        else:
            items.append({"item": "Tests directory", "status": "warn", "detail": "No tests directory found"})

        # README
        for rfile in ["README.md", "README.rst", "README.txt"]:
            if os.path.isfile(os.path.join(self.workspace_root, rfile)):
                items.append({"item": "README", "status": "ok", "detail": f"Found: {rfile}"})
                break
        else:
            items.append({"item": "README", "status": "warn", "detail": "No README found"})

        # .env.example
        if os.path.isfile(os.path.join(self.workspace_root, ".env.example")):
            items.append({"item": ".env.example", "status": "ok", "detail": "Present"})
        else:
            items.append({"item": ".env.example", "status": "info", "detail": "Not found (OK if no env vars needed)"})

        # Git clean
        import subprocess
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                if r.stdout.strip():
                    items.append({"item": "Git working tree", "status": "warn",
                                  "detail": f"{len(r.stdout.strip().splitlines())} uncommitted change(s)"})
                else:
                    items.append({"item": "Git working tree", "status": "ok", "detail": "Clean"})
        except Exception:
            pass

        return items
