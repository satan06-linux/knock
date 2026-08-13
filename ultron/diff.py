"""
diff.py - Compact diff display for Ultron.
Shows a minimal summary: filename  +N -N  with changed lines listed.
"""
import difflib
from rich.panel import Panel
from rich.text import Text


def generate_diff_panel(file_path: str, old_content: str, new_content: str) -> Panel:
    """
    Generate a compact, readable diff panel.
    Shows: filename  +N -N  then up to 20 changed lines with + / - prefix.
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(old_lines, new_lines, n=0))

    if not diff:
        return Panel(
            Text("No changes.", style="yellow"),
            title=f"[bold blue]{file_path}[/bold blue]",
            border_style="yellow",
            expand=False,
        )

    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    rich_text = Text()

    # Summary line
    adds_str = f"[bold green]+{added}[/bold green]" if added else ""
    dels_str = f"[bold red]-{removed}[/bold red]" if removed else ""
    spacer = "  " if added and removed else ""
    rich_text.append(f"{file_path}  ", style="bold white")
    rich_text.append_text(Text.from_markup(f"{adds_str}{spacer}{dels_str}\n"))

    # Show changed lines only (skip headers)
    shown = 0
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        clean = line.rstrip("\r\n")
        if line.startswith("+"):
            rich_text.append(f"  {clean}\n", style="green")
        elif line.startswith("-"):
            rich_text.append(f"  {clean}\n", style="red")
        shown += 1
        if shown >= 20:
            remaining = sum(
                1 for l in diff
                if (l.startswith("+") or l.startswith("-"))
                and not l.startswith("+++") and not l.startswith("---")
            ) - shown
            if remaining > 0:
                rich_text.append(f"  ... {remaining} more line(s)\n", style="dim")
            break

    return Panel(
        rich_text,
        title=f"[bold blue]{file_path}[/bold blue]",
        border_style="cyan",
        expand=False,
        padding=(0, 1),
    )
