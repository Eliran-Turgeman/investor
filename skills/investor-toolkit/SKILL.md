---
name: investor-toolkit
description: "Use when Codex or another coding agent needs to work with this repository's local investor toolkit: ingest and normalize US-listed company research data, read local filings/metrics, run deterministic intrinsic valuation from explicit assumptions JSON, compare valuation scenarios, reverse DCF market expectations, build or update a portfolio workbook/watchlist, run portfolio diagnostics, triage discovery candidates, review agent-harness artifacts, import vendor data, run eval/audit checks, or estimate Israeli Section 102 RSU tax. The skill tells the agent to use the deterministic `investor` CLI for data preparation, metrics, valuation outputs, portfolio artifacts/signals, discovery and audit artifacts, RSU tax estimates, and artifact discovery, then answer by reading and citing local files or command inputs."
---

# Investor Toolkit

The CLI is the deterministic toolkit layer. The agent owns interpretation, assumptions, memo writing, and user-facing narrative.

## Core Boundary

Use `investor` for deterministic operations only:

```powershell
investor quickstart <TICKER>
investor research start <TICKER>
investor research ingest <TICKER>
investor research ingest <TICKER> --refresh
investor research metrics <TICKER>
investor assumptions init <TICKER> --model <MODEL> --scenario <SCENARIO> --output <PATH>
investor assumptions validate <PATH>
investor value <TICKER> --assumptions <PATH>
investor value compare <TICKER> --assumptions <PATH> --assumptions <PATH>
investor reverse-dcf <TICKER> --assumptions <PATH>
investor onboarding init
investor portfolio init --output <PATH>
investor portfolio import --workbook <PATH>
investor portfolio value
investor portfolio signals --workbook <PATH>
investor portfolio export --workbook <PATH>
investor portfolio refresh --offline --workbook <PATH>
investor discovery discover --ticker <TICKER>
investor discovery refresh <TICKER> --offline
investor discovery score <TICKER>
investor discovery brief <TICKER>
investor discovery reject <TICKER> --reason <REASON>
investor discovery defer <TICKER> --reason <REASON>
investor discovery propose-promotions
investor discovery promote <TICKER> --approved
investor discovery review-watchlist --offline
investor agents run --provider dry-run --ticker <TICKER>
investor agents verify-claims <TICKER>
investor agents approve <TICKER> --state analyst_approved --reason <REASON>
investor data import --kind fundamentals --path <PATH> --provider <PROVIDER>
investor eval run --suite gold_candidates
investor audit verify
investor rsu-tax
```

Do not expect or call CLI commands for question answering, memo writing, thesis challenge, assumption selection, broker integration, automated trading, or buy/sell recommendations. Discovery and agent-harness commands write auditable proposal artifacts; the agent/user owns judgment.

If `investor` is not installed, run from the repo root:

```powershell
python -m investor_toolkit <command> <args>
```

## Company Research Workflow

1. Normalize tickers to uppercase.
2. For first-time local setup, prefer:

```powershell
investor quickstart MSFT
```

3. For live research on an existing ticker, refresh local source data before answering unless the user asks for offline/local-only work:

```powershell
investor research ingest MSFT --refresh
```

4. If network access is unavailable or the user asks for offline/local-only work, check for `research/<TICKER>/company.json`. If source data is missing, run:

```powershell
investor research start MSFT --offline
```

5. If metrics are missing or stale after offline work, run:

```powershell
investor research metrics MSFT
```

Read local artifacts directly:

- `research/<TICKER>/company.json`
- `research/<TICKER>/filings/metadata/filings.json`
- `research/<TICKER>/filings/metadata/submissions.json`
- `research/<TICKER>/metrics/metrics.md`
- `research/<TICKER>/metrics/metrics.json`
- `research/<TICKER>/data/financials.json`
- `research/<TICKER>/data/prices.json`
- `research/<TICKER>/extracted/**/business.md`
- `research/<TICKER>/extracted/**/risk-factors.md`
- `research/<TICKER>/extracted/**/mdna.md`
- `research/<TICKER>/extracted/**/notes.md`
- `research/<TICKER>/index/filing_chunks.jsonl`

