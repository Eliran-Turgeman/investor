[CmdletBinding()]
param(
    [switch]$SkipCodexSkill,
    [switch]$SkipCodexMcp,
    [string]$SecUserAgent = "InvestorResearchAssistant contact@example.com"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host "[setup] $Message"
}

function ConvertTo-TomlLiteral {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Remove-TomlSection {
    param(
        [string]$Content,
        [string]$SectionName
    )
    $pattern = "(?ms)^\[$([regex]::Escape($SectionName))\]\r?\n.*?(?=^\[|\z)"
    return [regex]::Replace($Content, $pattern, "")
}

function Install-CodexMcpServer {
    param(
        [string]$ConfigPath,
        [string]$RepoRoot,
        [string]$VenvPython,
        [string]$SecUserAgent
    )

    $ConfigDir = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $Existing = if (Test-Path $ConfigPath) {
        Get-Content -Raw -Path $ConfigPath
    } else {
        ""
    }

    $Existing = Remove-TomlSection -Content $Existing -SectionName "mcp_servers.investor"
    $Existing = Remove-TomlSection -Content $Existing -SectionName "mcp_servers.investor.env"
    $Existing = $Existing.TrimEnd()

    $ResearchRoot = Join-Path $RepoRoot "research"
    $PortfolioDir = Join-Path $RepoRoot "portfolio"
    $AssumptionsDir = Join-Path $RepoRoot "assumptions"
    $ValuationsDir = Join-Path $RepoRoot "valuations"
    $McpBlock = @(
        "[mcp_servers.investor]",
        "command = $(ConvertTo-TomlLiteral $VenvPython)",
        "args = [",
        "  ""-m"",",
        "  ""investor_toolkit.mcp_server"",",
        "  ""--workspace-root"",",
        "  $(ConvertTo-TomlLiteral $RepoRoot),",
        "  ""--research-root"",",
        "  $(ConvertTo-TomlLiteral $ResearchRoot),",
        "  ""--portfolio-dir"",",
        "  $(ConvertTo-TomlLiteral $PortfolioDir),",
        "  ""--assumptions-dir"",",
        "  $(ConvertTo-TomlLiteral $AssumptionsDir),",
        "  ""--valuations-dir"",",
        "  $(ConvertTo-TomlLiteral $ValuationsDir)",
        "]",
        "",
        "[mcp_servers.investor.env]",
        "SEC_USER_AGENT = $(ConvertTo-TomlLiteral $SecUserAgent)"
    ) -join "`n"

    $Prefix = if ([string]::IsNullOrWhiteSpace($Existing)) { "" } else { "$Existing`n`n" }
    Set-Content -Path $ConfigPath -Value "$Prefix$McpBlock`n" -Encoding utf8
}

Write-Step "Checking Python 3.11+"
& python -c "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11+ is required. Install Python 3.11 or newer and rerun this script."
}

if (-not (Test-Path ".venv")) {
    Write-Step "Creating .venv"
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv."
    }
} else {
    Write-Step "Using existing .venv"
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Could not find .venv Python at $VenvPython."
}

Write-Step "Upgrading pip"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

Write-Step "Installing investor-toolkit in editable mode"
& $VenvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    throw "Editable install failed."
}

if (-not $SkipCodexSkill) {
    $SkillSource = Join-Path $RepoRoot "skills\investor-toolkit"
    $SkillTarget = Join-Path $env:USERPROFILE ".codex\skills\investor-toolkit"
    if (-not (Test-Path $SkillSource)) {
        throw "Local Codex skill folder is missing: $SkillSource"
    }
    Write-Step "Installing Codex skill to $SkillTarget"
    New-Item -ItemType Directory -Force -Path $SkillTarget | Out-Null
    Copy-Item -Path (Join-Path $SkillSource "*") -Destination $SkillTarget -Recurse -Force
} else {
    Write-Step "Skipping Codex skill installation"
}

if ($SkipCodexSkill -or $SkipCodexMcp) {
    Write-Step "Skipping Codex MCP server registration"
} else {
    $CodexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
    Write-Step "Registering Codex MCP server in $CodexConfig"
    Install-CodexMcpServer `
        -ConfigPath $CodexConfig `
        -RepoRoot $RepoRoot `
        -VenvPython $VenvPython `
        -SecUserAgent $SecUserAgent
}

Write-Host ""
Write-Host "Next commands:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host '  $env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"'
Write-Host "  .\scripts\doctor.ps1"
Write-Host "  investor quickstart MSFT"
Write-Host "  Restart Codex to load the investor MCP server."
