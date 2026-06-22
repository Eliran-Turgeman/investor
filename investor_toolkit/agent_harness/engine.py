from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..audit import AuditLedger
from ..discovery import DiscoveryHarness
from ..discovery.schemas import SchemaValidationError
from ..utils import normalize_ticker, read_json, utc_now_iso, write_json, write_text
from .claim_verifier import verify_review_claims
from .llm import AgentLlmClient, AgentLlmResponse, TokenUsage
from .schemas import (
    PROMPT_VERSION,
    blocked_agent_content,
    schema_for_agent,
    validate_agent_response,
)


SCHEMA_VERSION = "1.0"

ROLE_AGENTS = (
    "business_quality",
    "valuation_skeptic",
    "risk_bear_case",
    "portfolio_fit",
)

STATE_PROPOSALS = {
    "research_more",
    "defer",
    "reject",
    "promote_candidate",
}

CASUAL_AGENT_SELECTION_EXCLUDED_STATES = {
    "rejected",
    "deferred",
    "analyst_approved",
    "analyst_rejected",
    "needs_more_evidence",
    "promoted_to_watchlist",
}


@dataclass(slots=True)
class AgentHarnessPaths:
    portfolio_dir: Path
    agent_runs_dir: Path
    agent_reviews_dir: Path
    agent_briefs_dir: Path
    audit_db: Path
    candidates: Path
    watchlist: Path
    holdings: Path


