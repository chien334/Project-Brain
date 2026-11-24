---
name: legacy-cpp-windows-reverse
description: Guides reverse-engineering, cross-source IPC tracing, and safe modification of legacy C++/Win32 Session 0 services, Desktop HMI clients, DLLs, COM/IDL, VB6/Delphi/COBOL, and IEC 61131-3 ST / PLC LASAL codebases (including Japanese CP932/Shift-JIS, EUC-JP, and UTF-16 LE/BE sources).
---

# Legacy C++, Windows Session 0 IPC, PLC ST/LASAL & Japanese Reverse Engineering

## Overview

Legacy industrial, Windows desktop, and Japanese enterprise systems rarely communicate over HTTP/OpenAPI alone. They typically pair a **Windows Session 0 Service** (`win_service`), a **Session 1+ Desktop HMI / Client** (`desktop_client`), **native DLL / COM modules** (`native_lib`), and optionally **PLC / IEC 61131-3 Structured Text / Sigmatek LASAL controllers** (`plc_st`), often written with **CP932 / Shift-JIS**, **EUC-JP**, or **UTF-16 LE/BE** comments and resources.

This skill establishes safe discovery, encoding hygiene, Session 0 IPC boundary tracing, and binary contract drift verification using ProjectBrain.

## When to Use

- Onboarding or reverse-engineering legacy C/C++, MFC/Win32, COM/IDL, `.rc`/`.def`, VB6 (`.vbp`), Delphi (`.dpr`/`.dproj`), Qt (`.pro`), or COBOL repositories.
- Tracing IPC boundaries between Windows Session 0 services, Desktop HMIs, DLLs, and PLC/LASAL controllers (`*.lcp`, `*.lxp`, `*.st`, `*.scl`, `*.pou`).
- Working in Japanese legacy codebases encoded in Shift-JIS/CP932, EUC-JP, ISO-2022-JP, or UTF-16 LE/BE.
- Modifying shared binary structs (`typedef struct`, `#pragma pack`), pipe payloads, shared memory layouts, COM interfaces, Win32 messages, DLL exports, or PLC `VAR_GLOBAL` / LASAL `SERVER`/`CLIENT` channels.

## Project-Specific Overrides (`sub/*.md`)

**Mandatory rule:** Before executing this skill, check `.agents/skills/legacy-cpp-windows-reverse/sub/*.md`. Read all `.md` files in `sub/` (excluding `README.md`) alphabetically. Project rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (while keeping ProjectBrain MCP verification gates intact). Never edit `SKILL.md` for project-only rules — write them in `.agents/skills/legacy-cpp-windows-reverse/sub/<topic>.md` (or use `create-project-skill`) so project overrides survive client-kit upgrades.

---

## 1. Encoding Safety (CP932 / Shift-JIS, EUC-JP, UTF-16 LE/BE)

Garbled Japanese comments (`\ufffd`) destroy domain terminology, error-code tables, and HMI screen/control semantics.

1. **ProjectBrain Automatic Decoding**: `legacy_encoding.py` (`decode_legacy_bytes` / `read_text_with_encoding`) automatically decodes legacy sources and resources in priority order without replacement corruption:
   - BOM / escape-coded streams (`utf-8-sig`, `iso2022_jp`, UTF-16 LE/BE BOM, and BOM-less UTF-16 LE/BE detected via NUL-byte asymmetry — common in `.rc`, `.def`, `.reg`, and Visual Studio headers).
   - Strict `utf-8`.
   - Japanese legacy encodings (`cp932`, `shift_jis`, `euc_jp`, `iso2022_jp`) disambiguated by Japanese character naturalness scoring.
   - `cp1252` / `latin-1` fallback.
2. **Reading Files Safely in Shell / Python**:
   - Never force `utf-8` with `errors="replace"` or `errors="ignore"` on unknown legacy files.
   - When inspecting files locally with scripts, reuse ProjectBrain's decoder (`from projectbrain.graphify_engine.legacy_encoding import read_text_with_encoding`) or verify encoding (`file -I <path>` / `nkf -g <path>`) before reading.
