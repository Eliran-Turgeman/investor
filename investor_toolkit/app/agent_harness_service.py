from __future__ import annotations

from typing import Any

from ..agent_harness.approvals import approve_candidate
from ..agent_harness.claim_verifier import verify_review_file
from ..agent_harness import AgentHarness, DeterministicDryRunClient, OpenAIResponsesClient
from ..agent_harness.llm import DEFAULT_OPENAI_MODEL
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult, warning_from_text


class AgentHarnessService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def run_discovery_research(
        self,
        tickers: list[str] | None = None,
        limit: int = 5,
        run_id: str | None = None,
        discover: bool = True,
        include_default_screens: bool = True,
        refresh_research: bool = False,
        offline: bool = False,
        provider: str = "openai",
        model: str | None = None,
        reasoning_effort: str = "low",
        verbosity: str = "low",
        max_context_chars: int = 18000,
        apply_agent_states: bool = False,
    ) -> OperationResult:
        if refresh_research and not offline:
            self.context.require_sec_user_agent()
        llm_client = self._client(
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
        harness = AgentHarness(
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            research_root=self.context.research_root,
            assumptions_dir=self.context.assumptions_dir,
            valuations_dir=self.context.valuations_dir,
            llm_client=llm_client,
            max_context_chars=max_context_chars,
        )
        result = harness.run_discovery_research(
            tickers=tickers,
            limit=limit,
            run_id=run_id,
            discover=discover,
            include_default_screens=include_default_screens,
            refresh_research=refresh_research,
            offline=offline,
            provider_label=provider,
            apply_agent_states=apply_agent_states,
        )
        return self._wrap("agents.run_discovery_research", result)

    def verify_claims(self, ticker: str) -> OperationResult:
        review_path = self.context.portfolio_dir / "agent_reviews" / f"{ticker.upper()}.json"
        result = verify_review_file(review_path, cwd=self.context.workspace_root)
        return self._wrap("agents.verify_claims", result)

    def approve(self, ticker: str, state: str, reason: str, reviewer: str = "analyst") -> OperationResult:
        result = approve_candidate(
            ticker=ticker,
            state=state,
            reason=reason,
            reviewer=reviewer,
            portfolio_dir=self.context.portfolio_dir,
            cwd=self.context.workspace_root,
        )
        return self._wrap("agents.approve", result)

    def _client(
        self,
        provider: str,
        model: str | None,
        reasoning_effort: str,
        verbosity: str,
    ) -> Any:
        provider = provider.lower()
        if provider == "openai":
            return OpenAIResponsesClient(
                model=model or DEFAULT_OPENAI_MODEL,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
            )
        if provider == "dry-run":
            return DeterministicDryRunClient(model=model or "dry-run")
        raise ValueError("provider must be openai or dry-run")

    def _wrap(self, operation: str, result: dict[str, Any]) -> OperationResult:
        warnings = [warning_from_text(str(item)) for item in result.get("warnings", [])]
        artifacts = [ref for ref in self.catalog.agent_harness_artifacts() if ref.exists]
        return OperationResult(
            operation=operation,
            data=result,
            warnings=warnings,
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
        )
