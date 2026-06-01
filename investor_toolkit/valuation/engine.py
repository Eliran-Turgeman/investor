from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from ..storage import ResearchStorage
from ..utils import money, normalize_ticker, parse_iso_date, percent, read_json, safe_divide, write_json, write_text


SCHEMA_VERSION = "1.0"
SUPPORTED_MODELS = ("fcff-dcf", "owner-earnings-dcf", "reverse-dcf", "epv", "multiples")
REVERSE_SOLVE_TARGETS = (
    "revenueGrowthYears1To5",
    "targetOperatingMargin",
    "terminalGrowthRate",
    "discountRate",
)
TARGET_VALUE_BASES = ("current_market_price", "custom_share_price", "custom_enterprise_value")
MULTIPLE_METRIC_TYPES = ("earnings", "freeCashFlow", "revenue", "ebitda", "operatingIncome")


@dataclass(slots=True)
class ValuationWarning:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(slots=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[ValuationWarning] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class LocalFinancialData:
    ticker: str
    company_name: str
    company_dir: Path
    financial_rows: list[dict[str, Any]]
    prices: list[dict[str, Any]]
    metric_periods: list[dict[str, Any]]
    latest_ttm: dict[str, Any] = field(default_factory=dict)

    @property
    def latest_financial(self) -> dict[str, Any]:
        return _latest_period_row(self.financial_rows)

    @property
    def latest_metrics(self) -> dict[str, Any]:
        return _latest_period_row(self.metric_periods)

    @property
    def latest_price_row(self) -> dict[str, Any]:
        dated_rows = [
            (row_date, row)
            for row in self.prices
            for row_date in [parse_iso_date(str(row.get("date", "") or ""))]
            if row_date is not None and _price_row_matches_ticker(row, self.ticker) and _price_value(row) is not None
        ]
        return sorted(dated_rows, key=lambda item: item[0])[-1][1] if dated_rows else {}

    @property
    def latest_price(self) -> float | None:
        return _price_value(self.latest_price_row)

    @property
    def latest_price_date(self) -> str | None:
        value = self.latest_price_row.get("date")
        return str(value) if value else None

    @property
    def base_fiscal_year(self) -> int | None:
        if self.latest_ttm.get("fiscalYear"):
            return int(self.latest_ttm["fiscalYear"])
        latest = self.latest_financial or self.latest_metrics
        fiscal_year = latest.get("fiscalYear")
        if isinstance(fiscal_year, int):
            return fiscal_year
        period = str(latest.get("period", ""))
        return int(period[:4]) if period[:4].isdigit() else None

    def latest_value(self, field_name: str) -> float | None:
        financial = self.latest_financial
        metrics = self.latest_metrics
        if field_name == "revenue":
            return _first_not_none(
                _first_num(self.latest_ttm, "revenue"),
                _first_num(financial, "revenue"),
                _first_num(metrics, "revenue"),
            )
        if field_name == "operating_income":
            return _first_not_none(
                _first_num(self.latest_ttm, "operatingIncome", "operating_income"),
                _first_num(financial, "operatingIncome", "operating_income"),
                _first_num(metrics, "operating_income"),
            )
        if field_name == "net_income":
            return _first_not_none(
                _first_num(self.latest_ttm, "netIncome", "net_income"),
                _first_num(financial, "netIncome", "net_income"),
                _first_num(metrics, "net_income"),
            )
        if field_name == "free_cash_flow":
            return _first_not_none(
                _first_num(self.latest_ttm, "freeCashFlow", "free_cash_flow"),
                _first_num(metrics, "free_cash_flow"),
                _free_cash_flow(financial),
            )
        if field_name == "cash_and_equivalents":
            return _first_not_none(
                _first_num(self.latest_ttm, "cash", "cashAndEquivalents"),
                _first_num(financial, "cash", "cashAndEquivalents"),
                _first_num(metrics, "cash_and_equivalents"),
            )
        if field_name == "total_debt":
            return _first_not_none(
                _first_num(self.latest_ttm, "totalDebt", "total_debt"),
                _first_num(financial, "totalDebt", "total_debt"),
                _first_num(metrics, "total_debt"),
            )
        if field_name == "shares_outstanding":
            return _first_not_none(
                _first_num(self.latest_ttm, "dilutedShares", "weighted_average_diluted_shares"),
                _first_num(financial, "dilutedShares", "weighted_average_diluted_shares"),
                _first_num(metrics, "weighted_average_diluted_shares", "shares_outstanding"),
            )
        if field_name == "current_share_price":
            return self.latest_price
        if field_name == "market_cap":
            market_cap = _first_positive_num(metrics, "market_cap")
            if market_cap is not None:
                return market_cap
            price = self.latest_price
            shares = self.latest_value("shares_outstanding")
            return price * shares if price is not None and shares is not None and shares > 0 else None
        return None

    def historical_metric_values(self, field_name: str) -> list[float]:
        values: list[float] = []
        for row in self.metric_periods:
            value = _num(row.get(field_name))
            if value is not None and math.isfinite(value):
                values.append(value)
        return values

    def historical_tax_rates(self) -> list[float]:
        rates: list[float] = []
        for row in self.financial_rows:
            tax = _first_num(row, "incomeTaxExpense", "income_tax_expense")
            pretax = _first_num(row, "pretaxIncome", "pretax_income")
            rate = safe_divide(tax, pretax)
            if rate is not None and 0 <= rate <= 0.5:
                rates.append(rate)
        return rates


def load_assumptions(path: str | Path, cwd: str | Path | None = None) -> dict[str, Any]:
    resolved_path = _resolve_workspace_path(path, cwd)
    data = read_json(resolved_path, None)
    if not isinstance(data, dict):
        raise ValueError(f"Assumptions file must contain a JSON object: {path}")
    return data


def load_local_financial_data(
    ticker: str,
    cwd: str | Path = ".",
    research_root: str | Path | None = None,
    require: bool = True,
) -> LocalFinancialData:
    ticker = normalize_ticker(ticker)
    storage = ResearchStorage(cwd, research_root=research_root)
    company_dir = storage.company_dir(ticker)
    if require and not company_dir.exists():
        raise FileNotFoundError(f"Missing local research folder for {ticker}: {company_dir}")
    company = storage.load_company(ticker)
    company_name = company.name if company and company.name else ticker
    financial_rows = read_json(company_dir / "data" / "financials.json", []) or []
    prices = read_json(company_dir / "data" / "prices.json", []) or []
    metrics = read_json(company_dir / "metrics" / "metrics.json", {}) or {}
    company_facts = read_json(company_dir / "data" / "company_facts.json", {}) or {}
    metric_periods = metrics.get("periods", []) if isinstance(metrics, dict) else []
    data = LocalFinancialData(
        ticker=ticker,
        company_name=company_name,
        company_dir=company_dir,
        financial_rows=financial_rows if isinstance(financial_rows, list) else [],
        prices=prices if isinstance(prices, list) else [],
        metric_periods=metric_periods if isinstance(metric_periods, list) else [],
        latest_ttm=_latest_ttm_from_company_facts(company_facts),
    )
    if require:
        missing = []
        if not data.financial_rows:
            missing.append("research/<TICKER>/data/financials.json")
        if not data.prices:
            missing.append("research/<TICKER>/data/prices.json")
        if not data.metric_periods:
            missing.append("research/<TICKER>/metrics/metrics.json")
        if missing:
            raise FileNotFoundError(f"Missing local valuation inputs for {ticker}: {', '.join(missing)}")
    return data


def init_assumptions_file(
    ticker: str,
    model: str,
    scenario: str,
    output_path: str | Path,
    cwd: str | Path = ".",
    research_root: str | Path | None = None,
) -> Path:
    data = load_local_financial_data(ticker, cwd=cwd, research_root=research_root, require=False)
    assumptions = create_assumptions_template(data, model=model, scenario=scenario)
    report = validate_assumptions(assumptions, data=None, structural_only=True)
    if report.errors:
        raise ValueError("Generated assumptions template was invalid: " + "; ".join(report.errors))
    output = _resolve_workspace_path(output_path, cwd)
    write_json(output, assumptions)
    return output


def create_assumptions_template(data: LocalFinancialData, model: str, scenario: str) -> dict[str, Any]:
    model = _normalize_model(model)
    explicit_years = 10
    assumptions: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "ticker": data.ticker,
        "companyName": data.company_name,
        "valuationDate": date.today().isoformat(),
        "scenario": scenario or "base",
        "model": model,
        "currency": "USD",
        "projection": {
            "explicitYears": explicit_years,
            "baseFiscalYear": data.base_fiscal_year,
        },
        "businessAssumptions": {},
        "discountingAssumptions": {"discountRate": None},
        "capitalStructureAdjustments": {
            "cashAndEquivalents": data.latest_value("cash_and_equivalents"),
            "totalDebt": data.latest_value("total_debt"),
            "minorityInterest": 0,
            "nonOperatingAssets": 0,
        },
        "shareAssumptions": {
            "sharesOutstanding": data.latest_value("shares_outstanding"),
            "annualDilutionRate": None,
        },
        "marginOfSafety": {"required": 0.25},
        "metadata": {
            "createdBy": "agent",
            "source": "local-financial-data",
            "notes": [
                "Template generated by the deterministic valuation CLI.",
                "Null values are judgment assumptions that must be filled before valuation.",
            ],
        },
    }
    business = assumptions["businessAssumptions"]
    if model in {"fcff-dcf", "reverse-dcf"}:
        business.update(
            {
                "revenueGrowth": [{"year": year, "value": None} for year in range(1, explicit_years + 1)],
                "targetOperatingMargin": None,
                "taxRate": None,
                "reinvestmentRate": None,
                "terminalGrowthRate": None,
            }
        )
    if model == "owner-earnings-dcf":
        business.update(
            {
                "ownerEarningsBase": data.latest_value("free_cash_flow"),
                "ownerEarningsGrowth": [{"year": year, "value": None} for year in range(1, explicit_years + 1)],
                "maintenanceCapexAssumption": None,
                "terminalGrowthRate": None,
            }
        )
    if model == "epv":
        business.update(
            {
                "normalizedOperatingEarnings": None,
                "taxRate": None,
            }
        )
    if model == "multiples":
        business.update(
            {
                "normalizedMetric": None,
                "fairMultiple": None,
                "metricType": None,
            }
        )
    if model == "reverse-dcf":
        assumptions.update(
            {
                "currentSharePrice": data.latest_value("current_share_price"),
                "solveFor": None,
                "targetValueBasis": "current_market_price",
                "targetSharePrice": None,
                "targetEnterpriseValue": None,
            }
        )
    return assumptions


