from __future__ import annotations

from pathlib import Path
from typing import Any

from ..discovery import DiscoveryHarness
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult, warning_from_text


class DiscoveryService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def discover(
        self,
        tickers: list[str] | None = None,
        source_file: str | Path | None = None,
        config_file: str | Path | None = None,
        screen_name: str = "manual",
        include_default_screens: bool = True,
        resurface_rejected: bool = False,
        run_id: str | None = None,
    ) -> OperationResult:
        result = self._harness().discover(
            tickers=tickers,
            source_file=source_file,
            config_file=config_file,
            screen_name=screen_name,
            include_default_screens=include_default_screens,
            resurface_rejected=resurface_rejected,
            run_id=run_id,
        )
        return self._wrap("discovery.discover", result)

    def refresh(self, ticker: str, offline: bool = False, refresh: bool = False) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        result = self._harness().refresh(ticker, offline=offline, refresh=refresh)
        return self._wrap(
            "discovery.refresh",
            result,
            status="blocked" if result.get("errors") else "ok",
            errors=list(result.get("errors", [])),
        )

    def score(self, ticker: str) -> OperationResult:
        return self._wrap("discovery.score", self._harness().score(ticker))

    def brief(self, ticker: str) -> OperationResult:
        return self._wrap("discovery.brief", self._harness().brief(ticker))

    def reject(self, ticker: str, reason: str) -> OperationResult:
        return self._wrap("discovery.reject", self._harness().reject(ticker, reason))

    def defer(self, ticker: str, reason: str) -> OperationResult:
        return self._wrap("discovery.defer", self._harness().defer(ticker, reason))

    def propose_promotions(self, limit: int = 10) -> OperationResult:
        return self._wrap("discovery.propose_promotions", self._harness().propose_promotions(limit=limit))

    def promote(self, ticker: str, approved: bool = False) -> OperationResult:
        return self._wrap("discovery.promote", self._harness().promote(ticker, approved=approved))

    def review_watchlist(self, offline: bool = False, refresh: bool = False) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        return self._wrap("discovery.review_watchlist", self._harness().review_watchlist(offline=offline, refresh=refresh))

    def _harness(self) -> DiscoveryHarness:
        return DiscoveryHarness(
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            research_root=self.context.research_root,
            assumptions_dir=self.context.assumptions_dir,
            valuations_dir=self.context.valuations_dir,
            today=self.context.today,
        )

    def _wrap(
        self,
        operation: str,
        result: dict[str, Any],
        status: str = "ok",
        errors: list[str] | None = None,
    ) -> OperationResult:
        warnings = [warning_from_text(str(item)) for item in result.get("warnings", [])]
        artifacts = [ref for ref in self.catalog.discovery_artifacts() if ref.exists]
        return OperationResult(
            operation=operation,
            status=status,
            data=result,
            warnings=warnings,
            errors=errors or [],
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
        )
