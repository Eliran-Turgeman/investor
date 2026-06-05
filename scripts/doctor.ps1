[CmdletBinding()]
param()

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Failures = 0

function Write-Ok {
    param([string]$Message)
    Write-Host "[ok] $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[warn] $Message"
}

function Write-Fail {
    param([string]$Message)
    $script:Failures += 1
    Write-Host "[fail] $Message"
}

$Python = "python"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
}

$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Python 3.11+ available: $PythonVersion"
} else {
    Write-Fail "Python 3.11+ is required. Run .\scripts\setup.ps1 after installing Python."
}

$ImportPath = & $Python -c "import investor_toolkit, pathlib; print(pathlib.Path(investor_toolkit.__file__).resolve())" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "investor_toolkit imports from $ImportPath"
} else {
    Write-Fail "investor_toolkit is not importable. Run .\scripts\setup.ps1."
}

$PathCommand = Get-Command investor -ErrorAction SilentlyContinue
$VenvInvestor = Join-Path $RepoRoot ".venv\Scripts\investor.exe"
if ($PathCommand) {
    Write-Ok "investor command is on PATH: $($PathCommand.Source)"
} elseif (Test-Path $VenvInvestor) {
    Write-Ok "investor console script is installed in .venv; activate with .\.venv\Scripts\Activate.ps1"
} else {
    Write-Fail "investor console script was not found. Run .\scripts\setup.ps1."
}

$SecUserAgent = [Environment]::GetEnvironmentVariable("SEC_USER_AGENT")
if ([string]::IsNullOrWhiteSpace($SecUserAgent) -or $SecUserAgent.ToLower().Contains("set sec_user_agent")) {
    Write-Warn 'SEC_USER_AGENT is not set. Online SEC commands need: $env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"'
} else {
    Write-Ok "SEC_USER_AGENT is set."
}

$ResearchHome = [Environment]::GetEnvironmentVariable("RESEARCH_HOME")
if ([string]::IsNullOrWhiteSpace($ResearchHome)) {
    Write-Ok "RESEARCH_HOME is not set; default research root is .\research"
} else {
    Write-Ok "RESEARCH_HOME is set to $ResearchHome"
}

$LocalSkill = Join-Path $RepoRoot "skills\investor-toolkit\SKILL.md"
if (Test-Path $LocalSkill) {
    Write-Ok "Local Codex skill is present: $LocalSkill"
} else {
    Write-Warn "Local Codex skill is missing: $LocalSkill"
}

$GlobalSkill = Join-Path $env:USERPROFILE ".codex\skills\investor-toolkit\SKILL.md"
if (Test-Path $GlobalSkill) {
    Write-Ok "Global Codex skill is installed: $GlobalSkill"
} else {
    Write-Warn "Global Codex skill is not installed. Run .\scripts\setup.ps1 to install it, or use -SkipCodexSkill for CLI-only setup."
}

$StooqKey = [Environment]::GetEnvironmentVariable("STOOQ_API_KEY")
if ([string]::IsNullOrWhiteSpace($StooqKey)) {
    Write-Warn "STOOQ_API_KEY is not set. This is optional; Yahoo is used as the default market-data source."
} else {
    Write-Ok "STOOQ_API_KEY is set."
}

if ($Failures -gt 0) {
    exit 1
}
exit 0
