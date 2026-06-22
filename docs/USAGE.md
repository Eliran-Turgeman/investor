# Investor Toolkit Usage Guide

The `investor` CLI is the deterministic toolkit layer. It ingests and normalizes company research data, calculates local metrics, runs explicit-assumption intrinsic valuation models, exports portfolio workbooks, builds rule-based portfolio diagnostics, and estimates Israeli Section 102 RSU taxes. It does not answer investment questions or generate investment analysis.

## Install

From the latest GitHub release. This installs the CLI, the global Codex skill, and the local Codex MCP server registration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 | iex"
```

CLI-only opt-out:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Join-Path $env:TEMP 'investor-install.ps1'; irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 -OutFile $p; & $p -SkipCodexSkill"
```

Codex skill without MCP registration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Join-Path $env:TEMP 'investor-install.ps1'; irm https://github.com/Eliran-Turgeman/investor/releases/latest/download/install.ps1 -OutFile $p; & $p -SkipCodexMcp"
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
investor onboarding init
investor portfolio init
investor portfolio import
investor portfolio export
investor portfolio value
investor portfolio signals
investor portfolio refresh
investor discovery discover
investor discovery refresh
investor discovery score
investor discovery brief
investor discovery reject
investor discovery defer
investor discovery propose-promotions
investor discovery promote
investor discovery review-watchlist
investor agents run
investor rsu-tax
investor-mcp
```

There are intentionally no CLI commands for `ask`, `memo`, `challenge`, or investment recommendations. Valuation and portfolio commands calculate from explicit inputs and rules; the agent/user owns the assumptions and interpretation.

## MCP Server

`investor-mcp` runs a local stdio MCP server for MCP-capable assistants and IDEs:

```powershell
investor-mcp --workspace-root .
```

Optional path settings:

```powershell
investor-mcp `
  --workspace-root . `
  --research-root .\research `
  --portfolio-dir .\portfolio `
  --assumptions-dir .\assumptions `
  --valuations-dir .\valuations
```

The MCP server is a thin adapter over the same application services used by the CLI. It exposes:

- tools for investor profile status/onboarding, portfolio context, company artifact discovery, research refresh, assumptions initialization and validation, valuation, scenario comparison, portfolio valuation, and portfolio signals
- resources for local investor profile, portfolio, and company research artifacts
- prompts for investor onboarding, portfolio review, company deep dive, thesis challenge, and candidate briefs

Tool outputs use a stable operation envelope with `schemaVersion`, `operation`, `status`, `generatedAt`, `data`, `warnings`, `errors`, `sourcePaths`, `artifacts`, and `nextActions`.

The MCP server does not provide investment recommendations or broker/trading actions. It exposes deterministic data and calculations for an assistant to interpret.

Profile gating:

- `get_profile_status` reports `onboardingRequired`, existing profile artifacts, and missing profile artifacts.
- `investor://profile/status` is a virtual resource that exists even before onboarding files are written.
- `get_portfolio_context` includes `profileStatus` and returns onboarding next actions when profile artifacts are missing.
- MCP prompt `investor_onboarding` tells assistants to ask only broad questions, call `init_investor_profile`, and avoid a long questionnaire.

`scripts/setup.ps1` registers this server in `%USERPROFILE%\.codex\config.toml` by default with `SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"` so online SEC refresh tools can run after Codex is restarted.

### `investor onboarding init`

Creates starter investor profile and policy artifacts without a long questionnaire.

```powershell
investor onboarding init
```

Optional broad overrides:

```powershell
investor onboarding init `
  --focus software `
  --focus ai_related_hardware_or_hardware_adjacent_businesses `
  --external-exposure MSFT:50000:USD:RSU `
  --external-exposure PANW:75000:USD:RSU `
  --other-portfolio index_portfolio:250000:NIS
