#!/usr/bin/env bash
# ==============================================================================
# ProjectBrain v3.0 — From-Scratch Bootstrap Installer (macOS / Linux)
# ==============================================================================
# Supports:
#   --profile standard   : Core Graphify AST, FastAPI, FastMCP, Office parsers (default)
#   --profile cpu-ocr    : Standard + RapidOCR ONNX Runtime (lightweight offline CPU OCR)
#   --profile gpu        : Standard + PaddleOCR GPU + PyTorch/Sentence-Transformers GPU/MPS
#   --profile cuda-full  : Standard + PaddleOCR GPU + PyTorch + NVIDIA RAPIDS cuGraph/cuDF
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROFILE=""
VENV_DIR=".venv"
PYTHON_BIN=""
NON_INTERACTIVE=false
SKIP_CHECKS=false

# Color helpers (only when stdout is a terminal)
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
ProjectBrain v3.0 — From-Scratch Installer (macOS / Linux)

Usage:
  ./scripts/install_from_scratch.sh [OPTIONS]

Options:
  --profile <name>     Install profile: standard (default), cpu-ocr, gpu, cuda-full
  --venv-dir <path>    Virtual environment directory (default: .venv)
  --python <bin>       Specific Python >= 3.10 executable to use
  --non-interactive    Do not prompt; use --profile or default 'standard'
  --skip-checks        Skip post-install 'hardware-status' and 'plugins list' checks
  -h, --help           Show this help message and exit

Profiles:
  standard             pip install -e .
  cpu-ocr              pip install -e ".[ocr-onnx]"
  gpu                  pip install -e ".[ocr-gpu,graphify-gpu]"
  cuda-full            pip install -e ".[ocr-gpu,graphify-cuda]"

Examples:
  ./scripts/install_from_scratch.sh --profile standard
  ./scripts/install_from_scratch.sh --profile cpu-ocr
  ./scripts/install_from_scratch.sh --profile gpu
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --profile)
            [ $# -ge 2 ] || { error "Missing value for --profile"; exit 2; }
            PROFILE="$2"
            shift 2
            ;;
        --profile=*)
            PROFILE="${1#*=}"
            shift
            ;;
        --venv-dir)
            [ $# -ge 2 ] || { error "Missing value for --venv-dir"; exit 2; }
            VENV_DIR="$2"
            shift 2
            ;;
        --venv-dir=*)
            VENV_DIR="${1#*=}"
            shift
            ;;
        --python)
            [ $# -ge 2 ] || { error "Missing value for --python"; exit 2; }
            PYTHON_BIN="$2"
            shift 2
            ;;
        --python=*)
            PYTHON_BIN="${1#*=}"
            shift
            ;;
        --non-interactive|-y)
            NON_INTERACTIVE=true
            shift
            ;;
        --skip-checks)
            SKIP_CHECKS=true
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

cd "${REPO_ROOT}"

printf "\n%s==============================================================%s\n" "${C_BOLD}" "${C_RESET}"
printf "%s  ProjectBrain v3.0 — From-Scratch Installer (macOS / Linux)  %s\n" "${C_BOLD}" "${C_RESET}"
printf "%s==============================================================%s\n\n" "${C_BOLD}" "${C_RESET}"

# 1. Interactive profile selection if not provided and running in a TTY
if [ -z "${PROFILE}" ]; then
    if [ "${NON_INTERACTIVE}" = false ] && [ -t 0 ]; then
        printf "Select an installation profile:\n"
        printf "  1) standard   - Core Graphify AST, REST/MCP Server, Web UI, Office parsers (default)\n"
        printf "  2) cpu-ocr    - Standard + RapidOCR (ONNX Runtime CPU offline OCR)\n"
        printf "  3) gpu        - Standard + PaddleOCR GPU + PyTorch/Sentence-Transformers GPU/MPS\n"
        printf "  4) cuda-full  - Standard + PaddleOCR GPU + PyTorch + NVIDIA RAPIDS cuGraph/cuDF\n"
        printf "Choose [1-4] (default: 1): "
        read -r choice
        case "${choice:-1}" in
            1|standard)  PROFILE="standard" ;;
            2|cpu-ocr)   PROFILE="cpu-ocr" ;;
            3|gpu)       PROFILE="gpu" ;;
            4|cuda-full) PROFILE="cuda-full" ;;
            *)
                error "Invalid selection '${choice}'. Expected 1-4 or profile name."
                exit 2
                ;;
        esac
    else
        PROFILE="standard"
    fi
fi

case "${PROFILE}" in
    standard)
        PIP_TARGET="."
        ;;
    cpu-ocr)
        PIP_TARGET=".[ocr-onnx]"
        ;;
    gpu)
        PIP_TARGET=".[ocr-gpu,graphify-gpu]"
        ;;
    cuda-full)
        PIP_TARGET=".[ocr-gpu,graphify-cuda]"
        ;;
    *)
        error "Unsupported profile '${PROFILE}'. Valid values: standard, cpu-ocr, gpu, cuda-full."
        exit 2
        ;;
