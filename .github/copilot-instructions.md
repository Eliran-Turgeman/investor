# Investor Toolkit Workspace Instructions

This repository separates deterministic investor data/calculation work from agent analysis.

## Boundary

Use the `investor` CLI only as a deterministic data/calculation provider:

```powershell
investor quickstart <TICKER>
investor research start <TICKER>
investor research ingest <TICKER>
investor research metrics <TICKER>
investor assumptions init <TICKER> --model <MODEL> --scenario <SCENARIO> --output <PATH>
investor assumptions validate <PATH>
investor value <TICKER> --assumptions <PATH>
investor value compare <TICKER> --assumptions <PATH> --assumptions <PATH>
investor reverse-dcf <TICKER> --assumptions <PATH>
investor portfolio init --output <PATH>
investor portfolio import --workbook <PATH>
investor portfolio value
investor portfolio signals --workbook <PATH>
investor portfolio refresh --offline --workbook <PATH>
investor rsu-tax
```

Do not expect CLI commands for question answering, memo writing, thesis challenge, assumption selection, or recommendations. Valuation commands only calculate deterministic outputs from explicit assumptions JSON; the agent owns judgment and interpretation.

If the console script is not installed, run:

```powershell
python -m investor_toolkit research <command> <TICKER>
python -m investor_toolkit assumptions <command> <args>
python -m investor_toolkit value <TICKER> --assumptions <PATH>
python -m investor_toolkit rsu-tax <args>
```

## Workflow For Company Analysis

1. Normalize the ticker to uppercase.
2. For first-time setup, `investor quickstart <TICKER>` is acceptable.
3. Check whether `research/<TICKER>/company.json` exists.
4. If source data is missing, run `investor research start <TICKER>`.
5. If data may be stale, run `investor research ingest <TICKER>`.
6. If metrics are missing or stale, run `investor research metrics <TICKER>`.
7. Read local artifacts directly and cite them in answers.

Start with:

- `research/<TICKER>/company.json`
- `research/<TICKER>/filings/metadata/filings.json`
- `research/<TICKER>/metrics/metrics.md`
- `research/<TICKER>/metrics/metrics.json`
- `research/<TICKER>/data/financials.json`
- `research/<TICKER>/data/prices.json`
- `research/<TICKER>/extracted/**/business.md`
- `research/<TICKER>/extracted/**/risk-factors.md`
- `research/<TICKER>/extracted/**/mdna.md`
- `research/<TICKER>/extracted/**/notes.md`
- `research/<TICKER>/index/filing_chunks.jsonl`

Use `rg` over `research/<TICKER>/extracted` for targeted filing evidence.

## Workflow For Intrinsic Valuation

1. Ensure local research data and metrics exist.
2. Always write assumptions into JSON before valuation.
3. Validate assumptions before running valuation.
4. Run `investor value` and use result JSON as source of truth.
5. Prefer conservative/base/aggressive ranges for serious valuation requests.
6. Use `investor reverse-dcf` to analyze what the current market price implies.
7. Separate facts, assumptions, deterministic calculations, and judgment.
8. Never provide direct buy/sell instructions.

Example:

```powershell
investor assumptions init MSFT --model fcff-dcf --scenario base --output assumptions/MSFT.base.json
investor assumptions validate assumptions/MSFT.base.json
investor value MSFT --assumptions assumptions/MSFT.base.json --include-sensitivity --format json --output valuations/MSFT.base.result.json
```

## Workflow For Portfolio Diagnostics

Use portfolio commands for deterministic workbook and signal workflows:

```powershell
investor portfolio init --output portfolio/portfolio.xlsx
investor portfolio import --workbook portfolio/portfolio.xlsx
investor portfolio value
investor portfolio signals --workbook portfolio/portfolio.xlsx
```

The workbook is the user-facing editing surface for holdings, watchlist rows, assumption paths, and user fair values. Import after Excel edits before recalculating. `portfolio value` runs existing valuation logic only for assumptions files that already exist; it does not choose assumptions. Signals are rule-based diagnostics such as `Opportunity`, `Watch`, `Review`, or `No decision`; never present them as direct buy/sell/hold instructions.

Read portfolio artifacts directly when explaining results:

- `portfolio/holdings.json`
- `portfolio/watchlist.json`
- `portfolio/assumption_overrides.json`
- `portfolio/rules.json`
- `portfolio/signals.json`
- `portfolio/valuation_audit.json`
- `portfolio/portfolio.xlsx`

## Workflow For Israeli RSU Tax Estimates

Use `investor rsu-tax` for deterministic Israeli Section 102 RSU estimates:

```powershell
investor rsu-tax --ticker <TICKER> --grant-date <YYYY-MM-DD> --shares <N> --ordinary-tax-rate <RATE>
```

For a human-led terminal session, `investor rsu-tax` with no flags prompts for ticker, grant date, share count, and marginal tax rate. In agent/scripted use, pass those flags explicitly. The calculator fetches market prices and USD/ILS FX when possible; use `--grant-price-usd`, `--sale-price-usd`, or `--fx-usd-ils` for overrides or provider fallback. It infers the Section 102 2-year scenario from the grant date unless `--qualified-102` or `--early-sale` is supplied. Treat output as an estimate, not tax advice.

## Answering Standards

- Treat SEC filings and deterministic metrics as primary evidence.
- Cite local filing section paths, metrics files, or data files for material claims.
- Never invent missing financial numbers.
- Say when data is missing, stale, ambiguous, restated, or provider-dependent.
- Avoid direct buy/sell instructions and short-term price predictions.
- For fair-value work, write assumptions JSON first, validate it, then cite deterministic CLI valuation output.
- For RSU tax work, cite the command inputs and label output as an estimate.

## Agent-Owned Outputs

If asked to create analysis artifacts, write them as agent-owned files such as:

- `research/<TICKER>/memo.md`
- `research/<TICKER>/questions.md`
- `research/<TICKER>/thesis-log.md`
- `research/<TICKER>/valuation.md`

Do not describe those as CLI-generated artifacts.
