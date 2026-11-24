<#
.SYNOPSIS
    ProjectBrain Client Kit Installer (Windows PowerShell)
    Supports Remote mode (-ServerUrl) and Local mode (-LocalKit or repo clone).
#>

[CmdletBinding()]
param(
    [Alias("target-dir")]
    [string]$TargetDir = ".",
    [Alias("local-kit")]
    [string]$LocalKit = "",
    [Alias("server-url", "url")]
    [string]$ServerUrl = "",
    [string]$Token = "",
    [Alias("project-id")]
    [string]$ProjectId = "",
    [Alias("source-id")]
    [string]$SourceId = "",
    [string]$Ide = "all",
    [switch]$Bootstrap,
    [Alias("non-interactive")]
    [switch]$NonInteractive,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

function Write-Info([string]$Msg) { Write-Host "[INFO] $Msg" -ForegroundColor Cyan }
function Write-Ok([string]$Msg)   { Write-Host "[OK]   $Msg" -ForegroundColor Green }
function Write-Warn([string]$Msg) { Write-Host "[WARN] $Msg" -ForegroundColor Yellow }
function Write-Err([string]$Msg)  { Write-Host "[ERR]  $Msg" -ForegroundColor Red }

$ServerUrlExplicit = -not [string]::IsNullOrWhiteSpace($ServerUrl)

$DefaultInjectedUrl = Get-Variable -Name "PB_DEFAULT_SERVER_URL" -ValueOnly -ErrorAction SilentlyContinue
if ([string]::IsNullOrWhiteSpace($DefaultInjectedUrl)) {
    $DefaultInjectedUrl = $env:PB_DEFAULT_SERVER_URL
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    if (-not [string]::IsNullOrWhiteSpace($env:PB_SERVER_URL)) { $ServerUrl = $env:PB_SERVER_URL }
    elseif (-not [string]::IsNullOrWhiteSpace($env:PB_URL)) { $ServerUrl = $env:PB_URL }
    elseif (-not [string]::IsNullOrWhiteSpace($DefaultInjectedUrl)) { $ServerUrl = $DefaultInjectedUrl }
}

if ([string]::IsNullOrWhiteSpace($Token)) {
    if (-not [string]::IsNullOrWhiteSpace($env:PB_AUTH_TOKEN)) { $Token = $env:PB_AUTH_TOKEN }
    elseif (-not [string]::IsNullOrWhiteSpace($env:PB_TOKEN)) { $Token = $env:PB_TOKEN }
    elseif (-not [string]::IsNullOrWhiteSpace($env:PB_API_KEY)) { $Token = $env:PB_API_KEY }
}

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    if (-not [string]::IsNullOrWhiteSpace($env:PB_PROJECT_ID)) { $ProjectId = $env:PB_PROJECT_ID }
    elseif (-not [string]::IsNullOrWhiteSpace($env:PB_DEFAULT_USER_ID)) { $ProjectId = $env:PB_DEFAULT_USER_ID }
}

if ([string]::IsNullOrWhiteSpace($SourceId) -and -not [string]::IsNullOrWhiteSpace($env:PB_SOURCE_ID)) {
    $SourceId = $env:PB_SOURCE_ID
}

# Validate IDE selection
$ValidIdes = @("all", "claude", "cursor", "antigravity", "codex")
$InstallClaude = $false
$InstallCursor = $false
$InstallAntigravity = $false
$InstallCodex = $false

foreach ($part in ($Ide.ToLower() -split ",")) {
    $trimmed = $part.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
    if ($ValidIdes -notcontains $trimmed) {
        Write-Err "Unsupported -Ide value '$trimmed'. Valid values: all, claude, cursor, antigravity, codex."
        exit 2
    }
    switch ($trimmed) {
        "all" {
            $InstallClaude = $true
            $InstallCursor = $true
            $InstallAntigravity = $true
            $InstallCodex = $true
        }
        "claude"      { $InstallClaude = $true }
        "cursor"      { $InstallCursor = $true }
        "antigravity" { $InstallAntigravity = $true }
        "codex"       { $InstallCodex = $true }
    }
}

if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
}
$AbsTargetDir = (Resolve-Path $TargetDir).Path

