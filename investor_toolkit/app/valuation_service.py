from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..utils import write_json
from ..valuation import (
    compare_valuations,
    export_agent_context,
    init_assumptions_file,
    load_assumptions,
    run_valuation,
    validate_assumptions_file,
)
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult, OperationWarning, path_text


class ValuationService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def init_assumptions(
        self,
        ticker: str,
        model: str,
        scenario: str,
        output_path: str | Path,
    ) -> OperationResult:
        path = init_assumptions_file(
            ticker,
            model=model,
            scenario=scenario,
            output_path=output_path,
            cwd=self.context.workspace_root,
            research_root=self.context.research_root,
        )
        return OperationResult(
            operation="valuation.init_assumptions",
            data={
                "ticker": ticker.upper(),
                "model": model,
                "scenario": scenario or "base",
                "assumptionsPath": str(path),
            },
            sourcePaths=[str(path)],
        )

    def validate_assumptions(self, path: str | Path, expected_ticker: str | None = None) -> OperationResult:
        report = validate_assumptions_file(
            path,
            cwd=self.context.workspace_root,
            research_root=self.context.research_root,
            expected_ticker=expected_ticker,
        )
        warnings = [
            OperationWarning(code=warning.code, message=warning.message)
            for warning in report.warnings
        ]
        errors = list(report.errors)
        return OperationResult(
            operation="valuation.validate_assumptions",
            status="ok" if report.ok else "blocked",
            data={
                "path": path_text(self.context.resolve_path(path)),
                "ok": report.ok,
                "errors": errors,
                "warnings": [warning.to_dict() for warning in warnings],
            },
            warnings=warnings,
            errors=errors,
            sourcePaths=[path_text(self.context.resolve_path(path))],
        )

    def run(
        self,
        ticker: str,
        assumptions_path: str | Path,
        include_sensitivity: bool = False,
        include_debug: bool = False,
        output_path: str | Path | None = None,
        export_context: bool = False,
    ) -> OperationResult:
        result = run_valuation(
            ticker,
            assumptions_path,
            cwd=self.context.workspace_root,
            research_root=self.context.research_root,
            include_sensitivity=include_sensitivity,
            include_debug=include_debug,
        )
        source_paths = [path_text(self.context.resolve_path(assumptions_path))]
        if export_context:
            paths = export_agent_context(
                result,
                load_assumptions(assumptions_path, cwd=self.context.workspace_root),
                cwd=self.context.workspace_root,
            )
            result["agentContext"] = paths
            source_paths.extend(str(value) for value in paths.values())
        if output_path:
            output = self.context.resolve_path(output_path)
            write_json(output, result)
            source_paths.append(str(output))
        return OperationResult(
            operation="valuation.run",
            data=result,
            warnings=_warnings_from_result(result),
            sourcePaths=source_paths,
        )

    def compare(
        self,
        ticker: str,
        assumption_paths: list[str | Path],
        include_sensitivity: bool = False,
        output_path: str | Path | None = None,
    ) -> OperationResult:
        comparison = compare_valuations(
            ticker,
            assumption_paths,
            cwd=self.context.workspace_root,
            research_root=self.context.research_root,
            include_sensitivity=include_sensitivity,
        )
        source_paths = [path_text(self.context.resolve_path(path)) for path in assumption_paths]
        if output_path:
            output = self.context.resolve_path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            source_paths.append(str(output))
        return OperationResult(
            operation="valuation.compare",
            data=comparison,
            warnings=_warnings_from_comparison(comparison),
            sourcePaths=source_paths,
        )


def _warnings_from_result(result: dict[str, Any]) -> list[OperationWarning]:
    warnings = []
    for item in result.get("warnings", []):
        if isinstance(item, dict):
            warnings.append(
                OperationWarning(
                    code=str(item.get("code") or "VALUATION_WARNING"),
                    message=str(item.get("message") or item),
                )
            )
        elif item:
            warnings.append(OperationWarning(code="VALUATION_WARNING", message=str(item)))
    return warnings


def _warnings_from_comparison(comparison: dict[str, Any]) -> list[OperationWarning]:
    warnings = []
    for result in comparison.get("results", []):
        warnings.extend(_warnings_from_result(result))
    return warnings

