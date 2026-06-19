from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..storage import ResearchStorage
from ..utils import normalize_ticker, parse_iso_date, read_json, safe_divide, utc_now_iso, write_json
from ..valuation import load_assumptions, run_valuation
from ..valuation.engine import load_local_financial_data
from ..workflow import ResearchWorkflow
from .xlsx import read_xlsx, write_xlsx


SCHEMA_VERSION = "1.0"
DEFAULT_PORTFOLIO_DIR = "portfolio"
DEFAULT_WORKBOOK = "portfolio/portfolio.xlsx"
DEFAULT_ASSUMPTIONS_DIR = "assumptions"
DEFAULT_VALUATIONS_DIR = "valuations"

HOLDINGS_COLUMNS = [
    ("ticker", "Ticker"),
    ("shares", "Shares"),
    ("costBasisPerShare", "Cost Basis Per Share"),
    ("targetAllocation", "Target Allocation"),
    ("maxAllocation", "Max Allocation"),
    ("thesisStatus", "Thesis Status"),
    ("userFairValuePerShare", "User Fair Value"),
    ("requiredMarginOfSafety", "Required Margin Of Safety"),
    ("notes", "Notes"),
]

WATCHLIST_COLUMNS = [
    ("ticker", "Ticker"),
    ("targetEntryPrice", "Target Entry Price"),
    ("priority", "Priority"),
    ("userFairValuePerShare", "User Fair Value"),
    ("requiredMarginOfSafety", "Required Margin Of Safety"),
    ("notes", "Notes"),
]

ASSUMPTION_COLUMNS = [
    ("ticker", "Ticker"),
    ("scenario", "Scenario"),
    ("model", "Model"),
    ("assumptionsPath", "Assumptions Path"),
    ("resultPath", "Result Path"),
    ("userFairValuePerShare", "User Fair Value"),
    ("requiredMarginOfSafety", "Required Margin Of Safety"),
    ("notes", "Notes"),
]

SIGNAL_COLUMNS = [
    ("ticker", "Ticker"),
    ("label", "Signal"),
    ("severity", "Severity"),
    ("reason", "Reason"),
    ("currentSharePrice", "Current Price"),
    ("priceDate", "Price Date"),
    ("decisionFairValuePerShare", "Decision Fair Value"),
    ("decisionValueSource", "Decision Value Source"),
    ("marginOfSafety", "Margin Of Safety"),
    ("requiredMarginOfSafety", "Required Margin Of Safety"),
    ("conservativeFairValuePerShare", "Conservative Fair Value"),
    ("baseFairValuePerShare", "Base Fair Value"),
    ("aggressiveFairValuePerShare", "Aggressive Fair Value"),
    ("userFairValuePerShare", "User Fair Value"),
    ("qualityScore", "Quality Score"),
    ("dataQuality", "Data Quality"),
    ("warnings", "Warnings"),
    ("sourceFiles", "Source Files"),
]

VALUATION_COLUMNS = [
    ("ticker", "Ticker"),
    ("scenario", "Scenario"),
    ("model", "Model"),
    ("currentSharePrice", "Current Price"),
    ("priceDate", "Price Date"),
    ("fairValuePerShare", "Fair Value"),
    ("upsideDownside", "Upside Downside"),
    ("marginOfSafety", "Margin Of Safety"),
    ("requiredMarginOfSafety", "Required Margin Of Safety"),
    ("meetsRequiredMarginOfSafety", "Meets Required Margin"),
    ("warningCount", "Warning Count"),
    ("sourcePath", "Source Path"),
]

DATA_QUALITY_COLUMNS = [
    ("ticker", "Ticker"),
    ("status", "Status"),
    ("priceDate", "Price Date"),
    ("priceAgeDays", "Price Age Days"),
    ("latestMetricsPeriod", "Latest Metrics Period"),
    ("issues", "Issues"),
    ("warnings", "Warnings"),
]

AUDIT_COLUMNS = [
    ("key", "Key"),
    ("value", "Value"),
]


@dataclass(slots=True)
class PortfolioPaths:
    workbook: Path
    portfolio_dir: Path
    holdings: Path
    watchlist: Path
    assumptions: Path
    rules: Path
    signals: Path
    valuation_audit: Path


def init_portfolio(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
) -> dict[str, Any]:
    paths = _paths(cwd=cwd, workbook_path=workbook_path, portfolio_dir=portfolio_dir)
    paths.portfolio_dir.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(paths.holdings, _default_holdings())
    _write_json_if_missing(paths.watchlist, _default_watchlist())
    _write_json_if_missing(paths.assumptions, _default_assumption_overrides())
    _write_json_if_missing(paths.rules, default_rules())
    export_portfolio_workbook(paths.workbook, cwd=cwd, portfolio_dir=paths.portfolio_dir)
    return _summary(
        "portfolio initialized",
        workbook=str(paths.workbook),
        portfolioDir=str(paths.portfolio_dir),
        holdings=str(paths.holdings),
        watchlist=str(paths.watchlist),
        rules=str(paths.rules),
    )


