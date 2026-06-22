from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from ..audit import file_hash, stable_hash
from ..portfolio.engine import default_rules
from ..storage import ResearchStorage
from ..utils import append_text, normalize_ticker, parse_iso_date, read_json, utc_now_iso, write_json, write_text
from ..valuation import validate_assumptions_file
from ..workflow import ResearchWorkflow
from .schemas import COMPONENT_SCORE_KEYS, SCHEMA_VERSION, validate_discovery_artifact


ResearchRunner = Callable[[str, bool, bool], dict[str, Any]]


DEFAULT_DISCOVERY_SOURCES: list[dict[str, Any]] = [
    {
        "name": "profile_seed_software_ai_infrastructure",
        "sourceType": "configured_seed",
        "description": (
            "Profile-aligned seed universe for software, cybersecurity, semiconductors, "
            "AI infrastructure, and AI hardware-adjacent businesses. This is an attention "
            "queue, not a recommendation list."
        ),
        "tickers": ["MSFT", "NVDA", "AVGO", "AMD", "PANW", "CRWD", "DDOG", "NET", "SNOW", "MDB"],
    }
]

FOCUS_KEYWORDS = (
    "software",
    "cybersecurity",
    "security",
    "semiconductor",
    "semiconductors",
    "ai",
    "artificial intelligence",
    "accelerated computing",
    "data center",
    "datacenter",
    "cloud",
    "infrastructure",
    "gpu",
    "networking",
    "database",
    "developer",
)

HARD_AVOID_KEYWORDS = (
    " adr",
    "american depositary",
    "china-based",
    "chinese issuer",
    "variable interest entity",
    " vie",
    "cayman islands",
)

RISK_KEYWORDS = (
    "customer concentration",
    "concentration",
    "competition",
    "competitive",
    "cyclical",
    "cyclicality",
    "supply chain",
    "export control",
    "regulation",
    "regulatory",
    "material weakness",
    "restructuring",
    "china",
)

STATE_WRITE_PROTECTED = {
    "rejected",
    "deferred",
    "analyst_approved",
    "analyst_rejected",
    "needs_more_evidence",
    "promoted_to_watchlist",
}

PROMOTION_PROPOSAL_EXCLUDED_STATES = {
    "rejected",
    "deferred",
    "analyst_rejected",
    "needs_more_evidence",
    "promoted_to_watchlist",
}


@dataclass(slots=True)
class DiscoveryPaths:
    portfolio_dir: Path
    candidates: Path
    top_opportunities: Path
    briefs_dir: Path
    rejected_dir: Path
    discovery_runs_dir: Path
    watchlist: Path
    holdings: Path


