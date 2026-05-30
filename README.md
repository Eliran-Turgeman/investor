# Investor Toolkit CLI

This repository separates deterministic data work from agent analysis.

The `investor` CLI is a local-first toolkit for deterministic investor workflows. The first toolkit area is stock research data ingestion. The second is an Israeli Section 102 RSU tax estimator.

The CLI does not answer investment questions, generate memos, challenge theses, or estimate fair value. Use the repo-local Codex skill or Copilot instructions for that agent experience.

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
investor --help
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
investor research start MSFT
investor research ingest MSFT
investor research metrics MSFT
investor rsu-tax --ticker MSFT --grant-date 2022-05-30 --shares 100 --ordinary-tax-rate 47
```

| Command | Purpose |
| --- | --- |
| `investor research start <ticker>` | Create the local data folder and, unless `--offline`, run initial ingestion. |
| `investor research ingest <ticker>` | Refresh filings, provider responses, normalized data, extracted sections, metrics, and chunk index. |
| `investor research metrics <ticker>` | Recalculate deterministic metrics from normalized local data. |
| `investor rsu-tax` | Estimate Israeli Section 102 RSU sale taxes from ticker/date inputs or manual overrides. |

Offline mode is available for local-only rebuilds:

```powershell
investor research start MSFT --offline
investor research ingest MSFT --offline
investor research metrics MSFT --offline
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

## Israeli RSU Tax Estimate

`investor rsu-tax` estimates Israeli Section 102 trustee capital-gains-track RSU taxation for a sale. The easier path is to provide the stock ticker and grant date; the CLI fetches market prices and latest USD/ILS FX, infers whether the grant is past the 2-year holding period, and prints one scenario.

The easiest non-interactive use is:

```powershell
investor rsu-tax `
  --ticker MSFT `
  --grant-date 2022-05-30 `
  --shares 100 `
  --ordinary-tax-rate 47
```

You can also run `investor rsu-tax` with no flags. In an interactive terminal it prompts for ticker, grant date, shares, and marginal ordinary tax rate, then fetches prices and FX automatically.

Optional inputs:

- `--grant-price-usd` to override the 30-calendar-day average grant baseline
- `--sale-price-usd` to override the latest available market close
- `--fx-usd-ils` to override or replace the online USD/ILS FX fetch
- `--sale-fees-ils`
- `--capital-gain-offset-ils`
- `--salary-ytd-ils` to estimate employee National Insurance + health contributions
- `--qualified-102` or `--early-sale` to override the grant-date eligibility inference

This is an estimate only, not tax advice. It assumes Israeli tax residency and Section 102 trustee capital-gains-track treatment.

## Investor Toolkit Agent Integration

The repo includes two local agent integration points:

- Codex skill: `skills/investor-toolkit/SKILL.md`
- Copilot workspace instructions: `.github/copilot-instructions.md`

Use the toolkit skill in Codex/Copilot-style sessions when you want to ask questions like:

```text
Use the investor-toolkit skill. Estimate fair market value for MSFT based on the local research data.
What are the biggest risks in the latest 10-K?
Draft a bear case using the filings and metrics.
Estimate Israeli Section 102 RSU tax for 336 MSFT shares granted on 2022-05-30.
```

Both integrations instruct the agent to run only deterministic CLI commands as needed, then read local artifacts and cite them in the conversation.

For Codex global discovery, copy or sync the skill folder to:

```text
%USERPROFILE%\.codex\skills\investor-toolkit
```

## Provider Limitations

- Company research workflows target US-listed public companies.
- SEC filings and SEC XBRL facts are canonical.
- Historical prices are imported from Yahoo by default; Stooq is optional with `STOOQ_API_KEY`.
- USD/ILS FX is fetched from ExchangeRate-API's open endpoint unless `--fx-usd-ils` is supplied.
- Provider data may be delayed, incomplete, restated, or unavailable.
- The CLI is not a financial advisor, stock picker, broker, filing-grade tax tool, or trading system.
- The RSU calculator is an estimate, not a filing-grade Israeli tax engine.

See [docs/USAGE.md](docs/USAGE.md) for details.
