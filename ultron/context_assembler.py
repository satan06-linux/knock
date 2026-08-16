"""
context_assembler.py - P2.6: Intelligent context assembly.
Builds task-appropriate context based on intent — not just a flat 50k char dump.
Token-aware (uses provider context_window, not just char count).
"""
import os
from typing import List, Dict, Any, Optional

from ultron.task import TaskIntent


# Tokens per context type (approximate — 1 token ≈ 4 chars)
TREE_TOKENS       = 500    # workspace tree
FILE_TOKENS       = 2000   # per pinned file
SYMBOL_TOKENS     = 300    # symbol index
CONVENTION_TOKENS = 1500   # similar file conventions

# Strategy per intent
_STRATEGY: Dict[str, List[str]] = {
    TaskIntent.ASK.value:      ["tree", "pinned"],
    TaskIntent.ANALYZE.value:  ["tree", "pinned", "symbols"],
    TaskIntent.DEBUG.value:    ["tree", "pinned", "error_context", "test_files"],
    TaskIntent.FEATURE.value:  ["tree", "pinned", "conventions", "entry_points"],
    TaskIntent.REFACTOR.value: ["tree", "pinned", "callers", "test_files"],
    TaskIntent.TEST.value:     ["tree", "pinned", "test_files"],
    TaskIntent.REVIEW.value:   ["tree", "pinned"],
    TaskIntent.SETUP.value:    ["tree", "pinned"],
    TaskIntent.UNKNOWN.value:  ["tree", "pinned"],
}


class ContextAssembler:
    """
    Assembles model context based on task intent.
    Respects provider context_window (token-aware budget).
    Principle: fast first, deep when necessary.
    """

    def __init__(
        self,
        workspace_root: str,
        context_manager,        # existing ContextManager
        repo_map=None,
        project_profile=None,
        provider_context_window: int = 16384,
    ):
        self.workspace_root = workspace_root
        self.ctx = context_manager
        self.repo_map = repo_map
        self.profile = project_profile
        self.context_window = provider_context_window
        # Reserve 40% for model output + conversation history
        self.available_tokens = int(provider_context_window * 0.6)

    def assemble(
        self,
        intent: str,
        task_description: str = "",
        last_error: str = "",
    ) -> str:
        """
        Build context string for the given intent.
        Returns formatted context block to inject into system message.
        """
        strategy = _STRATEGY.get(intent, _STRATEGY[TaskIntent.UNKNOWN.value])
        sections = []
        used_tokens = 0

        for component in strategy:
            if used_tokens >= self.available_tokens:
                sections.append(f"\n[Context budget exhausted at {used_tokens} tokens]")
                break

            content, tokens = self._get_component(component, task_description, last_error)
            if content and tokens > 0:
                sections.append(content)
                used_tokens += tokens

        return "\n".join(sections)

    def _get_component(self, name: str, task_desc: str, last_error: str):
        """Returns (content_str, estimated_tokens)."""
        if name == "tree":
            tree = self.ctx.get_workspace_tree()
            tokens = len(tree) // 4
            return f"=== WORKSPACE TREE ===\n{tree}", tokens

        elif name == "pinned":
            content = self.ctx.build_context_prompt()
            tokens = len(content) // 4
            return content, tokens

        elif name == "symbols" and self.repo_map and self.repo_map.index:
            summary = self.repo_map.get_summary()
            lines = [f"=== REPOSITORY SYMBOLS ==="]
            lines.append(f"Total: {summary['total_files']} files, {summary['test_files']} tests")
            for lang, count in sorted(summary.get("by_language", {}).items(), key=lambda x: -x[1])[:5]:
                lines.append(f"  {lang}: {count} files")
            content = "\n".join(lines)
            return content, SYMBOL_TOKENS

        elif name == "conventions" and self.repo_map and self.repo_map.index:
            from ultron.analyzer import ConventionFinder
            cf = ConventionFinder(self.workspace_root, self.repo_map)
            similar = cf.find_similar_files(task_desc, max_results=3)
            if not similar:
                return "", 0
            lines = ["=== SIMILAR EXISTING FILES (study conventions) ==="]
            for f in similar:
                snippet = cf.read_conventions(f, lines=20)
                lines.append(f"\n--- {f} ---\n{snippet}")
            content = "\n".join(lines)
            return content, CONVENTION_TOKENS

        elif name == "entry_points" and self.profile:
            eps = self.profile.get_entry_points()
            if not eps:
                return "", 0
            content = "=== ENTRY POINTS ===\n" + "\n".join(f"  {f}" for f in eps)
            return content, 100

        elif name == "error_context" and last_error:
            content = f"=== LAST ERROR ===\n{last_error[-1000:]}"
            return content, len(last_error) // 4

        elif name == "test_files" and self.repo_map:
            tests = self.repo_map.get_test_files()[:5]
            if not tests:
                return "", 0
            content = "=== TEST FILES ===\n" + "\n".join(f"  {f}" for f in tests)
            return content, 100

        elif name == "callers" and self.repo_map and self.repo_map.index:
            # For refactor: show files that import pinned files
            importers = []
            for pinned in list(self.ctx.pinned_files)[:3]:
                importers.extend(self.repo_map.who_imports(pinned))
            if not importers:
                return "", 0
            content = "=== FILES THAT IMPORT PINNED FILES ===\n" + "\n".join(f"  {f}" for f in set(importers)[:8])
            return content, 200

        return "", 0

    def usage_ratio(self, content: str) -> float:
        """Return current context usage as ratio of provider limit."""
        tokens = len(content) // 4
        return tokens / max(self.context_window, 1)
