# Project-Specific Overrides for `legacy-cpp-windows-reverse`

## Purpose

The `sub/` directory stores **repository-specific overrides and domain knowledge** for `legacy-cpp-windows-reverse` without modifying `SKILL.md`.

Typical uses in `legacy-cpp-windows-reverse/sub/`:
- `sub/session0-ipc.md`: Exact `project_id` / `source_id` mappings (`win_service`, `desktop_client`, `native_lib`), Named Pipe names, `Global\` Shared Memory struct layouts, custom SCM control codes, COM CLSIDs, and `WM_COPYDATA` opcodes.
- `sub/lasal-tags.md`: PLC / IEC 61131-3 ST / Sigmatek LASAL controller topology, `SERVER`/`CLIENT` channel conventions, `VAR_GLOBAL` register maps, and HMI tag bindings.
- `sub/jp-glossary.md`: Project-specific Japanese business terms (`業務用語`), HMI screen IDs (`IDD_*`), error code prefixes (`ERR_*`), and file-encoding rules (e.g., which directories must stay `CP932` vs `UTF-16 LE`).

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for encoding safety, `.sln`/`.lcp`/`*.st` discovery, Session 0 IPC tracing, and ProjectBrain MCP gates.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as build/test commands, encoding exceptions, or IPC conventions), while keeping all ProjectBrain MCP verification steps intact (`projectbrain_cross_source_impact`, `projectbrain_hybrid_query`, `projectbrain_sync_graphify`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/legacy-cpp-windows-reverse/sub/session0-ipc.md`:

```markdown
# Project Override: Session 0 Service & HMI IPC Contract Map

> Applies to: `.agents/skills/legacy-cpp-windows-reverse/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `source_id=*`

## Overrides (Replaces Base Defaults)
- **Encoding Policy:** Keep all `.rc` files in `UTF-16 LE` (with BOM) and all `.cpp`/`.h`/`.st` files in `CP932` (`CRLF`) so legacy MSVC / PLC compilers build without warnings.

## Extensions (Adds Project Context)
- **Sub-Sources (`project_id="<project_id>"`):**
  - `core-service` (`win_service`): Session 0 Windows Service (`MyAppSvc`)
  - `hmi-client` (`desktop_client`): MFC Desktop HMI
  - `plc-ctrl` (`plc_st`): Sigmatek LASAL / IEC 61131-3 ST controller
- **Critical IPC Boundaries:**
  - Named Pipe `\\.\pipe\MyAppCmd` (`calls_pipe`) — command header `CMD_HEADER` (`#pragma pack(push, 1)`)
  - Shared Memory `Global\MyAppTelemetry` (`shares_shm`) — `TELEMETRY_FRAME`
  - LASAL Channel `MotorAxis01.Pos` (`links_plc_channel`) — `plc-ctrl` ↔ `core-service`

## Additional Verification Checks
- [ ] Run `projectbrain_cross_source_impact(target="TELEMETRY_FRAME")` before editing any shared header in `include/`.
```
