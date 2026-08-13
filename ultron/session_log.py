"""
session_log.py - Structured JSONL session logging.
Logs every tool call and model call to ~/.ultron/logs/<hash>/session_YYYYMMDD.jsonl
Builds user trust: "I can see exactly what Ultron did."
"""
import os
import json
import hashlib
from datetime import datetime
from typing import Any, Dict, Optional


class SessionLogger:
    """Persistent structured logger for all Ultron agent activity."""

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        path_hash = hashlib.md5(self.workspace_root.encode()).hexdigest()
        self.log_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "logs", path_hash
        )
        os.makedirs(self.log_dir, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        self.log_path = os.path.join(self.log_dir, f"session_{today}.jsonl")

    def _write(self, entry: Dict[str, Any]):
        entry["timestamp"] = datetime.now().isoformat()
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # logging must never crash the agent

    def log_tool_call(
        self,
        tool: str,
        args_summary: str,
        result: str,
        exit_code: int = 0,
        risk_level: str = "unknown",
    ):
        self._write({
            "event": "tool_call",
            "tool": tool,
            "args": args_summary[:200],
            "result": result[:300],
            "exit_code": exit_code,
            "risk_level": risk_level,
        })

    def log_model_call(
        self,
        provider: str,
        model: str,
        prompt_chars: int,
        response_chars: int,
        tool_calls_count: int = 0,
    ):
        self._write({
            "event": "model_call",
            "provider": provider,
            "model": model,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "tool_calls": tool_calls_count,
        })

    def log_task_start(self, task_id: str, intent: str, prompt: str):
        self._write({
            "event": "task_start",
            "task_id": task_id,
            "intent": intent,
            "prompt": prompt[:200],
        })

    def log_task_end(self, task_id: str, status: str, files_changed: list, elapsed: float):
        self._write({
            "event": "task_end",
            "task_id": task_id,
            "status": status,
            "files_changed": files_changed,
            "elapsed_seconds": round(elapsed, 2),
        })

    def log_provider_event(self, event: str, provider: str, detail: str = ""):
        self._write({
            "event": f"provider_{event}",
            "provider": provider,
            "detail": detail[:200],
        })

    def load_today(self) -> list:
        """Load today's log entries."""
        if not os.path.isfile(self.log_path):
            return []
        entries = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        return entries

    def load_recent(self, days: int = 7) -> list:
        """Load log entries from the last N days."""
        entries = []
        try:
            for fname in sorted(os.listdir(self.log_dir), reverse=True)[:days]:
                if fname.startswith("session_") and fname.endswith(".jsonl"):
                    fpath = os.path.join(self.log_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    try:
                                        entries.append(json.loads(line))
                                    except Exception:
                                        pass
                    except Exception:
                        pass
        except Exception:
            pass
        return entries

    def summarize(self, entries: list) -> Dict[str, Any]:
        """Compute summary stats from log entries."""
        tool_calls = [e for e in entries if e.get("event") == "tool_call"]
        model_calls = [e for e in entries if e.get("event") == "model_call"]
        task_starts = [e for e in entries if e.get("event") == "task_start"]
        task_ends = [e for e in entries if e.get("event") == "task_end"]

        total_prompt_chars = sum(e.get("prompt_chars", 0) for e in model_calls)
        tool_counts: Dict[str, int] = {}
        for tc in tool_calls:
            name = tc.get("tool", "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1

        failed_tools = [e for e in tool_calls if e.get("exit_code", 0) != 0]

        return {
            "total_tasks": len(task_starts),
            "total_tool_calls": len(tool_calls),
            "total_model_calls": len(model_calls),
            "total_prompt_chars": total_prompt_chars,
            "tool_usage": tool_counts,
            "failed_tool_calls": len(failed_tools),
            "tasks_completed": sum(
                1 for e in task_ends if e.get("status") == "verified"
            ),
        }