3. **Preserving On-Disk Encoding on Edits**:
   - **Do NOT bulk-convert** `.rc`, `.cpp`, `.h`, `.vbp`, `.pas`, or `.st` files to UTF-8 unless explicitly requested. Older MSVC resource compilers (`rc.exe`), VB6, Delphi, and PLC IDEs expect CP932 or UTF-16 LE.
   - When editing an existing legacy file, detect its original encoding and line endings (usually `CRLF`) and write back in that same encoding.
4. **Glossary & Japanese Spec Alignment**:
   - Preserve original Japanese identifiers/comments alongside English reverse-engineering notes (e.g., mapping `画面ID` / `エラーコード` / `#define ERR_*` / `IDD_*` / `IDC_*` to C++ structs and PLC tags) and store project-wide glossaries in `sub/jp-glossary.md` or `__shared__` docs.

---

## 2. Multi-Source Workspace Discovery (`.sln`, `.vcxproj`, `.lcp`, `*.st`, Legacy Projects)

Before reading individual files blindly, initialize or inspect the ProjectBrain Multi-Source Hub:

1. **Preview Discovery (`projectbrain_discover_workspace_sources`)**:
   ```text
   projectbrain_discover_workspace_sources(workspace_root=".")
   ```
   ProjectBrain automatically discovers and classifies legacy sub-sources:
   - **Visual Studio Solutions & Projects** (`.sln`, `.vcxproj`, `.vcproj`, `.csproj`, `.vbproj`): Expands `.sln` child projects into isolated sub-sources (`win_service`, `desktop_client`, `native_lib`, `backend`) and extracts `.sln` `ProjectDependencies` + `<ProjectReference>` into `depends_on` edges.
   - **PLC / Structured Text / Sigmatek LASAL** (`.lcp`, `.lxp`, `*.st`, `*.scl`, `*.pou`): Classifies as `source_type="plc_st"` (`tech_stack=["iec61131-st"]` or `["iec61131-st", "lasal"]`), extracting `PROGRAM`, `FUNCTION_BLOCK`, `FUNCTION`, `CLASS`, `METHOD`, `TYPE ... STRUCT`, `VAR_*`, and LASAL `SERVER`/`CLIENT` nodes.
   - **Other Legacy Stacks**: `CMakeLists.txt`, `Makefile`/`GNUmakefile`, Qt (`*.pro`), VB6 (`*.vbp`), Delphi (`*.dpr`/`*.dproj`), plus `.idl`, `.def`, `.rc`, `.cbl`/`.cob`, `.bat`/`.cmd`, `.reg`.
2. **Bootstrap the Hub (`projectbrain_bootstrap_project_hub`)**:
   ```text
   projectbrain_bootstrap_project_hub(
     project_id="<project_id>",
     workspace_root=".",
     auto_discover=True,
     sync_graphs=True,
     auto_link=True,
     upgrade_mode="delta"
   )
   ```
3. **Verify Topology**:
   - Call `projectbrain_list_sources(project_id="<project_id>")` to confirm canonical `source_id`s (`<project_id>@<source_id>:<branch>`).

---

## 3. Windows Session 0 IPC & PLC Boundary Tracing

Windows Session 0 isolation separates non-interactive services from Session 1+ Desktop HMIs and PLC runtimes. Always trace both sides of an IPC boundary before changing a protocol, struct, or tag.

### Supported Cross-Source IPC & PLC Relation Types