def validate_assumptions_file(
    path: str | Path,
    cwd: str | Path = ".",
    research_root: str | Path | None = None,
    expected_ticker: str | None = None,
) -> ValidationReport:
    assumptions = load_assumptions(path, cwd=cwd)
    ticker = expected_ticker or str(assumptions.get("ticker", "") or "")
    data = None
    if ticker:
        data = load_local_financial_data(ticker, cwd=cwd, research_root=research_root, require=False)
    return validate_assumptions(assumptions, expected_ticker=expected_ticker, data=data)


def validate_assumptions(
    assumptions: dict[str, Any],
    expected_ticker: str | None = None,
    data: LocalFinancialData | None = None,
    structural_only: bool = False,
) -> ValidationReport:
    report = ValidationReport()
    _validate_structure(assumptions, report)
    if report.errors or structural_only:
        return report

    model = str(assumptions.get("model", ""))
    if expected_ticker:
        expected = normalize_ticker(expected_ticker)
        actual = str(assumptions.get("ticker", "")).upper()
        if actual != expected:
            report.errors.append(f"ticker must match command ticker: expected {expected}, got {actual or 'missing'}")

    _validate_projection(assumptions, report)
    _validate_margin_of_safety(assumptions, report)
    if model == "fcff-dcf":
        _validate_fcff_assumptions(assumptions, report, data=data)
    elif model == "owner-earnings-dcf":
        _validate_owner_earnings_assumptions(assumptions, report, data=data)
    elif model == "epv":
        _validate_epv_assumptions(assumptions, report, data=data)
    elif model == "multiples":
        _validate_multiples_assumptions(assumptions, report, data=data)
    elif model == "reverse-dcf":
        _validate_reverse_dcf_assumptions(assumptions, report, data=data)

    _append_common_warnings(assumptions, report, data)
    return report


def run_valuation(
    ticker: str,
    assumptions_path: str | Path,
    cwd: str | Path = ".",
    research_root: str | Path | None = None,
    include_sensitivity: bool = False,
    include_debug: bool = False,
) -> dict[str, Any]:
    ticker = normalize_ticker(ticker)
    assumptions = load_assumptions(assumptions_path, cwd=cwd)
    data = load_local_financial_data(ticker, cwd=cwd, research_root=research_root, require=True)
    report = validate_assumptions(assumptions, expected_ticker=ticker, data=data)
    if report.errors:
        raise ValueError(_format_invalid_assumptions(assumptions_path, report))
    warnings = report.warnings
    model = str(assumptions.get("model", ""))
    if model == "fcff-dcf":
        result = _value_fcff_dcf(data, assumptions, warnings=warnings, include_debug=include_debug)
    elif model == "owner-earnings-dcf":
        result = _value_owner_earnings_dcf(data, assumptions, warnings=warnings, include_debug=include_debug)
    elif model == "epv":
        result = _value_epv(data, assumptions, warnings=warnings, include_debug=include_debug)
    elif model == "multiples":
        result = _value_multiples(data, assumptions, warnings=warnings, include_debug=include_debug)
    elif model == "reverse-dcf":
        result = _value_reverse_dcf(data, assumptions, warnings=warnings, include_debug=include_debug)
    else:
        raise ValueError(f"Unsupported valuation model: {model}")
    if include_sensitivity and model in {"fcff-dcf", "reverse-dcf"}:
        sensitivity_assumptions = result.get("resolvedAssumptionsForSensitivity") or assumptions
        result["sensitivity"] = _sensitivity(data, sensitivity_assumptions)
    result.pop("resolvedAssumptionsForSensitivity", None)
    if not include_debug:
        result.pop("debug", None)
    return result


