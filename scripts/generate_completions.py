"""
generate_completions.py - Generate shell completion scripts for Ultron CLI.

Usage:
    python scripts/generate_completions.py bash   > ~/.bash_completion.d/ultron
    python scripts/generate_completions.py zsh    > ~/.zsh/completions/_ultron
    python scripts/generate_completions.py fish   > ~/.config/fish/completions/ultron.fish

Install all:
    python scripts/generate_completions.py --install
"""
import sys
import os

# All Ultron slash commands with descriptions
COMMANDS = [
    ("/add", "Pin a file to the prompt context"),
    ("/drop", "Remove a file from context"),
    ("/files", "List pinned context files"),
    ("/run", "Execute a terminal command"),
    ("/diff", "Show git diff (staged + unstaged)"),
    ("/commit", "AI-generated git commit"),
    ("/undo", "Revert last Ultron task"),
    ("/workspace", "Show workspace status"),
    ("/tree", "Show directory tree"),
    ("/refresh", "Refresh project index"),
    ("/status", "Show status dashboard"),
    ("/logs", "View command execution logs"),
    ("/last-error", "Show last execution failure"),
    ("/repeat", "Repeat last prompt"),
    ("/cancel", "Cancel active task"),
    ("/onboard", "Detect project framework and commands"),
    ("/plan", "Generate implementation plan"),
    ("/tasks", "Task checklist management"),
    ("/test", "Run test command"),
    ("/lint", "Run lint command"),
    ("/fix", "Auto-fix last error"),
    ("/clear", "Reset agent memory"),
    ("/help", "Show all commands"),
    ("/exit", "Quit Ultron"),
    ("/quit", "Quit Ultron"),
    # Phase 2
    ("/analyze", "Build repository map"),
    ("/find-folder", "Search for a folder"),
    ("/open", "Switch workspace"),
    ("/find", "Text search across files"),
    ("/symbol", "Find symbol definitions"),
    ("/references", "Find all references to a symbol"),
    ("/flow", "Trace symbol flow"),
    ("/explain", "AI explanation of code"),
    ("/impact", "Impact analysis"),
    ("/why", "Investigate failure"),
    ("/min-repro", "Generate minimal reproduction"),
    ("/init-project", "Create ULTRON.md and .ultron.toml"),
    # Phase 3
    ("/mode", "Set intent mode"),
    ("/contract", "Show active change contract"),
    ("/verify", "Run verification checks"),
    ("/review", "Code review"),
    ("/reproduce", "Save bug reproduction package"),
    ("/bisect", "Guided git bisect"),
    # Phase 4
    ("/worktree", "Manage git worktrees"),
    ("/pr-summary", "Generate PR summary"),
    ("/commit-check", "Check commit message quality"),
    ("/decisions", "View decision log"),
    ("/monorepo", "Detect monorepo packages"),
    ("/recent", "Recent workspaces"),
    ("/alias", "Manage workspace aliases"),
    ("/feature", "Vertical-slice feature plan"),
    ("/scaffold-audit", "Audit scaffold gaps"),
    ("/docs-check", "Check docs that need updating"),
    ("/handoff", "Generate handoff report"),
    ("/doctor", "Environment diagnostics"),
    ("/health", "Workspace health analysis"),
    ("/release-check", "Release readiness checklist"),
    # Model Hub
    ("/models", "Interactive provider picker"),
    ("/model", "Show or switch model"),
    ("/model-info", "Model capability info"),
    ("/provider", "Manage API keys"),
    ("/fallback", "Set fallback provider"),
    # Workstream E
    ("/trace", "Trace symbol through layers"),
    ("/compare", "Compare branches"),
    ("/flaky-test", "Detect flaky tests"),
    ("/metrics", "Task completion metrics"),
    ("/session-log", "Session activity log"),
    ("/plugins", "List loaded plugins"),
    ("/context-status", "Context window usage"),
]

MODES = ["ask", "plan", "build", "fix", "review"]


