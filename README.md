# Ultron CLI

> A terminal-based AI coding agent that runs locally on Ollama or any cloud provider.

[![CI](https://github.com/your-username/ultron/actions/workflows/pytest.yml/badge.svg)](https://github.com/your-username/ultron/actions/workflows/pytest.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-381%20passing-brightgreen)](tests/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What is Ultron?

Ultron is a workspace-first, local-first terminal AI coding agent. Install it once, run `ultron` from any project folder, and use the interactive session to understand, build, test, debug, review, and deliver code — all without leaving your terminal.

**Works completely offline** with Ollama. Switch to Groq, Claude, GPT-4, Gemini, or OpenRouter when you need stronger reasoning — all with API keys stored securely in your OS keyring, never in files or git.

```
  _    _ _   _______ _____   ____  _   _
 | |  | | | |__   __|  __ \ / __ \| \ | |
 | |  | | |    | |  | |__) | |  | |  \| |
 | |  | | |    | |  |  _  /| |  | | . ` |
 | |__| | |____| |  | | \ \| |__| | |\  |
  \____/|______|_|  |_|  \_\\____/|_| \_|

   >> LOCAL AI CODING AGENT // VERSION 0.1.0 <<
```

---

## Features

### 🔒 Safety First (Phase 0)
- **Path containment** — resolves symlinks, blocks traversal, multi-drive, and sibling escapes
- **Sensitive file deny list** — `.git`, `.env`, `*credential*` blocked by default
- **Tool argument validation** — malformed model calls never execute
- **Dirty file guard** — warns before editing uncommitted changes
- **Conflict-aware `/undo`** — SHA-256 checksums detect user modifications before reverting

### ⚡ Fast Everyday Coding (Phase 1)
- **Interactive REPL** with tab-completion for commands and file paths
- **Project onboarding** (`/onboard`) — detects language, package manager, test/lint/build commands
- **Side-effect command detection** — prompts before install, migrate, server launch, network calls
- **Smart work summary** — shows modified files, commands run, warnings at task end
- **Process cancellation** — Ctrl+C cleanly kills subprocess groups

### 🧠 Project Intelligence (Phase 2)
- **Repository indexer** (`/analyze`) — regex-based symbol, import, and test detection
- **Symbol search** (`/symbol`, `/references`, `/flow`) — find definitions and call sites
- **Impact analysis** (`/impact`) — risk-scored report before changing code
- **Failure investigation** (`/why`) — extracts root cause from noisy logs
- **Minimal reproduction** (`/min-repro`) — generates reproduction scripts
- **`ULTRON.md` + `.ultron.toml`** — project instructions auto-injected every turn

### 🎯 Safe Autonomous Engineering (Phase 3)
- **Intent modes** — `/mode ask|plan|build|fix|review` with dual-layer enforcement
- **Change contracts** — scope budget that pauses when task exceeds planned files
- **Verification gate** (`/verify`) — structured pass/fail/skip/not_run reporting
- **3-tier code review** (`/review`) — verified_match / heuristic / AI suggestion
- **Refactor guard** — auto-checks callers and test coverage before refactoring
- **Evidence tagging** — every completion summary labels Observed / Verified / Inferred / Not verified
- **Clarification gate** — pauses and asks before executing when requirements are ambiguous

### 🚀 Professional Delivery (Phase 4)
- **Git worktrees** (`/worktree`) — isolated branches for risky work
- **PR summary** (`/pr-summary`) — AI-generated PR description from diff
- **Commit quality** (`/commit-check`) — conventional commit validation, debug code detection
- **Monorepo support** (`/monorepo`) — detects packages, targeted test/build commands
- **Feature planner** (`/feature`) — vertical-slice plan: models → API → tests → docs
- **Scaffold auditor** (`/scaffold-audit`) — catches missing exports, tests, env vars
- **Developer handoff** (`/handoff`) — generates ready-to-share task report
- **Release checklist** (`/release-check`) — version, changelog, README, git clean
- **Environment doctor** (`/doctor`) — validates runtime, git, Ollama, disk space
- **Health analysis** (`/health`) — detects dead code, async blocking calls, N+1 patterns
- **CI/headless mode** (`ultron-ci`) — JSON output for automation pipelines

### 🔌 Model Hub (Provider System)
- **7 providers** — Ollama, Groq, Anthropic/Claude, OpenAI, Google Gemini, OpenRouter, OpenAI-Compatible
- **Secure key storage** — OS keyring (never in files, git, or logs)
- **Interactive picker** (`/models`) — numbered menu, hidden key entry, connection test
- **Automatic fallback** — if cloud provider fails, falls back to local Ollama
- **Failure recovery** — Retry / Switch provider / Use Ollama / Read-only mode

### 🏗️ Structured Task Lifecycle (Maturity)
- **TaskRouter** — classifies every prompt into intent (debug/feature/refactor/test/...)
- **Budget enforcer** — stops task at tool call limit, time budget, or repair attempt limit
- **ToolRegistry + PolicyEngine** — formal risk levels, mode-based blocking, explicit rules
- **ChangeTracker** — unified expected vs actual file tracking with scope delta
- **Metrics collector** — auto-records task completion rate, duration, unverified rate
- **Feature tracer** (`/trace`) — maps symbol through route → service → domain → persistence
- **Branch comparer** (`/compare`) — diff stats and commits between branches
- **Flaky test detector** (`/flaky-test`) — re-runs test N times, detects non-determinism
- **Test output parser** — structured results for pytest, unittest, npm, cargo, go test

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/ultron.git
cd ultron

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Pull an Ollama model (for local use)
ollama pull qwen2.5-coder:7b

# Run
ultron
```

### Requirements
- Python 3.11+
- [Ollama](https://ollama.ai) (for local inference) — or any cloud API key
- Git

---

## Quick Start

```bash
# Start in your project directory
cd /path/to/your/project
ultron

# Or with a specific model
ultron --model llama3.1:8b

# Auto-approve all changes (CI / scripted use)
ultron --yes

# Use a cloud provider
ultron --model claude-sonnet-4-5
```

---

## Slash Commands Reference

### Core
| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/add <file>` | Pin file to prompt context |
| `/drop <file>` | Remove file from context |
| `/files` | List pinned files |
| `/clear` | Reset conversation memory |
| `/exit` | Quit Ultron |

### Git & Workflow
| Command | Description |
|---|---|
| `/diff` | Show staged + unstaged + untracked changes |
| `/commit` | AI-generated git commit message |
| `/undo` | Revert last Ultron task (conflict-aware) |
| `/worktree [list\|create\|remove]` | Manage git worktrees |
| `/pr-summary [base]` | Generate PR description |
| `/commit-check <msg>` | Validate commit message quality |
| `/decisions [n]` | View recent decision log |

### Project Intelligence
| Command | Description |
|---|---|
| `/analyze` | Build + display repository map |
| `/find <query>` | Text search across all indexed files |
| `/symbol <name>` | Find symbol definitions |
| `/references <symbol>` | Find all references |
| `/flow <symbol>` | Trace definition → callers → tests |
| `/explain <file\|symbol>` | AI explanation of code |
| `/impact <file\|symbol>` | Risk-scored impact analysis |
| `/why [log]` | Investigate failure with AI root cause |
| `/min-repro [log]` | Generate minimal reproduction script |
| `/trace <symbol>` | Trace through architectural layers |

### Coding Workflow
| Command | Description |
|---|---|
| `/plan <task>` | Generate implementation plan |
| `/tasks` | Show task checklist + active task state |
| `/test [target]` | Run test command |
| `/lint [target]` | Run lint command |
| `/fix` | Auto-fix last error |
| `/verify [checks]` | Structured verification (impact-aware) |
| `/review` | 3-tier code review |

### Intent Modes
| Command | Description |
|---|---|
| `/mode ask` | Read-only: explain and inspect |
| `/mode plan` | Read-only: generate plans |
| `/mode build` | Full editing (default) |
| `/mode fix` | Focused repair mode |
| `/mode review` | Inspect diffs, no edits |

### Delivery
| Command | Description |
|---|---|
| `/feature <desc>` | Vertical-slice feature plan |
| `/scaffold-audit` | Audit for missing exports, tests, env vars |
| `/docs-check` | Identify docs that need updating |
| `/handoff [desc]` | Generate developer handoff report |
| `/doctor` | Environment diagnostics |
| `/health` | Workspace health analysis |
| `/release-check` | Release readiness checklist |
| `/monorepo` | Detect packages in workspace |
| `/recent` | Show recently opened workspaces |
| `/alias [list\|add\|remove]` | Manage workspace aliases |

### Model Hub
| Command | Description |
|---|---|
| `/models` | Interactive provider + model picker |
| `/model [name]` | Show or switch active model |
| `/model-info` | Context window, tools, streaming info |
| `/provider [status\|add\|remove]` | Manage API keys |
| `/fallback [provider/model]` | Set fallback provider |

### Quality & Analysis
| Command | Description |
|---|---|
| `/compare [branch]` | Compare branches |
| `/flaky-test [cmd]` | Re-run test N times for flakiness |
| `/bisect` | Guided git bisect session |
| `/reproduce [desc]` | Save sanitized bug reproduction |
| `/metrics` | Task completion rate and history |
| `/onboard` | Detect project framework and commands |
| `/init-project` | Create ULTRON.md and .ultron.toml |

---

## Model Hub

Ultron supports 7 model providers. Switch with `/models`:

```
╔══ Ultron Model Hub ══╗

  ✓ 1. Ollama                    Local inference, no API key needed  ← active
  ○ 2. Groq                      Fast cloud inference, free tier
  ○ 3. Anthropic / Claude        Claude Sonnet, Haiku, Opus
  ○ 4. OpenAI                    GPT-4o, GPT-4o-mini, o1
  ○ 5. Google Gemini             Gemini 2.0 Flash, 1.5 Pro
  ○ 6. OpenRouter                200+ models via one API key
  ○ 7. OpenAI-Compatible Server  LM Studio, vLLM, LocalAI
```

API keys are stored in your **OS credential vault** (Windows Credential Manager / macOS Keychain / Linux Secret Service). They never touch files, logs, git history, or `.ultron.toml`.

If the active provider fails mid-session:
```
Provider failure: quota exceeded.

Recovery options:
  1. Retry current provider
  2. Switch provider (/models)
  3. Use local Ollama fallback
  4. Continue in read-only mode
```

---

## Project Memory

Ultron stores per-project data outside your workspace in `~/.ultron/`:

```
~/.ultron/
  workspaces/<hash>/
    project_memory.json   # detected commands, project type
    tasks.json            # task checklist
  checkpoints/<hash>/     # undo snapshots
  repo_map/<hash>/        # symbol index cache
  decisions/<hash>/       # auto-recorded task decisions
  metrics/<hash>/         # task completion metrics
```

Git working tree stays **completely clean** — nothing is written inside your project.

### ULTRON.md

Create `ULTRON.md` in your project root (or run `/init-project`):

```markdown
# Project Instructions for Ultron

## Architecture Notes
This is a FastAPI service. Routes are in api/, services in services/, models in models/.

## Conventions
- Use snake_case for all Python identifiers
- All service methods must have type annotations
- Tests go in tests/ mirroring the source structure

## Verified Commands
- Test: pytest tests/ -v
- Lint: flake8 . --max-line-length=100
```

Ultron reads this file on every turn automatically.

---

## CI / Headless Mode

### GitHub Actions

The included `.github/workflows/pytest.yml` runs the full test suite on every push:

```yaml
python -m pytest tests/ -v --ignore=tests/live_integration_test.py
```

### Headless JSON mode

```bash
# Run a task non-interactively
ultron-ci --workspace /path/to/project --prompt "Fix the failing test in test_auth.py"

# Output
{
  "success": true,
  "files_changed": ["auth/service.py"],
  "commands_run": ["pytest tests/test_auth.py"],
  "evidence": ["Verified by command: pytest: 5 passed"],
  "exit_code": 0
}
```

---

## Evaluation Harness

Run the built-in eval suite to verify Ultron behaves correctly:

```bash
python scripts/run_eval.py

# Output
┌─ Ultron Eval Harness ────────────────────────────────────┐
│ #  Scenario                          Result  Detail       │
│ 1  Write a file                      PASS    app/new.py   │
│ 2  Read then explain (no mutations)  PASS    No files     │
│ 3  Respect ask mode (no writes)      PASS    Blocked      │
│ 4  Over-budget task stops at BLOCKED PASS    status=block │
│ 5  Multi-file requires plan          PASS    file2 blocked│
└──────────────────────────────────────────────────────────┘
5/5 scenarios passed.
```

---

## Architecture

```
ultron/
├── agent.py          Core agentic loop — task lifecycle, tool dispatch, history
├── repl.py           Interactive REPL — 70+ slash commands, tab completion
├── cli.py            Entry point (Click CLI)
├── models.py         Legacy OllamaModel (backward compat)
├── tools.py          File I/O, shell exec, git operations
├── context.py        Pinned file context management (50k char budget)
├── checkpoint.py     Undo system — SHA-256 conflict detection
├── diff.py           Compact diff display
├── security.py       Path containment + sensitive file deny list
├── onboard.py        Project discovery + persistent memory
├── repo_map.py       Workspace indexer — symbols, imports, tests
├── analyzer.py       Impact analysis, failure investigation, conventions
├── contract.py       Change contract engine — scope enforcement
├── reviewer.py       3-tier code reviewer
├── verifier.py       Verification gate — structured pass/fail reporting
├── task.py           Task model + TaskRouter + BudgetEnforcer
├── tool_registry.py  ToolRegistry + PolicyEngine + CommandRunner
├── change_tracker.py Unified scope tracking
├── tracer.py         /trace, /compare, /flaky-test, TestOutputParser
├── eval_suite.py     MockProvider, FixtureWorkspace, MetricsCollector
├── git_workflow.py   Worktrees, PR summary, commit quality, decision log
├── monorepo.py       Package detection, workspace aliases
├── delivery.py       Feature planner, scaffold auditor, handoff, doctor
├── headless.py       CI/JSON mode
└── providers/
    ├── base.py       ModelProvider interface
    ├── credential_store.py  OS keyring storage
    ├── registry.py   Provider registry + interactive picker
    ├── ollama.py     Ollama local provider
    ├── groq.py       Groq provider
    ├── anthropic.py  Anthropic/Claude provider
    ├── openai.py     OpenAI provider
    ├── gemini.py     Google Gemini provider
    ├── openrouter.py OpenRouter provider
    └── openai_compat.py  LM Studio / vLLM / LocalAI
```

---

## Test Suite

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific suite
python -m pytest tests/test_phase2.py -v    # Project intelligence
python -m pytest tests/test_phase3.py -v    # Autonomous engineering
python -m pytest tests/test_providers.py -v # Model hub
python -m pytest tests/test_maturity.py -v  # Task lifecycle + eval

# Live integration test (requires Ollama)
python -m pytest tests/live_integration_test.py -v
```

**381 tests, ~3 minutes, zero failures.**

| Test File | Tests | Coverage |
|---|---|---|
| `test_agent.py` | 3 | Path safety, file ops, context |
| `verify_agent.py` | 11 | Dirty file, undo, malformed tools, REPL |
| `test_phase2.py` | 45 | RepoMap, ImpactAnalyzer, FailureInvestigator |
| `test_phase3.py` | 65 | Contracts, reviewer, verifier, modes |
| `test_phase4.py` | 83 | Git workflow, monorepo, delivery, headless |
| `test_providers.py` | 62 | All 7 providers, credential store, registry |
| `test_maturity.py` | 100 | Task, ToolRegistry, PolicyEngine, tracer, eval |
| `test_eval_harness.py` | 12 | 5 eval scenarios with MockProvider |

---

## Configuration

### `.ultron.toml`

```toml
[project]
name = "my-app"
language = "Python"

[commands]
test = "pytest tests/ -v"
lint = "flake8 . --max-line-length=100"
build = ""
format = "black ."
run = "python app.py"

[context]
always_include = ["app/config.py", "ARCHITECTURE.md"]

[permissions]
require_approval = ["rm", "migrate", "deploy"]

[contracts]
max_files = 8
unplanned_file_policy = "ask"
```

---

## Safety Model

| Concern | Protection |
|---|---|
| Workspace escape | `validate_path()` with realpath + commonpath |
| Secret file access | Deny list: `.git`, `.env`, `*credential*` |
| Malformed tool calls | Schema validation before execution |
| Silent user overwrite | Conflict detection via SHA-256 before undo |
| Pre-existing dirty changes | Explicit user approval required |
| Unintended git staging | `git add -- <pathspec>` scoped commits only |
| Scope creep | Change contracts with max_files budget |
| Infinite loops | 12 tool call limit + time budget per task |
| Debug code in commits | CommitQualityChecker scans diff |
| API key exposure | OS keyring only, never files/logs/git |

---

## Maturity Level

| Level | Status |
|---|---|
| 1 — Safe assistant | ✅ Complete |
| 2 — Project-aware dev tool | ✅ Complete |
| 3 — Controlled coding agent | ✅ Complete |
| 4 — Flexible engineering platform | ✅ Complete |
| 5 — Trusted developer teammate | 🔄 Eval running, metrics collecting |

---

## Contributing

1. Fork the repo
2. Create a branch: `git checkout -b feat/my-feature`
3. Make changes + add tests
4. Run: `python -m pytest tests/ -q`
5. Push and open a PR

All PRs must pass the full test suite (381 tests).

---

## License

MIT — see [LICENSE](LICENSE)

---

*Built with ❤️ — local-first, privacy-first, developer-first.*