```

Expected outputs in `portfolio/`:

```text
investor_policy.md
goals.json
preferences.json
position_sizing.json
valuation_policy.json
risk_policy.json
decision_process.json
operating_preferences.json
external_exposure.json
onboarding_notes.md
thesis_template.md
bear_case_template.md
theses/README.md
rejected/README.md
decisions/README.md
```

By default, existing profile files are not overwritten. Use `--overwrite` only when you intentionally want to regenerate them. The command writes broad defaults such as long-term horizon, valuation discipline, high-signal monthly briefs, and short-brief-first research depth. It does not provide investment recommendations.

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

### `investor portfolio init`

Creates a local portfolio workbook and JSON templates.

```powershell
investor portfolio init --output portfolio/portfolio.xlsx
```

Expected outputs:

```text
portfolio/portfolio.xlsx
portfolio/holdings.json
portfolio/watchlist.json
portfolio/assumption_overrides.json
portfolio/rules.json
```

The workbook contains editable `Holdings`, `Watchlist`, and `Assumptions` sheets plus generated `Valuations`, `Signals`, `Portfolio`, `Data Quality`, and `Audit` sheets.

### `investor portfolio import`

Imports user-edited workbook inputs into normalized JSON.

```powershell
investor portfolio import --workbook portfolio/portfolio.xlsx
```

Use this after editing holdings, watchlist rows, user fair values, or assumption paths in Excel. The import normalizes tickers, parses percentages like `25%` as `0.25`, rejects out-of-range allocation and margin rates, and treats `Result Path` as generated output rather than user input.

### `investor portfolio value`

Runs deterministic valuations for portfolio tickers using existing assumptions files.

```powershell
investor portfolio value `
  --portfolio-dir portfolio `
  --assumptions-dir assumptions `
  --valuations-dir valuations
```

The command discovers `assumptions/<TICKER>.<SCENARIO>.json` plus any workbook-imported assumption paths, runs `investor value` logic in-process, and writes model-qualified files such as `valuations/<TICKER>.<SCENARIO>.<MODEL>.result.json`. It does not choose assumptions.

### `investor portfolio signals`

Builds deterministic signal JSON from holdings/watchlist inputs, valuation result files, user fair values, local prices, metrics, and `portfolio/rules.json`.

```powershell
investor portfolio signals `
  --assumptions-dir assumptions `
  --valuations-dir valuations `
  --workbook portfolio/portfolio.xlsx
```

Expected output:

```text
portfolio/signals.json
portfolio/portfolio.xlsx
```

Signals use labels such as `Strong opportunity`, `Opportunity`, `Watch`, `Fairly valued`, `Review`, `Review: above range`, and `No decision`. They are rule-based diagnostics, not buy/sell/hold recommendations. Data-quality failures such as stale prices or missing fair value produce `No decision`. If a portfolio-generated valuation result is older than its source assumptions file, the signal includes a freshness warning.

### `investor portfolio export`

Regenerates the workbook from portfolio JSON, valuation outputs, and signals.

```powershell
investor portfolio export `
  --assumptions-dir assumptions `
  --valuations-dir valuations `
  --workbook portfolio/portfolio.xlsx
```

### `investor portfolio refresh`

Refreshes local research for portfolio tickers, runs available valuations, writes signals, and exports the workbook.

```powershell
investor portfolio refresh --workbook portfolio/portfolio.xlsx
investor portfolio refresh --offline --workbook portfolio/portfolio.xlsx
```

Online refresh requires `SEC_USER_AGENT`. Offline refresh uses only local cached data and should be used when network access is unavailable.

### `investor discovery`

Runs the automated stock discovery and triage harness. The harness owns candidate queue state, ranked opportunities, briefs, rejection/defer records, and watchlist promotion proposals. It does not mutate holdings and does not add anything to `watchlist.json` without explicit `promote --approved`.

```powershell
investor discovery discover --ticker MSFT --ticker PANW --no-default-screens
investor discovery discover --source-file portfolio/my_screen.json
investor discovery refresh MSFT --offline
investor discovery score MSFT
investor discovery brief MSFT
investor discovery reject MSFT --reason "Outside circle of competence."
investor discovery defer MSFT --reason "Need fresh valuation output."
investor discovery propose-promotions
investor discovery promote MSFT --approved
investor discovery review-watchlist --offline
```

Expected outputs:

```text
portfolio/candidates.json
portfolio/top_opportunities.json
portfolio/candidate_briefs/<TICKER>.md
portfolio/rejected/<TICKER>.md
portfolio/discovery_runs/<RUN_ID>.json
```

Every ranked candidate carries source facts, deterministic calculations, component scores, key risks, missing evidence, next action, artifact paths or MCP-style URIs, and a watchlist promotion rationale. Missing data, stale prices, failed refreshes, invalid assumptions, and stale valuation outputs are explicit warnings.

### `investor agents run`

