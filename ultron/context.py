import os
import fnmatch
from typing import Set, List, Dict

class ContextManager:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.pinned_files: Set[str] = set()
        # Same ignore patterns as tool manager
        self.ignore_patterns = [
            ".git", "node_modules", "__pycache__", ".venv", "venv", 
            "env", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
            "build", "dist", "*.egg-info", "*.pyc", "*.o", "*.bin"
        ]

    def _is_ignored(self, path: str) -> bool:
        """Check if a path matches any ignore patterns."""
        parts = os.path.normpath(path).split(os.sep)
        for part in parts:
            for pattern in self.ignore_patterns:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def add_file(self, rel_path: str) -> bool:
        """Add a file to context relative to workspace root. Returns True if successful."""
        from ultron.security import validate_path
        try:
            abs_path = validate_path(rel_path, self.workspace_root)
            if os.path.isfile(abs_path):
                norm_rel_path = os.path.relpath(abs_path, self.workspace_root).replace(os.sep, "/")
                self.pinned_files.add(norm_rel_path)
                return True
        except PermissionError:
            raise
        except Exception:
            pass
        return False

    def drop_file(self, rel_path: str) -> bool:
        """Remove a file from context. Returns True if removed."""
        norm_rel_path = rel_path.replace(os.sep, "/")
        if norm_rel_path in self.pinned_files:
            self.pinned_files.remove(norm_rel_path)
            return True
        
        # Try relative resolution
        abs_path = os.path.abspath(os.path.join(self.workspace_root, rel_path))
        resolved_rel = os.path.relpath(abs_path, self.workspace_root).replace(os.sep, "/")
        if resolved_rel in self.pinned_files:
            self.pinned_files.remove(resolved_rel)
            return True
            
        return False

    def clear(self):
        """Clears all pinned files."""
        self.pinned_files.clear()

    def get_workspace_tree(self) -> str:
        """Generates a text representation of the directory structure (max 200 lines)."""
        tree = []
        for root, dirs, files in os.walk(self.workspace_root):
            # Prune ignored dirs in-place
            dirs[:] = [d for d in dirs if not self._is_ignored(os.path.join(root, d))]
            
            rel_root = os.path.relpath(root, self.workspace_root)
            if rel_root == ".":
                depth = 0
                tree.append(".")
            else:
                depth = rel_root.count(os.sep) + 1
                indent = "  " * depth
                tree.append(f"{indent}{os.path.basename(root)}/")
                
            indent = "  " * (depth + 1)
            for f in sorted(files):
                if not self._is_ignored(os.path.join(root, f)):
                    tree.append(f"{indent}{f}")
                    if len(tree) >= 200:
                        tree.append("... [directory tree truncated, exceeds 200 lines limit] ...")
                        return "\n".join(tree)
                        
        return "\n".join(tree)

    def build_context_prompt(self) -> str:
        """Builds a formatted text block containing pinned file contents (capped at 50,000 chars) and tree layout."""
        lines = []
        lines.append("=== WORKSPACE DIRECTORY STRUCTURE ===")
        lines.append(self.get_workspace_tree())
        lines.append("\n=== ACTIVE CONTEXT FILES ===")
        
        if not self.pinned_files:
            lines.append("No active files are pinned to the context. You can use tools like 'view_file' to read code files if needed.")
        else:
            total_chars = 0
            char_budget = 50000
            
            for rel_path in sorted(self.pinned_files):
                abs_path = os.path.join(self.workspace_root, rel_path)
                lines.append(f"\n--- File: {rel_path} ---")
                if os.path.isfile(abs_path):
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        
                        # Check budget remaining
                        if total_chars + len(content) > char_budget:
                            remaining = char_budget - total_chars
                            if remaining > 0:
                                lines.append(content[:remaining] + "\n... [content truncated: file exceeds 50,000 character context budget] ...")
                                total_chars = char_budget
                            else:
                                lines.append("... [content omitted: 50,000 character context budget exhausted] ...")
                        else:
                            lines.append(content)
                            total_chars += len(content)
                            
                    except Exception as e:
                        lines.append(f"<Error reading file: {str(e)}>")
                else:
                    lines.append("<File no longer exists>")
                    
        return "\n".join(lines)
