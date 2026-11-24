---
name: gemini-desktop-worker
description: Cross-platform Gemini Desktop App Worker Bridge (macOS Gemini.app, Windows Gemini.exe/UIAutomation, Linux PWA, and Mock CI) for offloading heavy, read-only analytical workloads at $0.00 AGY API token spend with pre-flight SecretRedactor, Clipboard Save/Restore, Sentinel anti-truncation framing, and Context-Minimized artifact pointers.
---

# Gemini Desktop Worker Skill

## 1. Overview & Purpose

The **Gemini Desktop Worker** (`gemini-desktop-app` in `EXTERNAL_WORKERS`) enables Antigravity (`agy`) orchestrators and subagents to operate `gemini-desktop-app` as a **first-class peer model** (`mode="task"`, `background=True`, `allow_edits=True/False`, `0` focus switches, `$0.00` AGY API token spend) via the developer's locally authenticated **Google Gemini Desktop App**. Under the **Conditional 60% Peer Allocation Policy** (`ORCHESTRATOR_DESKTOP_SHARE=0.60`), when `desktop-doctor` / `GET /api/desktop/status` reports `ready == true` (macOS or Windows), `gemini-desktop-app` takes ~60% of total SWE workload (sequential heavy stages + at most 1 heavy builder per `parallel_group`) while the remaining ~40% executes concurrently on the AGY API fleet (`gemini-4-hydrogen`, `gemini-4-neon`, `gemini-4-carbon`). When `ready == false` (missing app or locked screen) or `--no-desktop` / `--api-only` is set, the orchestrator seamlessly falls back **100% to the pure API fleet**. It tracks equivalent baseline savings (`AGY Quota Saved` vs `gemini-4-carbon`) and preserves strict **Context Minimization** via file pointers (`docs/reports/desktop_worker/{job_id}.md`).

### Supported Platforms & Stdlib-Only Drivers
| Platform | Driver Name | App Discovery & Automation Strategy (Zero External `pip` Packages) |
|---|---|---|
| **macOS (`darwin`)** | `macos_ax` *(or optional `orca`)* | Discovers `com.google.GeminiMacOS` / `com.google.gemini` (`/Applications/Gemini.app`, `mdfind`, PWA fallback) via `plistlib`. Checks screen lock & `AXIsProcessTrustedWithOptions(null)`. Runs in **Silent Background Mode (`background=True` default)**: opens fresh chat via `googlegemini://newchat?ext_src=app_shortcut`, selects **`Task` mode** (default) or `Chat` mode, mounts `--workspace` via daemon-thread `AXPress` on `Folders` (`ConnectedFolderNSMenu`, zero `CGEvent` cursor movement), injects prompt via direct `AXValue` write + `AXPress` submit (zero keystrokes, zero clipboard touch), passively tails `~/Library/Caches/com.google.GeminiMacOS/Logs/diagnostic*.log` (`0%` focus theft during 15–240s analysis), extracts verbatim `AXStaticText.AXDescription`, and restores `AXFrontmost` if `OmnichatWindow` was hidden (with `pbcopy`+`Cmd+V` / `Cmd+Shift+C` fallback only when needed). |
| **Windows (`win32`)** | `windows_uia` | Discovers `%LOCALAPPDATA%\Programs\Gemini\Gemini.exe`, Start Menu `.lnk` / `Get-StartApps`, or Edge/Chrome PWA. Runs in **Silent Background Mode (`background=True` default)** via `powershell.exe -NoProfile -NonInteractive -Sta -WindowStyle Minimized -ExecutionPolicy Bypass -Command -` using pure `.NET System.Windows.Automation` (`ValuePattern` + `InvokePattern` + `TextPattern`) with **`0` `SetForegroundWindow`, `0` `SendKeys`, and `0` `Clipboard`** via a private `0600` temp file (`AGY_DESKTOP_PROMPT_FILE`). |
| **Linux (`linux`)** | `linux_pwa` | Detects `$DISPLAY` or `$WAYLAND_DISPLAY`, launches Chromium/Chrome/Edge `--app=https://gemini.google.com --new-window`, injects via `wl-copy`/`xclip`/`xsel` + `xdotool`/`wtype`, restores clipboard, and degrades gracefully on headless hosts with `--driver mock` guidance. |
| **CI / Headless** | `mock` | Deterministic zero-GUI simulator (`MockGeminiDriver`) with configurable `MOCK_DELAY_MS` and fault-injection tags (`[MOCK_TIMEOUT]`, `[MOCK_ERROR]`, `[MOCK_PARTIAL]`, `[MOCK_CHROME]`). Never touches OS windows or clipboard. |

