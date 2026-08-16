import os
import sys
import re
import hashlib
import datetime
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm

from ultron.agent import UltronAgent, VALID_MODES
from ultron.onboard import ProjectMemoryManager
from ultron.analyzer import ImpactAnalyzer, FailureInvestigator, ConventionFinder, find_folders, create_ultron_md_template, create_ultron_toml_template
from ultron.git_workflow import WorktreeManager, PRSummaryGenerator, CommitQualityChecker, DecisionLog
from ultron.monorepo import MonorepoDetector, WorkspaceAliasManager
from ultron.delivery import FeaturePlanner, ScaffoldAuditor, DocsChecker, HandoffGenerator, EnvironmentDoctor, HealthAnalyzer, ReleaseChecker
from ultron.tracer import FeatureTracer, BranchComparer, FlakyTestDetector, TestOutputParser, VerificationPlanner

# Phase 3 REPL-layer: tools that are "mutation" commands for mode enforcement
_REPL_MUTATION_COMMANDS = {
    "/run":    "run_command",
    "/commit": "git_commit",
    "/test":   "run_command",
    "/lint":   "run_command",
    "/fix":    "run_command",
}

class UltronCompleter(Completer):
    def __init__(self, workspace_root: str, context_manager):
        self.workspace_root = workspace_root
        self.context = context_manager
        self.commands = [
            "/add", "/drop", "/files", "/run", "/diff", "/commit", "/undo",
            "/workspace", "/tree", "/refresh", "/status", "/logs", "/last-error",
            "/repeat", "/cancel", "/onboard", "/plan", "/tasks", "/test", "/lint",
            "/fix", "/clear", "/help", "/exit", "/quit",
            # Phase 2
            "/analyze", "/find-folder", "/open", "/find", "/symbol", "/references",
            "/flow", "/explain", "/impact", "/why", "/min-repro", "/init-project",
            # Phase 3
            "/mode", "/contract", "/verify", "/review", "/reproduce", "/bisect",
            # Phase 4
            "/worktree", "/pr-summary", "/commit-check", "/decisions",
            "/monorepo", "/recent", "/alias",
            "/feature", "/scaffold-audit", "/docs-check", "/handoff",
            "/doctor", "/health", "/release-check",
            # Model Hub
            "/models", "/model", "/model-info", "/provider",
            "/fallback",
            # Workstream E remaining
            "/trace", "/compare", "/flaky-test",
            # Metrics
            "/metrics",
            # Session + Plugins
            "/session-log", "/plugins", "/context-status",
            # P1/P2
            "/probe", "/route-info",
            # P3/P4
            "/self-repair", "/known-good", "/replay", "/notify-config", "/audit",
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        
        # Complete slash commands
        if text.startswith("/") and " " not in text:
            for cmd in self.commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))
            return
            
        # Complete file paths for /add and /drop
        for cmd in ["/add ", "/drop "]:
            if text.startswith(cmd):
                arg = text[len(cmd):]
                # Walk workspace to find files starting with `arg`
                for root, dirs, files in os.walk(self.workspace_root):
                    # Filter ignored directories in-place
                    dirs[:] = [d for d in dirs if not self.context._is_ignored(os.path.join(root, d))]
                    
                    for f in files:
                        full_path = os.path.join(root, f)
                        if self.context._is_ignored(full_path):
                            continue
                        rel_path = os.path.relpath(full_path, self.workspace_root).replace(os.sep, "/")
                        if rel_path.startswith(arg):
                            yield Completion(rel_path, start_position=-len(arg))
                return