Use `rg` over `research/<TICKER>/extracted` for filing evidence. Use `metrics.json` for calculated numbers.

## Valuation Workflow

When valuation is needed:

1. Ensure local research and metrics exist.
2. Write assumptions to JSON before valuation.
3. Fill judgment fields from local history, explicit user assumptions, and clearly stated agent judgment.
4. Validate the assumptions.
5. Run valuation and cite deterministic result JSON.

```powershell
investor assumptions init MSFT --model fcff-dcf --scenario base --output assumptions/MSFT.base.json
investor assumptions validate assumptions/MSFT.base.json
investor value MSFT --assumptions assumptions/MSFT.base.json --include-sensitivity --format json --output valuations/MSFT.base.result.json
```

Rules:

- Never invent valuation outputs; use CLI result JSON as source of truth.
- Separate source facts, assumptions, deterministic calculations, and judgment.
- Prefer conservative/base/aggressive scenario ranges for serious valuation work.
- Use `reverse-dcf` when the user asks whether valuation looks demanding or expensive.
- Never provide direct buy/sell/hold instructions.

## Portfolio Workflow

Use this workflow when the user wants to build, update, review, or maintain a long-term stock portfolio/watchlist by chatting with the agent.

### Artifact Model

Default portfolio artifacts:

- `portfolio/investor_policy.md` - user profile, investment policy, and assistant behavior guardrails.
- `portfolio/goals.json` - objective, benchmark, horizon, and optimization priorities.
- `portfolio/preferences.json` - style, circle of competence, avoid rules, and challenge preference.
- `portfolio/position_sizing.json` - active portfolio sizing and concentration policy.
- `portfolio/valuation_policy.json` - margin of safety and valuation method policy.
- `portfolio/risk_policy.json` - risk preferences and higher-risk opportunity handling.
- `portfolio/decision_process.json` - candidate evaluation and monthly workflow policy.
- `portfolio/operating_preferences.json` - research cadence and output-depth preferences.
- `portfolio/external_exposure.json` - RSUs and other portfolios tracked separately from active holdings.
- `portfolio/onboarding_notes.md` - onboarding design notes and inferred-default policy.
- `portfolio/thesis_template.md` - reusable agent-owned thesis memo template.
- `portfolio/bear_case_template.md` - reusable agent-owned bear-case memo template.
- `portfolio/theses/README.md` - folder guide for thesis notes.
- `portfolio/rejected/README.md` - folder guide for skipped, deferred, or rejected ideas.
- `portfolio/decisions/README.md` - folder guide for decisions, monthly reviews, and process records.
- `portfolio/portfolio.xlsx` - user-facing workbook.
- `portfolio/holdings.json` - normalized holdings from workbook or agent edits.
- `portfolio/watchlist.json` - normalized watchlist from workbook or agent edits.
- `portfolio/assumption_overrides.json` - workbook-imported assumption paths, user fair values, and required margins.
- `portfolio/rules.json` - deterministic signal thresholds.
- `portfolio/signals.json` - rule-based signal output.
- `portfolio/valuation_audit.json` - valuation run audit.
- `portfolio/audit.db` - hash-chained institutional harness audit ledger.
- `portfolio/candidates.json` - discovery candidate queue.
- `portfolio/top_opportunities.json` - ranked discovery candidates proposed for review.
- `portfolio/candidate_briefs/<TICKER>.md` - discovery candidate brief.
- `portfolio/discovery_runs/<RUN_ID>.json` - append-only discovery run log.
- `portfolio/agent_runs/<RUN_ID>.json` - LLM agent harness run log.
- `portfolio/agent_reviews/<TICKER>.json` - structured per-ticker agent review.
- `portfolio/agent_briefs/<TICKER>.md` - per-ticker agent review brief.
- `portfolio/approvals/<TICKER>.<TIMESTAMP>.json` - analyst approval, rejection, or missing-evidence record.
- `data_imports/<PROVIDER>/<RUN_ID>.json` - vendor import manifest.
- `evals/results/<RUN_ID>.json` - local agent-harness eval result.
- `assumptions/<TICKER>.<SCENARIO>.json` - explicit valuation assumptions.
- `valuations/<TICKER>.<SCENARIO>.<MODEL>.result.json` - portfolio-generated valuation results.

