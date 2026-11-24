---
name: orchestrator
description: Dual-Channel Multi-Agent Orchestrator with ProjectBrain Multi-Source Hub integration, SKILL.md + sub/*.md override loading, 3-Stage Circuit Breaker, and real-time SSE telemetry
---

# Dual-Channel Multi-Agent Orchestrator

Start full-session Multi-Agent Orchestrator mode:

0. **MANDATORY ProjectBrain Hub & Skill Override Gate (Execute FIRST)**:
   - Verify the ProjectBrain hub is initialized for the workspace:
     1. Call `projectbrain_list_sources(project_id="<project_id>")`.
     2. If empty or uninitialized, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`.
   - Load `.agents/skills/multi-agent-orchestrator/SKILL.md` **AND** all `.agents/skills/multi-agent-orchestrator/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific overrides over `SKILL.md` defaults.
   - For every delegated worker (or sequential persona in single-agent IDEs), inject its target `.agents/skills/<skill>/SKILL.md`, all `<skill>/sub/*.md` overrides (excluding `README.md`), and scoped ProjectBrain context (`projectbrain_hybrid_query(project_id=..., source_id=..., include_shared_docs=True, relation_depth=1)` and `projectbrain_cross_source_impact` when touching shared contracts).
   - After any code edits in a sub-source, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.

1. **MANDATORY Turn-1 Pre-Flight Gate**:
   - Before decomposing tasks or calling `invoke_subagent`, ALWAYS run (when `~/.gemini/tools/orchestrator/client.py` or local orchestrator CLI is present; otherwise proceed with `API_ONLY_100` / host subagent mode):
     ```bash
     python3 ~/.gemini/tools/orchestrator/client.py preflight --workspace .
     ```
     *(If `~/.gemini/tools/orchestrator/client.py` is absent, use `tools/orchestrator/client.py` or `packages/orchestrator/client.py`, or fall back directly to host subagent/single-agent execution.)*
   - `preflight` checks/auto-starts the `:8484` Orchestrator Dashboard (`http://localhost:8484`), probes live Gemini Desktop App readiness (`desktop_share.desktop_ready`), allocates pending DAG tasks (`target_share=0.60`), and outputs `EXECUTION_MODE: HYBRID_60_DESKTOP` or `EXECUTION_MODE: API_ONLY_100`.
   - SQLite persistence is active at `tools/orchestrator/orchestrator.db` (or `packages/orchestrator/orchestrator.db`).

2. **Dual-Channel Fire & Delegate Protocol (Command & Control vs. Implementation)**:
   - **Zero Direct Code Implementation**: In multi-agent mode, the Orchestrator delegates application code edits and test runs to role-based workers (if the host IDE is single-agent without subagents, adopt each role persona step-by-step instead).
   - **Orchestrator Command & Control Exception**: Running `client.py preflight`, `client.py allocate-dag`, `client.py update`, `client.py next-ready`, ProjectBrain MCP hub calls, and dispatching Channel A tasks via `client.py desktop-ask` (`run_command` with `WaitMsBeforeAsync: 500`) **ARE required Orchestrator dispatch operations** — execute them directly!
   - **Case A — When `preflight` reports `EXECUTION_MODE: HYBRID_60_DESKTOP` (`desktop_ready == true`)**:
     - **HARD INVARIANT (~60% Gemini Desktop App Share)**: Do **NOT** send `spec-engineer`, `task-planner`, `code-reviewer`, `doc-generator`, non-critical `debugger`, `performance-auditor`, OR the primary heavy `builder` of a wave to an AGY `pro` / `inherit` `invoke_subagent`!
     - **Channel A (`gemini-desktop-app` — ~60% tasks, `$0.00` AGY API spend, `0` focus switches)**:
       Dispatch directly via `run_command` (`WaitMsBeforeAsync: 500` async background task):
       - Read-only synthesis (`spec-engineer`, `task-planner`, `code-reviewer`, `performance-auditor`, `doc-generator` critique — omit `--allow-edits`):
         `python3 ~/.gemini/tools/orchestrator/client.py desktop-ask --task <TASK_ID> --agent <ROLE> --workspace . --prompt "<BOUNDED_PROMPT>"`
       - Mutating heavy `builder` or non-critical `debugger` (max **1** per `parallel_group` — pass `--allow-edits`):
         `python3 ~/.gemini/tools/orchestrator/client.py desktop-ask --task <TASK_ID> --agent builder --workspace . --allow-edits --prompt "<BOUNDED_PROMPT>"`
       - **Parallel-Group Mutex Invariant**: Dispatch **at most 1** task per `parallel_group` to `desktop-ask` so `Gemini.app`'s single-window FIFO lock NEVER serializes sibling workers.
     - **Channel B (`AGY Subagents` — ~40% tasks, concurrent in the same turn)**:
       - Sibling parallel `builder` (`<=3` files), `test-engineer`, `scout` -> `invoke_subagent(Model="flash")` (**Gemini 4 Hydrogen** `198 tok/s` / **3.8 Flash**).
       - **Rule 1 Security Audits** (`security-auditor`, `has_security_impact=True`, `critical` complexity), Circuit Breaker Stage 2/3, and busy-queue Neon spillover -> `invoke_subagent(Model="pro")` (or `"inherit"`).
   - **Case B — When `preflight` reports `EXECUTION_MODE: API_ONLY_100` (`desktop_ready == false`)**:
     - Dispatch **100%** of tasks via host subagents (`invoke_subagent` / `Task` / `Agent`):
       - `Model="pro"` (or `"inherit"`) for `gemini-4-carbon` / `gemini-4-neon` roles (`security-auditor`, `spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, multi-file `builder >=4` files).
       - `Model="flash"` for `gemini-4-hydrogen` / `gemini-3.8-flash` roles (bounded `builder <=3` files, `test-engineer`, `scout`, `code-simplifier`, `doc-generator`).
   - **Reactive Yield**: End your turn immediately after dispatching Channel A (`run_command`, `WaitMsBeforeAsync=500`) + Channel B (`invoke_subagent`) workers. Never poll or sleep.

3. **Periodic Table Intelligent Model Routing (Neon SWE Fleet + Conditional 60% Desktop Peer)**:
   - **Conditional 60% Peer (`gemini-desktop-app`)**: Active whenever `preflight` reports `desktop_ready == true` (`$0.00` AGY API spend, `0` focus switches).
   - **Tier 1 (`gemini-4-carbon`, `Model="pro"`, `reasoning_effort: high | medium`)**: Rule 1 security audits (OWASP/auth/crypto/CVE/migration), `critical` complexity specs, and Circuit Breaker Stage 3 (`retry_count >= 3`).
   - **Tier 1.5 / 2 (`gemini-4-neon`, `gemini-pro-orchestrator-neon`, `Model="pro"`/`"inherit"`)**: Primary SWE Brain for non-security `spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, multi-file `builder` (`>= 4` files) in `API_ONLY_100` mode or Desktop spillover, and CB Stage 2 (`retry_count == 2`).
   - **Tier 3 (`gemini-4-hydrogen`, `Model="flash"`, `198 tok/s`)**: Bounded parallel `builder` slices (`<= 3` files), `test-engineer`, `scout`, and UI iteration.
   - **Tier 4 (`gemini-3.8-flash`, `Model="flash"`, `low`)**: Cost-first scaffolding, `code-simplifier`, `doc-generator`.

4. **3-Stage Fast-Retry & Escalation Circuit Breaker (Channel B API Only)**:
   - **Stage 1 (`retry_count == 1`)**: Hydrogen `invoke_subagent(Model="flash")` performs 1 fast self-healing turn (`effort: xhigh`).
   - **Stage 2 (`retry_count == 2`)**: Escalate to **Gemini 4 Neon** (`invoke_subagent(Model="pro")`, `high`/`xhigh`).
   - **Stage 3 (`retry_count >= 3`)** or security impact: Trip hard directly to **Gemini 4 Carbon** (`invoke_subagent(Model="pro")`, `high` effort).

5. **Condition-Based Multi-Source DAG Scheduling & Parallel Fan-Out**:
   - Group independent tasks across sub-sources (`source_id`) using `parallel_group` (e.g., `BUILD-PARALLEL`) and prerequisites with `depends_on`.
   - Before any contract/DTO/API change, verify blast radius with `projectbrain_cross_source_impact(project_id=..., target=..., source_id=...)`.
   - Enforce the **Decoupled File Scope Invariant**: parallel workers across Channel A and Channel B must operate on strictly disjoint files.
   - Discover ready unblocked tasks via `python3 ~/.gemini/tools/orchestrator/client.py next-ready`.

6. **Token Telemetry & Turnkey CLI Subcommands**:
   - **Turn-1 Pre-Flight**: `python3 ~/.gemini/tools/orchestrator/client.py preflight --workspace .` (or `--no-desktop`)
   - **DAG Allocation**: `python3 ~/.gemini/tools/orchestrator/client.py allocate-dag --target-desktop-share 0.60 --apply`
   - **Ready Queue**: `python3 ~/.gemini/tools/orchestrator/client.py next-ready`
   - **Channel A Dispatch**: `python3 ~/.gemini/tools/orchestrator/client.py desktop-ask --task <TASK_ID> --agent <ROLE> --workspace . [--allow-edits] --prompt "..."`
   - **Telemetry & Export**: `client.py update ...` and `client.py export --format markdown --out docs/reports/mission_audit_report.md`

See also: [`pb-orchestrate.md`](./pb-orchestrate.md) and [`workflow-pipeline.md`](./workflow-pipeline.md).