Runs the LLM-backed multi-agent discovery and research harness. This is the AI layer: it uses role agents to analyze persisted local evidence and writes auditable reviews, briefs, proposed states, and token usage.

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
$env:OPENAI_API_KEY = "..."
investor agents run --provider openai --refresh-research --limit 5
```

Explicit tickers:

```powershell
investor agents run --provider openai --ticker MSFT --ticker PANW --refresh-research --limit 2
```

No-token dry run:

```powershell
investor agents run --provider dry-run --ticker MSFT --no-default-screens
```

Expected outputs:

```text
portfolio/audit.db
portfolio/agent_runs/<RUN_ID>.json
portfolio/agent_reviews/<TICKER>.json
portfolio/agent_briefs/<TICKER>.md
portfolio/candidates.json
```

The command uses five LLM calls per candidate in the first implementation: four role agents and one committee chair. Use `--limit`, `--ticker`, and `--max-context-chars` to control token spend. Agent triage is persisted as `agentSuggestedState`; the workflow state becomes `agent_reviewed` until an analyst records `analyst_approved`, `analyst_rejected`, or `needs_more_evidence`. The deprecated `--apply-agent-states` flag is ignored. Agents never mutate holdings or watchlist entries.

Verify persisted agent claims:

```powershell
investor agents verify-claims MSFT
```

Record analyst review:

```powershell
investor agents approve MSFT --state analyst_approved --reason "Ready for explicit watchlist-promotion review."
investor agents approve MSFT --state analyst_rejected --reason "Outside circle of competence."
investor agents approve MSFT --state needs_more_evidence --reason "Need fresh valuation output."
```

`analyst_approved` requires an existing agent review, existing agent brief, and clean claim verification. `investor discovery promote <TICKER> --approved` also requires current `analyst_approved` state and matching approval source hashes.

Vendor-drop imports use normalized CSV or Parquet contracts:

```powershell
investor data import --kind fundamentals --path vendor.csv --provider ExampleVendor
investor data import --kind prices --path prices.csv --provider ExampleVendor --max-price-age-days 5 --block-stale-prices
```

Import rows must include a `provider` column matching `--provider`. Prices require `adjustment`; fundamentals and estimates require `unit`; duplicate primary keys, invalid currencies, invalid periods, and invalid dates block the import.

Local eval suites use analyst-labeled JSONL files:

```powershell
investor eval run --suite gold_candidates
```

Verify the audit ledger hash chains and append-only triggers:

```powershell
investor audit verify
investor audit verify --path portfolio/audit.db
```

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
portfolio/
  investor_policy.md
  goals.json
  preferences.json
  position_sizing.json
  valuation_policy.json
  risk_policy.json
  decision_process.json
  operating_preferences.json
  external_exposure.json
  onboarding_notes.md
  thesis_template.md
  bear_case_template.md
  theses/
    README.md
  rejected/
    README.md
  decisions/
    README.md
  portfolio.xlsx
  holdings.json
  watchlist.json
  assumption_overrides.json
  rules.json
  signals.json
  valuation_audit.json
context/
  valuations/
    MSFT.base.md
```

Artifact ownership:

- CLI-owned: `company.json`, `filings/`, `extracted/`, `data/`, `metrics/`, `index/`.
- CLI-owned valuation outputs: assumptions templates, validation output, valuation result files, scenario comparisons, and exported agent context.
- CLI-owned portfolio outputs: normalized imported JSON, signal JSON, valuation audit JSON, and regenerated workbook exports.
- Agent/user-owned: `memo.md`, `questions.md`, thesis logs, final valuation interpretation, and any other recommendation or judgment files.

The CLI will not create or overwrite agent/user-owned analysis files.

## Agent Workflow

Use `skills/investor-toolkit/SKILL.md` in a Codex session, or rely on `.github/copilot-instructions.md` in Copilot. The agent should run the CLI only for deterministic data preparation or RSU tax estimates, then read local artifacts and command output directly.

Example user prompt:

```text
Use the investor-toolkit skill. Estimate fair market value for MSFT from the local research data.
Use the investor-toolkit skill. Estimate Israeli Section 102 RSU tax for 336 MSFT shares granted on 2022-05-30.
Use the investor-toolkit skill. Import my portfolio workbook, run available valuations, then summarize No decision signals with source paths.
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
- V1 includes deterministic portfolio workbook export/import and rule-based portfolio diagnostics, but does not provide broker integration, automated trading, tax advice, or buy/sell/hold recommendations.
- V1 does not provide question answering, memo writing, or real-time data.
- `investor rsu-tax` is a deterministic estimate, not a filing-grade Israeli tax engine.
