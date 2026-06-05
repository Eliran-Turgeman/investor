# Investor Toolkit Usage Guide

The `investor` CLI is the deterministic toolkit layer. It ingests and normalizes company research data, calculates local metrics, runs explicit-assumption intrinsic valuation models, and estimates Israeli Section 102 RSU taxes. It does not answer investment questions or generate investment analysis.

## Install

From the latest GitHub release. This installs both the CLI and the global Codex skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 | iex"
```

CLI-only opt-out:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Join-Path $env:TEMP 'investor-install.ps1'; irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 -OutFile $p; & $p -SkipCodexSkill"
```

From a source checkout:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
investor --help
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

SEC is canonical for filings, company identity, filing metadata, and XBRL facts. Yahoo historical prices are used by default as a convenience source; Stooq can be used when `STOOQ_API_KEY` is set. USD/ILS FX for `investor rsu-tax` uses ExchangeRate-API's open endpoint unless manually overridden.

## Command Reference

Current command surface:

```text
investor quickstart
investor research start
investor research ingest
investor research metrics
investor assumptions init
investor assumptions validate
investor value
investor value compare
investor reverse-dcf
investor rsu-tax
```

There are intentionally no CLI commands for `ask`, `memo`, `challenge`, or investment recommendations. Valuation commands calculate from explicit assumptions; the agent/user owns the assumptions and interpretation.

### `investor quickstart <ticker>`

Runs the first useful company-research path and prints agent-ready next steps.

```powershell
investor quickstart MSFT
investor quickstart MSFT --offline
investor quickstart MSFT --refresh
```

Expected actions:

- Require `SEC_USER_AGENT` for online runs before provider requests.
- Create the local ticker workspace.
- Run the same online ingestion path as `investor research start <ticker>` unless `--offline` is supplied.
- Print key artifact paths and copy-ready agent prompts.

Use this for a friend's first run or anytime you want a guided setup for one ticker.

### `investor research start <ticker>`

Creates a local data workspace and, unless `--offline`, runs initial ingestion.

```powershell
investor research start MSFT
investor research start MSFT --offline
```

Expected actions:

- Resolve ticker and company identity when online.
- Create `research/<TICKER>/`.
- Fetch recent SEC filing metadata and raw filings when online.
- Fetch SEC company facts and historical prices when online.
- Extract filing sections.
- Build `index/filing_chunks.jsonl`.
- Calculate deterministic metrics.

Use this for a first run on a ticker. Online `start` writes `company.json` and delegates to the same ingestion pipeline used by `investor research ingest`.

### `investor research ingest <ticker>`

Refreshes source data and derived local data.

```powershell
investor research ingest MSFT
investor research ingest MSFT --refresh
investor research ingest MSFT --offline
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

### `investor research metrics <ticker>`

Recalculates deterministic metrics from normalized local data.

```powershell
investor research metrics MSFT
investor research metrics MSFT --offline
```

Expected outputs:

```text
research/MSFT/metrics/metrics.json
research/MSFT/metrics/metrics.md
```

Metrics cover growth, margins, cash generation, balance sheet quality, capital allocation, returns, and valuation support where source data is available.

### `investor assumptions init <ticker>`

Creates a valuation assumptions JSON template. The template is schema-valid and prefilled with deterministic local values where available, while judgment fields remain `null`.

```powershell
investor assumptions init MSFT `
  --model fcff-dcf `
  --scenario base `
  --output assumptions/MSFT.base.json
```

Supported models are `fcff-dcf`, `owner-earnings-dcf`, `reverse-dcf`, `epv`, and `multiples`.

### `investor assumptions validate <path>`

Validates an assumptions JSON file and any `use_latest` local-data references.

```powershell
investor assumptions validate assumptions/MSFT.base.json
```

Validation fails on missing required assumptions, invalid numeric ranges, ticker mismatch during valuation, and terminal growth greater than or equal to the discount rate. Warnings flag assumptions that may be aggressive relative to common valuation guardrails or local history.

### `investor value <ticker>`

Runs a deterministic valuation from an assumptions file.

```powershell
investor value MSFT `
  --assumptions assumptions/MSFT.base.json `
  --include-sensitivity `
  --format json `
  --output valuations/MSFT.base.result.json
```

Options:

- `--format text|json|markdown`
- `--output <path>`
- `--include-sensitivity` for FCFF/reverse DCF sensitivity tables
- `--include-debug` for projected-year details
- `--export-agent-context` to write `context/valuations/<TICKER>.<SCENARIO>.*`

When SEC `company_facts.json` contains newer quarterly data than `financials.json`, valuation uses a latest twelve-month base for revenue, cash flow, and related metrics, plus latest available quarterly cash, debt, and diluted shares.

