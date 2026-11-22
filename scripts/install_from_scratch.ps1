<#
.SYNOPSIS
    ProjectBrain v3.0 — From-Scratch Bootstrap Installer (Windows PowerShell)

.DESCRIPTION
    Creates a Python >= 3.10 virtual environment, upgrades pip, installs the selected
    ProjectBrain dependency profile, initializes .env from .env.example if missing,
    runs hardware-status and plugins list sanity checks, and prints launch instructions.

.PARAMETER Profile
    Installation profile: standard (default), cpu-ocr, gpu, cuda-full

.PARAMETER VenvDir
    Virtual environment directory (default: .venv)

.PARAMETER PythonBin
    Optional explicit Python executable path or command (must be >= 3.10)

.PARAMETER NonInteractive
    Skip interactive prompt when -Profile is not specified (defaults to standard)

.PARAMETER SkipChecks
    Skip post-install 'projectbrain hardware-status' and 'projectbrain plugins list' checks

.EXAMPLE
    .\scripts\install_from_scratch.ps1 -Profile standard
    .\scripts\install_from_scratch.ps1 -Profile cpu-ocr
    .\scripts\install_from_scratch.ps1 -Profile gpu
#>

[CmdletBinding()]
param(
    [ValidateSet("", "standard", "cpu-ocr", "gpu", "cuda-full")]
    [string]$Profile = "",
    [string]$VenvDir = ".venv",
    [string]$PythonBin = "",
    [switch]$NonInteractive,
    [switch]$SkipChecks,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

function Write-Info([string]$Msg)    { Write-Host "[INFO] $Msg" -ForegroundColor Cyan }
function Write-Ok([string]$Msg)      { Write-Host "[OK]   $Msg" -ForegroundColor Green }
function Write-WarnMsg([string]$Msg) { Write-Host "[WARN] $Msg" -ForegroundColor Yellow }
function Write-ErrMsg([string]$Msg)  { Write-Host "[ERR]  $Msg" -ForegroundColor Red }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

Write-Host ""
Write-Host "==============================================================" -ForegroundColor White
Write-Host "  ProjectBrain v3.0 — From-Scratch Installer (Windows)        " -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor White
Write-Host ""

# 1. Interactive profile selection if omitted
if ([string]::IsNullOrWhiteSpace($Profile)) {
    if (-not $NonInteractive -and [Environment]::UserInteractive) {
        Write-Host "Select an installation profile:"
        Write-Host "  1) standard   - Core Graphify AST, REST/MCP Server, Web UI, Office parsers (default)"
        Write-Host "  2) cpu-ocr    - Standard + RapidOCR (ONNX Runtime CPU offline OCR)"
        Write-Host "  3) gpu        - Standard + PaddleOCR GPU + PyTorch/Sentence-Transformers GPU"
        Write-Host "  4) cuda-full  - Standard + PaddleOCR GPU + PyTorch + NVIDIA RAPIDS cuGraph/cuDF"
        $choice = Read-Host "Choose [1-4] (default: 1)"
        switch ($choice) {
            ""          { $Profile = "standard" }
            "1"         { $Profile = "standard" }
            "standard"  { $Profile = "standard" }
            "2"         { $Profile = "cpu-ocr" }
            "cpu-ocr"   { $Profile = "cpu-ocr" }
            "3"         { $Profile = "gpu" }
            "gpu"       { $Profile = "gpu" }
            "4"         { $Profile = "cuda-full" }
            "cuda-full" { $Profile = "cuda-full" }
            default {
                Write-ErrMsg "Invalid selection '$choice'. Expected 1-4 or profile name."
                exit 2
            }
        }
    } else {
        $Profile = "standard"
    }
}

$PipTarget = switch ($Profile) {
    "standard"  { "." }
    "cpu-ocr"   { ".[ocr-onnx]" }
    "gpu"       { ".[ocr-gpu,graphify-gpu]" }
    "cuda-full" { ".[ocr-gpu,graphify-cuda]" }
}

Write-Info "Repository root : $RepoRoot"
Write-Info "Install profile : $Profile (pip install -e `"$PipTarget`")"

# 2. Locate Python >= 3.10
function Test-Python310 {
    param([string]$Cmd, [string[]]$PrefixArgs = @())
    try {
        $null = & $Cmd @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$ResolvedCmd = ""
$ResolvedArgs = @()

if (-not [string]::IsNullOrWhiteSpace($PythonBin)) {
    if (Test-Python310 -Cmd $PythonBin) {
        $ResolvedCmd = $PythonBin
    } else {
        Write-ErrMsg "Provided Python '$PythonBin' was not found or is older than Python 3.10."
        exit 1
    }
} else {
    $candidates = @(
        @{ Cmd = "py"; Args = @("-3.12") },
        @{ Cmd = "py"; Args = @("-3.11") },
        @{ Cmd = "py"; Args = @("-3.10") },
        @{ Cmd = "py"; Args = @("-3") },
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "python3"; Args = @() }
    )
    foreach ($cand in $candidates) {
        if (Get-Command $cand.Cmd -ErrorAction SilentlyContinue) {
            if (Test-Python310 -Cmd $cand.Cmd -PrefixArgs $cand.Args) {
                $ResolvedCmd = $cand.Cmd
                $ResolvedArgs = $cand.Args
                break
            }
        }
    }
}

if ([string]::IsNullOrWhiteSpace($ResolvedCmd)) {
    Write-ErrMsg "Python >= 3.10 is required but was not found on PATH."
    Write-ErrMsg "Install Python 3.10+ (e.g. 'winget install Python.Python.3.12') and re-run."
    exit 1
}

$PyVersion = & $ResolvedCmd @ResolvedArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
Write-Ok "Detected Python $PyVersion ($ResolvedCmd $($ResolvedArgs -join ' '))"

# 3. Create or reuse virtual environment
$VenvPath = Join-Path $RepoRoot $VenvDir
if (-not (Test-Path $VenvPath)) {
    Write-Info "Creating virtual environment at $VenvDir..."
    & $ResolvedCmd @ResolvedArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Ok "Virtual environment created."
} else {
    Write-Info "Reusing existing virtual environment at $VenvDir."
}

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$VenvPb = Join-Path $VenvPath "Scripts\projectbrain.exe"

if (-not (Test-Path $VenvPython)) {
    Write-ErrMsg "Virtual environment Python not found at $VenvPython."
    exit 1
}

# 4. Upgrade pip & install selected profile
Write-Info "Upgrading pip, setuptools, and wheel..."
& $VenvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Info "Installing ProjectBrain ($Profile)..."
if ($Profile -eq "standard") {
    & $VenvPython -m pip install -e .
} else {
    & $VenvPython -m pip install -e $PipTarget
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Ok "Dependencies installed for profile '$Profile'."

# 5. Bootstrap .env from .env.example if absent
$EnvFile = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Ok "Created .env from .env.example."
    } else {
        Write-WarnMsg ".env.example not found; skipping .env bootstrap."
    }
} else {
    Write-Info "Existing .env detected; preserving your current configuration."
}

# 6. Post-install sanity checks
if (-not $SkipChecks) {
    Write-Info "Running sanity check: projectbrain hardware-status"
    try {
        if (Test-Path $VenvPb) { & $VenvPb hardware-status } else { & $VenvPython -m projectbrain.main hardware-status }
    } catch {
        Write-WarnMsg "hardware-status check encountered an issue: $_"
    }

    Write-Info "Running sanity check: projectbrain plugins list"
    try {
        if (Test-Path $VenvPb) { & $VenvPb plugins list } else { & $VenvPython -m projectbrain.main plugins list }
    } catch {
        Write-WarnMsg "plugins list check encountered an issue: $_"
    }
} else {
    Write-WarnMsg "Skipping post-install sanity checks (-SkipChecks)."
}

# 7. Next-step instructions
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  ProjectBrain v3.0 installation complete!" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Activate the virtual environment:"
Write-Host "       .\$VenvDir\Scripts\Activate.ps1"
Write-Host ""
Write-Host "  2. (Optional) Edit .env to configure PostgreSQL, Ollama/Local AI, or Auth."
Write-Host ""
Write-Host "  3. Start the ProjectBrain REST, MCP & Studio UI server:"
Write-Host "       projectbrain serve"
Write-Host "     (or: .\$VenvDir\Scripts\python.exe -m projectbrain.main serve)"
Write-Host ""
Write-Host "  4. Open the v3.0 Engineering Workbench in your browser:"
Write-Host "       http://localhost:8080/dashboard/"
Write-Host ""
Write-Host "  First-time local admin seed token: pb_tok_admin"
Write-Host "  (Recommended: switch to 'Token / API Key' on Login Gate, sign in with"
Write-Host "   pb_tok_admin, then set a password & rotate your token in My Profile / RBAC Admin.)"
Write-Host ""