---

## Project-Specific Overrides (sub/*.md)

**Mandatory rule:** Before executing this skill, check `.agents/skills/gemini-desktop-worker/sub/*.md`. Read all `.md` files in `sub/` (except `README.md`) alphabetically. Project rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (while keeping ProjectBrain MCP verification steps intact). Never edit `SKILL.md` for project-only rules — write them in `sub/<name>.md` (or use `create-project-skill`) so `sub/` survives system skill upgrades.

## ProjectBrain Tooling Integration

Pair Gemini Desktop App offload with ProjectBrain to keep prompts concise, prevent cross-source blind spots, and persist context-minimized analytical reports:

1. **Pre-Distill Context Before Desktop Dispatch**:
   - `projectbrain_list_sources(project_id="<project_id>")` and `projectbrain_hybrid_query(query="<task focus>", project_id="<project_id>", branch="main", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` — fetch exact file paths, shared docs, and cross-source edges so the `desktop-ask` prompt only cites relevant relative paths rather than oversized context dumps.
   - `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol or route>", source_id="<source_id>", branch="main", relation_depth=2)` — surface sibling-service constraints in read-only critique briefs.
   - `projectbrain_hardware_status()` — inspect local hardware readiness alongside `desktop-doctor` when deciding between local Graphify/AI execution and desktop offload.
2. **Persist & Sync Desktop Worker Outputs**:
   - `projectbrain_upload_document(file_path="docs/reports/desktop_worker/<job_id>.md", project_id="<project_id>", branch="main", doc_key="desktop_<job_id>", scope="source", doc_role="reference")` or `projectbrain_store(content="<summary>", user_id="<project_id>", tags=["desktop-worker", "<task_id>"])` — archive distilled, secret-redacted findings into ProjectBrain memory instead of pasting long transcripts into agent chat.
   - `projectbrain_sync_graphify(workspace_path="<subpath>", project_id="<project_id>", branch="<branch>", git_dirty_only=True, source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)` — sync deltas immediately after any bounded `allow_edits=True` desktop builder task completes.

---

## 2. Interaction Modes & Direct Workspace Binding (`Task` vs. `Chat`)

`Gemini.app` supports two distinct interaction modes controlled by `--mode` (CLI) or `mode` (REST API `POST /api/desktop/dispatch`):

| Mode | Flag / Value | Default? | Workspace Access | Best Use Case |
|---|---|---|---|---|
| **`Task` Mode** | `--mode task` | **Yes (Default)** | Mounts local `--workspace` via `Folders` (`ConnectedFolderNSMenu`) | Repository-wide architecture reviews, multi-file root-cause synthesis, spec critiques referencing relative project paths. |
| **`Chat` Mode** | `--mode chat` | No | Standalone prompt window (no folder mounted) | Self-contained conceptual prompts, pasted log snippets, or standalone design questions without workspace attachment. |

### 2.1 Direct Workspace Connection (`--workspace <path>`)
- **Auto-Default**: `--workspace` automatically defaults to `os.getcwd()` when omitted (pass `--workspace .` or `--workspace /path/to/project` to bind explicitly).
- **Zero-Copy Prompting via `Folders` (`ConnectedFolderNSMenu`)**: Because Gemini App in **`Task` mode** mounts the local project directory directly through its `Folders` (`ConnectedFolderNSMenu`) menu, AGY orchestrators and subagents do **NOT** need to copy huge source files into prompts or `--context-file` payloads.
- Instead, dispatch a concise prompt referencing **relative file paths** (for example, `packages/orchestrator/router.py` or `docs/specs/my_feature.spec.md`) with `--mode task --workspace /path/to/project` and let `Gemini.app` inspect the workspace directly.

