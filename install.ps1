[CmdletBinding()]
param(
    [string]$Repo = "Eliran-Turgeman/investor",
    [string]$Version = "latest",
    [string]$InstallDir = (Join-Path $env:USERPROFILE "investor-toolkit"),
    [switch]$SkipCodexSkill,
    [switch]$SkipCodexMcp,
    [string]$SecUserAgent = "InvestorResearchAssistant contact@example.com",
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[install] $Message"
}

if ([string]::IsNullOrWhiteSpace($Repo) -or -not $Repo.Contains("/")) {
    throw "Repo must be in owner/name format."
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    throw "InstallDir cannot be empty."
}

$ReleaseBase = if ($Version -eq "latest") {
    "https://github.com/$Repo/releases/latest/download"
} else {
    "https://github.com/$Repo/releases/download/$Version"
}

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "investor-toolkit-install-$([System.Guid]::NewGuid().ToString('N'))"
$ZipPath = Join-Path $TempRoot "investor-toolkit.zip"
$ExtractRoot = Join-Path $TempRoot "extract"

New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

try {
    Write-Step "Downloading $ReleaseBase/investor-toolkit.zip"
    Invoke-WebRequest -Uri "$ReleaseBase/investor-toolkit.zip" -OutFile $ZipPath -UseBasicParsing

    Write-Step "Extracting release package"
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractRoot -Force

    Write-Step "Installing to $InstallDir"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Get-ChildItem -Force $ExtractRoot | Copy-Item -Destination $InstallDir -Recurse -Force

    $SetupScript = Join-Path $InstallDir "scripts\setup.ps1"
    if (-not (Test-Path $SetupScript)) {
        throw "Release package is missing scripts\setup.ps1."
    }

    Write-Step "Running setup"
    if ($SkipCodexSkill -and $SkipCodexMcp) {
        & $SetupScript -SkipCodexSkill -SkipCodexMcp -SecUserAgent $SecUserAgent
    } elseif ($SkipCodexSkill) {
        & $SetupScript -SkipCodexSkill -SecUserAgent $SecUserAgent
    } elseif ($SkipCodexMcp) {
        & $SetupScript -SkipCodexMcp -SecUserAgent $SecUserAgent
    } else {
        & $SetupScript -SecUserAgent $SecUserAgent
    }
    if ($LASTEXITCODE -ne 0) {
        throw "setup.ps1 failed."
    }

    if (-not $SkipDoctor) {
        $DoctorScript = Join-Path $InstallDir "scripts\doctor.ps1"
        if (Test-Path $DoctorScript) {
            Write-Step "Running doctor"
            & $DoctorScript
            if ($LASTEXITCODE -ne 0) {
                throw "doctor.ps1 found blocking setup issues."
            }
        }
    }

    Write-Host ""
    Write-Host "Installed Investor Toolkit to:"
    Write-Host "  $InstallDir"
    Write-Host ""
    Write-Host "Next commands:"
    Write-Host "  Set-Location `"$InstallDir`""
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host '  $env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"'
    Write-Host "  investor quickstart MSFT"
    Write-Host ""
    Write-Host "The investor-toolkit Codex skill and MCP server were installed globally unless skipped."
    Write-Host "Restart Codex to load the investor MCP server."
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
