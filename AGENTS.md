# Investor Toolkit Agent Guide

This repository separates deterministic data/calculation work from agent analysis. Use the `investor` CLI to prepare local artifacts and calculate explicit models, then read files and command output to answer the user.

## Operating Boundary

Use `investor` only for deterministic operations:

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

Do not expect CLI commands for question answering, memo writing, thesis challenge, assumption selection, or investment recommendations. Discovery and agent-harness commands write auditable proposal artifacts; the agent/user owns interpretation and narrative.

If `investor` is not installed, run from the repo root:

```powershell
python -m investor_toolkit <command> <args>
```

## Company Research

For first-time local setup, prefer:

```powershell
investor quickstart MSFT
```

For live research, refresh local source data before answering unless the user asks for offline/local-only work:

```powershell
investor research ingest MSFT --refresh
```

If network access is unavailable, use `--offline` and clearly say when source data is missing or stale.

Read local artifacts directly, starting with:

- `research/<TICKER>/company.json`
- `research/<TICKER>/filings/metadata/filings.json`
- `research/<TICKER>/metrics/metrics.md`
- `research/<TICKER>/metrics/metrics.json`
- `research/<TICKER>/data/financials.json`
- `research/<TICKER>/data/prices.json`
- `research/<TICKER>/extracted/**/business.md`
- `research/<TICKER>/extracted/**/risk-factors.md`
- `research/<TICKER>/extracted/**/mdna.md`
- `research/<TICKER>/index/filing_chunks.jsonl`

Use `rg` over extracted filings for targeted evidence.

## Valuation

Always write assumptions to JSON before valuation, validate them, then cite deterministic valuation output:

```powershell
investor assumptions init MSFT --model fcff-dcf --scenario base --output assumptions/MSFT.base.json
investor assumptions validate assumptions/MSFT.base.json
investor value MSFT --assumptions assumptions/MSFT.base.json --include-sensitivity --format json --output valuations/MSFT.base.result.json
```

Separate source facts, assumptions, deterministic calculations, and judgment. Never invent valuation outputs or give direct buy/sell instructions.

## Portfolio Workbook

Use portfolio commands for deterministic workbook and signal workflows:

```powershell
investor onboarding init
investor portfolio init --output portfolio/portfolio.xlsx
investor portfolio import --workbook portfolio/portfolio.xlsx
investor portfolio value
investor portfolio signals --workbook portfolio/portfolio.xlsx
investor portfolio export --workbook portfolio/portfolio.xlsx
```

The workbook is the user-facing editing surface for holdings, watchlist rows, assumption paths, and user fair values. Import after Excel edits before recalculating. Signals are rule-based diagnostics such as `Opportunity`, `Watch`, `Review`, or `No decision`; do not present them as direct buy/sell/hold instructions.

Read portfolio artifacts directly when explaining results:

- `portfolio/investor_policy.md`
- `portfolio/goals.json`
- `portfolio/preferences.json`
- `portfolio/position_sizing.json`
- `portfolio/valuation_policy.json`
- `portfolio/risk_policy.json`
- `portfolio/decision_process.json`
- `portfolio/operating_preferences.json`
- `portfolio/external_exposure.json`
- `portfolio/onboarding_notes.md`
- `portfolio/thesis_template.md`
- `portfolio/bear_case_template.md`
- `portfolio/theses/README.md`
- `portfolio/rejected/README.md`
- `portfolio/decisions/README.md`
- `portfolio/holdings.json`
- `portfolio/watchlist.json`
- `portfolio/assumption_overrides.json`
- `portfolio/rules.json`
- `portfolio/signals.json`
- `portfolio/valuation_audit.json`
- `portfolio/audit.db`
- `portfolio/candidates.json`
- `portfolio/top_opportunities.json`
- `portfolio/candidate_briefs/<TICKER>.md`
- `portfolio/discovery_runs/<RUN_ID>.json`
- `portfolio/agent_runs/<RUN_ID>.json`
- `portfolio/agent_reviews/<TICKER>.json`
- `portfolio/agent_briefs/<TICKER>.md`
- `portfolio/approvals/<TICKER>.<TIMESTAMP>.json`
- `portfolio/portfolio.xlsx`

If profile artifacts are missing, use `investor onboarding init`. Keep onboarding simple: broad defaults first, a few high-level questions only when needed, and no investment recommendations.

For MCP workflows, check `get_profile_status` before personalized portfolio review or candidate generation. If `onboardingRequired` is true, use `init_investor_profile` after asking only broad questions. `investor://profile/status` is always available. After onboarding, `get_portfolio_context` surfaces existing profile artifacts in `data.profileArtifacts`, `artifacts`, and `sourcePaths`.

## Discovery And Agent Harness

Use `investor discovery` to maintain a triage queue for stock ideas. Discovery may score candidates, write briefs, reject or defer ideas, and propose promotions, but it never mutates holdings and only changes `portfolio/watchlist.json` through `investor discovery promote <TICKER> --approved` after current clean `analyst_approved` state exists.

Use `investor agents run` only when an LLM-backed institutional-pilot review is explicitly useful. Prefer `--provider dry-run` for workflow checks without token spend. For OpenAI-backed runs, set `OPENAI_API_KEY`, control spend with `--limit`, `--ticker`, and `--max-context-chars`, and remember the command writes proposal artifacts under `portfolio/agent_runs/`, `portfolio/agent_reviews/`, and `portfolio/agent_briefs/`. Agent output records `agentSuggestedState`; analyst state is recorded separately with `investor agents approve`.

Verify persisted agent claims before relying on an agent review:

```powershell
investor agents verify-claims MSFT
```

Treat unsupported claims, stale deterministic data, missing review artifacts, or stale approval source hashes as blockers for promotion.

## Vendor Imports, Evals, And Audit

Use `investor data import` for normalized vendor CSV or Parquet drops. CSV works in the dependency-free CLI; Parquet requires optional pandas/pyarrow support in the active Python environment. Imports validate provider provenance, required columns, primary keys, currencies, units, periods, price adjustment basis, stale prices, and restatement flags. Read `data_imports/<PROVIDER>/<RUN_ID>.json` for import status and `normalizedPath`.

Use `investor eval run --suite <SUITE>` for local analyst-labeled agent-harness evals. Gold rows live at `evals/<SUITE>.jsonl`; results are written to `evals/results/<RUN_ID>.json`.

Use `investor audit verify` to check `portfolio/audit.db` hash chains and append-only triggers before trusting institutional harness audit history.

## RSU Tax

For Israeli Section 102 RSU estimates, use:

```powershell
investor rsu-tax --ticker MSFT --grant-date 2022-05-30 --shares 100 --ordinary-tax-rate 47
```

Treat the output as an estimate, not tax advice. Cite command inputs and call out manual overrides.

## SEC User Agent

Online SEC requests require a descriptive user agent:

```powershell
$env:SEC_USER_AGENT = "InvestorResearchAssistant contact@example.com"
```

Do not require the user's personal email.

## Answering Standards

- Treat SEC filings and deterministic metrics as primary evidence.
- Cite local filing sections, metrics files, data files, or command inputs/output for material claims.
- Never invent missing financial numbers.
- Say when data is missing, stale, ambiguous, restated, or provider-dependent.
- Avoid direct buy/sell instructions and short-term price predictions.

## Agent-Owned Outputs

If asked to create analysis artifacts, write them as agent-owned files such as:

- `research/<TICKER>/memo.md`
- `research/<TICKER>/questions.md`
- `research/<TICKER>/thesis-log.md`
- `research/<TICKER>/valuation.md`

Do not describe those files as CLI-generated artifacts.