| Contract Kind | Relation Type | Typical Providers ↔ Consumers | Drift-Sensitive Payload / Surface |
| :--- | :--- | :--- | :--- |
| `named_pipe` | `calls_pipe` | `CreateNamedPipe` ↔ `CreateFile` / `CallNamedPipe` (`\\.\pipe\<Name>`) | Pipe name, message struct / command opcodes |
| `shared_memory` | `shares_shm` | `CreateFileMapping` ↔ `OpenFileMapping` / `MapViewOfFile` (`Global\...`, `Local\...`) | Mapped `struct` layout, `#pragma pack`, field offsets |
| `scm_service` | `controls_service` | `ServiceMain` / `RegisterServiceCtrlHandler` ↔ `OpenService` / `ControlService` / `sc.exe` | Service name, custom control codes (`128..255`) |
| `com_idl` | `calls_com` | `.idl` `interface` / `UUID` / `CLSID` ↔ `CoCreateInstance` / `QueryInterface` | Method signatures, `[uuid(...)]`, vtable order |
| `win32_msg` | `sends_win32_msg` | `ON_MESSAGE` / `WndProc` ↔ `SendMessage` / `PostMessage` / `WM_COPYDATA` / `RegisterWindowMessage` | Message ID, `COPYDATASTRUCT` / `WPARAM` / `LPARAM` |
| `dll_export` | `loads_dll` | `__declspec(dllexport)` / `.def` ↔ `LoadLibrary` / `GetProcAddress` / `.lib` link | Exported symbol name, calling convention, signature |
| `socket_ipc` | `calls_socket` | Winsock `bind` / `listen` ↔ `connect` (fixed local port/host) | Port, binary packet header & byte order |
| `plc_lasal_channel` | `links_plc_channel` / `shares_plc_tag` | LASAL `SERVER` / `VAR_GLOBAL` / I/O address ↔ LASAL `CLIENT` / HMI / Session 0 bridge | Channel/tag name, data type, scaling/register map |

### Required Tracing & Impact Gate Workflow

1. **Retrieve Scoped Context + Cross-Source Neighbors**:
   - Use `projectbrain_hybrid_query(query="<pipe, shm, struct, COM interface, or PLC tag>", project_id="<project_id>", branch="<branch>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` to inspect AST callers/callees, linked IPC peers, and `__shared__` specs together.
   - Use `projectbrain_query(query="<symbol or error code>", user_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` for decoded Japanese comments, error tables, and prior reverse-engineering notes.
2. **Pre-Edit Blast-Radius & Drift Check (MANDATORY before editing any IPC/struct/tag)**:
   - Call:
     ```text
     projectbrain_cross_source_impact(
       project_id="<project_id>",
       target="<pipe_or_shm_or_struct_or_tag>",
       source_id="<source_id>",
       branch="<branch>",
       relation_depth=2
     )
     ```
   - Inspect `affected_sources`, `direct_relations`, `propagated_relations`, and **`drifted_relations`**.
   - **ABI / Packing Guardrail:** Any change to `#pragma pack`, `struct` field order/width, `WM_COPYDATA` payload, COM `.idl` signature, `.def` ordinal/export, or PLC `VAR_GLOBAL` type breaks binary compatibility across sub-sources unless every linked consumer/provider is updated in lockstep.
3. **Post-Edit Delta Sync & Drift Reconciliation**:
   - Sync **only** the modified sub-source(s):
     ```text
     projectbrain_sync_graphify(
       workspace_path="<sub_source_path>",
       project_id="<project_id>",
       source_id="<source_id>",
       branch="<branch>",
       git_dirty_only=True,
       upgrade_mode="delta",
       auto_link_sources=True
     )
     ```
   - Re-check `drifted_relations` in the sync output or via `projectbrain_cross_source_impact`. Never leave unreported `drifted` or `stale` IPC/PLC edges.
   - When documenting newly reverse-engineered protocols or Session 0 maps, upload to `__shared__` (`projectbrain_upload_document(..., scope="project_shared")`) or record project-specific conventions under `.agents/skills/legacy-cpp-windows-reverse/sub/`.

---

## Verification Checklist

- [ ] Loaded `.agents/skills/legacy-cpp-windows-reverse/sub/*.md` (excluding `README.md`) before starting analysis or edits.
- [ ] Verified workspace sub-sources via `projectbrain_list_sources` / `projectbrain_bootstrap_project_hub` (`.sln`, `.vcxproj`, `.lcp`, `*.st`, `.vbp`, `.dpr`).
- [ ] Confirmed Japanese comments/specs decoded cleanly (no `\ufffd` mojibake) and preserved original file encodings on disk when editing.
- [ ] Ran `projectbrain_cross_source_impact` before modifying any Session 0 IPC contract, binary struct, DLL export, COM interface, or PLC tag.
- [ ] Synced modified sub-sources with `projectbrain_sync_graphify(upgrade_mode="delta", auto_link_sources=True)` and verified zero unresolved `drifted_relations`.