def import_portfolio_workbook(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
) -> dict[str, Any]:
    paths = _paths(cwd=cwd, workbook_path=workbook_path, portfolio_dir=portfolio_dir)
    sheets = read_xlsx(paths.workbook)
    missing_sheets = [name for name in ("Holdings", "Watchlist", "Assumptions") if name not in sheets]
    if missing_sheets:
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(missing_sheets)}")
    holdings = _records_from_sheet(sheets["Holdings"], HOLDINGS_COLUMNS, kind="holding", sheet_name="Holdings")
    watchlist = _records_from_sheet(sheets["Watchlist"], WATCHLIST_COLUMNS, kind="watchlist", sheet_name="Watchlist")
    assumptions = _records_from_sheet(sheets["Assumptions"], ASSUMPTION_COLUMNS, kind="assumption", sheet_name="Assumptions")
    write_json(paths.holdings, {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "holdings": holdings})
    write_json(paths.watchlist, {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "watchlist": watchlist})
    write_json(
        paths.assumptions,
        {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "assumptions": assumptions},
    )
    _write_json_if_missing(paths.rules, default_rules())
    return _summary(
        "portfolio workbook imported",
        workbook=str(paths.workbook),
        holdingsImported=len(holdings),
        watchlistImported=len(watchlist),
        assumptionsImported=len(assumptions),
    )


def export_portfolio_workbook(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
    assumptions_dir: str | Path = DEFAULT_ASSUMPTIONS_DIR,
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
    research_root: str | Path | None = None,
) -> dict[str, Any]:
    paths = _paths(cwd=cwd, workbook_path=workbook_path, portfolio_dir=portfolio_dir)
    paths.portfolio_dir.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(paths.holdings, _default_holdings())
    _write_json_if_missing(paths.watchlist, _default_watchlist())
    _write_json_if_missing(paths.assumptions, _default_assumption_overrides())
    _write_json_if_missing(paths.rules, default_rules())

    inputs = load_portfolio_inputs(cwd=cwd, portfolio_dir=paths.portfolio_dir)
    valuations = collect_portfolio_valuations(inputs.tickers, cwd=cwd, valuations_dir=valuations_dir)
    signals = build_portfolio_signals(
        cwd=cwd,
        portfolio_dir=paths.portfolio_dir,
        valuations_dir=valuations_dir,
        research_root=research_root,
        write=False,
    )
    write_xlsx(
        paths.workbook,
        [
            _sheet("Holdings", _records_to_rows(inputs.holdings, HOLDINGS_COLUMNS), widths=[14, 12, 18, 18, 16, 16, 18, 24, 48]),
            _sheet("Watchlist", _records_to_rows(inputs.watchlist, WATCHLIST_COLUMNS), widths=[14, 18, 12, 18, 24, 48]),
            _sheet(
                "Assumptions",
                _records_to_rows(_assumption_sheet_rows(inputs, cwd, assumptions_dir, valuations_dir), ASSUMPTION_COLUMNS),
                widths=[14, 16, 20, 42, 42, 18, 24, 48],
            ),
            _sheet("Valuations", _records_to_rows(valuations, VALUATION_COLUMNS), widths=[14, 16, 20, 16, 14, 16, 16, 18, 24, 20, 14, 48]),
            _sheet("Signals", _records_to_rows(signals["rows"], SIGNAL_COLUMNS), widths=[14, 20, 14, 56, 16, 14, 18, 24, 18, 24, 18, 18, 18, 18, 14, 14, 56, 64]),
            _sheet("Portfolio", _portfolio_rows(inputs, signals["rows"]), widths=[24, 24, 24, 24, 48]),
            _sheet("Data Quality", _records_to_rows(signals["dataQuality"], DATA_QUALITY_COLUMNS), widths=[14, 16, 14, 14, 20, 56, 56]),
            _sheet("Audit", _records_to_rows(_audit_rows(paths, inputs, valuations, signals), AUDIT_COLUMNS), widths=[28, 80]),
        ],
    )
    return _summary("portfolio workbook exported", workbook=str(paths.workbook), tickers=len(inputs.tickers))