class AgentHarness:
    def __init__(
        self,
        cwd: str | Path = ".",
        portfolio_dir: str | Path = "portfolio",
        research_root: str | Path | None = None,
        assumptions_dir: str | Path = "assumptions",
        valuations_dir: str | Path = "valuations",
        llm_client: AgentLlmClient | None = None,
        max_context_chars: int = 18000,
    ) -> None:
        self.cwd = Path(cwd).resolve()
        self.portfolio_dir = _resolve_workspace_path(portfolio_dir, self.cwd)
        self.research_root = _resolve_workspace_path(research_root or "research", self.cwd)
        self.assumptions_dir = _resolve_workspace_path(assumptions_dir, self.cwd)
        self.valuations_dir = _resolve_workspace_path(valuations_dir, self.cwd)
        self.discovery = DiscoveryHarness(
            cwd=self.cwd,
            portfolio_dir=self.portfolio_dir,
            research_root=self.research_root,
            assumptions_dir=self.assumptions_dir,
            valuations_dir=self.valuations_dir,
        )
        self.llm_client = llm_client
        self.max_context_chars = max_context_chars

    @property
    def paths(self) -> AgentHarnessPaths:
        return AgentHarnessPaths(
            portfolio_dir=self.portfolio_dir,
            agent_runs_dir=self.portfolio_dir / "agent_runs",
            agent_reviews_dir=self.portfolio_dir / "agent_reviews",
            agent_briefs_dir=self.portfolio_dir / "agent_briefs",
            audit_db=self.portfolio_dir / "audit.db",
            candidates=self.portfolio_dir / "candidates.json",
            watchlist=self.portfolio_dir / "watchlist.json",
            holdings=self.portfolio_dir / "holdings.json",
        )

    def run_discovery_research(
        self,
        tickers: list[str] | None = None,
        limit: int = 5,
        run_id: str | None = None,
        discover: bool = True,
        include_default_screens: bool = True,
        refresh_research: bool = False,
        offline: bool = False,
        provider_label: str | None = None,
        apply_agent_states: bool = False,
    ) -> dict[str, Any]:
        if self.llm_client is None:
            raise ValueError("AgentHarness requires an LLM client.")
        now = utc_now_iso()
        resolved_run_id = _unique_run_id(self.paths.agent_runs_dir, run_id or _generated_run_id("agents"))
        ledger = AuditLedger(self.paths.audit_db)
        normalized_tickers = [normalize_ticker(ticker) for ticker in tickers or []]
        warnings: list[str] = []
        if apply_agent_states:
            warnings.append(
                "applyAgentStates is ignored in the institutional harness; agents write agentSuggestedState only."
            )
        discovery_result: dict[str, Any] | None = None
        if discover:
            discovery_result = self.discovery.discover(
                tickers=normalized_tickers,
                include_default_screens=include_default_screens,
                screen_name="agent_harness",
                run_id=f"{resolved_run_id}-discover",
            )
            ledger.record_tool_call(
                run_id=resolved_run_id,
                tool_name="discovery.discover",
                inputs={"tickers": normalized_tickers, "includeDefaultScreens": include_default_screens},
                outputs=discovery_result,
            )
        selected = self._select_candidates(normalized_tickers, limit)
        reviews = []
        total_usage = TokenUsage()
        for ticker in selected:
            if refresh_research:
                refresh_result = self.discovery.refresh(ticker, offline=offline, refresh=not offline)
                ledger.record_tool_call(
                    run_id=resolved_run_id,
                    tool_name="discovery.refresh",
                    inputs={"ticker": ticker, "offline": offline, "refresh": not offline},
                    outputs=refresh_result,
                    status="error" if refresh_result.get("errors") else "ok",
                    error="; ".join(str(item) for item in refresh_result.get("errors", [])),
                )
                warnings.extend(str(item) for item in refresh_result.get("warnings", []))
                if refresh_result.get("errors"):
                    warnings.extend(str(item) for item in refresh_result.get("errors", []))
            score_result = self.discovery.score(ticker)
            ledger.record_tool_call(
                run_id=resolved_run_id,
                tool_name="discovery.score",
                inputs={"ticker": ticker},
                outputs=score_result,
            )
            self.discovery.brief(ticker)
            ledger.record_tool_call(
                run_id=resolved_run_id,
                tool_name="discovery.brief",
                inputs={"ticker": ticker},
                outputs={"ticker": ticker},
            )
            candidate = score_result["candidate"]
            before_state = str(candidate.get("state") or "")
            review = self._run_candidate_agents(ticker, candidate, run_id=resolved_run_id, ledger=ledger)
            verification = verify_review_claims(review, cwd=self.cwd)
            review["claimVerification"] = verification
            for check in verification.get("checks", []):
                ledger.record_claim_check(
                    run_id=resolved_run_id,
                    ticker=ticker,
                    agent_name=str(check.get("agent") or ""),
                    claim=check.get("claim", {}),
                    status=str(check.get("status") or ""),
                    reason=str(check.get("reason") or ""),
                    source_path=str(check.get("sourcePath") or ""),
                    uri=str(check.get("uri") or ""),
                )
            if verification.get("unsupportedCount", 0) > 0:
                chair = review.get("committeeChair", {}).get("content", {})
                if isinstance(chair, dict) and chair.get("suggestedState") == "promote_candidate":
                    chair["suggestedState"] = "research_more"
                    chair["promotionRationale"] = (
                        "Promotion blocked because claim verification found unsupported claims or stale deterministic data."
                    )
                    chair.setdefault("missingEvidence", []).append("Repair unsupported claims or stale data before promotion review.")
                    warnings.append(f"{ticker}: promotion blocked by claim verification")
            total_usage.add(_usage_from_review(review))
            self._write_candidate_review(review)
            self._write_agent_brief(review)
            candidate_after_state = self._record_candidate_agent_review(ticker, review, apply_agent_states=apply_agent_states)
            ledger.record_candidate_event(
                run_id=resolved_run_id,
                ticker=ticker,
                event_type="agent_review",
                before_state=before_state,
                after_state=candidate_after_state,
                payload=self._review_summary(review),
            )
            reviews.append(self._review_summary(review))
        run_log = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": resolved_run_id,
            "generatedAt": now,
            "provider": provider_label or self.llm_client.provider,
            "model": self.llm_client.model,
            "command": "agents.run_discovery_research",
            "discover": discover,
            "includeDefaultScreens": include_default_screens,
            "refreshResearch": refresh_research,
            "offline": offline,
            "applyAgentStates": apply_agent_states,
            "selectedTickers": selected,
            "discoveryRunId": discovery_result.get("runId") if isinstance(discovery_result, dict) else None,
            "reviews": reviews,
            "warnings": _unique_strings(warnings),
            "tokenUsage": total_usage.to_dict(),
            "promptVersion": PROMPT_VERSION,
            "artifacts": {
                "agentRun": str(self.paths.agent_runs_dir / f"{resolved_run_id}.json"),
                "agentReviewsDir": str(self.paths.agent_reviews_dir),
                "agentBriefsDir": str(self.paths.agent_briefs_dir),
                "candidates": str(self.paths.candidates),
                "auditDb": str(self.paths.audit_db),
            },
        }
        run_path = self.paths.agent_runs_dir / f"{resolved_run_id}.json"
        write_json(run_path, run_log)
        ledger.record_run(
            run_id=resolved_run_id,
            command="agents.run_discovery_research",
            provider=provider_label or self.llm_client.provider,
            model=self.llm_client.model,
            prompt_version=PROMPT_VERSION,
            config={
                "discover": discover,
                "includeDefaultScreens": include_default_screens,
                "refreshResearch": refresh_research,
                "offline": offline,
                "applyAgentStates": apply_agent_states,
            },
            inputs={"tickers": normalized_tickers, "limit": limit},
            outputs=run_log,
            token_usage=total_usage.to_dict(),
            warnings=_unique_strings(warnings),
        )
        return run_log

    def _select_candidates(self, requested_tickers: list[str], limit: int) -> list[str]:
        if requested_tickers:
            return requested_tickers[:limit]
        queue = self.discovery.load_candidates()
        candidates = [
            item
            for item in queue.get("candidates", [])
            if isinstance(item, dict)
            and item.get("ticker")
            and item.get("state") not in CASUAL_AGENT_SELECTION_EXCLUDED_STATES
        ]
        scored = []
        for candidate in candidates:
            score = candidate.get("totalScore")
            if score is None:
                score_result = self.discovery.score(str(candidate["ticker"]))
                score = score_result["candidate"].get("totalScore")
            scored.append((float(score or 0), str(candidate["ticker"]).upper()))
        return [ticker for _, ticker in sorted(scored, reverse=True)[:limit]]

    def _run_candidate_agents(
        self,
        ticker: str,
        candidate: dict[str, Any],
        run_id: str,
        ledger: AuditLedger,
    ) -> dict[str, Any]:
        context = self._candidate_context(ticker, candidate)
        role_outputs = []
        blocked_errors = []
        for role in ROLE_AGENTS:
            response, status, error = self._complete_validated_agent(
                agent_name=role,
                instructions=_role_instructions(role),
                input_text=context,
            )
            if status != "ok":
                blocked_errors.append(f"{role}: {error or status}")
            ledger.record_agent_call(
                run_id=run_id,
                ticker=ticker,
                agent_name=role,
                provider=response.provider,
                model=response.model,
                prompt_version=PROMPT_VERSION,
                inputs={"context": context, "schema": schema_for_agent(role)},
                outputs=response.content,
                usage=response.usage.to_dict(),
                status=status,
                error=error,
            )
            role_outputs.append({"agent": role, **response.to_dict()})
        chair_input = context + "\n\nRole agent outputs:\n" + json.dumps(
            [item["content"] for item in role_outputs],
            indent=2,
            sort_keys=True,
        )
        chair, chair_status, chair_error = self._complete_validated_agent(
            agent_name="committee_chair",
            instructions=_chair_instructions(),
            input_text=chair_input,
        )
        if chair_status != "ok":
            blocked_errors.append(f"committee_chair: {chair_error or chair_status}")
        ledger.record_agent_call(
            run_id=run_id,
            ticker=ticker,
            agent_name="committee_chair",
            provider=chair.provider,
            model=chair.model,
            prompt_version=PROMPT_VERSION,
            inputs={"context": chair_input, "schema": schema_for_agent("committee_chair")},
            outputs=chair.content,
            usage=chair.usage.to_dict(),
            status=chair_status,
            error=chair_error,
        )
        chair_content = dict(chair.content)
        if blocked_errors:
            chair_content["suggestedState"] = "research_more"
            chair_content["promotionRationale"] = "Blocked because one or more agent outputs failed strict validation."
            chair_content.setdefault("missingEvidence", []).extend(blocked_errors)
        suggested = str(chair_content.get("suggestedState") or "research_more")
        if suggested not in STATE_PROPOSALS:
            chair_content["suggestedState"] = "research_more"
            chair_content.setdefault("missingEvidence", []).append(f"Unsupported suggested state was normalized from {suggested!r}.")
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now_iso(),
            "ticker": ticker,
            "companyName": candidate.get("companyName") or ticker,
            "provider": self.llm_client.provider,
            "model": self.llm_client.model,
            "promptVersion": PROMPT_VERSION,
            "deterministicCandidate": candidate,
            "roleOutputs": role_outputs,
            "committeeChair": {**chair.to_dict(), "content": chair_content},
            "tokenUsage": _sum_usage([*role_outputs, chair.to_dict()]).to_dict(),
            "sourcePaths": _source_paths(candidate),
        }

    def _complete_validated_agent(
        self,
        agent_name: str,
        instructions: str,
        input_text: str,
    ) -> tuple[AgentLlmResponse, str, str]:
        try:
            response = self.llm_client.complete_json(
                agent_name=agent_name,
                instructions=instructions,
                input_text=input_text,
                schema_hint=schema_for_agent(agent_name),
            )
            validate_agent_response(agent_name, response.content)
            return response, "ok", ""
        except (SchemaValidationError, ValueError) as exc:
            blocked = blocked_agent_content(agent_name, str(exc))
            return (
                AgentLlmResponse(
                    content=blocked,
                    rawText=json.dumps(blocked, sort_keys=True),
                    model=getattr(self.llm_client, "model", "unknown"),
                    provider=getattr(self.llm_client, "provider", "unknown"),
                    usage=TokenUsage(),
                ),
                "blocked",
                str(exc),
            )

    def _candidate_context(self, ticker: str, candidate: dict[str, Any]) -> str:
        packet = {
            "guardrails": [
                "Do not provide buy/sell/hold recommendations.",
                "Separate source facts, deterministic calculations, and judgment.",
                "Never invent missing financial numbers.",
                "Treat watchlist promotion as a proposal requiring explicit user approval.",
                "The investor prefers software, cybersecurity, semiconductors, AI infrastructure, and AI hardware-adjacent businesses.",
                "Avoid China/ADRs, unclear business drivers, cheap-only ideas without quality, and businesses outside circle of competence.",
            ],
            "candidate": candidate,
            "portfolioSnapshot": {
                "holdingsTickers": _portfolio_tickers(self.paths.holdings, "holdings"),
                "watchlistTickers": _portfolio_tickers(self.paths.watchlist, "watchlist"),
            },
            "filingSnippets": self._filing_snippets(ticker),
        }
        text = json.dumps(packet, indent=2, sort_keys=True, allow_nan=False)
        return text[: self.max_context_chars]

    def _filing_snippets(self, ticker: str) -> dict[str, str]:
        base = self.research_root / ticker / "extracted"
        snippets: dict[str, str] = {}
        if not base.exists():
            return snippets
        for section in ("business.md", "risk-factors.md", "mdna.md"):
            matches = sorted(base.rglob(section))
            if not matches:
                continue
            try:
                snippets[section] = matches[-1].read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
        return snippets

    def _write_candidate_review(self, review: dict[str, Any]) -> Path:
        path = self.paths.agent_reviews_dir / f"{review['ticker']}.json"
        write_json(path, review)
        return path

    def _write_agent_brief(self, review: dict[str, Any]) -> Path:
        path = self.paths.agent_briefs_dir / f"{review['ticker']}.md"
        chair = review.get("committeeChair", {}).get("content", {})
        lines = [
            f"# {review['ticker']} Agent Review",
            "",
            f"Generated: {review['generatedAt']}",
            f"Provider/model: {review['provider']} / {review['model']}",
            f"Suggested state: {chair.get('suggestedState', 'research_more')}",
            "",
            "## Committee Summary",
            "",
            str(chair.get("judgmentSummary") or chair.get("summary") or "No summary returned."),
            "",
            "## Promotion Rationale",
            "",
            str(chair.get("promotionRationale") or "No promotion rationale returned."),
            "",
            "## Role Outputs",
            "",
        ]
        for output in review.get("roleOutputs", []):
            content = output.get("content", {})
            lines.extend(
                [
                    f"### {output.get('agent')}",
                    "",
                    f"- Verdict: {content.get('verdict', 'n/a')}",
                    f"- Summary: {content.get('summary', 'n/a')}",
                    f"- Confidence: {content.get('confidence', 'n/a')}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Token Usage",
                "",
                f"- Input tokens: {review['tokenUsage'].get('inputTokens', 0)}",
                f"- Output tokens: {review['tokenUsage'].get('outputTokens', 0)}",
                f"- Total tokens: {review['tokenUsage'].get('totalTokens', 0)}",
                "",
            ]
        )
        write_text(path, "\n".join(lines))
        return path

    def _record_candidate_agent_review(
        self,
        ticker: str,
        review: dict[str, Any],
        apply_agent_states: bool = False,
    ) -> str:
        queue = self.discovery.load_candidates()
        candidate = next(
            (item for item in queue.get("candidates", []) if isinstance(item, dict) and str(item.get("ticker", "")).upper() == ticker),
            None,
        )
        if candidate is None:
            return ""
        chair = review.get("committeeChair", {}).get("content", {})
        suggested = str(chair.get("suggestedState") or "research_more")
        candidate["lastAgentReviewedAt"] = review["generatedAt"]
        candidate["agentReviewPath"] = str(self.paths.agent_reviews_dir / f"{ticker}.json")
        candidate["agentBriefPath"] = str(self.paths.agent_briefs_dir / f"{ticker}.md")
        candidate["agentSuggestedState"] = suggested
        candidate["agentJudgmentSummary"] = str(chair.get("judgmentSummary") or chair.get("summary") or "")
        candidate["agentPromotionRationale"] = str(chair.get("promotionRationale") or "")
        candidate["agentTokenUsage"] = review.get("tokenUsage", {})
        if candidate.get("state") not in {
            "rejected",
            "deferred",
            "analyst_approved",
            "analyst_rejected",
            "needs_more_evidence",
            "promoted_to_watchlist",
        }:
            candidate["state"] = "agent_reviewed"
            candidate["lastUpdatedAt"] = review["generatedAt"]
        queue["updatedAt"] = utc_now_iso()
        self.discovery._write_candidates(queue)
        return str(candidate.get("state") or "")

    def _review_summary(self, review: dict[str, Any]) -> dict[str, Any]:
        chair = review.get("committeeChair", {}).get("content", {})
        ticker = str(review.get("ticker", ""))
        return {
            "ticker": ticker,
            "suggestedState": chair.get("suggestedState"),
            "judgmentSummary": chair.get("judgmentSummary") or chair.get("summary"),
            "agentReviewPath": str(self.paths.agent_reviews_dir / f"{ticker}.json"),
            "agentBriefPath": str(self.paths.agent_briefs_dir / f"{ticker}.md"),
            "tokenUsage": review.get("tokenUsage", {}),
        }


