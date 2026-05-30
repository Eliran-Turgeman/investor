---
name: value-investing-research
description: Use when Codex or another coding agent needs to analyze a US-listed public company from this repository's local research data. The skill tells the agent how to use the deterministic CLI only for data ingestion, normalization, filing extraction, metrics, and local artifact discovery, then answer user questions by reading and citing files under research ticker folders.
---

# Value Investing Research

The CLI is only a data provider. The agent does the analysis by reading local artifacts.

## Core Boundary

Use `research` for deterministic operations only:

```powershell
research start <TICKER>
research ingest <TICKER>
research metrics <TICKER>
```

Do not expect or call CLI commands for question answering, memo writing, thesis challenge, fair-value estimates, or investment recommendations. Those are agent tasks.

If `research` is not installed, run from the repo root:

```powershell
python -m investor_research <command> <TICKER>
```

## Agent Workflow

1. Normalize the ticker to uppercase.
2. Check for `research/<TICKER>/company.json`.
3. If source data is missing, run:

```powershell
research start <TICKER>
```

4. If source data may be stale, run:

```powershell
research ingest <TICKER>
```

5. If metrics are missing or stale, run:

```powershell
research metrics <TICKER>
```

6. Read local artifacts directly. Start with:

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

Use `rg` over `research/<TICKER>/extracted` for targeted evidence. Use `metrics.json` for numbers that need calculation or comparison.

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
- For fair-value work, make assumptions explicit and label them as agent assumptions, not CLI output.

## Agent-Owned Outputs

If the user asks for a memo, thesis log, valuation, or research questions, create or update those as agent-owned files such as:

- `research/<TICKER>/memo.md`
- `research/<TICKER>/questions.md`
- `research/<TICKER>/thesis-log.md`
- `research/<TICKER>/valuation.md`

Do not describe those as CLI-generated artifacts.