### 2.2 Execution Scope Contract in `Task` Mode (`allow_edits=True/False`)
Direct workspace binding gives `Gemini.app` direct visibility into project files, governed by the `allow_edits` flag (`allow_edits=False` default for analytical stages; `allow_edits=True` permitted for at most 1 heavy builder per `parallel_group` when `ready == true`):
1. **Read-Only Analysis (`allow_edits=False`, Default for Spec/Plan/Review/Debug)**: `Gemini.app` inspects the mounted workspace directly for analysis, critique, and synthesis, writing **zero edits** to any workspace file.
2. **Bounded Heavy Builder (`allow_edits=True`, Max 1 per `parallel_group`)**: When allocated by `allocate-dag` (`ORCHESTRATOR_DESKTOP_SHARE=0.60`), at most **1 heavy builder slice per `parallel_group`** may run on `gemini-desktop-app` (`mode="task"`, `background=True`, `allow_edits=True`) on strictly disjoint target files, ensuring `Gemini.app`'s single-window FIFO lock never blocks sibling `gemini-4-hydrogen` builders.
3. **Avoid Confirmation Blocks & Emit Sentinel Artifacts**: Prompts must stay bounded to their assigned scope (never unbounded shell/git mutations) so `Gemini.app` does not stall on interactive approval dialogs, and analytical outputs are persisted inside the Split-Literal Sentinel envelope at `docs/reports/desktop_worker/{job_id}.md`.

### 2.3 Silent Background Execution (`--background` Default `True` & `--allow-foreground`)
By default (`--background`, `background=True`), the Desktop Worker runs with **zero focus theft** so developers can keep typing in their IDE, browser, or terminal uninterrupted during 15–240s heavy analysis:

- **macOS (`macos_ax`) Zero-Focus-Theft Pipeline (`0` Focus Switches)**:
  1. **Stealth `OmnichatWindow` Hydration**: If `OmnichatWindow` is closed or in `MinichatWindow` (`AXSystemDialog`), opens `OmnichatWindow` via `AXMenuBar -> File -> Open App` (`AXPress`) with `AXHidden=True` — **0 `AXFrontmost` transitions, 0 `open -a` focus theft**.
  2. **Daemon-Thread `Folders` Selection**: Opens `Folders` (`ConnectedFolderNSMenu`) via background daemon-thread `AXPress` with **zero `CGEvent` cursor movement**.
  3. **Direct `AXValue` Write + `AXPress` Submit**: Writes the Sentinel-wrapped prompt directly into the input `AXTextArea` via `AXValue` and triggers submission via `AXPress` (**zero synthetic keystrokes, zero clipboard touch**).
  4. **Passive AX & Diagnostic Log Tailing**: Passively inspects `AXHidden` `OmnichatWindow` via `poll_chat` and tails `~/Library/Caches/com.google.GeminiMacOS/Logs/diagnostic*.log` (`removeStreamingConversation()` / `agentModeTask: ended` + quiet debounce in Task mode so intermediate stream turns never bounce windows).
  5. **Verbatim `AXStaticText.AXDescription` Extraction**: Reads the completed response directly from `AXStaticText.AXDescription` / `AXButton.AXDescription` with `0` focus switches and `0` clipboard mutations.
- **Windows (`windows_uia`) Zero-Focus-Theft Pipeline**:
  - Uses pure `.NET UIAutomation` (**`ValuePattern` + `InvokePattern` + `TextPattern`**) launched with `-WindowStyle Minimized` (`0` `SetForegroundWindow`, `0` `SendKeys`, `0` `Clipboard`).
- **`--allow-foreground` (Opt-in Only)**:
  - Foreground activation is disabled by default (`allow_foreground=False`). Pass `--allow-foreground` **only once** when registering a brand-new unbookmarked folder via the macOS `NSOpenPanel` (`Add new folder…`) dialog; all subsequent runs in that workspace execute silently in the background.

---

## 3. Conditional 60% Peer Allocation Policy & Task Fit Matrix

