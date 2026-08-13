# Ultron CLI — Architecture

## Overview

Ultron is structured around four core concerns:

1. **Safety** — every file operation, command execution, and git action goes through formal validation
2. **Task lifecycle** — every user request follows a structured Understand→Inspect→Plan→Execute→Verify→Handoff workflow
3. **Provider abstraction** — the agent works identically regardless of which AI model is active
4. **Observability** — every action is logged, every completion is evidence-tagged

---

## Module Map

```
ultron/
├── cli.py              Entry point (Click). Initializes agent, checks Ollama, starts REPL.
├── repl.py             Interactive REPL. Routes 70+ slash commands to handler methods.
│
├── agent.py            Core agentic loop.
│                       - Creates Task on every run()
│                       - Streams model responses
│                       - Dispatches tool calls via PolicyEngine
│                       - Enforces intent modes and budget limits
│                       - Logs to SessionLogger and MetricsCollector
│
├── task.py             Task model + TaskRouter + BudgetEnforcer.
│                       - TaskRouter classifies prompt into 8 intents
│                       - WORKFLOW_TEMPLATES defines ordered steps per intent
│                       - BudgetEnforcer stops tasks at tool/time limits
│
├── tool_registry.py    ToolRegistry + PolicyEngine + CommandRunner.
│                       - 8 default tools with formal RiskLevel
│                       - PolicyEngine evaluates allow/ask/deny per mode
│                       - CommandRunner: structured subprocess with timeout
│
├── change_tracker.py   Unified scope tracking.
│                       - Records expected vs actual files per task
│                       - Detects scope growth beyond contract
│
├── contract.py         Change contract engine.
│                       - Formal scope declaration before multi-file edits
│                       - Configurable max_files ceiling
│                       - ask/block policy for unplanned files
│
├── security.py         Path containment + sensitive file deny list.
│                       - validate_path(): realpath + commonpath + Windows drive check
│                       - Deny list: .git, .env, *credential*
│                       - is_side_effect_command(): blocks installs/migrations/servers
│
├── checkpoint.py       Conflict-aware undo system.
│                       - SHA-256 snapshot before every file write
│                       - /undo detects user modifications since task completed
│
├── context.py          Pinned file context (50k char budget).
│                       - /add /drop for manual pinning
│                       - build_context_prompt() assembles system message content
│
├── models.py           Legacy OllamaModel (backward compat).
│                       - Kept for tools/onboard that reference agent.model directly
│
├── session_log.py      Persistent JSONL audit trail.
│                       - Logs every tool call, model call, task start/end
│                       - Stored in ~/.ultron/logs/<workspace_hash>/
│
├── eval_suite.py       Evaluation infrastructure.
│                       - MockProvider: scripted deterministic responses
│                       - FixtureWorkspace: disposable git repos for testing
│                       - MetricsCollector: task completion rate tracking
│
├── repo_map.py         Workspace indexer.
│                       - Regex-based symbol, import, test extraction
│                       - Incremental cache (mtime-based, disk-persisted)
│
├── analyzer.py         Project intelligence.
│                       - ImpactAnalyzer: risk-scored change impact
│                       - FailureInvestigator: root cause from error logs
│                       - ConventionFinder: auto-inject similar files before edits
│                       - RefactorGuard: caller + test check before refactors
│
├── tracer.py           Feature flow + branch comparison + test quality.
│                       - FeatureTracer: route→service→domain→persistence layers
│                       - BranchComparer: git diff between branches
│                       - FlakyTestDetector: multi-run flakiness detection
│                       - TestOutputParser: pytest/npm/cargo/go structured results
│                       - VerificationPlanner: impact-aware check selection
│
├── verifier.py         Verification gate.
│                       - /verify command with pass/fail/skip/not_run reporting
│                       - Calls VerificationPlanner for smart check selection
│                       - Uses TestOutputParser for structured evidence
│
├── reviewer.py         3-tier code reviewer.
│                       - Tier 1: verified_match (regex at file:line)
│                       - Tier 2: heuristic (pattern-based)
│                       - Tier 3: ai_suggestion (model opinion, never "confirmed defect")
│
├── onboard.py          Project discovery + persistent memory.
│                       - Detects language, package manager, test/build/lint commands
│                       - Stores in ~/.ultron/workspaces/<hash>/project_memory.json
│
├── git_workflow.py     Professional git operations.
│                       - WorktreeManager: isolated branches
│                       - PRSummaryGenerator: AI-powered PR descriptions
│                       - CommitQualityChecker: conventional commits + debug scan
│                       - DecisionLog: persistent task decision history
│
├── monorepo.py         Monorepo + workspace management.
│                       - MonorepoDetector: finds packages by manifest files
│                       - WorkspaceAliasManager: named workspace shortcuts
│
├── delivery.py         Project delivery assistant.
│                       - FeaturePlanner: vertical-slice plan generation
│                       - ScaffoldAuditor: missing exports/tests/migrations
│                       - DocsChecker: affected documentation detection
│                       - HandoffGenerator: developer-ready task reports
│                       - EnvironmentDoctor: runtime validation
│                       - HealthAnalyzer: dead code, N+1, async blocking
│                       - ReleaseChecker: version/changelog/test readiness
│
├── headless.py         CI/JSON mode.
│                       - run_headless(): non-interactive task execution
│                       - Returns structured JSON result
│
├── plugin_loader.py    Dynamic plugin system.
│                       - Scans ~/.ultron/plugins/*.py
│                       - Registers ULTRON_TOOLS and ULTRON_PROVIDERS
│
├── diff.py             Compact diff display.
│                       - Shows filename +N -N summary
│                       - Lists changed lines (max 20)
│
└── providers/
    ├── base.py         ModelProvider abstract interface.
    │                   - health_check(), list_models(), capabilities(), chat()
    │                   - is_available() alias for backward compat
    ├── credential_store.py  OS keyring storage (Windows/macOS/Linux).
    ├── registry.py     Provider registry + interactive picker + fallback.
    ├── ollama.py       Ollama local provider (primary).
    ├── groq.py         Groq (fast inference).
    ├── anthropic.py    Anthropic/Claude (native Messages API).
    ├── openai.py       OpenAI (GPT-4o, o1).
    ├── gemini.py       Google Gemini (OpenAI-compat endpoint).
    ├── openrouter.py   OpenRouter (200+ models).
    └── openai_compat.py  LM Studio, vLLM, LocalAI.
```

