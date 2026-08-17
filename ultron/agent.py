import os
import sys
import re
import json
from enum import Enum
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.live import Live
from rich.spinner import Spinner

from ultron.models import OllamaModel
from ultron.tools import ToolManager
from ultron.context import ContextManager
from ultron.diff import generate_diff_panel
from ultron.checkpoint import CheckpointManager
from ultron.onboard import ProjectMemoryManager
from ultron.repo_map import RepoMap
from ultron.analyzer import load_project_instructions, RefactorGuard, ConventionFinder
from ultron.contract import ChangeContractManager
from ultron.providers.registry import ProviderRegistry
from ultron.task import Task, TaskRouter, TaskStatus, TaskIntent, EvidenceKind, BudgetEnforcer
from ultron.tool_registry import ToolRegistry, PolicyEngine, CommandRunner, RiskLevel
from ultron.change_tracker import ChangeTracker
from ultron.eval_suite import MetricsCollector, TaskMetrics
from ultron.git_workflow import DecisionLog
from ultron.session_log import SessionLogger
from ultron.event_bus import get_bus, BusEvent
from ultron.health_monitor import HealthMonitor
from ultron.model_router import ModelRouter, get_health_tracker
from ultron.project_profile import ProjectProfile
from ultron.task_replay import TaskReplay
from ultron.notifications import NotificationManager


# ---------------------------------------------------------------------------
# Evidence tagging (Phase 3)
# ---------------------------------------------------------------------------

class EvidenceTag(str, Enum):
    OBSERVED     = "Observed from code"
    VERIFIED     = "Verified by command"
    INFERRED     = "Inferred"
    NOT_VERIFIED = "Not verified"


# ---------------------------------------------------------------------------
# Intent mode constants (Phase 3)
# ---------------------------------------------------------------------------

VALID_MODES = {"ask", "plan", "build", "fix", "review"}

# Tools forbidden per mode
_MODE_BLOCKED_TOOLS: Dict[str, set] = {
    "ask":    {"write_file", "patch_file", "git_commit", "run_command"},
    "plan":   {"write_file", "patch_file", "git_commit", "run_command"},
    "review": {"write_file", "patch_file", "git_commit"},
    "build":  set(),
    "fix":    set(),
}

# Refactor-intent keywords that trigger automatic safety check
_REFACTOR_KEYWORDS = {
    "refactor", "rename", "move", "extract", "restructure", "reorganize",
}