def compare_valuations(
    ticker: str,
    assumption_paths: list[str | Path],
    cwd: str | Path = ".",
    research_root: str | Path | None = None,
    include_sensitivity: bool = False,
) -> dict[str, Any]:
    if len(assumption_paths) < 2:
        raise ValueError("value compare requires at least two --assumptions files")
    results = [
        run_valuation(
            ticker,
            path,
            cwd=cwd,
            research_root=research_root,
            include_sensitivity=include_sensitivity,
            include_debug=False,
        )
        for path in assumption_paths
    ]
    assumptions = [load_assumptions(path, cwd=cwd) for path in assumption_paths]
    scenarios = []
    for path, result in zip(assumption_paths, results):
        valuation = result["valuation"]
        scenarios.append(
            {
                "scenario": result["scenario"],
                "model": result["model"],
                "assumptionsPath": str(path),
                "fairValuePerShare": valuation.get("fairValuePerShare"),
                "currentSharePrice": valuation.get("currentSharePrice"),
                "upsideDownside": valuation.get("upsideDownside"),
                "marginOfSafety": valuation.get("marginOfSafety"),
                "meetsRequiredMarginOfSafety": valuation.get("meetsRequiredMarginOfSafety"),
                "warningCount": len(result.get("warnings", [])),
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ticker": normalize_ticker(ticker),
        "valuationDate": date.today().isoformat(),
        "currency": results[0].get("currency", "USD") if results else "USD",
        "scenarios": scenarios,
        "assumptionDifferences": _assumption_differences(assumptions),
        "results": results,
    }


def export_agent_context(
    result: dict[str, Any],
    assumptions: dict[str, Any],
    cwd: str | Path = ".",
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    base_dir = _resolve_workspace_path(output_dir, cwd) if output_dir else Path(cwd) / "context" / "valuations"
    base = f"{_safe_filename_part(result['ticker'], 'ticker')}.{_safe_filename_part(result['scenario'], 'scenario')}"
    markdown_path = base_dir / f"{base}.md"
    result_path = base_dir / f"{base}.result.json"
    assumptions_path = base_dir / f"{base}.assumptions.json"
    write_text(markdown_path, _render_agent_context_markdown(result, assumptions))
    write_json(result_path, result)
    write_json(assumptions_path, assumptions)
    return {
        "markdown": str(markdown_path),
        "resultJson": str(result_path),
        "assumptionsJson": str(assumptions_path),
    }


def render_validation_report(path: str | Path, report: ValidationReport) -> str:
    path_text = str(path)
    if report.errors:
        lines = [f"Invalid assumptions file: {path_text}", "", "Errors:"]
        lines.extend(f"- {error}" for error in report.errors)
    else:
        lines = [f"Valid assumptions file: {path_text}"]
    if report.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning.message}" for warning in report.warnings)
    return "\n".join(lines)


def render_valuation_result(result: dict[str, Any], output_format: str = "text") -> str:
    output_format = output_format.lower()
    if output_format == "json":
        return json.dumps(result, indent=2, sort_keys=True) + "\n"
    if output_format == "markdown":
        return _render_valuation_markdown(result)
    if output_format != "text":
        raise ValueError(f"Unsupported output format: {output_format}")
    return _render_valuation_text(result)


def render_comparison(comparison: dict[str, Any], output_format: str = "text") -> str:
    output_format = output_format.lower()
    if output_format == "json":
        return json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    lines = [f"{comparison['ticker']} valuation scenario comparison", ""]
    if output_format == "markdown":
        lines = [f"# {comparison['ticker']} Valuation Scenario Comparison", ""]
        lines.append("| Scenario | Model | Fair value | Current price | Upside/downside | Margin of safety |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for scenario in comparison["scenarios"]:
            lines.append(
                "| {scenario} | {model} | {fair} | {current} | {upside} | {mos} |".format(
                    scenario=scenario["scenario"],
                    model=scenario["model"],
                    fair=_money_per_share(scenario.get("fairValuePerShare")),
                    current=_money_per_share(scenario.get("currentSharePrice")),
                    upside=percent(scenario.get("upsideDownside")),
                    mos=percent(scenario.get("marginOfSafety")),
                )
            )
        lines.extend(["", "## Main Assumption Differences", ""])
        lines.extend(f"- {item}" for item in comparison.get("assumptionDifferences", []))
        return "\n".join(lines) + "\n"
    if output_format != "text":
        raise ValueError(f"Unsupported output format: {output_format}")
    header = f"{'Scenario':<18} {'Model':<20} {'Fair value':>12} {'Current':>12} {'Upside':>10} {'MOS':>10}"
    lines.extend([header, "-" * len(header)])
    for scenario in comparison["scenarios"]:
        lines.append(
            f"{scenario['scenario']:<18} {scenario['model']:<20} "
            f"{_money_per_share(scenario.get('fairValuePerShare')):>12} "
            f"{_money_per_share(scenario.get('currentSharePrice')):>12} "
            f"{percent(scenario.get('upsideDownside')):>10} "
            f"{percent(scenario.get('marginOfSafety')):>10}"
        )
    if comparison.get("assumptionDifferences"):
        lines.extend(["", "Main assumption differences:"])
        lines.extend(f"- {item}" for item in comparison["assumptionDifferences"])
    return "\n".join(lines) + "\n"


def _validate_structure(assumptions: dict[str, Any], report: ValidationReport) -> None:
    required = [
        "schemaVersion",
        "ticker",
        "valuationDate",
        "scenario",
        "model",
        "currency",
        "projection",
        "businessAssumptions",
        "discountingAssumptions",
        "shareAssumptions",
        "marginOfSafety",
        "metadata",
    ]
    for field_name in required:
        if field_name not in assumptions:
            report.errors.append(f"missing required field: {field_name}")
    schema_version = assumptions.get("schemaVersion")
    if schema_version and schema_version != SCHEMA_VERSION:
        report.errors.append(f"unsupported schemaVersion: {schema_version}")
    ticker = assumptions.get("ticker")
    if isinstance(ticker, str) and ticker.strip():
        try:
            normalize_ticker(ticker)
        except ValueError as exc:
            report.errors.append(str(exc))
    elif "ticker" in assumptions:
        report.errors.append("ticker must be a non-empty string")
    if parse_iso_date(str(assumptions.get("valuationDate", "") or "")) is None and "valuationDate" in assumptions:
        report.errors.append("valuationDate must be a valid ISO date")
    model = assumptions.get("model")
    if model and model not in SUPPORTED_MODELS:
        report.errors.append(f"unsupported model: {model}")
    for object_field in ["projection", "businessAssumptions", "discountingAssumptions", "shareAssumptions", "marginOfSafety", "metadata"]:
        if object_field in assumptions and not isinstance(assumptions.get(object_field), dict):
            report.errors.append(f"{object_field} must be an object")


def _validate_projection(assumptions: dict[str, Any], report: ValidationReport) -> None:
    explicit_years = _num(_get_path(assumptions, "projection.explicitYears"))
    if explicit_years is None:
        report.errors.append("projection.explicitYears is required and must be numeric")
        return
    if int(explicit_years) != explicit_years or not 1 <= int(explicit_years) <= 30:
        report.errors.append("projection.explicitYears must be an integer between 1 and 30")


def _validate_margin_of_safety(assumptions: dict[str, Any], report: ValidationReport) -> None:
    required = _num(_get_path(assumptions, "marginOfSafety.required"))
    if required is None:
        report.errors.append("marginOfSafety.required is required and must be numeric")
    elif not 0 <= required <= 0.8:
        report.errors.append("marginOfSafety.required must be between 0 and 0.8")


def _validate_fcff_assumptions(
    assumptions: dict[str, Any],
    report: ValidationReport,
    data: LocalFinancialData | None = None,
    reverse_solve_for: str | None = None,
) -> None:
    years = int(_num(_get_path(assumptions, "projection.explicitYears")) or 0)
    _validate_revenue_growth(assumptions, report, years, reverse_solve_for=reverse_solve_for)
    target_margin = _maybe_required_number(
        assumptions,
        "businessAssumptions.targetOperatingMargin",
        report,
        allow_missing=reverse_solve_for == "targetOperatingMargin",
    )
    tax_rate = _maybe_required_number(assumptions, "businessAssumptions.taxRate", report)
    reinvestment_rate = _maybe_required_number(assumptions, "businessAssumptions.reinvestmentRate", report)
    terminal_growth = _maybe_required_number(
        assumptions,
        "businessAssumptions.terminalGrowthRate",
        report,
        allow_missing=reverse_solve_for == "terminalGrowthRate",
    )
    discount_rate = _maybe_required_number(
        assumptions,
        "discountingAssumptions.discountRate",
        report,
        allow_missing=reverse_solve_for == "discountRate",
    )
    shares = _resolve_numeric(
        _get_path(assumptions, "shareAssumptions.sharesOutstanding"),
        data,
        "shares_outstanding",
        "shareAssumptions.sharesOutstanding",
        report,
    )
    _validate_capital_structure_adjustments(assumptions, report, data, cash_debt_required=False)
    if target_margin is not None and not 0 <= target_margin <= 1:
        report.errors.append("businessAssumptions.targetOperatingMargin must be between 0 and 1")
    if tax_rate is not None and not 0 <= tax_rate <= 0.5:
        report.errors.append("businessAssumptions.taxRate must be between 0 and 0.5")
    if reinvestment_rate is not None:
        if reinvestment_rate < 0:
            report.errors.append("businessAssumptions.reinvestmentRate must be non-negative")
        elif reinvestment_rate > 1:
            report.warnings.append(
                ValuationWarning(
                    "HIGH_REINVESTMENT_RATE",
                    "Reinvestment rate is above 100%; this may be valid for some firms but should be intentional.",
                )
            )
    if discount_rate is not None and discount_rate <= 0:
        report.errors.append("discountingAssumptions.discountRate must be positive")
    if terminal_growth is not None and terminal_growth <= -1:
        report.errors.append("businessAssumptions.terminalGrowthRate must be greater than -100%")
    if terminal_growth is not None and discount_rate is not None and terminal_growth >= discount_rate:
        report.errors.append("businessAssumptions.terminalGrowthRate must be lower than discountingAssumptions.discountRate")
    if shares is not None and shares <= 0:
        report.errors.append("shareAssumptions.sharesOutstanding must be positive")
    annual_dilution_raw = _get_path(assumptions, "shareAssumptions.annualDilutionRate")
    annual_dilution = _num(annual_dilution_raw)
    if annual_dilution_raw not in (None, ""):
        if annual_dilution is None:
            report.errors.append("shareAssumptions.annualDilutionRate must be numeric")
        elif annual_dilution <= -1:
            report.errors.append("shareAssumptions.annualDilutionRate must be greater than -100%")
        elif not -0.1 <= annual_dilution <= 0.2:
            report.warnings.append(
                ValuationWarning(
                    "UNUSUAL_DILUTION_RATE",
                    "Annual dilution rate is outside the usual -10% to +20% range.",
                )
            )
    if data is not None:
        latest_revenue = data.latest_value("revenue")
        if latest_revenue is None:
            report.errors.append("local financial data is missing latest revenue")
        elif latest_revenue <= 0:
            report.errors.append("local financial data latest revenue must be positive")
        if data.latest_value("current_share_price") is None:
            report.errors.append("local market data is missing current share price")


def _validate_owner_earnings_assumptions(
    assumptions: dict[str, Any], report: ValidationReport, data: LocalFinancialData | None = None
) -> None:
    years = int(_num(_get_path(assumptions, "projection.explicitYears")) or 0)
    owner_base = _maybe_required_number(assumptions, "businessAssumptions.ownerEarningsBase", report)
    growth_values = _growth_assumption_values(_get_path(assumptions, "businessAssumptions.ownerEarningsGrowth"), years)
    if not growth_values or any(value is None for value in growth_values):
        report.errors.append("businessAssumptions.ownerEarningsGrowth must contain numeric growth values for each explicit year")
    else:
        for index, value in enumerate(growth_values, start=1):
            if value is not None and value <= -1:
                report.errors.append(f"businessAssumptions.ownerEarningsGrowth year {index} must be greater than -100%")
    maintenance = _maybe_required_number(assumptions, "businessAssumptions.maintenanceCapexAssumption", report)
    terminal_growth = _maybe_required_number(assumptions, "businessAssumptions.terminalGrowthRate", report)
    discount_rate = _maybe_required_number(assumptions, "discountingAssumptions.discountRate", report)
    shares = _resolve_numeric(
        _get_path(assumptions, "shareAssumptions.sharesOutstanding"),
        data,
        "shares_outstanding",
        "shareAssumptions.sharesOutstanding",
        report,
    )
    _validate_capital_structure_adjustments(assumptions, report, data, cash_debt_required=False)
    if owner_base is not None and owner_base <= 0:
        report.errors.append("businessAssumptions.ownerEarningsBase must be positive")
    if maintenance is not None and maintenance < 0:
        report.errors.append("businessAssumptions.maintenanceCapexAssumption must be non-negative")
    if discount_rate is not None and discount_rate <= 0:
        report.errors.append("discountingAssumptions.discountRate must be positive")
    if terminal_growth is not None and terminal_growth <= -1:
        report.errors.append("businessAssumptions.terminalGrowthRate must be greater than -100%")
    if terminal_growth is not None and discount_rate is not None and terminal_growth >= discount_rate:
        report.errors.append("businessAssumptions.terminalGrowthRate must be lower than discountingAssumptions.discountRate")
    if shares is not None and shares <= 0:
        report.errors.append("shareAssumptions.sharesOutstanding must be positive")


def _validate_epv_assumptions(
    assumptions: dict[str, Any], report: ValidationReport, data: LocalFinancialData | None = None
) -> None:
    earnings = _maybe_required_number(assumptions, "businessAssumptions.normalizedOperatingEarnings", report)
    tax_rate = _maybe_required_number(assumptions, "businessAssumptions.taxRate", report)
    discount_rate = _maybe_required_number(assumptions, "discountingAssumptions.discountRate", report)
    shares = _resolve_numeric(_get_path(assumptions, "shareAssumptions.sharesOutstanding"), data, "shares_outstanding", "shareAssumptions.sharesOutstanding", report)
    _validate_capital_structure_adjustments(assumptions, report, data, cash_debt_required=True)
    if earnings is not None and earnings <= 0:
        report.errors.append("businessAssumptions.normalizedOperatingEarnings must be positive")
    if tax_rate is not None and not 0 <= tax_rate <= 0.5:
        report.errors.append("businessAssumptions.taxRate must be between 0 and 0.5")
    if discount_rate is not None and discount_rate <= 0:
        report.errors.append("discountingAssumptions.discountRate must be positive")
    if shares is not None and shares <= 0:
        report.errors.append("shareAssumptions.sharesOutstanding must be positive")


def _validate_multiples_assumptions(
    assumptions: dict[str, Any], report: ValidationReport, data: LocalFinancialData | None = None
) -> None:
    metric = _maybe_required_number(assumptions, "businessAssumptions.normalizedMetric", report)
    multiple = _maybe_required_number(assumptions, "businessAssumptions.fairMultiple", report)
    metric_type = _get_path(assumptions, "businessAssumptions.metricType")
    shares = _resolve_numeric(_get_path(assumptions, "shareAssumptions.sharesOutstanding"), data, "shares_outstanding", "shareAssumptions.sharesOutstanding", report)
    _validate_capital_structure_adjustments(assumptions, report, data, cash_debt_required=True)
    if metric is not None and metric <= 0:
        report.errors.append("businessAssumptions.normalizedMetric must be positive")
    if multiple is not None and multiple <= 0:
        report.errors.append("businessAssumptions.fairMultiple must be positive")
    if metric_type not in MULTIPLE_METRIC_TYPES:
        report.errors.append(f"businessAssumptions.metricType must be one of: {', '.join(MULTIPLE_METRIC_TYPES)}")
    if shares is not None and shares <= 0:
        report.errors.append("shareAssumptions.sharesOutstanding must be positive")
    if multiple is not None and data is not None:
        historical = _historical_multiple_values(data, str(metric_type))
        if historical:
            historical_median = median(historical)
            if multiple > historical_median * 1.5:
                report.warnings.append(
                    ValuationWarning(
                        "FAIR_MULTIPLE_ABOVE_HISTORY",
                        f"Fair multiple is materially above the historical median of {historical_median:.1f}x.",
                    )
                )


def _validate_reverse_dcf_assumptions(
    assumptions: dict[str, Any], report: ValidationReport, data: LocalFinancialData | None = None
) -> None:
    solve_for = assumptions.get("solveFor")
    target_basis = assumptions.get("targetValueBasis")
    if solve_for not in REVERSE_SOLVE_TARGETS:
        report.errors.append(f"solveFor must be one of: {', '.join(REVERSE_SOLVE_TARGETS)}")
        return
    if target_basis not in TARGET_VALUE_BASES:
        report.errors.append(f"targetValueBasis must be one of: {', '.join(TARGET_VALUE_BASES)}")
    if target_basis == "current_market_price":
        current = _resolve_numeric(assumptions.get("currentSharePrice"), data, "current_share_price", "currentSharePrice", report)
        if current is not None and current <= 0:
            report.errors.append("currentSharePrice must be positive")
    elif target_basis == "custom_share_price":
        target = _num(assumptions.get("targetSharePrice"))
        if target is None or target <= 0:
            report.errors.append("targetSharePrice must be positive when targetValueBasis is custom_share_price")
    elif target_basis == "custom_enterprise_value":
        target = _num(assumptions.get("targetEnterpriseValue"))
        if target is None or target <= 0:
            report.errors.append("targetEnterpriseValue must be positive when targetValueBasis is custom_enterprise_value")
    _validate_fcff_assumptions(assumptions, report, data=data, reverse_solve_for=str(solve_for))


def _value_fcff_dcf(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    warnings: list[ValuationWarning],
    include_debug: bool = False,
) -> dict[str, Any]:
    years = int(_get_path(assumptions, "projection.explicitYears"))
    base_revenue = _required_latest(data, "revenue")
    revenue_growth = _growth_assumption_values(_get_path(assumptions, "businessAssumptions.revenueGrowth"), years)
    operating_margin = _required_assumption_number(assumptions, "businessAssumptions.targetOperatingMargin")
    tax_rate = _required_assumption_number(assumptions, "businessAssumptions.taxRate")
    reinvestment_rate = _required_assumption_number(assumptions, "businessAssumptions.reinvestmentRate")
    terminal_growth = _required_assumption_number(assumptions, "businessAssumptions.terminalGrowthRate")
    discount_rate = _required_assumption_number(assumptions, "discountingAssumptions.discountRate")
    shares = _resolved_required_number(assumptions, data, "shareAssumptions.sharesOutstanding", "shares_outstanding")
    annual_dilution = _num(_get_path(assumptions, "shareAssumptions.annualDilutionRate")) or 0.0
    projected_shares = shares * ((1 + annual_dilution) ** years)
    cash = _resolved_optional_number(assumptions, data, "capitalStructureAdjustments.cashAndEquivalents", "cash_and_equivalents") or 0.0
    debt = _resolved_optional_number(assumptions, data, "capitalStructureAdjustments.totalDebt", "total_debt") or 0.0
    minority_interest = _num(_get_path(assumptions, "capitalStructureAdjustments.minorityInterest")) or 0.0
    non_operating_assets = _num(_get_path(assumptions, "capitalStructureAdjustments.nonOperatingAssets")) or 0.0

    projected_years: list[dict[str, Any]] = []
    revenue = base_revenue
    explicit_value = 0.0
    final_fcff = 0.0
    for year in range(1, years + 1):
        growth = float(revenue_growth[year - 1])
        revenue *= 1 + growth
        operating_income = revenue * operating_margin
        nopat = operating_income * (1 - tax_rate)
        reinvestment = nopat * reinvestment_rate
        fcff = nopat - reinvestment
        discount_factor = (1 + discount_rate) ** year
        present_value = fcff / discount_factor
        explicit_value += present_value
        final_fcff = fcff
        projected_years.append(
            {
                "year": year,
                "revenue": revenue,
                "revenueGrowth": growth,
                "operatingIncome": operating_income,
                "nopat": nopat,
                "reinvestment": reinvestment,
                "fcff": fcff,
                "presentValue": present_value,
            }
        )
    terminal_value = final_fcff * (1 + terminal_growth) / (discount_rate - terminal_growth)
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** years)
    enterprise_value = explicit_value + discounted_terminal_value
    equity_value = enterprise_value + cash - debt + non_operating_assets - minority_interest
    return _build_result(
        data,
        assumptions,
        model_name="fcff-dcf",
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        shares=projected_shares,
        warnings=_with_model_warnings(
            warnings,
            enterprise_value=enterprise_value,
            discounted_terminal_value=discounted_terminal_value,
        ),
        drivers={
            "discountRate": discount_rate,
            "terminalGrowthRate": terminal_growth,
            "targetOperatingMargin": operating_margin,
            "explicitForecastValue": explicit_value,
            "terminalValue": discounted_terminal_value,
            "terminalValueUndiscounted": terminal_value,
            "terminalValuePercentOfEnterpriseValue": safe_divide(discounted_terminal_value, enterprise_value),
        },
        debug={"projectedYears": projected_years} if include_debug else None,
    )


def _value_owner_earnings_dcf(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    warnings: list[ValuationWarning],
    include_debug: bool = False,
) -> dict[str, Any]:
    years = int(_get_path(assumptions, "projection.explicitYears"))
    owner_earnings = _required_assumption_number(assumptions, "businessAssumptions.ownerEarningsBase")
    growth_values = _growth_assumption_values(_get_path(assumptions, "businessAssumptions.ownerEarningsGrowth"), years)
    maintenance = _required_assumption_number(assumptions, "businessAssumptions.maintenanceCapexAssumption")
    terminal_growth = _required_assumption_number(assumptions, "businessAssumptions.terminalGrowthRate")
    discount_rate = _required_assumption_number(assumptions, "discountingAssumptions.discountRate")
    shares = _resolved_required_number(assumptions, data, "shareAssumptions.sharesOutstanding", "shares_outstanding")
    explicit_value = 0.0
    projected_years = []
    final_owner_earnings = 0.0
    for year in range(1, years + 1):
        owner_earnings *= 1 + float(growth_values[year - 1])
        adjusted_owner_earnings = owner_earnings * (1 - maintenance) if 0 <= maintenance <= 1 else owner_earnings - maintenance
        present_value = adjusted_owner_earnings / ((1 + discount_rate) ** year)
        explicit_value += present_value
        final_owner_earnings = adjusted_owner_earnings
        projected_years.append({"year": year, "ownerEarnings": adjusted_owner_earnings, "presentValue": present_value})
    terminal_value = final_owner_earnings * (1 + terminal_growth) / (discount_rate - terminal_growth)
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** years)
    enterprise_value = explicit_value + discounted_terminal_value
    cash = _resolved_optional_number(assumptions, data, "capitalStructureAdjustments.cashAndEquivalents", "cash_and_equivalents") or 0.0
    debt = _resolved_optional_number(assumptions, data, "capitalStructureAdjustments.totalDebt", "total_debt") or 0.0
    minority_interest = _num(_get_path(assumptions, "capitalStructureAdjustments.minorityInterest")) or 0.0
    non_operating_assets = _num(_get_path(assumptions, "capitalStructureAdjustments.nonOperatingAssets")) or 0.0
    equity_value = enterprise_value + cash - debt + non_operating_assets - minority_interest
    return _build_result(
        data,
        assumptions,
        model_name="owner-earnings-dcf",
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        shares=shares,
        warnings=_with_model_warnings(warnings, enterprise_value=enterprise_value, discounted_terminal_value=discounted_terminal_value),
        drivers={
            "discountRate": discount_rate,
            "terminalGrowthRate": terminal_growth,
            "explicitForecastValue": explicit_value,
            "terminalValue": discounted_terminal_value,
            "terminalValuePercentOfEnterpriseValue": safe_divide(discounted_terminal_value, enterprise_value),
        },
        debug={"projectedYears": projected_years} if include_debug else None,
    )