# Auto-detect local ProjectBrain clone if executed from disk
$AutoLocalKit = ""
if ($MyInvocation.MyCommand.Path) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $CandidateRoot = Resolve-Path (Join-Path $ScriptDir "..")
    if ((Test-Path (Join-Path $CandidateRoot ".agents\skills")) -and (Test-Path (Join-Path $CandidateRoot "scripts\install_client.ps1"))) {
        $AutoLocalKit = $CandidateRoot.Path
    }
}

$Mode = ""
$KitSource = ""
if (-not [string]::IsNullOrWhiteSpace($LocalKit)) {
    if (-not (Test-Path (Join-Path $LocalKit ".agents\skills"))) {
        Write-Err "Local kit directory '$LocalKit' does not contain '.agents\skills'."
        exit 1
    }
    $Mode = "local"
    $KitSource = (Resolve-Path $LocalKit).Path
} elseif ($ServerUrlExplicit -or -not [string]::IsNullOrWhiteSpace($ServerUrl)) {
    $Mode = "remote"
} elseif (-not [string]::IsNullOrWhiteSpace($AutoLocalKit)) {
    $Mode = "local"
    $KitSource = $AutoLocalKit
} elseif (-not $NonInteractive -and [Environment]::UserInteractive) {
    $inputUrl = Read-Host "Enter ProjectBrain Server URL [http://localhost:8080]"
    $ServerUrl = if ([string]::IsNullOrWhiteSpace($inputUrl)) { "http://localhost:8080" } else { $inputUrl }
    $Mode = "remote"
} else {
    Write-Err "No -LocalKit or -ServerUrl specified and no local ProjectBrain kit found."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    $ServerUrl = "http://localhost:8080"
}
$ServerUrl = $ServerUrl.TrimEnd("/")

$DefaultProjectId = Split-Path -Leaf $AbsTargetDir
if ([string]::IsNullOrWhiteSpace($DefaultProjectId)) { $DefaultProjectId = "default" }
if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    if (-not $NonInteractive -and [Environment]::UserInteractive) {
        $inputProj = Read-Host "Enter Project ID [$DefaultProjectId]"
        $ProjectId = if ([string]::IsNullOrWhiteSpace($inputProj)) { $DefaultProjectId } else { $inputProj }
    } else {
        $ProjectId = $DefaultProjectId
    }
}

if ([string]::IsNullOrWhiteSpace($Token) -and -not $NonInteractive -and [Environment]::UserInteractive) {
    $Token = Read-Host "Enter ProjectBrain Token (optional, press Enter to skip)"
}