def run_portfolio_valuations(
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
    assumptions_dir: str | Path = DEFAULT_ASSUMPTIONS_DIR,
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
    research_root: str | Path | None = None,
    include_sensitivity: bool = False,
) -> dict[str, Any]:
    inputs = load_portfolio_inputs(cwd=cwd, portfolio_dir=portfolio_dir)
    assumption_paths = discover_assumption_paths(inputs, cwd=cwd, assumptions_dir=assumptions_dir)
    output_dir = _resolve_workspace_path(valuations_dir, cwd)
    rows = []
    warnings = []
    errors = []
    valued = 0
    used_result_names: set[str] = set()
    for ticker in inputs.tickers:
        ticker_paths = assumption_paths.get(ticker, [])
        if not ticker_paths:
            warnings.append(f"{ticker}: no assumptions file found")
            continue
        for assumptions_path in ticker_paths:
            try:
                assumptions = load_assumptions(assumptions_path, cwd=cwd)
                scenario = _safe_filename_part(str(assumptions.get("scenario") or "base"))
                model = _safe_filename_part(str(assumptions.get("model") or "model"))
                result = run_valuation(
                    ticker,
                    assumptions_path,
                    cwd=cwd,
                    research_root=research_root,
                    include_sensitivity=include_sensitivity,
                    include_debug=False,
                )
                result["portfolioSource"] = {
                    "assumptionsPath": str(assumptions_path),
                    "assumptionsModifiedAt": _mtime_iso(assumptions_path),
                }
                result_path = _unique_valuation_result_path(output_dir, ticker, scenario, model, used_result_names)
                write_json(result_path, result)
                valued += 1
                rows.append(
                    {
                        "ticker": ticker,
                        "scenario": result.get("scenario"),
                        "assumptionsPath": str(assumptions_path),
                        "resultPath": str(result_path),
                        "status": "ok",
                    }
                )
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                message = f"{ticker}: {assumptions_path}: {exc}"
                errors.append(message)
                rows.append({"ticker": ticker, "assumptionsPath": str(assumptions_path), "status": "error", "error": message})
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now_iso(),
        "valuedCount": valued,
        "warnings": warnings,
        "errors": errors,
        "rows": rows,
    }
    paths = _paths(cwd=cwd, portfolio_dir=portfolio_dir)
    write_json(paths.valuation_audit, audit)
    return audit


def build_portfolio_signals(
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
    research_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    paths = _paths(cwd=cwd, portfolio_dir=portfolio_dir)
    inputs = load_portfolio_inputs(cwd=cwd, portfolio_dir=paths.portfolio_dir)
    rules = load_rules(cwd=cwd, portfolio_dir=paths.portfolio_dir)
    rows = []
    data_quality_rows = []
    for ticker in inputs.tickers:
        valuations = _valuation_results_for_ticker(ticker, cwd=cwd, valuations_dir=valuations_dir)
        user_value = inputs.user_fair_value(ticker)
        input_required_margin = inputs.required_margin(ticker)
        required_margin = (
            input_required_margin
            if input_required_margin is not None
            else _rule_float(rules, "requiredMarginOfSafety", 0.25)
        )
        signal = _build_signal_row(
            ticker=ticker,
            valuations=valuations,
            user_fair_value=user_value,
            required_margin=required_margin,
            rules=rules,
            cwd=cwd,
            research_root=research_root,
        )
        rows.append(signal)
        data_quality_rows.append(_data_quality_row(signal))
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now_iso(),
        "rows": rows,
        "dataQuality": data_quality_rows,
    }
    if write:
        write_json(paths.signals, output)
    return output


def refresh_portfolio(
    cwd: str | Path = ".",
    portfolio_dir: str | Path | None = None,
    workbook_path: str | Path | None = None,
    research_root: str | Path | None = None,
    assumptions_dir: str | Path = DEFAULT_ASSUMPTIONS_DIR,
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
    offline: bool = False,
    refresh: bool = False,
    include_sensitivity: bool = False,
) -> dict[str, Any]:
    inputs = load_portfolio_inputs(cwd=cwd, portfolio_dir=portfolio_dir)
    workflow = ResearchWorkflow(Path(cwd), research_root=research_root)
    research_rows = []
    for ticker in inputs.tickers:
        try:
            storage = ResearchStorage(cwd, research_root=research_root)
            if storage.company_dir(ticker).exists():
                result = workflow.ingest(ticker, offline=offline, refresh=refresh)
            else:
                result = workflow.start(ticker, offline=offline, refresh=refresh)
            research_rows.append({"ticker": ticker, "status": "ok", "messages": result.messages, "warnings": result.warnings})
        except (FileNotFoundError, ValueError) as exc:
            research_rows.append({"ticker": ticker, "status": "error", "error": str(exc)})
    valuation_audit = run_portfolio_valuations(
        cwd=cwd,
        portfolio_dir=portfolio_dir,
        assumptions_dir=assumptions_dir,
        valuations_dir=valuations_dir,
        research_root=research_root,
        include_sensitivity=include_sensitivity,
    )
    signals = build_portfolio_signals(
        cwd=cwd,
        portfolio_dir=portfolio_dir,
        valuations_dir=valuations_dir,
        research_root=research_root,
        write=True,
    )
    if workbook_path:
        export_portfolio_workbook(
            workbook_path,
            cwd=cwd,
            portfolio_dir=portfolio_dir,
            assumptions_dir=assumptions_dir,
            valuations_dir=valuations_dir,
            research_root=research_root,
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now_iso(),
        "research": research_rows,
        "valuation": valuation_audit,
        "signals": {"count": len(signals["rows"])},
    }