def _value_epv(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    warnings: list[ValuationWarning],
    include_debug: bool = False,
) -> dict[str, Any]:
    earnings = _required_assumption_number(assumptions, "businessAssumptions.normalizedOperatingEarnings")
    tax_rate = _required_assumption_number(assumptions, "businessAssumptions.taxRate")
    discount_rate = _required_assumption_number(assumptions, "discountingAssumptions.discountRate")
    shares = _resolved_required_number(assumptions, data, "shareAssumptions.sharesOutstanding", "shares_outstanding")
    cash = _resolved_required_number(assumptions, data, "capitalStructureAdjustments.cashAndEquivalents", "cash_and_equivalents")
    debt = _resolved_required_number(assumptions, data, "capitalStructureAdjustments.totalDebt", "total_debt")
    minority_interest = _num(_get_path(assumptions, "capitalStructureAdjustments.minorityInterest")) or 0.0
    non_operating_assets = _num(_get_path(assumptions, "capitalStructureAdjustments.nonOperatingAssets")) or 0.0
    after_tax_earnings = earnings * (1 - tax_rate)
    enterprise_value = after_tax_earnings / discount_rate
    equity_value = enterprise_value + cash - debt + non_operating_assets - minority_interest
    return _build_result(
        data,
        assumptions,
        model_name="epv",
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        shares=shares,
        warnings=warnings,
        drivers={
            "discountRate": discount_rate,
            "normalizedOperatingEarnings": earnings,
            "normalizedAfterTaxOperatingEarnings": after_tax_earnings,
        },
        debug={"epvFormula": "normalizedOperatingEarnings * (1 - taxRate) / discountRate"} if include_debug else None,
    )