---

## Key Data Flows

### User prompt → task completion

```
User types prompt
    ↓
repl.py start() → agent.run(prompt)
    ↓
TaskRouter.classify(prompt) → TaskIntent (debug/feature/refactor/...)
    ↓
Task created with intent, mode, budget
    ↓
ConventionFinder auto-injects similar files (FEATURE/REFACTOR only)
    ↓
_get_system_message() → injects mode + task hint + conventions + context
    ↓
model.chat(messages) → streaming chunks
    ↓
BudgetEnforcer.check() → continue / warn / stop
    ↓
tool_calls dispatched → PolicyEngine.evaluate() → allow/ask/deny
    ↓
_execute_tool_with_confirmation():
    write_file/patch_file → dirty check → checkpoint → diff panel → approval
    run_command → side-effect check → approval → execute
    ↓
Evidence tagged: OBSERVED (file write) / VERIFIED (exit 0) / NOT_VERIFIED
    ↓
SessionLogger logs every action
    ↓
Task finalized: VERIFIED / BLOCKED / CANCELLED
    ↓
MetricsCollector.record() + DecisionLog.record()
    ↓
Work summary printed
```

### /undo flow

```
User types /undo
    ↓
CheckpointManager.get_latest_checkpoint()
    ↓
For each file: current SHA-256 == post-edit SHA-256?
    YES → restore backup atomically
    NO  → conflict detected → Confirm.ask("Force revert?")
    ↓
Backup files removed from ~/.ultron/checkpoints/<hash>/
```

### Provider switching

```
User types /models
    ↓
ProviderRegistry.interactive_pick(console)
    ↓
Show numbered catalog (7 providers)
    ↓
User selects → needs_key? → hidden key input → health_check()
    ↓
Key passes → store_key(provider, key) → OS keyring
    ↓
Model list fetched → numbered picker
    ↓
_build_provider(id, model, url) → ModelProvider instance
    ↓
agent.model = new_provider
    ↓
Ollama set as fallback if switching away from local
```

---

## Storage Layout

```
~/.ultron/
├── workspaces/<hash>/
│   ├── project_memory.json    # detected commands, project type
│   └── tasks.json             # /tasks checklist
├── checkpoints/<hash>/
│   ├── backup_<id>.tmp        # binary file backups (pre-edit)
│   └── metadata.json          # post-edit hashes
├── repo_map/<hash>/
│   └── index.json             # symbol/import/test index cache
├── decisions/<hash>/
│   └── YYYYMMDD_HHMMSS.json   # per-task decision records
├── metrics/<hash>/
│   └── TIMESTAMP_ID.json      # TaskMetrics records
├── logs/<hash>/
│   └── session_YYYYMMDD.jsonl # structured audit trail
├── contracts/<hash>/
│   └── active.json            # current change contract
├── reviews/<hash>/
│   └── TIMESTAMP.json         # /review reports
├── verify/<hash>/
│   └── TIMESTAMP.json         # /verify reports
├── handoffs/<hash>/
│   └── handoff_TIMESTAMP.md   # /handoff reports
└── plugins/
    └── *.py                   # user plugins
```

---

## Adding a New Provider

1. Create `ultron/providers/myprovider.py` implementing `ModelProvider`
2. Add to `PROVIDER_CATALOG` in `registry.py`
3. Add `_build_provider()` case for your provider ID
4. Add model display list function if needed
5. Add tests in `tests/test_providers.py`

## Adding a New Tool

1. Register in `ToolRegistry.build_default()` with appropriate `RiskLevel`
2. Add executor in `agent._execute_tool_with_confirmation()`
3. Add validation in `validate_tool_args()` in `agent.py`
4. Add to `models.py` `get_tool_definitions()` for Ollama schema
5. Add tests

## Adding a New Slash Command

1. Add to `UltronCompleter.commands` list in `repl.py`
2. Add `elif cmd == "/mycommand":` routing in `handle_slash_command()`
3. Implement `_cmd_mycommand(self, arg)` method
4. Add to help table in `print_help()`
