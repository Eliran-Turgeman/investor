[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }
if ($Tag -notmatch '^v\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$') {
    throw "Version must look like 0.1.0 or v0.1.0."
}

& git diff --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Working tree has unstaged changes. Commit or stash them before publishing $Tag."
}

& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Index has staged changes. Commit or unstage them before publishing $Tag."
}

& git rev-parse -q --verify "refs/tags/$Tag" *> $null
if ($LASTEXITCODE -eq 0) {
    throw "Tag already exists locally: $Tag"
}

Write-Host "[release] Creating annotated tag $Tag"
& git tag -a $Tag -m "Investor Toolkit $Tag"
if ($LASTEXITCODE -ne 0) {
    throw "git tag failed."
}

Write-Host "[release] Pushing $Tag to origin"
& git push origin $Tag
if ($LASTEXITCODE -ne 0) {
    throw "git push failed."
}

Write-Host ""
Write-Host "GitHub Actions will publish the release assets for $Tag."
Write-Host "Release page:"
Write-Host "  https://github.com/Eliran-Turgeman/investor/releases/tag/$Tag"
