#!/usr/bin/env bash
# ==============================================================================
# ProjectBrain Client Kit Installer (macOS / Linux)
# Supports Remote mode (--server-url) and Local mode (--local-kit or repo clone)
# Preserves all user/project .agents/skills/*/sub/* files untouched.
# ==============================================================================

set -euo pipefail

TARGET_DIR="."
LOCAL_KIT=""
SERVER_URL="${PB_SERVER_URL:-${PB_URL:-}}"
SERVER_URL_EXPLICIT=false
TOKEN="${PB_AUTH_TOKEN:-${PB_TOKEN:-${PB_API_KEY:-}}}"
PROJECT_ID="${PB_PROJECT_ID:-${PB_DEFAULT_USER_ID:-}}"
SOURCE_ID="${PB_SOURCE_ID:-}"
IDE="all"
DO_BOOTSTRAP=false
NON_INTERACTIVE=false

if [ -t 1 ]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
else
    C_RESET=""
    C_BOLD=""
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
fi

info()    { printf "%s[INFO]%s %s\n" "${C_CYAN}" "${C_RESET}" "$*"; }
success() { printf "%s[OK]%s   %s\n" "${C_GREEN}" "${C_RESET}" "$*"; }
warn()    { printf "%s[WARN]%s %s\n" "${C_YELLOW}" "${C_RESET}" "$*"; }
error()   { printf "%s[ERR]%s  %s\n" "${C_RED}" "${C_RESET}" "$*" >&2; }

usage() {
    cat <<EOF
ProjectBrain Client Kit Installer (macOS / Linux)

Usage:
  ./scripts/install_client.sh [OPTIONS]
  curl -fsSL https://<server>/client-kit/install.sh | bash -s -- [OPTIONS]

Options:
  --target-dir <dir>     Target workspace directory (default: .)
  --local-kit <dir>      Install from local ProjectBrain clone instead of remote server
  --server-url <url>     ProjectBrain server URL (remote kit download + .mcp.json)
  --token <tok>          ProjectBrain Bearer / PAT token for .mcp.json and --bootstrap
  --project-id <id>      Project ID for .mcp.json and --bootstrap (default: target basename)
  --source-id <id>       Optional sub-source ID for multi-source workspaces
  --ide <name>           Target IDE(s): all (default), claude, cursor, antigravity, codex
  --bootstrap            Call POST \$SERVER_URL/master/hub/bootstrap after installing
  --non-interactive, -y  Do not prompt for missing values
  -h, --help             Show this help message and exit
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target-dir)
            [ $# -ge 2 ] || { error "Missing value for --target-dir"; exit 2; }
            TARGET_DIR="$2"
            shift 2
            ;;
        --target-dir=*)
            TARGET_DIR="${1#*=}"
            shift
            ;;
        --local-kit)
            [ $# -ge 2 ] || { error "Missing value for --local-kit"; exit 2; }
            LOCAL_KIT="$2"
            shift 2
            ;;
        --local-kit=*)
            LOCAL_KIT="${1#*=}"
            shift
            ;;
        --server-url|--url)
            [ $# -ge 2 ] || { error "Missing value for $1"; exit 2; }
            SERVER_URL="$2"
            SERVER_URL_EXPLICIT=true
            shift 2
            ;;
        --server-url=*|--url=*)
            SERVER_URL="${1#*=}"
            SERVER_URL_EXPLICIT=true
            shift
            ;;
        --token)
            [ $# -ge 2 ] || { error "Missing value for --token"; exit 2; }
            TOKEN="$2"
            shift 2
            ;;
        --token=*)
            TOKEN="${1#*=}"
            shift
            ;;
        --project-id)
            [ $# -ge 2 ] || { error "Missing value for --project-id"; exit 2; }
            PROJECT_ID="$2"
            shift 2
            ;;
        --project-id=*)
            PROJECT_ID="${1#*=}"
            shift
            ;;
        --source-id)
            [ $# -ge 2 ] || { error "Missing value for --source-id"; exit 2; }
            SOURCE_ID="$2"
            shift 2
            ;;
        --source-id=*)
            SOURCE_ID="${1#*=}"
            shift
            ;;
        --ide)
            [ $# -ge 2 ] || { error "Missing value for --ide"; exit 2; }
            IDE="$2"
            shift 2
            ;;
        --ide=*)
            IDE="${1#*=}"
            shift
            ;;
        --bootstrap)
            DO_BOOTSTRAP=true
            shift
            ;;
        --non-interactive|-y)
            NON_INTERACTIVE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 2
            ;;
    esac