class PortfolioInputs:
    def __init__(
        self,
        holdings: list[dict[str, Any]],
        watchlist: list[dict[str, Any]],
        assumptions: list[dict[str, Any]],
    ) -> None:
        self.holdings = holdings
        self.watchlist = watchlist
        self.assumptions = assumptions
        self.tickers = sorted(
            {
                str(item.get("ticker", "")).upper()
                for item in [*holdings, *watchlist, *assumptions]
                if item.get("ticker")
            }
        )

    def user_fair_value(self, ticker: str) -> float | None:
        return _first_float_for_ticker(
            ticker,
            self.assumptions,
            self.holdings,
            self.watchlist,
            field="userFairValuePerShare",
        )

    def required_margin(self, ticker: str) -> float | None:
        return _first_float_for_ticker(
            ticker,
            self.assumptions,
            self.holdings,
            self.watchlist,
            field="requiredMarginOfSafety",
        )


def load_portfolio_inputs(cwd: str | Path = ".", portfolio_dir: str | Path | None = None) -> PortfolioInputs:
    paths = _paths(cwd=cwd, portfolio_dir=portfolio_dir)
    holdings = read_json(paths.holdings, _default_holdings())
    watchlist = read_json(paths.watchlist, _default_watchlist())
    assumptions = read_json(paths.assumptions, _default_assumption_overrides())
    return PortfolioInputs(
        holdings=_normalize_records(holdings.get("holdings", []), kind="holding") if isinstance(holdings, dict) else [],
        watchlist=_normalize_records(watchlist.get("watchlist", []), kind="watchlist") if isinstance(watchlist, dict) else [],
        assumptions=_normalize_records(assumptions.get("assumptions", []), kind="assumption") if isinstance(assumptions, dict) else [],
    )


def load_rules(cwd: str | Path = ".", portfolio_dir: str | Path | None = None) -> dict[str, Any]:
    paths = _paths(cwd=cwd, portfolio_dir=portfolio_dir)
    rules = read_json(paths.rules, default_rules())
    resolved = rules if isinstance(rules, dict) else default_rules()
    _validate_rules(resolved)
    return resolved


def default_rules() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": utc_now_iso(),
        "signals": {
            "requiredMarginOfSafety": 0.25,
            "watchMarginOfSafety": 0.10,
            "stalePriceDays": 10,
            "scenarioOrder": ["conservative", "base", "aggressive"],
        },
        "quality": {
            "minimumScoreForOpportunity": 60,
            "minimumScoreForStrongOpportunity": 75,
        },
    }


def collect_portfolio_valuations(
    tickers: list[str],
    cwd: str | Path = ".",
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
) -> list[dict[str, Any]]:
    rows = []
    for ticker in tickers:
        for item in _valuation_results_for_ticker(ticker, cwd=cwd, valuations_dir=valuations_dir):
            result = item["result"]
            valuation = result.get("valuation", {}) if isinstance(result, dict) else {}
            market = result.get("market", {}) if isinstance(result, dict) else {}
            rows.append(
                {
                    "ticker": result.get("ticker", ticker),
                    "scenario": result.get("scenario"),
                    "model": result.get("model"),
                    "currentSharePrice": valuation.get("currentSharePrice"),
                    "priceDate": market.get("priceDate"),
                    "fairValuePerShare": valuation.get("fairValuePerShare"),
                    "upsideDownside": valuation.get("upsideDownside"),
                    "marginOfSafety": valuation.get("marginOfSafety"),
                    "requiredMarginOfSafety": valuation.get("requiredMarginOfSafety"),
                    "meetsRequiredMarginOfSafety": valuation.get("meetsRequiredMarginOfSafety"),
                    "warningCount": len(result.get("warnings", [])),
                    "sourcePath": item["path"],
                }
            )
    return rows


def discover_assumption_paths(
    inputs: PortfolioInputs,
    cwd: str | Path = ".",
    assumptions_dir: str | Path = DEFAULT_ASSUMPTIONS_DIR,
) -> dict[str, list[Path]]:
    root = _resolve_workspace_path(assumptions_dir, cwd)
    by_ticker: dict[str, list[Path]] = {ticker: [] for ticker in inputs.tickers}
    for item in inputs.assumptions:
        path_value = str(item.get("assumptionsPath") or "").strip()
        if not path_value:
            continue
        path = _resolve_workspace_path(path_value, cwd)
        ticker = str(item.get("ticker", "")).upper()
        if ticker in by_ticker and path not in by_ticker[ticker]:
            by_ticker[ticker].append(path)
    if root.exists():
        for ticker in inputs.tickers:
            for path in sorted(root.glob(f"{ticker}.*.json")):
                if path not in by_ticker[ticker]:
                    by_ticker[ticker].append(path)
    return by_ticker


def render_portfolio_summary(result: dict[str, Any]) -> str:
    lines = [str(result.get("message") or "portfolio command completed")]
    for key, value in result.items():
        if key == "message":
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            lines.append(f"- {key}: {value}")
    if "warnings" in result and result["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result["warnings"])
    if "errors" in result and result["errors"]:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result["errors"])
    return "\n".join(lines) + "\n"