def bash_completion() -> str:
    cmds = " ".join(cmd for cmd, _ in COMMANDS)
    return f"""# Ultron CLI bash completion
# Source this file: source ~/.bash_completion.d/ultron
# Or add to ~/.bashrc: source /path/to/ultron_completion.bash

_ultron_completions() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Complete slash commands in REPL
    if [[ "$cur" == /* ]]; then
        COMPREPLY=($(compgen -W "{cmds}" -- "$cur"))
        return
    fi

    # Complete --mode flag values
    if [[ "$prev" == "--mode" ]]; then
        COMPREPLY=($(compgen -W "{' '.join(MODES)}" -- "$cur"))
        return
    fi

    # Complete CLI flags
    local flags="--model --base-url --yes --auto-commit --help"
    COMPREPLY=($(compgen -W "$flags" -- "$cur"))
}}

complete -F _ultron_completions ultron
complete -F _ultron_completions ultron-ci
"""


def zsh_completion() -> str:
    cmd_defs = "\n".join(
        f"    '{cmd}:{desc}'"
        for cmd, desc in COMMANDS
    )
    return f"""#compdef ultron

# Ultron CLI zsh completion
# Place in ~/.zsh/completions/_ultron or $(brew --prefix)/share/zsh/site-functions/_ultron

_ultron() {{
    local -a commands
    commands=(
{cmd_defs}
    )

    _arguments \\
        '--model[Ollama model to use]:model:' \\
        '--base-url[Ollama API base URL]:url:' \\
        '--yes[Auto-approve all operations]' \\
        '--auto-commit[Auto-commit changes]' \\
        '*:command:->command'

    case $state in
        command)
            _describe 'ultron commands' commands
            ;;
    esac
}}

_ultron "$@"
"""


def fish_completion() -> str:
    lines = ["# Ultron CLI fish completion"]
    lines.append("# Place in ~/.config/fish/completions/ultron.fish")
    lines.append("")
    for cmd, desc in COMMANDS:
        safe_desc = desc.replace("'", "\\'")
        lines.append(f"complete -c ultron -f -a '{cmd}' -d '{safe_desc}'")
    lines.append("")
    lines.append("complete -c ultron -l model -d 'Ollama model name'")
    lines.append("complete -c ultron -l base-url -d 'Ollama API base URL'")
    lines.append("complete -c ultron -l yes -d 'Auto-approve all operations'")
    lines.append("complete -c ultron -l auto-commit -d 'Auto-commit changes'")
    return "\n".join(lines)


def install_completions():
    home = os.path.expanduser("~")
    installed = []

    # Bash
    bash_dir = os.path.join(home, ".bash_completion.d")
    os.makedirs(bash_dir, exist_ok=True)
    bash_path = os.path.join(bash_dir, "ultron")
    with open(bash_path, "w") as f:
        f.write(bash_completion())
    installed.append(f"bash: {bash_path}")

    # Zsh
    zsh_dir = os.path.join(home, ".zsh", "completions")
    os.makedirs(zsh_dir, exist_ok=True)
    zsh_path = os.path.join(zsh_dir, "_ultron")
    with open(zsh_path, "w") as f:
        f.write(zsh_completion())
    installed.append(f"zsh: {zsh_path}")

    # Fish
    fish_dir = os.path.join(home, ".config", "fish", "completions")
    os.makedirs(fish_dir, exist_ok=True)
    fish_path = os.path.join(fish_dir, "ultron.fish")
    with open(fish_path, "w") as f:
        f.write(fish_completion())
    installed.append(f"fish: {fish_path}")

    print("Shell completions installed:")
    for line in installed:
        print(f"  ✓ {line}")
    print("\nFor bash, add to ~/.bashrc:")
    print(f"  source ~/.bash_completion.d/ultron")
    print("\nFor zsh, add to ~/.zshrc:")
    print(f"  fpath=(~/.zsh/completions $fpath)")
    print(f"  autoload -Uz compinit && compinit")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "bash"

    if arg == "--install":
        install_completions()
    elif arg == "zsh":
        print(zsh_completion())
    elif arg == "fish":
        print(fish_completion())
    else:
        print(bash_completion())
