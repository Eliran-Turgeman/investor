# Discovery Harness

The discovery harness automates attention, not conviction. It owns candidate workflow state, ranking, briefs, rejection/defer logs, and watchlist promotion proposals. The existing investor research, metrics, valuation, and portfolio engines remain the deterministic data and calculation layer.

## Architecture

Inputs:

- Configured discovery screens, source files, or explicit ticker inputs.
- Local research artifacts under `research/<TICKER>/`.
- Local valuation outputs under `valuations/`.
- Portfolio context under `portfolio/holdings.json`, `portfolio/watchlist.json`, `portfolio/rules.json`, and `portfolio/assumption_overrides.json`.

Core flow:

1. `investor discovery discover` normalizes tickers, appends a run log, and idempotently updates `portfolio/candidates.json`.
2. `investor discovery refresh <TICKER>` calls the deterministic research workflow to refresh local research and metrics.
3. `investor discovery score <TICKER>` reads only local artifacts and writes component scores, source facts, deterministic calculations, warnings, and promotion rationale.
4. `investor discovery brief <TICKER>` writes `portfolio/candidate_briefs/<TICKER>.md` from persisted evidence.
5. `reject` and `defer` preserve reasoning in candidate state. Rejections also append `portfolio/rejected/<TICKER>.md`.
6. `propose-promotions` writes `portfolio/top_opportunities.json` with candidates that deserve explicit user review.
7. `promote <TICKER> --approved` is the only command that updates `portfolio/watchlist.json`; holdings are never mutated. It also requires current `analyst_approved` state, a current approval artifact, clean claim verification, and matching approval source hashes.

The first implementation ships stable local and fixture-friendly discovery inputs. Live market screeners can be added later behind the same source contract without changing candidate artifacts.

## JSON Schemas

Schemas live in `investor_toolkit.discovery.schemas` and are validated before writes and after reads.

- `CANDIDATES_SCHEMA`: validates `portfolio/candidates.json`.
- `DISCOVERY_RUN_SCHEMA`: validates append-only `portfolio/discovery_runs/<RUN_ID>.json`.
- `TOP_OPPORTUNITIES_SCHEMA`: validates `portfolio/top_opportunities.json`.

Candidate records include:

- `ticker`, `companyName`, and `state`.
- `sources` and `seenInRuns`.
- `artifactRefs` with local paths and MCP-style URIs.
- `sourceFacts`, `deterministicCalculations`, `componentScores`, and `totalScore`.
- `judgmentSummary`, `keyRisks`, `missingEvidence`, `warnings`, `nextAction`, and `watchlistPromotionRationale`.

Allowed states:

```text
discovered, screened, refreshed, briefed, deferred, rejected, promote_candidate,
agent_reviewed, analyst_approved, analyst_rejected, needs_more_evidence,
promoted_to_watchlist
```

The deterministic discovery score may mark a candidate as `promote_candidate` for explicit review. The AI agent harness records its opinion separately as `agentSuggestedState`; analyst approval records the final review state.

Component scores:

```text
profile_fit, business_quality, growth_runway, valuation_sanity,
balance_sheet, downside_risk, evidence_freshness, portfolio_fit
```

## CLI Design

```powershell
investor discovery discover --ticker MSFT --ticker PANW --no-default-screens
investor discovery discover --source-file portfolio/my_screen.json
investor discovery refresh MSFT --refresh
investor discovery refresh MSFT --offline
investor discovery score MSFT
investor discovery brief MSFT
investor discovery reject MSFT --reason "Outside circle of competence."
investor discovery defer MSFT --reason "Need fresh valuation output."
investor discovery propose-promotions
investor discovery promote MSFT --approved
investor discovery review-watchlist --offline
```

Safety rules:

- Discovery never mutates holdings.
- Discovery never adds to the watchlist unless `promote` is called with `--approved` after a current clean analyst approval exists.
- Discovery signals are triage diagnostics, not buy/sell/hold instructions.
- Missing data, stale prices, failed refreshes, invalid assumptions, and stale valuation outputs are explicit warnings.