The workbook is useful for manual Excel review. For chat-first workflows, the agent may also update `holdings.json`, `watchlist.json`, and `assumption_overrides.json` directly from explicit user input. Do not invent positions, share counts, cost basis, or user fair values.

Run lightweight onboarding when profile artifacts are missing:

```powershell
investor onboarding init
```

Onboarding should remain simple: use broad defaults, ask only a few high-level questions when needed, and label inferred preferences separately from explicit user answers.

For MCP workflows, call `get_profile_status` before personalized portfolio review or candidate generation. If `onboardingRequired` is true, use the `investor_onboarding` prompt or call `init_investor_profile` after asking only broad questions. The virtual resource `investor://profile/status` is always available. After onboarding, `get_portfolio_context` surfaces existing profile artifacts in `data.profileArtifacts`, `artifacts`, and `sourcePaths`.

### Bootstrap Or Open Portfolio

If no portfolio exists, initialize it:

```powershell
investor portfolio init --output portfolio/portfolio.xlsx
```

If the user edited Excel, import it before recalculating:

```powershell
investor portfolio import --workbook portfolio/portfolio.xlsx
```

If the user gives holdings/watchlist changes in chat, update the JSON artifacts directly using the existing schema, then export the workbook:

```powershell
investor portfolio export --assumptions-dir assumptions --valuations-dir valuations --workbook portfolio/portfolio.xlsx
```

### Add Or Update A Ticker

For each ticker the user adds or asks to review:

1. Refresh or create local research:

```powershell
investor research ingest MSFT --refresh
```

Use `--offline` only when requested or when network is unavailable.

2. Ensure assumptions exist for the required scenarios. For a serious portfolio decision, prefer conservative/base/aggressive:

```powershell
investor assumptions init MSFT --model fcff-dcf --scenario conservative --output assumptions/MSFT.conservative.json
investor assumptions init MSFT --model fcff-dcf --scenario base --output assumptions/MSFT.base.json
investor assumptions init MSFT --model fcff-dcf --scenario aggressive --output assumptions/MSFT.aggressive.json
```

3. Fill all `null` assumptions before valuation. Anchor assumptions in local metrics and filings, and place concise rationale in `metadata.notes`.
4. Validate every assumptions file:

```powershell
investor assumptions validate assumptions/MSFT.base.json
```

5. Run single-ticker valuation if needed, or let the portfolio valuation command run all existing portfolio assumptions.

### Recalculate Portfolio

Run portfolio valuations from existing assumptions:

```powershell
investor portfolio value --portfolio-dir portfolio --assumptions-dir assumptions --valuations-dir valuations --include-sensitivity
```

Then build signals and update the workbook:

```powershell
investor portfolio signals --portfolio-dir portfolio --assumptions-dir assumptions --valuations-dir valuations --workbook portfolio/portfolio.xlsx
```

For a full local refresh:

```powershell
investor portfolio refresh --portfolio-dir portfolio --assumptions-dir assumptions --valuations-dir valuations --workbook portfolio/portfolio.xlsx
```

Online refresh requires `SEC_USER_AGENT`. Use `--offline` when the user asks for local-only work.

### Interpret Portfolio Signals

Read `portfolio/signals.json`, `portfolio/valuation_audit.json`, and relevant valuation result files. Treat signal labels as diagnostics:

- `Strong opportunity`, `Opportunity`, `Watch`, `Fairly valued`, `Review`, `Review: above range`, `No decision`.
- `No decision` means data or assumptions are insufficient, stale, invalid, or missing.
- Do not translate signals into direct buy/sell/hold instructions.

When summarizing, include:

- Current price/date and data quality status.
- Fair value source: user fair value, base valuation, conservative/aggressive scenario, or missing.
- Margin of safety versus required margin.
- Main warnings, including stale prices, invalid assumptions, valuation result older than assumptions, or missing source data.
- Source paths for material claims.

### Resolve `No decision`