### 3.1 Pre-Flight Detection Gate (`desktop-doctor` / `GET /api/desktop/status`)
1. **When Gemini Desktop App IS installed & ready (`ready == true` on macOS or Windows)**:
   - `gemini-desktop-app` operates as a **first-class peer model** (`mode="task"`, `background=True`, `allow_edits=True/False`, `0` focus switches, `$0.00` AGY API token spend) prioritized for **~60% of total SWE workload** (`ORCHESTRATOR_DESKTOP_SHARE=0.60`):
     - **Sequential Heavy Stages**: `spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, and `doc-generator`.
     - **Parallel Fan-Out Cap**: **At most 1 heavy builder per `parallel_group`** (`allow_edits=True`), guaranteeing `Gemini.app`'s single-window FIFO lock NEVER blocks concurrent Fan-Out.
   - **Remaining ~40% Runs Concurrently on AGY API**:
     - `gemini-4-hydrogen` (`198 tok/s`) for sibling parallel builders, `test-engineer`, and `scout`.
     - `gemini-4-neon` spillover when the Desktop FIFO queue is busy or for additional multi-file slices.
     - `gemini-4-carbon` (`high`) strictly locked on **Rule 1 Security Audits**.
2. **When Gemini Desktop App is NOT installed or NOT ready (`ready == false`, e.g., missing app or locked screen)** — or `--no-desktop` / `--api-only` is passed:
   - Automatically and seamlessly falls back **100% to the pure API fleet (`Gemini 4 Neon` + `Gemini 4 Hydrogen` + `Gemini 4 Carbon` + `Gemini 3.8 Flash`)** just as before.

### 3.2 Workload Fit Matrix
| Workload Type | Route to `gemini-desktop-app`? | Recommended Tier / Rule |
|---|---|---|
| **Sequential Heavy Stages (`spec-engineer`, `task-planner`, `code-reviewer`, `debugger`, `performance-auditor`, `doc-generator`)** | **YES (~60% Peer Share)** | Offload when `ready == true` (`mode="task"`, `background=True`, `allow_edits=False`). Saves `$0.20–$2.50+` per run at `$0.00` AGY API spend. |
| **Heavy Builder Slice in `parallel_group`** | **YES (Max 1 / group)** | At most **1** heavy builder per `parallel_group` (`mode="task"`, `allow_edits=True`, disjoint files) so the single-window FIFO lock never blocks sibling `gemini-4-hydrogen` (`198 tok/s`) builders. |
| **Sibling Parallel Builders, `test-engineer`, `scout` (~40% API Share)** | **NO (Run on AGY API)** | Keep on **`gemini-4-hydrogen`** (`198 tok/s`) or **`gemini-4-neon`** spillover for true parallel Fan-Out concurrency. |
| **Security Audits / Unredacted Auth, Secrets, PII** | **NEVER** | **Rule 1 Guardrail**: `has_security_impact=True` or security keywords (`auth`, `crypto`, `jwt`, `owasp`, `cve`, `rbac`) MUST stay strictly locked on **`gemini-4-carbon`** (`high`). Use `--strict-secrets` whenever accidental secret presence must abort. |
| **Low-Latency Telemetry / Micro-Linting** | **NEVER** | GUI round-trip (`10s–180s`) is unsuitable for sub-second tasks; keep on **`gemini-3.8-flash`**. |

---

## 4. Pre-Flight Setup & Diagnostics (`desktop-doctor`)

Always verify local desktop readiness before dispatching jobs in `hybrid_desktop` mode.

### 4.1 CLI Pre-Flight Check
```bash
# Via Orchestrator Server (or standalone desktop_worker.py doctor)
python3 packages/orchestrator/client.py desktop-doctor --format text
python3 packages/orchestrator/client.py desktop-doctor --json