def _build_signal_row(
    ticker: str,
    valuations: list[dict[str, Any]],
    user_fair_value: float | None,
    required_margin: float,
    rules: dict[str, Any],
    cwd: str | Path,
    research_root: str | Path | None,
) -> dict[str, Any]:
    valuation_by_scenario = _best_valuation_by_scenario(valuations)
    base = _valuation_fair_value(valuation_by_scenario.get("base"))
    conservative = _valuation_fair_value(valuation_by_scenario.get("conservative"))
    aggressive = _valuation_fair_value(valuation_by_scenario.get("aggressive"))
    decision_value = user_fair_value if user_fair_value is not None else base
    decision_source = "user fair value" if user_fair_value is not None else "base valuation"
    if decision_value is None:
        decision_value = conservative or aggressive
        decision_source = "available scenario valuation" if decision_value is not None else "missing"

    price, price_date = _price_from_valuations_or_local(ticker, valuations, cwd=cwd, research_root=research_root)
    data = load_local_financial_data(ticker, cwd=cwd, research_root=research_root, require=False)
    quality_score, quality_warnings = _quality_score(data)
    source_files = [str(item["path"]) for item in valuations]
    warnings = (
        _valuation_warnings(valuations)
        + _duplicate_scenario_warnings(valuations)
        + _valuation_freshness_warnings(valuations)
        + quality_warnings
    )
    errors = []
    stale_days = _rule_int(rules, "stalePriceDays", 10)
    price_age = _price_age_days(price_date)
    if price is None:
        errors.append("missing current price")
    if price_date is None:
        warnings.append("missing price date")
    elif price_age is not None and price_age > stale_days:
        errors.append(f"latest price is stale ({price_age} days old)")
    if decision_value is None or decision_value <= 0:
        errors.append("missing usable fair value")

    if errors:
        label = "No decision"
        severity = "blocked"
        reason = "; ".join(errors)
        margin = None
    else:
        assert price is not None
        assert decision_value is not None
        margin = safe_divide(decision_value - price, decision_value)
        watch_margin = _rule_float(rules, "watchMarginOfSafety", 0.10)
        min_quality = _quality_rule_float(rules, "minimumScoreForOpportunity", 60)
        strong_quality = _quality_rule_float(rules, "minimumScoreForStrongOpportunity", 75)
        if conservative is not None and price <= conservative * (1 - required_margin) and quality_score >= strong_quality:
            label = "Strong opportunity"
            severity = "positive"
            reason = "price is below conservative fair value after required margin of safety"
        elif margin is not None and margin >= required_margin and quality_score >= min_quality:
            label = "Opportunity"
            severity = "positive"
            reason = f"{decision_source} clears the required margin of safety"
        elif margin is not None and margin >= watch_margin:
            label = "Watch"
            severity = "neutral"
            reason = f"{decision_source} has some margin of safety but does not clear the required threshold"
        elif aggressive is not None and price > aggressive:
            label = "Review: above range"
            severity = "warning"
            reason = "price is above aggressive fair value scenario"
        elif margin is not None and margin < 0:
            label = "Review"
            severity = "warning"
            reason = f"price is above {decision_source}"
        else:
            label = "Fairly valued"
            severity = "neutral"
            reason = "price is near the available fair value estimate"

    return {
        "ticker": ticker,
        "label": label,
        "severity": severity,
        "reason": reason,
        "currentSharePrice": price,
        "priceDate": price_date,
        "decisionFairValuePerShare": decision_value,
        "decisionValueSource": decision_source,
        "marginOfSafety": margin,
        "requiredMarginOfSafety": required_margin,
        "conservativeFairValuePerShare": conservative,
        "baseFairValuePerShare": base,
        "aggressiveFairValuePerShare": aggressive,
        "userFairValuePerShare": user_fair_value,
        "qualityScore": quality_score,
        "latestMetricsPeriod": data.latest_metrics.get("period") if data.latest_metrics else "",
        "dataQuality": "blocked" if errors else "ok",
        "warnings": "; ".join(warnings),
        "sourceFiles": "; ".join(source_files),
    }


def _data_quality_row(signal: dict[str, Any]) -> dict[str, Any]:
    price_age = _price_age_days(signal.get("priceDate"))
    return {
        "ticker": signal.get("ticker"),
        "status": signal.get("dataQuality"),
        "priceDate": signal.get("priceDate"),
        "priceAgeDays": price_age,
        "latestMetricsPeriod": signal.get("latestMetricsPeriod"),
        "issues": signal.get("reason") if signal.get("dataQuality") == "blocked" else "",
        "warnings": signal.get("warnings"),
    }


def _quality_score(data: Any) -> tuple[float, list[str]]:
    latest = data.latest_metrics if data is not None else {}
    score = 50.0
    warnings = []
    if _num(latest.get("revenue_growth_yoy")) is not None and latest["revenue_growth_yoy"] > 0:
        score += 8
    if _num(latest.get("operating_margin")) is not None and latest["operating_margin"] >= 0.10:
        score += 10
    if _num(latest.get("fcf_margin")) is not None and latest["fcf_margin"] >= 0.05:
        score += 10
    if _num(latest.get("fcf_conversion_from_net_income")) is not None and latest["fcf_conversion_from_net_income"] >= 0.8:
        score += 8
    if _num(latest.get("roic")) is not None and latest["roic"] >= 0.10:
        score += 10
    debt_to_equity = _num(latest.get("debt_to_equity"))
    if debt_to_equity is not None and debt_to_equity <= 1:
        score += 6
    if _num(latest.get("sbc_percent_revenue")) is not None and latest["sbc_percent_revenue"] > 0.10:
        score -= 10
        warnings.append("stock-based compensation is high relative to revenue")
    if _num(latest.get("share_count_change")) is not None and latest["share_count_change"] > 0.05:
        score -= 8
        warnings.append("share count is rising materially")
    if not latest:
        warnings.append("metrics data missing")
    return max(0.0, min(100.0, score)), warnings


