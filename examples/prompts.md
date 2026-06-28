# Agent Prompt Examples

Use these from the repo root after running `.\scripts\setup.ps1`, setting `SEC_USER_AGENT`, and running `investor quickstart <TICKER>`.

## Latest Filing Risks

```text
Use the investor-toolkit skill. Refresh local data for MSFT, then summarize the latest filing risks. Cite the specific local filing sections you used and separate evidence from interpretation.
```

## Business Quality Memo

```text
Use the investor-toolkit skill. Build a business quality memo for MSFT from local filings and metrics. Cover revenue durability, margins, returns on capital, balance sheet strength, and capital allocation. Cite local files.
```

## Bear Case

```text
Use the investor-toolkit skill. Draft a bear case for MSFT using only local filings and deterministic metrics. Identify the strongest evidence, weak points in the argument, and open questions.
```

## Deterministic Valuation Workflow

```text
Use the investor-toolkit skill. Prepare a base-case FCFF DCF for MSFT. First refresh local data, then initialize assumptions to assumptions/MSFT.base.json, explain which null judgment fields need to be filled, validate the file, run the valuation, and cite the result JSON. Do not give buy/sell advice.
```

## Portfolio Signal Review

```text
Use the investor-toolkit skill. Import my portfolio workbook, run available portfolio valuations and signals, then summarize only the No decision and Review rows with source paths and next deterministic actions. Do not translate signals into buy/sell/hold advice.
```

## Discovery Candidate Triage

```text
Use the investor-toolkit skill. Run discovery for MSFT and PANW without default screens, score the candidates, write briefs, and summarize the promotion rationale, missing evidence, and next actions from the local artifacts.
```

## Agent Harness Dry Run

```text
Use the investor-toolkit skill. Run the agent harness in dry-run mode for MSFT, inspect the generated agent run/review artifacts, and explain what a real OpenAI-backed run would need before analyst approval or watchlist promotion.
```

## Vendor Import Check

```text
Use the investor-toolkit skill. Import this normalized vendor fundamentals CSV with provider ExampleVendor, then read the import manifest and summarize any validation warnings or blockers.
```

## Israeli Section 102 RSU Estimate

```text
Use the investor-toolkit skill. Estimate Israeli Section 102 RSU tax for 100 MSFT shares granted on 2022-05-30 with a 47% ordinary tax rate. Use the CLI output as the source of truth and label it as an estimate, not tax advice.
```
