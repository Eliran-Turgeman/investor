# Value Investing Research Data CLI

This repository separates deterministic data work from agent analysis.

The `research` CLI is a local-first data provider. It fetches SEC filings, caches raw provider responses, extracts filing sections, normalizes financial data, imports historical prices, calculates deterministic metrics, and writes machine-readable/local-readable artifacts under `research/<TICKER>/`.

It does not answer investment questions, generate memos, challenge theses, or estimate fair value. Use the repo-local Codex skill for that agent experience.

## Install

Requirements:

- Python 3.11+
- A local checkout of this repository
- Internet access for live ingestion
- A descriptive SEC user agent for SEC requests

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
research --help
```

## Configuration

SEC requires a descriptive user agent for automated requests:

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
```

Use any appropriate project/contact address; it does not need to be a personal email.

Optional:

```powershell
$env:RESEARCH_HOME = ".\research"
$env:STOOQ_API_KEY = "..."
```

## CLI Commands

```powershell
research start MSFT
research ingest MSFT
research metrics MSFT
```

| Command | Purpose |
| --- | --- |
| `research start <ticker>` | Create the local data folder and, unless `--offline`, run initial ingestion. |
| `research ingest <ticker>` | Refresh filings, provider responses, normalized data, extracted sections, metrics, and chunk index. |
| `research metrics <ticker>` | Recalculate deterministic metrics from normalized local data. |

Offline mode is available for local-only rebuilds:

```powershell
research start MSFT --offline
research ingest MSFT --offline
research metrics MSFT --offline
```

Use `start` for first-time setup. Use `ingest` later to refresh or rebuild an existing ticker folder. Online `start` already runs ingestion after creating `company.json`.

## Local Artifacts

```text
research/
  MSFT/
    company.json
    filings/
      raw/
      metadata/
        filings.json
    extracted/
      2025-10K/
        business.md
        risk-factors.md
        mdna.md
        financial-statements.md
        notes.md
        extraction.json
    data/
      company_facts.json
      financials.json
      financials.csv
      income_statement.csv
      balance_sheet.csv
      cash_flow.csv
      prices.json
      prices.csv
      provider_responses/
    metrics/
      metrics.json
      metrics.md
    index/
      filing_chunks.jsonl
```

The CLI owns source data, normalized data, extracted filing text, metrics, and indexes. Agents or users may create separate files such as `memo.md`, `questions.md`, or valuation notes, but the CLI will not generate analysis.

## Agent Integration

The repo includes two local agent integration points:

- Codex skill: `skills/value-investing-research/SKILL.md`
- Copilot workspace instructions: `.github/copilot-instructions.md`

Use that skill in Codex/Copilot-style sessions when you want to ask questions like:

```text
Estimate fair market value for MSFT based on the local research data.
What are the biggest risks in the latest 10-K?
Draft a bear case using the filings and metrics.
```

Both integrations instruct the agent to run only deterministic CLI commands as needed, then read local artifacts and cite them in the conversation.

For Codex global discovery, copy or sync the skill folder to:

```text
%USERPROFILE%\.codex\skills\value-investing-research
```

## Provider Limitations

- V1 targets US-listed public companies.
- SEC filings and SEC XBRL facts are canonical.
- Historical prices are imported from Yahoo by default; Stooq is optional with `STOOQ_API_KEY`.
- Provider data may be delayed, incomplete, restated, or unavailable.
- The CLI is not a financial advisor, stock picker, broker, tax tool, or trading system.

See [docs/USAGE.md](docs/USAGE.md) for details.
