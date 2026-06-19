from __future__ import annotations

from pathlib import Path
from typing import Any

from ..portfolio import (
    build_portfolio_signals,
    export_portfolio_workbook,
    import_portfolio_workbook,
    init_portfolio,
    refresh_portfolio,
    run_portfolio_valuations,
)
from ..portfolio.engine import collect_portfolio_valuations, load_portfolio_inputs, load_rules
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult, warning_from_text


class PortfolioService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def context_snapshot(self) -> OperationResult:
        inputs = load_portfolio_inputs(cwd=self.context.workspace_root, portfolio_dir=self.context.portfolio_dir)
        rules = load_rules(cwd=self.context.workspace_root, portfolio_dir=self.context.portfolio_dir)
        valuations = collect_portfolio_valuations(
            inputs.tickers,
            cwd=self.context.workspace_root,
            valuations_dir=self.context.valuations_dir,
        )
        artifacts = [ref for ref in self.catalog.portfolio_artifacts() if ref.exists]
        return OperationResult(
            operation="portfolio.context",
            data={
                "tickers": inputs.tickers,
                "holdings": inputs.holdings,
                "watchlist": inputs.watchlist,
                "assumptions": inputs.assumptions,
                "rules": rules,
                "valuations": valuations,
            },
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
        )

    def init(self, workbook_path: str | Path | None = None) -> OperationResult:
        result = init_portfolio(
            workbook_path or self.context.portfolio_dir / "portfolio.xlsx",
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
        )
        return self._wrap("portfolio.init", result)

    def import_workbook(self, workbook_path: str | Path | None = None) -> OperationResult:
        result = import_portfolio_workbook(
            workbook_path or self.context.portfolio_dir / "portfolio.xlsx",
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
        )
        return self._wrap("portfolio.import", result)

    def export_workbook(self, workbook_path: str | Path | None = None) -> OperationResult:
        result = export_portfolio_workbook(
            workbook_path or self.context.portfolio_dir / "portfolio.xlsx",
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            assumptions_dir=self.context.assumptions_dir,
            valuations_dir=self.context.valuations_dir,
            research_root=self.context.research_root,
        )
        return self._wrap("portfolio.export", result)

    def value(self, include_sensitivity: bool = False) -> OperationResult:
        result = run_portfolio_valuations(
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            assumptions_dir=self.context.assumptions_dir,
            valuations_dir=self.context.valuations_dir,
            research_root=self.context.research_root,
            include_sensitivity=include_sensitivity,
        )
        status = "blocked" if result.get("errors") else "ok"
        return self._wrap("portfolio.value", result, status=status)

    def signals(self, write: bool = True, workbook_path: str | Path | None = None) -> OperationResult:
        result = build_portfolio_signals(
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            valuations_dir=self.context.valuations_dir,
            research_root=self.context.research_root,
            write=write,
        )
        if workbook_path:
            export_portfolio_workbook(
                workbook_path,
                cwd=self.context.workspace_root,
                portfolio_dir=self.context.portfolio_dir,
                assumptions_dir=self.context.assumptions_dir,
                valuations_dir=self.context.valuations_dir,
                research_root=self.context.research_root,
            )
        return self._wrap("portfolio.signals", result)

    def refresh(
        self,
        workbook_path: str | Path | None = None,
        offline: bool = False,
        refresh: bool = False,
        include_sensitivity: bool = False,
    ) -> OperationResult:
        if not offline:
            self.context.require_sec_user_agent()
        result = refresh_portfolio(
            cwd=self.context.workspace_root,
            portfolio_dir=self.context.portfolio_dir,
            workbook_path=workbook_path or self.context.portfolio_dir / "portfolio.xlsx",
            research_root=self.context.research_root,
            assumptions_dir=self.context.assumptions_dir,
            valuations_dir=self.context.valuations_dir,
            offline=offline,
            refresh=refresh,
            include_sensitivity=include_sensitivity,
        )
        errors = _portfolio_refresh_errors(result)
        return self._wrap("portfolio.refresh", result, status="blocked" if errors else "ok", errors=errors)

    def _wrap(
        self,
        operation: str,
        result: dict[str, Any],
        status: str = "ok",
        errors: list[str] | None = None,
    ) -> OperationResult:
        artifacts = [ref for ref in self.catalog.portfolio_artifacts() if ref.exists]
        warnings = []
        for item in result.get("warnings", []):
            warnings.append(warning_from_text(str(item)))
        for item in result.get("errors", []):
            warnings.append(warning_from_text(str(item), code="ERROR"))
        return OperationResult(
            operation=operation,
            status=status,
            data=result,
            warnings=warnings,
            errors=errors or list(result.get("errors", [])),
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
        )


def _portfolio_refresh_errors(result: dict[str, Any]) -> list[str]:
    errors = [
        row.get("error", "")
        for row in result.get("research", [])
        if isinstance(row, dict) and row.get("status") == "error" and row.get("error")
    ]
    errors.extend(result.get("valuation", {}).get("errors", []))
    return [str(error) for error in errors if error]