# Standalone check without server.py running:
python3 packages/orchestrator/desktop_worker.py doctor --json
```

### 4.2 Platform Permission Checklist
- **macOS (`darwin`)**:
  1. Ensure `/Applications/Gemini.app` (or `~/Applications/Gemini.app`) is installed and signed in.
  2. Grant Accessibility access to your terminal / Antigravity host in **System Settings -> Privacy & Security -> Accessibility**.
  3. Keep the screen unlocked during execution (`CGSSessionScreenIsLocked` fails fast to avoid blind timeouts).
  4. Optional: set `GEMINI_PREFER_ORCA=1` if you want `orca computer` prioritized over `macos_ax` when `orca` is on `PATH`.
- **Windows (`win32`)**:
  1. Ensure `Gemini.exe` is installed (`%LOCALAPPDATA%\Programs\Gemini\Gemini.exe`) or Gemini is installed as an Edge/Chrome PWA and signed in.
  2. Ensure `powershell.exe` is available (requires `-Sta` mode for `.NET` Clipboard access, handled automatically by `WindowsGeminiDriver`).
- **Linux (`linux`)**:
  1. Run inside an active X11 (`$DISPLAY`) or Wayland (`$WAYLAND_DISPLAY`) session with Chromium/Chrome/Edge and `wl-copy`/`xclip` + `xdotool`/`wtype`.
  2. On headless CI runners, pass `--driver mock` (or `GEMINI_DESKTOP_DRIVER=mock`).

---

## 5. Execution Protocol & Context Minimization

### 5.1 Smart Router & Conditional 60% Peer DAG Allocator
Run the pre-flight gate and allocate your DAG across `gemini-desktop-app` (~60%, `ORCHESTRATOR_DESKTOP_SHARE=0.60`) and the AGY API fleet (~40%)—with automatic 100% pure API fallback when `ready == false`:
```bash
# 1. Pre-flight check + automatic 60% peer allocation on the active DAG
python3 packages/orchestrator/client.py desktop-doctor --format text
ORCHESTRATOR_DESKTOP_SHARE=0.60 python3 packages/orchestrator/client.py allocate-dag \
  --target-desktop-share 0.60 \
  --apply

# 2. Override to force 100% pure API fleet (disables desktop allocation)
python3 packages/orchestrator/client.py allocate-dag --no-desktop --apply
python3 packages/orchestrator/client.py allocate-dag --api-only --apply

# 3. Single-task recommendation in hybrid_desktop mode
python3 packages/orchestrator/client.py recommend \
  --agent spec-engineer \
  --complexity high \
  --budget-mode hybrid_desktop \
  --desktop-available \
  --read-only
```
REST API equivalent: `POST /api/router/allocate-dag` (accepts `target_desktop_share: 0.60`, `apply: true`, `no_desktop: false` / `api_only: false`).

### 5.2 Dispatching Jobs (`desktop-ask` / `desktop-dispatch` & REST API)
```bash
# 1. Task mode (default) with Silent Background Execution (background=True default)
#    Mounts --workspace via Folders (ConnectedFolderNSMenu) with zero focus theft & zero clipboard touch!
python3 packages/orchestrator/client.py desktop-ask \
  --task TASK-20 \
  --mode task \
  --workspace . \
  --background \
  --prompt "Read docs/specs/my_feature.spec.md and packages/orchestrator/router.py. Return a read-only architectural critique without editing any files." \
  --driver auto \
  --timeout 180

# 2. First-time registration of a brand-new unbookmarked workspace folder on macOS
#    (--allow-foreground is opt-in only for the one-time NSOpenPanel "Add new folder…" dialog)
python3 packages/orchestrator/client.py desktop-ask \
  --task TASK-21 \
  --mode task \
  --workspace /path/to/new-project \
  --allow-foreground \
  --prompt-file docs/specs/my_feature.spec.md \
  --driver auto \
  --timeout 180

# 3. Chat mode (no workspace folder attached) queued asynchronously in background
python3 packages/orchestrator/client.py desktop-dispatch \
  --task REVIEW-01 \
  --mode chat \
  --prompt "Critique the module boundaries in docs/plans/my_feature.plan.md" \
  --async