def _value_multiples(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    warnings: list[ValuationWarning],
    include_debug: bool = False,
) -> dict[str, Any]:
    metric = _required_assumption_number(assumptions, "businessAssumptions.normalizedMetric")
    fair_multiple = _required_assumption_number(assumptions, "businessAssumptions.fairMultiple")
    metric_type = str(_get_path(assumptions, "businessAssumptions.metricType"))
    shares = _resolved_required_number(assumptions, data, "shareAssumptions.sharesOutstanding", "shares_outstanding")
    value = metric * fair_multiple
    cash = _resolved_required_number(assumptions, data, "capitalStructureAdjustments.cashAndEquivalents", "cash_and_equivalents")
    debt = _resolved_required_number(assumptions, data, "capitalStructureAdjustments.totalDebt", "total_debt")
    minority_interest = _num(_get_path(assumptions, "capitalStructureAdjustments.minorityInterest")) or 0.0
    non_operating_assets = _num(_get_path(assumptions, "capitalStructureAdjustments.nonOperatingAssets")) or 0.0
    if metric_type in {"earnings", "freeCashFlow"}:
        equity_value = value
        enterprise_value = equity_value - cash + debt - non_operating_assets + minority_interest
    else:
        enterprise_value = value
        equity_value = enterprise_value + cash - debt + non_operating_assets - minority_interest
    return _build_result(
        data,
        assumptions,
        model_name="multiples",
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        shares=shares,
        warnings=warnings,
        drivers={
            "metricType": metric_type,
            "normalizedMetric": metric,
            "fairMultiple": fair_multiple,
        },
        debug={"valueBasis": "equity" if metric_type in {"earnings", "freeCashFlow"} else "enterprise"} if include_debug else None,
    )


def _value_reverse_dcf(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    warnings: list[ValuationWarning],
    include_debug: bool = False,
) -> dict[str, Any]:
    solve_for = str(assumptions["solveFor"])
    target_basis = str(assumptions.get("targetValueBasis", "current_market_price"))
    target_value = _reverse_target_value(data, assumptions, target_basis)
    working = copy.deepcopy(assumptions)

    def objective(candidate: float) -> float:
        candidate_assumptions = copy.deepcopy(working)
        _apply_reverse_candidate(candidate_assumptions, solve_for, candidate)
        result = _value_fcff_dcf(data, candidate_assumptions, warnings=[], include_debug=False)
        valuation = result["valuation"]
        if target_basis == "custom_enterprise_value":
            return float(valuation["enterpriseValue"])
        return float(valuation["fairValuePerShare"])

    lower, upper = _reverse_bounds(working, solve_for)
    lower_value = objective(lower) - target_value
    upper_value = objective(upper) - target_value
    if lower_value == 0:
        solved = lower
    elif upper_value == 0:
        solved = upper
    elif lower_value * upper_value > 0:
        raise ValueError(
            f"Reverse DCF could not bracket a solution for {solve_for}; "
            f"target value is outside the supported search range."
        )
    else:
        solved = _bisect_monotonic(objective, target_value, lower, upper)
    _apply_reverse_candidate(working, solve_for, solved)
    result = _value_fcff_dcf(data, working, warnings=warnings, include_debug=include_debug)
    result["model"] = "reverse-dcf"
    result["drivers"]["solveFor"] = solve_for
    result["drivers"]["impliedAssumption"] = solved
    result["drivers"]["targetValueBasis"] = target_basis
    result["drivers"]["targetValue"] = target_value
    result["resolvedAssumptionsForSensitivity"] = working
    if include_debug:
        result.setdefault("debug", {})["reverseDcf"] = {
            "solveFor": solve_for,
            "solution": solved,
            "lowerBound": lower,
            "upperBound": upper,
        }
    return result


def _build_result(
    data: LocalFinancialData,
    assumptions: dict[str, Any],
    model_name: str,
    enterprise_value: float,
    equity_value: float,
    shares: float,
    warnings: list[ValuationWarning],
    drivers: dict[str, Any],
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fair_value = equity_value / shares
    current_price = data.latest_value("current_share_price")
    market_cap = data.latest_value("market_cap")
    upside = safe_divide(fair_value, current_price)
    upside = upside - 1 if upside is not None else None
    margin = safe_divide(fair_value - current_price, fair_value) if current_price is not None else None
    required_margin = _num(_get_path(assumptions, "marginOfSafety.required")) or 0.0
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "ticker": data.ticker,
        "valuationDate": str(assumptions.get("valuationDate") or date.today().isoformat()),
        "scenario": str(assumptions.get("scenario") or "base"),
        "model": model_name,
        "currency": str(assumptions.get("currency") or "USD"),
        "market": {
            "currentSharePrice": current_price,
            "marketCap": market_cap,
            "priceDate": data.latest_price_date,
        },
        "valuation": {
            "enterpriseValue": enterprise_value,
            "equityValue": equity_value,
            "fairValuePerShare": fair_value,
            "currentSharePrice": current_price,
            "upsideDownside": upside,
            "marginOfSafety": margin,
            "requiredMarginOfSafety": required_margin,
            "meetsRequiredMarginOfSafety": bool(margin is not None and margin >= required_margin),
        },
        "drivers": drivers,
        "warnings": [warning.to_dict() for warning in warnings],
    }
    if debug is not None:
        result["debug"] = debug
    return result


def _sensitivity(data: LocalFinancialData, assumptions: dict[str, Any]) -> dict[str, Any]:
    base_discount = _required_assumption_number(assumptions, "discountingAssumptions.discountRate")
    base_terminal = _required_assumption_number(assumptions, "businessAssumptions.terminalGrowthRate")
    base_margin = _required_assumption_number(assumptions, "businessAssumptions.targetOperatingMargin")
    growth_values = _growth_assumption_values(
        _get_path(assumptions, "businessAssumptions.revenueGrowth"),
        int(_get_path(assumptions, "projection.explicitYears")),
    )
    base_first_five_growth = sum(growth_values[: min(5, len(growth_values))]) / min(5, len(growth_values))
    discount_rates = [max(0.001, base_discount - 0.01), base_discount, base_discount + 0.01]
    terminal_rates = [base_terminal - 0.005, base_terminal, base_terminal + 0.005]
    matrix = []
    for discount_rate in discount_rates:
        row = []
        for terminal_growth in terminal_rates:
            if terminal_growth >= discount_rate:
                row.append(None)
                continue
            scenario = copy.deepcopy(assumptions)
            _set_path(scenario, "discountingAssumptions.discountRate", discount_rate)
            _set_path(scenario, "businessAssumptions.terminalGrowthRate", terminal_growth)
            row.append(_value_fcff_dcf(data, scenario, warnings=[], include_debug=False)["valuation"]["fairValuePerShare"])
        matrix.append(row)
    margin_values = [max(0.0, base_margin - 0.02), base_margin, min(1.0, base_margin + 0.02)]
    growth_values_to_test = [base_first_five_growth - 0.02, base_first_five_growth, base_first_five_growth + 0.02]
    return {
        "discountRateVsTerminalGrowthRate": {
            "discountRates": discount_rates,
            "terminalGrowthRates": terminal_rates,
            "fairValuePerShare": matrix,
        },
        "targetOperatingMargin": [
            {"value": value, "fairValuePerShare": _sensitivity_value(data, assumptions, "businessAssumptions.targetOperatingMargin", value)}
            for value in margin_values
        ],
        "revenueGrowthYears1To5": [
            {"value": value, "fairValuePerShare": _sensitivity_first_five_growth(data, assumptions, value)}
            for value in growth_values_to_test
        ],
    }


def _sensitivity_value(data: LocalFinancialData, assumptions: dict[str, Any], path: str, value: float) -> float:
    scenario = copy.deepcopy(assumptions)
    _set_path(scenario, path, value)
    return _value_fcff_dcf(data, scenario, warnings=[], include_debug=False)["valuation"]["fairValuePerShare"]


def _sensitivity_first_five_growth(data: LocalFinancialData, assumptions: dict[str, Any], value: float) -> float:
    scenario = copy.deepcopy(assumptions)
    growth = _get_path(scenario, "businessAssumptions.revenueGrowth")
    for item in growth[:5]:
        item["value"] = value
    return _value_fcff_dcf(data, scenario, warnings=[], include_debug=False)["valuation"]["fairValuePerShare"]


