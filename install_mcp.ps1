# ResearchForge — MCP installer (Windows, no Python/venv required)
#
# Installs the self-contained ResearchForge.exe to a canonical per-user
# location and registers it with Claude Code as an MCP server. The exe doubles
# as the GUI app (double-click) and the MCP server (run with --mcp).
#
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/baachraf/ResearchForge/main/install_mcp.ps1 | iex
#   # or, from a clone:
#   ./install_mcp.ps1

$ErrorActionPreference = "Stop"

# --- Canonical location (source of truth, no admin rights needed) ----------
$Dir = Join-Path $env:LOCALAPPDATA "Programs\ResearchForge"
$Exe = Join-Path $Dir "ResearchForge.exe"
$Url = "https://github.com/baachraf/ResearchForge/releases/latest/download/ResearchForge.exe"

# --- Download only if not already present ----------------------------------
if (Test-Path $Exe) {
    Write-Host "ResearchForge.exe already present at:`n  $Exe" -ForegroundColor Green
} else {
    Write-Host "Downloading ResearchForge.exe -> $Exe" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    Invoke-WebRequest -Uri $Url -OutFile $Exe
    Write-Host "Downloaded." -ForegroundColor Green
}

# --- Register with Claude Code (user scope = all projects) -----------------
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "The 'claude' CLI was not found on PATH. The exe is installed at:`n  $Exe`nRegister it manually with:`n  claude mcp add researchforge -s user -- `"$Exe`" --mcp"
    return
}

# Replace any prior registration so re-runs are idempotent
claude mcp remove researchforge -s user 2>$null | Out-Null
claude mcp add researchforge -s user -- "$Exe" --mcp

Write-Host "`nVerifying..." -ForegroundColor Cyan
claude mcp get researchforge

Write-Host "`nDone. Open a new Claude Code session to use the rf_* tools." -ForegroundColor Green
