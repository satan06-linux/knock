"""
conversation.py - Message history management & tool call parsing for Ultron Agent.
"""
import re
import json
from typing import List, Dict, Any, Optional

VALID_MODES = {"ask", "plan", "build", "fix", "review"}

_MODE_BLOCKED_TOOLS: Dict[str, set] = {
    "ask":    {"write_file", "patch_file", "git_commit", "run_command"},
    "plan":   {"write_file", "patch_file", "git_commit", "run_command"},
    "review": {"write_file", "patch_file", "git_commit"},
    "build":  set(),
    "fix":    set(),
}

_REFACTOR_KEYWORDS = {
    "refactor", "rename", "move", "extract", "restructure", "reorganize",
}


def parse_fallback_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse fallback tool calls from textual JSON responses from LLM."""
    content = content.strip()
    if not content:
        return []
        
    code_block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    if code_block_match:
        json_str = code_block_match.group(1).strip()
    else:
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = content[start_idx:end_idx+1].strip()
        else:
            return []
            
    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            if "name" in data and "arguments" in data:
                return [{
                    "id": "call_fallback",
                    "type": "function",
                    "function": {
                        "name": data["name"],
                        "arguments": data["arguments"]
                    }
                }]
            elif "tool_calls" in data:
                calls = []
                for tc in data["tool_calls"]:
                    if "function" in tc:
                        calls.append(tc)
                return calls
            elif "function" in data and "parameters" in data:
                return [{
                    "id": "call_fallback",
                    "type": "function",
                    "function": {
                        "name": data["function"],
                        "arguments": data["parameters"]
                    }
                }]
        elif isinstance(data, list):
            calls = []
            for item in data:
                if isinstance(item, dict) and "name" in item:
                    calls.append({
                        "id": "call_fallback",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item.get("arguments", {})
                        }
                    })
            return calls
    except Exception:
        pass
    return []


def get_trimmed_history_messages(messages: List[Dict[str, Any]], max_messages: int = 40) -> List[Dict[str, Any]]:
    """Trim message history while preserving system prompt and recent context."""
    if len(messages) <= max_messages:
        return messages

    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    recent_messages = messages[-max_messages:]
    
    if system_msg and recent_messages[0] != system_msg:
        return [system_msg] + recent_messages
    return recent_messages