done

IDE_LOWER="$(printf '%s' "${IDE}" | tr '[:upper:]' '[:lower:]')"
INSTALL_CLAUDE=false
INSTALL_CURSOR=false
INSTALL_ANTIGRAVITY=false
INSTALL_CODEX=false

IFS=',' read -ra IDE_PARTS <<< "${IDE_LOWER}"
for part in "${IDE_PARTS[@]}"; do
    trimmed="$(printf '%s' "${part}" | tr -d '[:space:]')"
    [ -n "${trimmed}" ] || continue
    case "${trimmed}" in
        all)
            INSTALL_CLAUDE=true
            INSTALL_CURSOR=true
            INSTALL_ANTIGRAVITY=true
            INSTALL_CODEX=true
            ;;
        claude)      INSTALL_CLAUDE=true ;;
        cursor)      INSTALL_CURSOR=true ;;
        antigravity) INSTALL_ANTIGRAVITY=true ;;
        codex)       INSTALL_CODEX=true ;;
        *)
            error "Unsupported --ide value '${trimmed}'. Valid values: all, claude, cursor, antigravity, codex."
            exit 2
            ;;
    esac
done

mkdir -p "${TARGET_DIR}"
ABS_TARGET_DIR="$(cd "${TARGET_DIR}" && pwd)"

# Detect if executed directly from a ProjectBrain clone
AUTO_LOCAL_KIT=""
SCRIPT_SELF="${BASH_SOURCE[0]:-}"
if [ -n "${SCRIPT_SELF}" ] && [ -f "${SCRIPT_SELF}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_SELF}")" && pwd)"
    CANDIDATE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
    if [ -d "${CANDIDATE_ROOT}/.agents/skills" ] && [ -f "${CANDIDATE_ROOT}/scripts/install_client.sh" ]; then
        AUTO_LOCAL_KIT="${CANDIDATE_ROOT}"
    fi
fi

EFFECTIVE_DEFAULT_URL="${PB_DEFAULT_SERVER_URL:-}"
MODE=""
KIT_SOURCE=""

if [ -n "${LOCAL_KIT}" ]; then
    if [ ! -d "${LOCAL_KIT}/.agents/skills" ]; then
        error "Local kit directory '${LOCAL_KIT}' does not contain '.agents/skills'."
        exit 1
    fi
    MODE="local"
    KIT_SOURCE="$(cd "${LOCAL_KIT}" && pwd)"
elif [ "${SERVER_URL_EXPLICIT}" = true ] || [ -n "${SERVER_URL}" ] || [ -n "${EFFECTIVE_DEFAULT_URL}" ]; then
    MODE="remote"
    SERVER_URL="${SERVER_URL:-${EFFECTIVE_DEFAULT_URL}}"
elif [ -n "${AUTO_LOCAL_KIT}" ]; then
    MODE="local"
    KIT_SOURCE="${AUTO_LOCAL_KIT}"
elif [ "${NON_INTERACTIVE}" = false ] && [ -t 0 ]; then
    printf "Enter ProjectBrain Server URL [http://localhost:8080]: "
    read -r input_url
    SERVER_URL="${input_url:-http://localhost:8080}"
    MODE="remote"
