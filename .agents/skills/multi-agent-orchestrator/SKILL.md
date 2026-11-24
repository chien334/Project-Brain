---
name: multi-agent-orchestrator
description: Multi-agent orchestration skill following the 1-Agent = 1-Skill paradigm (addyosmani/agent-skills). Coordinates specialized subagents across the software delivery lifecycle with real-time SSE telemetry, context minimization, Periodic Table intelligent model routing (gemini-4-neon SWE Frontier #1 with xhigh effort & ~105 tok/s, gemini-4-carbon Security Frontier, gemini-4-hydrogen with xhigh effort & 198 tok/s velocity, gemini-3.8-flash active micro-tier, and legacy gemini-4-argon), automated DAG scheduling, and audit exports.
---

# Multi-Agent Orchestrator Skill

## 1. Overview & Mission

The **Multi-Agent Orchestrator** coordinates autonomous subagents to deliver complex software projects with high velocity, zero state drift, and rigorous quality gates. 

Rooted in the **1-Agent = 1-Skill** paradigm popularized by Addy Osmani's agent design patterns, every agent in the fleet operates under a bounded scope, specialized persona, and strict context minimization protocol.

### Core Objectives
1. **MANDATORY Turn-1 Pre-Flight & Dual-Channel Dispatch (~60% Desktop / ~40% AGY)**:
   On Turn 1 of every `/orchestrator` invocation, run `python3 ~/.gemini/tools/orchestrator/client.py preflight --workspace .` (auto-starts `:8484` if needed and checks `desktop_share.desktop_ready`):
   - **When `EXECUTION_MODE: HYBRID_60_DESKTOP` (`desktop_ready == true`)**:
     - **Hard Invariant**: Never route `spec-engineer`, `task-planner`, `code-reviewer`, `doc-generator`, non-critical `debugger`, `performance-auditor`, or a wave's primary heavy `builder` to AGY `pro`/`inherit` `invoke_subagent`.
     - **Channel A (`gemini-desktop-app`, ~60% tasks, `$0.00` AGY API spend, `0` focus switches)**: Dispatch directly via `run_command(..., WaitMsBeforeAsync=500)` calling `python3 ~/.gemini/tools/orchestrator/client.py desktop-ask --task <ID> --agent <ROLE> --workspace . [--allow-edits] --prompt "..."` (max 1 task per `parallel_group`). *(Alternative: thin `invoke_subagent(Model="flash_lite")` wrapper that only calls `client.py desktop-ask`.)*
     - **Channel B (`AGY Subagents`, ~40% tasks, concurrent in same turn)**: Dispatch sibling parallel builders/testers/scouts via `invoke_subagent(Model="flash")` (`gemini-4-hydrogen` / `3.8-flash`), and Rule 1 security audits + CB Stage 2/3 via `invoke_subagent(Model="pro")` (`gemini-4-carbon` / `4-neon`).
   - **When `EXECUTION_MODE: API_ONLY_100` (`desktop_ready == false`)**: Automatically fall back 100% to `invoke_subagent` across the Periodic Table fleet (`pro`/`inherit` + `flash`).
2. **Divide and Conquer**: Deconstruct monolithic tasks into discrete, verifiable slices with clear boundary contracts.
3. **Context Minimization**: Prevent token bloat by using pointer-based file references (`file:///...`) and scoped briefings.
4. **Periodic Table Intelligent Model Routing (Neon SWE Fleet)**: Dynamically match task complexity to the optimal model tier:
   - ✦ **Tier 1 (Frontier Security & Escalation)**: `gemini-4-carbon` ($2.00 / $10.00 / $0.50; $4/$20 Dec 1, 2026) — Stage 6 security audits, auth/crypto/OWASP/CVE/migration invariants (`has_security_impact`, `critical`), and Circuit Breaker Stage 3 (`retry >= 3`).
   - ⚡✦ **Tier 1.5 / 2 (SWE Frontier & Orchestrator Brain)**: `gemini-4-neon` (Element #10, $3.00 / $15.00 / $0.15, 95% cache savings, ~105 tok/s, `xhigh` effort, Coding Agent Index #1 `58.64`, Intelligence `61`, `gemini-pro-orchestrator-neon`) — Primary SWE Brain replacing `gemini-4-argon` and taking over non-security `spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, multi-file `builder` (`>= 4` files), and Circuit Breaker Stage 2 (`retry == 2`) at ~32% lower cost/task than Carbon.
   - ✦ **Tier 2b (Legacy Deep Reasoning)**: `gemini-4-argon` ($1.25 / $5.00 / $0.3125 legacy; $2/$10 → $4/$20 Dec 1, 2026) — Superseded by `gemini-4-neon` across SWE routing; retained as legacy/fallback entry.
   - ⚡ **Tier 3 (High-Throughput Workhorse)**: `gemini-4-hydrogen` ($1.50 / $7.50 / $0.075, 198 tok/s, `xhigh` effort) — Primary SWE Hands for bounded parallel `builder` slices (`<= 3` files), `test-engineer`, `scout`, and rapid UI iteration (~24% quota saved when paired with `Orchestrator (Neon)`).
   - ⚡ **Tier 4 (Cost-Efficient & Active Micro-Tier)**: `gemini-3.8-flash` ($0.75 / $3.75 / $0.075 live; `low` effort) — Bulk scaffolding, cost-first implementations, simplification, docs, and active micro-tier telemetry/linting (replaces absent `gemini-3.8-flash-lite` default).
   - 🪨 **Tier 5 (Deprecated / Legacy Alias)**: `gemini-3.8-flash-lite` ($0.0375 / $0.15 / $0.01) — Absent from live `swarm` inventory; kept only for historical SQLite ledger normalization.
4. **"Fast Hands, Deep Brain" Topologies**: Accelerate end-to-end execution through Scout-Then-Architect (Hydrogen scouts feed compact pointers to Neon), Fan-Out Builders with Dual Quality Gates (Hydrogen bounded builders `<= 3` files + Neon multi-file `>= 4` files & 5-axis review + Carbon security), and the 3-Stage Fast-Retry Circuit Breaker (`retry 1` Hydrogen `xhigh` ➔ `retry 2` Neon `high`/`xhigh` ➔ `retry >= 3` Carbon `high`).
5. **Automated DAG Scheduling**: Evaluate task dependencies (`depends_on`) automatically via a ready-queue evaluator for concurrent Fan-Out.
6. **Real-time Observability & Financial Analytics**: Stream subagent progress, telemetry, thoughts, and per-model token expenditure (USD & VND) via Server-Sent Events (SSE) and SQLite.
7. **Audit & Compliance Sign-off**: Export permanent post-mission execution reports in Markdown and structured JSON.

---

## Project-Specific Overrides (sub/*.md)

**Mandatory rule:** Before executing this skill, check `.agents/skills/multi-agent-orchestrator/sub/*.md`. Read all `.md` files in `sub/` (except `README.md`) alphabetically. Project rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (while keeping ProjectBrain MCP verification steps intact). Never edit `SKILL.md` for project-only rules — write them in `sub/<name>.md` (or use `create-project-skill`) so `sub/` survives system skill upgrades.

### Mandatory `sub/*.md` Subagent Delegation Rules

1. **Propagate `sub/*.md` to Every Dispatched Worker**: Whenever the Orchestrator assigns a persona (`spec-engineer`, `task-planner`, `builder`, `test-engineer`, `code-reviewer`, `security-auditor`, `doc-generator`, `debugger`, `performance-auditor`), the task brief MUST identify the corresponding `.agents/skills/<skill>/SKILL.md` AND instruct the worker to read `.agents/skills/<skill>/sub/*.md` (all `.md` files except `README.md`, alphabetically) before executing.
2. **Project-Level Override Governance**: Project-wide routing rules, budget ceilings, `project_id` / `source_id` mappings, and disjoint worktree ownership rules belong in `.agents/skills/multi-agent-orchestrator/sub/*.md` (never edited directly into `SKILL.md`).
3. **Zero Silent Relaxation**: Project overrides in `sub/*.md` may tighten gates, swap paths, or tune model tiers, but MUST NOT bypass Rule 1 Security Audits or ProjectBrain MCP cross-source verification.

---

## ProjectBrain Tooling Integration

Coordinate multi-source parallel engineering using ProjectBrain's hub topology, blast-radius guards, and delta graph syncs alongside the Orchestrator DAG:

1. **Hub Initialization & Source Inventory (Pre-Dispatch)**:
   - `projectbrain_discover_workspace_sources(workspace_root=".")` — scan services, frontends, workers, and shared docs before building the DAG.
   - `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True, upgrade_mode="delta", root_branch="main")` — zero-touch provisioning of project DB, sub-sources, AST graphs, `__shared__` specs, and cross-source contract edges.
   - `projectbrain_list_sources(project_id="<project_id>")` — fetch canonical `source_id`s so parallel builders stay strictly partitioned by source and disjoint file paths.
2. **Contract Blast-Radius & Context-Minimized Briefings**:
   - `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol, endpoint, or schema>", source_id="<source_id>", branch="main", relation_depth=2)` — check affected sibling services and `drifted_relations` before fanning out parallel builders across API/frontend boundaries.
   - `projectbrain_hybrid_query(query="<slice objective>", project_id="<project_id>", branch="main", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` — pass concise file pointers and shared doc keys in worker briefs instead of pasting large source files.
3. **Post-Wave Graph Reconciliation & Audit**:
   - `projectbrain_sync_graphify(workspace_path="<subpath>", project_id="<project_id>", branch="<branch>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)` — incrementally update each modified source after Fan-In and check for newly introduced `drifted`/`stale` contract edges.

---

## 2. The 9 Specialized Personas (1-Agent = 1-Skill)

Each agent persona maps strictly to a single skill specialty. Agents never cross functional boundaries unless explicitly delegated.

```
+-----------------------------------------------------------------------------------+
|                           Multi-Agent Orchestrator                                |
|     (gemini-pro-orchestrator-neon, Smart Routing, DAG Ready Queue, SSE Telemetry) |
+-----------------------------------------------------------------------------------+
                                          |
   +--------------------------------------+-----------------------------------+
   |                    |                 |                 |                 |
   v                    v                 v                 v                 v
[1. Spec]          [2. Plan]         [3. Build]        [4. Test]        [5. Review]
Product Spec       Architect         Software Builder  Test Spec        Code Review
Gemini 4 Neon      Gemini 4 Neon     Hydrogen (<=3f)   Gemini 4 Hydrogen Gemini 4 Neon
(effort: high)*    (medium/high)     Neon (>=4f)       (effort: medium) (effort: high)
   |                    |                 |                 |                 |
   +--------------------+-----------------+-----------------+-----------------+
                                          |
                       +--------------------+--------------------+
                       |                                         |
                       v                                         v
                [6. Security]                              [7. Release]
                Security Specialist                        DevOps & Deployment
                Gemini 4 Carbon (Effort: high)             Gemini 4 Hydrogen / Neon
```
*\*Security/critical specs stay locked on `Gemini 4 Carbon` (`high`).*

| # | Persona | Primary Skill & Responsibility | Recommended Model & Effort | Output Artifact | Workflow Runbook |
|---|---------|--------------------------------|----------------------------|-----------------|------------------|
| 1 | **Multi-Agent Orchestrator** | Task scheduling, dependency resolution, SSE telemetry, context dispatch | ⚡✦ **Gemini 4 Neon** (`gemini-pro-orchestrator-neon`, `medium`/`high`) | Orchestrator Plan, DAG | [`workflow-pipeline.md`](../../workflows/workflow-pipeline.md) |
| 2 | **Product / Spec Specialist** | Requirements discovery, user stories, acceptance criteria, edge-case matrix | ⚡✦ **Gemini 4 Neon** (`high`; ✦ **Carbon** `high` if security/critical) | `docs/specs/{feature_id}.spec.md` | [`workflow-spec.md`](../../workflows/workflow-spec.md) |
| 3 | **Architect / Planner** | System design, contract definition, database schemas, component topology | ⚡✦ **Gemini 4 Neon** (`medium`/`high`) | `docs/plans/{feature_id}.plan.md` | [`workflow-plan.md`](../../workflows/workflow-plan.md) |
| 4 | **Software Builder** | Code implementation, UI/API development, minimal blast radius edits | ⚡ **Gemini 4 Hydrogen** (`<= 3` files, `medium`/`high`/`xhigh`) / ⚡✦ **Neon** (`>= 4` files) | Source Code Files on disk | [`workflow-build.md`](../../workflows/workflow-build.md) |
| 5 | **Test Specialist** | Test suites, mocks, unit tests, integration tests, assertions | ⚡ **Gemini 4 Hydrogen** (`medium`; `xhigh` retry 1) | `docs/reports/test_report.json` | [`workflow-test.md`](../../workflows/workflow-test.md) |
| 6 | **Code Reviewer** | 5-axis code quality inspection, anti-pattern detection, invariants | ⚡✦ **Gemini 4 Neon** (`high`) | `docs/reviews/review_signoff.md` | [`workflow-review.md`](../../workflows/workflow-review.md) |
| 7 | **Security Specialist** | Threat modeling, OWASP verification, secret scanning, RBAC checks | ✦ **Gemini 4 Carbon** (`high` — Rule 1 locked; optional Neon CWE pre-scan) | `docs/security/security_audit.md` | [`workflow-security.md`](../../workflows/workflow-security.md) |
| 8 | **Root-Cause Debugger** | Deep failure isolation, complex multi-component diagnostics | ⚡✦ **Gemini 4 Neon** (`high`/`xhigh`; ✦ **Carbon** if critical/security) | Diagnostic Report & Fix Plan | - |
| 9 | **Observability / Telemetry** | Instrumentation, heartbeat logging, quick formatting, dead code linting | ⚡ **Gemini 3.8 Flash** (`low`) | Telemetry Logs & Metrics | - |

---

## 3. Handoff Pipeline & Artifact Data Contracts (Output(N) ➔ Input(N+1))

Every stage delivers an explicit, structured file artifact that serves as the strictly bounded input for the next stage. Raw conversation histories are never passed directly to subagents; instead, only the target artifact path is provided:

```
[User Request]
       │
       ▼
[Stage 1: Spec] ──▶ docs/specs/{feature_id}.spec.md (Gemini 4 Neon, effort: high; Carbon if security)
                           │
                           ▼
                   [Stage 2: Plan] ──▶ docs/plans/{feature_id}.plan.md (Gemini 4 Neon)
                                               │
                                               ▼
                                      [GET /api/dag/ready] ──▶ Evaluates Unblocked Tasks
                                               │
                                               ▼
                                      [Stage 3: Parallel Builders] ──▶ Disjoint Code Files (Hydrogen <=3f, 198 tok/s | Neon >=4f)
                                                                               │
                                                                               ▼
                                      [Stage 4: Tests] ◀── (Code + Spec AC) ──▶ docs/reports/test_report.json (Gemini 4 Hydrogen)
                                               │
                                               ▼
                                      [Stage 5: Review] ──▶ docs/reviews/review_signoff.md (Gemini 4 Neon, effort: high)
                                                                      │
                                                                      ▼
                                                              [Stage 6: Security] ──▶ docs/security/security_audit.md (Gemini 4 Carbon, effort: high)
                                                                                              │
                                                                                              ▼
                                                                                      [Audit Export & Release]
```

---

## 4. Periodic Table Intelligent Model Routing Matrix

To maximize precision, throughput, and financial economy, tasks are dynamically routed across the Periodic Table model tiers:

| Tier | Engine Model | Canonical ID | Input / 1M | Output / 1M | Cache Read / 1M | Tunable Reasoning Effort | Role Specialization & Rationale |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Tier 1: Frontier Security** | **Gemini 4 Carbon** | `gemini-4-carbon` | **$2.00** *(+$4 Dec 1)* | **$10.00** *(+$20 Dec 1)* | **$0.50** | `low`, `medium`, `high` | • `security-auditor`: Deep OWASP/auth/crypto/CVE/migration audits (Rule 1 invariant).<br>• `critical` complexity & security specs.<br>• Circuit Breaker Stage 3 (`retry_count >= 3`) final escalation. |
| **Tier 1.5 / 2: SWE Frontier (`mid_frontier`)** | **Gemini 4 Neon (NEW)** | `gemini-4-neon` | **$3.00** | **$15.00** | **$0.15** *(95% off)* | `low`, `medium`, `high`, `xhigh` | • Coding Agent Index #1 (`58.64`), Intelligence `61` (`xhigh`), `~105 tok/s`, native `gemini-pro-orchestrator-neon`.<br>• Replaces Argon & takes over non-security `spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, multi-file `builder` (`>= 4` files), & CB Stage 2 (`retry == 2`) at ~32% lower cost/task than Carbon. |
| **Tier 2b: Legacy Deep Reasoning** | **Gemini 4 Argon** | `gemini-4-argon` | **$1.25** *(+$4 Dec 1)* | **$5.00** *(+$20 Dec 1)* | **$0.3125** | `low`, `medium`, `high` | • Superseded by `gemini-4-neon` across default SWE routing.<br>• Retained for historical SQLite telemetry & explicit legacy fallback. |
| **Tier 3: High-Throughput Workhorse** | **Gemini 4 Hydrogen** | `gemini-4-hydrogen` | **$1.50** | **$7.50** | **$0.075** *(95% off)* | `low`, `medium`, `high`, `xhigh` | • Bounded parallel `builder` (`<= 3` files), `ui-builder`, `test-engineer`, `scout` at 198 tok/s (~2x faster than Neon, avoids ~40s TTFT).<br>• `Orchestrator (Neon)` + `Hydrogen` saves ~24% quota.<br>• Circuit Breaker Stage 1 (`retry_count == 1`, `xhigh`). |
| **Tier 4: Cost-Efficient & Active Micro-Tier** | **Gemini 3.8 Flash** | `gemini-3.8-flash` | **$0.75** *(live)* | **$3.75** *(live)* | **$0.075** | `low`, `medium`, `high` | • Active default for `cost_saving` builders, `doc-generator`, `code-simplifier` (`low`), and `observability` (replaces absent `3.8-flash-lite` default). |
| **Tier 5: Deprecated / Legacy Alias** | **Gemini 3.8 Flash-Lite** | `gemini-3.8-flash-lite` | **$0.0375** | **$0.15** | **$0.01** | `None` | • Absent from live `swarm` runtime; retained strictly as registry alias for historical DB rows. |
| **Tier 1.5 / Peer Offload** | **Gemini Desktop App** (`EXTERNAL_WORKERS`) | `gemini-desktop-app` | **$0.00** | **$0.00** | **$0.00** | `None` | • **Conditional 60% Peer Model (`ORCHESTRATOR_DESKTOP_SHARE=0.60`)**: When `desktop-doctor` / `GET /api/desktop/status` reports `ready == true` (macOS/Windows), operates as a first-class peer model (`mode="task"`, `background=True`, `allow_edits=True/False`, `0` focus switches, `$0.00` AGY API spend) for sequential heavy stages (`spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, `doc-generator`) + **at most 1 heavy builder per `parallel_group`**.<br>• Automatically falls back 100% to the pure API fleet when `ready == false` or `--no-desktop` / `--api-only` is set. |

### Tunable Reasoning Effort Guidelines (`--reasoning-effort` / `--effort`)
- **`xhigh`** (Supported on **`gemini-4-neon`** and **`gemini-4-hydrogen`**): Maximum depth thinking for challenging algorithmic edge cases, multi-file debugging, 5-axis quality-first reviews, and Stage 1 self-healing retries. *(Clamped to `high` on Carbon/Argon).*
- **`high`**: Activates deep multi-pass reasoning, autonomous verification loops, and vulnerability fuzzing. Use for `security-auditor` on Carbon, `spec-engineer` / `code-reviewer` / `debugger` on Neon, and bounded `builder` tasks on Hydrogen.
- **`medium`** (Default): Balanced deep reasoning for `orchestrator`, `task-planner`, multi-file `builder` (`>= 4` files) on Neon, and bounded `builder`/`test-engineer` on Hydrogen.
- **`low`**: Light reasoning check for `gemini-3.8-flash` micro-tier tasks, fast Hydrogen `scout` runs, and `speed_first` Neon planning.

---

## 5. "Fast Hands, Deep Brain" Advanced Topologies

To maximize fleet velocity while preserving mathematical correctness and security invariants, the orchestrator applies four advanced multi-agent topologies:

### Topology A: Scout-Then-Architect (Token Minimization)
1. **Parallel Fast Scouting**: The orchestrator dispatches 2 to 4 `gemini-4-hydrogen` scouts (`effort: low`).
2. **Compact URI Pointers**: Scouts search the codebase using ripgrep and AST tools, returning strictly compact URI pointers (`file:///path/to/file#L10-L25`) and summary cards without dumping raw code files into the chat context.
3. **Deep Synthesis**: `gemini-4-neon` receives the consolidated pointer summary (< 4,000 tokens), avoiding context bloat, and crafts the master architecture specification and task DAG at `~105 tok/s` (2x Argon speed).
4. **Efficiency Impact**: Cuts scout phase time from 3 minutes to 45 seconds and slashes prompt context tokens fed to the architect by **80%**.

### Topology B: Fan-Out Hydrogen Builders + Neon Brain + Carbon Security Gate
1. **Contract Locking**: `gemini-4-neon` (`gemini-pro-orchestrator-neon` / `task-planner`) designs the feature specification and locks disjoint file contracts.
2. **Massive Parallel Implementation**: Up to 8 `gemini-4-hydrogen` workers (`group: BUILD-PARALLEL`, `file_count <= 3`, `effort: medium`/`high`) implement assigned file slices concurrently at 198 tok/s (~24% quota saved), while complex multi-file refactors (`file_count >= 4` or `complexity == "high"`) route to `gemini-4-neon`.
3. **High-Speed Test Execution**: A `gemini-4-hydrogen` test engineer executes unit/integration test suites against spec acceptance criteria.
4. **Dual Quality Gate**:
   - `gemini-4-neon` (`effort: high`) performs the 5-axis code quality and architecture review (and optional CWE pre-scan).
   - `gemini-4-carbon` (`effort: high`) executes the final Rule 1 OWASP security audit before production release.

### Topology C: 3-Stage Fast-Retry & Escalation Circuit Breaker
```
            [Hydrogen Builder / Fixer]
                       │
                 Executes Task
                       │
                    Result?
                  /        \
               PASS        FAIL
               /              \
       [Next DAG Stage]    Retry / Security?
                            /      |       \
                     retry==1   retry==2   retry>=3 OR Security
                        /          |             \
           Stage 1 Self-Fix   Stage 2 Escalation  Stage 3 Trip Hard
          (Hydrogen, xhigh)   (Neon, high/xhigh)   (Carbon, high)
```
1. **Attempt 0 (`retry_count: 0`)**: Task executes on `gemini-4-hydrogen` (`effort: medium` or `high`).
2. **Stage 1 (`retry_count: 1`)**: Fast self-healing retry turn on `gemini-4-hydrogen` with elevated effort (`effort: xhigh`).
3. **Stage 2 (`retry_count: 2`)**: Escalates to **`gemini-4-neon`** (`high`, or `xhigh` if `complexity == "high"`; `status: "escalated_neon"`), resolving tough bugs 2x faster and at 32% lower cost than Carbon.
4. **Stage 3 (`retry_count >= 3`)**: Trips circuit breaker hard to **`gemini-4-carbon`** (`reasoning_effort: high`, `status: "tripped"`) for final frontier root-cause diagnosis.
5. **Zero-Downgrade Security Guardrail**: If failure logs match security terms (`auth`, `rbac`, `secret`, `leak`, `overflow`, `crypto`), circuit breaker trips immediately to Carbon without intermediate retries.

### Topology D: Smart Router Budget Modes (including Hybrid Desktop Offload)
- **`balanced` (Default)**: Orchestrator, Specs, Plans, 5-Axis Reviews, non-critical Debugging, and multi-file Builders (`>= 4` files) on `gemini-4-neon`; bounded Builders (`<= 3` files), Testers & Scouts on `gemini-4-hydrogen` (`198 tok/s`); Micro-tier/Docs on `gemini-3.8-flash` (`low`); Security strictly on `gemini-4-carbon`.
- **`speed_first` / `latency_sensitive`**: Bounded Builders, Testers, Docs, and medium-complexity Reviews (`xhigh`) on `gemini-4-hydrogen`; Orchestrator/Specs/Plans/Debugging on `gemini-4-neon` (`low`/`medium`); Security strictly on Carbon.
- **`quality_first`**: Medium Builders & Reviews (`xhigh`) on `gemini-4-neon`; `spec-engineer`, high-complexity planning, and Security on `gemini-4-carbon`.
- **`cost_saving` / `cost_first`**: Bounded Builders & Micro-tier on `gemini-3.8-flash` (`low`); heavy non-orchestrator roles on `gemini-4-hydrogen` (`xhigh`); Orchestrator on `gemini-4-neon` (`low`); Security strictly on Carbon.
- **`hybrid_desktop` / `offload_heavy_to_desktop`**: Evaluates **Rule 1.5 (Desktop Offload Gate)** after Rule 1 (Security Guardrail) & Rule 2 (Circuit Breaker), using the **Turnkey Dual-Channel 60% `gemini-desktop-app` Peer Allocation Policy**:
  1. **Mandatory Turn-1 Pre-Flight Gate (`client.py preflight` / `desktop-doctor` / `GET /api/desktop/status`)**:
     - **When Gemini Desktop App IS installed & ready (`EXECUTION_MODE: HYBRID_60_DESKTOP`, `desktop_ready == true`)**: `gemini-desktop-app` operates as a **first-class peer model** (`mode="task"`, `background=True`, `allow_edits=True/False`, `0` focus switches, `$0.00` AGY API token spend) prioritized for **~60% of total SWE workload** (`ORCHESTRATOR_DESKTOP_SHARE=0.60`):
       - **Channel A Dispatch (`run_command`, `WaitMsBeforeAsync=500`)**: Because `invoke_subagent` only accepts AGY API models (`inherit`/`pro`/`flash`/`flash_lite`) and has NO desktop model option, Channel A tasks (`spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, `doc-generator` + **at most 1 heavy builder per `parallel_group`** with `--allow-edits`) MUST execute via `run_command(WaitMsBeforeAsync=500)` calling `python3 ~/.gemini/tools/orchestrator/client.py desktop-ask ...` (or via a thin `Model="flash_lite"` wrapper that solely runs `client.py desktop-ask`).
       - **Channel B Dispatch (`invoke_subagent`, concurrent ~40% share)**: Sibling parallel builders (`<=3` files), `test-engineer`, `scout` on `Model="flash"` (`gemini-4-hydrogen` `198 tok/s`); Neon spillover and **Rule 1 Security Audits** (`security-auditor`, `has_security_impact`, `critical` complexity) on `Model="pro"` (`gemini-4-carbon` / `gemini-4-neon`).
     - **When Gemini Desktop App is NOT installed or NOT ready (`EXECUTION_MODE: API_ONLY_100`, `desktop_ready == false`)** — or `--no-desktop` / `--api-only` is passed: Automatically and seamlessly falls back **100% to `invoke_subagent` across the pure API fleet (`Gemini 4 Neon` + `Gemini 4 Hydrogen` + `Gemini 4 Carbon` + `Gemini 3.8 Flash`)**.
  2. **DAG Peer Allocation (`preflight --tasks-json ...` / `allocate-dag` / `POST /api/router/allocate-dag`)**: Automatically partitions DAG tasks across Channel A `gemini-desktop-app` (~60% target share) and Channel B AGY subagents (~40% concurrent share) while preserving the single-window FIFO lock and Rule 1 Security Guardrail.

### Smart Router API & CLI
Run Turn-1 preflight first, and query the smart router before assigning workers:
```bash
# 0. Mandatory Turn-1 Pre-Flight Gate (auto-starts :8484 if needed, probes desktop_ready, outputs Dual-Channel directives)
python3 ~/.gemini/tools/orchestrator/client.py preflight --workspace .
python3 ~/.gemini/tools/orchestrator/client.py preflight --tasks-json tasks.json --apply --workspace .
python3 ~/.gemini/tools/orchestrator/client.py preflight --no-desktop --workspace .

# Query CLI with budget mode and retry count
python3 ~/.gemini/tools/orchestrator/client.py recommend \
  --agent builder \
  --complexity medium \
  --budget-mode speed_first \
  --retry-count 1

# Hybrid Desktop offload check for heavy read-only synthesis
python3 ~/.gemini/tools/orchestrator/client.py recommend \
  --agent spec-engineer \
  --complexity high \
  --budget-mode hybrid_desktop \
  --desktop-available \
  --read-only

# Pre-flight & Conditional 60% Peer DAG Allocation (ORCHESTRATOR_DESKTOP_SHARE=0.60)
python3 ~/.gemini/tools/orchestrator/client.py desktop-doctor --format text
ORCHESTRATOR_DESKTOP_SHARE=0.60 python3 ~/.gemini/tools/orchestrator/client.py allocate-dag --target-desktop-share 0.60 --apply

# Force 100% pure API fleet fallback (disables Desktop allocation)
python3 ~/.gemini/tools/orchestrator/client.py allocate-dag --no-desktop --apply
python3 ~/.gemini/tools/orchestrator/client.py allocate-dag --api-only --apply

# Channel A Dispatch to local Gemini Desktop App ($0.00 AGY API spend, 0 focus switches)
# Task mode (default) mounts workspace via Folders (ConnectedFolderNSMenu) — reference relative paths concisely
python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
  --task SPEC-01 \
  --agent spec-engineer \
  --mode task \
  --workspace . \
  --prompt "Inspect docs/specs/draft_brief.md and packages/orchestrator/router.py. Return a read-only critique without editing any files." \
  --driver auto

# Mutating heavy builder slice on Channel A (max 1 per parallel_group): include --allow-edits
python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
  --task BUILD-01 \
  --agent builder \
  --mode task \
  --workspace . \
  --allow-edits \
  --prompt "Implement BUILD-01 on packages/orchestrator/router.py cleanly and report changed files."

# Or standalone Chat mode without mounting a workspace folder
python3 packages/orchestrator/client.py desktop-dispatch \
  --task SPEC-02 \
  --mode chat \
  --prompt-file docs/specs/draft_brief.md \
  --async

# REST API: POST /api/desktop/dispatch (Task mode + workspace binding)
# {"task_id": "SPEC-01", "mode": "task", "workspace": ".", "prompt": "...", "driver": "auto", "async_mode": true}

# Security task always routes to Carbon regardless of budget mode
python3 packages/orchestrator/client.py recommend \
  --agent security-auditor \
  --complexity critical \
  --security-impact

# REST API: POST /api/router/recommend
# Request Body:
# {
#   "agent_type": "builder",
#   "complexity": "medium",
#   "has_security_impact": false,
#   "budget_mode": "speed_first",
#   "retry_count": 0
# }
# Response:
# {
#   "status": "ok",
#   "recommendation": {
#     "model": "gemini-4-hydrogen",
#     "reasoning_effort": "high",
#     "tier": "high_throughput",
#     "rationale": "Parallel code implementation... dispatched to Gemini 4 Hydrogen for 2.5x speed and 198 tok/s throughput.",
#     "estimated_cost_tier": "$$$",
#     "fallback_model": "gemini-3.8-flash"
#   }
# }
```

---

## 6. Parallel Worker Execution & Automated DAG Ready-Queue

The Orchestrator implements a **Condition-Based Directed Acyclic Graph (DAG)** with automated Fan-Out and Fan-In:

```
                   ┌──▶ [Parallel Worker 1: Frontend UI] (Hydrogen <=3f, 198 tok/s) ──┐
                   │                                                                   │
[Plan (Neon)] ─────┼──▶ [Parallel Worker 2: Backend API] (Hydrogen <=3f, 198 tok/s) ───┼──▶ [Barrier Sync & Fan-In]
                   │                                                                   │
                   └──▶ [Parallel Worker 3: Multi-File]  (Neon >=4f, ~105 tok/s) ──────┘
```

### Core Concurrency Invariants:
1. **Dependency Invariant (`depends_on`)**:
   - A task enters the `READY` state only when all tasks listed in `depends_on` have reached `status == "completed"`.
2. **Automated Ready-Queue Discovery**:
   - Instead of manual inspection, query `GET /api/dag/ready` or run `python3 packages/orchestrator/client.py next-ready`.
   - The endpoint automatically separates runnable unblocked tasks from blocked tasks and flags `can_fan_out: true` when multiple tasks can run concurrently.
3. **Decoupled File Scope Invariant**:
   - Parallel builder workers MUST operate on strictly disjoint, non-overlapping file paths (e.g., `packages/orchestrator/models.py` vs `dashboard.py` vs `client.py`).
4. **Barrier Synchronization (Fan-In)**:
   - When all subagents in a parallel group reach 100%, the Orchestrator synthesizes outputs, validates cross-cutting contracts, and advances to the next stage.

---

## 7. Context Minimization Protocol

Context window exhaustion is the primary source of hallucinations and drift in LLM agent swarms. The Orchestrator enforces:

1. **Pointers Over Copies**:
   - Never copy entire files into prompts.
   - Reference files using exact absolute paths and line numbers:
     `[models.py](file:///path/to/packages/orchestrator/models.py#L10-L60)`.
2. **Strictly Bounded Slices**:
   - Limit each subagent assignment to a maximum of 1-3 target files and 1 discrete objective.
3. **Structured Handoff Summaries**:
   - When a subagent completes a task, it reports:
     - Exact files modified or created.
     - Verification command and output.
     - Brief 2-3 sentence execution summary.
4. **No Sprawl**:
   - Subagents must not introduce speculative abstractions or modify unassigned files outside their scope.

---

## 8. Real-Time Telemetry & Per-Model Financial Analytics

Hospital Suite includes a zero-dependency, real-time Python dashboard powered by SSE (Server-Sent Events) and SQLite.

### Starting the Server
```bash
# Start server on default port 8484
python3 packages/orchestrator/server.py --port 8484

# Or customize port via environment variable
ORCHESTRATOR_PORT=9000 python3 packages/orchestrator/server.py
```

### Dashboard Features (`GET http://localhost:8484/`)
- **Obsidian Emerald Carbon, Neon Violet & Electric Cyan Hydrogen Badges**: Visual indicators for `gemini-4-carbon`, **Neon Violet (`#a855f7`)** for `gemini-4-neon` / `gemini-pro-orchestrator-neon`, `gemini-4-argon` (legacy), and electric cyan `#06b6d4` for `gemini-4-hydrogen` with pulsing reasoning pills (including `xhigh`).
- **Per-Model Financial Ledger**: Dedicated breakdown of token consumption (prompt, completion, cache read), USD spend, VND spend (25,400 VND/USD), and 95% cache savings across Carbon, Neon (`$3.00 / $15.00 / $0.15`), Hydrogen (`$1.50 / $7.50 / $0.075`), Argon, and 3.8 Flash.
- **Interactive SVG DAG Visualizer**: Graphical dependency graph displaying task nodes, active glowing pulse rings, and dependency arrows.
- **Smart Router Interactive Sandbox**: Test model routing recommendations with `speed_first` mode and simulate the 3-Stage Fast-Retry Circuit Breaker (`retry == 1` Hydrogen `xhigh` ➔ `retry == 2` Neon ➔ `retry >= 3` Carbon).
- **One-Click Audit Exporter**: Button to instantly download signed Markdown audit post-mortems.

### REST API Endpoints
- `GET /`: Dashboard Web UI.
- `GET /events`: Server-Sent Events stream (`text/event-stream`, including `desktop_job_queued`, `desktop_job_started`, `desktop_job_completed`, `desktop_job_failed`, `desktop_job_cancelled`).
- `GET /api/state`: Full JSON snapshot of tasks, logs, and metrics.
- `GET /api/models`: Model registry metadata for all 5 API tiers plus `external_workers` (`gemini-desktop-app`).
- `POST /api/router/recommend`: Smart model and reasoning effort recommendation (supporting `budget_mode`, `retry_count`, `desktop_available`, `read_only`, and `prefer_desktop`).
- `POST /api/router/allocate-dag`: Conditional 60% `gemini-desktop-app` peer DAG allocator (`target_desktop_share`, `ORCHESTRATOR_DESKTOP_SHARE`, `apply`, `no_desktop` / `api_only`, automatic 100% pure API fallback when `ready == false`).
- `GET /api/dag/ready`: Evaluate DAG dependencies and return ready unblocked tasks.
- `GET /api/analytics/models`: 5-tier per-model token consumption, financial breakdown, and Desktop Worker `AGY Quota Saved` totals.
- `GET /api/desktop/status`: Cross-platform Gemini Desktop App readiness probe (`macos_ax`, `windows_uia`, `linux_pwa`, `orca`, `mock`).
- `POST /api/desktop/dispatch`: Enqueue or synchronously execute a secret-redacted, sentinel-framed job on the local Gemini Desktop App (`docs/reports/desktop_worker/{job_id}.md`).
- `GET /api/desktop/jobs` & `GET /api/desktop/jobs/{job_id}`: Inspect FIFO desktop worker queue and artifact pointers.
- `POST /api/desktop/cancel` & `POST /api/desktop/jobs/{job_id}/cancel`: Cancel a queued or active desktop job cleanly.
- `GET /api/audit/export?format=markdown|json`: Export signed mission audit report.
- `POST /api/tasks`: Upsert task status with `reasoning_effort` (`xhigh`), tokens, and dependencies.
- `POST /api/logs`: Append log entry.
- `POST /api/reset`: Reset all task, log, and desktop job states.

---

## 9. Subagent Reporting Protocol & CLI Usage

Subagents report their execution lifecycle via the `client.py` CLI or Python SDK.

### CLI Usage Guide

```bash
# 1. Announce task start with model and reasoning effort (including xhigh on Hydrogen)
python3 packages/orchestrator/client.py update \
  --task TASK-01 \
  --agent builder \
  --status running \
  --progress 15 \
  --model gemini-4-hydrogen \
  --reasoning-effort high \
  --complexity medium \
  --group BUILD-PARALLEL \
  --depends-on TASK-00 \
  --scope "Frontend component implementation" \
  --notes "Initiating parallel build on disjoint file slice"

# 2. Emit telemetry logs
python3 packages/orchestrator/client.py log \
  --task TASK-01 \
  --agent builder \
  --level info \
  --message "Component rendered and passed local type checks"

# 3. Discover next ready unblocked tasks
python3 packages/orchestrator/client.py next-ready

# 4. Mark task completed with token estimates
python3 packages/orchestrator/client.py update \
  --task TASK-01 \
  --agent builder \
  --status completed \
  --progress 100 \
  --input-tokens 24000 \
  --output-tokens 1800 \
  --cache-tokens 65000 \
  --notes "Implementation complete, passed syntax check"

# 5. Export mission audit report
python3 packages/orchestrator/client.py export --format markdown --out mission_audit_report.md
```

### Python SDK Usage

```python
from packages.orchestrator.client import OrchestratorClient

client = OrchestratorClient(base_url="http://localhost:8484")

# Query Smart Router with speed_first budget mode
rec = client.recommend(
    agent_type="builder",
    complexity="medium",
    budget_mode="speed_first",
    retry_count=0
)
print(f"Recommended model: {rec['recommendation']['model']}, effort: {rec['recommendation']['reasoning_effort']}")

# Announce task start on Gemini 4 Hydrogen
client.update_task(
    task_id="TASK-01",
    agent_type="builder",
    status="running",
    progress=20,
    model="gemini-4-hydrogen",
    reasoning_effort="high",
    complexity="medium",
    scope="Core UI component implementation",
    notes="Building with 198 tok/sec velocity"
)

# Stream log or thought
client.log(
    task_id="TASK-01",
    agent_type="builder",
    level="thought",
    message="Rendering UI virtual DOM diff"
)

# Query ready queue
ready = client.next_ready()
print(f"Can fan out: {ready['can_fan_out']}, ready count: {len(ready['ready_tasks'])}")

# Complete task
client.update_task(
    task_id="TASK-01",
    agent_type="builder",
    status="completed",
    progress=100,
    input_tokens=15000,
    output_tokens=1200,
    cache_tokens=45000,
    notes="Zero syntax errors detected"
)
```

---

## 10. Verification

Before signing off on a multi-agent orchestration mission, confirm:

- [ ] Checked `.agents/skills/multi-agent-orchestrator/sub/*.md` overrides before dispatching
- [ ] Every subagent brief explicitly required reading its assigned `.agents/skills/<skill>/sub/*.md` overrides (excluding `README.md`) alphabetically
- [ ] Multi-source project hub was verified/bootstrapped (`projectbrain_bootstrap_project_hub`, `projectbrain_list_sources`) and shared-contract changes checked via `projectbrain_cross_source_impact`
- [ ] Parallel builders worked on strictly disjoint file paths / `source_id` scopes
- [ ] Post-wave delta sync (`projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)`) completed with zero unresolved `drifted` contract edges
