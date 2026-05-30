# Usage Guide

The `research` CLI is the deterministic data layer. It ingests, normalizes, extracts, caches, indexes, and calculates. It does not answer investment questions or generate investment analysis.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
research --help
```

## Configure Providers

SEC requests require a descriptive user agent:

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
```

Optional settings:

```powershell
$env:RESEARCH_HOME = ".\research"
$env:STOOQ_API_KEY = "..."
```

SEC is canonical for filings, company identity, filing metadata, and XBRL facts. Yahoo historical prices are used by default as a convenience source; Stooq can be used when `STOOQ_API_KEY` is set.

## Command Reference

Current command surface:

```text
research start
research ingest
research metrics
```

There are intentionally no CLI commands for `ask`, `memo`, `challenge`, `valuation`, or `context`. Those are agent/user responsibilities over local data.

### `research start <ticker>`

Creates a local data workspace and, unless `--offline`, runs initial ingestion.

```powershell
research start MSFT
research start MSFT --offline
```

Expected actions:

- Resolve ticker and company identity when online.
- Create `research/<TICKER>/`.
- Fetch recent SEC filing metadata and raw filings when online.
- Fetch SEC company facts and historical prices when online.
- Extract filing sections.
- Build `index/filing_chunks.jsonl`.
- Calculate deterministic metrics.

Use this for a first run on a ticker. Online `start` writes `company.json` and delegates to the same ingestion pipeline used by `research ingest`.

### `research ingest <ticker>`

Refreshes source data and derived local data.

```powershell
research ingest MSFT
research ingest MSFT --refresh
research ingest MSFT --offline
```

Expected actions:

- Check for new or missing filings.
- Download missing raw filings.
- Refresh or reuse cached provider responses.
- Rebuild normalized financial data.
- Re-extract filing sections.
- Rebuild the local chunk index.
- Recalculate metrics.

Use this for existing tickers when you want fresh filings/provider data or want to rebuild derived artifacts from cached/local data.

### `research metrics <ticker>`

Recalculates deterministic metrics from normalized local data.

```powershell
research metrics MSFT
research metrics MSFT --offline
```

Expected outputs:

```text
research/MSFT/metrics/metrics.json
research/MSFT/metrics/metrics.md
```

Metrics cover growth, margins, cash generation, balance sheet quality, capital allocation, returns, and valuation support where source data is available.

## Local Artifact Layout

```text
research/
  MSFT/
    company.json
    filings/
      raw/
        2025-10K.html
      metadata/
        filings.json
        submissions.json
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

Artifact ownership:

- CLI-owned: `company.json`, `filings/`, `extracted/`, `data/`, `metrics/`, `index/`.
- Agent/user-owned: `memo.md`, `questions.md`, valuation notes, thesis logs, and any other interpretation files.

The CLI will not create or overwrite agent/user-owned analysis files.

## Agent Workflow

Use `skills/value-investing-research/SKILL.md` in a Codex session, or rely on `.github/copilot-instructions.md` in Copilot. The agent should run the CLI only for deterministic data preparation, then read local artifacts directly.

Example user prompt:

```text
Use the value-investing-research skill. Estimate fair market value for MSFT from the local research data.
```

The agent should cite local files such as:

- `research/MSFT/metrics/metrics.md`
- `research/MSFT/extracted/**/business.md`
- `research/MSFT/extracted/**/risk-factors.md`
- `research/MSFT/extracted/**/mdna.md`
- `research/MSFT/data/financials.json`
- `research/MSFT/index/filing_chunks.jsonl`

## Offline Mode

`--offline` avoids provider calls and uses only local files/caches:

```powershell
research ingest MSFT --offline
research metrics MSFT --offline
```

If required local inputs are missing, the command should fail clearly or produce empty deterministic outputs rather than invent data.

## Provider Limitations

SEC:

- Canonical for filings and XBRL company facts.
- Requires respectful rate limiting and a descriptive `SEC_USER_AGENT`.
- Filings may have inconsistent HTML, section labels, units, and restatements.

Market data:

- Yahoo or Stooq data may be delayed, throttled, unavailable, or inconsistent with paid providers.
- Market data is a convenience layer and should not override SEC filing facts.

Product scope:

- V1 is for US-listed public-company research.
- V1 does not provide question answering, fair-value estimates, memo writing, real-time data, broker integration, automated trading, tax advice, portfolio management, or buy/sell recommendations.