else
    error "No --local-kit or --server-url specified and no local ProjectBrain kit found."
    exit 1
fi

SERVER_URL="${SERVER_URL:-${EFFECTIVE_DEFAULT_URL:-http://localhost:8080}}"
SERVER_URL="${SERVER_URL%/}"

DEFAULT_PROJECT_ID="$(basename "${ABS_TARGET_DIR}")"
[ -n "${DEFAULT_PROJECT_ID}" ] || DEFAULT_PROJECT_ID="default"

if [ -z "${PROJECT_ID}" ]; then
    if [ "${NON_INTERACTIVE}" = false ] && [ -t 0 ]; then
        printf "Enter Project ID [%s]: " "${DEFAULT_PROJECT_ID}"
        read -r input_proj
        PROJECT_ID="${input_proj:-${DEFAULT_PROJECT_ID}}"
    else
        PROJECT_ID="${DEFAULT_PROJECT_ID}"
    fi
fi

if [ -z "${TOKEN}" ] && [ "${NON_INTERACTIVE}" = false ] && [ -t 0 ]; then
    printf "Enter ProjectBrain Token (optional, press Enter to skip): "
    read -r input_tok
    TOKEN="${input_tok}"
fi

STAGING_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t 'pbkit')"
trap 'rm -rf "${STAGING_DIR}"' EXIT

info "Mode        : ${MODE}"
info "Target dir  : ${ABS_TARGET_DIR}"
info "Server URL  : ${SERVER_URL}"
info "Project ID  : ${PROJECT_ID}"
info "IDE profile : ${IDE_LOWER}"