class UltronREPL:
    def __init__(self, agent: UltronAgent):
        self.agent = agent
        self.console = Console()
        self.history_file = os.path.join(self.agent.workspace_root, ".ultron_history")
        self.session = PromptSession(
            history=FileHistory(self.history_file),
            completer=UltronCompleter(self.agent.workspace_root, self.agent.context)
        )
        
        # Style prompt_toolkit input bar
        self.prompt_style = Style.from_dict({
            'prompt': '#ff00ff bold',
            'command': '#00ffff',
        })
        self.last_user_prompt = None
        self.last_task_mutated = False
        self.memory_manager = ProjectMemoryManager(self.agent.workspace_root)

    def print_help(self):
        """Displays help commands in a beautiful Panel."""
        table = Table(box=None, show_header=False)
        table.add_row("[cyan]/add <file>[/cyan]", "Pin a file to the prompt context")
        table.add_row("[cyan]/drop <file>[/cyan]", "Remove a file from the prompt context")
        table.add_row("[cyan]/files[/cyan]", "List all pinned files in the active context")
        table.add_row("[cyan]/run <cmd>[/cyan]", "Execute a terminal shell command")
        table.add_row("[cyan]/diff[/cyan]", "Show unstaged git diffs")
        table.add_row("[cyan]/commit[/cyan]", "Autonomously stage and git-commit changes using AI message")
        table.add_row("[cyan]/undo[/cyan]", "Revert changes made by the latest Ultron task")
        table.add_row("[cyan]/workspace[/cyan]", "Show active workspace path and status dashboard")
        table.add_row("[cyan]/onboard[/cyan]", "Deterministic scan and AI summary of project framework & commands")
        table.add_row("[cyan]/tree[/cyan]", "Show the current directory tree")
        table.add_row("[cyan]/refresh[/cyan]", "Refresh the project cache and directory structure")
        table.add_row("[cyan]/status[/cyan]", "Display active workspace status metrics")
        table.add_row("[cyan]/logs [count][/cyan]", "View execution logs for recent terminal commands")
        table.add_row("[cyan]/last-error[/cyan]", "Show trace logs of the most recent execution failure")
        table.add_row("[cyan]/repeat[/cyan]", "Repeat the last natural language prompt")
        table.add_row("[cyan]/cancel[/cyan]", "Cancel the active thinking stream or background process")
        table.add_row("[cyan]/clear[/cyan]", "Reset agent memory")
        table.add_row("[cyan]/help[/cyan]", "Show this help guide")
        table.add_row("[cyan]/exit[/cyan] / [cyan]/quit[/cyan]", "Exit Ultron CLI")
        table.add_row("[bold white]── Phase 2 ──[/bold white]", "")
        table.add_row("[cyan]/analyze[/cyan]", "Build and display repository map summary")
        table.add_row("[cyan]/find-folder <name>[/cyan]", "Search for a folder by name in workspace")
        table.add_row("[cyan]/open <n or path>[/cyan]", "Switch workspace (with confirmation)")
        table.add_row("[cyan]/find <query>[/cyan]", "Text search across all indexed files")
        table.add_row("[cyan]/symbol <name>[/cyan]", "Find symbol definitions in codebase")
        table.add_row("[cyan]/references <symbol>[/cyan]", "Find all references to a symbol")
        table.add_row("[cyan]/flow <symbol>[/cyan]", "Trace symbol: definition → callers → tests")
        table.add_row("[cyan]/explain <file|symbol>[/cyan]", "AI explanation of a file or symbol")
        table.add_row("[cyan]/impact <file|symbol>[/cyan]", "Impact analysis before changing code")
        table.add_row("[cyan]/why[/cyan]", "Investigate latest failure with AI root cause")
        table.add_row("[cyan]/min-repro[/cyan]", "Generate minimal reproduction script")
        table.add_row("[cyan]/init-project[/cyan]", "Create ULTRON.md and .ultron.toml templates")
        table.add_row("[bold white]── Phase 3 (Modes) ──[/bold white]", "")
        table.add_row("[cyan]/mode <name>[/cyan]", "Set intent mode: ask | plan | build | fix | review")
        table.add_row("  [dim]ask[/dim]", "Read-only: explain and inspect only")
        table.add_row("  [dim]plan[/dim]", "Read-only: generate plans, no edits")
        table.add_row("  [dim]build[/dim]", "Full editing with approval (DEFAULT)")
        table.add_row("  [dim]fix[/dim]", "Focused repair with change contract")
        table.add_row("  [dim]review[/dim]", "Inspect diffs and source, no edits")
        table.add_row("[bold white]── Phase 3 (Actions) ──[/bold white]", "")
        table.add_row("[cyan]/contract[/cyan]", "Show the active change contract")
        table.add_row("[cyan]/verify [checks...][/cyan]", "Run verification checks (tests lint format_check typecheck)")
        table.add_row("[cyan]/review [target][/cyan]", "One-time code review with tier-classified findings")
        table.add_row("[cyan]/reproduce[/cyan]", "Save bug reproduction package to disk")
        table.add_row("[cyan]/bisect[/cyan]", "Guided git bisect session")
        table.add_row("[bold white]── Phase 4 ──[/bold white]", "")
        table.add_row("[cyan]/worktree [list|create|remove][/cyan]", "Manage Git worktrees for isolated work")
        table.add_row("[cyan]/pr-summary [base][/cyan]", "Generate PR summary (AI or template)")
        table.add_row("[cyan]/commit-check <msg>[/cyan]", "Check commit message quality")
        table.add_row("[cyan]/decisions [n][/cyan]", "View recent decision log entries")
        table.add_row("[cyan]/monorepo[/cyan]", "Detect packages/services in workspace")
        table.add_row("[cyan]/recent[/cyan]", "Show recently opened workspaces")
        table.add_row("[cyan]/alias [list|add|remove][/cyan]", "Manage workspace aliases")
        table.add_row("[cyan]/feature <desc>[/cyan]", "Generate vertical-slice feature plan")
        table.add_row("[cyan]/scaffold-audit[/cyan]", "Audit changed files for scaffolding gaps")
        table.add_row("[cyan]/docs-check[/cyan]", "Check which docs need updating")
        table.add_row("[cyan]/handoff [desc][/cyan]", "Generate developer handoff report")
        table.add_row("[cyan]/doctor[/cyan]", "Run environment diagnostics")
        table.add_row("[cyan]/health[/cyan]", "Health analysis: dead code, N+1, async issues")
        table.add_row("[cyan]/release-check[/cyan]", "Release readiness checklist")
        table.add_row("[bold white]── Model Hub ──[/bold white]", "")
        table.add_row("[cyan]/models[/cyan]", "Interactive provider + model picker")
        table.add_row("[cyan]/model [name][/cyan]", "Show or switch active model")
        table.add_row("[cyan]/model-info[/cyan]", "Context window, tools, streaming info")
        table.add_row("[cyan]/provider [status|add|remove][/cyan]", "Manage provider API keys (stored in OS keyring)")
        table.add_row("[cyan]/fallback [provider/model][/cyan]", "Set fallback when primary provider fails")
        table.add_row("[cyan]/trace <symbol>[/cyan]", "Trace symbol through arch layers (route→service→db)")
        table.add_row("[cyan]/compare [base-branch][/cyan]", "Compare current branch to another")
        table.add_row("[cyan]/flaky-test [cmd][/cyan]", "Re-run test N times to detect flakiness")
        table.add_row("[cyan]/metrics[/cyan]", "Show task completion rate and session metrics")
        table.add_row("[cyan]/session-log [days][/cyan]", "Show detailed session activity log")
        table.add_row("[cyan]/plugins[/cyan]", "List loaded plugins from ~/.ultron/plugins/")
        table.add_row("[cyan]/context-status[/cyan]", "Show context window usage vs provider limit")
        table.add_row("[bold white]── P1/P2/P3/P4 ──[/bold white]", "")
        table.add_row("[cyan]/probe[/cyan]", "Probe active model capabilities empirically")
        table.add_row("[cyan]/route-info [intent][/cyan]", "Show model routing rationale")
        table.add_row("[cyan]/self-repair[/cyan]", "Detect damage and run Ultron self-repair loop")
        table.add_row("[cyan]/known-good [record][/cyan]", "Show or record the known-good version")
        table.add_row("[cyan]/replay [id|list][/cyan]", "View task execution replay timeline")
        table.add_row("[cyan]/notify-config <email> <smtp>[/cyan]", "Configure email notifications")
        table.add_row("[cyan]/audit [days][/cyan]", "View audit event log with security summary")
        self.console.print(Panel(
            table,
            title="[bold magenta]Ultron CLI Commands[/bold magenta]",
            border_style="magenta",
            expand=False
        ))

    def _get_mode_prompt(self) -> str:
        """Return the mode-prefixed prompt string, including task status if active."""
        mode = self.agent.intent_mode.upper()
        task = getattr(self.agent, "current_task", None)
        if task and task.status.value not in ("planned", "verified", "cancelled"):
            return f"[{mode}|{task.status.value.upper()}] ultron > "
        return f"[{mode}] ultron > "

    def _enforce_repl_mode(self, cmd: str) -> bool:
        """
        REPL-layer mode enforcement (P0 requirement).
        Checks slash commands that map to mutation tools against the active intent mode.
        Returns True if the command is allowed, False if blocked.
        """
        tool_equiv = _REPL_MUTATION_COMMANDS.get(cmd)
        if tool_equiv is None:
            return True  # Not a mutation command
        refusal = self.agent._enforce_intent_mode(tool_equiv)
        if refusal:
            self.console.print(f"[bold red][Mode Block][/bold red] {refusal}")
            return False
        return True

    def handle_slash_command(self, cmd_line: str) -> bool:
        """Processes slash commands. Returns True if REPL should continue, False to exit."""
        parts = cmd_line.strip().split(" ", 1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ["/exit", "/quit"]:
            self.console.print("[bold magenta]Goodbye. Ultron shutting down.[/bold magenta]")
            return False
            
        elif cmd == "/help":
            self.print_help()

        elif cmd == "/mode":
            if not arg:
                self.console.print(f"[yellow]Current mode: [bold]{self.agent.intent_mode.upper()}[/bold][/yellow]")
                self.console.print(f"[dim]Valid modes: {', '.join(sorted(VALID_MODES))}[/dim]")
            elif arg.lower() not in VALID_MODES:
                self.console.print(f"[red]Unknown mode '{arg}'. Valid modes: {', '.join(sorted(VALID_MODES))}[/red]")
            else:
                self.agent.intent_mode = arg.lower()
                self.console.print(f"[green]* Mode set to: [bold]{arg.upper()}[/bold][/green]")
            
        elif cmd == "/clear":
            self.agent.clear_memory()
            
        elif cmd == "/files":
            files = self.agent.context.pinned_files
            if not files:
                self.console.print("[yellow]No files in context. Pin files using /add <file>[/yellow]")
            else:
                self.console.print("[bold cyan]Pinned files in context:[/bold cyan]")
                for f in sorted(files):
                    self.console.print(f" - {f}")
                    
        elif cmd == "/add":
            if not arg:
                self.console.print("[red]Usage: /add <file_path>[/red]")
            else:
                success = self.agent.context.add_file(arg)
                if success:
                    self.console.print(f"[green]* Added [bold]{arg}[/bold] to context.[/green]")
                else:
                    self.console.print(f"[red]Error: File '{arg}' not found.[/red]")
                    
        elif cmd == "/drop":
            if not arg:
                self.console.print("[red]Usage: /drop <file_path>[/red]")
            else:
                success = self.agent.context.drop_file(arg)
                if success:
                    self.console.print(f"[yellow]* Dropped [bold]{arg}[/bold] from context.[/yellow]")
                else:
                    self.console.print(f"[red]Error: File '{arg}' is not in context.[/red]")
                    
        elif cmd == "/run":
            if not arg:
                self.console.print("[red]Usage: /run <command>[/red]")
            elif not self._enforce_repl_mode("/run"):
                pass  # already printed refusal
            else:
                # Route through shared command runner for consistency
                raw = self.agent.tools.execute_command_with_policy(
                    arg, require_approval=True, context="/run command"
                )
                if raw.get("exit_code") == -1 and raw.get("stderr") == "Declined by user.":
                    self.console.print("[red]Command declined.[/red]")
                else:
                    out = (raw.get("stdout") or "") + ("\n" + raw.get("stderr") if raw.get("stderr") else "")
                    self.console.print(out.strip() or "(No output)")
                
        elif cmd == "/diff":
            status = self.agent.tools.git_status()
            if status.startswith("Not a git repository"):
                self.console.print(f"[red]{status}[/red]")
            else:
                self.console.print("[bold cyan]Current Git Status:[/bold cyan]")
                self.console.print(status)
                
                # Dynamic HEAD check to resolve staging correctly
                empty_hash = self.agent.tools.get_empty_tree_hash()
                head_check = self.agent.tools.run_command("git rev-parse HEAD")
                if "exited with code 0" in head_check:
                    staged_diff = self.agent.tools.run_command("git diff --cached")
                else:
                    staged_diff = self.agent.tools.run_command(f"git diff --cached {empty_hash}")
                unstaged_diff = self.agent.tools.run_command("git diff")
                
                # Check untracked files
                untracked_res = self.agent.tools.run_command("git status --porcelain")
                untracked_files = []
                if "exited with code 0" in untracked_res or "Command exited with code 0" in untracked_res:
                    for line in untracked_res.splitlines():
                        if line.startswith("?? "):
                            untracked_files.append(line[3:])
                
                self.console.print("\n[bold green]--- Staged Changes ---[/bold green]")
                self.console.print(staged_diff if "Stdout" in staged_diff else "(No staged changes)")
                
                self.console.print("\n[bold yellow]--- Unstaged Changes ---[/bold yellow]")
                self.console.print(unstaged_diff if "Stdout" in unstaged_diff else "(No unstaged changes)")
                
                if untracked_files:
                    self.console.print("\n[bold red]--- Untracked Files ---[/bold red]")
                    for uf in untracked_files:
                        self.console.print(f" [red]?[/red] {uf}")
                
        elif cmd == "/commit":
            if not self._enforce_repl_mode("/commit"):
                return True
            # Autocommitting using AI generated commit message
            status = self.agent.tools.git_status()
            if status.startswith("Not a git repository"):
                self.console.print(f"[red]{status}[/red]")
                return True
            if "clean" in status:
                self.console.print("[yellow]Workspace is clean. Nothing to commit.[/yellow]")
                return True
                
            self.console.print("[cyan]Analyzing diff and generating commit message using local AI...[/cyan]")
            empty_hash = self.agent.tools.get_empty_tree_hash()
            head_check = self.agent.tools.run_command("git rev-parse HEAD")
            if "exited with code 0" in head_check:
                diff_res = self.agent.tools.run_command("git diff HEAD")
            else:
                diff_res = self.agent.tools.run_command(f"git diff {empty_hash}")
            
            prompt = (
                f"You are an expert Git assistant. Generate a highly descriptive, conventional commit message "
                f"for the changes in the following diff. The message should contain a short title "
                f"(e.g., 'feat: add user signup flow') followed by a brief description if necessary. "
                f"Respond with ONLY the commit message text. Do not wrap in markdown quotes or backticks.\n\n"
                f"Diff:\n{diff_res}"
            )
            
            # Request commit message generation (non-stream)
            try:
                commit_message = ""
                chat_generator = self.agent.model.chat([{"role": "user", "content": prompt}], stream=True)
                while True:
                    try:
                        chunk = next(chat_generator)
                        if chunk["type"] == "content":
                            commit_message += chunk["delta"]
                    except StopIteration as e:
                        break
                        
                commit_message = commit_message.strip()
                if not commit_message:
                    commit_message = "ultron: incremental updates"
                    
                self.console.print(Panel(
                    commit_message,
                    title="[bold green]AI Generated Commit Message[/bold green]",
                    border_style="green",
                    expand=False
                ))
                
                # Ask user for confirmation
                if Confirm.ask("[bold yellow]Stage changes and execute git commit?[/bold yellow]"):
                    res = self.agent.tools.git_commit(commit_message)
                    self.console.print(f"[green]*[/green] {res}")
            except Exception as e:
                self.console.print(f"[red]Error generating commit message: {str(e)}[/red]")
                
        elif cmd == "/undo":
            self.agent.checkpoint.undo(self.console)
            
        elif cmd == "/workspace":
            if arg:
                self.console.print("[red]For security, path switching via /workspace is disabled.[/red]")
                self.console.print("[yellow]Use the Phase 2 discovery workflow to change project workspaces.[/yellow]")
            else:
                self.console.print(self.agent.get_status_dashboard())
                
        elif cmd == "/tree":
            self.console.print("[bold cyan]Workspace Directory Structure:[/bold cyan]")
            self.console.print(self.agent.context.get_workspace_tree())
            
        elif cmd == "/refresh":
            self.agent.context.get_workspace_tree() # Clear & rebuild context files tree cache
            self.console.print("[green]* Project structure tree and index cache refreshed.[/green]")
            
        elif cmd == "/status":
            self.console.print(self.agent.get_status_dashboard())
            
        elif cmd == "/logs":
            count = int(arg) if arg and arg.isdigit() else 5
            logs = self.agent.tools.execution_logs[-count:]
            if not logs:
                self.console.print("[yellow]No command execution logs available.[/yellow]")
            else:
                self.console.print(f"[bold cyan]Last {len(logs)} Command Execution Logs:[/bold cyan]")
                for i, entry in enumerate(logs, 1):
                    self.console.print(f"\n[bold white]Log #{i}: {entry['command']}[/bold white] (Exit Code: {entry['exit_code']})")
                    if entry['stdout']:
                        self.console.print(f"[dim]Stdout:[/dim]\n{entry['stdout'].strip()}")
                    if entry['stderr']:
                        self.console.print(f"[red]Stderr:[/red]\n{entry['stderr'].strip()}")
                        
        elif cmd == "/last-error":
            err = getattr(self.agent.tools, "last_error", None)
            if not err:
                self.console.print("[green]No execution failures recorded.[/green]")
            else:
                self.console.print("[bold red]Last Execution Failure Details:[/bold red]")
                self.console.print(err)
                
        elif cmd == "/repeat":
            if not self.last_user_prompt:
                self.console.print("[red]No previous prompt to repeat.[/red]")
            else:
                self.console.print(f"[cyan]Repeating last prompt:[/cyan] [bold]{self.last_user_prompt}[/bold]")
                if self.last_task_mutated:
                    self.console.print("[yellow]Warning: The previous task mutated files in the workspace.[/yellow]")
                    confirm = Confirm.ask("[bold yellow]Are you sure you want to run this modifying task again?[/bold yellow]")
                    if not confirm:
                        self.console.print("[red]Repeat cancelled.[/red]")
                        return True
                self.agent.run(self.last_user_prompt)
                self.last_task_mutated = bool(self.agent.checkpoint.current_task_files)
                
        elif cmd == "/cancel":
            if self.agent.tools.current_process:
                self.console.print("[red]Cancelling running terminal process...[/red]")
                self.agent.tools.terminate_current_process()
            else:
                self.console.print("[yellow]No running command or task to cancel.[/yellow]")
                
        elif cmd == "/onboard":
            self.memory_manager.onboard(self.agent, self.console)
            
        elif cmd == "/plan":
            if not arg:
                self.console.print("[red]Usage: /plan <task_description>[/red]")
            else:
                self.console.print("[cyan]Generating task implementation plan...[/cyan]")
                prompt = (
                    f"You are a coding assistant. Prepare a detailed implementation plan for the following task:\n"
                    f"Task: {arg}\n\n"
                    f"Please list:\n"
                    f"1. Files that will be created or modified.\n"
                    f"2. Step-by-step description of modifications.\n"
                    f"3. Verification plan (specific tests or terminal commands to run).\n\n"
                    f"Format the output using clear markdown structure."
                )
                
                plan_text = ""
                try:
                    chat_generator = self.agent.model.chat([{"role": "user", "content": prompt}], stream=True)
                    while True:
                        try:
                            chunk = next(chat_generator)
                            if chunk["type"] == "content":
                                plan_text += chunk["delta"]
                        except StopIteration:
                            break
                except Exception as e:
                    plan_text = f"Error generating plan: {str(e)}"
                    
                self.console.print(Panel(plan_text, title="[bold green]AI Implementation Plan[/bold green]", border_style="green"))
                self.agent.last_plan_task = arg
                
        elif cmd == "/tasks":
            tasks = self.memory_manager.load_tasks()
            # Show current active task state at top
            current_task = getattr(self.agent, "current_task", None)
            if current_task:
                from ultron.task import TaskStatus
                status_color = {
                    "planned": "dim", "inspecting": "cyan", "editing": "yellow",
                    "testing": "blue", "blocked": "red", "verified": "green", "cancelled": "dim"
                }.get(current_task.status.value, "white")
                self.console.print(Panel(
                    f"[bold white]ID:[/bold white] {current_task.id}\n"
                    f"[bold white]Intent:[/bold white] {current_task.intent.value}\n"
                    f"[bold white]Status:[/bold white] [{status_color}]{current_task.status.value.upper()}[/{status_color}]\n"
                    f"[bold white]Budget:[/bold white] {current_task.budget_status()}\n"
                    f"[bold white]Files:[/bold white] {', '.join(current_task.actual_files) or 'none yet'}",
                    title="[bold cyan]Active Task[/bold cyan]",
                    border_style="cyan", expand=False
                ))
            if not arg:
                if not tasks:
                    self.console.print("[yellow]No tasks in the current checklist. Add tasks using /tasks add <description>[/yellow]")
                else:
                    self.console.print("[bold cyan]Task Checklist Status:[/bold cyan]")
                    status_symbols = {
                        "planned": "[ ]",
                        "editing": "[/]",
                        "testing": "[*]",
                        "verified": "[x]"
                    }
                    for i, t in enumerate(tasks, 1):
                        self.console.print(f" {i}. {status_symbols.get(t['status'], '[ ]')} {t['desc']}")
            else:
                parts = arg.split(" ", 1)
                subcmd = parts[0].lower()
                subarg = parts[1] if len(parts) > 1 else ""
                
                if subcmd == "add":
                    if not subarg:
                        self.console.print("[red]Usage: /tasks add <description>[/red]")
                    else:
                        tasks.append({"desc": subarg, "status": "planned"})
                        self.memory_manager.save_tasks(tasks)
                        self.console.print(f"[green]* Task added: {subarg}[/green]")
                elif subcmd in ["check", "verify", "edit", "test"]:
                    if not subarg or not subarg.isdigit():
                        self.console.print(f"[red]Usage: /tasks {subcmd} <task_number>[/red]")
                    else:
                        idx = int(subarg) - 1
                        if idx < 0 or idx >= len(tasks):
                            self.console.print("[red]Error: Task index out of range.[/red]")
                        else:
                            status_map = {
                                "check": "verified",
                                "verify": "verified",
                                "edit": "editing",
                                "test": "testing"
                            }
                            tasks[idx]["status"] = status_map[subcmd]
                            self.memory_manager.save_tasks(tasks)
                            self.console.print(f"[green]* Task #{idx+1} marked as {status_map[subcmd]}.[/green]")
                elif subcmd == "clear":
                    tasks.clear()
                    self.memory_manager.save_tasks(tasks)
                    self.console.print("[green]* Task checklist cleared.[/green]")
                else:
                    self.console.print("[red]Unknown tasks subcommand. Use add, check, edit, test, or clear.[/red]")
                    
        elif cmd == "/test":
            if not self._enforce_repl_mode("/test"):
                return True
            cmd_info = self.memory_manager.load_memory()["commands"]["test"]
            test_cmd = cmd_info["cmd"]
            if not test_cmd:
                self.console.print("[yellow]No test command configured. Run /onboard to detect tests.[/yellow]")
                return True
                
            if arg:
                test_cmd = f"{test_cmd} {arg}"
                
            if cmd_info.get("status") == "unverified":
                self.console.print(f"[yellow]Warning: Discovered command '{test_cmd}' is unverified.[/yellow]")
                approved = Confirm.ask("[bold yellow]Do you approve running this unverified test command?[/bold yellow]")
                if not approved:
                    self.console.print("[red]Command skipped.[/red]")
                    return True
                    
            self.console.print(f"[dim]Running tests: {test_cmd}[/dim]")
            res = self.agent.tools.run_command(test_cmd)
            self.console.print(res)
            
            if "exited with code 0" in res or "Command exited with code 0" in res:
                mem = self.memory_manager.load_memory()
                mem["commands"]["test"]["status"] = "verified"
                if not arg:
                    mem["commands"]["test"]["cmd"] = test_cmd
                self.memory_manager.save_memory(mem)
                self.console.print("[green]* Test command verified and saved.[/green]")
                
        elif cmd == "/lint":
            if not self._enforce_repl_mode("/lint"):
                return True
            cmd_info = self.memory_manager.load_memory()["commands"]["lint"]
            lint_cmd = cmd_info["cmd"]
            if not lint_cmd:
                self.console.print("[yellow]No lint command configured. Run /onboard to detect linters.[/yellow]")
                return True
                
            if arg:
                lint_cmd = f"{lint_cmd} {arg}"
                
            if cmd_info.get("status") == "unverified":
                self.console.print(f"[yellow]Warning: Discovered command '{lint_cmd}' is unverified.[/yellow]")
                approved = Confirm.ask("[bold yellow]Do you approve running this unverified lint command?[/bold yellow]")
                if not approved:
                    self.console.print("[red]Command skipped.[/red]")
                    return True
                    
            self.console.print(f"[dim]Running linter: {lint_cmd}[/dim]")
            res = self.agent.tools.run_command(lint_cmd)
            self.console.print(res)
            
            if "exited with code 0" in res or "Command exited with code 0" in res:
                mem = self.memory_manager.load_memory()
                mem["commands"]["lint"]["status"] = "verified"
                if not arg:
                    mem["commands"]["lint"]["cmd"] = lint_cmd
                self.memory_manager.save_memory(mem)
                self.console.print("[green]* Lint command verified and saved.[/green]")
                
        elif cmd == "/fix":
            if not self._enforce_repl_mode("/fix"):
                return True
            err = getattr(self.agent.tools, "last_error", None)
            if not err:
                self.console.print("[green]No execution failures recorded to fix.[/green]")
            else:
                self.console.print(f"[bold cyan]Triggering focused repair loop for error:[/bold cyan]\n{err}")
                self.agent.run(f"Please analyze and fix the following execution failure:\n{err}")
                
        # ----------------------------------------------------------------
        # Phase 2 commands
        # --------------------------------------------------------------------------------------------

        elif cmd == "/analyze":
            self._cmd_analyze(arg)

        elif cmd == "/find-folder":
            self._cmd_find_folder(arg)

        elif cmd == "/open":
            self._cmd_open(arg)

        elif cmd == "/find":
            self._cmd_find(arg)

        elif cmd == "/symbol":
            self._cmd_symbol(arg)

        elif cmd == "/references":
            self._cmd_references(arg)

        elif cmd == "/flow":
            self._cmd_flow(arg)

        elif cmd == "/explain":
            self._cmd_explain(arg)

        elif cmd == "/impact":
            self._cmd_impact(arg)

        elif cmd == "/why":
            self._cmd_why(arg)

        elif cmd == "/min-repro":
            self._cmd_min_repro(arg)

        elif cmd == "/init-project":
            self._cmd_init_project()

        # ----------------------------------------------------------------
        # Phase 3 commands
        # ----------------------------------------------------------------

        elif cmd == "/contract":
            self.agent.contract.display(self.console)

        elif cmd == "/verify":
            self._cmd_verify(arg)

        elif cmd == "/review":
            self._cmd_review(arg)

        elif cmd == "/reproduce":
            self._cmd_reproduce(arg)

        elif cmd == "/bisect":
            self._cmd_bisect()

        # ----------------------------------------------------------------
        # Phase 4 commands
        # ----------------------------------------------------------------

        elif cmd == "/worktree":
            self._cmd_worktree(arg)

        elif cmd == "/pr-summary":
            self._cmd_pr_summary(arg)

        elif cmd == "/commit-check":
            self._cmd_commit_check(arg)

        elif cmd == "/decisions":
            self._cmd_decisions(arg)

        elif cmd == "/monorepo":
            self._cmd_monorepo()

        elif cmd == "/recent":
            self._cmd_recent()

        elif cmd == "/alias":
            self._cmd_alias(arg)

        elif cmd == "/feature":
            self._cmd_feature(arg)

        elif cmd == "/scaffold-audit":
            self._cmd_scaffold_audit()

        elif cmd == "/docs-check":
            self._cmd_docs_check()

        elif cmd == "/handoff":
            self._cmd_handoff(arg)

        elif cmd == "/doctor":
            self._cmd_doctor()

        elif cmd == "/health":
            self._cmd_health()

        elif cmd == "/release-check":
            self._cmd_release_check()

        # ----------------------------------------------------------------
        # Model Hub commands
        # ----------------------------------------------------------------

        elif cmd == "/models":
            self._cmd_models()

        elif cmd == "/model":
            self._cmd_model(arg)

        elif cmd == "/model-info":
            self._cmd_model_info()

        elif cmd == "/provider":
            self._cmd_provider(arg)

        elif cmd == "/fallback":
            self._cmd_fallback(arg)

        elif cmd == "/trace":
            self._cmd_trace(arg)

        elif cmd == "/compare":
            self._cmd_compare(arg)

        elif cmd == "/flaky-test":
            self._cmd_flaky_test(arg)

        elif cmd == "/metrics":
            self._cmd_metrics()

        elif cmd == "/session-log":
            self._cmd_session_log(arg)

        elif cmd == "/plugins":
            self._cmd_plugins()

        elif cmd == "/context-status":
            self._cmd_context_status()

        elif cmd == "/probe":
            self._cmd_probe()

        elif cmd == "/route-info":
            self._cmd_route_info(arg)

        elif cmd == "/self-repair":
            self._cmd_self_repair()

        elif cmd == "/known-good":
            self._cmd_known_good(arg)

        elif cmd == "/replay":
            self._cmd_replay(arg)

        elif cmd == "/notify-config":
            self._cmd_notify_config(arg)

        elif cmd == "/audit":
            self._cmd_audit(arg)

        else:
            self.console.print(f"[red]Unknown command: {cmd}. Type /help for assistance.[/red]")
            
        return True

    # ----------------------------------------------------------------
    # Phase 2 command implementations
    # ----------------------------------------------------------------

    def _ensure_repo_map(self, rebuild: bool = False) -> bool:
        """Build/refresh repo map, return True if index has entries."""
        rm = self.agent.repo_map
        if rebuild or not rm.index:
            self.console.print("[cyan]Building repository index...[/cyan]")
            count = rm.build(force=rebuild)
            self.console.print(f"[green]* Indexed {count} file(s). Total: {len(rm.index)} files.[/green]")
        return bool(rm.index)

    def _cmd_analyze(self, arg: str):
        """Analyze the workspace and print a compact repo map summary."""
        self._ensure_repo_map(rebuild=True)
        rm = self.agent.repo_map
        summary = rm.get_summary()

        table = Table(box=None, show_header=False)
        table.add_row("[bold white]Total files indexed:[/bold white]", str(summary["total_files"]))
        table.add_row("[bold white]Test files:[/bold white]", str(summary["test_files"]))
        for lang, count in sorted(summary["by_language"].items(), key=lambda x: -x[1]):
            table.add_row(f"  [cyan]{lang}[/cyan]", str(count))

        self.console.print(Panel(table, title="[bold magenta]Repository Map Summary[/bold magenta]", border_style="magenta", expand=False))

        # List entry points (non-test files with main/app/server/index in name)
        entry_candidates = [
            p for p in rm.index
            if not rm.index[p].get("is_test") and
            any(kw in os.path.basename(p).lower() for kw in ["main", "app", "server", "index", "cli", "run"])
        ]
        if entry_candidates:
            self.console.print("\n[bold white]Likely entry points:[/bold white]")
            for f in entry_candidates[:10]:
                self.console.print(f"  [green]{f}[/green]")

        test_files = rm.get_test_files()
        if test_files:
            self.console.print(f"\n[bold white]Test files ({len(test_files)}):[/bold white]")
            for f in test_files[:10]:
                self.console.print(f"  [yellow]{f}[/yellow]")
            if len(test_files) > 10:
                self.console.print(f"  [dim]... and {len(test_files)-10} more[/dim]")

    def _cmd_find_folder(self, arg: str):
        """Search for a folder by name in the workspace (and optionally drives)."""
        if not arg:
            self.console.print("[red]Usage: /find-folder <name>[/red]")
            return

        self.console.print(f"[cyan]Searching workspace for folders matching '{arg}'...[/cyan]")
        results = find_folders(arg, self.agent.workspace_root, max_results=20)

        if not results:
            self.console.print(f"[yellow]No folders found matching '{arg}' in workspace.[/yellow]")
            expand = Confirm.ask("[bold yellow]Search drives (read-only, may be slow)?[/bold yellow]")
            if expand:
                search_root = os.path.splitdrive(self.agent.workspace_root)[0] + os.sep
                self.console.print(f"[cyan]Searching drive {search_root}...[/cyan]")
                results = find_folders(arg, search_root, max_results=20)

        if not results:
            self.console.print(f"[red]No folders found matching '{arg}'.[/red]")
            return

        self.console.print(f"\n[bold white]Found {len(results)} match(es):[/bold white]")
        for i, r in enumerate(results, 1):
            self.console.print(f"  [cyan]{i}.[/cyan] {r}")
        self.console.print("\n[dim]Use /open <number or path> to switch workspace.[/dim]")
        # Store for /open by number
        self._last_folder_results = results

    def _cmd_open(self, arg: str):
        """Switch workspace to a new path (by number from /find-folder or explicit path)."""
        if not arg:
            self.console.print("[red]Usage: /open <number or path>[/red]")
            return

        # Resolve by number from last /find-folder results
        target = arg
        last_results = getattr(self, "_last_folder_results", [])
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(last_results):
                target = last_results[idx]
            else:
                self.console.print("[red]Invalid number. Run /find-folder first.[/red]")
                return

        if not os.path.isdir(target):
            self.console.print(f"[red]Error: '{target}' is not a valid directory.[/red]")
            return

        self.console.print(f"\n[bold yellow]Switching workspace to:[/bold yellow] {target}")
        self.console.print("[yellow]This will reset all context, pinned files, task state, and conversation memory.[/yellow]")
        confirmed = Confirm.ask("[bold yellow]Confirm workspace switch?[/bold yellow]")
        if not confirmed:
            self.console.print("[red]Workspace switch cancelled.[/red]")
            return

        # Rebuild agent with new workspace
        from ultron.agent import UltronAgent
        new_agent = UltronAgent(
            workspace_root=target,
            model_name=self.agent.model.model_name,
            auto_approve=self.agent.auto_approve,
            auto_commit=self.agent.auto_commit,
        )
        new_agent.model.base_url = self.agent.model.base_url
        self.agent = new_agent
        self.memory_manager = ProjectMemoryManager(target)
        self.console.print(f"[green]* Workspace switched to: {target}[/green]")
        self.console.print(self.agent.get_status_dashboard())

    def _cmd_find(self, arg: str):
        """Text search across all indexed files."""
        if not arg:
            self.console.print("[red]Usage: /find <query>[/red]")
            return
        self._ensure_repo_map()
        results = self.agent.repo_map.find_text(arg)
        if not results:
            self.console.print(f"[yellow]No matches for '{arg}'.[/yellow]")
            return
        self.console.print(f"[bold white]{len(results)} match(es) for '[cyan]{arg}[/cyan]':[/bold white]")
        for r in results[:50]:
            self.console.print(f"  [green]{r['file']}:{r['line']}[/green]  {r['text'][:100]}")
        if len(results) > 50:
            self.console.print(f"  [dim]... {len(results)-50} more matches (showing first 50)[/dim]")

    def _cmd_symbol(self, arg: str):
        """Find symbol definitions across the codebase."""
        if not arg:
            self.console.print("[red]Usage: /symbol <name>[/red]")
            return
        self._ensure_repo_map()
        results = self.agent.repo_map.find_symbol(arg)
        if not results:
            self.console.print(f"[yellow]No symbol matching '{arg}' found.[/yellow]")
            return
        self.console.print(f"[bold white]{len(results)} symbol(s) matching '[cyan]{arg}[/cyan]':[/bold white]")
        for r in results:
            self.console.print(f"  [green]{r['file']}:{r['line']}[/green]  [{r['kind']}] [bold]{r['name']}[/bold]")

    def _cmd_references(self, arg: str):
        """Find all references to a symbol."""
        if not arg:
            self.console.print("[red]Usage: /references <symbol>[/red]")
            return
        self._ensure_repo_map()
        refs = self.agent.repo_map.find_references(arg)
        if not refs:
            self.console.print(f"[yellow]No references to '{arg}' found.[/yellow]")
            return
        self.console.print(f"[bold white]{len(refs)} reference(s) to '[cyan]{arg}[/cyan]':[/bold white]")
        for r in refs[:50]:
            self.console.print(f"  [green]{r['file']}:{r['line']}[/green]  {r['text'][:100]}")
        if len(refs) > 50:
            self.console.print(f"  [dim]... {len(refs)-50} more[/dim]")

    def _cmd_flow(self, arg: str):
        """Trace the flow of a symbol: where defined, where called, related tests."""
        if not arg:
            self.console.print("[red]Usage: /flow <symbol>[/red]")
            return
        self._ensure_repo_map()
        rm = self.agent.repo_map
        defs = rm.find_symbol(arg)
        callers = rm.callers_of(arg)
        related_tests = []
        for d in defs:
            related_tests.extend(rm.find_related_tests(d["file"]))
        related_tests = list(set(related_tests))

        self.console.print(Panel(
            f"[bold white]Symbol:[/bold white] [cyan]{arg}[/cyan]\n\n"
            f"[bold white]Defined in:[/bold white]\n" +
            ("\n".join(f"  [green]{d['file']}:{d['line']}[/green] [{d['kind']}]" for d in defs) or "  Not found") +
            f"\n\n[bold white]Called from ({len(callers)}):[/bold white]\n" +
            ("\n".join(f"  [yellow]{c['file']}:{c['line']}[/yellow]  {c['text'][:80]}" for c in callers[:10]) or "  No callers found") +
            (f"\n  [dim]... {len(callers)-10} more[/dim]" if len(callers) > 10 else "") +
            f"\n\n[bold white]Related tests:[/bold white]\n" +
            ("\n".join(f"  [magenta]{t}[/magenta]" for t in related_tests[:5]) or "  None found"),
            title=f"[bold cyan]Flow: {arg}[/bold cyan]",
            border_style="cyan",
            expand=False
        ))

    def _cmd_explain(self, arg: str):
        """Ask the LLM to explain a file or symbol with codebase context."""
        if not arg:
            self.console.print("[red]Usage: /explain <file or symbol>[/red]")
            return
        self._ensure_repo_map()
        rm = self.agent.repo_map

        # Determine if arg is a file or symbol
        is_file = os.path.isfile(os.path.join(self.agent.workspace_root, arg))
        context_snippets = []

        if is_file:
            abs_path = os.path.join(self.agent.workspace_root, arg)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()[:4000]
                context_snippets.append(f"File: {arg}\n```\n{content}\n```")
            except Exception:
                pass
            syms = rm.get_file_symbols(arg)
            if syms:
                context_snippets.append("Symbols: " + ", ".join(s["name"] for s in syms[:20]))
        else:
            defs = rm.find_symbol(arg)
            for d in defs[:3]:
                abs_path = os.path.join(self.agent.workspace_root, d["file"])
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    start = max(0, d["line"] - 1)
                    snippet = "".join(lines[start:start+30])
                    context_snippets.append(f"From {d['file']}:{d['line']}:\n```\n{snippet}\n```")
                except Exception:
                    pass

        if not context_snippets:
            self.console.print(f"[yellow]Could not find '{arg}' in the index. Try /analyze first.[/yellow]")
            return

        prompt = (
            f"Explain the following code in the context of this project. "
            f"Be concise — 3 to 6 sentences. Describe what it does, its role, "
            f"and any notable design decisions.\n\n" + "\n\n".join(context_snippets)
        )

        self.console.print(f"[cyan]Explaining '{arg}'...[/cyan]")
        explanation = ""
        try:
            gen = self.agent.model.chat([{"role": "user", "content": prompt}], stream=True)
            while True:
                try:
                    chunk = next(gen)
                    if chunk["type"] == "content":
                        explanation += chunk["delta"]
                except StopIteration:
                    break
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            return

        self.console.print(Panel(explanation.strip(), title=f"[bold cyan]Explanation: {arg}[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_impact(self, arg: str):
        """Run impact analysis on a file or symbol."""
        if not arg:
            self.console.print("[red]Usage: /impact <file or symbol>[/red]")
            return
        self._ensure_repo_map()
        analyzer = ImpactAnalyzer(self.agent.repo_map)

        is_file = os.path.isfile(os.path.join(self.agent.workspace_root, arg.replace("/", os.sep)))
        if is_file:
            report = analyzer.analyze_file(arg.replace(os.sep, "/"))
            target_label = f"File: {arg}"
            syms_text = "\n".join(
                f"  [{s['kind']}] [bold]{s['name']}[/bold] (line {s['line']})"
                for s in report["symbols_defined"][:15]
            ) or "  None"
            callers_text = "\n".join(
                f"  [yellow]{c['file']}:{c['line']}[/yellow]  {c['text'][:80]}"
                for c in report["callers"][:10]
            ) or "  None"
            imported_by_text = "\n".join(f"  [cyan]{f}[/cyan]" for f in report["imported_by"][:10]) or "  None"
            tests_text = "\n".join(f"  [magenta]{t}[/magenta]" for t in report["related_tests"]) or "  None"
        else:
            report = analyzer.analyze_symbol(arg)
            target_label = f"Symbol: {arg}"
            syms_text = "\n".join(
                f"  [green]{d['file']}:{d['line']}[/green] [{d['kind']}]"
                for d in report["definitions"][:10]
            ) or "  Not found"
            callers_text = "\n".join(
                f"  [yellow]{c['file']}:{c['line']}[/yellow]  {c['text'][:80]}"
                for c in report["callers"][:10]
            ) or "  None"
            imported_by_text = ""
            tests_text = "\n".join(
                f"  [magenta]{t['file']}:{t['line']}[/magenta]  {t['text'][:60]}"
                for t in report.get("test_references", [])[:5]
            ) or "  None"

        risk_color = {"low": "green", "medium": "yellow", "high": "red"}.get(report["risk"], "white")
        warnings_text = "\n".join(f"  [yellow]⚠ {w}[/yellow]" for w in report["warnings"]) or "  None"

        body = (
            f"[bold white]Risk:[/bold white] [{risk_color}]{report['risk'].upper()}[/{risk_color}]\n\n"
            f"[bold white]Symbols defined:[/bold white]\n{syms_text}\n\n"
            f"[bold white]Callers / call sites:[/bold white]\n{callers_text}\n\n"
        )
        if imported_by_text:
            body += f"[bold white]Imported by:[/bold white]\n{imported_by_text}\n\n"
        body += (
            f"[bold white]Related tests:[/bold white]\n{tests_text}\n\n"
            f"[bold white]Warnings:[/bold white]\n{warnings_text}"
        )

        self.console.print(Panel(body, title=f"[bold red]Impact: {target_label}[/bold red]", border_style="red", expand=False))

    def _cmd_why(self, arg: str):
        """Investigate the latest failure (or passed log text) and explain root cause."""
        self._ensure_repo_map()
        investigator = FailureInvestigator(self.agent.workspace_root, self.agent.repo_map)

        # Use last_error from tools, or arg as log text, or ask model about last command output
        log_text = arg
        if not log_text:
            log_text = getattr(self.agent.tools, "last_error", "") or ""
        if not log_text and self.agent.tools.execution_logs:
            last = self.agent.tools.execution_logs[-1]
            log_text = f"{last.get('stdout','')}\n{last.get('stderr','')}"
        if not log_text:
            self.console.print("[yellow]No error log available. Run a command first or pass log text: /why <log>[/yellow]")
            return

        report = investigator.investigate(log_text)

        errors_text = "\n".join(f"  [red]{e[:120]}[/red]" for e in report["extracted_errors"]) or "  Could not extract specific errors"
        locations_text = "\n".join(
            f"  [green]{l['file']}" + (f":{l['line']}" if l['line'] else "") + "[/green]"
            for l in report["source_locations"]
        ) or "  No source locations identified"
        suggestions_text = "\n".join(f"  • {s}" for s in report["repair_suggestions"])

        body = (
            f"[bold white]Error type:[/bold white] [cyan]{report['error_type']}[/cyan]\n\n"
            f"[bold white]Extracted errors:[/bold white]\n{errors_text}\n\n"
            f"[bold white]Source locations:[/bold white]\n{locations_text}\n\n"
            f"[bold white]Repair suggestions:[/bold white]\n{suggestions_text}"
        )
        self.console.print(Panel(body, title="[bold red]Failure Investigation (/why)[/bold red]", border_style="red", expand=False))

        # If Ollama is available, ask for root cause analysis
        if self.agent.model.is_available() and report["extracted_errors"]:
            self.console.print("[cyan]Asking AI for root cause analysis...[/cyan]")
            prompt = (
                f"You are a debugging expert. Analyze this error and give a concise root cause "
                f"(2-4 sentences) and the most likely fix. Be specific about file and line if known.\n\n"
                f"Error type: {report['error_type']}\n"
                f"Errors:\n" + "\n".join(report["extracted_errors"][:5]) + "\n"
                f"Source locations: {[l['file'] for l in report['source_locations'][:3]]}"
            )
            analysis = ""
            try:
                gen = self.agent.model.chat([{"role": "user", "content": prompt}], stream=True)
                while True:
                    try:
                        chunk = next(gen)
                        if chunk["type"] == "content":
                            analysis += chunk["delta"]
                    except StopIteration:
                        break
            except Exception:
                pass
            if analysis:
                self.console.print(Panel(analysis.strip(), title="[bold cyan]AI Root Cause Analysis[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_min_repro(self, arg: str):
        """Generate a minimal reproduction script for the last failure."""
        self._ensure_repo_map()
        investigator = FailureInvestigator(self.agent.workspace_root, self.agent.repo_map)

        log_text = arg
        if not log_text:
            log_text = getattr(self.agent.tools, "last_error", "") or ""
        if not log_text and self.agent.tools.execution_logs:
            last = self.agent.tools.execution_logs[-1]
            log_text = f"{last.get('stdout','')}\n{last.get('stderr','')}"
        if not log_text:
            self.console.print("[yellow]No error log found. Pass log text: /min-repro <log>[/yellow]")
            return

        # Provide LLM callable if online
        model_callable = None
        if self.agent.model.is_available():
            def model_callable(prompt: str) -> str:
                result = ""
                try:
                    gen = self.agent.model.chat([{"role": "user", "content": prompt}], stream=True)
                    while True:
                        try:
                            chunk = next(gen)
                            if chunk["type"] == "content":
                                result += chunk["delta"]
                        except StopIteration:
                            break
                except Exception:
                    pass
                return result

        self.console.print("[cyan]Generating minimal reproduction script...[/cyan]")
        script = investigator.generate_min_repro(log_text, model_callable)

        self.console.print(Panel(script, title="[bold cyan]Minimal Reproduction Script[/bold cyan]", border_style="cyan", expand=False))

        save = Confirm.ask("[bold yellow]Save this script to min_repro.py?[/bold yellow]")
        if save:
            res = self.agent.tools.write_file("min_repro.py", script)
            self.console.print(f"[green]* {res}[/green]")

    def _cmd_init_project(self):
        """Create ULTRON.md and .ultron.toml templates in the workspace."""
        from ultron.analyzer import create_ultron_md_template, create_ultron_toml_template
        r1 = create_ultron_md_template(self.agent.workspace_root)
        r2 = create_ultron_toml_template(self.agent.workspace_root)
        self.console.print(f"[green]* {r1}[/green]")
        self.console.print(f"[green]* {r2}[/green]")
        self.console.print("[dim]Edit ULTRON.md to add project instructions and .ultron.toml for settings.[/dim]")

    # ----------------------------------------------------------------
    # Phase 3 command implementations
    # ----------------------------------------------------------------

    def _cmd_verify(self, arg: str):
        """Run structured verification checks (impact-aware via VerificationPlanner)."""
        from ultron.verifier import Verifier
        checks = arg.split() if arg.strip() else None
        mem = self.memory_manager.load_memory()

        # Get changed files for VerificationPlanner
        changed_files = list(self.agent.checkpoint.current_task_files.keys())
        if not changed_files:
            import subprocess
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.agent.workspace_root, capture_output=True, text=True
            )
            changed_files = [f.strip() for f in r.stdout.splitlines() if f.strip()]

        verifier = Verifier(
            tools=self.agent.tools,
            project_memory=mem,
            workspace_root=self.agent.workspace_root,
            intent_mode=self.agent.intent_mode,
        )

        if checks is None and changed_files:
            self.console.print(f"[dim]VerificationPlanner selected checks based on {len(changed_files)} changed file(s).[/dim]")

        self.console.print("[cyan]Running verification checks...[/cyan]")
        report = verifier.run(
            checks=checks,
            changed_files=changed_files if checks is None else None,
            use_planner=checks is None,
        )
        verifier.display_report(report, self.console)
        verifier.save_report(report)

        # Attach evidence to active contract if present
        active = self.agent.contract.load_active()
        if active is not None:
            evidence = {c.category: c.status for c in report.checks}
            self.agent.contract.complete_contract(evidence)
            self.console.print("[dim]Verification evidence recorded in active contract.[/dim]")

    def _cmd_review(self, arg: str):
        """One-time code review — inspects existing diff, never edits files."""
        from ultron.reviewer import CodeReviewer
        reviewer = CodeReviewer(self.agent.workspace_root)
        self.console.print("[cyan]Collecting diff and running review...[/cyan]")
        report = reviewer.review(agent=self.agent, model=self.agent.model)
        reviewer.display_report(report, self.console)
        reviewer.save_report(report, self.agent.workspace_root)

    def _cmd_reproduce(self, arg: str):
        """Save a sanitized bug reproduction package to disk."""
        from ultron.analyzer import FailureInvestigator
        import re as _re

        # --- Detect language for extension ---
        lang_ext = {"Python": ".py", "NodeJS": ".js", "TypeScript": ".ts",
                    "Rust": ".rs", "Go": ".go"}
        proj_type = self.agent.tools.detect_project_type()
        ext = ".txt"
        for lang, e in lang_ext.items():
            if lang.lower() in proj_type.lower():
                ext = e
                break

        # --- Secret redaction helper ---
        SECRET_PATTERN = _re.compile(
            r'(?i)(password|passwd|token|secret|api_key|apikey|auth_key|private_key)'
            r'\s*=\s*["\'][^"\']{6,}',
        )
        def redact(text: str) -> str:
            return SECRET_PATTERN.sub(lambda m: m.group(0).split("=")[0] + "=<REDACTED>", text)

        # --- Collect materials ---
        log_text = getattr(self.agent.tools, "last_error", "") or ""
        if not log_text and self.agent.tools.execution_logs:
            last = self.agent.tools.execution_logs[-1]
            log_text = f"{last.get('stdout','')}\n{last.get('stderr','')}"

        self._ensure_repo_map()
        investigator = FailureInvestigator(self.agent.workspace_root, self.agent.repo_map)
        script = investigator.generate_min_repro(log_text or "(no error log)", model_callable=None)

        # Diff collection
        from ultron.reviewer import CodeReviewer
        reviewer = CodeReviewer(self.agent.workspace_root)
        diff_text, _ = reviewer.collect_diff(agent=self.agent)

        # --- Create save directory ---
        ws_hash = hashlib.md5(self.agent.workspace_root.encode()).hexdigest()[:12]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        repro_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "reproductions", ws_hash, ts
        )
        os.makedirs(repro_dir, exist_ok=True)

        # --- Write files ---
        files_written = []
        try:
            # error.log — secrets redacted
            error_path = os.path.join(repro_dir, "error.log")
            with open(error_path, "w", encoding="utf-8") as f:
                f.write(redact(log_text or "(no error log)"))
            files_written.append("error.log")

            # diff.patch
            patch_path = os.path.join(repro_dir, "diff.patch")
            with open(patch_path, "w", encoding="utf-8") as f:
                f.write(diff_text or "(no diff available)")
            files_written.append("diff.patch")

            # min_repro.<ext>
            repro_name = f"min_repro{ext}"
            repro_path = os.path.join(repro_dir, repro_name)
            with open(repro_path, "w", encoding="utf-8") as f:
                f.write(script)
            files_written.append(repro_name)

            # report.md
            desc = arg.strip() or "Bug reproduction"
            report_md = (
                f"# Reproduction Report\n\n"
                f"**Description:** {desc}\n"
                f"**Timestamp:** {ts}\n"
                f"**Workspace:** {self.agent.workspace_root}\n\n"
                f"## Files\n"
                + "\n".join(f"- `{f}`" for f in files_written) + "\n"
            )
            report_path = os.path.join(repro_dir, "report.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_md)
            files_written.append("report.md")
        except Exception as e:
            self.console.print(f"[red]Error saving reproduction package: {e}[/red]")
            return

        self.console.print(Panel(
            f"[green]Reproduction package saved to:[/green]\n{repro_dir}\n\n"
            + "\n".join(f"  [cyan]{f}[/cyan]" for f in files_written),
            title="[bold green]/reproduce[/bold green]",
            border_style="green",
            expand=False,
        ))

    def _cmd_bisect(self):
        """Print guided git bisect instructions. Uses Git commits only — never checkpoints."""
        self.console.print(Panel(
            "[bold white]Git Bisect Guidance[/bold white]\n\n"
            "[dim]git bisect works with Git commit history. File checkpoints are NOT valid bisect baselines.[/dim]",
            title="[bold cyan]/bisect[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))

        commit_hash = ""
        try:
            commit_hash = self.session.prompt(
                "Enter known-good Git commit hash or tag (leave blank for general guidance): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            pass

        if commit_hash:
            instructions = (
                f"[bold white]Run these commands to start bisecting:[/bold white]\n\n"
                f"  [green]git bisect start[/green]\n"
                f"  [green]git bisect bad HEAD[/green]          [dim]# current commit is broken[/dim]\n"
                f"  [green]git bisect good {commit_hash}[/green]   [dim]# last known-good commit[/dim]\n\n"
                f"Git will check out commits for you to test. After testing each:\n"
                f"  [green]git bisect good[/green]   [dim]# if this commit is fine[/dim]\n"
                f"  [green]git bisect bad[/green]    [dim]# if this commit is broken[/dim]\n\n"
                f"When done: [green]git bisect reset[/green]"
            )
        else:
            instructions = (
                "[bold white]General git bisect steps:[/bold white]\n\n"
                "1. Find a commit where things worked:  [green]git log --oneline -20[/green]\n"
                "2. Start a bisect session:\n"
                "   [green]git bisect start[/green]\n"
                "   [green]git bisect bad HEAD[/green]\n"
                "   [green]git bisect good <known-good-hash>[/green]\n"
                "3. Test each checkout Git gives you, then run:\n"
                "   [green]git bisect good[/green] or [green]git bisect bad[/green]\n"
                "4. Git will identify the first bad commit.\n"
                "5. Reset when done: [green]git bisect reset[/green]\n\n"
                "[dim]Note: file-level /undo checkpoints are NOT valid bisect baselines.[/dim]"
            )

        self.console.print(Panel(
            instructions,
            title="[bold cyan]Git Bisect Instructions[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))

    # ----------------------------------------------------------------
    # Phase 4 command implementations
    # ----------------------------------------------------------------

    def _cmd_worktree(self, arg: str):
        """Manage Git worktrees: list, create, remove."""
        wm = WorktreeManager(self.agent.workspace_root)
        parts = arg.split() if arg else []
        subcmd = parts[0] if parts else "list"

        if subcmd == "list" or not subcmd:
            trees = wm.list_worktrees()
            if not trees:
                self.console.print("[yellow]No worktrees found (or not a git repo).[/yellow]")
                return
            self.console.print("[bold white]Worktrees:[/bold white]")
            for t in trees:
                branch = t.get("branch", "?").replace("refs/heads/", "")
                commit = t.get("commit", "?")
                self.console.print(f"  [green]{t['path']}[/green]  [{branch}] {commit}")

        elif subcmd == "create":
            if len(parts) < 2:
                self.console.print("[red]Usage: /worktree create <branch-name> [base-branch][/red]")
                return
            branch = parts[1]
            base = parts[2] if len(parts) > 2 else "HEAD"
            self.console.print(f"[cyan]Creating worktree for branch '{branch}' from '{base}'...[/cyan]")
            ok, msg = wm.create_worktree(branch, base)
            color = "green" if ok else "red"
            self.console.print(f"[{color}]* {msg}[/{color}]")

        elif subcmd == "remove":
            if len(parts) < 2:
                self.console.print("[red]Usage: /worktree remove <path>[/red]")
                return
            path = " ".join(parts[1:])
            force = Confirm.ask(f"[bold yellow]Force remove worktree at '{path}'?[/bold yellow]")
            ok, msg = wm.remove_worktree(path, force=force)
            color = "green" if ok else "red"
            self.console.print(f"[{color}]* {msg}[/{color}]")
        else:
            self.console.print("[red]Usage: /worktree [list|create <branch>|remove <path>][/red]")

    def _cmd_pr_summary(self, arg: str):
        """Generate a PR summary for the current branch."""
        gen = PRSummaryGenerator(self.agent.workspace_root)
        base = arg.strip() if arg.strip() else "main"

        if self.agent.model.is_available():
            self.console.print(f"[cyan]Generating AI PR summary (base: {base})...[/cyan]")
            summary = gen.generate_with_ai(self.agent.model, base_branch=base)
        else:
            self.console.print(f"[cyan]Generating PR summary (base: {base})...[/cyan]")
            stats = gen.get_diff_stats()
            summary = gen.generate(
                title="Pull Request",
                description=stats["stat_output"] or "_No changes detected._",
                test_evidence="",
                risks="",
                migration_notes="",
                reviewer_checklist=["Tests pass", "No debug code left", "Docs updated", "Conventional commit message"],
                base_branch=base,
            )

        self.console.print(Panel(summary, title="[bold green]PR Summary[/bold green]", border_style="green", expand=False))
        save = Confirm.ask("[bold yellow]Save PR summary to PR_SUMMARY.md?[/bold yellow]")
        if save:
            res = self.agent.tools.write_file("PR_SUMMARY.md", summary)
            self.console.print(f"[green]* {res}[/green]")

    def _cmd_commit_check(self, arg: str):
        """Run commit quality checks on a message."""
        if not arg:
            self.console.print("[red]Usage: /commit-check <commit message>[/red]")
            return
        checker = CommitQualityChecker()
        report = checker.run_full_check(arg, self.agent.workspace_root, self.agent.repo_map if self.agent.repo_map.index else None)

        if report["passed"]:
            self.console.print("[bold green]✓ Commit quality check passed.[/bold green]")
        else:
            self.console.print("[bold yellow]Commit quality issues found:[/bold yellow]")
            for issue in report["all_issues"]:
                self.console.print(f"  [yellow]⚠[/yellow] {issue}")

    def _cmd_decisions(self, arg: str):
        """View recent decision log entries."""
        log = DecisionLog(self.agent.workspace_root)
        count = int(arg) if arg and arg.isdigit() else 3
        entries = log.load_recent(count)
        if not entries:
            self.console.print("[yellow]No decision log entries found.[/yellow]")
            return
        self.console.print(f"[bold white]Last {len(entries)} decision(s):[/bold white]")
        for e in entries:
            self.console.print(Panel(
                log.format_entry(e),
                title=f"[cyan]{e.get('timestamp', '')}[/cyan]",
                border_style="cyan", expand=False
            ))

    def _cmd_monorepo(self):
        """Detect and display monorepo packages."""
        detector = MonorepoDetector(self.agent.workspace_root)
        packages = detector.detect_packages()
        if not packages:
            self.console.print("[yellow]No packages detected. This may be a single-package project.[/yellow]")
            return

        is_mono = detector.is_monorepo(packages)
        title = "[bold magenta]Monorepo Packages[/bold magenta]" if is_mono else "[bold white]Project Packages[/bold white]"
        self.console.print(f"\n{title} ({len(packages)} found):\n")

        for i, pkg in enumerate(packages, 1):
            rel = pkg.rel_path(self.agent.workspace_root)
            self.console.print(f"  [cyan]{i}.[/cyan] [bold]{pkg.name}[/bold]  [{pkg.lang}]  {rel}")
            cmds = detector.get_targeted_commands(pkg)
            if cmds:
                for action, cmd in cmds.items():
                    self.console.print(f"       [dim]{action}: {cmd}[/dim]")

    def _cmd_recent(self):
        """Show recently opened workspaces."""
        manager = WorkspaceAliasManager()
        manager.record_recent(self.agent.workspace_root)
        recents = manager.get_recent()
        if not recents:
            self.console.print("[yellow]No recent workspaces recorded.[/yellow]")
            return
        self.console.print("[bold white]Recent workspaces:[/bold white]")
        for i, r in enumerate(recents, 1):
            ts = r.get("timestamp", "")[:16]
            self.console.print(f"  [cyan]{i}.[/cyan] {r['path']}  [dim]{ts}[/dim]")
        self.console.print("\n[dim]Use /open <path> to switch workspace.[/dim]")

    def _cmd_alias(self, arg: str):
        """Manage workspace aliases: add, remove, list."""
        manager = WorkspaceAliasManager()
        parts = arg.split() if arg else []
        subcmd = parts[0] if parts else "list"

        if subcmd == "list" or not parts:
            aliases = manager.list_aliases()
            if not aliases:
                self.console.print("[yellow]No aliases defined. Use: /alias add <name> <path>[/yellow]")
            else:
                self.console.print("[bold white]Workspace aliases:[/bold white]")
                for name, path in aliases.items():
                    self.console.print(f"  [cyan]{name}[/cyan]  →  {path}")

        elif subcmd == "add":
            if len(parts) < 3:
                self.console.print("[red]Usage: /alias add <name> <path>[/red]")
                return
            name, path = parts[1], " ".join(parts[2:])
            result = manager.add_alias(name, path)
            self.console.print(f"[green]* {result}[/green]")

        elif subcmd == "remove":
            if len(parts) < 2:
                self.console.print("[red]Usage: /alias remove <name>[/red]")
                return
            result = manager.remove_alias(parts[1])
            self.console.print(f"[yellow]* {result}[/yellow]")
        else:
            self.console.print("[red]Usage: /alias [list|add <name> <path>|remove <name>][/red]")

    def _cmd_feature(self, arg: str):
        """Generate a vertical-slice feature plan."""
        if not arg:
            self.console.print("[red]Usage: /feature <description>[/red]")
            return
        self._ensure_repo_map()
        planner = FeaturePlanner(self.agent.workspace_root, self.agent.repo_map)
        self.console.print(f"[cyan]Planning feature: {arg}...[/cyan]")
        plan = planner.plan(arg, self.agent.model if self.agent.model.is_available() else None)
        self.console.print(Panel(plan, title=f"[bold green]Feature Plan: {arg}[/bold green]", border_style="green", expand=False))
        save = Confirm.ask("[bold yellow]Save plan to FEATURE_PLAN.md?[/bold yellow]")
        if save:
            res = self.agent.tools.write_file("FEATURE_PLAN.md", plan)
            self.console.print(f"[green]* {res}[/green]")

    def _cmd_scaffold_audit(self):
        """Audit recently changed files for scaffolding gaps."""
        changed = list(self.agent.checkpoint.current_task_files.keys())
        if not changed:
            # Fallback: git diff files
            import subprocess
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.agent.workspace_root,
                capture_output=True, text=True
            )
            changed = [f.strip() for f in r.stdout.splitlines() if f.strip()]

        if not changed:
            self.console.print("[yellow]No changed files found to audit.[/yellow]")
            return

        auditor = ScaffoldAuditor()
        rm = self.agent.repo_map if self.agent.repo_map.index else None
        findings = auditor.audit(changed, self.agent.workspace_root, rm)

        if not findings:
            self.console.print("[bold green]✓ No scaffold gaps found.[/bold green]")
            return

        self.console.print(f"[bold yellow]{len(findings)} scaffold issue(s) found:[/bold yellow]")
        for f in findings:
            sev_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(f["severity"], "white")
            self.console.print(f"  [{sev_color}][{f['severity'].upper()}][/{sev_color}] {f['file']}: {f['issue']}")

    def _cmd_docs_check(self):
        """Check which documentation files need updating."""
        changed = list(self.agent.checkpoint.current_task_files.keys())
        if not changed:
            import subprocess
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.agent.workspace_root,
                capture_output=True, text=True
            )
            changed = [f.strip() for f in r.stdout.splitlines() if f.strip()]

        checker = DocsChecker(self.agent.workspace_root)
        report = checker.check(changed)

        body = ""
        if report["affected_docs"]:
            body += "[bold white]Affected docs:[/bold white]\n"
            for d in report["affected_docs"]:
                body += f"  [cyan]{d}[/cyan]\n"
        if report["api_changes"]:
            body += "\n[bold white]API route changes:[/bold white]\n"
            for f in report["api_changes"]:
                body += f"  [yellow]{f}[/yellow]\n"
        if report["recommendations"]:
            body += "\n[bold white]Recommendations:[/bold white]\n"
            for r in report["recommendations"]:
                body += f"  • {r}\n"
        if not body:
            body = "[green]No documentation updates required.[/green]"

        self.console.print(Panel(body.strip(), title="[bold cyan]Docs Check[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_handoff(self, arg: str):
        """Generate and save a developer handoff report."""
        changed = list(self.agent.checkpoint.current_task_files.keys())
        commands = [e["command"] for e in self.agent.tools.execution_logs[-10:]]
        evidence = getattr(self.agent, "_task_evidence", [])
        test_results = ""
        for log in reversed(self.agent.tools.execution_logs):
            if "pytest" in log["command"] or "test" in log["command"]:
                test_results = f"Exit {log['exit_code']}: {log.get('stdout','')[:300]}"
                break

        gen = HandoffGenerator(self.agent.workspace_root)
        desc = arg or "Task completed via Ultron"
        report = gen.generate(
            task_description=desc,
            changed_files=changed,
            commands_run=commands,
            test_results=test_results,
            risks=[],
            limitations=[],
            next_steps=["Review changes", "Run full test suite", "Update documentation if needed"],
            decisions=[str(e) for e in evidence[:5]],
        )
        self.console.print(Panel(report[:2000], title="[bold green]Handoff Report[/bold green]", border_style="green", expand=False))
        path = gen.save(report)
        self.console.print(f"[green]* Saved to: {path}[/green]")

    def _cmd_doctor(self):
        """Run environment diagnostics using HealthMonitor."""
        from ultron.health_monitor import HealthMonitor
        monitor = HealthMonitor(self.agent.workspace_root, self.agent)
        checks = monitor.check_all()
        overall = monitor.overall_status(checks)

        color_map = {"ok": "green", "warn": "yellow", "degraded": "red", "error": "red"}
        icon_map  = {"ok": "✓", "warn": "⚠", "degraded": "✗", "error": "✗"}

        self.console.print(f"\n[bold white]Environment Diagnostics:[/bold white]  Overall: [{color_map.get(overall.lower(), 'white')}]{overall}[/{color_map.get(overall.lower(), 'white')}]\n")
        for c in checks:
            color = color_map.get(c.status, "white")
            icon  = icon_map.get(c.status, "•")
            self.console.print(f"  [{color}]{icon}[/{color}] [bold]{c.component}[/bold]: {c.detail}")

    def _cmd_probe(self):
        """Probe active model capabilities and update its profile."""
        from ultron.capability_probe import CapabilityProbe
        if not self.agent.model.is_available():
            self.console.print("[red]Model not available. Cannot probe.[/red]")
            return
        self.console.print(f"[cyan]Probing {self.agent.model.model_name}...[/cyan]")
        probe = CapabilityProbe(self.agent.model, self.console)
        results = probe.probe_all()
        profile = probe.update_profile(results)

        from rich.table import Table as RT
        table = RT(show_header=True, header_style="bold white")
        table.add_column("Capability", style="cyan")
        table.add_column("Result", width=8)
        table.add_column("Reliability", width=12)
        table.add_column("Latency", width=10)
        for cap, r in results.items():
            icon = "[green]✓[/green]" if r.passed else "[red]✗[/red]"
            table.add_row(cap, icon, f"{r.reliability:.0%}", f"{r.latency:.2f}s")
        self.console.print(table)
        self.console.print(f"[green]* Profile saved for {profile.provider}/{profile.model}[/green]")

    def _cmd_route_info(self, arg: str):
        """Show routing rationale for a given intent."""
        from ultron.model_router import ModelRouter
        router = ModelRouter()
        intent = arg.strip() or (self.agent.current_task.intent.value if self.agent.current_task else "unknown")
        info = router.describe_routing(intent)
        self.console.print(Panel(
            "\n".join(f"[bold white]{k}:[/bold white] {v}" for k, v in info.items()),
            title=f"[bold cyan]Routing: {intent}[/bold cyan]",
            border_style="cyan", expand=False
        ))

    def _cmd_health(self):
        """Run health analysis on the workspace."""
        self._ensure_repo_map()
        analyzer = HealthAnalyzer()
        self.console.print("[cyan]Running health analysis...[/cyan]")
        findings = analyzer.analyze_workspace(self.agent.workspace_root, self.agent.repo_map)

        if not findings:
            self.console.print("[bold green]✓ No health issues found.[/bold green]")
            return

        self.console.print(f"[bold yellow]{len(findings)} health issue(s):[/bold yellow]")
        by_type: Dict[str, list] = {}
        for f in findings:
            by_type.setdefault(f["type"], []).append(f)

        for ftype, items in by_type.items():
            self.console.print(f"\n  [bold white]{ftype.replace('_', ' ').title()}[/bold white] ({len(items)})")
            for item in items[:5]:
                sev_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(item["severity"], "white")
                self.console.print(f"    [{sev_color}]{item['file']}:{item['line']}[/{sev_color}]  {item['detail'][:80]}")
            if len(items) > 5:
                self.console.print(f"    [dim]... {len(items)-5} more[/dim]")

    def _cmd_release_check(self):
        """Run release readiness checklist."""
        checker = ReleaseChecker(self.agent.workspace_root)
        items = checker.check()
        self.console.print("[bold white]Release Readiness Checklist:[/bold white]\n")
        all_ok = True
        for item in items:
            color = {"ok": "green", "warn": "yellow", "error": "red", "info": "cyan"}.get(item["status"], "white")
            icon = {"ok": "✓", "warn": "⚠", "error": "✗", "info": "i"}.get(item["status"], "•")
            if item["status"] in ("warn", "error"):
                all_ok = False
            self.console.print(f"  [{color}]{icon}[/{color}] [bold]{item['item']}[/bold]: {item['detail']}")
        self.console.print()
        if all_ok:
            self.console.print("[bold green]✓ Project looks release-ready.[/bold green]")
        else:
            self.console.print("[bold yellow]⚠ Address warnings before releasing.[/bold yellow]")

    # ----------------------------------------------------------------
    # Model Hub command implementations
    # ----------------------------------------------------------------

    def _cmd_models(self):
        """Interactive provider + model picker."""
        registry = self.agent.provider_registry
        provider = registry.interactive_pick(self.console)
        if provider:
            # Update agent.model for backward compat with existing code
            self.agent.model = provider
            self.console.print(f"[green]* Agent now using: {provider.provider_name} / {provider.model_name}[/green]")

    def _cmd_model(self, arg: str):
        """Switch model on the active provider, or show current."""
        if not arg:
            p = self.agent.model
            name = getattr(p, "provider_name", "Ollama")
            model = getattr(p, "model_name", getattr(p, "model_name", "?"))
            self.console.print(f"[bold white]Active model:[/bold white] [cyan]{name}[/cyan] / [cyan]{model}[/cyan]")
            self.console.print("[dim]Use /models to switch provider/model.[/dim]")
            return
        # Try to set model on current provider
        try:
            self.agent.model.model_name = arg
            self.console.print(f"[green]* Model set to: {arg}[/green]")
        except Exception as e:
            self.console.print(f"[red]Could not set model: {e}[/red]")

    def _cmd_model_info(self):
        """Show capability info for the active model."""
        p = self.agent.model
        name = getattr(p, "provider_name", "Ollama")
        model = getattr(p, "model_name", "?")
        try:
            caps = p.capabilities()
            body = (
                f"[bold white]Provider:[/bold white]       {name}\n"
                f"[bold white]Model:[/bold white]          {model}\n"
                f"[bold white]Context window:[/bold white] {caps.context_window:,} tokens\n"
                f"[bold white]Streaming:[/bold white]      {'Yes' if caps.streaming else 'No'}\n"
                f"[bold white]Native tools:[/bold white]   {'Yes' if caps.native_tools else 'No (fallback active)'}\n"
                f"[bold white]Vision:[/bold white]         {'Yes' if caps.vision else 'No'}\n"
                f"[bold white]Max output:[/bold white]     {caps.max_output_tokens:,} tokens"
            )
        except Exception:
            body = f"[bold white]Provider:[/bold white] {name}\n[bold white]Model:[/bold white] {model}"
        self.console.print(Panel(body, title="[bold cyan]Model Info[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_provider(self, arg: str):
        """Manage providers: status, add, remove."""
        from ultron.providers.credential_store import get_key, delete_key, has_key, mask_key
        parts = arg.split() if arg else []
        subcmd = parts[0] if parts else "status"

        if subcmd == "status" or not parts:
            registry = self.agent.provider_registry
            statuses = registry.connection_status()
            self.console.print("\n[bold white]Provider Status:[/bold white]\n")
            for s in statuses:
                if s["active"]:
                    tag = "[bold green]● ACTIVE[/bold green]"
                elif s["connected"]:
                    tag = "[green]✓ connected[/green]"
                elif s["has_key"]:
                    tag = "[yellow]⚠ key stored, offline[/yellow]"
                else:
                    tag = "[dim]○ not configured[/dim]"
                key_info = f"  [dim]{s.get('key_masked','')}[/dim]" if s.get("key_masked") else ""
                models_info = f"  [dim]{s.get('model_count','')} models[/dim]" if s.get("model_count") else ""
                self.console.print(f"  {tag}  [bold]{s['name']}[/bold]{key_info}{models_info}")

        elif subcmd == "add":
            if len(parts) < 3:
                self.console.print("[red]Usage: /provider add <provider_id> <api_key>[/red]")
                self.console.print("[dim]Provider IDs: openai, anthropic, groq, gemini, openrouter[/dim]")
                return
            provider_id = parts[1].lower()
            api_key = parts[2]
            from ultron.providers.credential_store import store_key
            if store_key(provider_id, api_key):
                self.console.print(f"[green]* Key for '{provider_id}' saved to OS keyring.[/green]")
            else:
                self.console.print(f"[red]Failed to save key. Is keyring available?[/red]")

        elif subcmd == "remove":
            if len(parts) < 2:
                self.console.print("[red]Usage: /provider remove <provider_id>[/red]")
                return
            provider_id = parts[1].lower()
            if delete_key(provider_id):
                self.console.print(f"[yellow]* Key for '{provider_id}' removed from keyring.[/yellow]")
            else:
                self.console.print(f"[red]No key found for '{provider_id}'.[/red]")
        else:
            self.console.print("[red]Usage: /provider [status|add <id> <key>|remove <id>][/red]")

    def _cmd_fallback(self, arg: str):
        """Set or show the fallback model."""
        registry = self.agent.provider_registry
        if not arg:
            fb = registry._fallback
            if fb:
                self.console.print(f"[bold white]Fallback:[/bold white] [cyan]{fb.provider_name}[/cyan] / [cyan]{fb.model_name}[/cyan]")
            else:
                self.console.print("[yellow]No fallback configured.[/yellow]")
            return

        # arg = "ollama" or "ollama/model_name"
        parts = arg.split("/", 1)
        pid = parts[0].lower()
        mname = parts[1] if len(parts) > 1 else ""

        from ultron.providers.registry import _build_provider, PROVIDER_CATALOG
        catalog_entry = next((p for p in PROVIDER_CATALOG if p["id"] == pid), None)
        if not catalog_entry:
            self.console.print(f"[red]Unknown provider '{pid}'. Valid: ollama, groq, anthropic, openai, gemini, openrouter[/red]")
            return

        model = mname or catalog_entry["default_model"]
        provider = _build_provider(pid, model)
        if not provider:
            self.console.print(f"[red]Could not build provider '{pid}'. Is key configured?[/red]")
            return

        registry.set_fallback(provider, pid)
        self.console.print(f"[green]* Fallback set to: {provider.provider_name} / {model}[/green]")

    def _cmd_trace(self, arg: str):
        """Trace a symbol's flow through architectural layers."""
        if not arg:
            self.console.print("[red]Usage: /trace <symbol>[/red]")
            return
        self._ensure_repo_map()
        tracer = FeatureTracer(self.agent.workspace_root, self.agent.repo_map)
        self.console.print(f"[cyan]Tracing '{arg}' through codebase layers...[/cyan]")
        result = tracer.trace(arg)

        if "error" in result:
            self.console.print(f"[red]{result['error']}[/red]")
            return

        flow = " → ".join(result["flow_path"]) if result["flow_path"] else "unknown"
        body = f"[bold white]Symbol:[/bold white] [cyan]{arg}[/cyan]\n"
        body += f"[bold white]Flow:[/bold white] {flow}\n\n"

        for layer, files in result["layers"].items():
            body += f"[bold white]{layer.title()}:[/bold white]\n"
            for f in files[:5]:
                body += f"  [green]{f['file']}[/green]\n"

        if result["related_tests"]:
            body += f"\n[bold white]Tests:[/bold white]\n"
            for t in result["related_tests"][:5]:
                body += f"  [magenta]{t}[/magenta]\n"

        body += f"\n[dim]Total references: {result['total_references']}[/dim]"
        self.console.print(Panel(body.strip(), title=f"[bold cyan]Trace: {arg}[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_compare(self, arg: str):
        """Compare current branch to another branch."""
        comparer = BranchComparer(self.agent.workspace_root)
        current = comparer.current_branch()

        if not arg:
            branches = comparer.list_branches()
            self.console.print(f"\n[bold white]Current branch:[/bold white] [cyan]{current}[/cyan]")
            self.console.print("[bold white]Available branches:[/bold white]")
            for i, b in enumerate(branches[:15], 1):
                active = " [bold yellow]← current[/bold yellow]" if b == current else ""
                self.console.print(f"  [cyan]{i}.[/cyan] {b}{active}")
            self.console.print("\n[dim]Usage: /compare <base-branch>[/dim]")
            return

        base = arg.strip()
        self.console.print(f"[cyan]Comparing {base} → {current}...[/cyan]")
        result = comparer.compare(base, current)

        body = (
            f"[bold white]Base:[/bold white]    {result['base']}\n"
            f"[bold white]Target:[/bold white]  {result['target']}\n"
            f"[bold white]Files changed:[/bold white] {result['file_count']}\n\n"
            f"[bold white]Stat:[/bold white]\n{result['stat'] or '(no diff)'}\n"
        )

        if result["diverged_commits"]:
            body += f"\n[bold white]Commits in {current} not in {base}:[/bold white]\n"
            for c in result["diverged_commits"][:10]:
                body += f"  [dim]{c}[/dim]\n"

        if result["changed_files"]:
            body += f"\n[bold white]Changed files:[/bold white]\n"
            for f in result["changed_files"][:20]:
                body += f"  [green]{f}[/green]\n"
            if len(result["changed_files"]) > 20:
                body += f"  [dim]... {len(result['changed_files'])-20} more[/dim]\n"

        self.console.print(Panel(body.strip(), title=f"[bold cyan]Compare: {base} ↔ {current}[/bold cyan]", border_style="cyan", expand=False))

    def _cmd_flaky_test(self, arg: str):
        """Re-run a test multiple times to detect flakiness."""
        if not arg:
            # Try to get test command from project memory
            mem = self.memory_manager.load_memory()
            cmd = mem.get("commands", {}).get("test", {}).get("cmd", "")
            if not cmd:
                self.console.print("[red]Usage: /flaky-test <test-command>[/red]")
                self.console.print("[dim]Example: /flaky-test pytest tests/test_auth.py -v[/dim]")
                return
            arg = cmd

        from rich.prompt import Prompt
        runs_str = Prompt.ask("[bold yellow]How many runs?[/bold yellow]", default="5")
        runs = int(runs_str) if runs_str.isdigit() else 5

        self.console.print(f"[cyan]Running '{arg}' {runs} times to check for flakiness...[/cyan]")
        detector = FlakyTestDetector(self.agent.workspace_root)
        result = detector.run_multiple(arg, runs=runs)

        # Build results table
        from rich.table import Table as RichTable
        table = RichTable(show_header=True, header_style="bold white")
        table.add_column("Run", style="cyan", width=5)
        table.add_column("Result", width=10)
        table.add_column("Exit Code", width=10)

        for r in result["results"]:
            status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
            table.add_row(str(r["run"]), status, str(r["exit_code"]))

        self.console.print(table)

        color = "red" if result["flaky_detected"] else "green"
        icon = "⚠" if result["flaky_detected"] else "✓"
        self.console.print(f"\n[{color}]{icon} {result['variation']} — {result['recommendation']}[/{color}]")

    def _cmd_metrics(self):
        """Show comprehensive task metrics dashboard (session + historical)."""
        from ultron.eval_suite import MetricsCollector
        collector = MetricsCollector(self.agent.workspace_root)
        # Merge session metrics into collector for full picture
        for m in self.agent.metrics_collector.session_metrics:
            collector.session_metrics.append(m)

        summary = collector.compute_summary()
        history = collector.load_history(50)

        if not summary and not history:
            self.console.print("[yellow]No task metrics recorded yet. Run some tasks first.[/yellow]")
            return

        def pct(v): return f"{v*100:.0f}%" if v is not None else "n/a"

        body = (
            f"[bold white]Total tasks:[/bold white]           {summary.get('total_tasks', 0)}\n"
            f"[bold white]Completion rate:[/bold white]       {pct(summary.get('task_completion_rate'))}\n"
            f"[bold white]Avg tool calls:[/bold white]        {summary.get('avg_tool_calls_per_task', 0)}\n"
            f"[bold white]Avg duration:[/bold white]          {summary.get('avg_duration_seconds', 0):.1f}s\n"
            f"[bold white]Unverified rate:[/bold white]       {pct(summary.get('unverified_claim_rate'))}\n"
            f"[bold white]Unsafe actions:[/bold white]        {summary.get('total_unsafe_actions', 0)}\n"
            f"[bold white]Approval friction:[/bold white]     {summary.get('approval_friction', 0):.1f} approvals/task\n"
            f"[bold white]Context overflow rate:[/bold white] {pct(summary.get('context_overflow_rate'))}\n"
        )

        body += "\n[bold white]Recent tasks:[/bold white]\n"
        for m in (history or [])[-10:]:
            status = "[green]✓[/green]" if m.get("success") else "[red]✗[/red]"
            ts = m.get("timestamp", "")[:16]
            intent = m.get("intent", "?")
            prompt = m.get("prompt", "")[:55]
            body += f"  {status} [{intent}] {prompt}  [dim]{ts}[/dim]\n"

        self.console.print(Panel(
            body.strip(),
            title="[bold cyan]Task Metrics Dashboard[/bold cyan]",
            border_style="cyan", expand=False
        ))

    def _cmd_session_log(self, arg: str):
        """Show today's session log or recent activity."""
        from ultron.session_log import SessionLogger
        logger = SessionLogger(self.agent.workspace_root)
        days = int(arg) if arg and arg.isdigit() else 1
        entries = logger.load_today() if days == 1 else logger.load_recent(days)

        if not entries:
            self.console.print("[yellow]No session log entries found.[/yellow]")
            return

        summary = logger.summarize(entries)
        self.console.print(Panel(
            f"[bold white]Total tasks:[/bold white]       {summary['total_tasks']}\n"
            f"[bold white]Tool calls:[/bold white]        {summary['total_tool_calls']}\n"
            f"[bold white]Model calls:[/bold white]       {summary['total_model_calls']}\n"
            f"[bold white]Failed tools:[/bold white]      {summary['failed_tool_calls']}\n"
            f"[bold white]Tasks verified:[/bold white]    {summary['tasks_completed']}\n"
            f"[bold white]Top tools:[/bold white]         "
            + ", ".join(f"{k}({v})" for k, v in sorted(summary['tool_usage'].items(), key=lambda x: -x[1])[:5]),
            title=f"[bold cyan]Session Log ({days} day(s))[/bold cyan]",
            border_style="cyan", expand=False
        ))

        # Show last 10 entries
        self.console.print("\n[bold white]Recent events:[/bold white]")
        for e in entries[-10:]:
            ts = e.get("timestamp", "")[:19]
            ev = e.get("event", "?")
            detail = ""
            if ev == "tool_call":
                detail = f"[green]{e.get('tool')}[/green] → exit {e.get('exit_code')}"
            elif ev == "task_start":
                detail = f"[cyan]{e.get('intent')}[/cyan]: {e.get('prompt','')[:50]}"
            elif ev == "task_end":
                color = "green" if e.get("status") == "verified" else "yellow"
                detail = f"[{color}]{e.get('status')}[/{color}] — {', '.join(e.get('files_changed', []))}"
            elif ev == "model_call":
                detail = f"[magenta]{e.get('provider')}/{e.get('model')}[/magenta]"
            self.console.print(f"  [dim]{ts}[/dim]  {ev}: {detail}")

    def _cmd_plugins(self):
        """List loaded plugins."""
        from ultron.plugin_loader import discover_plugins, PLUGIN_DIR
        plugins = discover_plugins()
        if not plugins:
            self.console.print(f"[yellow]No plugins found in {PLUGIN_DIR}[/yellow]")
            self.console.print("[dim]To add a plugin, place a .py file in ~/.ultron/plugins/[/dim]")
            self.console.print("[dim]Run: python -c \"from ultron.plugin_loader import create_plugin_template; print(create_plugin_template('my_plugin'))\"[/dim]")
            return
        self.console.print(f"[bold white]Plugins in {PLUGIN_DIR}:[/bold white]")
        for p in plugins:
            name = os.path.basename(p)
            self.console.print(f"  [green]{name}[/green]  {p}")

    def _cmd_context_status(self):
        """Show current context window usage vs provider limit."""
        try:
            caps = self.agent.model.capabilities()
            context_content = self.agent.context.build_context_prompt()
            sys_msg = self.agent._get_system_message()
            full_text = sys_msg.get("content", "")
            char_count = len(full_text)
            token_estimate = char_count // 4
            limit = caps.context_window
            ratio = token_estimate / limit if limit > 0 else 0
            color = "green" if ratio < 0.5 else "yellow" if ratio < 0.75 else "red"
            bar_width = 30
            filled = int(ratio * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)

            body = (
                f"[bold white]Provider:[/bold white]      {getattr(self.agent.model, 'provider_name', 'Ollama')}\n"
                f"[bold white]Model:[/bold white]         {self.agent.model.model_name}\n"
                f"[bold white]Context limit:[/bold white] {limit:,} tokens\n"
                f"[bold white]Estimated used:[/bold white] ~{token_estimate:,} tokens ({ratio*100:.0f}%)\n"
                f"[bold white]Usage:[/bold white]         [{color}]{bar}[/{color}] {ratio*100:.0f}%\n"
                f"[bold white]Pinned files:[/bold white]  {len(self.agent.context.pinned_files)}\n"
                f"\n[dim]Tip: /drop files or /clear to free context.[/dim]"
            )
            self.console.print(Panel(body, title="[bold cyan]Context Status[/bold cyan]", border_style="cyan", expand=False))
        except Exception as e:
            self.console.print(f"[red]Error checking context: {e}[/red]")

    def _cmd_self_repair(self):
        """Run Ultron self-repair loop."""
        from ultron.recovery_bootstrap import detect_damage
        from ultron.self_repair import SelfRepairEngine
        self.console.print("[cyan]Checking for damage...[/cyan]")
        damage = detect_damage(self.agent.workspace_root)
        if not damage:
            self.console.print("[bold green]✓ No damage detected. Ultron is healthy.[/bold green]")
            return
        self.console.print(f"[bold red]{len(damage)} issue(s) detected:[/bold red]")
        for d in damage:
            self.console.print(f"  [red]- {d}[/red]")
        if not Confirm.ask("[bold yellow]Attempt self-repair?[/bold yellow]"):
            return
        engine = SelfRepairEngine(self.agent.workspace_root, self.agent.model, self.console)
        result = engine.run(damage)
        if result["final_status"] == "recovered":
            self.console.print("[bold green]✓ Self-repair successful.[/bold green]")
        else:
            self.console.print(f"[bold red]Self-repair failed. Rolled back: {result.get('rolled_back')}[/bold red]")

    def _cmd_known_good(self, arg: str):
        """Record or show the known-good version."""
        from ultron.known_good import get_known_good, record_known_good_from_current, is_current_known_good
        if arg.strip() == "record":
            msg = record_known_good_from_current(self.agent.workspace_root)
            self.console.print(f"[green]* {msg}[/green]")
        else:
            known = get_known_good()
            if not known:
                self.console.print("[yellow]No known-good record. Run: /known-good record[/yellow]")
                return
            current = is_current_known_good(self.agent.workspace_root)
            status = "[green]✓ current[/green]" if current else "[yellow]⚠ differs from current HEAD[/yellow]"
            self.console.print(Panel(
                f"[bold white]Commit:[/bold white] {known.get('commit','?')[:16]}\n"
                f"[bold white]Recorded:[/bold white] {known.get('timestamp','?')[:19]}\n"
                f"[bold white]Tests passed:[/bold white] {known.get('tests_passed',0)}\n"
                f"[bold white]Status:[/bold white] {status}",
                title="[bold cyan]Known-Good Version[/bold cyan]",
                border_style="cyan", expand=False,
            ))

    def _cmd_replay(self, arg: str):
        """View task replay timeline. /replay [task_id|list]"""
        from ultron.task_replay import TaskReplay
        replay = TaskReplay(self.agent.workspace_root)
        if not arg or arg == "list":
            records = replay.list_recent(10)
            if not records:
                self.console.print("[yellow]No replay records found.[/yellow]")
                return
            self.console.print("[bold white]Recent task replays:[/bold white]")
            for r in records:
                status_color = "green" if r.get("final_status") == "verified" else "yellow"
                self.console.print(
                    f"  [cyan]{r['task_id']}[/cyan]  [{status_color}]{r.get('final_status','?')}[/{status_color}]"
                    f"  {r.get('intent','?')}  {r.get('started_at','?')[:16]}"
                    f"  [dim]{r.get('prompt','')[:50]}[/dim]"
                )
            self.console.print("[dim]Use /replay <task_id> to see full timeline.[/dim]")
        else:
            record = replay.load(arg.strip())
            if not record:
                self.console.print(f"[red]No replay found for task ID: {arg}[/red]")
                return
            timeline = replay.format_timeline(record)
            self.console.print(Panel(timeline, title=f"[bold cyan]Replay: {arg}[/bold cyan]",
                                     border_style="cyan", expand=False))

    def _cmd_notify_config(self, arg: str):
        """Configure email notifications. /notify-config <email> <smtp_host> [port]"""
        from ultron.notifications import NotificationManager
        parts = arg.split() if arg else []
        if len(parts) < 2:
            self.console.print("[red]Usage: /notify-config <email> <smtp_host> [port][/red]")
            self.console.print("[dim]Example: /notify-config you@example.com smtp.gmail.com 587[/dim]")
            return
        email, smtp_host = parts[0], parts[1]
        port = int(parts[2]) if len(parts) > 2 else 587
        NotificationManager.configure_email(email, smtp_host, smtp_port=port)
        self.console.print(f"[green]* Notifications configured → {email} via {smtp_host}:{port}[/green]")
        self.console.print("[dim]Note: SMTP password stored in ~/.ultron/settings.json (not in git)[/dim]")

    def _cmd_audit(self, arg: str):
        """Show audit event log. /audit [days]"""
        from ultron.audit import AuditLogger
        days = int(arg) if arg and arg.isdigit() else 1
        logger = AuditLogger(self.agent.workspace_root)
        entries = logger.load_recent(days) if days > 1 else logger.load_today()

        if not entries:
            self.console.print("[yellow]No audit entries found.[/yellow]")
            return

        # Group by event type for summary
        from collections import Counter
        counts = Counter(e.get("event_type", "?") for e in entries)
        summary = "\n".join(f"  {k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:10])

        self.console.print(Panel(
            f"[bold white]Total events:[/bold white] {len(entries)}\n\n"
            f"[bold white]By type:[/bold white]\n{summary}",
            title=f"[bold cyan]Audit Log ({days} day(s))[/bold cyan]",
            border_style="cyan", expand=False,
        ))

        # Show last 10 notable events (denies, violations, errors)
        notable = [e for e in entries if any(
            kw in e.get("event_type", "") for kw in ["DENIED", "VIOLATION", "ERROR", "BLOCKED", "SECRET"]
        )]
        if notable:
            self.console.print(f"\n[bold yellow]Notable events ({len(notable)}):[/bold yellow]")
            for e in notable[-10:]:
                ts = e.get("timestamp", "")[:19]
                et = e.get("event_type", "?")
                reason = e.get("reason", "")[:60]
                self.console.print(f"  [dim]{ts}[/dim]  [red]{et}[/red]: {reason}")

    def start(self):
        """Starts the interactive prompt loop."""
        self.console.print("[cyan]Interactive session loaded. Type [/cyan][magenta]/help[/magenta][cyan] to view system commands.[/cyan]\n")
        
        while True:
            try:
                # Prompt bar shows active mode
                prompt_str = self._get_mode_prompt()
                user_input = self.session.prompt(prompt_str, style=self.prompt_style).strip()
                if not user_input:
                    continue
                    
                if user_input.startswith("/"):
                    should_continue = self.handle_slash_command(user_input)
                    if not should_continue:
                        break
                else:
                    self.last_user_prompt = user_input
                    self.agent._stop_requested = False
                    self.agent.run(user_input)
                    self.last_task_mutated = bool(self.agent.checkpoint.current_task_files)

            except KeyboardInterrupt:
                self.console.print("\n[bold red]⚡ Interrupted.[/bold red]")

                # 1. Kill any running subprocess
                if self.agent.tools.current_process:
                    self.console.print("[red]Stopping subprocess...[/red]")
                    self.agent.tools.terminate_current_process()

                # 2. Mark current task as cancelled
                if self.agent.current_task:
                    from ultron.task import TaskStatus
                    self.agent.current_task.status = TaskStatus.CANCELLED
                    self.console.print(f"[yellow]Task [{self.agent.current_task.id}] cancelled.[/yellow]")

                # 3. Signal streaming loop to stop on next iteration
                self.agent._stop_requested = True

                self.console.print("[dim]Type a new prompt to continue, or /exit to quit.[/dim]")
            except EOFError:
                # Ctrl+D exits
                self.console.print("\n[bold magenta]Goodbye. Ultron shutting down.[/bold magenta]")
                break
            except Exception as e:
                self.console.print(f"\n[red]REPL Error: {str(e)}[/red]")