def _render_valuation_text(result: dict[str, Any]) -> str:
    valuation = result["valuation"]
    drivers = result.get("drivers", {})
    lines = [
        f"{result['ticker']} intrinsic valuation - {result['scenario']} scenario",
        "",
        f"Model: {_display_model(result['model'])}",
        f"Valuation date: {result['valuationDate']}",
        f"Currency: {result['currency']}",
        "",
        f"Current share price: {_money_per_share(valuation.get('currentSharePrice'))}",
        f"Estimated fair value: {_money_per_share(valuation.get('fairValuePerShare'))}",
        f"Upside/downside: {percent(valuation.get('upsideDownside'))}",
        f"Margin of safety: {percent(valuation.get('marginOfSafety'))}",
        f"Required margin of safety: {percent(valuation.get('requiredMarginOfSafety'))}",
        f"Passes margin of safety requirement: {'Yes' if valuation.get('meetsRequiredMarginOfSafety') else 'No'}",
        "",
        "Main assumptions:",
    ]
    for label, key in [
        ("Discount rate", "discountRate"),
        ("Terminal growth", "terminalGrowthRate"),
        ("Target operating margin", "targetOperatingMargin"),
        ("Fair multiple", "fairMultiple"),
        ("Implied assumption", "impliedAssumption"),
    ]:
        if key in drivers:
            value = drivers[key]
            formatted = percent(value) if isinstance(value, (int, float)) and key != "fairMultiple" else _format_driver_value(value)
            lines.append(f"- {label}: {formatted}")
    if "explicitForecastValue" in drivers:
        lines.append(f"- Explicit forecast value: {money(drivers.get('explicitForecastValue'))}")
    if result.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning['message']}" for warning in result["warnings"])
    if result.get("sensitivity"):
        lines.extend(["", _render_sensitivity_text(result["sensitivity"])])
    return "\n".join(lines) + "\n"


def _render_valuation_markdown(result: dict[str, Any]) -> str:
    valuation = result["valuation"]
    lines = [
        f"# {result['ticker']} Intrinsic Valuation - {result['scenario']}",
        "",
        f"- Model: {_display_model(result['model'])}",
        f"- Valuation date: {result['valuationDate']}",
        f"- Current share price: {_money_per_share(valuation.get('currentSharePrice'))}",
        f"- Estimated fair value: {_money_per_share(valuation.get('fairValuePerShare'))}",
        f"- Upside/downside: {percent(valuation.get('upsideDownside'))}",
        f"- Margin of safety: {percent(valuation.get('marginOfSafety'))}",
        f"- Required margin of safety: {percent(valuation.get('requiredMarginOfSafety'))}",
        "",
        "## Drivers",
        "",
    ]
    for key, value in result.get("drivers", {}).items():
        lines.append(f"- {key}: {_format_driver_value(value)}")
    if result.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning['message']}" for warning in result["warnings"])
    if result.get("sensitivity"):
        lines.extend(["", "## Sensitivity", "", _render_sensitivity_text(result["sensitivity"])])
    return "\n".join(lines) + "\n"


def _render_agent_context_markdown(result: dict[str, Any], assumptions: dict[str, Any]) -> str:
    valuation = result["valuation"]
    lines = [
        f"# Valuation Context: {result['ticker']} {result['scenario']}",
        "",
        "## Summary",
        "",
        f"- Ticker: {result['ticker']}",
        f"- Scenario: {result['scenario']}",
        f"- Model: {result['model']}",
        f"- Market price: {_money_per_share(valuation.get('currentSharePrice'))}",
        f"- Fair value: {_money_per_share(valuation.get('fairValuePerShare'))}",
        f"- Margin of safety: {percent(valuation.get('marginOfSafety'))}",
        "",
        "## Main Assumptions",
        "",
    ]
    for key, value in result.get("drivers", {}).items():
        lines.append(f"- {key}: {_format_driver_value(value)}")
    lines.extend(["", "## Main Warnings", ""])
    if result.get("warnings"):
        lines.extend(f"- {warning['message']}" for warning in result["warnings"])
    else:
        lines.append("- No deterministic warnings emitted.")
    lines.extend(["", "## Historical Sanity Checks", ""])
    lines.append("- Compare growth, margins, taxes, and multiples against `research/<TICKER>/metrics/metrics.json` before relying on the scenario.")
    lines.extend(["", "## Sensitivity Summary", ""])
    if result.get("sensitivity"):
        lines.append(_render_sensitivity_text(result["sensitivity"]))
    else:
        lines.append("- Sensitivity was not requested for this run.")
    lines.extend(["", "## Important Caveats", ""])
    lines.extend(
        [
            "- This is deterministic output from the assumptions JSON, not investment advice.",
            "- The assumptions are the thesis; inspect them before interpreting the valuation.",
            "- Do not provide direct buy/sell instructions from this output.",
        ]
    )
    lines.extend(["", "## Assumptions JSON", "", "```json", json.dumps(assumptions, indent=2, sort_keys=True), "```"])
    return "\n".join(lines) + "\n"


def _render_sensitivity_text(sensitivity: dict[str, Any]) -> str:
    matrix = sensitivity.get("discountRateVsTerminalGrowthRate", {})
    discount_rates = matrix.get("discountRates", [])
    terminal_rates = matrix.get("terminalGrowthRates", [])
    values = matrix.get("fairValuePerShare", [])
    lines = ["Sensitivity: discount rate vs terminal growth", ""]
    header = "Discount Rate".ljust(16) + "".join(percent(rate).rjust(12) for rate in terminal_rates)
    lines.append(header)
    for discount_rate, row in zip(discount_rates, values):
        lines.append(percent(discount_rate).ljust(16) + "".join(_money_per_share(value).rjust(12) for value in row))
    return "\n".join(lines)


def _append_common_warnings(
    assumptions: dict[str, Any],
    report: ValidationReport,
    data: LocalFinancialData | None,
) -> None:
    terminal_growth = _num(_get_path(assumptions, "businessAssumptions.terminalGrowthRate"))
    discount_rate = _num(_get_path(assumptions, "discountingAssumptions.discountRate"))
    if terminal_growth is not None and terminal_growth > 0.035:
        report.warnings.append(ValuationWarning("HIGH_TERMINAL_GROWTH", "Terminal growth is above 3.5%."))
    if discount_rate is not None and discount_rate < 0.06:
        report.warnings.append(ValuationWarning("LOW_DISCOUNT_RATE", "Discount rate is below 6%."))
    growth = _growth_assumption_values(
        _get_path(assumptions, "businessAssumptions.revenueGrowth"),
        int(_num(_get_path(assumptions, "projection.explicitYears")) or 0),
        allow_missing=True,
    )
    if sum(1 for value in growth if value is not None and value > 0.15) > 10:
        report.warnings.append(ValuationWarning("SUSTAINED_HIGH_GROWTH", "Revenue growth remains above 15% for more than 10 years."))
    if data is not None:
        margin = _num(_get_path(assumptions, "businessAssumptions.targetOperatingMargin"))
        historical_margins = data.historical_metric_values("operating_margin")
        if margin is not None and historical_margins and margin > max(historical_margins) + 0.05:
            report.warnings.append(
                ValuationWarning(
                    "MARGIN_ABOVE_HISTORY",
                    "Target operating margin is materially higher than the historical maximum.",
                )
            )
        tax_rate = _num(_get_path(assumptions, "businessAssumptions.taxRate"))
        historical_taxes = data.historical_tax_rates()
        if tax_rate is not None and len(historical_taxes) >= 3 and tax_rate < (sum(historical_taxes) / len(historical_taxes)) - 0.05:
            report.warnings.append(
                ValuationWarning("LOW_TAX_RATE_VS_HISTORY", "Tax rate is materially below the historical average.")
            )
    scenario = str(assumptions.get("scenario", "")).lower()
    if scenario == "conservative":
        average_growth = _average([value for value in growth[:5] if value is not None])
        if average_growth is not None and average_growth > 0.12:
            report.warnings.append(
                ValuationWarning("CONSERVATIVE_SCENARIO_HIGH_GROWTH", "Scenario is named conservative but assumes high near-term growth.")
            )
        if discount_rate is not None and discount_rate < 0.08:
            report.warnings.append(
                ValuationWarning("CONSERVATIVE_SCENARIO_LOW_DISCOUNT", "Scenario is named conservative but uses a discount rate below 8%.")
            )


def _with_model_warnings(
    warnings: list[ValuationWarning],
    enterprise_value: float,
    discounted_terminal_value: float,
) -> list[ValuationWarning]:
    result = list(warnings)
    concentration = safe_divide(discounted_terminal_value, enterprise_value)
    if concentration is not None and concentration > 0.6:
        result.append(
            ValuationWarning(
                "TERMINAL_VALUE_CONCENTRATION",
                f"Terminal value represents {concentration * 100:.1f}% of enterprise value.",
            )
        )
    return result


def _validate_revenue_growth(
    assumptions: dict[str, Any],
    report: ValidationReport,
    years: int,
    reverse_solve_for: str | None = None,
) -> None:
    raw_growth = _get_path(assumptions, "businessAssumptions.revenueGrowth")
    if not isinstance(raw_growth, list):
        report.errors.append("businessAssumptions.revenueGrowth must be a list")
        return
    if len(raw_growth) < years:
        report.errors.append("businessAssumptions.revenueGrowth must include one entry per explicit projection year")
        return
    for index, item in enumerate(raw_growth[:years], start=1):
        if reverse_solve_for == "revenueGrowthYears1To5" and index <= 5:
            continue
        value = item.get("value") if isinstance(item, dict) else None
        parsed = _num(value)
        if parsed is None:
            report.errors.append(f"businessAssumptions.revenueGrowth year {index} must be numeric")
        elif parsed <= -1:
            report.errors.append(f"businessAssumptions.revenueGrowth year {index} must be greater than -100%")


