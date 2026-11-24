"""Client Kit distribution API and safe kit installation helpers."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

router = APIRouter(tags=["client-kit"])

_SKIP_DIR_OR_FILE_NAMES = frozenset({
    "__pycache__",
    ".DS_Store",
    ".git",
    ".pytest_cache",
    "graphify-out",
})
_SKIP_SUFFIXES = (".pyc", ".pyo")
_SAFE_BASE_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9._:\[\]-]+(?:/[A-Za-z0-9._~/-]*)?$"
)
_VALID_IDES = frozenset({"all", "claude", "cursor", "antigravity", "codex"})


def get_kit_root() -> Path:
    """Locate the ProjectBrain repository / client kit root containing .agents/skills."""
    env_root = os.getenv("PB_CLIENT_KIT_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / ".agents" / "skills").is_dir():
            return candidate

    here = Path(__file__).resolve()
    # Standard location: src/projectbrain/server/routes/client_kit.py -> parents[4]
    # Checking parents[3]..parents prevents issues across editable/packaged layouts.
    for parent in here.parents:
        if (parent / ".agents" / "skills").is_dir():
            return parent

    cwd = Path.cwd().resolve()
    if (cwd / ".agents" / "skills").is_dir():
        return cwd

    raise HTTPException(
        status_code=500,
        detail="Client kit root (.agents/skills) could not be located.",
    )


def _should_skip_entry(rel_parts: Tuple[str, ...], filename: str) -> bool:
    if filename in _SKIP_DIR_OR_FILE_NAMES:
        return True
    if any(part in _SKIP_DIR_OR_FILE_NAMES for part in rel_parts):
        return True
    if filename.endswith(_SKIP_SUFFIXES):
        return True
    return False


def _iter_kit_files(kit_root: Optional[Path] = None) -> Iterator[Tuple[str, Path]]:
    """Yield (arcname, abs_path) tuples for distributable ProjectBrain client kit files."""
    root = (kit_root or get_kit_root()).resolve()

    # 1. .agents/skills/** (SKILL.md, sub/README.md, references — skip __pycache__, .DS_Store, working sub/*.md)
    skills_dir = root / ".agents" / "skills"
    if skills_dir.is_dir():
        for path in sorted(skills_dir.rglob("*"), key=lambda p: p.as_posix()):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            rel_to_skills = path.relative_to(skills_dir)
            if _should_skip_entry(rel_to_skills.parts, path.name):
                continue
            # Inside <skill>/sub/, distribute only sub/README.md (never leak project-specific sub/*.md)
            if (
                len(rel_to_skills.parts) >= 3
                and rel_to_skills.parts[1] == "sub"
                and path.name != "README.md"
            ):
                continue
            yield path.relative_to(root).as_posix(), resolved

    # 2. .agents/workflows/*.md
    workflows_dir = root / ".agents" / "workflows"
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.md"), key=lambda p: p.name):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            if _should_skip_entry((), path.name):
                continue
            yield path.relative_to(root).as_posix(), resolved

    # 3. .claude/commands/*.md
    claude_dir = root / ".claude" / "commands"
    if claude_dir.is_dir():
        for path in sorted(claude_dir.glob("*.md"), key=lambda p: p.name):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            if _should_skip_entry((), path.name):
                continue
            yield path.relative_to(root).as_posix(), resolved

    # 4. .cursor/commands/*.md
    cursor_dir = root / ".cursor" / "commands"
    if cursor_dir.is_dir():
        for path in sorted(cursor_dir.glob("*.md"), key=lambda p: p.name):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            if _should_skip_entry((), path.name):
                continue
            yield path.relative_to(root).as_posix(), resolved

    # 5. AGENTS.md (if present)
    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        resolved = agents_md.resolve()
        if resolved.is_relative_to(root):
            yield "AGENTS.md", resolved


def _sanitize_base_url(raw_url: str) -> str:
    cleaned = (raw_url or "").strip().rstrip("/")
    if cleaned and _SAFE_BASE_URL_RE.match(cleaned):
        return cleaned
    return "http://localhost:8080"


def _inject_sh_default_server_url(script_text: str, base_url: str) -> str:
    if re.search(r"^\s*PB_DEFAULT_SERVER_URL\s*=", script_text, re.MULTILINE):
        return script_text
    inject_line = f'PB_DEFAULT_SERVER_URL="${{PB_DEFAULT_SERVER_URL:-{base_url}}}"\n'
    if script_text.startswith("#!"):
        nl = script_text.find("\n")
        if nl != -1:
            return script_text[: nl + 1] + inject_line + script_text[nl + 1 :]
        return script_text + "\n" + inject_line
    return inject_line + script_text


def _find_ps_param_block_end(script_text: str) -> Optional[int]:
    """Return index immediately after the top-level PowerShell param(...) block, if present."""
    # Strip block comments <# ... #> positions so we don't match inside help text
    pos = 0
    length = len(script_text)
    in_block_comment = False
    while pos < length:
        if not in_block_comment and script_text.startswith("<#", pos):
            in_block_comment = True
            pos += 2
            continue
        if in_block_comment:
            end_comment = script_text.find("#>", pos)
            if end_comment == -1:
                return None
            pos = end_comment + 2
            in_block_comment = False
            continue
        # Skip whitespace and line comments
        ch = script_text[pos]
        if ch in " \t\r\n":
            pos += 1
            continue
        if ch == "#":
            nl = script_text.find("\n", pos)
            if nl == -1:
                return None
            pos = nl + 1
            continue
        if script_text.startswith("[CmdletBinding(", pos) or script_text.startswith("[CmdletBinding()]", pos):
            bracket_end = script_text.find("]", pos)
            if bracket_end == -1:
                return None
            pos = bracket_end + 1
            continue
        m = re.match(r"param\s*\(", script_text[pos:], re.IGNORECASE)
        if not m:
            return None
        paren_start = pos + m.end() - 1
        depth = 0
        in_sq = False
        in_dq = False
        i = paren_start
        while i < length:
            c = script_text[i]
            if in_sq:
                if c == "'" and i + 1 < length and script_text[i + 1] == "'":
                    i += 2
                    continue
                if c == "'":
                    in_sq = False
            elif in_dq:
                if c == "`" and i + 1 < length:
                    i += 2
                    continue
                if c == '"':
                    in_dq = False
            else:
                if c == "'":
                    in_sq = True
                elif c == '"':
                    in_dq = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
        return None
    return None


def _inject_ps1_default_server_url(script_text: str, base_url: str) -> str:
    if re.search(
        r"^\s*\$(?:script:|env:)?PB_DEFAULT_SERVER_URL\s*=",
        script_text,
        re.MULTILINE,
    ):
        return script_text

    inject_block = (
        f"if (-not $PB_DEFAULT_SERVER_URL) {{ $PB_DEFAULT_SERVER_URL = '{base_url}' }}\n"
        "if (-not $env:PB_DEFAULT_SERVER_URL) { $env:PB_DEFAULT_SERVER_URL = $PB_DEFAULT_SERVER_URL }\n"
    )
    param_end = _find_ps_param_block_end(script_text)
    if param_end is not None:
        nl = script_text.find("\n", param_end)
        if nl != -1:
            return script_text[: nl + 1] + inject_block + script_text[nl + 1 :]
        return script_text + "\n" + inject_block
    return inject_block + script_text


def parse_ide_selection(ide: str) -> Set[str]:
    raw = (ide or "all").strip().lower()
    if not raw or raw == "all":
        return {"claude", "cursor", "antigravity", "codex"}
    selected: Set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if token not in _VALID_IDES:
            raise ValueError(
                f"Unsupported IDE '{token}'. Valid values: {', '.join(sorted(_VALID_IDES))}"
            )
        if token == "all":
            return {"claude", "cursor", "antigravity", "codex"}
        selected.add(token)
    if not selected:
        return {"claude", "cursor", "antigravity", "codex"}
    return selected


def safe_install_kit_tree(
    kit_root: Path,
    target_dir: Path,
    ide: str = "all",
) -> Dict[str, Any]:
    """Install the ProjectBrain client kit into target_dir while preserving all */sub/* user files."""
    selected_ides = parse_ide_selection(ide)
    resolved_kit = kit_root.resolve()
    resolved_target = target_dir.resolve()
    resolved_target.mkdir(parents=True, exist_ok=True)

    items = list(_iter_kit_files(resolved_kit))
    installed_files: List[str] = []
    preserved_sub_files: List[str] = []
    skills_seen: Set[str] = set()
    workflows_count = 0
    claude_commands_count = 0
    cursor_commands_count = 0
    agents_md_installed = False

    has_explicit_claude = any(arc.startswith(".claude/commands/") for arc, _ in items)
    has_explicit_cursor = any(arc.startswith(".cursor/commands/") for arc, _ in items)

    # Stage into a temp directory first so kit_root == target_dir or partial reads remain atomic/safe
    with tempfile.TemporaryDirectory(prefix="pb_client_kit_") as tmp_dir:
        staging = Path(tmp_dir)
        for arcname, abs_path in items:
            staged_path = staging / arcname
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, staged_path)

        for arcname, _ in items:
            src_file = staging / arcname
            dst_file = resolved_target / arcname

            if arcname.startswith(".agents/skills/"):
                parts = Path(arcname).parts
                if len(parts) >= 3:
                    skills_seen.add(parts[2])
                # CRITICAL sub/ preservation rule: never overwrite or delete existing */sub/* files
                # (only install sub/README.md if missing).
                if len(parts) >= 5 and parts[3] == "sub":
                    if parts[4] != "README.md" or dst_file.exists():
                        if dst_file.exists():
                            preserved_sub_files.append(arcname)
                        continue
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                installed_files.append(arcname)

            elif arcname.startswith(".agents/workflows/"):
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                installed_files.append(arcname)
                workflows_count += 1

                # Mirror workflows into IDE command dirs only if explicit IDE command files are absent
                wf_name = Path(arcname).name
                if "claude" in selected_ides and not has_explicit_claude:
                    claude_dst = resolved_target / ".claude" / "commands" / wf_name
                    claude_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, claude_dst)
                    installed_files.append(f".claude/commands/{wf_name}")
                    claude_commands_count += 1
                if "cursor" in selected_ides and not has_explicit_cursor:
                    cursor_dst = resolved_target / ".cursor" / "commands" / wf_name
                    cursor_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, cursor_dst)
                    installed_files.append(f".cursor/commands/{wf_name}")
                    cursor_commands_count += 1

            elif arcname.startswith(".claude/commands/"):
                if "claude" in selected_ides:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    installed_files.append(arcname)
                    claude_commands_count += 1

            elif arcname.startswith(".cursor/commands/"):
                if "cursor" in selected_ides:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    installed_files.append(arcname)
                    cursor_commands_count += 1

            elif arcname == "AGENTS.md":
                if not dst_file.exists():
                    shutil.copy2(src_file, dst_file)
                    installed_files.append(arcname)
                    agents_md_installed = True

    return {
        "target_dir": str(resolved_target),
        "ide": sorted(selected_ides),
        "installed_files": installed_files,
        "preserved_sub_files": preserved_sub_files,
        "skills_count": len(skills_seen),
        "workflows_count": workflows_count,
        "claude_commands_count": claude_commands_count,
        "cursor_commands_count": cursor_commands_count,
        "agents_md_installed": agents_md_installed,
    }


def merge_mcp_json(
    target_dir: Path,
    server_url: str,
    token: str,
    project_id: str,
    source_id: Optional[str] = None,
) -> Path:
    """Create or merge .mcp.json at target_dir preserving existing mcpServers entries."""
    resolved_target = target_dir.resolve()
    resolved_target.mkdir(parents=True, exist_ok=True)
    mcp_path = resolved_target / ".mcp.json"

    clean_url = (server_url or "http://localhost:8080").strip().rstrip("/")
    clean_token = (token or "").strip() or "YOUR_PB_TOKEN"
    clean_project = (project_id or "").strip() or resolved_target.name or "default"
    clean_source = (source_id or "").strip()

    sse_url = f"{clean_url}/mcp/sse?token={clean_token}&project_id={clean_project}"
    if clean_source:
        sse_url += f"&source_id={clean_source}"
    http_url = f"{clean_url}/mcp-http"

    data: Dict[str, Any] = {}
    if mcp_path.is_file():
        try:
            loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            backup = resolved_target / ".mcp.json.bak"
            shutil.copy2(mcp_path, backup)
            data = {}

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}

    sse_entry: Dict[str, Any] = {
        "type": "sse",
        "url": sse_url,
        "headers": {"Authorization": f"Bearer {clean_token}"},
    }
    http_headers: Dict[str, str] = {
        "Authorization": f"Bearer {clean_token}",
        "X-PB-Project-Id": clean_project,
    }
    if clean_source:
        http_headers["X-PB-Source-Id"] = clean_source

    http_entry: Dict[str, Any] = {
        "type": "http",
        "url": http_url,
        "headers": http_headers,
    }

    servers["projectbrain"] = sse_entry
    servers["projectbrain-http"] = http_entry
    data["mcpServers"] = servers

    mcp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return mcp_path


@router.get("/client-kit/manifest")
async def get_client_kit_manifest() -> Dict[str, Any]:
    kit_root = get_kit_root()
    items = list(_iter_kit_files(kit_root))

    skills: List[str] = []
    seen_skills: Set[str] = set()
    workflows: List[str] = []
    claude_commands: List[str] = []
    cursor_commands: List[str] = []

    for arcname, _ in items:
        parts = arcname.split("/")
        if arcname.startswith(".agents/skills/") and len(parts) >= 4:
            skill_name = parts[2]
            if skill_name not in seen_skills:
                seen_skills.add(skill_name)
                skills.append(skill_name)
        elif arcname.startswith(".agents/workflows/") and len(parts) == 3:
            workflows.append(parts[2])
        elif arcname.startswith(".claude/commands/") and len(parts) == 3:
            claude_commands.append(parts[2])
        elif arcname.startswith(".cursor/commands/") and len(parts) == 3:
            cursor_commands.append(parts[2])

    return {
        "status": "ok",
        "skills": skills,
        "workflows": workflows,
        "claude_commands": claude_commands,
        "cursor_commands": cursor_commands,
        "file_count": len(items),
    }


@router.get("/client-kit/bundle.tar.gz")
async def download_client_kit_tarball() -> StreamingResponse:
    kit_root = get_kit_root()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname, abs_path in _iter_kit_files(kit_root):
            tar.add(str(abs_path), arcname=arcname, recursive=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="projectbrain-client-kit.tar.gz"'
        },
    )


@router.get("/client-kit/bundle.zip")
async def download_client_kit_zip() -> StreamingResponse:
    kit_root = get_kit_root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, abs_path in _iter_kit_files(kit_root):
            zf.write(str(abs_path), arcname=arcname)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="projectbrain-client-kit.zip"'
        },
    )


@router.get("/client-kit/install.sh")
async def download_install_sh(request: Request) -> PlainTextResponse:
    kit_root = get_kit_root()
    script_path = kit_root / "scripts" / "install_client.sh"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="scripts/install_client.sh not found.")
    raw = script_path.read_text(encoding="utf-8")
    base_url = _sanitize_base_url(str(request.base_url).rstrip("/"))
    rendered = _inject_sh_default_server_url(raw, base_url)
    return PlainTextResponse(content=rendered, media_type="text/plain; charset=utf-8")


@router.get("/client-kit/install.ps1")
async def download_install_ps1(request: Request) -> PlainTextResponse:
    kit_root = get_kit_root()
    script_path = kit_root / "scripts" / "install_client.ps1"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="scripts/install_client.ps1 not found.")
    raw = script_path.read_text(encoding="utf-8")
    base_url = _sanitize_base_url(str(request.base_url).rstrip("/"))
    rendered = _inject_ps1_default_server_url(raw, base_url)
    return PlainTextResponse(content=rendered, media_type="text/plain; charset=utf-8")
