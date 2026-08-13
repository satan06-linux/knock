import sys
import click
from rich.console import Console
from rich.panel import Panel

from ultron.agent import UltronAgent
from ultron.repl import UltronREPL

BANNER = """
[bold magenta]
  _    _ _   _______ _____   ____  _   _ 
 | |  | | | |__   __|  __ \\ / __ \\| \\ | |
 | |  | | |    | |  | |__) | |  | |  \\| |
 | |  | | |    | |  |  _  /| |  | | . ` |
 | |__| | |____| |  | | \\ \\| |__| | |\\  |
  \\____/|______|_|  |_|  \\_\\\\____/|_| \\_|
[/bold magenta]
[bold cyan]   >> LOCAL AI CODING AGENT // VERSION 0.1.0 << [/bold cyan]
"""

@click.command()
@click.option("--model", default="qwen2.5-coder:7b", help="Ollama model to use (default: qwen2.5-coder:7b)")
@click.option("--base-url", default="http://localhost:11434", help="Ollama API base URL (default: http://localhost:11434)")
@click.option("--yes", is_flag=True, help="Auto-approve all tool runs and file writes")
@click.option("--auto-commit", is_flag=True, help="Automatically commit successful changes to Git")
def main(model: str, base_url: str, yes: bool, auto_commit: bool):
    """
    Ultron CLI - An advanced, terminal-based AI coding assistant for engineering projects.
    """
    console = Console()
    console.clear()
    console.print(BANNER)
    
    # 1. Initialize Agent
    with console.status("[cyan]Initializing Ultron agent...[/cyan]") as status:
        agent = UltronAgent(
            workspace_root=".",
            model_name=model,
            auto_approve=yes,
            auto_commit=auto_commit
        )
        # Update model client URL if user provided custom one
        agent.model.base_url = base_url.rstrip("/")
        
        # 2. Check model availability
        if not agent.model.is_available():
            status.stop()
            console.print(Panel(
                f"[bold red]Ollama model '{model}' was not found or Ollama is offline.[/bold red]\n\n"
                f"Please ensure:\n"
                f"1. Ollama is running localy (base URL: {base_url})\n"
                f"2. You have downloaded the model using: [bold yellow]ollama pull {model}[/bold yellow]",
                title="Model Offline Error",
                border_style="red"
            ))
            sys.exit(1)
            
    console.print(f"[green]* Connected to Ollama using model: [bold]{model}[/bold][/green]\n")
    console.print(agent.get_status_dashboard())
    console.print()

    # 3. Start REPL
    repl = UltronREPL(agent)
    repl.start()

if __name__ == "__main__":
    main()