def _maybe_required_number(
    assumptions: dict[str, Any],
    path: str,
    report: ValidationReport,
    allow_missing: bool = False,
) -> float | None:
    value = _get_path(assumptions, path)
    if _is_missing(value):
        if not allow_missing:
            report.errors.append(f"{path} is required and must be numeric")
        return None
    parsed = _num(value)
    if parsed is None:
        report.errors.append(f"{path} must be numeric")
    return parsed


def _resolve_numeric(
    value: Any,
    data: LocalFinancialData | None,
    latest_field: str,
    path: str,
    report: ValidationReport,
    required: bool = True,
) -> float | None:
    if value == "use_latest":
        if data is None:
            if required:
                report.errors.append(f"{path} uses use_latest but local financial data is unavailable")
            return None
        resolved = data.latest_value(latest_field)
        if resolved is None and required:
            report.errors.append(f"{path} could not be resolved from local financial data")
        return resolved
    if _is_missing(value):
        if required:
            report.errors.append(f"{path} is required and must be numeric")
        return None
    parsed = _num(value)
    if parsed is None:
        report.errors.append(f"{path} must be numeric or use_latest")
    return parsed


def _validate_capital_structure_adjustments(
    assumptions: dict[str, Any],
    report: ValidationReport,
    data: LocalFinancialData | None,
    cash_debt_required: bool,
) -> tuple[float | None, float | None]:
    cash = _resolve_numeric(
        _get_path(assumptions, "capitalStructureAdjustments.cashAndEquivalents"),
        data,
        "cash_and_equivalents",
        "capitalStructureAdjustments.cashAndEquivalents",
        report,
        required=cash_debt_required,
    )
    debt = _resolve_numeric(
        _get_path(assumptions, "capitalStructureAdjustments.totalDebt"),
        data,
        "total_debt",
        "capitalStructureAdjustments.totalDebt",
        report,
        required=cash_debt_required,
    )
    if cash is not None and cash < 0:
        report.errors.append("capitalStructureAdjustments.cashAndEquivalents must be non-negative")
    if debt is not None and debt < 0:
        report.errors.append("capitalStructureAdjustments.totalDebt must be non-negative")
    _validate_optional_non_negative_number(
        assumptions,
        "capitalStructureAdjustments.minorityInterest",
        report,
    )
    _validate_optional_non_negative_number(
        assumptions,
        "capitalStructureAdjustments.nonOperatingAssets",
        report,
    )
    return cash, debt


def _validate_optional_non_negative_number(
    assumptions: dict[str, Any],
    path: str,
    report: ValidationReport,
) -> float | None:
    raw = _get_path(assumptions, path)
    if _is_missing(raw):
        return None
    parsed = _num(raw)
    if parsed is None:
        report.errors.append(f"{path} must be numeric")
        return None
    if parsed < 0:
        report.errors.append(f"{path} must be non-negative")
    return parsed


def _format_invalid_assumptions(path: str | Path, report: ValidationReport) -> str:
    return render_validation_report(path, report)


def _normalize_model(model: str) -> str:
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported model: {model}")
    return model


def _latest_period_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}

    def key(row: dict[str, Any]) -> tuple[int, int, str]:
        fiscal_year = row.get("fiscalYear")
        period = str(row.get("period", ""))
        if isinstance(fiscal_year, int):
            return fiscal_year, _period_rank(period), period
        if period[:4].isdigit():
            return int(period[:4]), _period_rank(period), period
        return 0, _period_rank(period), period

    return sorted(rows, key=key)[-1]