class DiscoveryHarness:
    def __init__(
        self,
        cwd: str | Path = ".",
        portfolio_dir: str | Path = "portfolio",
        research_root: str | Path | None = None,
        assumptions_dir: str | Path = "assumptions",
        valuations_dir: str | Path = "valuations",
        research_runner: ResearchRunner | None = None,
        today: date | None = None,
    ) -> None:
        self.cwd = Path(cwd).resolve()
        self.storage = ResearchStorage(self.cwd, research_root=research_root)
        self.research_root = self.storage.research_root
        self.portfolio_dir = _resolve_workspace_path(portfolio_dir, self.cwd)
        self.assumptions_dir = _resolve_workspace_path(assumptions_dir, self.cwd)
        self.valuations_dir = _resolve_workspace_path(valuations_dir, self.cwd)
        self.research_runner = research_runner
        self.today = today or date.today()

    @property
    def paths(self) -> DiscoveryPaths:
        return DiscoveryPaths(
            portfolio_dir=self.portfolio_dir,
            candidates=self.portfolio_dir / "candidates.json",
            top_opportunities=self.portfolio_dir / "top_opportunities.json",
            briefs_dir=self.portfolio_dir / "candidate_briefs",
            rejected_dir=self.portfolio_dir / "rejected",
            discovery_runs_dir=self.portfolio_dir / "discovery_runs",
            watchlist=self.portfolio_dir / "watchlist.json",
            holdings=self.portfolio_dir / "holdings.json",
        )

    def discover(
        self,
        tickers: list[str] | None = None,
        source_file: str | Path | None = None,
        config_file: str | Path | None = None,
        screen_name: str = "manual",
        include_default_screens: bool = True,
        resurface_rejected: bool = False,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        queue = self.load_candidates()
        candidates_by_ticker = _candidate_map(queue)
        sources = self._discovery_sources(
            tickers=tickers or [],
            source_file=source_file,
            config_file=config_file,
            screen_name=screen_name,
            include_default_screens=include_default_screens,
        )
        resolved_run_id = _unique_run_id(self.paths.discovery_runs_dir, run_id or _generated_run_id("discover"))
        discovered: list[str] = []
        updated: list[str] = []
        suppressed: list[str] = []
        skipped: list[dict[str, str]] = []
        warnings: list[str] = []

        for source in sources:
            for raw_ticker in source.get("tickers", []):
                try:
                    ticker = normalize_ticker(str(raw_ticker))
                except ValueError as exc:
                    skipped.append({"ticker": str(raw_ticker), "reason": str(exc), "source": str(source.get("name", ""))})
                    continue
                existing = candidates_by_ticker.get(ticker)
                if existing and existing.get("state") == "rejected" and not resurface_rejected:
                    suppressed.append(ticker)
                    continue
                if existing is None:
                    candidate = _new_candidate(ticker, now)
                    queue["candidates"].append(candidate)
                    candidates_by_ticker[ticker] = candidate
                    discovered.append(ticker)
                else:
                    candidate = existing
                    updated.append(ticker)
                candidate["lastSeenAt"] = now
                candidate["lastUpdatedAt"] = now
                if candidate.get("state") == "rejected" and resurface_rejected:
                    candidate["state"] = "discovered"
                    candidate["resurfacedAt"] = now
                elif candidate.get("state") not in STATE_WRITE_PROTECTED:
                    candidate["state"] = "discovered"
                _append_unique(candidate.setdefault("seenInRuns", []), resolved_run_id)
                _append_source(candidate.setdefault("sources", []), source, now)
                if not candidate.get("companyName"):
                    candidate["companyName"] = self._company_name(ticker)

        queue["updatedAt"] = now
        self._write_candidates(queue)
        self._write_top_opportunities(queue["candidates"], selection="ranked_candidates")
        run_log = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": resolved_run_id,
            "generatedAt": now,
            "command": "discover",
            "screens": [_screen_log_item(source) for source in sources],
            "discovered": sorted(set(discovered)),
            "updated": sorted(set(updated)),
            "suppressed": sorted(set(suppressed)),
            "skipped": skipped,
            "warnings": warnings,
            "artifacts": {
                "candidates": str(self.paths.candidates),
                "topOpportunities": str(self.paths.top_opportunities),
            },
        }
        run_path = self._write_run_log(run_log)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "runId": resolved_run_id,
            "runPath": str(run_path),
            "candidatesPath": str(self.paths.candidates),
            "topOpportunitiesPath": str(self.paths.top_opportunities),
            "discovered": sorted(set(discovered)),
            "updated": sorted(set(updated)),
            "suppressed": sorted(set(suppressed)),
            "skipped": skipped,
            "warnings": warnings,
            "candidateCount": len(queue["candidates"]),
        }

    def refresh(self, ticker: str, offline: bool = False, refresh: bool = False) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        now = utc_now_iso()
        queue = self.load_candidates()
        candidate = _ensure_candidate(queue, ticker, now)
        warnings: list[str] = []
        errors: list[str] = []
        try:
            runner = self.research_runner or self._default_research_runner
            result = runner(ticker, offline, refresh)
            messages = list(result.get("messages", []))
            warnings.extend(str(item) for item in result.get("warnings", []))
            candidate["state"] = "refreshed"
            candidate["lastRefreshedAt"] = now
            candidate["refreshStatus"] = "ok"
            candidate["refreshMessages"] = messages
        except Exception as exc:
            messages = []
            errors.append(str(exc))
            warnings.append(f"refresh failed: {exc}")
            candidate["refreshStatus"] = "error"
            candidate["refreshError"] = str(exc)
        candidate["lastUpdatedAt"] = now
        candidate["companyName"] = self._company_name(ticker)
        candidate["artifactRefs"] = self._company_artifact_refs(ticker)
        candidate["warnings"] = _unique_strings([*candidate.get("warnings", []), *warnings])
        queue["updatedAt"] = now
        self._write_candidates(queue)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "ticker": ticker,
            "status": "error" if errors else "ok",
            "messages": messages,
            "warnings": warnings,
            "errors": errors,
            "candidate": candidate,
        }

    def score(self, ticker: str) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        now = utc_now_iso()
        queue = self.load_candidates()
        candidate = _ensure_candidate(queue, ticker, now)
        analysis = self._score_candidate(ticker)
        protected_state = candidate.get("state") in STATE_WRITE_PROTECTED
        candidate.update(analysis)
        candidate["lastScoredAt"] = now
        candidate["lastUpdatedAt"] = now
        if not protected_state:
            candidate["state"] = "promote_candidate" if analysis["watchlistPromotionCandidate"] else "screened"
        queue["updatedAt"] = now
        self._write_candidates(queue)
        self._write_top_opportunities(queue["candidates"], selection="ranked_candidates")
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "ticker": ticker,
            "candidate": candidate,
            "topOpportunitiesPath": str(self.paths.top_opportunities),
        }

    def brief(self, ticker: str) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        queue = self.load_candidates()
        candidate = _candidate_map(queue).get(ticker)
        if candidate is None or candidate.get("totalScore") is None:
            self.score(ticker)
            queue = self.load_candidates()
            candidate = _candidate_map(queue)[ticker]
        now = utc_now_iso()
        content = self._render_brief(candidate, now)
        path = self.paths.briefs_dir / f"{ticker}.md"
        write_text(path, content)
        if candidate.get("state") not in STATE_WRITE_PROTECTED:
            candidate["state"] = "promote_candidate" if candidate.get("watchlistPromotionCandidate") else "briefed"
        candidate["lastBriefedAt"] = now
        candidate["lastUpdatedAt"] = now
        candidate["briefPath"] = str(path)
        queue["updatedAt"] = now
        self._write_candidates(queue)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "ticker": ticker,
            "briefPath": str(path),
            "candidate": candidate,
        }

    def reject(self, ticker: str, reason: str) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        reason = reason.strip()
        if not reason:
            raise ValueError("reject requires a non-empty reason")
        now = utc_now_iso()
        queue = self.load_candidates()
        candidate = _ensure_candidate(queue, ticker, now)
        candidate["state"] = "rejected"
        candidate["rejectionReason"] = reason
        candidate["rejectedAt"] = now
        candidate["lastUpdatedAt"] = now
        candidate["nextAction"] = "Do not resurface unless explicitly reactivated by the user."
        path = self.paths.rejected_dir / f"{ticker}.md"
        append_text(path, f"## {now}\n\nReason: {reason}\n\n")
        candidate["rejectionPath"] = str(path)
        queue["updatedAt"] = now
        self._write_candidates(queue)
        return {"schemaVersion": SCHEMA_VERSION, "generatedAt": now, "ticker": ticker, "rejectionPath": str(path)}

    def defer(self, ticker: str, reason: str) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        reason = reason.strip()
        if not reason:
            raise ValueError("defer requires a non-empty reason")
        now = utc_now_iso()
        queue = self.load_candidates()
        candidate = _ensure_candidate(queue, ticker, now)
        candidate["state"] = "deferred"
        candidate["deferReason"] = reason
        candidate["deferredAt"] = now
        candidate["lastUpdatedAt"] = now
        _append_unique(candidate.setdefault("missingEvidence", []), reason)
        candidate["nextAction"] = "Revisit only when the deferred condition or missing evidence is resolved."
        queue["updatedAt"] = now
        self._write_candidates(queue)
        return {"schemaVersion": SCHEMA_VERSION, "generatedAt": now, "ticker": ticker, "reason": reason}

    def propose_promotions(self, limit: int = 10) -> dict[str, Any]:
        queue = self.load_candidates()
        candidates = [
            candidate
            for candidate in queue.get("candidates", [])
            if candidate.get("watchlistPromotionCandidate")
            and candidate.get("state") not in PROMOTION_PROPOSAL_EXCLUDED_STATES
        ]
        ranked = _ranked(candidates)[: max(0, limit)]
        path = self._write_top_opportunities(ranked, selection="promotion_candidates")
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now_iso(),
            "topOpportunitiesPath": str(path),
            "rows": ranked,
        }

    def promote(self, ticker: str, approved: bool = False) -> dict[str, Any]:
        ticker = normalize_ticker(ticker)
        if not approved:
            raise ValueError("Explicit user approval is required before adding a ticker to watchlist.json.")
        now = utc_now_iso()
        queue = self.load_candidates()
        candidate = _candidate_map(queue).get(ticker)
        if candidate is None:
            raise ValueError(f"Candidate not found: {ticker}")
        approval = self._validate_promotion_approval(ticker=ticker, candidate=candidate)
        watchlist = self._load_watchlist()
        rows = watchlist.setdefault("watchlist", [])
        already_present = any(str(item.get("ticker", "")).upper() == ticker for item in rows if isinstance(item, dict))
        if not already_present:
            rows.append(
                {
                    "ticker": ticker,
                    "priority": "review",
                    "notes": (
                        "Promoted from discovery harness for user review. "
                        "Requires thesis work and explicit assumptions before any portfolio decision."
                    ),
                }
            )
        watchlist["schemaVersion"] = str(watchlist.get("schemaVersion") or SCHEMA_VERSION)
        watchlist["updatedAt"] = now
        write_json(self.paths.watchlist, watchlist)
        candidate["state"] = "promoted_to_watchlist"
        candidate["promotedAt"] = now
        candidate["lastUpdatedAt"] = now
        candidate["nextAction"] = "Draft or update portfolio/theses/<TICKER>.md before any portfolio decision."
        queue["updatedAt"] = now
        self._write_candidates(queue)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "ticker": ticker,
            "watchlistPath": str(self.paths.watchlist),
            "approvalPath": approval["approvalPath"],
            "alreadyPresent": already_present,
        }

    def review_watchlist(self, offline: bool = False, refresh: bool = False) -> dict[str, Any]:
        watchlist = self._load_watchlist()
        tickers = [
            normalize_ticker(str(item.get("ticker", "")))
            for item in watchlist.get("watchlist", [])
            if isinstance(item, dict) and item.get("ticker")
        ]
        rows = []
        for ticker in tickers:
            refresh_result = self.refresh(ticker, offline=offline, refresh=refresh)
            score_result = self.score(ticker)
            rows.append(
                {
                    "ticker": ticker,
                    "refreshStatus": refresh_result.get("status"),
                    "totalScore": score_result["candidate"].get("totalScore"),
                    "state": score_result["candidate"].get("state"),
                }
            )
        return {"schemaVersion": SCHEMA_VERSION, "generatedAt": utc_now_iso(), "rows": rows}

    def load_candidates(self) -> dict[str, Any]:
        if not self.paths.candidates.exists():
            return {
                "schemaVersion": SCHEMA_VERSION,
                "generatedAt": utc_now_iso(),
                "updatedAt": utc_now_iso(),
                "candidates": [],
            }
        data = read_json(self.paths.candidates, None)
        validate_discovery_artifact("candidates", data)
        return data

    def _write_candidates(self, queue: dict[str, Any]) -> None:
        validate_discovery_artifact("candidates", queue)
        write_json(self.paths.candidates, queue)

    def _write_run_log(self, run_log: dict[str, Any]) -> Path:
        validate_discovery_artifact("discovery_run", run_log)
        path = self.paths.discovery_runs_dir / f"{run_log['runId']}.json"
        write_json(path, run_log)
        return path

    def _write_top_opportunities(self, candidates: list[dict[str, Any]], selection: str) -> Path:
        ranked = _ranked(candidates)
        for index, candidate in enumerate(ranked, start=1):
            candidate["rank"] = index
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now_iso(),
            "selection": selection,
            "rows": ranked,
        }
        validate_discovery_artifact("top_opportunities", payload)
        write_json(self.paths.top_opportunities, payload)
        return self.paths.top_opportunities

    def _default_research_runner(self, ticker: str, offline: bool, refresh: bool) -> dict[str, Any]:
        workflow = ResearchWorkflow(self.cwd, research_root=self.research_root)
        company_dir = self.storage.company_dir(ticker)
        if company_dir.exists():
            result = workflow.ingest(ticker, offline=offline, refresh=refresh)
        else:
            result = workflow.start(ticker, offline=offline, refresh=refresh)
        return {
            "ticker": result.ticker,
            "companyDir": str(result.company_dir),
            "messages": result.messages,
            "warnings": result.warnings,
        }

    def _discovery_sources(
        self,
        tickers: list[str],
        source_file: str | Path | None,
        config_file: str | Path | None,
        screen_name: str,
        include_default_screens: bool,
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        if include_default_screens:
            sources.extend(json.loads(json.dumps(DEFAULT_DISCOVERY_SOURCES)))
        configured = Path(config_file) if config_file else self.paths.portfolio_dir / "discovery_config.json"
        if configured.exists():
            sources.extend(_load_sources_from_path(_resolve_workspace_path(configured, self.cwd)))
        if source_file:
            sources.extend(_load_sources_from_path(_resolve_workspace_path(source_file, self.cwd)))
        if tickers:
            sources.append(
                {
                    "name": screen_name or "manual",
                    "sourceType": "manual",
                    "description": "Ticker(s) supplied directly to the discovery command.",
                    "tickers": tickers,
                }
            )
        return sources

    def _score_candidate(self, ticker: str) -> dict[str, Any]:
        company = _safe_read_json(self.research_root / ticker / "company.json", {})
        metrics = _safe_read_json(self.research_root / ticker / "metrics" / "metrics.json", {})
        prices = _safe_read_json(self.research_root / ticker / "data" / "prices.json", [])
        filings = _safe_read_json(self.research_root / ticker / "filings" / "metadata" / "filings.json", [])
        latest_metrics = _latest_period_row(metrics.get("periods", []) if isinstance(metrics, dict) else [])
        latest_price = _latest_price_row(prices, ticker) if isinstance(prices, list) else {}
        latest_filing = _latest_filing(filings) if isinstance(filings, list) else {}
        business_paths = sorted((self.research_root / ticker / "extracted").rglob("business.md"))
        risk_paths = sorted((self.research_root / ticker / "extracted").rglob("risk-factors.md"))
        business_text = _read_text_sample(business_paths)
        risk_text = _read_text_sample(risk_paths)
        valuation_outputs = self._valuation_outputs(ticker)
        assumption_warnings = self._assumption_warnings(ticker)

        artifact_refs = self._company_artifact_refs(ticker)
        artifact_refs.extend(self._valuation_artifact_refs(ticker))
        source_facts = self._source_facts(ticker, company, latest_metrics, latest_price, latest_filing, valuation_outputs)
        calculations = self._deterministic_calculations(latest_metrics, latest_price, latest_filing, valuation_outputs)
        warnings = self._data_warnings(ticker, metrics, prices, filings, business_paths, risk_paths, valuation_outputs)
        warnings.extend(assumption_warnings)

        profile_context = _profile_context(company, business_text)
        hard_avoid = _has_hard_avoid(company, business_text)
        component_scores = {
            "profile_fit": self._profile_fit_score(company, business_text, hard_avoid),
            "business_quality": self._business_quality_score(latest_metrics),
            "growth_runway": self._growth_runway_score(latest_metrics, business_text),
            "valuation_sanity": self._valuation_sanity_score(latest_metrics, valuation_outputs),
            "balance_sheet": self._balance_sheet_score(latest_metrics),
            "downside_risk": self._downside_risk_score(latest_metrics, risk_text, hard_avoid),
            "evidence_freshness": self._evidence_freshness_score(latest_price, latest_filing, metrics, business_paths, risk_paths),
            "portfolio_fit": self._portfolio_fit_score(ticker, hard_avoid, profile_context),
        }
        total_score = _weighted_total(component_scores)
        missing_evidence = self._missing_evidence(metrics, prices, filings, business_paths, risk_paths, valuation_outputs)
        key_risks = self._key_risks(latest_metrics, risk_text, hard_avoid)
        promotion_candidate, promotion_rationale = self._promotion_assessment(component_scores, total_score, hard_avoid, missing_evidence)
        next_action = self._next_action(promotion_candidate, component_scores, missing_evidence, hard_avoid)
        return {
            "companyName": str(company.get("name") or company.get("title") or ticker) if isinstance(company, dict) else ticker,
            "artifactRefs": artifact_refs,
            "sourceFacts": source_facts,
            "deterministicCalculations": calculations,
            "componentScores": component_scores,
            "totalScore": round(total_score, 1),
            "judgmentSummary": self._judgment_summary(component_scores, promotion_candidate, hard_avoid, missing_evidence),
            "keyRisks": key_risks,
            "missingEvidence": missing_evidence,
            "warnings": _unique_strings(warnings),
            "nextAction": next_action,
            "watchlistPromotionCandidate": promotion_candidate,
            "watchlistPromotionRationale": promotion_rationale,
        }

    def _company_name(self, ticker: str) -> str:
        company = self.storage.load_company(ticker)
        return company.name if company and company.name else ticker

    def _company_artifact_refs(self, ticker: str) -> list[dict[str, Any]]:
        base = self.research_root / ticker
        refs = [
            ("company", base / "company.json", f"investor://company/{ticker}/company"),
            ("filings", base / "filings" / "metadata" / "filings.json", f"investor://company/{ticker}/filings"),
            ("metrics", base / "metrics" / "metrics.json", f"investor://company/{ticker}/metrics-json"),
            ("financials", base / "data" / "financials.json", f"investor://company/{ticker}/financials"),
            ("prices", base / "data" / "prices.json", f"investor://company/{ticker}/prices"),
            ("filing-index", base / "index" / "filing_chunks.jsonl", f"investor://company/{ticker}/filing-index"),
        ]
        business = sorted((base / "extracted").rglob("business.md"))
        risks = sorted((base / "extracted").rglob("risk-factors.md"))
        if business:
            refs.append(("business-section", business[-1], f"investor://company/{ticker}/extracted/{business[-1].parent.name}/business"))
        if risks:
            refs.append(("risk-factors-section", risks[-1], f"investor://company/{ticker}/extracted/{risks[-1].parent.name}/risk-factors"))
        return [_artifact(kind, path, uri) for kind, path, uri in refs]

    def _valuation_artifact_refs(self, ticker: str) -> list[dict[str, Any]]:
        return [
            _artifact("valuation-result", path, f"investor://valuation/{ticker}/{path.stem}")
            for path in sorted(self.valuations_dir.glob(f"{ticker}.*.result.json"))
        ]

    def _valuation_outputs(self, ticker: str) -> list[dict[str, Any]]:
        outputs = []
        for path in sorted(self.valuations_dir.glob(f"{ticker}.*.result.json")):
            data = _safe_read_json(path, {})
            if not isinstance(data, dict) or str(data.get("ticker", "")).upper() != ticker:
                continue
            valuation = data.get("valuation", {}) if isinstance(data.get("valuation"), dict) else {}
            market = data.get("market", {}) if isinstance(data.get("market"), dict) else {}
            outputs.append(
                {
                    "path": str(path),
                    "scenario": data.get("scenario"),
                    "model": data.get("model"),
                    "fairValuePerShare": _num(valuation.get("fairValuePerShare")),
                    "currentSharePrice": _num(valuation.get("currentSharePrice")),
                    "marginOfSafety": _num(valuation.get("marginOfSafety")),
                    "priceDate": market.get("priceDate"),
                    "warnings": data.get("warnings", []),
                    "portfolioSource": data.get("portfolioSource"),
                }
            )
        return outputs

    def _assumption_warnings(self, ticker: str) -> list[str]:
        warnings = []
        for path in self._assumption_paths(ticker):
            try:
                report = validate_assumptions_file(path, cwd=self.cwd, research_root=self.research_root, expected_ticker=ticker)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(f"invalid assumptions or missing local data for {path}: {exc}")
                continue
            if report.errors:
                warnings.append(f"invalid assumptions in {path}: {'; '.join(report.errors)}")
            for warning in report.warnings:
                warnings.append(f"assumption warning in {path}: {warning.message}")
        for output in self._valuation_outputs(ticker):
            source = output.get("portfolioSource")
            if not isinstance(source, dict):
                continue
            assumptions_path = source.get("assumptionsPath")
            if not assumptions_path:
                continue
            result_path = Path(str(output.get("path", "")))
            source_path = Path(str(assumptions_path))
            if source_path.exists() and result_path.exists() and result_path.stat().st_mtime < source_path.stat().st_mtime:
                warnings.append(f"valuation result is older than source assumptions: {source_path}")
        return warnings

    def _assumption_paths(self, ticker: str) -> list[Path]:
        paths: list[Path] = []
        if self.assumptions_dir.exists():
            paths.extend(sorted(self.assumptions_dir.glob(f"{ticker}.*.json")))
        overrides = _safe_read_json(self.portfolio_dir / "assumption_overrides.json", {})
        for item in overrides.get("assumptions", []) if isinstance(overrides, dict) else []:
            if not isinstance(item, dict) or str(item.get("ticker", "")).upper() != ticker:
                continue
            raw_path = item.get("assumptionsPath")
            if raw_path:
                paths.append(_resolve_workspace_path(raw_path, self.cwd))
        unique: list[Path] = []
        for path in paths:
            if path not in unique:
                unique.append(path)
        return unique

    def _source_facts(
        self,
        ticker: str,
        company: Any,
        latest_metrics: dict[str, Any],
        latest_price: dict[str, Any],
        latest_filing: dict[str, Any],
        valuation_outputs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        metrics_path = self.research_root / ticker / "metrics" / "metrics.json"
        company_path = self.research_root / ticker / "company.json"
        prices_path = self.research_root / ticker / "data" / "prices.json"
        filings_path = self.research_root / ticker / "filings" / "metadata" / "filings.json"
        if isinstance(company, dict):
            for field, label in [("sector", "Sector"), ("industry", "Industry"), ("exchange", "Exchange")]:
                if company.get(field):
                    facts.append(_fact(label, company.get(field), company_path, f"investor://company/{ticker}/company"))
        metric_fields = [
            ("period", "Latest metrics period"),
            ("revenue_growth_yoy", "Revenue growth YoY"),
            ("operating_margin", "Operating margin"),
            ("fcf_margin", "FCF margin"),
            ("fcf_conversion_from_net_income", "FCF conversion from net income"),
            ("roic", "ROIC"),
            ("debt_to_equity", "Debt to equity"),
            ("price_to_free_cash_flow", "Price to free cash flow"),
            ("ev_to_revenue", "EV to revenue"),
        ]
        for field, label in metric_fields:
            if latest_metrics.get(field) is not None:
                facts.append(_fact(label, latest_metrics.get(field), metrics_path, f"investor://company/{ticker}/metrics-json"))
        if latest_price:
            if latest_price.get("date"):
                facts.append(_fact("Latest price date", latest_price.get("date"), prices_path, f"investor://company/{ticker}/prices"))
            if _price_value(latest_price) is not None:
                facts.append(_fact("Latest price", _price_value(latest_price), prices_path, f"investor://company/{ticker}/prices"))
        if latest_filing:
            facts.append(
                _fact(
                    "Latest filing",
                    f"{latest_filing.get('formType', '')} {latest_filing.get('filingDate', '')}".strip(),
                    filings_path,
                    f"investor://company/{ticker}/filings",
                )
            )
        for output in valuation_outputs:
            if output.get("fairValuePerShare") is not None:
                facts.append(
                    _fact(
                        f"{output.get('scenario') or 'valuation'} fair value per share",
                        output.get("fairValuePerShare"),
                        Path(str(output["path"])),
                        f"investor://valuation/{ticker}/{Path(str(output['path'])).stem}",
                    )
                )
        return facts

    def _deterministic_calculations(
        self,
        latest_metrics: dict[str, Any],
        latest_price: dict[str, Any],
        latest_filing: dict[str, Any],
        valuation_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "latestMetricsPeriod": latest_metrics.get("period"),
            "priceAgeDays": self._age_days(latest_price.get("date")),
            "latestFilingAgeDays": self._age_days(latest_filing.get("filingDate")),
            "valuationOutputs": valuation_outputs,
            "latestMetricValuesUsed": {
                key: latest_metrics.get(key)
                for key in [
                    "revenue_growth_yoy",
                    "operating_margin",
                    "fcf_margin",
                    "fcf_conversion_from_net_income",
                    "roic",
                    "debt_to_equity",
                    "share_count_change",
                    "sbc_percent_revenue",
                    "price_to_free_cash_flow",
                    "price_to_earnings",
                    "ev_to_revenue",
                    "interest_coverage",
                ]
                if latest_metrics.get(key) is not None
            },
        }

    def _data_warnings(
        self,
        ticker: str,
        metrics: Any,
        prices: Any,
        filings: Any,
        business_paths: list[Path],
        risk_paths: list[Path],
        valuation_outputs: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        if not isinstance(metrics, dict) or not metrics.get("periods"):
            warnings.append(f"{ticker}: missing metrics.json or no metric periods")
        if not isinstance(prices, list) or not prices:
            warnings.append(f"{ticker}: missing prices.json or no price rows")
        else:
            price_row = _latest_price_row(prices, ticker)
            age = self._age_days(price_row.get("date"))
            if age is None:
                warnings.append(f"{ticker}: latest price date is missing or invalid")
            elif age > _stale_price_days(self.portfolio_dir):
                warnings.append(f"{ticker}: stale price data ({age} days old)")
        if not isinstance(filings, list) or not filings:
            warnings.append(f"{ticker}: missing filing metadata")
        if not business_paths:
            warnings.append(f"{ticker}: missing extracted business section")
        if not risk_paths:
            warnings.append(f"{ticker}: missing extracted risk factors section")
        for output in valuation_outputs:
            for warning in output.get("warnings", []):
                message = warning.get("message") if isinstance(warning, dict) else str(warning)
                if message:
                    warnings.append(f"{ticker}: valuation warning: {message}")
        return warnings

    def _missing_evidence(
        self,
        metrics: Any,
        prices: Any,
        filings: Any,
        business_paths: list[Path],
        risk_paths: list[Path],
        valuation_outputs: list[dict[str, Any]],
    ) -> list[str]:
        missing = []
        if not isinstance(metrics, dict) or not metrics.get("periods"):
            missing.append("Local metrics are missing; run discovery refresh or investor research metrics.")
        if not isinstance(prices, list) or not prices:
            missing.append("Local price data is missing or unavailable.")
        if not isinstance(filings, list) or not filings:
            missing.append("SEC filing metadata is missing.")
        if not business_paths:
            missing.append("Extracted business section is missing.")
        if not risk_paths:
            missing.append("Extracted risk factors section is missing.")
        if not valuation_outputs:
            missing.append("No deterministic valuation result is available yet.")
        return missing

    def _key_risks(self, latest_metrics: dict[str, Any], risk_text: str, hard_avoid: bool) -> list[str]:
        risks = []
        if hard_avoid:
            risks.append("Potential ADR, China, VIE, or geography/corporate-structure exclusion flagged.")
        if _num(latest_metrics.get("sbc_percent_revenue")) is not None and latest_metrics["sbc_percent_revenue"] > 0.10:
            risks.append("Stock-based compensation is high relative to revenue.")
        if _num(latest_metrics.get("share_count_change")) is not None and latest_metrics["share_count_change"] > 0.05:
            risks.append("Share count is rising materially.")
        if _num(latest_metrics.get("fcf_margin")) is not None and latest_metrics["fcf_margin"] < 0:
            risks.append("Free cash flow margin is negative.")
        found = [keyword for keyword in RISK_KEYWORDS if keyword in risk_text.lower()]
        if found:
            risks.append("Risk factor text mentions: " + ", ".join(sorted(set(found))[:6]) + ".")
        if not risks:
            risks.append("No deterministic red flags were detected from available local metrics and risk text.")
        return risks

    def _profile_fit_score(self, company: Any, business_text: str, hard_avoid: bool) -> float:
        text = _combined_company_text(company, business_text)
        score = 45.0
        matches = sum(1 for keyword in FOCUS_KEYWORDS if keyword in text)
        if matches:
            score += min(35.0, matches * 8.0)
        if "software" in text or "subscription" in text or "cloud" in text:
            score += 10
        if hard_avoid:
            score -= 55
        if not text.strip():
            score -= 20
        return _clamp(score)

    def _business_quality_score(self, latest: dict[str, Any]) -> float:
        if not latest:
            return 20.0
        score = 40.0
        score += _threshold_score(_num(latest.get("operating_margin")), [(0.25, 18), (0.15, 12), (0.08, 6)])
        score += _threshold_score(_num(latest.get("fcf_margin")), [(0.20, 18), (0.10, 12), (0.05, 6)])
        score += _threshold_score(_num(latest.get("fcf_conversion_from_net_income")), [(1.0, 12), (0.8, 8), (0.5, 3)])
        score += _threshold_score(_num(latest.get("roic")), [(0.20, 14), (0.12, 10), (0.08, 5)])
        if _num(latest.get("sbc_percent_revenue")) is not None and latest["sbc_percent_revenue"] > 0.10:
            score -= 10
        if _num(latest.get("share_count_change")) is not None and latest["share_count_change"] > 0.05:
            score -= 8
        if _num(latest.get("free_cash_flow")) is not None and latest["free_cash_flow"] < 0:
            score -= 20
        return _clamp(score)

    def _growth_runway_score(self, latest: dict[str, Any], business_text: str) -> float:
        if not latest:
            return 20.0
        growth = _num(latest.get("revenue_growth_yoy"))
        score = 40.0
        if growth is None:
            score -= 10
        elif growth >= 0.25:
            score += 35
        elif growth >= 0.15:
            score += 25
        elif growth >= 0.08:
            score += 16
        elif growth >= 0:
            score += 6
        else:
            score -= 20
        thematic_matches = sum(1 for keyword in ("ai", "cloud", "cybersecurity", "semiconductor", "data center") if keyword in business_text.lower())
        score += min(18.0, thematic_matches * 6.0)
        return _clamp(score)

    def _valuation_sanity_score(self, latest: dict[str, Any], valuation_outputs: list[dict[str, Any]]) -> float:
        score = 35.0
        margins = [_num(output.get("marginOfSafety")) for output in valuation_outputs]
        margins = [margin for margin in margins if margin is not None]
        if margins:
            best_margin = max(margins)
            if best_margin >= 0.30:
                score += 30
            elif best_margin >= 0.10:
                score += 20
            elif best_margin >= 0:
                score += 10
            else:
                score -= 15
        p_fcf = _num(latest.get("price_to_free_cash_flow"))
        if p_fcf is not None:
            if p_fcf <= 25:
                score += 18
            elif p_fcf <= 40:
                score += 8
            elif p_fcf > 60:
                score -= 18
        pe = _num(latest.get("price_to_earnings"))
        if pe is not None:
            if pe <= 30:
                score += 8
            elif pe > 70:
                score -= 8
        ev_rev = _num(latest.get("ev_to_revenue"))
        if ev_rev is not None:
            if ev_rev <= 10:
                score += 6
            elif ev_rev > 20:
                score -= 10
        return _clamp(score)

    def _balance_sheet_score(self, latest: dict[str, Any]) -> float:
        if not latest:
            return 30.0
        score = 50.0
        debt_to_equity = _num(latest.get("debt_to_equity"))
        if debt_to_equity is None:
            score -= 5
        elif debt_to_equity <= 0.5:
            score += 25
        elif debt_to_equity <= 1.0:
            score += 15
        elif debt_to_equity > 2.0:
            score -= 25
        interest_coverage = _num(latest.get("interest_coverage"))
        if interest_coverage is not None:
            if interest_coverage >= 8:
                score += 10
            elif interest_coverage < 2:
                score -= 15
        cash = _num(latest.get("cash_and_equivalents"))
        debt = _num(latest.get("total_debt"))
        if cash is not None and debt is not None and cash >= debt:
            score += 10
        return _clamp(score)

    def _downside_risk_score(self, latest: dict[str, Any], risk_text: str, hard_avoid: bool) -> float:
        score = 70.0
        if hard_avoid:
            score -= 45
        text = risk_text.lower()
        keyword_hits = sum(1 for keyword in RISK_KEYWORDS if keyword in text)
        score -= min(25.0, keyword_hits * 4.0)
        if _num(latest.get("fcf_margin")) is not None and latest["fcf_margin"] < 0:
            score -= 20
        if _num(latest.get("debt_to_equity")) is not None and latest["debt_to_equity"] > 2.0:
            score -= 15
        return _clamp(score)

    def _evidence_freshness_score(
        self,
        latest_price: dict[str, Any],
        latest_filing: dict[str, Any],
        metrics: Any,
        business_paths: list[Path],
        risk_paths: list[Path],
    ) -> float:
        score = 100.0
        price_age = self._age_days(latest_price.get("date"))
        if price_age is None:
            score -= 35
        elif price_age > 45:
            score -= 40
        elif price_age > _stale_price_days(self.portfolio_dir):
            score -= 20
        filing_age = self._age_days(latest_filing.get("filingDate"))
        if filing_age is None:
            score -= 20
        elif filing_age > 500:
            score -= 20
        if not isinstance(metrics, dict) or not metrics.get("periods"):
            score -= 35
        if not business_paths:
            score -= 10
        if not risk_paths:
            score -= 10
        return _clamp(score)

    def _portfolio_fit_score(self, ticker: str, hard_avoid: bool, profile_context: bool) -> float:
        holdings = _portfolio_tickers(self.paths.holdings, "holdings")
        watchlist = _portfolio_tickers(self.paths.watchlist, "watchlist")
        score = 50.0
        if ticker in holdings:
            score -= 25
        if ticker in watchlist:
            score -= 10
        if profile_context:
            score += 20
        if hard_avoid:
            score -= 35
        return _clamp(score)

    def _promotion_assessment(
        self,
        scores: dict[str, float],
        total_score: float,
        hard_avoid: bool,
        missing_evidence: list[str],
    ) -> tuple[bool, str]:
        blockers = []
        if hard_avoid:
            blockers.append("profile exclusion or ADR/China/VIE warning")
        if scores["profile_fit"] < 65:
            blockers.append("profile fit below threshold")
        if scores["business_quality"] < 60:
            blockers.append("business quality below threshold")
        if scores["growth_runway"] < 50:
            blockers.append("growth runway below threshold")
        if scores["valuation_sanity"] < 40:
            blockers.append("valuation evidence or sanity below threshold")
        if scores["balance_sheet"] < 50:
            blockers.append("balance sheet below threshold")
        if scores["downside_risk"] < 45:
            blockers.append("downside risk score below threshold")
        if scores["evidence_freshness"] < 55:
            blockers.append("evidence freshness below threshold")
        if total_score < 60:
            blockers.append("weighted score below threshold")
        if len(missing_evidence) >= 4:
            blockers.append("too much missing evidence for promotion review")
        if blockers:
            return False, "Not a watchlist promotion candidate: " + "; ".join(blockers) + "."
        return True, "Promotion candidate for explicit user review because component scores clear the quality, profile, evidence, and risk gates."

    def _next_action(
        self,
        promotion_candidate: bool,
        scores: dict[str, float],
        missing_evidence: list[str],
        hard_avoid: bool,
    ) -> str:
        if hard_avoid:
            return "Reject or require explicit user override because profile exclusion was flagged."
        if promotion_candidate:
            return "Propose for explicit user watchlist review; do not add to watchlist without approval."
        if scores["evidence_freshness"] < 55:
            return "Refresh local research and filings before ranking further."
        if "No deterministic valuation result is available yet." in missing_evidence:
            return "Create explicit valuation assumptions and run deterministic valuation before promotion review."
        return "Keep in candidate queue for later review or defer with a specific evidence condition."

    def _judgment_summary(
        self,
        scores: dict[str, float],
        promotion_candidate: bool,
        hard_avoid: bool,
        missing_evidence: list[str],
    ) -> str:
        if hard_avoid:
            return "Profile exclusion is flagged; this should not be resurfaced casually."
        strengths = [name for name, value in scores.items() if value >= 70]
        weak = [name for name, value in scores.items() if value < 50]
        if promotion_candidate:
            return "Candidate deserves explicit watchlist review based on the current local evidence, subject to user approval."
        if weak:
            return "Candidate remains in triage because weak components need work: " + ", ".join(weak) + "."
        if missing_evidence:
            return "Candidate has plausible fit, but missing evidence prevents promotion review."
        if strengths:
            return "Candidate has some attractive components, but the promotion gate is not fully cleared."
        return "Candidate is screened but not currently differentiated enough for watchlist promotion review."

    def _age_days(self, value: Any) -> int | None:
        parsed = parse_iso_date(str(value or ""))
        if parsed is None:
            return None
        return (self.today - parsed).days

    def _load_watchlist(self) -> dict[str, Any]:
        data = read_json(self.paths.watchlist, None)
        if isinstance(data, dict):
            data.setdefault("schemaVersion", SCHEMA_VERSION)
            data.setdefault("watchlist", [])
            return data
        return {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "watchlist": []}

    def _validate_promotion_approval(self, *, ticker: str, candidate: dict[str, Any]) -> dict[str, Any]:
        if candidate.get("state") != "analyst_approved":
            raise ValueError("Promotion requires candidate state analyst_approved.")
        approval_path_value = str(candidate.get("approvalPath") or "")
        if not approval_path_value:
            raise ValueError("Promotion requires a current analyst approval artifact.")
        approval_path = _resolve_workspace_path(approval_path_value, self.cwd)
        if not approval_path.is_file():
            raise FileNotFoundError(f"Promotion approval artifact is missing: {approval_path}")
        approval = read_json(approval_path, None)
        if not isinstance(approval, dict):
            raise ValueError(f"Promotion approval artifact is invalid JSON object: {approval_path}")
        if str(approval.get("ticker") or "").upper() != ticker:
            raise ValueError("Promotion approval artifact ticker does not match candidate.")
        if approval.get("state") != "analyst_approved":
            raise ValueError("Promotion approval artifact must have state analyst_approved.")
        review_path = _resolve_workspace_path(
            str(candidate.get("agentReviewPath") or approval.get("agentReviewPath") or self.portfolio_dir / "agent_reviews" / f"{ticker}.json"),
            self.cwd,
        )
        brief_path = _resolve_workspace_path(
            str(candidate.get("agentBriefPath") or approval.get("agentBriefPath") or self.portfolio_dir / "agent_briefs" / f"{ticker}.md"),
            self.cwd,
        )
        if not review_path.is_file():
            raise FileNotFoundError(f"Promotion requires an existing agent review: {review_path}")
        if not brief_path.is_file():
            raise FileNotFoundError(f"Promotion requires an existing agent brief: {brief_path}")
        review = read_json(review_path, None)
        if not isinstance(review, dict):
            raise ValueError(f"Promotion agent review is invalid JSON object: {review_path}")
        verification = review.get("claimVerification")
        if not isinstance(verification, dict):
            raise ValueError("Promotion requires claimVerification on the agent review.")
        try:
            unsupported_count = int(verification.get("unsupportedCount"))
        except (TypeError, ValueError):
            raise ValueError("Promotion requires numeric claimVerification.unsupportedCount.") from None
        if unsupported_count != 0:
            raise ValueError("Promotion requires clean claimVerification unsupportedCount==0.")
        expected_hashes = approval.get("sourceHashes")
        if not isinstance(expected_hashes, dict):
            raise ValueError("Promotion approval artifact is missing sourceHashes.")
        actual_hashes = _approval_source_hashes(
            candidate=candidate,
            review_path=review_path,
            brief_path=brief_path,
            review=review,
        )
        mismatched = [
            key
            for key in ("candidates", "agentReview", "agentBrief", "claimVerification")
            if str(expected_hashes.get(key) or "") != actual_hashes[key]
        ]
        if mismatched:
            raise ValueError(
                "Promotion approval artifact is stale; source hash mismatch for "
                + ", ".join(sorted(mismatched))
                + "."
            )
        return {"approvalPath": str(approval_path), "sourceHashes": actual_hashes}

    def _render_brief(self, candidate: dict[str, Any], generated_at: str) -> str:
        lines = [
            f"# {candidate['ticker']} Candidate Brief",
            "",
            f"Generated: {generated_at}",
            f"Company: {candidate.get('companyName') or candidate['ticker']}",
            f"State: {candidate.get('state')}",
            f"Total score: {_display(candidate.get('totalScore'))}",
            "",
            "## Source Facts",
            "",
        ]
        facts = candidate.get("sourceFacts", [])
        if facts:
            lines.extend(_fact_line(fact) for fact in facts)
        else:
            lines.append("- No source facts available yet.")
        lines.extend(["", "## Deterministic Calculations", ""])
        scores = candidate.get("componentScores", {})
        if scores:
            for key in COMPONENT_SCORE_KEYS:
                lines.append(f"- {key}: {_display(scores.get(key))}")
        else:
            lines.append("- No component scores available yet.")
        calculations = candidate.get("deterministicCalculations", {})
        for key in ("priceAgeDays", "latestFilingAgeDays", "latestMetricsPeriod"):
            if calculations.get(key) is not None:
                lines.append(f"- {key}: {calculations.get(key)}")
        lines.extend(
            [
                "",
                "## Judgment Summary",
                "",
                candidate.get("judgmentSummary") or "No judgment summary available.",
                "",
                "## Key Risks",
                "",
            ]
        )
        key_risks = candidate.get("keyRisks", [])
        if key_risks:
            lines.extend(f"- {item}" for item in key_risks)
        else:
            lines.append("- None recorded.")
        lines.extend(["", "## Missing Evidence", ""])
        missing_evidence = candidate.get("missingEvidence", [])
        if missing_evidence:
            lines.extend(f"- {item}" for item in missing_evidence)
        else:
            lines.append("- None recorded.")
        lines.extend(
            [
                "",
                "## Next Action",
                "",
                candidate.get("nextAction") or "No next action recorded.",
                "",
                "## Watchlist Promotion Assessment",
                "",
                candidate.get("watchlistPromotionRationale") or "No promotion assessment recorded.",
                "",
                "## Source Artifacts",
                "",
            ]
        )
        artifacts = candidate.get("artifactRefs", [])
        if artifacts:
            lines.extend(_artifact_line(item) for item in artifacts)
        else:
            lines.append("- No artifacts recorded.")
        lines.append("")
        return "\n".join(lines)


def _new_candidate(ticker: str, now: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "companyName": ticker,
        "state": "discovered",
        "firstDiscoveredAt": now,
        "lastSeenAt": now,
        "lastUpdatedAt": now,
        "lastRefreshedAt": None,
        "lastScoredAt": None,
        "lastBriefedAt": None,
        "sources": [],
        "seenInRuns": [],
        "artifactRefs": [],
        "sourceFacts": [],
        "deterministicCalculations": {},
        "componentScores": {},
        "totalScore": None,
        "judgmentSummary": "",
        "keyRisks": [],
        "missingEvidence": [],
        "warnings": [],
        "nextAction": "Refresh local research and score candidate.",
        "watchlistPromotionCandidate": False,
        "watchlistPromotionRationale": "Not evaluated yet.",
    }


def _ensure_candidate(queue: dict[str, Any], ticker: str, now: str) -> dict[str, Any]:
    candidates = _candidate_map(queue)
    if ticker in candidates:
        return candidates[ticker]
    candidate = _new_candidate(ticker, now)
    queue.setdefault("candidates", []).append(candidate)
    return candidate


def _candidate_map(queue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("ticker", "")).upper(): item
        for item in queue.get("candidates", [])
        if isinstance(item, dict) and item.get("ticker")
    }


def _ranked(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        dict(candidate)
        for candidate in candidates
        if candidate.get("totalScore") is not None and candidate.get("state") != "rejected"
    ]
    return sorted(
        rows,
        key=lambda item: (
            bool(item.get("watchlistPromotionCandidate")),
            float(item.get("totalScore") or 0),
            str(item.get("ticker", "")),
        ),
        reverse=True,
    )


def _append_source(sources: list[dict[str, Any]], source: dict[str, Any], surfaced_at: str) -> None:
    name = str(source.get("name") or "unnamed")
    source_type = str(source.get("sourceType") or "configured")
    key = (name, source_type)
    existing = {(item.get("name"), item.get("sourceType")) for item in sources}
    if key in existing:
        return
    sources.append(
        {
            "name": name,
            "sourceType": source_type,
            "description": str(source.get("description") or ""),
            "surfacedAt": surfaced_at,
            "notes": str(source.get("notes") or ""),
        }
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


def _generated_run_id(command: str) -> str:
    stamp = datetime.now(UTC).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{command}"


def _unique_run_id(run_dir: Path, run_id: str) -> str:
    safe = _safe_file_stem(run_id)
    candidate = safe
    index = 2
    while (run_dir / f"{candidate}.json").exists():
        candidate = f"{safe}-{index}"
        index += 1
    return candidate


def _load_sources_from_path(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, None)
    if isinstance(data, list):
        return [{"name": path.stem, "sourceType": "source_file", "description": str(path), "tickers": data}]
    if not isinstance(data, dict):
        raise ValueError(f"Discovery source file must contain a JSON object or array: {path}")
    if isinstance(data.get("screens"), list):
        return [dict(item) for item in data["screens"] if isinstance(item, dict)]
    if isinstance(data.get("tickers"), list):
        return [
            {
                "name": str(data.get("name") or path.stem),
                "sourceType": str(data.get("sourceType") or "source_file"),
                "description": str(data.get("description") or path),
                "tickers": data["tickers"],
            }
        ]
    return []


def _screen_log_item(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(source.get("name") or ""),
        "sourceType": str(source.get("sourceType") or ""),
        "description": str(source.get("description") or ""),
        "tickerCount": len(source.get("tickers", []) if isinstance(source.get("tickers"), list) else []),
    }


def _safe_read_json(path: Path, default: Any) -> Any:
    try:
        return read_json(path, default)
    except (json.JSONDecodeError, OSError):
        return default


def _latest_period_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [row for row in rows if isinstance(row, dict)]
    if not valid_rows:
        return {}

    def key(row: dict[str, Any]) -> tuple[int, int, str]:
        period = str(row.get("period", ""))
        year = int(period[:4]) if period[:4].isdigit() else int(row.get("fiscalYear") or 0)
        rank = 0
        text = period.upper()
        for candidate_rank, marker in [(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4")]:
            if marker in text:
                rank = candidate_rank
                break
        if "FY" in text:
            rank = 5
        if "TTM" in text:
            rank = 6
        return year, rank, period

    return sorted(valid_rows, key=key)[-1]


def _latest_price_row(rows: list[dict[str, Any]], ticker: str) -> dict[str, Any]:
    dated = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_ticker = str(row.get("ticker", "") or "").upper()
        if row_ticker not in {"", ticker.upper()}:
            continue
        row_date = parse_iso_date(str(row.get("date", "") or ""))
        if row_date is not None and _price_value(row) is not None:
            dated.append((row_date, row))
    return sorted(dated, key=lambda item: item[0])[-1][1] if dated else {}


def _latest_filing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dated = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        filed = parse_iso_date(str(row.get("filingDate", "") or ""))
        if filed is not None:
            dated.append((filed, row))
    return sorted(dated, key=lambda item: item[0])[-1][1] if dated else {}


def _read_text_sample(paths: list[Path], limit: int = 50000) -> str:
    chunks = []
    remaining = limit
    for path in paths[-3:]:
        if remaining <= 0:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append(text[:remaining])
        remaining -= len(chunks[-1])
    return "\n".join(chunks)


def _profile_context(company: Any, business_text: str) -> bool:
    text = _combined_company_text(company, business_text)
    return any(keyword in text for keyword in FOCUS_KEYWORDS)


def _has_hard_avoid(company: Any, business_text: str) -> bool:
    text = _combined_company_text(company, business_text)
    return any(keyword in text for keyword in HARD_AVOID_KEYWORDS)


def _combined_company_text(company: Any, business_text: str) -> str:
    parts = [business_text]
    if isinstance(company, dict):
        parts.extend(str(company.get(key) or "") for key in ("name", "title", "exchange", "sector", "industry"))
    return " " + " ".join(parts).lower() + " "


def _threshold_score(value: float | None, thresholds: list[tuple[float, float]]) -> float:
    if value is None:
        return 0.0
    for threshold, points in thresholds:
        if value >= threshold:
            return points
    return 0.0


def _weighted_total(scores: dict[str, float]) -> float:
    weights = {
        "profile_fit": 1.2,
        "business_quality": 1.3,
        "growth_runway": 1.0,
        "valuation_sanity": 1.0,
        "balance_sheet": 0.9,
        "downside_risk": 1.0,
        "evidence_freshness": 0.9,
        "portfolio_fit": 0.7,
    }
    numerator = sum(scores[key] * weights[key] for key in COMPONENT_SCORE_KEYS)
    denominator = sum(weights.values())
    return numerator / denominator


def _portfolio_tickers(path: Path, key: str) -> set[str]:
    data = read_json(path, {}) or {}
    rows = data.get(key, []) if isinstance(data, dict) else []
    return {
        str(item.get("ticker", "")).upper()
        for item in rows
        if isinstance(item, dict) and item.get("ticker")
    }


def _stale_price_days(portfolio_dir: Path) -> int:
    rules = read_json(portfolio_dir / "rules.json", default_rules()) or default_rules()
    try:
        return int(rules.get("signals", {}).get("stalePriceDays", 10))
    except (TypeError, ValueError):
        return 10


def _price_value(row: dict[str, Any]) -> float | None:
    return _first_num(row, "close", "adjustedClose")


def _first_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return round(max(minimum, min(maximum, value)), 1)


def _artifact(kind: str, path: Path, uri: str) -> dict[str, Any]:
    return {"kind": kind, "path": str(path), "uri": uri, "exists": path.exists()}


def _fact(label: str, value: Any, source_path: Path, uri: str) -> dict[str, Any]:
    return {"label": label, "value": value, "sourcePath": str(source_path), "uri": uri}


def _fact_line(fact: dict[str, Any]) -> str:
    return f"- {fact.get('label')}: {_display(fact.get('value'))} ({fact.get('sourcePath')}; {fact.get('uri')})"


def _artifact_line(item: dict[str, Any]) -> str:
    status = "exists" if item.get("exists") else "missing"
    return f"- {item.get('kind')}: {item.get('path')} ({item.get('uri')}; {status})"


def _approval_source_hashes(
    *,
    candidate: dict[str, Any],
    review_path: Path,
    brief_path: Path,
    review: dict[str, Any],
) -> dict[str, str]:
    return {
        "candidates": stable_hash(_candidate_hash_payload(candidate)),
        "agentReview": file_hash(review_path),
        "agentBrief": file_hash(brief_path),
        "claimVerification": stable_hash(review.get("claimVerification", {})),
    }


def _candidate_hash_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "approvalSourceHashes"}


def _display(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _safe_file_stem(value: str) -> str:
    cleaned = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "-" for char in value).strip("._-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "run"


def _resolve_workspace_path(path: str | Path, cwd: str | Path = ".") -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(cwd) / resolved
    return resolved.resolve()
