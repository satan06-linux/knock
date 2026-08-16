"""
task_replay.py - P4.2: Task Replay.
Records and replays task execution timelines for debugging.
Source code NOT recorded by default (privacy).
"""
import os
import json
import hashlib
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional


@dataclass
class ReplayAction:
    timestamp: str
    action_type: str     # "tool_call" | "model_response" | "user_prompt" | "policy_decision"
    tool: Optional[str] = None
    args_summary: str = ""
    result_summary: str = ""
    decision: Optional[str] = None
    risk: Optional[str] = None


@dataclass
class TaskReplayRecord:
    task_id: str
    prompt: str
    intent: str
    model: str
    provider: str
    started_at: str
    completed_at: str = ""
    final_status: str = ""
    files_changed: List[str] = field(default_factory=list)
    actions: List[ReplayAction] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


class TaskReplay:
    """
    Records task execution for later replay and debugging.
    Stored in ~/.ultron/replays/<workspace_hash>/<task_id>.json
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        path_hash = hashlib.md5(
            os.path.realpath(workspace_root).encode()
        ).hexdigest()
        self.replay_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "replays", path_hash
        )
        os.makedirs(self.replay_dir, exist_ok=True)

    def start_recording(self, task_id: str, prompt: str, intent: str,
                        model: str, provider: str) -> TaskReplayRecord:
        record = TaskReplayRecord(
            task_id=task_id,
            prompt=prompt[:300],
            intent=intent,
            model=model,
            provider=provider,
            started_at=datetime.now().isoformat(),
        )
        return record

    def record_action(self, record: TaskReplayRecord, action: ReplayAction):
        record.actions.append(action)

    def record_tool_call(self, record: TaskReplayRecord, tool: str,
                         args_summary: str, result_summary: str,
                         decision: str = "ALLOW", risk: str = "unknown"):
        record.actions.append(ReplayAction(
            timestamp=datetime.now().isoformat(),
            action_type="tool_call",
            tool=tool,
            args_summary=args_summary[:100],
            result_summary=result_summary[:200],
            decision=decision,
            risk=risk,
        ))

    def record_model_response(self, record: TaskReplayRecord, response_summary: str):
        record.actions.append(ReplayAction(
            timestamp=datetime.now().isoformat(),
            action_type="model_response",
            result_summary=response_summary[:200],
        ))

    def finalize(self, record: TaskReplayRecord, status: str,
                 files_changed: List[str], evidence: List[str]):
        record.completed_at = datetime.now().isoformat()
        record.final_status = status
        record.files_changed = files_changed
        record.evidence = [str(e)[:100] for e in evidence[:10]]
        self._save(record)

    def _save(self, record: TaskReplayRecord):
        path = os.path.join(self.replay_dir, f"{record.task_id}.json")
        try:
            tmp = path + ".tmp"
            data = {
                "task_id": record.task_id,
                "prompt": record.prompt,
                "intent": record.intent,
                "model": record.model,
                "provider": record.provider,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "final_status": record.final_status,
                "files_changed": record.files_changed,
                "evidence": record.evidence,
                "actions": [
                    {k: v for k, v in a.__dict__.items() if v is not None}
                    for a in record.actions
                ],
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def load(self, task_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.replay_dir, f"{task_id}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def list_recent(self, count: int = 10) -> List[Dict[str, Any]]:
        try:
            files = sorted(
                [f for f in os.listdir(self.replay_dir) if f.endswith(".json")],
                key=lambda f: os.path.getmtime(os.path.join(self.replay_dir, f)),
                reverse=True
            )[:count]
            records = []
            for fname in files:
                data = self.load(os.path.splitext(fname)[0])
                if data:
                    records.append(data)
            return records
        except Exception:
            return []

    def format_timeline(self, record: Dict[str, Any]) -> str:
        lines = [
            f"Task: {record.get('task_id')}",
            f"Prompt: {record.get('prompt', '')[:80]}",
            f"Intent: {record.get('intent')}  Status: {record.get('final_status')}",
            f"Model: {record.get('provider')}/{record.get('model')}",
            f"Started: {record.get('started_at', '')[:19]}  Completed: {record.get('completed_at', '')[:19]}",
            f"Files: {', '.join(record.get('files_changed', []))}",
            "",
            "Timeline:",
        ]
        for action in record.get("actions", []):
            ts = action.get("timestamp", "")[:19]
            atype = action.get("action_type", "?")
            tool = action.get("tool", "")
            decision = action.get("decision", "")
            result = action.get("result_summary", "")[:60]
            line = f"  [{ts}] {atype}"
            if tool:
                line += f" → {tool}"
            if decision:
                line += f" [{decision}]"
            if result:
                line += f": {result}"
            lines.append(line)
        return "\n".join(lines)
