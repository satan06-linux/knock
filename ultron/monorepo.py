"""
monorepo.py - Phase 4: Monorepo and multi-project support.
Detects packages, services, shared libraries, workspace aliases, /recent.
"""
import os
import json
import re
import fnmatch
import hashlib
from typing import Dict, List, Any, Optional, Tuple


# ---------------------------------------------------------------------------
# Package signatures per ecosystem
# ---------------------------------------------------------------------------

PACKAGE_SIGNATURES = [
    ("package.json",    "node",   "NodeJS/TypeScript"),
    ("Cargo.toml",      "rust",   "Rust"),
    ("go.mod",          "go",     "Go"),
    ("setup.py",        "python", "Python"),
    ("pyproject.toml",  "python", "Python"),
    ("requirements.txt","python", "Python"),
    ("pom.xml",         "java",   "Java (Maven)"),
    ("build.gradle",    "java",   "Java (Gradle)"),
    ("CMakeLists.txt",  "cpp",    "C/C++ (CMake)"),
    ("Makefile",        "make",   "C/C++ (Makefile)"),
]

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "target", "build", "dist", "vendor", ".pytest_cache", ".mypy_cache",
}


# ---------------------------------------------------------------------------
# Package detector
# ---------------------------------------------------------------------------

class Package:
    def __init__(self, path: str, ecosystem: str, lang: str, name: str):
        self.path = path          # absolute path to package root
        self.ecosystem = ecosystem
        self.lang = lang
        self.name = name          # derived from manifest or dir name

    def rel_path(self, workspace_root: str) -> str:
        return os.path.relpath(self.path, workspace_root).replace(os.sep, "/")

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "ecosystem": self.ecosystem, "lang": self.lang, "name": self.name}


def _read_package_name(manifest_path: str, ecosystem: str) -> str:
    """Try to extract a package name from the manifest file."""
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if ecosystem == "node":
            m = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            if m:
                return m.group(1)
        elif ecosystem == "rust":
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', content, re.MULTILINE)
            if m:
                return m.group(1)
        elif ecosystem == "go":
            m = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
            if m:
                return m.group(1).split("/")[-1]
        elif ecosystem == "python":
            m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                return m.group(1)
    except Exception:
        pass
    return os.path.basename(os.path.dirname(manifest_path))


class MonorepoDetector:
    """Detect packages/services in a workspace."""

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(workspace_root)

    def detect_packages(self, max_depth: int = 4) -> List[Package]:
        """Walk workspace up to max_depth, find all package roots."""
        packages = []
        seen_paths = set()

        for root, dirs, files in os.walk(self.workspace_root):
            # Prune ignored dirs
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

            # Check depth
            rel = os.path.relpath(root, self.workspace_root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > max_depth:
                dirs.clear()
                continue

            for manifest, ecosystem, lang in PACKAGE_SIGNATURES:
                if manifest in files:
                    if root not in seen_paths:
                        seen_paths.add(root)
                        manifest_path = os.path.join(root, manifest)
                        name = _read_package_name(manifest_path, ecosystem)
                        packages.append(Package(root, ecosystem, lang, name))
                    break  # one package per dir

        return packages

    def get_active_package(self, packages: List[Package], file_path: str) -> Optional[Package]:
        """Find the package that contains a given file path."""
        abs_file = os.path.realpath(os.path.abspath(file_path))
        # Find the deepest matching package
        best = None
        best_len = 0
        for pkg in packages:
            pkg_abs = os.path.realpath(pkg.path)
            try:
                if abs_file.startswith(pkg_abs + os.sep) or abs_file == pkg_abs:
                    if len(pkg_abs) > best_len:
                        best = pkg
                        best_len = len(pkg_abs)
            except Exception:
                pass
        return best

    def get_targeted_commands(self, pkg: Package) -> Dict[str, str]:
        """Return targeted test/build/lint commands for a package."""
        cmds: Dict[str, str] = {}
        rel = os.path.relpath(pkg.path, self.workspace_root).replace(os.sep, "/")

        if pkg.ecosystem == "node":
            cmds["test"] = f"npm test --prefix {rel}"
            cmds["build"] = f"npm run build --prefix {rel}"
            cmds["lint"] = f"npm run lint --prefix {rel}"
        elif pkg.ecosystem == "rust":
            cmds["test"] = f"cargo test --manifest-path {rel}/Cargo.toml"
            cmds["build"] = f"cargo build --manifest-path {rel}/Cargo.toml"
            cmds["lint"] = f"cargo clippy --manifest-path {rel}/Cargo.toml"
        elif pkg.ecosystem == "go":
            cmds["test"] = f"go test ./{rel}/..."
            cmds["build"] = f"go build ./{rel}/..."
            cmds["lint"] = f"go vet ./{rel}/..."
        elif pkg.ecosystem == "python":
            cmds["test"] = f"pytest {rel}"
            cmds["lint"] = f"flake8 {rel}"
        elif pkg.ecosystem == "make":
            cmds["build"] = f"make -C {rel}"
            cmds["test"] = f"make -C {rel} test"

        return cmds

    def is_monorepo(self, packages: List[Package]) -> bool:
        """True if more than one package detected."""
        return len(packages) > 1


# ---------------------------------------------------------------------------
# Workspace aliases + /recent
# ---------------------------------------------------------------------------

class WorkspaceAliasManager:
    """
    Persists named workspace aliases and recent workspace history.
    Stored in ~/.ultron/workspaces/aliases.json
    """

    def __init__(self):
        self.store_dir = os.path.join(os.path.expanduser("~"), ".ultron", "workspaces")
        os.makedirs(self.store_dir, exist_ok=True)
        self.aliases_path = os.path.join(self.store_dir, "aliases.json")
        self.recent_path = os.path.join(self.store_dir, "recent.json")
        self._aliases: Dict[str, str] = self._load(self.aliases_path, {})
        self._recent: List[Dict[str, str]] = self._load(self.recent_path, [])

    def _load(self, path: str, default):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save(self, path: str, data):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def add_alias(self, name: str, workspace_path: str) -> str:
        abs_path = os.path.realpath(os.path.abspath(workspace_path))
        if not os.path.isdir(abs_path):
            return f"Error: '{workspace_path}' is not a valid directory."
        self._aliases[name] = abs_path
        self._save(self.aliases_path, self._aliases)
        return f"Alias '{name}' → {abs_path}"

    def remove_alias(self, name: str) -> str:
        if name not in self._aliases:
            return f"Alias '{name}' not found."
        del self._aliases[name]
        self._save(self.aliases_path, self._aliases)
        return f"Removed alias '{name}'."

    def resolve_alias(self, name: str) -> Optional[str]:
        return self._aliases.get(name)

    def list_aliases(self) -> Dict[str, str]:
        return dict(self._aliases)

    def record_recent(self, workspace_path: str):
        abs_path = os.path.realpath(os.path.abspath(workspace_path))
        # Remove existing entry if present
        self._recent = [r for r in self._recent if r.get("path") != abs_path]
        self._recent.insert(0, {
            "path": abs_path,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        })
        self._recent = self._recent[:20]  # keep last 20
        self._save(self.recent_path, self._recent)

    def get_recent(self, count: int = 10) -> List[Dict[str, str]]:
        return self._recent[:count]