### `investor value compare <ticker>`

Compares multiple scenarios.

```powershell
investor value compare MSFT `
  --assumptions assumptions/MSFT.conservative.json `
  --assumptions assumptions/MSFT.base.json `
  --assumptions assumptions/MSFT.aggressive.json `
  --format markdown `
  --output valuations/MSFT.comparison.md
```

### `investor reverse-dcf <ticker>`

Alias for running a `reverse-dcf` assumptions file. Reverse DCF solves one unknown at a time: `revenueGrowthYears1To5`, `targetOperatingMargin`, `terminalGrowthRate`, or `discountRate`.

### `investor rsu-tax`

Estimates Israeli Section 102 trustee capital-gains-track RSU taxation from user-supplied inputs.

Interactive use:

```powershell
investor rsu-tax
```

The prompt asks for ticker, grant date, shares, and marginal ordinary tax rate. It then fetches the grant baseline, sale price, and USD/ILS FX automatically.

Non-interactive use:

```powershell
investor rsu-tax `
  --ticker MSFT `
  --grant-date 2022-05-30 `
  --shares 100 `
  --ordinary-tax-rate 47
```

Primary inputs:

- `--ticker`: stock ticker used to fetch grant and sale prices
- `--grant-date`: grant date as `YYYY-MM-DD`
- `--shares`
- `--ordinary-tax-rate`: marginal ordinary-income tax rate as `0.47` or `47`

Optional inputs:

- `--grant-price-usd`: manual grant baseline override
- `--sale-price-usd`: manual sale price override
- `--fx-usd-ils`: manual USD/ILS override and fallback
- `--sale-fees-ils`, default `0`
- `--capital-gain-offset-ils`, default `0`
- `--salary-ytd-ils`, optional annual salary/YTD income used to estimate employee National Insurance + health contributions
- `--qualified-102`, force qualified Section 102 output
- `--early-sale`, force early/non-compliant output

Default behavior:

- Grant baseline is the 30-calendar-day average market close ending on `--grant-date`.
- Sale price is the latest available market close as of today.
- USD/ILS FX is fetched from ExchangeRate-API's open endpoint unless `--fx-usd-ils` is supplied.
- Eligibility is inferred from `grant date + 2 years <= today`.
- Only the selected/inferred scenario is shown. Manual-only legacy input without a grant date still shows both scenarios for comparison.

Output:

- selected scenario label
- fetched or overridden input sources
- ordinary-income component, capital-gain component, estimated taxes, net proceeds, and effective tax rate

The calculator is an estimate only, not tax advice.

Tax references used for the current constants/model:

- Baker McKenzie Global Equity Matrix, Israel RS/RSU.
- Israel National Insurance Institute salaried-worker rates and thresholds, effective January 1, 2026.
- PwC Israel individual tax summary for general personal-income tax context.
- ExchangeRate-API open endpoint documentation for no-key latest FX rates.

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
assumptions/
  MSFT.base.json
valuations/
  MSFT.base.result.json
context/
  valuations/
    MSFT.base.md
```

Artifact ownership:

- CLI-owned: `company.json`, `filings/`, `extracted/`, `data/`, `metrics/`, `index/`.
- CLI-owned valuation outputs: assumptions templates, validation output, valuation result files, scenario comparisons, and exported agent context.
- Agent/user-owned: `memo.md`, `questions.md`, thesis logs, final valuation interpretation, and any other recommendation or judgment files.

The CLI will not create or overwrite agent/user-owned analysis files.

## Agent Workflow

Use `skills/investor-toolkit/SKILL.md` in a Codex session, or rely on `.github/copilot-instructions.md` in Copilot. The agent should run the CLI only for deterministic data preparation or RSU tax estimates, then read local artifacts and command output directly.

Example user prompt:

```text
Use the investor-toolkit skill. Estimate fair market value for MSFT from the local research data.
Use the investor-toolkit skill. Estimate Israeli Section 102 RSU tax for 336 MSFT shares granted on 2022-05-30.
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
investor research ingest MSFT --offline
investor research metrics MSFT --offline
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
- ExchangeRate-API open FX data updates once per day and requires attribution under its open endpoint terms.

Product scope:

- V1 includes US-listed public-company research data and Israeli Section 102 RSU tax estimates.
- V1 includes deterministic valuation calculations from explicit assumptions, but does not choose those assumptions or provide buy/sell recommendations.
- V1 does not provide question answering, memo writing, real-time data, broker integration, automated trading, tax advice, or portfolio management.
- `investor rsu-tax` is a deterministic estimate, not a filing-grade Israeli tax engine.