def _valuation_results_for_ticker(
    ticker: str,
    cwd: str | Path = ".",
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
) -> list[dict[str, Any]]:
    root = _resolve_workspace_path(valuations_dir, cwd)
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.glob(f"{ticker}.*.result.json")):
        result = read_json(path, None)
        if isinstance(result, dict) and str(result.get("ticker", "")).upper() == ticker:
            rows.append({"path": str(path), "result": result})
    return rows


def _valuation_fair_value(item: dict[str, Any] | None) -> float | None:
    if not item:
        return None
    valuation = item.get("result", {}).get("valuation", {})
    return _num(valuation.get("fairValuePerShare"))


def _valuation_warnings(valuations: list[dict[str, Any]]) -> list[str]:
    warnings = []
    for item in valuations:
        scenario = item.get("result", {}).get("scenario", "scenario")
        for warning in item.get("result", {}).get("warnings", []):
            message = warning.get("message") if isinstance(warning, dict) else str(warning)
            if message:
                warnings.append(f"{scenario}: {message}")
    return warnings


def _valuation_freshness_warnings(valuations: list[dict[str, Any]]) -> list[str]:
    warnings = []
    for item in valuations:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        source = result.get("portfolioSource")
        if not isinstance(source, dict):
            continue
        assumptions_path = source.get("assumptionsPath")
        if not assumptions_path:
            continue
        result_path = Path(str(item.get("path", "")))
        source_path = Path(str(assumptions_path))
        scenario = result.get("scenario", "scenario")
        if not source_path.exists():
            warnings.append(f"{scenario}: source assumptions file is missing: {source_path}")
            continue
        if result_path.exists() and result_path.stat().st_mtime < source_path.stat().st_mtime:
            warnings.append(f"{scenario}: valuation result is older than source assumptions: {source_path}")
    return warnings


def _price_from_valuations_or_local(
    ticker: str,
    valuations: list[dict[str, Any]],
    cwd: str | Path,
    research_root: str | Path | None,
) -> tuple[float | None, str | None]:
    candidates: list[tuple[date | None, float, str | None]] = []
    for item in valuations:
        result = item.get("result", {})
        valuation = result.get("valuation", {}) if isinstance(result, dict) else {}
        market = result.get("market", {}) if isinstance(result, dict) else {}
        price = _num(valuation.get("currentSharePrice"))
        if price is not None:
            price_date = str(market.get("priceDate") or "") or None
            candidates.append((parse_iso_date(price_date), price, price_date))
    data = load_local_financial_data(ticker, cwd=cwd, research_root=research_root, require=False)
    if data.latest_price is not None:
        candidates.append((parse_iso_date(data.latest_price_date), data.latest_price, data.latest_price_date))
    if not candidates:
        return None, None
    dated = [candidate for candidate in candidates if candidate[0] is not None]
    selected = max(dated or candidates, key=lambda item: item[0] or date.min)
    return selected[1], selected[2]


def _price_age_days(price_date: Any) -> int | None:
    parsed = parse_iso_date(str(price_date or ""))
    if parsed is None:
        return None
    return (date.today() - parsed).days


