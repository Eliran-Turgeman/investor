from __future__ import annotations

from pathlib import Path
from typing import Any

from ..profile import ProfileInitRequest, init_profile
from .artifact_catalog import ArtifactCatalog
from .context import AppContext
from .schemas import OperationResult


class ProfileService:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self.catalog = ArtifactCatalog(context)

    def init(
        self,
        *,
        benchmark: str = "S&P 500",
        horizon_min_years: int = 5,
        horizon_max_years: int = 10,
        ideas_per_month: int = 3,
        required_margin_of_safety: float = 0.30,
        max_position_size: float = 0.30,
        focus_areas: list[str] | tuple[str, ...] | None = None,
        avoid_areas: list[str] | tuple[str, ...] | None = None,
        external_exposures: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        other_portfolios: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        external_exposure_affects_active_portfolio: bool = False,
        overwrite: bool = False,
    ) -> OperationResult:
        _validate_profile_inputs(
            horizon_min_years=horizon_min_years,
            horizon_max_years=horizon_max_years,
            ideas_per_month=ideas_per_month,
            required_margin_of_safety=required_margin_of_safety,
            max_position_size=max_position_size,
        )
        request = ProfileInitRequest(
            portfolio_dir=Path(self.context.portfolio_dir),
            today=self.context.today,
            benchmark=benchmark,
            horizon_min_years=horizon_min_years,
            horizon_max_years=horizon_max_years,
            ideas_per_month=ideas_per_month,
            required_margin_of_safety=required_margin_of_safety,
            max_position_size=max_position_size,
            focus_areas=tuple(focus_areas or ()),
            avoid_areas=tuple(avoid_areas or ()),
            external_exposures=tuple(external_exposures or ()),
            other_portfolios=tuple(other_portfolios or ()),
            external_exposure_affects_active_portfolio=external_exposure_affects_active_portfolio,
            overwrite=overwrite,
        )
        result = init_profile(request)
        artifacts = [ref for ref in self.catalog.profile_artifacts() if ref.exists]
        return OperationResult(
            operation="profile.init",
            data=result,
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
            nextActions=[
                "Read investor://profile/policy before portfolio analysis.",
                "Use short candidate briefs first; go deep only when requested.",
            ],
        )

    def status(self) -> OperationResult:
        status = self.catalog.profile_status()
        artifacts = [self.catalog.profile_status_resource()]
        artifacts.extend(ref for ref in self.catalog.profile_artifacts() if ref.exists)
        return OperationResult(
            operation="profile.status",
            data=status,
            sourcePaths=[ref.path for ref in self.catalog.profile_artifacts() if ref.exists],
            artifacts=artifacts,
            nextActions=list(status["nextActions"]),
        )

    def context_snapshot(self) -> OperationResult:
        artifacts = [ref for ref in self.catalog.profile_artifacts() if ref.exists]
        return OperationResult(
            operation="profile.context",
            data={"status": self.catalog.profile_status(), "artifacts": [ref.to_dict() for ref in artifacts]},
            sourcePaths=[ref.path for ref in artifacts],
            artifacts=artifacts,
        )


def _validate_profile_inputs(
    *,
    horizon_min_years: int,
    horizon_max_years: int,
    ideas_per_month: int,
    required_margin_of_safety: float,
    max_position_size: float,
) -> None:
    if horizon_min_years < 1 or horizon_max_years < 1 or horizon_max_years < horizon_min_years:
        raise ValueError("horizon must be a positive range with max >= min")
    if ideas_per_month < 1:
        raise ValueError("ideas_per_month must be at least 1")
    if required_margin_of_safety < 0 or required_margin_of_safety > 0.8:
        raise ValueError("required_margin_of_safety must be between 0 and 0.8")
    if max_position_size < 0 or max_position_size > 1:
        raise ValueError("max_position_size must be between 0 and 1")