def _period_rank(period: str) -> int:
    text = str(period).upper()
    for rank, marker in [(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4")]:
        if marker in text:
            return rank
    if "FY" in text:
        return 5
    if "TTM" in text:
        return 6
    return 0


def _latest_ttm_from_company_facts(facts: dict[str, Any]) -> dict[str, Any]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {}) if isinstance(facts, dict) else {}
    if not us_gaap:
        return {}
    annual_revenue = _latest_annual_fact(us_gaap, ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"), "USD")
    if not annual_revenue:
        return {}
    latest_ytd_revenue = _latest_ytd_after_annual(
        us_gaap,
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "USD",
        annual_revenue,
    )
    if not latest_ytd_revenue:
        return {}
    row: dict[str, Any] = {
        "period": f"{latest_ytd_revenue.get('fy')}-{latest_ytd_revenue.get('fp')}-TTM",
        "fiscalYear": latest_ytd_revenue.get("fy"),
        "source": "company_facts_ttm",
        "latestQuarterEnd": latest_ytd_revenue.get("end"),
        "latestQuarterFiled": latest_ytd_revenue.get("filed"),
    }
    duration_fields = {
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "operatingIncome": ("OperatingIncomeLoss",),
        "netIncome": ("NetIncomeLoss", "ProfitLoss"),
        "pretaxIncome": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
        "incomeTaxExpense": ("IncomeTaxExpenseBenefit",),
        "operatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
        "capitalExpenditures": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    }
    for field_name, concepts in duration_fields.items():
        value = _ttm_value(us_gaap, concepts, "USD", annual_revenue, latest_ytd_revenue)
        if value is not None:
            row[field_name] = value
    shares = _latest_duration_fact_after_annual(
        us_gaap,
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingDiluted"),
        "shares",
        annual_revenue,
    )
    if shares is not None:
        row["dilutedShares"] = _num(shares.get("val"))
    cash = _latest_instant_value(
        us_gaap,
        ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        "USD",
        after_end=str(annual_revenue.get("end", "")),
    )
    if cash is not None:
        row["cash"] = cash
    current_debt = _latest_instant_value(
        us_gaap,
        ("ShortTermBorrowings", "ShortTermDebt", "LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"),
        "USD",
        after_end=str(annual_revenue.get("end", "")),
    )
    noncurrent_debt = _latest_instant_value(
        us_gaap,
        ("LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"),
        "USD",
        after_end=str(annual_revenue.get("end", "")),
    )
    total_debt = _latest_instant_value(us_gaap, ("LongTermDebt",), "USD", after_end=str(annual_revenue.get("end", "")))
    if current_debt is not None or noncurrent_debt is not None:
        row["totalDebt"] = (current_debt or 0.0) + (noncurrent_debt or 0.0)
    elif total_debt is not None:
        row["totalDebt"] = total_debt
    if row.get("operatingCashFlow") is not None and row.get("capitalExpenditures") is not None:
        row["freeCashFlow"] = row["operatingCashFlow"] - abs(row["capitalExpenditures"])
    return row


def _ttm_value(
    us_gaap: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
    annual_fact: dict[str, Any],
    latest_ytd_fact: dict[str, Any],
) -> float | None:
    annual = _num(annual_fact.get("val")) if _concept_contains_fact(us_gaap, concepts, annual_fact, unit) else None
    latest_ytd = _num(latest_ytd_fact.get("val")) if _concept_contains_fact(us_gaap, concepts, latest_ytd_fact, unit) else None
    if annual is None:
        annual_fact_for_concept = _latest_annual_fact(us_gaap, concepts, unit)
        annual = _num(annual_fact_for_concept.get("val")) if annual_fact_for_concept else None
    if latest_ytd is None:
        latest_ytd_fact_for_concept = _matching_duration_fact(us_gaap, concepts, unit, latest_ytd_fact.get("start"), latest_ytd_fact.get("end"))
        latest_ytd = _num(latest_ytd_fact_for_concept.get("val")) if latest_ytd_fact_for_concept else None
    prior_ytd_fact = _matching_duration_fact(
        us_gaap,
        concepts,
        unit,
        annual_fact.get("start"),
        _previous_year_date(str(latest_ytd_fact.get("end", ""))),
    )
    prior_ytd = _num(prior_ytd_fact.get("val")) if prior_ytd_fact else None
    if annual is None or latest_ytd is None or prior_ytd is None:
        return None
    return annual + latest_ytd - prior_ytd


def _latest_annual_fact(us_gaap: dict[str, Any], concepts: tuple[str, ...], unit: str) -> dict[str, Any] | None:
    facts = [
        fact
        for fact in _facts_for_concepts(us_gaap, concepts, unit)
        if fact.get("form") in {"10-K", "10-K/A"} and fact.get("fp") == "FY" and _looks_like_annual_fact(fact)
    ]
    return sorted(facts, key=_fact_sort_key)[-1] if facts else None


def _latest_ytd_after_annual(
    us_gaap: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
    annual_fact: dict[str, Any],
) -> dict[str, Any] | None:
    annual_end = str(annual_fact.get("end", ""))
    facts = [
        fact
        for fact in _facts_for_concepts(us_gaap, concepts, unit)
        if fact.get("form") in {"10-Q", "10-Q/A"}
        and str(fact.get("start", "")) > annual_end
        and str(fact.get("end", "")) > annual_end
        and _duration_days(fact) >= 60
    ]
    return sorted(facts, key=_interim_duration_sort_key)[-1] if facts else None


def _latest_duration_fact_after_annual(
    us_gaap: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
    annual_fact: dict[str, Any],
) -> dict[str, Any] | None:
    annual_end = str(annual_fact.get("end", ""))
    facts = [
        fact
        for fact in _facts_for_concepts(us_gaap, concepts, unit)
        if fact.get("form") in {"10-Q", "10-Q/A"}
        and str(fact.get("start", "")) > annual_end
        and str(fact.get("end", "")) > annual_end
    ]
    return sorted(facts, key=_interim_duration_sort_key)[-1] if facts else None


def _latest_instant_value(
    us_gaap: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
    after_end: str,
) -> float | None:
    facts = [
        fact
        for fact in _facts_for_concepts(us_gaap, concepts, unit)
        if fact.get("form") in {"10-Q", "10-Q/A", "10-K", "10-K/A"}
        and fact.get("end")
        and str(fact.get("end")) > after_end
        and not fact.get("start")
    ]
    if not facts:
        return None
    return _num(sorted(facts, key=_fact_sort_key)[-1].get("val"))


def _matching_duration_fact(
    us_gaap: dict[str, Any],
    concepts: tuple[str, ...],
    unit: str,
    start: Any,
    end: Any,
) -> dict[str, Any] | None:
    facts = [
        fact
        for fact in _facts_for_concepts(us_gaap, concepts, unit)
        if fact.get("start") == start and fact.get("end") == end and fact.get("form") in {"10-Q", "10-Q/A", "10-K", "10-K/A"}
    ]
    return sorted(facts, key=_fact_sort_key)[-1] if facts else None


def _concept_contains_fact(us_gaap: dict[str, Any], concepts: tuple[str, ...], fact: dict[str, Any], unit: str) -> bool:
    return any(fact in _facts_for_concepts(us_gaap, (concept,), unit) for concept in concepts)


def _facts_for_concepts(us_gaap: dict[str, Any], concepts: tuple[str, ...], unit: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for concept in concepts:
        units = us_gaap.get(concept, {}).get("units", {})
        if unit in units:
            facts.extend(units.get(unit, []))
    return facts


def _looks_like_annual_fact(fact: dict[str, Any]) -> bool:
    days = _duration_days(fact)
    return 300 <= days <= 380


def _duration_days(fact: dict[str, Any]) -> int:
    start = parse_iso_date(str(fact.get("start", "") or ""))
    end = parse_iso_date(str(fact.get("end", "") or ""))
    if start is None or end is None:
        return 0
    return (end - start).days


def _previous_year_date(value: str) -> str | None:
    parsed = parse_iso_date(value)
    if parsed is None:
        return None
    try:
        return parsed.replace(year=parsed.year - 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year - 1, day=28).isoformat()


def _fact_sort_key(fact: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(fact.get("end", "")),
        str(fact.get("filed", "")),
        str(fact.get("start", "")),
        str(fact.get("accn", "")),
    )


def _interim_duration_sort_key(fact: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(fact.get("end", "")),
        str(fact.get("filed", "")),
        _duration_days(fact),
        str(fact.get("accn", "")),
    )


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _first_positive_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(row.get(key))
        if value is not None and value > 0:
            return value
    return None


def _first_not_none(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _price_value(row: dict[str, Any]) -> float | None:
    for key in ("close", "adjustedClose"):
        value = _num(row.get(key))
        if value is not None and value > 0:
            return value
    return None


def _price_row_matches_ticker(row: dict[str, Any], ticker: str) -> bool:
    row_ticker = str(row.get("ticker", "") or "").upper()
    return row_ticker in {"", ticker.upper()}


def _safe_filename_part(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    cleaned = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "-"
        for char in raw
    ).strip("._-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or fallback


def _resolve_workspace_path(path: str | Path, cwd: str | Path | None = None) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute() and cwd is not None:
        resolved = Path(cwd) / resolved
    return resolved


def _free_cash_flow(row: dict[str, Any]) -> float | None:
    operating_cash_flow = _first_num(row, "operatingCashFlow", "operating_cash_flow")
    capex = _first_num(row, "capitalExpenditures", "capital_expenditures")
    if operating_cash_flow is None or capex is None:
        return None
    return operating_cash_flow + capex if capex < 0 else operating_cash_flow - capex


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    current = data
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _growth_assumption_values(raw_growth: Any, years: int, allow_missing: bool = False) -> list[float | None]:
    if isinstance(raw_growth, bool):
        return []
    if isinstance(raw_growth, (int, float)):
        parsed = _num(raw_growth)
        if parsed is None:
            return []
        return [parsed] * years
    if not isinstance(raw_growth, list):
        return []
    values: list[float | None] = []
    for item in raw_growth[:years]:
        value = item.get("value") if isinstance(item, dict) else item
        parsed = _num(value)
        values.append(parsed if parsed is not None else (None if allow_missing else parsed))
    return values


def _required_latest(data: LocalFinancialData, field_name: str) -> float:
    value = data.latest_value(field_name)
    if value is None:
        raise ValueError(f"Local financial data is missing required field: {field_name}")
    return value


def _required_assumption_number(assumptions: dict[str, Any], path: str) -> float:
    value = _num(_get_path(assumptions, path))
    if value is None:
        raise ValueError(f"Missing required numeric assumption: {path}")
    return value


def _resolved_required_number(assumptions: dict[str, Any], data: LocalFinancialData, path: str, latest_field: str) -> float:
    raw = _get_path(assumptions, path)
    value = data.latest_value(latest_field) if raw == "use_latest" else _num(raw)
    if value is None:
        raise ValueError(f"Missing required numeric assumption: {path}")
    return value


def _resolved_optional_number(
    assumptions: dict[str, Any], data: LocalFinancialData, path: str, latest_field: str
) -> float | None:
    raw = _get_path(assumptions, path)
    if raw == "use_latest":
        return data.latest_value(latest_field)
    return _num(raw)


def _reverse_target_value(data: LocalFinancialData, assumptions: dict[str, Any], target_basis: str) -> float:
    if target_basis == "current_market_price":
        current = assumptions.get("currentSharePrice")
        value = data.latest_value("current_share_price") if current == "use_latest" or current is None else _num(current)
    elif target_basis == "custom_share_price":
        value = _num(assumptions.get("targetSharePrice"))
    else:
        value = _num(assumptions.get("targetEnterpriseValue"))
    if value is None or value <= 0:
        raise ValueError(f"Could not resolve reverse DCF target value for {target_basis}")
    return value


def _reverse_bounds(assumptions: dict[str, Any], solve_for: str) -> tuple[float, float]:
    terminal_growth = _num(_get_path(assumptions, "businessAssumptions.terminalGrowthRate")) or 0.0
    discount_rate = _num(_get_path(assumptions, "discountingAssumptions.discountRate")) or 0.1
    if solve_for == "revenueGrowthYears1To5":
        return -0.5, 0.5
    if solve_for == "targetOperatingMargin":
        return 0.01, 0.8
    if solve_for == "terminalGrowthRate":
        return -0.02, max(-0.019, discount_rate - 0.001)
    if solve_for == "discountRate":
        return max(terminal_growth + 0.001, 0.01), 0.3
    raise ValueError(f"Unsupported reverse DCF solve target: {solve_for}")


def _apply_reverse_candidate(assumptions: dict[str, Any], solve_for: str, candidate: float) -> None:
    if solve_for == "revenueGrowthYears1To5":
        growth = _get_path(assumptions, "businessAssumptions.revenueGrowth")
        for item in growth[:5]:
            item["value"] = candidate
    elif solve_for == "targetOperatingMargin":
        _set_path(assumptions, "businessAssumptions.targetOperatingMargin", candidate)
    elif solve_for == "terminalGrowthRate":
        _set_path(assumptions, "businessAssumptions.terminalGrowthRate", candidate)
    elif solve_for == "discountRate":
        _set_path(assumptions, "discountingAssumptions.discountRate", candidate)
    else:
        raise ValueError(f"Unsupported reverse DCF solve target: {solve_for}")


def _bisect_monotonic(objective: Any, target: float, lower: float, upper: float) -> float:
    lower_value = objective(lower)
    upper_value = objective(upper)
    increasing = upper_value > lower_value
    lo, hi = lower, upper
    for _ in range(80):
        mid = (lo + hi) / 2
        value = objective(mid)
        if abs(value - target) <= max(0.0001, target * 0.000001):
            return mid
        if increasing:
            if value < target:
                lo = mid
            else:
                hi = mid
        else:
            if value > target:
                lo = mid
            else:
                hi = mid
    return (lo + hi) / 2


def _historical_multiple_values(data: LocalFinancialData, metric_type: str) -> list[float]:
    mapping = {
        "earnings": "price_to_earnings",
        "freeCashFlow": "price_to_free_cash_flow",
        "revenue": "ev_to_revenue",
        "operatingIncome": "ev_to_ebit",
    }
    field_name = mapping.get(metric_type)
    return [value for value in data.historical_metric_values(field_name) if value > 0] if field_name else []


def _assumption_differences(assumptions: list[dict[str, Any]]) -> list[str]:
    paths = [
        "discountingAssumptions.discountRate",
        "businessAssumptions.terminalGrowthRate",
        "businessAssumptions.targetOperatingMargin",
        "businessAssumptions.reinvestmentRate",
        "businessAssumptions.fairMultiple",
        "businessAssumptions.metricType",
    ]
    differences = []
    for path in paths:
        values = []
        for item in assumptions:
            scenario = item.get("scenario", "scenario")
            value = _get_path(item, path)
            if value is not None:
                values.append(f"{scenario}: {_format_driver_value(value)}")
        if len({entry.split(': ', 1)[1] for entry in values}) > 1:
            differences.append(f"{path}: " + "; ".join(values))
    return differences


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _money_per_share(value: Any) -> str:
    parsed = _num(value)
    if parsed is None:
        return "n/a"
    return f"${parsed:,.2f}"


def _format_driver_value(value: Any) -> str:
    if isinstance(value, float):
        if abs(value) < 1:
            return percent(value)
        return f"{value:,.2f}"
    return str(value)


def _display_model(model: str) -> str:
    return {
        "fcff-dcf": "FCFF DCF",
        "owner-earnings-dcf": "Owner Earnings DCF",
        "reverse-dcf": "Reverse DCF",
        "epv": "Earnings Power Value",
        "multiples": "Multiples",
    }.get(model, model)