Use the reason in `portfolio/signals.json`:

- Missing research: run `investor quickstart <TICKER>` or `investor research ingest <TICKER> --refresh`.
- Missing price or stale price: refresh research/market data, or say the provider data is unavailable/stale.
- Missing fair value: create/fill/validate assumptions or ask the user for a user fair value.
- Invalid assumptions: read `portfolio/valuation_audit.json`, fix the assumptions JSON, validate, rerun `portfolio value`, then rerun `portfolio signals`.
- Valuation result older than assumptions: rerun `investor portfolio value`.

## Discovery And Agent Harness Workflow

Use `investor discovery` when the user wants candidate triage, watchlist review, or a lightweight opportunity queue. Discovery writes queue state, scores, source facts, deterministic calculations, warnings, briefs, and promotion proposals. It never mutates holdings and only changes `portfolio/watchlist.json` through:

```powershell
investor discovery promote MSFT --approved
```

Promotion requires current `analyst_approved` state, an approval artifact, clean claim verification, and matching approval source hashes.

Use `investor agents run` when an LLM-backed institutional-pilot review is explicitly useful. Prefer `--provider dry-run` for no-token workflow checks. For OpenAI-backed runs, set `OPENAI_API_KEY`, control spend with `--limit`, `--ticker`, and `--max-context-chars`, and treat `agentSuggestedState` as proposal-only.

```powershell
investor agents run --provider dry-run --ticker MSFT --no-default-screens
investor agents verify-claims MSFT
investor agents approve MSFT --state analyst_approved --reason "Ready for explicit watchlist-promotion review."
```

`analyst_approved` requires an existing agent review, existing agent brief, and clean claim verification. Unsupported claims, stale deterministic data, missing review artifacts, or stale approval source hashes block promotion.

## Vendor Imports, Evals, And Audit Workflow

Use `investor data import` for normalized vendor CSV or Parquet drops. Imports validate provider provenance, required columns, duplicate primary keys, currencies, units, periods, price adjustment basis, stale prices, and restatement flags. Read the manifest under `data_imports/<PROVIDER>/` for status, warnings, errors, and `normalizedPath`.

```powershell
investor data import --kind fundamentals --path vendor.csv --provider ExampleVendor
investor data import --kind prices --path prices.csv --provider ExampleVendor --max-price-age-days 5 --block-stale-prices
```

Use `investor eval run --suite <SUITE>` for analyst-labeled agent-harness evals and `investor audit verify` before trusting institutional harness audit history.

```powershell
investor eval run --suite gold_candidates
investor audit verify
```

## RSU Tax Workflow

For Israeli Section 102 RSU estimates, use:

```powershell
investor rsu-tax --ticker MSFT --grant-date 2022-05-30 --shares 100 --ordinary-tax-rate 47
```

Treat the output as an estimate, not tax advice. Cite command inputs and manual overrides.

## SEC User Agent

Online SEC requests require a descriptive user agent:

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
```

Do not require the user's personal email.

## Answering Standards

- Treat SEC filings, local metrics, assumptions JSON, valuation result JSON, portfolio signal JSON, and command inputs/output as primary evidence.
- Cite local filing sections, metrics files, data files, assumptions files, valuation files, portfolio files, or command inputs/output for material claims.
- Never invent missing financial numbers.
- Say when data is missing, stale, ambiguous, restated, provider-dependent, or only user-supplied.
- Avoid direct buy/sell/hold instructions and short-term price predictions.
- For fair-value work, write assumptions JSON first, validate it, then cite deterministic CLI valuation output.
- For portfolio work, import workbook edits if applicable, run deterministic valuation/signals, then cite `portfolio/signals.json` and source valuation files.
- For RSU tax work, cite command inputs and label the output as an estimate.

## Agent-Owned Outputs

If the user asks for analysis artifacts, write them as agent-owned files such as:

- `research/<TICKER>/memo.md`
- `research/<TICKER>/questions.md`
- `research/<TICKER>/thesis-log.md`
- `research/<TICKER>/valuation.md`
- `portfolio/review.md`
- `portfolio/questions.md`

Do not describe those as CLI-generated artifacts.