def _records_to_rows(records: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[list[Any]]:
    rows = [[label for _, label in columns]]
    for record in records:
        rows.append([_cell_value(record.get(key)) for key, _ in columns])
    return rows


def _records_from_sheet(
    rows: list[list[Any]],
    columns: list[tuple[str, str]],
    kind: str,
    sheet_name: str,
) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError(f"Workbook sheet {sheet_name} is missing a header row")
    header = [_header_key(value) for value in rows[0]]
    if _header_key("Ticker") not in header:
        raise ValueError(f"Workbook sheet {sheet_name} is missing required column: Ticker")
    key_by_header = {_header_key(label): key for key, label in columns}
    records = []
    for row in rows[1:]:
        raw: dict[str, Any] = {}
        for index, value in enumerate(row):
            if index < len(header) and header[index] in key_by_header:
                raw[key_by_header[header[index]]] = value
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        records.extend(_normalize_records([raw], kind=kind))
    return records


def _normalize_records(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    normalized = []
    for item in records:
        if not isinstance(item, dict):
            continue
        raw_ticker = str(item.get("ticker") or "").strip()
        if not raw_ticker:
            continue
        record = {"ticker": normalize_ticker(raw_ticker)}
        text_fields = {"notes", "thesisStatus", "priority", "scenario", "model", "assumptionsPath"}
        rate_fields = {"targetAllocation", "maxAllocation", "requiredMarginOfSafety"}
        float_fields = {"shares", "costBasisPerShare", "targetEntryPrice", "userFairValuePerShare"}
        for field in text_fields:
            if field in item and str(item.get(field) or "").strip():
                record[field] = str(item.get(field)).strip()
        for field in float_fields:
            value = _parse_float(item.get(field))
            if value is not None:
                record[field] = value
        for field in rate_fields:
            value = _parse_rate(item.get(field))
            if value is not None:
                _validate_portfolio_rate(field, value, ticker=record["ticker"])
                record[field] = value
        if kind == "assumption":
            record.setdefault("scenario", "base")
        normalized.append(record)
    return normalized


def _assumption_sheet_rows(
    inputs: PortfolioInputs,
    cwd: str | Path,
    assumptions_dir: str | Path = DEFAULT_ASSUMPTIONS_DIR,
    valuations_dir: str | Path = DEFAULT_VALUATIONS_DIR,
) -> list[dict[str, Any]]:
    rows = [dict(item) for item in inputs.assumptions]
    existing = {(row.get("ticker"), row.get("scenario"), row.get("assumptionsPath")) for row in rows}
    discovered = discover_assumption_paths(inputs, cwd=cwd, assumptions_dir=assumptions_dir)
    for ticker, paths in discovered.items():
        for path in paths:
            try:
                assumptions = load_assumptions(path, cwd=cwd)
            except (ValueError, FileNotFoundError, json.JSONDecodeError):
                assumptions = {}
            scenario = str(assumptions.get("scenario") or _scenario_from_path(path))
            model = str(assumptions.get("model") or "")
            key = (ticker, scenario, str(path))
            if key in existing:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "scenario": scenario,
                    "model": model,
                    "assumptionsPath": str(path),
                    "resultPath": str(
                        _resolve_workspace_path(valuations_dir, cwd)
                        / _valuation_result_filename(
                            ticker,
                            _safe_filename_part(scenario),
                            _safe_filename_part(model or "model"),
                        )
                    ),
                }
            )
    return rows


def _portfolio_rows(inputs: PortfolioInputs, signals: list[dict[str, Any]]) -> list[list[Any]]:
    signal_by_ticker = {row["ticker"]: row for row in signals}
    rows = [["Ticker", "Shares", "Current Value", "Signal", "Notes"]]
    total_value = 0.0
    holding_rows = []
    for holding in inputs.holdings:
        ticker = holding["ticker"]
        shares = _num(holding.get("shares")) or 0.0
        price = _num(signal_by_ticker.get(ticker, {}).get("currentSharePrice"))
        current_value = shares * price if price is not None else None
        if current_value is not None:
            total_value += current_value
        holding_rows.append([ticker, shares, current_value, signal_by_ticker.get(ticker, {}).get("label", ""), holding.get("notes", "")])
    rows.extend(holding_rows)
    rows.append([])
    rows.append(["Total Current Value", "", total_value, "", ""])
    return rows


def _audit_rows(paths: PortfolioPaths, inputs: PortfolioInputs, valuations: list[dict[str, Any]], signals: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": "Generated At", "value": utc_now_iso()},
        {"key": "Workbook", "value": str(paths.workbook)},
        {"key": "Portfolio Directory", "value": str(paths.portfolio_dir)},
        {"key": "Ticker Count", "value": len(inputs.tickers)},
        {"key": "Valuation Result Count", "value": len(valuations)},
        {"key": "Signal Count", "value": len(signals.get("rows", []))},
        {"key": "Holdings JSON", "value": str(paths.holdings)},
        {"key": "Watchlist JSON", "value": str(paths.watchlist)},
        {"key": "Assumption Overrides JSON", "value": str(paths.assumptions)},
        {"key": "Rules JSON", "value": str(paths.rules)},
    ]


def _sheet(name: str, rows: list[list[Any]], widths: list[float]) -> dict[str, Any]:
    return {"name": name, "rows": rows, "widths": widths}


def _paths(
    cwd: str | Path = ".",
    workbook_path: str | Path | None = None,
    portfolio_dir: str | Path | None = None,
) -> PortfolioPaths:
    root = Path(cwd)
    workbook = _resolve_workspace_path(workbook_path or DEFAULT_WORKBOOK, root)
    if portfolio_dir is None:
        portfolio = workbook.parent if workbook_path is not None else _resolve_workspace_path(DEFAULT_PORTFOLIO_DIR, root)
    else:
        portfolio = _resolve_workspace_path(portfolio_dir, root)
    return PortfolioPaths(
        workbook=workbook,
        portfolio_dir=portfolio,
        holdings=portfolio / "holdings.json",
        watchlist=portfolio / "watchlist.json",
        assumptions=portfolio / "assumption_overrides.json",
        rules=portfolio / "rules.json",
        signals=portfolio / "signals.json",
        valuation_audit=portfolio / "valuation_audit.json",
    )


def _default_holdings() -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "holdings": []}


def _default_watchlist() -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "watchlist": []}


def _default_assumption_overrides() -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "updatedAt": utc_now_iso(), "assumptions": []}


