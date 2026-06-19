# Release Publishing

GitHub Releases are the easiest way to give a friend a one-command installation path.

## One-Line Install

Latest release. This installs both the CLI and the global Codex skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 | iex"
```

CLI-only opt-out:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Join-Path $env:TEMP 'investor-install.ps1'; irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 -OutFile $p; & $p -SkipCodexSkill"
```

Specific release:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Join-Path $env:TEMP 'investor-install.ps1'; irm https://github.com/Eliran-Turgeman/investor/releases/download/v0.2.0/install.ps1 -OutFile $p; & $p -Version v0.2.0"
```

## Publishing

1. Commit all changes.
2. Run tests locally with `python -m unittest`.
3. Push a release tag:

```powershell
.\scripts\publish-release.ps1 -Version 0.2.0
```

The GitHub Actions release workflow runs tests, validates PowerShell syntax, builds `investor-toolkit.zip`, and publishes these release assets:

- `install.ps1`
- `investor-toolkit.zip`

The installer downloads `investor-toolkit.zip`, extracts it to `%USERPROFILE%\investor-toolkit` by default, runs `scripts/setup.ps1`, installs the Codex skill globally, and then runs `scripts/doctor.ps1`.

## Requirements

- The repository must be pushed to GitHub.
- The tag must match `vMAJOR.MINOR.PATCH`, for example `v0.1.0`.
- GitHub Actions needs the default `GITHUB_TOKEN` with `contents: write`, which the workflow requests.