def parse_fallback_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse fallback tool calls from textual JSON responses from LLM."""
    content = content.strip()
    if not content:
        return []
        
    # Check for markdown code blocks
    code_block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    if code_block_match:
        json_str = code_block_match.group(1).strip()
    else:
        # Find first '{' and last '}'
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = content[start_idx:end_idx+1].strip()
        else:
            return []
            
    try:
        data = json.loads(json_str)
        # Handle single tool call: {"name": "...", "arguments": {...}}
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
            # Handle native OpenAI format if outputted as JSON string: {"tool_calls": [...]}
            elif "tool_calls" in data:
                calls = []
                for tc in data["tool_calls"]:
                    if "function" in tc:
                        calls.append(tc)
                return calls
            # Handle format like {"function": "...", "parameters": {...}}
            elif "function" in data and "parameters" in data:
                return [{
                    "id": "call_fallback",
                    "type": "function",
                    "function": {
                        "name": data["function"],
                        "arguments": data["parameters"]
                    }
                }]
        # Handle array of tool calls: [{"name": "...", "arguments": {...}}, ...]
        elif isinstance(data, list):
            calls = []
            for item in data:
                if isinstance(item, dict) and "name" in item and "arguments" in item:
                    calls.append({
                        "id": f"call_fallback_{len(calls)}",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"]
                        }
                    })
            return calls
    except Exception:
        pass
        
    return []

def validate_tool_args(name: str, args: Any) -> Optional[str]:
    """
    Validate tool arguments. Returns None if valid, or a descriptive error string if invalid.
    """
    if not isinstance(args, dict):
        return f"Arguments must be a dictionary, got {type(args).__name__}"
        
    if name == "list_dir":
        path = args.get("path")
        if path is not None and not isinstance(path, str):
            return "Argument 'path' must be a string."
            
    elif name == "grep_search":
        if "query" not in args:
            return "Missing required argument 'query'."
        if not isinstance(args["query"], str):
            return "Argument 'query' must be a string."
        path = args.get("path")
        if path is not None and not isinstance(path, str):
            return "Argument 'path' must be a string."
            
    elif name == "view_file":
        if "path" not in args:
            return "Missing required argument 'path'."
        if not isinstance(args["path"], str):
            return "Argument 'path' must be a string."
        start_line = args.get("start_line")
        if start_line is not None and not isinstance(start_line, int):
            return "Argument 'start_line' must be an integer."
        end_line = args.get("end_line")
        if end_line is not None and not isinstance(end_line, int):
            return "Argument 'end_line' must be an integer."
            
    elif name == "write_file":
        if "path" not in args:
            return "Missing required argument 'path'."
        if not isinstance(args["path"], str):
            return "Argument 'path' must be a string."
        if "content" not in args:
            return "Missing required argument 'content'."
        if not isinstance(args["content"], str):
            return "Argument 'content' must be a string."
            
    elif name == "patch_file":
        if "path" not in args:
            return "Missing required argument 'path'."
        if not isinstance(args["path"], str):
            return "Argument 'path' must be a string."
        if "search_content" not in args:
            return "Missing required argument 'search_content'."
        if not isinstance(args["search_content"], str):
            return "Argument 'search_content' must be a string."
        if "replacement_content" not in args:
            return "Missing required argument 'replacement_content'."
        if not isinstance(args["replacement_content"], str):
            return "Argument 'replacement_content' must be a string."
            
    elif name == "run_command":
        if "command" not in args:
            return "Missing required argument 'command'."
        if not isinstance(args["command"], str):
            return "Argument 'command' must be a string."
            
    elif name == "git_commit":
        if "message" not in args:
            return "Missing required argument 'message'."
        if not isinstance(args["message"], str):
            return "Argument 'message' must be a string."
            
    elif name == "git_status":
        pass
        
    else:
        return f"Unknown tool: '{name}'"
        
    return None

class UltronAgent:
    def __init__(
        self,
        workspace_root: str,
        model_name: str = "qwen2.5-coder:7b",
        auto_approve: bool = False,
        auto_commit: bool = False
    ):
        self.workspace_root = os.path.abspath(workspace_root)
        self.model = OllamaModel(model_name)
        self.tools = ToolManager(workspace_root)
        self.context = ContextManager(workspace_root)

        # Provider registry — manages all cloud/local providers
        self.provider_registry = ProviderRegistry()

        # Task lifecycle
        self.task_router = TaskRouter()
        self.current_task: Optional[Task] = None
        self.budget_enforcer = BudgetEnforcer()

        # Tool registry + policy engine
        self.tool_registry = ToolRegistry.build_default()
        self.policy_engine = PolicyEngine(auto_approve=auto_approve)

        # Change tracker (unified scope tracking)
        self.change_tracker = ChangeTracker(workspace_root)

        # Metrics + decision log (auto-record at end of every task)
        self.metrics_collector = MetricsCollector(workspace_root)
        self.decision_log = DecisionLog(workspace_root)

        # Convention context (injected for FEATURE/REFACTOR tasks)
        self._convention_context: str = ""

        # Stop flag — set by REPL on Ctrl+C to cleanly abort the thinking loop
        self._stop_requested: bool = False

        # Session logger — persistent JSONL audit trail
        self.session_log = SessionLogger(workspace_root)

        # Load plugins
        try:
            from ultron.plugin_loader import register_all_plugins
            register_all_plugins(self.tool_registry, self.provider_registry)
        except Exception:
            pass
        self.console = Console()
        
        self.auto_approve = auto_approve
        self.auto_commit = auto_commit
        
        self.checkpoint = CheckpointManager(workspace_root)
        self.pre_dirty_files = set()
        self.memory_manager = ProjectMemoryManager(workspace_root)
        self.project_memory = self.memory_manager.load_memory()
        self.repo_map = RepoMap(workspace_root)

        # P0.1: Single tool execution pipeline (after repo_map is ready)
        from ultron.tool_executor import ToolExecutor
        from ultron.scope_manager import ScopeManager
        from ultron.audit import AuditLogger
        self.scope_manager = ScopeManager(workspace_root, self.repo_map)
        self.audit_logger = AuditLogger(workspace_root)
        self.tool_executor = ToolExecutor(
            workspace_root=workspace_root,
            tool_registry=self.tool_registry,
            policy_engine=self.policy_engine,
            scope_manager=self.scope_manager,
            audit_logger=self.audit_logger,
            tools=self.tools,
            checkpoint_manager=self.checkpoint,
            change_tracker=self.change_tracker,
            console=self.console,
        )

        # P1/P2: EventBus, ModelRouter, ProjectProfile, HealthMonitor
        self.bus = get_bus()
        self.model_router = ModelRouter()
        self.project_profile = ProjectProfile(workspace_root, self.repo_map, self.project_memory)
        self.health_tracker = get_health_tracker()

        # P3/P4: Task replay, notifications
        self.task_replay = TaskReplay(workspace_root)
        self.notification_manager = NotificationManager(self.console)
        self._current_replay: object = None
        
        self.last_plan_task = None
        self.task_commands = []

        # Phase 3: intent mode (default = build to preserve backwards compatibility)
        self.intent_mode: str = "build"

        # Phase 3: change contract manager (reads .ultron.toml [contracts] if present)
        toml_config = self._load_toml_config()
        self.contract = ChangeContractManager(workspace_root, toml_config=toml_config)

        # Phase 3: refactor guard
        self.refactor_guard = RefactorGuard(self.repo_map)

        # Phase 3: evidence log for task summary
        self._task_evidence: List[Dict[str, Any]] = []

        # Convention auto-inject context (set per run, empty when no conventions found)
        self._convention_context: str = ""
        
        # Message memory
        self.messages: List[Dict[str, Any]] = []
        
        # System instructions
        self.system_prompt_base = (
            "You are Ultron, a highly advanced terminal-based AI coding assistant.\n"
            "Your task is to help the user design, write, test, and debug code.\n"
            "You have access to files, directories, terminal shell execution, and git.\n\n"
            "Guidelines:\n"
            "1. When modifying existing files, prefer 'patch_file' over 'write_file' if the file is large, as it is much faster and more target-specific.\n"
            "2. Always verify code correcteness by executing build or test scripts using 'run_command' (e.g. running unit tests, linters, or compiling).\n"
            "3. If a command fails or compiler prints errors, analyze the output and autonomously try to fix it.\n"
            "4. Do not explain things excessively. Keep your replies concise and focused on the code changes.\n"
            "5. After successfully editing code, you can use 'git_commit' to save changes."
        )

    def _build_convention_context(self, user_prompt: str) -> str:
        """Helper to build convention context for FEATURE and REFACTOR tasks."""
        if not self.current_task or self.current_task.intent not in (TaskIntent.FEATURE, TaskIntent.REFACTOR):
            return ""
        if not self.repo_map or not self.repo_map.index:
            return ""
        try:
            from ultron.analyzer import ConventionFinder
            finder = ConventionFinder(self.workspace_root, self.repo_map)
            conventions = finder.get_project_conventions(user_prompt)
            similar = conventions.get("similar_files", [])[:3]
            if similar:
                lines = [
                    "=== CONVENTION CONTEXT ===",
                    "Before writing new code, study these similar existing files:",
                ]
                for sf in similar:
                    lines.append(f"- {sf}")
                lines.append("")
                lines.append(
                    "Follow their naming, folder layout, import style, "
                    "error handling, and test patterns."
                )
                lines.append("==========================")
                return "\n".join(lines)
        except Exception:
            pass
        return ""

    def get_trimmed_history_messages(self, max_turns: int = 12) -> List[Dict[str, Any]]:
        """
        Groups messages into complete turns (user query -> assistant content/tool calls -> tool replies).
        Keeps only the last `max_turns` complete turns.
        Generates an AI-summarized block of the older turns (max 2,000 chars) if history is trimmed.
        """
        if not self.messages:
            return []
            
        # Group messages by user turns
        turns = []
        current_turn = []
        
        for msg in self.messages:
            if msg["role"] == "user":
                if current_turn:
                    turns.append(current_turn)
                current_turn = [msg]
            else:
                current_turn.append(msg)
        if current_turn:
            turns.append(current_turn)
            
        if len(turns) <= max_turns:
            return self.messages
            
        # Slipped turns to trim
        trimmed_turns = turns[:-max_turns]
        active_turns = turns[-max_turns:]
        
        # Convert active turns back to flat message list
        flat_active = []
        for turn in active_turns:
            flat_active.extend(turn)
            
        # Summarize trimmed turns to fit under 2,000 characters
        summary_prompt = (
            "You are a memory compressor. Summarize the following past conversation turns between a user "
            "and a coding assistant. Highlight files created or modified, commands run, and final outcomes. "
            "Keep the summary dense, clear, and strictly under 2,000 characters.\n\n"
        )
        for i, turn in enumerate(trimmed_turns, 1):
            summary_prompt += f"\n[Turn {i}]:\n"
            for msg in turn:
                role = msg["role"]
                content = msg.get("content", "")
                if role == "user":
                    summary_prompt += f"User: {content}\n"
                elif role == "assistant":
                    summary_prompt += f"Assistant: {content}\n"
                    if msg.get("tool_calls"):
                        summary_prompt += f"Assistant Tool Calls: {json.dumps(msg['tool_calls'])}\n"
                elif role == "tool":
                    summary_prompt += f"Tool Output ({msg.get('name')}): {content[:100]}...\n"
                    
        # Get summary from Ollama (non-streaming, simple call to prevent looping)
        summary = ""
        try:
            payload = [
                {"role": "system", "content": "You are a concise summarizer."},
                {"role": "user", "content": summary_prompt}
            ]
            response = self.model.chat(payload, stream=False)
            summary = response.get("content", "").strip()
        except Exception:
            summary = "Earlier conversation turns were trimmed to conserve context size."
            
        # Truncate summary strictly to 2000 chars if LLM misbehaved
        if len(summary) > 2000:
            summary = summary[:1990] + "..."
            
        summary_msg = {
            "role": "system",
            "content": f"=== SUMMARY OF EARLIER WORK (CONTEXT) ===\n{summary}\n=========================================="
        }
        
        return [summary_msg] + flat_active

    def _load_toml_config(self) -> Dict[str, Any]:
        """Parse .ultron.toml into a dict for contract manager config. Returns {} on error."""
        toml_path = os.path.join(self.workspace_root, ".ultron.toml")
        if not os.path.isfile(toml_path):
            return {}
        try:
            import re as _re
            config: Dict[str, Any] = {}
            current_section = None
            with open(toml_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    sec = _re.match(r'^\[([\w.]+)\]$', line)
                    if sec:
                        current_section = sec.group(1)
                        if current_section not in config:
                            config[current_section] = {}
                        continue
                    kv = _re.match(r'^(\w+)\s*=\s*(.+)$', line)
                    if kv and current_section:
                        key, val = kv.group(1), kv.group(2).strip()
                        # Parse int / str
                        try:
                            config[current_section][key] = int(val)
                        except ValueError:
                            config[current_section][key] = val.strip('"\'')
            return config
        except Exception:
            return {}

    def _enforce_intent_mode(self, tool_name: str) -> Optional[str]:
        """
        Returns a refusal string if tool_name is forbidden in the current
        intent_mode, or None if the tool is allowed.
        Called at the top of _execute_tool_with_confirmation before dispatch.
        Also called from repl.py for REPL-layer enforcement.
        """
        blocked = _MODE_BLOCKED_TOOLS.get(self.intent_mode, set())
        if tool_name in blocked:
            mode_label = self.intent_mode.upper()
            return (
                f"[{mode_label} mode] Tool '{tool_name}' is not allowed in {self.intent_mode} mode. "
                f"Switch to build mode with /mode build to enable this action."
            )
        return None

    def _handle_clarification_gate(self, response_message: Dict[str, Any]) -> Optional[str]:
        """
        Structured clarification gate (Phase 3).

        If the model's response contains needs_clarification=True:
          - Always pause and show the question.
          - Discard any tool_calls (log a warning — this is invalid structured output).
          - Return the question string so the REPL can pause.

        Fallback for models without structured output:
          - If the text response has no tool_calls AND the response ends with a '?' sentence,
            return the last sentence as the question.

        Returns None if no clarification is needed.
        """
        # Structured output path: model embeds JSON with needs_clarification
        content = response_message.get("content", "") or ""
        try:
            # Try to find JSON block in content
            json_match = re.search(r'\{[^{}]*"needs_clarification"[^{}]*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                if data.get("needs_clarification") is True:
                    question = str(data.get("question", "")).strip()
                    # Discard tool calls — clarification + tools = invalid
                    if response_message.get("tool_calls"):
                        self.console.print(
                            "[yellow]Warning: Model returned clarification + tool calls — "
                            "tool calls discarded per Phase 3 gate rules.[/yellow]"
                        )
                        response_message["tool_calls"] = []
                    if question:
                        return question
        except Exception:
            pass

        # Fallback: no structured output, no tool calls, last sentence ends with '?'
        if not response_message.get("tool_calls") and content.strip():
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', content.strip()) if s.strip()]
            if sentences and sentences[-1].endswith("?"):
                return sentences[-1]

        return None

    def _get_system_message(self) -> Dict[str, Any]:
        """Compile base instructions, project instructions, active file contexts, and intent mode into the system message."""
        context_content = self.context.build_context_prompt()
        project_instructions = load_project_instructions(self.workspace_root)
        full_prompt = self.system_prompt_base

        # Inject intent mode description
        mode_descriptions = {
            "ask":    "CURRENT MODE: ASK — you may only read files and explain. Do NOT call write_file, patch_file, git_commit, or run_command.",
            "plan":   "CURRENT MODE: PLAN — you may read files and produce plans. Do NOT call write_file, patch_file, git_commit, or run_command.",
            "build":  "CURRENT MODE: BUILD — full editing capabilities enabled with approval policy.",
            "fix":    "CURRENT MODE: FIX — focused repair mode. Use a repair plan and change contract before editing.",
            "review": "CURRENT MODE: REVIEW — inspect source and diffs only. Do NOT call write_file or patch_file.",
        }
        mode_desc = mode_descriptions.get(self.intent_mode, "")
        if mode_desc:
            full_prompt += f"\n\n{mode_desc}"

        if project_instructions:
            full_prompt += f"\n\n{project_instructions}"
        # Inject task lifecycle hint
        if self.current_task:
            full_prompt += self.task_router.get_system_hint(self.current_task)
        # Inject convention context for feature/refactor tasks
        if self._convention_context:
            full_prompt += f"\n\n{self._convention_context}"
        full_prompt += f"\n\n{context_content}"

        # Context window awareness — warn if approaching provider limit
        try:
            caps = self.model.capabilities()
            char_estimate = len(full_prompt)
            token_estimate = char_estimate // 4
            if caps.context_window > 0:
                usage_ratio = token_estimate / caps.context_window
                if usage_ratio > 0.9:
                    self.console.print(
                        f"[bold red]⚠ Context at ~{usage_ratio*100:.0f}% of {caps.context_window:,} token limit. "
                        f"Consider /clear or dropping pinned files.[/bold red]"
                    )
                elif usage_ratio > 0.75:
                    self.console.print(
                        f"[yellow]Context at ~{usage_ratio*100:.0f}% of {caps.context_window:,} token limit.[/yellow]"
                    )
        except Exception:
            pass

        return {"role": "system", "content": full_prompt}

    def get_status_dashboard(self) -> Panel:
        """Compiles active status dashboard parameters into a beautiful rich Panel."""
        from rich.table import Table
        
        # Git Info
        branch, modified = self.tools.get_git_info()
        git_str = f"[cyan]{branch}[/cyan] ({modified} modified files)" if branch else "[yellow]Not a Git repository[/yellow]"
        
        # Project Type
        proj_type = self.tools.detect_project_type()
        
        # Context usage
        pinned_count = len(self.context.pinned_files)
        pinned_chars = 0
        for f in self.context.pinned_files:
            abs_p = os.path.join(self.workspace_root, f)
            if os.path.isfile(abs_p):
                try:
                    pinned_chars += os.path.getsize(abs_p)
                except Exception:
                    pass
                    
        tree_text = self.context.get_workspace_tree()
        tree_lines = len(tree_text.splitlines()) if tree_text else 0
        
        table = Table(box=None, show_header=False)
        table.add_row("[bold white]Workspace Path:[/bold white]", self.workspace_root)
        table.add_row("[bold white]Project Type:[/bold white]", proj_type)
        table.add_row("[bold white]Git Status:[/bold white]", git_str)
        table.add_row("[bold white]Active Model:[/bold white]", f"{self.model.model_name} (temp={self.model.temperature})")
        table.add_row(
            "[bold white]Context Pinned:[/bold white]", 
            f"{pinned_count} files ({pinned_chars:,} chars), Tree: {tree_lines} lines"
        )
        
        return Panel(
            table, 
            title="[bold magenta]Ultron Workspace Status[/bold magenta]", 
            border_style="magenta",
            expand=False
        )

    def clear_memory(self):
        """Reset the conversation memory."""
        self.messages.clear()
        self.console.print("[yellow]Agent memory cleared.[/yellow]")

    def run(self, user_prompt: str):
        """Runs the agentic loop to address the user's request."""
        self.checkpoint.start_task()
        self.pre_dirty_files.clear()
        self.task_commands.clear()
        self.change_tracker.reset()
        self.change_tracker.snapshot_git_state()
        self._stop_requested = False

        # Create and register task
        _budget = getattr(self, "max_iterations", 12)
        self.current_task = self.task_router.create_task(
            prompt=user_prompt,
            current_mode=self.intent_mode,
            max_tool_calls=_budget,
            time_budget_seconds=300.0,
        )
        self.current_task.status = TaskStatus.INSPECTING

        # Log task start
        self.session_log.log_task_start(
            self.current_task.id,
            self.current_task.intent.value,
            user_prompt,
        )
        # Update ToolExecutor with current task/transaction IDs
        self.tool_executor.task_id = self.current_task.id
        self.tool_executor.transaction_id = self.checkpoint._transaction_id
        self.scope_manager.set_initial_scope(
            self.current_task.expected_files or [],
            task_id=self.current_task.id,
        )

        # P4: Start task replay recording
        self._current_replay = self.task_replay.start_recording(
            task_id=self.current_task.id,
            prompt=user_prompt,
            intent=self.current_task.intent.value,
            model=getattr(self.model, "model_name", "unknown"),
            provider=getattr(self.model, "provider_name", "Ollama"),
        )

        # Build convention context once for FEATURE/REFACTOR tasks
        self._convention_context = self._build_convention_context(user_prompt)
        self._task_evidence.clear()

        # Phase 3: auto-run refactor safety check if refactor-intent keywords detected
        prompt_lower = user_prompt.lower()
        if any(kw in prompt_lower for kw in _REFACTOR_KEYWORDS):
            # We'll run the guard after repo map is ready; flag for post-task check
            self._refactor_intent = True
        else:
            self._refactor_intent = False
        
        # 1. Initialize messages if empty, or prepend system message dynamically
        trimmed_history = self.get_trimmed_history_messages()
        active_messages = [self._get_system_message()] + trimmed_history
        active_messages.append({"role": "user", "content": user_prompt})
        
        # Append user message to persistent history
        self.messages.append({"role": "user", "content": user_prompt})

        max_iterations = self.current_task.max_tool_calls
        iteration = 0

        while iteration < max_iterations:
            # Check budget BEFORE calling model
            if self.current_task:
                budget_check = self.budget_enforcer.check(self.current_task)
                if budget_check["action"] == "stop":
                    self.console.print(f"\n[bold red]Task stopped: {budget_check['reason']}[/bold red]")
                    self.current_task.status = TaskStatus.BLOCKED
                    break
                elif budget_check["action"] == "warn":
                    self.console.print(f"\n[yellow]⚠ Budget warning: {budget_check['reason']}[/yellow]")

            iteration += 1
            if self.current_task:
                self.current_task.tool_call_count = iteration

            # Honour Ctrl+C stop request
            if self._stop_requested:
                self.console.print("[bold red]Task aborted by user.[/bold red]")
                if self.current_task:
                    self.current_task.status = TaskStatus.CANCELLED
                break

            # We will use streaming for LLM text responses, and catch tool calls at the end
            self.console.print()  # Spacer
            
            # Streaming status spinner
            accumulated_content = ""
            tool_calls = []
            
            self.console.print("[cyan]Ultron is thinking...[/cyan]")
            
            try:
                # Use a generator to print content incrementally
                # Try active provider; fall back to fallback provider on connection error
                try:
                    chat_generator = self.model.chat(active_messages, stream=True)
                except Exception as provider_err:
                    # Try provider fallback
                    fallback = self.provider_registry._fallback
                    if fallback and fallback != self.model:
                        self.console.print(f"[yellow]Provider error: {provider_err}. Trying fallback...[/yellow]")
                        self.session_log.log_provider_event("fallback", str(getattr(self.model, "provider_name", "unknown")), str(provider_err))
                        self.model = fallback
                        chat_generator = self.model.chat(active_messages, stream=True)
                    else:
                        raise
                response_message = None
                
                # Buffering variables to avoid streaming raw JSON output
                is_buffering = True
                buffered_text = ""
                
                while True:
                    try:
                        chunk = next(chat_generator)
                        if chunk["type"] == "content":
                            delta = chunk["delta"]
                            accumulated_content += delta
                            
                            if is_buffering:
                                buffered_text += delta
                                # If buffer doesn't start with JSON structure markers, flush it
                                strip_buf = buffered_text.strip()
                                if strip_buf and not (strip_buf.startswith("{") or strip_buf.startswith("`")):
                                    is_buffering = False
                                    self.console.print(buffered_text, end="")
                                    sys.stdout.flush()
                                    buffered_text = ""
                            else:
                                self.console.print(delta, end="")
                                sys.stdout.flush()
                                
                        elif chunk["type"] == "tool_calls":
                            tool_calls = chunk["tool_calls"]
                    except StopIteration as e:
                        response_message = e.value
                        break
                
                # If we finished buffering but never flushed, check if it's a fallback tool call
                if is_buffering and buffered_text:
                    fallback = parse_fallback_tool_calls(buffered_text)
                    if fallback:
                        tool_calls = fallback
                        if response_message:
                            response_message["tool_calls"] = fallback
                            response_message["content"] = ""
                    else:
                        self.console.print(buffered_text, end="")
                        sys.stdout.flush()
                        
            except Exception as e:
                self.console.print(f"\n[red]Error connecting to Ollama: {str(e)}[/red]")
                # P1: Track provider health
                pname = getattr(self.model, "provider_name", "unknown")
                mname = getattr(self.model, "model_name", "unknown")
                self.health_tracker.record_call(pname, mname, 0.0, timed_out=True)
                self.bus.publish(BusEvent.MODEL_ERROR, {"provider": pname, "model": mname, "error": str(e)})
                break
                
            # If Ollama sent tool calls (or we parsed fallback ones), execute them
            if response_message and response_message.get("tool_calls"):
                # Append assistant tool-call request to messages
                active_messages.append(response_message)
                self.messages.append(response_message)
                
                tool_results = []
                
                for tool_call in response_message["tool_calls"]:
                    fn_name = tool_call["function"]["name"]
                    fn_args = tool_call["function"]["arguments"]
                    call_id = tool_call.get("id", "call_id")
                    
                    self.console.print(f"\n[bold yellow][Tool Call Request]:[/bold yellow] [green]{fn_name}[/green]")
                    
                    # Inspect and handle each tool
                    try:
                        validation_error = validate_tool_args(fn_name, fn_args)
                        if validation_error:
                            self.console.print(f"[red]Invalid tool call arguments: {validation_error}[/red]")
                            result = f"Error: Invalid tool arguments: {validation_error}"
                        else:
                            result = self._execute_tool_with_confirmation(fn_name, fn_args)
                        # Format as tool response message
                        tool_results.append({
                            "role": "tool",
                            "name": fn_name,
                            "content": result,
                            "tool_call_id": call_id
                        })
                    except Exception as e:
                        self.console.print(f"[red]Failed to execute tool {fn_name}: {str(e)}[/red]")
                        tool_results.append({
                            "role": "tool",
                            "name": fn_name,
                            "content": f"Error executing tool: {str(e)}",
                            "tool_call_id": call_id
                        })
                        
                # Append results to both active list and history
                for tr in tool_results:
                    active_messages.append(tr)
                    self.messages.append(tr)
                    
                # Continue loop to allow model to observe tool results
                continue
                
            else:
                # No tools called, reasoning completed!
                if response_message and response_message.get("content"):
                    # Save assistant text to history
                    self.messages.append(response_message)
                break
                
        if iteration >= max_iterations:
            self.console.print("\n[yellow]Reached maximum task iterations limit.[/yellow]")
            if self.current_task:
                self.current_task.status = TaskStatus.BLOCKED

        # Save task checkpoint
        self.checkpoint.save_task_checkpoint()

        # Finalize task status
        if self.current_task and self.current_task.status not in (TaskStatus.BLOCKED, TaskStatus.CANCELLED):
            has_unverified = self.current_task.has_unverified()
            self.current_task.status = TaskStatus.VERIFIED if not has_unverified else TaskStatus.TESTING

        # Auto-record metrics
        if self.current_task:
            import time as _time
            metrics = TaskMetrics(
                task_id=self.current_task.id,
                prompt=user_prompt[:200],
                intent=self.current_task.intent.value,
                success=self.current_task.status == TaskStatus.VERIFIED,
                files_changed=list(self.checkpoint.current_task_files.keys()),
                commands_run=[tc["command"] for tc in self.task_commands],
                tool_call_count=self.current_task.tool_call_count,
                duration_seconds=self.current_task.elapsed(),
                had_unverified=self.current_task.has_unverified(),
            )
            try:
                self.metrics_collector.record(metrics)
            except Exception:
                pass

        # Auto-record decision log
        changed_files = list(self.checkpoint.current_task_files.keys())

        # Log task end to session log
        if self.current_task:
            self.session_log.log_task_end(
                self.current_task.id,
                self.current_task.status.value,
                changed_files,
                self.current_task.elapsed(),
            )

        # P4: Finalize task replay
        if self._current_replay and self.current_task:
            self.task_replay.finalize(
                self._current_replay,
                status=self.current_task.status.value,
                files_changed=changed_files,
                evidence=[str(e) for e in self._task_evidence],
            )
        if changed_files or self.task_commands:
            try:
                self.decision_log.record(
                    task_description=user_prompt[:300],
                    plan=getattr(self, "last_plan_task", "") or "",
                    files_changed=changed_files,
                    commands_run=[tc["command"] for tc in self.task_commands],
                    evidence=[str(e) for e in self._task_evidence[:10]],
                )
            except Exception:
                pass
        
        # Git task-level commit prompt
        changed_files = list(self.checkpoint.current_task_files.keys())

        # Phase 3: post-task refactor safety and test-gap detection
        if changed_files:
            try:
                if getattr(self, "_refactor_intent", False) and self.repo_map.index:
                    report = self.refactor_guard.check_refactor_safety(changed_files)
                    risk_color = {"low": "green", "medium": "yellow", "high": "red"}.get(report.risk_level, "white")
                    self.console.print(
                        f"\n[bold white]Refactor Safety:[/bold white] "
                        f"[{risk_color}]{report.risk_level.upper()}[/{risk_color}]"
                    )
                    for w in report.warnings:
                        self.console.print(f"  [yellow]⚠ {w}[/yellow]")

                gaps = self.refactor_guard.detect_test_gaps(changed_files)
                if gaps:
                    self.console.print("\n[bold yellow]Test Coverage Gaps:[/bold yellow]")
                    for g in gaps:
                        self.console.print(f"  [yellow]⚠ No test file found for: {g}[/yellow]")
            except Exception:
                pass
        
        # Print Smart Work Summary with evidence tags (Phase 3)
        if changed_files or self.task_commands:
            self.console.print("\n" + "="*80)
            self.console.print("[bold green]          ULTRON TASK COMPLETION WORK SUMMARY[/bold green]")
            self.console.print("="*80)
            
            if changed_files:
                self.console.print("\n[bold white]Files Modified:[/bold white]")
                for f in changed_files:
                    self.console.print(f"  * {f}  [dim]({EvidenceTag.OBSERVED})[/dim]")
            else:
                self.console.print("\n[bold white]Files Modified:[/bold white] None")
                
            if self.task_commands:
                self.console.print("\n[bold white]Subprocesses Run:[/bold white]")
                for tc in self.task_commands:
                    cmd_str = tc["command"]
                    res_str = tc["result"]
                    exit_code = "Unknown"
                    ev_tag = EvidenceTag.INFERRED
                    for line in res_str.splitlines():
                        if "exited with code" in line or "Command exited with code" in line:
                            exit_code = line.strip()
                            ev_tag = EvidenceTag.VERIFIED if "code 0" in line else EvidenceTag.OBSERVED
                            break
                    self.console.print(f"  * `{cmd_str}` -> {exit_code}  [dim]({ev_tag})[/dim]")
            else:
                self.console.print("\n[bold white]Subprocesses Run:[/bold white] None")
                
            # Warnings / Staging Lockouts
            dirty_in_task = [f for f in changed_files if f in self.pre_dirty_files]
            if dirty_in_task:
                self.console.print("\n[bold yellow]Warnings / Staging Lockouts:[/bold yellow]")
                self.console.print("  - Task auto-commits are disabled for files that were dirty at task start:")
                for f in dirty_in_task:
                    self.console.print(f"    * {f}")

            # Evidence-based completion status
            has_unverified = any(
                ev.get("tag") == EvidenceTag.NOT_VERIFIED
                for ev in self._task_evidence
            )
            if has_unverified:
                self.console.print("\n[yellow]⚠ Task partially verified[/yellow] (some conclusions lack command evidence)")
            elif changed_files or self.task_commands:
                self.console.print("\n[green]✓ Task complete[/green]")
                    
            self.console.print("="*80 + "\n")
            
        if changed_files:
            dirty_in_task = [f for f in changed_files if f in self.pre_dirty_files]
            if dirty_in_task:
                self.console.print("[yellow]Warning: Task auto-commits are disabled for files that were dirty at task start.[/yellow]")
                self.console.print("[yellow]Please commit/stash/revert your changes manually.[/yellow]")
            else:
                if not self.auto_approve:
                    # Prompt task-level commit
                    do_commit = Confirm.ask("[bold yellow]Create task-level Git commit for these changes?[/bold yellow]")
                    if do_commit:
                        # Let's ask Ollama for commit message dynamically based on diff
                        self.console.print("[cyan]Generating commit message...[/cyan]")
                        diff_text = ""
                        for f in changed_files:
                            diff_text += self.tools.run_command(f"git diff -- {f}")
                        
                        prompt = (
                            f"Generate a conventional git commit message for changes in files: {', '.join(changed_files)}.\n"
                            f"Use title and optional brief description. Respond with ONLY the commit message text. Do not wrap in markdown quotes or backticks.\n\n"
                            f"Diff:\n{diff_text}"
                        )
                        
                        commit_message = ""
                        try:
                            chat_generator = self.model.chat([{"role": "user", "content": prompt}], stream=True)
                            while True:
                                try:
                                    chunk = next(chat_generator)
                                    if chunk["type"] == "content":
                                        commit_message += chunk["delta"]
                                except StopIteration:
                                    break
                        except Exception:
                            commit_message = f"ultron: update {', '.join(changed_files)}"
                            
                        commit_message = commit_message.strip() or f"ultron: update {', '.join(changed_files)}"
                        
                        self.console.print(Panel(commit_message, title="[bold green]AI Commit Message[/bold green]", border_style="green", expand=False))
                        
                        res = self.tools.git_commit(commit_message, files=changed_files)
                        self.console.print(f"[green]*[/green] {res}")
                else:
                    if self.auto_commit:
                        commit_msg = f"ultron: auto-commit changes to {', '.join(changed_files)}"
                        res = self.tools.git_commit(commit_msg, files=changed_files)
                        self.console.print(f"[dim]Git committed changes automatically: '{commit_msg}'[/dim]")

    def _execute_tool_with_confirmation(self, name: str, args: Dict[str, Any]) -> str:
        """Executes a tool, prompting the user for approval if it is a mutating command."""

        # Phase 3: enforce intent mode FIRST
        mode_refusal = self._enforce_intent_mode(name)
        if mode_refusal:
            self.console.print(f"\n[bold red][Mode Block][/bold red] {mode_refusal}")
            return f"Error: {mode_refusal}"

        # Read-only tools can be executed silently (but displayed in console)
        read_only_tools = ["list_dir", "view_file", "grep_search", "git_status"]
        
        # Determine target file contents for diffing
        if name in ["write_file", "patch_file"]:
            path = args.get("path")
            new_content = args.get("content", "")
            
            # Phase 3: pre-write contract scope check (BEFORE any checkpoint or write)
            contract_decision = self.contract.check_before_write(path)
            if contract_decision == "block":
                active = self.contract.load_active()
                if active is not None:
                    msg = (
                        f"File '{path}' is not in the change contract and the "
                        f"max_files limit ({self.contract.max_files}) has been reached. "
                        f"Cannot add more files to this task."
                    )
                else:
                    msg = f"File '{path}' write blocked by contract policy."
                self.console.print(f"\n[bold red][Contract Block][/bold red] {msg}")
                return f"Error: {msg}"
            elif contract_decision == "ask":
                active = self.contract.load_active()
                reason_hint = f"writing '{path}' (not listed in change contract)"
                self.console.print(
                    f"\n[bold yellow][Contract][/bold yellow] Unplanned file: [cyan]{path}[/cyan]"
                )
                approved = Confirm.ask(
                    f"[bold yellow]This file was not in the change contract. Allow writing '{path}'?[/bold yellow]"
                )
                if not approved:
                    self.console.print("[red]Unplanned file write rejected by user.[/red]")
                    return f"Error: User rejected unplanned write to '{path}'."
                self.contract.approve_unplanned_file(path)

            # Phase 3: legacy multi-file plan policy guard (only when NO active contract)
            if self.contract.load_active() is None:
                already_modified = [f for f in self.checkpoint.current_task_files.keys() if f != path]
                if already_modified and not getattr(self, "last_plan_task", None):
                    self.console.print(f"\n[bold red]Error: Multi-file edit targeting '{path}' blocked.[/bold red]")
                    self.console.print("[yellow]For complex/multi-file tasks, an implementation plan must first be created via '/plan <task>'.[/yellow]")
                    return "Error: Multi-file edits require a formulated implementation plan. Aborting tool call. Please tell the user to run `/plan` first."

            # Pre-existing dirty changes check
            is_dirty = self.tools.is_file_dirty(path)
            if is_dirty:
                self.console.print(f"\n[bold red]Warning: File '{path}' has pre-existing uncommitted changes.[/bold red]")
                self.console.print("[yellow]Ultron must never silently include pre-existing dirty changes in its task commit.[/yellow]")
                
                # Ask user if they want to proceed editing
                confirm_edit = Confirm.ask(f"[bold yellow]Do you approve Ultron editing '{path}' despite pre-existing changes?[/bold yellow]")
                if not confirm_edit:
                    return f"Error: Edit refused by user because '{path}' has pre-existing uncommitted changes. Tell the user to commit/stash/revert them first."
                    
                # Track as pre-dirty to disable auto commits
                self.pre_dirty_files.add(path)

            # Record checkpoint backup BEFORE mutation
            try:
                self.checkpoint.record_before_edit(path)
            except Exception as e:
                self.console.print(f"[red]Warning: Failed to create checkpoint backup for {path}: {str(e)}[/red]")
            
            # Load old content
            old_content = ""
            abs_path = os.path.abspath(os.path.join(self.workspace_root, path))
            if os.path.isfile(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                except Exception:
                    pass
            
            # Apply patching locally to show the potential result
            if name == "patch_file":
                search = args.get("search_content", "")
                replace = args.get("replacement_content", "")
                if old_content.count(search) == 1:
                    new_content = old_content.replace(search, replace, 1)
                else:
                    # In case of issue, show failure early
                    new_content = f"<Error: search block count is {old_content.count(search)}>"
            
            # Show beautiful diff
            self.console.print(generate_diff_panel(path, old_content, new_content))
            
            # Ask confirmation
            if not self.auto_approve:
                approved = Confirm.ask(f"[bold yellow]Apply these changes to {path}?[/bold yellow]")
                if not approved:
                    self.console.print("[red]Changes skipped by user.[/red]")
                    return "Error: User rejected code changes."
                    
            # Execute change
            res = ""
            if name == "write_file":
                res = self.tools.write_file(path, args.get("content"))
            else:
                res = self.tools.patch_file(path, args.get("search_content"), args.get("replacement_content"))
                
            self.console.print(f"[green]*[/green] {res}")
            
            # Phase 3: record completed touch in contract
            if not res.startswith("Error"):
                self.contract.record_completed_touch(path)
                # Evidence tag
                self._task_evidence.append({"item": path, "tag": EvidenceTag.OBSERVED})

            # Record after edit hash for undo verification
            if not res.startswith("Error"):
                self.checkpoint.record_after_edit(path)
                # Update change tracker
                self.change_tracker.record_after(path)
                if self.current_task:
                    self.current_task.status = TaskStatus.EDITING
                    if path not in self.current_task.actual_files:
                        self.current_task.actual_files.append(path)
                    self.current_task.add_evidence(
                        EvidenceKind.OBSERVED,
                        f"Modified {path}",
                        source=path
                    )
                # Invalidate repo map cache for this file
                norm = path.replace(os.sep, "/")
                if norm in self.repo_map.index:
                    del self.repo_map.index[norm]
            
            # If auto-commit is enabled and tool succeeded, commit changes
            if self.auto_commit and not res.startswith("Error"):
                if path in self.pre_dirty_files:
                    self.console.print(f"[yellow]Skipping auto-commit for '{path}' because it had pre-existing dirty changes.[/yellow]")
                else:
                    commit_msg = f"ultron: auto-commit changes to {path}"
                    self.tools.git_commit(commit_msg, files=[path])
                    self.console.print(f"[dim]Git committed changes automatically: '{commit_msg}'[/dim]")
                
            return res
            
        elif name == "run_command":
            cmd = args.get("command")
            self.console.print(Panel(f"[bold white]{cmd}[/bold white]", title="Terminal Command", border_style="yellow"))
            
            # Check side effects
            from ultron.security import is_side_effect_command
            has_side_effect, cmd_type = is_side_effect_command(cmd)
            
            if has_side_effect:
                self.console.print(f"[bold yellow]Warning: Command targets a side-effect command ({cmd_type}).[/bold yellow]")
                raw = self.tools.execute_command_with_policy(
                    cmd, require_approval=True,
                    context=f"{cmd_type} command"
                )
            else:
                raw = self.tools.execute_command_with_policy(
                    cmd,
                    require_approval=not self.auto_approve,
                    context="model-requested command",
                )

            exit_code = raw.get("exit_code", -1)
            if raw.get("stderr") == "Declined by user." and exit_code == -1:
                self.console.print("[red]Command skipped by user.[/red]")
                return "Error: User rejected command execution."

            # Build legacy-compatible result string
            out_parts = []
            if raw.get("stdout"):
                out_parts.append(f"--- Stdout ---\n{raw['stdout']}")
            if raw.get("stderr"):
                out_parts.append(f"--- Stderr ---\n{raw['stderr']}")
            out_str = "\n".join(out_parts) if out_parts else "(No output)"
            res = f"Command exited with code {exit_code}\n{out_str}"

            ev_tag = EvidenceTag.VERIFIED if exit_code == 0 else EvidenceTag.OBSERVED
            self._task_evidence.append({"item": cmd, "tag": ev_tag})
            self.task_commands.append({"command": cmd, "result": res})
            # Update task evidence
            if self.current_task:
                kind = EvidenceKind.VERIFIED if exit_code == 0 else EvidenceKind.NOT_VERIFIED
                self.current_task.add_evidence(kind, f"Ran: {cmd}", source=cmd)
                if exit_code == 0 and self.current_task.status == TaskStatus.EDITING:
                    self.current_task.status = TaskStatus.TESTING
            self.console.print(res)
            # Log tool call
            self.session_log.log_tool_call(
                tool=name, args_summary=f"command={cmd}",
                result=res[:200], exit_code=exit_code, risk_level="workspace_write"
            )
            return res
            
        elif name == "git_commit":
            msg = args.get("message")
            self.console.print(f"Git commit request: [bold]{msg}[/bold]")
            if not self.auto_approve:
                approved = Confirm.ask(f"[bold yellow]Execute Git Commit?[/bold yellow]")
                if not approved:
                    self.console.print("[red]Commit skipped by user.[/red]")
                    return "Error: User rejected git commit."
            res = self.tools.git_commit(msg)
            self.console.print(f"[green]*[/green] {res}")
            return res
            
        else:
            # Read-only or status tools
            self.console.print(f"[dim]Auto-executing read-only tool: {name}({args})[/dim]")
            if name == "list_dir":
                return self.tools.list_dir(args.get("path", "."))
            elif name == "view_file":
                return self.tools.view_file(args.get("path"), args.get("start_line"), args.get("end_line"))
            elif name == "grep_search":
                return self.tools.grep_search(args.get("query"), args.get("path"))
            elif name == "git_status":
                return self.tools.git_status()
            else:
                return f"Error: Unknown tool {name}"