should_skip_name() {
    local p="$1"
    case "${p}" in
        *__pycache__*|*.DS_Store|*.pyc|*.pyo|*/.git/*|*/.pytest_cache/*|*/graphify-out/*)
            return 0
            ;;
    esac
    return 1
}

if [ "${MODE}" = "remote" ]; then
    BUNDLE_URL="${SERVER_URL}/client-kit/bundle.tar.gz"
    info "Downloading client kit from ${BUNDLE_URL}..."
    if command -v curl >/dev/null 2>&1; then
        if curl -fsSL "${BUNDLE_URL}" -o "${STAGING_DIR}/bundle.tar.gz"; then
            tar -xzf "${STAGING_DIR}/bundle.tar.gz" -C "${STAGING_DIR}"
        elif command -v unzip >/dev/null 2>&1; then
            curl -fsSL "${SERVER_URL}/client-kit/bundle.zip" -o "${STAGING_DIR}/bundle.zip"
            unzip -q -o "${STAGING_DIR}/bundle.zip" -d "${STAGING_DIR}"
        else
            error "Failed to download ${BUNDLE_URL}"
            exit 1
        fi
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${STAGING_DIR}/bundle.tar.gz" "${BUNDLE_URL}"
        tar -xzf "${STAGING_DIR}/bundle.tar.gz" -C "${STAGING_DIR}"
    else
        error "curl or wget is required in remote mode."
        exit 1
    fi
else
    info "Staging local kit from ${KIT_SOURCE}..."
    if [ -d "${KIT_SOURCE}/.agents/skills" ]; then
        while IFS= read -r -d '' src_file; do
            rel="${src_file#${KIT_SOURCE}/.agents/skills/}"
            if should_skip_name "${rel}"; then
                continue
            fi
            skill_rest="${rel#*/}"
            case "${skill_rest}" in
                sub/*)
                    if [ "${skill_rest}" != "sub/README.md" ]; then
                        continue
                    fi
                    ;;
            esac
            dst="${STAGING_DIR}/.agents/skills/${rel}"
            mkdir -p "$(dirname "${dst}")"
            cp "${src_file}" "${dst}"
        done < <(find "${KIT_SOURCE}/.agents/skills" -type f -print0)
    fi

    for sub_dir in ".agents/workflows" ".claude/commands" ".cursor/commands"; do
        if [ -d "${KIT_SOURCE}/${sub_dir}" ]; then
            mkdir -p "${STAGING_DIR}/${sub_dir}"
            while IFS= read -r -d '' md_file; do
                cp "${md_file}" "${STAGING_DIR}/${sub_dir}/$(basename "${md_file}")"
            done < <(find "${KIT_SOURCE}/${sub_dir}" -maxdepth 1 -type f -name '*.md' -print0)
        fi
    done

    if [ -f "${KIT_SOURCE}/AGENTS.md" ]; then
        cp "${KIT_SOURCE}/AGENTS.md" "${STAGING_DIR}/AGENTS.md"
    fi
fi

# 1. Install .agents/skills/* while STRICTLY preserving all existing */sub/* files (except sub/README.md if missing)
SKILLS_INSTALLED=0
SUB_PRESERVED=0
if [ -d "${STAGING_DIR}/.agents/skills" ]; then
    while IFS= read -r -d '' src_file; do
        rel="${src_file#${STAGING_DIR}/.agents/skills/}"
        if should_skip_name "${rel}"; then
            continue
        fi
        skill_rest="${rel#*/}"
        dst_file="${ABS_TARGET_DIR}/.agents/skills/${rel}"
        case "${skill_rest}" in
            sub/*)
                if [ "${skill_rest}" != "sub/README.md" ] || [ -e "${dst_file}" ]; then
                    if [ -e "${dst_file}" ]; then
                        SUB_PRESERVED=$((SUB_PRESERVED + 1))
                    fi
                    continue
                fi
                ;;
        esac
        mkdir -p "$(dirname "${dst_file}")"
        cp -f "${src_file}" "${dst_file}"
        SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
    done < <(find "${STAGING_DIR}/.agents/skills" -type f -print0)
fi

# 2. Install .agents/workflows/*.md
WORKFLOWS_INSTALLED=0
if [ -d "${STAGING_DIR}/.agents/workflows" ]; then
    mkdir -p "${ABS_TARGET_DIR}/.agents/workflows"
    while IFS= read -r -d '' wf_file; do
        cp -f "${wf_file}" "${ABS_TARGET_DIR}/.agents/workflows/$(basename "${wf_file}")"
        WORKFLOWS_INSTALLED=$((WORKFLOWS_INSTALLED + 1))
    done < <(find "${STAGING_DIR}/.agents/workflows" -maxdepth 1 -type f -name '*.md' -print0)
fi

# 3. Install .claude/commands/*.md (fallback to mirroring workflows if kit lacks explicit .claude/commands)
CLAUDE_INSTALLED=0
if [ "${INSTALL_CLAUDE}" = true ]; then
    CLAUDE_SRC_DIR=""
    if [ -d "${STAGING_DIR}/.claude/commands" ] && [ -n "$(find "${STAGING_DIR}/.claude/commands" -maxdepth 1 -type f -name '*.md' -print -quit 2>/dev/null)" ]; then
        CLAUDE_SRC_DIR="${STAGING_DIR}/.claude/commands"
    elif [ -d "${STAGING_DIR}/.agents/workflows" ]; then
        CLAUDE_SRC_DIR="${STAGING_DIR}/.agents/workflows"
    fi
    if [ -n "${CLAUDE_SRC_DIR}" ]; then
        mkdir -p "${ABS_TARGET_DIR}/.claude/commands"
        while IFS= read -r -d '' cmd_file; do
            cp -f "${cmd_file}" "${ABS_TARGET_DIR}/.claude/commands/$(basename "${cmd_file}")"
            CLAUDE_INSTALLED=$((CLAUDE_INSTALLED + 1))
        done < <(find "${CLAUDE_SRC_DIR}" -maxdepth 1 -type f -name '*.md' -print0)
    fi
fi

# 4. Install .cursor/commands/*.md (fallback to mirroring workflows if kit lacks explicit .cursor/commands)
CURSOR_INSTALLED=0
if [ "${INSTALL_CURSOR}" = true ]; then
    CURSOR_SRC_DIR=""
    if [ -d "${STAGING_DIR}/.cursor/commands" ] && [ -n "$(find "${STAGING_DIR}/.cursor/commands" -maxdepth 1 -type f -name '*.md' -print -quit 2>/dev/null)" ]; then
        CURSOR_SRC_DIR="${STAGING_DIR}/.cursor/commands"
    elif [ -d "${STAGING_DIR}/.agents/workflows" ]; then
        CURSOR_SRC_DIR="${STAGING_DIR}/.agents/workflows"
    fi
    if [ -n "${CURSOR_SRC_DIR}" ]; then
        mkdir -p "${ABS_TARGET_DIR}/.cursor/commands"
        while IFS= read -r -d '' cmd_file; do
            cp -f "${cmd_file}" "${ABS_TARGET_DIR}/.cursor/commands/$(basename "${cmd_file}")"
            CURSOR_INSTALLED=$((CURSOR_INSTALLED + 1))
        done < <(find "${CURSOR_SRC_DIR}" -maxdepth 1 -type f -name '*.md' -print0)
    fi
fi

# 5. Install AGENTS.md if present in kit and absent in target
if [ -f "${STAGING_DIR}/AGENTS.md" ] && [ ! -e "${ABS_TARGET_DIR}/AGENTS.md" ]; then
    cp "${STAGING_DIR}/AGENTS.md" "${ABS_TARGET_DIR}/AGENTS.md"
    success "Installed AGENTS.md"
fi

# 6. Generate or merge .mcp.json without clobbering existing mcpServers
MCP_FILE="${ABS_TARGET_DIR}/.mcp.json"
MCP_TOKEN="${TOKEN:-YOUR_PB_TOKEN}"
SSE_URL="${SERVER_URL}/mcp/sse?token=${MCP_TOKEN}&project_id=${PROJECT_ID}"
if [ -n "${SOURCE_ID}" ]; then
    SSE_URL="${SSE_URL}&source_id=${SOURCE_ID}"
fi
HTTP_URL="${SERVER_URL}/mcp-http"

if command -v python3 >/dev/null 2>&1; then
    PB_MCP_FILE="${MCP_FILE}" \
    PB_SSE_URL="${SSE_URL}" \
    PB_HTTP_URL="${HTTP_URL}" \
    PB_EFF_TOKEN="${MCP_TOKEN}" \
    PB_EFF_PROJECT="${PROJECT_ID}" \
    PB_EFF_SOURCE="${SOURCE_ID}" \
    python3 - <<'PY'
import json
import os
import shutil
from pathlib import Path

mcp_path = Path(os.environ["PB_MCP_FILE"])
sse_url = os.environ["PB_SSE_URL"]
http_url = os.environ["PB_HTTP_URL"]
token = os.environ["PB_EFF_TOKEN"]
project_id = os.environ["PB_EFF_PROJECT"]
source_id = os.environ.get("PB_EFF_SOURCE", "").strip()

data = {}
if mcp_path.is_file():
    try:
        loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        shutil.copy2(mcp_path, mcp_path.with_name(".mcp.json.bak"))
        data = {}

servers = data.get("mcpServers")
if not isinstance(servers, dict):
    servers = {}

http_headers = {
    "Authorization": f"Bearer {token}",
    "X-PB-Project-Id": project_id,
}
if source_id:
    http_headers["X-PB-Source-Id"] = source_id

servers["projectbrain"] = {
    "type": "sse",
    "url": sse_url,
    "headers": {"Authorization": f"Bearer {token}"},
}
servers["projectbrain-http"] = {
    "type": "http",
    "url": http_url,
    "headers": http_headers,
}
data["mcpServers"] = servers
mcp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
    success "Updated ${MCP_FILE} (merged mcpServers safely)"
elif command -v jq >/dev/null 2>&1; then
    TMP_MCP="${STAGING_DIR}/mcp.json"
    if [ -f "${MCP_FILE}" ] && jq empty "${MCP_FILE}" >/dev/null 2>&1; then
        cp "${MCP_FILE}" "${TMP_MCP}"
    else
        printf '{}\n' > "${TMP_MCP}"
    fi
    jq \
        --arg sse_url "${SSE_URL}" \
        --arg http_url "${HTTP_URL}" \
        --arg bearer "Bearer ${MCP_TOKEN}" \
        --arg project_id "${PROJECT_ID}" \
        '.mcpServers = ((.mcpServers // {}) + {
            "projectbrain": {"type": "sse", "url": $sse_url, "headers": {"Authorization": $bearer}},
            "projectbrain-http": {"type": "http", "url": $http_url, "headers": {"Authorization": $bearer, "X-PB-Project-Id": $project_id}}
        })' "${TMP_MCP}" > "${MCP_FILE}"
    success "Updated ${MCP_FILE} via jq"
else
    if [ -f "${MCP_FILE}" ]; then
        warn "Neither python3 nor jq found; leaving existing ${MCP_FILE} untouched."
    else
        cat > "${MCP_FILE}" <<EOF
{
  "mcpServers": {
    "projectbrain": {
      "type": "sse",
      "url": "${SSE_URL}",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}"
      }
    },
    "projectbrain-http": {
      "type": "http",
      "url": "${HTTP_URL}",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}",
        "X-PB-Project-Id": "${PROJECT_ID}"
      }
    }
  }
}
EOF
        success "Created ${MCP_FILE}"
    fi
fi

success "Installed ${SKILLS_INSTALLED} skill file(s), ${WORKFLOWS_INSTALLED} workflow(s), ${CLAUDE_INSTALLED} Claude command(s), ${CURSOR_INSTALLED} Cursor command(s) (sub/ files preserved: ${SUB_PRESERVED})."

# 7. Optional Project Hub bootstrap
if [ "${DO_BOOTSTRAP}" = true ]; then
    if ! command -v curl >/dev/null 2>&1; then
        error "curl is required for --bootstrap."
        exit 1
    fi
    info "Bootstrapping Project Hub via ${SERVER_URL}/master/hub/bootstrap..."
    if command -v python3 >/dev/null 2>&1; then
        BOOT_PAYLOAD="$(PB_BOOT_PROJ="${PROJECT_ID}" PB_BOOT_WS="${ABS_TARGET_DIR}" python3 -c 'import json, os; print(json.dumps({"project_id": os.environ["PB_BOOT_PROJ"], "workspace_root": os.environ["PB_BOOT_WS"], "auto_discover": True, "sync_graphs": True, "auto_link": True}))')"
    elif command -v jq >/dev/null 2>&1; then
        BOOT_PAYLOAD="$(jq -nc --arg pid "${PROJECT_ID}" --arg ws "${ABS_TARGET_DIR}" '{project_id: $pid, workspace_root: $ws, auto_discover: true, sync_graphs: true, auto_link: true}')"
    else
        BOOT_PAYLOAD="{\"project_id\":\"${PROJECT_ID}\",\"workspace_root\":\"${ABS_TARGET_DIR}\",\"auto_discover\":true,\"sync_graphs\":true,\"auto_link\":true}"
    fi

    if [ -n "${TOKEN}" ]; then
        curl -fsS -X POST "${SERVER_URL}/master/hub/bootstrap" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${TOKEN}" \
            -d "${BOOT_PAYLOAD}"
    else
        curl -fsS -X POST "${SERVER_URL}/master/hub/bootstrap" \
            -H "Content-Type: application/json" \
            -d "${BOOT_PAYLOAD}"
    fi
    printf "\n"
    success "Hub bootstrap completed."
fi