# 4. Inspect queue & job results or cancel an active/queued job
python3 packages/orchestrator/client.py desktop-jobs --limit 20 --format text
python3 packages/orchestrator/client.py desktop-cancel --job-id dsk-20261117-a1b2
```

REST API (`POST /api/desktop/dispatch`) supports the same `mode` (default `"task"`), `workspace` (defaults to server `cwd`), `background` (default `true`), and `allow_foreground` (default `false`) parameters:
```json
{
  "task_id": "TASK-20",
  "mode": "task",
  "workspace": ".",
  "background": true,
  "allow_foreground": false,
  "prompt": "Inspect packages/orchestrator/router.py and summarize routing invariants (read-only, zero edits).",
  "driver": "auto",
  "redact_secrets": true,
  "async_mode": true
}
```

### 5.3 Consuming Context-Minimized Outputs
- Full responses are stripped of UI chrome (`"Gemini said"`, `"Show thinking"`, `"Copy"`, `"Export to Docs"`, `"Send feedback"`, `"Stop response"`) and saved to:
  `docs/reports/desktop_worker/{job_id}.md`
- Parent orchestrators and downstream agents should pass **only** the `artifact_path` pointer and the compact `summary_excerpt` (`<= 600` chars), never pasting raw multi-kilobyte transcripts into agent chat history.

---

## 6. Sentinel Protocol & Security Invariants

1. **Split-Literal Sentinel Framing (Prompt-Echo Immunity)**:
   - Prompts are wrapped by `SentinelEnvelope.build_prompt()` with `<<<AGY_DESKTOP_START:{job_id}>>>` and a **split-literal** end instruction (`"<<<AGY_DESKTOP_DONE:"` + `"{job_id}>>>"`) placed before `--- TASK CONTEXT ---`.
   - Because the unbroken end marker never appears in the user prompt bubble, `has_complete_sentinel()` cannot false-trigger on echoed prompt text.
   - Completion requires: busy indicators (`"Stop response"`, `"Generating your response…"`) absent + start/done sentinels present + `>= 500ms` length stability across 2 consecutive reads.
2. **Pre-Flight `SecretRedactor`**:
   - Automatically scrubs 8 credential classes (`google_api_key`, `openai_anthropic_key`, `github_token`, `aws_access_key`, `jwt_token`, `private_key_pem`, `dsn_credentials`, `bearer_or_secret_kv`) before touching the clipboard or SQLite `prompt_preview`.
   - Pass `--strict-secrets` to abort immediately (`status="rejected_secrets"`) whenever any credential pattern is detected.
3. **Zero-Clipboard Silent Mode & Clipboard Save/Restore Invariant**:
   - In default Silent Background Mode (`background=True`), macOS (`AXValue` + `AXPress` + `AXStaticText.AXDescription`) and Windows (`ValuePattern` + `InvokePattern` + `TextPattern`) perform **zero clipboard mutations** and **zero focus theft**.
   - Whenever clipboard-based fallback (`Cmd+V` / `Cmd+Shift+C` / `linux_pwa`) is used, drivers read prior clipboard content before injection and restore it inside `try ... finally` immediately after paste (`<= 300ms` window) and after extraction.
4. **Single-Window Mutex & Sequential Queue**:
   - GUI execution is serialized via an in-process `threading.Lock`, a cross-process advisory file lock (`fcntl.flock` / `msvcrt.locking`), and the server's FIFO `desktop_jobs` queue so parallel `gemini-4-hydrogen` builders can keep coding while desktop jobs drain safely one at a time.
5. **Prompt Size & Path Traversal Guards**:
   - Soft warning at `60,000` chars; hard rejection above `150,000` chars.
   - Custom `--output` paths dispatched via `POST /api/desktop/dispatch` must resolve strictly inside `<workspace_root>/docs/reports`.

---

## 7. Verification

Before concluding a Desktop Worker offload, confirm:

- [ ] Checked `.agents/skills/gemini-desktop-worker/sub/*.md` overrides before dispatching
- [ ] Confirmed `desktop-doctor` readiness (or clean fallback to AGY API fleet) and ensured Rule 1 security/secret prompts never route to `gemini-desktop-app`
- [ ] Passed only compact relative pointers (grounded via `projectbrain_hybrid_query` / `projectbrain_list_sources`) and consumed `docs/reports/desktop_worker/{job_id}.md` by pointer + short summary
- [ ] Synced any `allow_edits=True` changes via `projectbrain_sync_graphify(..., git_dirty_only=True, upgrade_mode="delta", auto_link_sources=True)`