esac

info "Repository root : ${REPO_ROOT}"
info "Install profile : ${PROFILE} (pip install -e \"${PIP_TARGET}\")"

# 2. Locate Python >= 3.10
check_python_ge_310() {
    local candidate="$1"
    if ! command -v "${candidate}" >/dev/null 2>&1; then
        return 1
    fi
    "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

if [ -n "${PYTHON_BIN}" ]; then
    if ! check_python_ge_310 "${PYTHON_BIN}"; then
        error "Provided Python '${PYTHON_BIN}' was not found or is older than Python 3.10."
        exit 1
    fi
else
    for cand in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if check_python_ge_310 "${cand}"; then
            PYTHON_BIN="$(command -v "${cand}")"
            break
        fi
    done
fi

if [ -z "${PYTHON_BIN}" ]; then
    error "Python >= 3.10 is required but was not found on PATH."
    error "Install Python 3.10+ (e.g. 'brew install python@3.12' or 'sudo apt install python3 python3-venv') and re-run."
    exit 1
fi

PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
success "Detected Python ${PY_VERSION} at ${PYTHON_BIN}"

# 3. Create or reuse virtual environment
if [ ! -d "${VENV_DIR}" ]; then
    info "Creating virtual environment at ${VENV_DIR}..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    success "Virtual environment created."
else
    info "Reusing existing virtual environment at ${VENV_DIR}."
fi

VENV_PYTHON="${REPO_ROOT}/${VENV_DIR}/bin/python"
VENV_PIP="${REPO_ROOT}/${VENV_DIR}/bin/pip"
VENV_PB="${REPO_ROOT}/${VENV_DIR}/bin/projectbrain"

if [ ! -x "${VENV_PYTHON}" ]; then
    error "Virtual environment Python not found at ${VENV_PYTHON}."
    exit 1
fi

# 4. Upgrade pip & install selected profile
info "Upgrading pip, setuptools, and wheel..."
"${VENV_PYTHON}" -m pip install --upgrade pip setuptools wheel

info "Installing ProjectBrain (${PROFILE})..."
if [ "${PROFILE}" = "standard" ]; then
    "${VENV_PIP}" install -e .
else
    "${VENV_PIP}" install -e "${PIP_TARGET}"
fi
success "Dependencies installed for profile '${PROFILE}'."

# 5. Bootstrap .env from .env.example if absent
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp ".env.example" ".env"
        success "Created .env from .env.example."
    else
        warn ".env.example not found; skipping .env bootstrap."
    fi
else
    info "Existing .env detected; preserving your current configuration."
fi

# 6. Post-install sanity checks
if [ "${SKIP_CHECKS}" = false ]; then
    info "Running sanity check: projectbrain hardware-status"
    if [ -x "${VENV_PB}" ]; then
        "${VENV_PB}" hardware-status || warn "hardware-status returned a non-zero exit status."
    else
        "${VENV_PYTHON}" -m projectbrain.main hardware-status || warn "hardware-status returned a non-zero exit status."
    fi

    info "Running sanity check: projectbrain plugins list"
    if [ -x "${VENV_PB}" ]; then
        "${VENV_PB}" plugins list || warn "plugins list returned a non-zero exit status."
    else
        "${VENV_PYTHON}" -m projectbrain.main plugins list || warn "plugins list returned a non-zero exit status."
    fi
else
    warn "Skipping post-install sanity checks (--skip-checks)."
fi

# 7. Next-step instructions
printf "\n%s==============================================================%s\n" "${C_GREEN}" "${C_RESET}"
printf "%s  ProjectBrain v3.0 installation complete!%s\n" "${C_BOLD}" "${C_RESET}"
printf "%s==============================================================%s\n\n" "${C_GREEN}" "${C_RESET}"
printf "Next steps:\n"
printf "  1. Activate the virtual environment:\n"
printf "       source %s/bin/activate\n\n" "${VENV_DIR}"
printf "  2. (Optional) Edit .env to configure PostgreSQL, Ollama/Local AI, or Auth.\n\n"
printf "  3. Start the ProjectBrain REST, MCP & Studio UI server:\n"
printf "       projectbrain serve\n"
printf "     (or: %s -m projectbrain.main serve)\n\n" "${VENV_DIR}/bin/python"
printf "  4. Open the v3.0 Engineering Workbench in your browser:\n"
printf "       http://localhost:8080/dashboard/\n\n"
printf "  First-time local admin seed token: pb_tok_admin\n"
printf "  (Recommended: switch to 'Token / API Key' on Login Gate, sign in with\n"
printf "   pb_tok_admin, then set a password & rotate your token in My Profile / RBAC Admin.)\n\n"