function Test-SkipRelativePath([string]$RelPath) {
    $norm = $RelPath.Replace("\", "/")
    if ($norm -match '(^|/)__pycache__(/|$)' -or $norm -match '(^|/)\.git(/|$)' -or $norm -match '(^|/)\.pytest_cache(/|$)' -or $norm -match '(^|/)graphify-out(/|$)') {
        return $true
    }
    if ($norm.EndsWith("/.DS_Store") -or $norm -eq ".DS_Store" -or $norm.EndsWith(".pyc") -or $norm.EndsWith(".pyo")) {
        return $true
    }
    return $false
}

$StagingDir = Join-Path ([System.IO.Path]::GetTempPath()) ("pb_client_kit_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

try {
    Write-Info "Mode        : $Mode"
    Write-Info "Target dir  : $AbsTargetDir"
    Write-Info "Server URL  : $ServerUrl"
    Write-Info "Project ID  : $ProjectId"
    Write-Info "IDE profile : $Ide"

    if ($Mode -eq "remote") {
        $ZipUrl = "$ServerUrl/client-kit/bundle.zip"
        $ZipPath = Join-Path $StagingDir "bundle.zip"
        Write-Info "Downloading client kit from $ZipUrl..."
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $StagingDir -Force
        Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
    } else {
        Write-Info "Staging local kit from $KitSource..."
        $LocalSkills = Join-Path $KitSource ".agents\skills"
        if (Test-Path $LocalSkills) {
            Get-ChildItem -Path $LocalSkills -Recurse -File | ForEach-Object {
                $rel = $_.FullName.Substring($LocalSkills.Length).TrimStart("\", "/").Replace("\", "/")
                if (Test-SkipRelativePath $rel) { return }
                $slashIdx = $rel.IndexOf("/")
                $skillRest = if ($slashIdx -ge 0) { $rel.Substring($slashIdx + 1) } else { "" }
                if ($skillRest.StartsWith("sub/") -and $skillRest -ne "sub/README.md") { return }
                $dst = Join-Path $StagingDir (".agents/skills/" + $rel)
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
                Copy-Item -Force $_.FullName $dst
            }
        }
        foreach ($subDir in @(".agents\workflows", ".claude\commands", ".cursor\commands")) {
            $srcSub = Join-Path $KitSource $subDir
            if (Test-Path $srcSub) {
                $dstSub = Join-Path $StagingDir $subDir
                New-Item -ItemType Directory -Force -Path $dstSub | Out-Null
                Get-ChildItem -Path $srcSub -Filter "*.md" -File | ForEach-Object {
                    Copy-Item -Force $_.FullName (Join-Path $dstSub $_.Name)
                }
            }
        }
        $AgentsMd = Join-Path $KitSource "AGENTS.md"
        if (Test-Path $AgentsMd) {
            Copy-Item -Force $AgentsMd (Join-Path $StagingDir "AGENTS.md")
        }
    }

    # 1. Install .agents/skills/* while STRICTLY preserving all existing */sub/* files (except sub/README.md if missing)
    $SkillsInstalled = 0
    $SubPreserved = 0
    $StagedSkills = Join-Path $StagingDir ".agents\skills"
    if (Test-Path $StagedSkills) {
        Get-ChildItem -Path $StagedSkills -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($StagedSkills.Length).TrimStart("\", "/").Replace("\", "/")
            if (Test-SkipRelativePath $rel) { return }
            $slashIdx = $rel.IndexOf("/")
            $skillRest = if ($slashIdx -ge 0) { $rel.Substring($slashIdx + 1) } else { "" }
            $dst = Join-Path $AbsTargetDir (".agents/skills/" + $rel)
            if ($skillRest.StartsWith("sub/")) {
                if ($skillRest -ne "sub/README.md" -or (Test-Path $dst)) {
                    if (Test-Path $dst) { $SubPreserved++ }
                    return
                }
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -Force $_.FullName $dst
            $SkillsInstalled++
        }
    }

    # 2. Install .agents/workflows/*.md
    $WorkflowsInstalled = 0
    $StagedWorkflows = Join-Path $StagingDir ".agents\workflows"
    if (Test-Path $StagedWorkflows) {
        $DstWorkflows = Join-Path $AbsTargetDir ".agents\workflows"
        New-Item -ItemType Directory -Force -Path $DstWorkflows | Out-Null
        Get-ChildItem -Path $StagedWorkflows -Filter "*.md" -File | ForEach-Object {
            Copy-Item -Force $_.FullName (Join-Path $DstWorkflows $_.Name)
            $WorkflowsInstalled++
        }
    }

    # 3. Install .claude/commands/*.md
    $ClaudeInstalled = 0
    if ($InstallClaude) {
        $StagedClaude = Join-Path $StagingDir ".claude\commands"
        $ClaudeFiles = if (Test-Path $StagedClaude) { @(Get-ChildItem -Path $StagedClaude -Filter "*.md" -File) } else { @() }
        if ($ClaudeFiles.Count -eq 0 -and (Test-Path $StagedWorkflows)) {
            $ClaudeFiles = @(Get-ChildItem -Path $StagedWorkflows -Filter "*.md" -File)
        }
        if ($ClaudeFiles.Count -gt 0) {
            $DstClaude = Join-Path $AbsTargetDir ".claude\commands"
            New-Item -ItemType Directory -Force -Path $DstClaude | Out-Null
            foreach ($f in $ClaudeFiles) {
                Copy-Item -Force $f.FullName (Join-Path $DstClaude $f.Name)
                $ClaudeInstalled++
            }
        }
    }

    # 4. Install .cursor/commands/*.md
    $CursorInstalled = 0
    if ($InstallCursor) {
        $StagedCursor = Join-Path $StagingDir ".cursor\commands"
        $CursorFiles = if (Test-Path $StagedCursor) { @(Get-ChildItem -Path $StagedCursor -Filter "*.md" -File) } else { @() }
        if ($CursorFiles.Count -eq 0 -and (Test-Path $StagedWorkflows)) {
            $CursorFiles = @(Get-ChildItem -Path $StagedWorkflows -Filter "*.md" -File)
        }
        if ($CursorFiles.Count -gt 0) {
            $DstCursor = Join-Path $AbsTargetDir ".cursor\commands"
            New-Item -ItemType Directory -Force -Path $DstCursor | Out-Null
            foreach ($f in $CursorFiles) {
                Copy-Item -Force $f.FullName (Join-Path $DstCursor $f.Name)
                $CursorInstalled++
            }
        }
    }

    # 5. Install AGENTS.md if present in kit and absent in target
    $StagedAgents = Join-Path $StagingDir "AGENTS.md"
    $DstAgents = Join-Path $AbsTargetDir "AGENTS.md"
    if ((Test-Path $StagedAgents) -and -not (Test-Path $DstAgents)) {
        Copy-Item -Force $StagedAgents $DstAgents
        Write-Ok "Installed AGENTS.md"
    }

    # 6. Generate or merge .mcp.json safely using ConvertFrom-Json
    $McpPath = Join-Path $AbsTargetDir ".mcp.json"
    $McpToken = if ([string]::IsNullOrWhiteSpace($Token)) { "YOUR_PB_TOKEN" } else { $Token }
    $SseUrl = "$ServerUrl/mcp/sse?token=$McpToken&project_id=$ProjectId"
    if (-not [string]::IsNullOrWhiteSpace($SourceId)) {
        $SseUrl = "$SseUrl&source_id=$SourceId"
    }
    $HttpUrl = "$ServerUrl/mcp-http"

    $McpData = $null
    if (Test-Path $McpPath) {
        try {
            $McpData = Get-Content -Path $McpPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Copy-Item -Force $McpPath "$McpPath.bak"
            $McpData = $null
        }
    }
    if ($null -eq $McpData) {
        $McpData = [PSCustomObject]@{}
    }
    if ($null -eq $McpData.PSObject.Properties["mcpServers"] -or $null -eq $McpData.mcpServers) {
        $McpData | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{}) -Force
    }

    $HttpHeaders = [PSCustomObject]@{
        "Authorization" = "Bearer $McpToken"
        "X-PB-Project-Id" = $ProjectId
    }
    if (-not [string]::IsNullOrWhiteSpace($SourceId)) {
        $HttpHeaders | Add-Member -MemberType NoteProperty -Name "X-PB-Source-Id" -Value $SourceId -Force
    }

    $SseEntry = [PSCustomObject]@{
        type = "sse"
        url = $SseUrl
        headers = [PSCustomObject]@{ Authorization = "Bearer $McpToken" }
    }
    $HttpEntry = [PSCustomObject]@{
        type = "http"
        url = $HttpUrl
        headers = $HttpHeaders
    }

    $McpData.mcpServers | Add-Member -MemberType NoteProperty -Name "projectbrain" -Value $SseEntry -Force
    $McpData.mcpServers | Add-Member -MemberType NoteProperty -Name "projectbrain-http" -Value $HttpEntry -Force

    $JsonOut = ($McpData | ConvertTo-Json -Depth 10) + "`n"
    [System.IO.File]::WriteAllText($McpPath, $JsonOut, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "Updated $McpPath"

    Write-Ok "Installed $SkillsInstalled skill file(s), $WorkflowsInstalled workflow(s), $ClaudeInstalled Claude command(s), $CursorInstalled Cursor command(s) (sub/ files preserved: $SubPreserved)."

    # 7. Optional Project Hub bootstrap
    if ($Bootstrap) {
        Write-Info "Bootstrapping Project Hub via $ServerUrl/master/hub/bootstrap..."
        $Headers = @{ "Content-Type" = "application/json" }
        if (-not [string]::IsNullOrWhiteSpace($Token)) {
            $Headers["Authorization"] = "Bearer $Token"
        }
        $BootBody = @{
            project_id = $ProjectId
            workspace_root = $AbsTargetDir
            auto_discover = $true
            sync_graphs = $true
            auto_link = $true
        } | ConvertTo-Json
        $BootResp = Invoke-RestMethod -Method Post -Uri "$ServerUrl/master/hub/bootstrap" -Headers $Headers -Body $BootBody
        Write-Ok "Hub bootstrap completed."
    }
} finally {
    if (Test-Path $StagingDir) {
        Remove-Item -Recurse -Force $StagingDir -ErrorAction SilentlyContinue
    }
}
