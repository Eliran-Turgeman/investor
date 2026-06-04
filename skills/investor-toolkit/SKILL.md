---
name: investor-toolkit
description: "Use when Codex or another coding agent needs to work with this repository's local investor toolkit: ingest and normalize US-listed company research data, read local filings/metrics, run deterministic intrinsic valuation from explicit assumptions JSON, compare valuation scenarios, run reverse DCF, or estimate Israeli Section 102 RSU tax. The skill tells the agent to use the deterministic `investor` CLI for data preparation, metrics, valuation outputs, RSU tax estimates, and artifact discovery, then answer by reading and citing local files or command inputs."
---

# Investor Toolkit

The CLI is the deterministic toolkit layer. The agent performs analysis by reading local artifacts and command output.

## Core Boundary

Use `investor` for deterministic operations only:

```powershell
investor research start <TICKER>
investor research ingest <TICKER>
investor research ingest <TICKER> --refresh
investor research metrics <TICKER>
investor assumptions init <TICKER> --model <MODEL> --scenario <SCENARIO> --output <PATH>
investor assumptions validate <PATH>
investor value <TICKER> --assumptions <PATH>
investor value compare <TICKER> --assumptions <PATH> --assumptions <PATH>
investor reverse-dcf <TICKER> --assumptions <PATH>
investor rsu-tax
```

Do not expect or call CLI commands for question answering, memo writing, thesis challenge, assumption selection, or investment recommendations. Valuation commands only calculate deterministic outputs from explicit assumptions JSON; the agent owns judgment and interpretation.

If `investor` is not installed, run from the repo root:

```powershell
python -m investor_toolkit research <command> <TICKER>
python -m investor_toolkit assumptions <command> <args>
python -m investor_toolkit value <TICKER> --assumptions <PATH>
python -m investor_toolkit rsu-tax <args>
```

## Company Research Workflow

1. Normalize the ticker to uppercase.
2. For every live research session on a ticker, refresh local source data before answering unless the user explicitly asks for offline/local-only work:

```powershell
investor research ingest <TICKER> --refresh
```

This existing command is responsible for fetching SEC filing metadata for every SEC filing filed in the last 2 years, downloading or reusing the raw filing documents, extracting or converting filing text, rebuilding the filing index, refreshing SEC company facts, refreshing market data, and recalculating metrics.

3. If network access is unavailable or the user explicitly asks for offline/local-only work, then check for `research/<TICKER>/company.json`. If source data is missing, run:

```powershell
investor research start <TICKER> --offline
```

4. If metrics are missing or stale after offline work, run:

```powershell
investor research metrics <TICKER>
```

5. Read local artifacts directly. Start with:

- `research/<TICKER>/company.json`
- `research/<TICKER>/filings/metadata/filings.json`
- `research/<TICKER>/filings/metadata/submissions.json`
- `research/<TICKER>/filings/raw/**`
- `research/<TICKER>/metrics/metrics.md`
- `research/<TICKER>/metrics/metrics.json`
- `research/<TICKER>/data/financials.json`
- `research/<TICKER>/data/prices.json`
- `research/<TICKER>/extracted/**/business.md`
- `research/<TICKER>/extracted/**/risk-factors.md`
- `research/<TICKER>/extracted/**/mdna.md`
- `research/<TICKER>/extracted/**/notes.md`
- `research/<TICKER>/extracted/**/document.md`
- `research/<TICKER>/index/filing_chunks.jsonl`

Use `rg` over `research/<TICKER>/extracted` for targeted evidence. Use `metrics.json` for numbers that need calculation or comparison.

## Valuation Workflow

When a user asks for valuation:

1. Normalize the ticker to uppercase.
2. Ensure local data and metrics exist using the company research workflow.
3. Write assumptions to a JSON file before valuation:

```powershell
investor assumptions init <TICKER> --model fcff-dcf --scenario base --output assumptions/<TICKER>.base.json
```

4. Fill every `null` judgment field from local history and explicit agent/user assumptions.
5. Validate before running:

```powershell
investor assumptions validate assumptions/<TICKER>.base.json
```

6. Run valuation and cite deterministic output:

```powershell
investor value <TICKER> --assumptions assumptions/<TICKER>.base.json --include-sensitivity --format json --output valuations/<TICKER>.base.result.json
```

Rules:

- Never invent valuation outputs; use CLI result JSON as source of truth.
- Separate source facts, assumptions, deterministic calculations, and judgment.
- Prefer conservative/base/aggressive scenario ranges for serious valuation requests.
- Explain which assumptions drive the result and flag high sensitivity.
- Use `reverse-dcf` to analyze what the current market price implies.
- Never provide direct buy/sell instructions.

## RSU Tax Workflow

For Israeli Section 102 RSU tax estimate requests, use the toolkit command:

```powershell
investor rsu-tax --ticker <TICKER> --grant-date <YYYY-MM-DD> --shares <N> --ordinary-tax-rate <RATE>
```

For a human-led terminal session, `investor rsu-tax` with no flags prompts for ticker, grant date, share count, and marginal tax rate. In agent/scripted use, pass those flags explicitly. The calculator fetches market prices and USD/ILS FX when possible; use `--grant-price-usd`, `--sale-price-usd`, or `--fx-usd-ils` when the user wants an override or a provider is unavailable. It infers the Section 102 2-year scenario from the grant date unless `--qualified-102` or `--early-sale` is supplied. Treat output as an estimate, not tax advice, and cite command inputs/output in the answer.

## SEC User Agent

Live SEC commands require a descriptive user agent. If missing, ask for or suggest a non-personal contact string:

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
```

Do not require the user's personal email.

## Answering Standards

For user-facing analysis, separate the agent's reasoning from source facts:

```markdown
## Answer

## Evidence

## Interpretation

## Open Questions
```

Rules:

- Treat SEC filings and deterministic metrics as primary evidence.
- Cite local filing section paths, metrics files, or data files for material claims.
- Never invent missing financial numbers.
- Say when local data is missing, stale, ambiguous, restated, or provider-dependent.
- Avoid direct buy/sell instructions and short-term price predictions.
- For fair-value work, write assumptions JSON first, validate it, then cite deterministic CLI valuation output.
- For RSU tax work, cite the command inputs and label the output as an estimate.

## Agent-Owned Outputs

If the user asks for a memo, thesis log, valuation, or research questions, create or update those as agent-owned files such as:

- `research/<TICKER>/memo.md`
- `research/<TICKER>/questions.md`
- `research/<TICKER>/thesis-log.md`
- `research/<TICKER>/valuation.md`

Do not describe those as CLI-generated artifacts.
