"""
headless.py - Phase 4: CI/headless JSON mode.
Runs Ultron non-interactively with structured JSON output.
Always requires an explicit workspace path.
"""
import os
import sys
import json
import argparse
from typing import Dict, Any, List, Optional


def run_headless(
    workspace_path: str,
    prompt: str,
    model_name: str = "qwen2.5-coder:7b",
    base_url: str = "http://localhost:11434",
    output_file: Optional[str] = None,
    auto_approve: bool = True,
) -> Dict[str, Any]:
    """
    Run a single Ultron task in headless mode.
    Returns a structured JSON result dict.
    Always requires an explicit workspace_path (no cwd fallback).
    """
    if not workspace_path:
        return {
            "success": False,
            "error": "workspace_path is required in headless mode.",
            "files_changed": [],
            "commands_run": [],
            "evidence": [],
        }

    abs_workspace = os.path.realpath(os.path.abspath(workspace_path))
    if not os.path.isdir(abs_workspace):
        return {
            "success": False,
            "error": f"Workspace path does not exist: {abs_workspace}",
            "files_changed": [],
            "commands_run": [],
            "evidence": [],
        }

    result: Dict[str, Any] = {
        "workspace": abs_workspace,
        "prompt": prompt,
        "model": model_name,
        "success": False,
        "error": None,
        "files_changed": [],
        "commands_run": [],
        "evidence": [],
        "exit_code": 0,
    }

    try:
        from ultron.agent import UltronAgent

        agent = UltronAgent(
            workspace_root=abs_workspace,
            model_name=model_name,
            auto_approve=auto_approve,
            auto_commit=False,
        )
        agent.model.base_url = base_url.rstrip("/")

        if not agent.model.is_available():
            result["error"] = f"Model '{model_name}' not available at {base_url}"
            result["exit_code"] = 1
            return result

        agent.run(prompt)

        result["files_changed"] = list(agent.checkpoint.current_task_files.keys())
        result["commands_run"] = [e["command"] for e in agent.tools.execution_logs[-20:]]
        result["evidence"] = getattr(agent, "_task_evidence", [])
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = 1

    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            result["error"] = (result.get("error") or "") + f" | Output write error: {e}"

    return result


def headless_cli_main():
    """Entry point for `ultron-ci` headless command."""
    parser = argparse.ArgumentParser(
        description="Ultron CI — headless JSON mode for automation pipelines."
    )
    parser.add_argument("--workspace", required=True, help="Absolute path to workspace (required)")
    parser.add_argument("--prompt", required=True, help="Task prompt to execute")
    parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model name")
    parser.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--output", default=None, help="Write JSON result to this file")
    parser.add_argument("--auto-approve", action="store_true", default=True,
                        help="Auto-approve all tool calls (default: True in CI mode)")

    args = parser.parse_args()

    result = run_headless(
        workspace_path=args.workspace,
        prompt=args.prompt,
        model_name=args.model,
        base_url=args.base_url,
        output_file=args.output,
        auto_approve=args.auto_approve,
    )

    print(json.dumps(result, indent=2))
    sys.exit(result.get("exit_code", 0))