def _role_instructions(role: str) -> str:
    base = (
        "You are one role in a stock research committee. Use only the provided evidence packet. "
        "Separate source facts from judgment. Do not invent numbers. Do not give buy/sell/hold advice. "
        "Return JSON only."
    )
    role_guidance = {
        "business_quality": "Assess business understandability, quality, moat clues, growth runway, and quality-vs-cheapness.",
        "valuation_skeptic": "Assess valuation evidence, valuation gaps, stale assumptions, and whether cheapness is supported by quality.",
        "risk_bear_case": "Build the bear case, downside risks, missing evidence, and reasons to reject or defer.",
        "portfolio_fit": "Assess circle-of-competence fit, portfolio/watchlist duplication, focus areas, and exclusions such as China/ADRs.",
    }
    return base + " " + role_guidance.get(role, "")


def _chair_instructions() -> str:
    return (
        "You are the committee chair for a stock discovery workflow. Synthesize the role agent outputs and deterministic "
        "candidate record into a triage proposal. Valid suggestedState values are research_more, defer, reject, "
        "and promote_candidate. Promotion means explicit user review only, not adding to watchlist and not a trading "
        "recommendation. Return JSON only."
    )


def _role_schema_hint(role: str) -> dict[str, Any]:
    return schema_for_agent(role)