def _write_json_if_missing(path: Path, data: Any) -> None:
    if not path.exists():
        write_json(path, data)


def _summary(message: str, **values: Any) -> dict[str, Any]:
    return {"message": message, **values}


def _resolve_workspace_path(path: str | Path, cwd: str | Path = ".") -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(cwd) / resolved
    return resolved.resolve()


def _header_key(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _cell_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    if value is None:
        return ""
    return str(value)


def _parse_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("$"):
        text = text[1:]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_rate(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().endswith("%"):
        parsed = _parse_float(value.strip()[:-1])
        return parsed / 100 if parsed is not None else None
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return parsed / 100 if parsed > 1 else parsed


def _validate_portfolio_rate(field: str, value: float, ticker: str) -> None:
    upper_bound = 0.8 if field == "requiredMarginOfSafety" else 1.0
    if value < 0 or value > upper_bound:
        raise ValueError(f"{ticker}: {field} must be between 0 and {upper_bound:g}")


def _validate_rules(rules: dict[str, Any]) -> None:
    signals = rules.get("signals", {}) if isinstance(rules.get("signals"), dict) else {}
    quality = rules.get("quality", {}) if isinstance(rules.get("quality"), dict) else {}
    for field in ("requiredMarginOfSafety", "watchMarginOfSafety"):
        value = _num(signals.get(field))
        if value is not None and (value < 0 or value > 0.8):
            raise ValueError(f"signals.{field} must be between 0 and 0.8")
    stale_days = _num(signals.get("stalePriceDays"))
    if stale_days is not None and stale_days < 0:
        raise ValueError("signals.stalePriceDays must be non-negative")
    for field in ("minimumScoreForOpportunity", "minimumScoreForStrongOpportunity"):
        value = _num(quality.get(field))
        if value is not None and (value < 0 or value > 100):
            raise ValueError(f"quality.{field} must be between 0 and 100")


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_float_for_ticker(ticker: str, *record_groups: list[dict[str, Any]], field: str) -> float | None:
    for records in record_groups:
        for item in records:
            if str(item.get("ticker", "")).upper() == ticker:
                value = _num(item.get(field))
                if value is not None:
                    return value
    return None


def _rule_float(rules: dict[str, Any], field: str, default: float) -> float:
    value = _num(rules.get("signals", {}).get(field) if isinstance(rules.get("signals"), dict) else None)
    return value if value is not None else default


def _rule_int(rules: dict[str, Any], field: str, default: int) -> int:
    value = _rule_float(rules, field, float(default))
    return int(value)


def _quality_rule_float(rules: dict[str, Any], field: str, default: float) -> float:
    value = _num(rules.get("quality", {}).get(field) if isinstance(rules.get("quality"), dict) else None)
    return value if value is not None else default


def _scenario_from_path(path: Path) -> str:
    parts = path.stem.split(".")
    return parts[1] if len(parts) >= 2 else "base"


def _valuation_result_filename(ticker: str, scenario: str, model: str) -> str:
    return f"{ticker}.{_safe_filename_part(scenario)}.{_safe_filename_part(model)}.result.json"


def _unique_valuation_result_path(
    output_dir: Path,
    ticker: str,
    scenario: str,
    model: str,
    used_names: set[str],
) -> Path:
    base = _valuation_result_filename(ticker, scenario, model)
    if base not in used_names:
        used_names.add(base)
        return output_dir / base
    index = 2
    while True:
        candidate = (
            f"{ticker}.{_safe_filename_part(scenario)}.{_safe_filename_part(model)}.{index}.result.json"
        )
        if candidate not in used_names:
            used_names.add(candidate)
            return output_dir / candidate
        index += 1


def _best_valuation_by_scenario(valuations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in valuations:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        scenario = str(result.get("scenario", "")).lower()
        if not scenario:
            continue
        current = best.get(scenario)
        if current is None or _model_rank(result.get("model")) < _model_rank(current.get("result", {}).get("model")):
            best[scenario] = item
    return best


def _duplicate_scenario_warnings(valuations: list[dict[str, Any]]) -> list[str]:
    models_by_scenario: dict[str, list[str]] = {}
    for item in valuations:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        scenario = str(result.get("scenario", "") or "").lower()
        model = str(result.get("model", "") or "unknown")
        if scenario:
            models_by_scenario.setdefault(scenario, []).append(model)
    warnings = []
    for scenario, models in models_by_scenario.items():
        unique_models = sorted(set(models))
        if len(models) > 1:
            preferred = sorted(unique_models, key=_model_rank)[0]
            warnings.append(
                f"multiple {scenario} valuation results found ({', '.join(unique_models)}); using {preferred} for scenario fair value"
            )
    return warnings


def _model_rank(model: Any) -> int:
    order = {
        "fcff-dcf": 0,
        "owner-earnings-dcf": 1,
        "epv": 2,
        "multiples": 3,
        "reverse-dcf": 4,
    }
    return order.get(str(model or ""), 99)


def _mtime_iso(path: Path) -> str | None:
    try:
        return date.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "-" for char in value).strip("._-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "base"
