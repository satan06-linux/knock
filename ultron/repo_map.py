"""
repo_map.py - Repository indexer for Phase 2 Project Intelligence.
Builds a compact map of files, symbols, imports, tests, and dependencies
using regex-based walking (no external dependencies required).
"""
import os
import re
import fnmatch
import hashlib
import json
import time
from typing import Dict, List, Optional, Any, Tuple

# File extensions to index per language
LANG_EXTENSIONS = {
    "python":     [".py"],
    "javascript": [".js", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "go":         [".go"],
    "rust":       [".rs"],
    "java":       [".java"],
    "c":          [".c", ".h"],
    "cpp":        [".cpp", ".cc", ".cxx", ".hpp"],
    "ruby":       [".rb"],
    "php":        [".php"],
}

ALL_CODE_EXTS = {ext for exts in LANG_EXTENSIONS.values() for ext in exts}

# Regex patterns per language for symbol extraction
SYMBOL_PATTERNS = {
    "python": [
        (re.compile(r"^class\s+(\w+)", re.MULTILINE),          "class"),
        (re.compile(r"^def\s+(\w+)", re.MULTILINE),            "function"),
        (re.compile(r"^\s+def\s+(\w+)", re.MULTILINE),         "method"),
        (re.compile(r"^async\s+def\s+(\w+)", re.MULTILINE),    "async_function"),
    ],
    "javascript": [
        (re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(r"^(?:export\s+)?class\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.MULTILINE), "arrow_function"),
    ],
    "typescript": [
        (re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(r"^(?:export\s+)?interface\s+(\w+)", re.MULTILINE), "interface"),
        (re.compile(r"^(?:export\s+)?type\s+(\w+)\s*=", re.MULTILINE), "type"),
        (re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.MULTILINE), "arrow_function"),
    ],
    "go": [
        (re.compile(r"^func\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(r"^func\s+\(\w+\s+\*?\w+\)\s+(\w+)", re.MULTILINE), "method"),
        (re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE), "struct"),
        (re.compile(r"^type\s+(\w+)\s+interface", re.MULTILINE), "interface"),
    ],
    "rust": [
        (re.compile(r"^pub\s+fn\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(r"^fn\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(r"^pub\s+struct\s+(\w+)", re.MULTILINE), "struct"),
        (re.compile(r"^pub\s+trait\s+(\w+)", re.MULTILINE), "trait"),
        (re.compile(r"^impl\s+(\w+)", re.MULTILINE), "impl"),
    ],
}

IMPORT_PATTERNS = {
    "python":     re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))", re.MULTILINE),
    "javascript": re.compile(r"""(?:import\s+.*?from\s+['\"](.*?)['\"]|require\s*\(\s*['\"](.*?)['\"])\s*"""),
    "typescript": re.compile(r"""(?:import\s+.*?from\s+['\"](.*?)['\"]|require\s*\(\s*['\"](.*?)['\"])\s*"""),
    "go":         re.compile(r'"([\w./]+)"'),
    "rust":       re.compile(r"^use\s+([\w:]+)", re.MULTILINE),
}


def _detect_lang(filepath: str) -> Optional[str]:
    ext = os.path.splitext(filepath)[1].lower()
    for lang, exts in LANG_EXTENSIONS.items():
        if ext in exts:
            return lang
    return None


def _is_test_file(filepath: str) -> bool:
    name = os.path.basename(filepath).lower()
    return (
        name.startswith("test_") or name.endswith("_test.py") or
        name.endswith(".test.ts") or name.endswith(".test.js") or
        name.endswith(".spec.ts") or name.endswith(".spec.js") or
        "test" in name.split(os.sep) or
        os.sep + "tests" + os.sep in filepath or
        os.sep + "test" + os.sep in filepath
    )


class RepoMap:
    """
    Compact indexed representation of the repository.
    Stores per-file: language, symbols, imports, is_test flag.
    Cached to disk and invalidated by file mtime changes.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        path_hash = hashlib.md5(self.workspace_root.encode()).hexdigest()
        self.cache_dir = os.path.join(os.path.expanduser("~"), ".ultron", "repo_map", path_hash)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_path = os.path.join(self.cache_dir, "index.json")

        self.ignore_patterns = [
            ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
            ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
            "build", "dist", "*.egg-info", "target", "vendor",
        ]

        # In-memory index: {rel_path: {lang, symbols, imports, is_test, mtime}}
        self.index: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self):
        if os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
            except Exception:
                self.index = {}

    def _save_cache(self):
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.index, f)
            os.replace(tmp, self.cache_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Ignore logic
    # ------------------------------------------------------------------

    def _is_ignored(self, path: str) -> bool:
        parts = os.path.normpath(path).split(os.sep)
        for part in parts:
            for pat in self.ignore_patterns:
                if fnmatch.fnmatch(part, pat):
                    return True
        return False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _extract_symbols(self, content: str, lang: str) -> List[Dict[str, Any]]:
        symbols = []
        patterns = SYMBOL_PATTERNS.get(lang, [])
        for pattern, kind in patterns:
            for m in pattern.finditer(content):
                line_no = content[:m.start()].count("\n") + 1
                symbols.append({"name": m.group(1), "kind": kind, "line": line_no})
        return symbols

    def _extract_imports(self, content: str, lang: str) -> List[str]:
        pattern = IMPORT_PATTERNS.get(lang)
        if not pattern:
            return []
        imports = []
        for m in pattern.finditer(content):
            for g in m.groups():
                if g:
                    imports.append(g.strip())
        return list(set(imports))

    def _index_file(self, abs_path: str, rel_path: str) -> Dict[str, Any]:
        lang = _detect_lang(abs_path)
        mtime = os.path.getmtime(abs_path)
        entry: Dict[str, Any] = {
            "lang": lang,
            "symbols": [],
            "imports": [],
            "is_test": _is_test_file(abs_path),
            "mtime": mtime,
        }
        if lang:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                entry["symbols"] = self._extract_symbols(content, lang)
                entry["imports"] = self._extract_imports(content, lang)
            except Exception:
                pass
        return entry

    def build(self, force: bool = False) -> int:
        """Walk workspace and (re)index changed files. Returns count of indexed files."""
        indexed = 0
        seen = set()

        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if not self._is_ignored(os.path.join(root, d))]

            for fname in files:
                abs_path = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALL_CODE_EXTS:
                    continue
                if self._is_ignored(abs_path):
                    continue

                rel_path = os.path.relpath(abs_path, self.workspace_root).replace(os.sep, "/")
                seen.add(rel_path)

                mtime = os.path.getmtime(abs_path)
                cached = self.index.get(rel_path)
                if not force and cached and abs(cached.get("mtime", 0) - mtime) < 0.01:
                    continue  # unchanged

                self.index[rel_path] = self._index_file(abs_path, rel_path)
                indexed += 1

        # Remove stale entries
        stale = [k for k in self.index if k not in seen]
        for k in stale:
            del self.index[k]

        self._save_cache()
        return indexed

    def refresh(self):
        """Incremental refresh."""
        return self.build(force=False)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def find_symbol(self, name: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Find all symbols matching name (case-insensitive substring)."""
        results = []
        name_lower = name.lower()
        for rel_path, entry in self.index.items():
            for sym in entry.get("symbols", []):
                if name_lower in sym["name"].lower():
                    if kind and sym["kind"] != kind:
                        continue
                    results.append({
                        "file": rel_path,
                        "name": sym["name"],
                        "kind": sym["kind"],
                        "line": sym["line"],
                    })
        return results

    def find_references(self, symbol: str) -> List[Dict[str, Any]]:
        """Find all files/lines that reference a symbol name."""
        results = []
        pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
        for rel_path, entry in self.index.items():
            abs_path = os.path.join(self.workspace_root, rel_path)
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    if pattern.search(line):
                        results.append({
                            "file": rel_path,
                            "line": i,
                            "text": line.strip(),
                        })
                        if len(results) >= 200:
                            return results
            except Exception:
                continue
        return results

    def find_text(self, query: str, case_sensitive: bool = False) -> List[Dict[str, Any]]:
        """Text search across all indexed files."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error:
            pattern = re.compile(re.escape(query), flags)

        results = []
        for rel_path in self.index:
            abs_path = os.path.join(self.workspace_root, rel_path)
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    if pattern.search(line):
                        results.append({"file": rel_path, "line": i, "text": line.strip()})
                        if len(results) >= 200:
                            return results
            except Exception:
                continue
        return results

    def get_file_symbols(self, rel_path: str) -> List[Dict[str, Any]]:
        rel_path = rel_path.replace(os.sep, "/")
        entry = self.index.get(rel_path, {})
        return entry.get("symbols", [])

    def get_imports(self, rel_path: str) -> List[str]:
        rel_path = rel_path.replace(os.sep, "/")
        entry = self.index.get(rel_path, {})
        return entry.get("imports", [])

    def get_test_files(self) -> List[str]:
        return [p for p, e in self.index.items() if e.get("is_test")]

    def get_summary(self) -> Dict[str, Any]:
        total = len(self.index)
        by_lang: Dict[str, int] = {}
        test_count = 0
        for entry in self.index.values():
            lang = entry.get("lang") or "other"
            by_lang[lang] = by_lang.get(lang, 0) + 1
            if entry.get("is_test"):
                test_count += 1
        return {"total_files": total, "by_language": by_lang, "test_files": test_count}

    def find_related_tests(self, rel_path: str) -> List[str]:
        """Find test files likely related to a given source file."""
        base = os.path.splitext(os.path.basename(rel_path))[0].lower()
        related = []
        for p, e in self.index.items():
            if e.get("is_test") and base in os.path.basename(p).lower():
                related.append(p)
        return related

    def callers_of(self, symbol: str) -> List[Dict[str, Any]]:
        """Find call sites of a symbol (files that reference it, excluding its definition)."""
        defs = {r["file"] for r in self.find_symbol(symbol)}
        refs = self.find_references(symbol)
        return [r for r in refs if r["file"] not in defs]

    def who_imports(self, rel_path: str) -> List[str]:
        """Find files that import from the given module path."""
        # Build a set of possible module identifiers for this file
        norm = rel_path.replace("/", ".").replace(os.sep, ".")
        base = os.path.splitext(norm)[0]  # strip extension
        parts = base.split(".")

        results = []
        for p, e in self.index.items():
            if p == rel_path:
                continue
            for imp in e.get("imports", []):
                imp_norm = imp.replace("/", ".").replace(os.sep, ".")
                if any(part in imp_norm for part in parts[-2:]):
                    results.append(p)
                    break
        return results