def _chair_schema_hint() -> dict[str, Any]:
    return schema_for_agent("committee_chair")


def _usage_from_review(review: dict[str, Any]) -> TokenUsage:
    usage = review.get("tokenUsage", {})
    return TokenUsage(
        inputTokens=int(usage.get("inputTokens", 0)),
        outputTokens=int(usage.get("outputTokens", 0)),
        totalTokens=int(usage.get("totalTokens", 0)),
        cachedInputTokens=int(usage.get("cachedInputTokens", 0)),
    )


def _sum_usage(items: list[dict[str, Any]]) -> TokenUsage:
    total = TokenUsage()
    for item in items:
        usage = item.get("usage", {})
        total.add(
            TokenUsage(
                inputTokens=int(usage.get("inputTokens", 0)),
                outputTokens=int(usage.get("outputTokens", 0)),
                totalTokens=int(usage.get("totalTokens", 0)),
                cachedInputTokens=int(usage.get("cachedInputTokens", 0)),
            )
        )
    return total


def _source_paths(candidate: dict[str, Any]) -> list[str]:
    paths = []
    for artifact in candidate.get("artifactRefs", []):
        if isinstance(artifact, dict) and artifact.get("exists") and artifact.get("path"):
            paths.append(str(artifact["path"]))
    for fact in candidate.get("sourceFacts", []):
        if isinstance(fact, dict) and fact.get("sourcePath"):
            paths.append(str(fact["sourcePath"]))
    return _unique_strings(paths)


def _portfolio_tickers(path: Path, key: str) -> list[str]:
    data = read_json(path, {}) or {}
    rows = data.get(key, []) if isinstance(data, dict) else []
    return sorted(
        {
            normalize_ticker(str(item.get("ticker", "")))
            for item in rows
            if isinstance(item, dict) and item.get("ticker")
        }
    )


def _unique_strings(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
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
