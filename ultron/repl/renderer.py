"""
renderer.py - Rich console rendering & UI panel helpers for Ultron REPL.
"""
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class REPLRenderer:
    def __init__(self, console: Console = None):
        self.console = console or Console()

    def print_welcome_banner(self, workspace_root: str, model_name: str):
        self.console.print(Panel(
            f"[bold cyan]Ultron AI Assistant[/bold cyan]\n"
            f"[dim]Workspace:[/dim] {workspace_root}\n"
            f"[dim]Model:[/dim] {model_name}\n"
            f"Type [bold green]/help[/bold green] for commands, [bold red]/exit[/bold red] to quit.",
            title="Ultron v0.1.0",
            border_style="magenta"
        ))

    def print_help(self):
        table = Table(title="Ultron REPL Commands", border_style="blue")
        table.add_column("Command", style="cyan")
        table.add_column("Description", style="white")

        table.add_row("/mode [mode]", "Switch mode (ask, plan, build, fix, review)")
        table.add_row("/verify", "Run verification suite (tests, lint, format)")
        table.add_row("/diff", "View git diff of workspace changes")
        table.add_row("/commit [msg]", "Commit staged workspace changes")
        table.add_row("/models", "List available Ollama/Cloud models")
        table.add_row("/help", "Display this help menu")
        table.add_row("/exit", "Exit Ultron REPL")

        self.console.print(table)
