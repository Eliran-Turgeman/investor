from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any


CAPITAL_GAINS_TAX_RATE = 0.25
NII_REDUCED_ANNUAL_THRESHOLD_ILS = 7_703 * 12
NII_ANNUAL_CAP_ILS = 51_910 * 12
NII_REDUCED_EMPLOYEE_RATE = 0.0427
NII_FULL_EMPLOYEE_RATE = 0.1217
SCENARIO_QUALIFIED = "qualified"
SCENARIO_EARLY = "early"


@dataclass(slots=True)
class PriceResolution:
    price_usd: float
    source: str
    first_date: date | None
    last_date: date | None
    row_count: int


@dataclass(slots=True)
class RsuTaxInputs:
    shares: float
    grant_price_usd: float
    sale_price_usd: float
    fx_usd_ils: float
    ordinary_tax_rate: float
    sale_fees_ils: float = 0.0
    capital_gain_offset_ils: float = 0.0
    salary_ytd_ils: float | None = None
    ticker: str | None = None
    grant_date: date | None = None
    sale_date: date | None = None
    grant_price_source: str | None = None
    sale_price_source: str | None = None
    fx_source: str | None = None
    selected_scenario: str | None = None
    scenario_source: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class RsuScenarioResult:
    name: str
    gross_sale_ils: float
    grant_value_ils: float
    net_sale_before_tax_ils: float
    ordinary_component_ils: float
    capital_gain_ils: float
    capital_gain_taxable_ils: float
    ordinary_tax_ils: float
    capital_gains_tax_ils: float
    ni_health_ils: float | None
    total_tax_ils: float
    net_proceeds_ils: float
    effective_tax_rate: float | None


@dataclass(slots=True)
class RsuTaxResult:
    inputs: RsuTaxInputs
    compliant: RsuScenarioResult
    early_sale: RsuScenarioResult
    selected_scenario: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": _jsonable(asdict(self.inputs)),
            "compliant": asdict(self.compliant),
            "early_sale": asdict(self.early_sale),
            "selected_scenario": self.selected_scenario,
        }

    @property
    def selected_result(self) -> RsuScenarioResult | None:
        if self.selected_scenario == SCENARIO_QUALIFIED:
            return self.compliant
        if self.selected_scenario == SCENARIO_EARLY:
            return self.early_sale
        return None


def calculate_rsu_tax(inputs: RsuTaxInputs) -> RsuTaxResult:
    _validate_inputs(inputs)
    selected_scenario = inputs.selected_scenario
    if selected_scenario is None and inputs.grant_date is not None and inputs.sale_date is not None:
        selected_scenario = infer_102_scenario(inputs.grant_date, inputs.sale_date)
        inputs.selected_scenario = selected_scenario
        inputs.scenario_source = "inferred from grant date and sale date"

    gross_sale = inputs.shares * inputs.sale_price_usd * inputs.fx_usd_ils
    grant_value = inputs.shares * inputs.grant_price_usd * inputs.fx_usd_ils
    net_sale = max(0.0, gross_sale - inputs.sale_fees_ils)

    compliant_ordinary = min(net_sale, grant_value)
    compliant_capital_gain = max(0.0, net_sale - compliant_ordinary)
    compliant = _build_scenario(
        name="Qualified Section 102 capital-gains track",
        gross_sale=gross_sale,
        grant_value=grant_value,
        net_sale=net_sale,
        ordinary_component=compliant_ordinary,
        capital_gain=compliant_capital_gain,
        inputs=inputs,
    )

    early_sale = _build_scenario(
        name="Early / non-compliant sale estimate",
        gross_sale=gross_sale,
        grant_value=grant_value,
        net_sale=net_sale,
        ordinary_component=net_sale,
        capital_gain=0.0,
        inputs=inputs,
    )
    return RsuTaxResult(
        inputs=inputs,
        compliant=compliant,
        early_sale=early_sale,
        selected_scenario=selected_scenario,
    )


def average_grant_price(
    price_rows: list[dict[str, Any]],
    grant_date: date,
    lookback_days: int = 30,
) -> PriceResolution:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be greater than zero")
    start_date = grant_date - timedelta(days=lookback_days - 1)
    rows = [
        (row_date, close)
        for row_date, close in (_price_row_value(row) for row in price_rows)
        if row_date is not None and close is not None and start_date <= row_date <= grant_date
    ]
    if not rows:
        raise ValueError(
            f"no market price rows found from {start_date.isoformat()} through {grant_date.isoformat()}"
        )
    rows.sort(key=lambda item: item[0])
    average = sum(close for _, close in rows) / len(rows)
    return PriceResolution(
        price_usd=average,
        source=(
            "30-calendar-day average close "
            f"({rows[0][0].isoformat()}..{rows[-1][0].isoformat()}, {len(rows)} trading rows)"
        ),
        first_date=rows[0][0],
        last_date=rows[-1][0],
        row_count=len(rows),
    )


def latest_sale_price(price_rows: list[dict[str, Any]], sale_date: date) -> PriceResolution:
    rows = [
        (row_date, close, str(row.get("source") or "market provider"))
        for row in price_rows
        for row_date, close in [_price_row_value(row)]
        if row_date is not None and close is not None and row_date <= sale_date
    ]
    if not rows:
        raise ValueError(f"no market price rows found on or before {sale_date.isoformat()}")
    rows.sort(key=lambda item: item[0])
    row_date, close, source = rows[-1]
    return PriceResolution(
        price_usd=close,
        source=f"{source} close on {row_date.isoformat()}",
        first_date=row_date,
        last_date=row_date,
        row_count=1,
    )


def infer_102_scenario(grant_date: date, sale_date: date) -> str:
    if sale_date < grant_date:
        raise ValueError("sale date cannot be before grant date")
    if sale_date >= add_years(grant_date, 2):
        return SCENARIO_QUALIFIED
    return SCENARIO_EARLY


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return date(value.year + years, 3, 1)


def render_rsu_tax_summary(result: RsuTaxResult) -> str:
    lines = [
        "Israeli Section 102 RSU Tax Estimate",
        "====================================",
        "",
    ]
    selected = result.selected_result
    if selected is not None:
        lines.extend([f"Scenario: {selected.name}", ""])
        lines.extend(_input_lines(result.inputs))
        lines.extend(["", *_scenario_lines(selected)])
    else:
        lines.extend(["Scenario: not selected - showing comparison", ""])
        lines.extend(_input_lines(result.inputs))
        lines.extend(
            [
                "",
                *_scenario_lines(result.compliant, include_name=True),
                "",
                *_scenario_lines(result.early_sale, include_name=True),
            ]
        )

    if result.inputs.warnings:
        lines.extend(["", "Warnings"])
        lines.extend(f"- {warning}" for warning in result.inputs.warnings)

    lines.extend(
        [
            "",
            "Notes",
            "- Estimate only, not tax advice.",
            "- Assumes Israeli tax resident and Section 102 trustee capital-gains track RSU treatment.",
            "- Grant baseline is a 30-calendar-day average close unless manually overridden.",
            "- NI/health is estimated only when salary/YTD is supplied and uses 2026 employee thresholds/rates.",
        ]
    )
    return "\n".join(lines)


def normalize_rate(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("ordinary tax rate must be finite")
    if value > 1:
        value = value / 100
    if value < 0 or value > 1:
        raise ValueError("ordinary tax rate must be between 0 and 1, or between 0 and 100 as a percentage")
    return value


def _build_scenario(
    name: str,
    gross_sale: float,
    grant_value: float,
    net_sale: float,
    ordinary_component: float,
    capital_gain: float,
    inputs: RsuTaxInputs,
) -> RsuScenarioResult:
    taxable_capital_gain = max(0.0, capital_gain - inputs.capital_gain_offset_ils)
    ordinary_tax = ordinary_component * inputs.ordinary_tax_rate
    capital_gains_tax = taxable_capital_gain * CAPITAL_GAINS_TAX_RATE
    ni_health = (
        estimate_ni_health(inputs.salary_ytd_ils, ordinary_component)
        if inputs.salary_ytd_ils is not None
        else None
    )
    total_tax = ordinary_tax + capital_gains_tax + (ni_health or 0.0)
    net_proceeds = net_sale - total_tax
    effective_rate = total_tax / net_sale if net_sale else None
    return RsuScenarioResult(
        name=name,
        gross_sale_ils=gross_sale,
        grant_value_ils=grant_value,
        net_sale_before_tax_ils=net_sale,
        ordinary_component_ils=ordinary_component,
        capital_gain_ils=capital_gain,
        capital_gain_taxable_ils=taxable_capital_gain,
        ordinary_tax_ils=ordinary_tax,
        capital_gains_tax_ils=capital_gains_tax,
        ni_health_ils=ni_health,
        total_tax_ils=total_tax,
        net_proceeds_ils=net_proceeds,
        effective_tax_rate=effective_rate,
    )


def estimate_ni_health(salary_ytd_ils: float, ordinary_income_ils: float) -> float:
    if not math.isfinite(salary_ytd_ils) or not math.isfinite(ordinary_income_ils):
        raise ValueError("NI/health inputs must be finite")
    if salary_ytd_ils < 0:
        raise ValueError("salary_ytd_ils cannot be negative")
    if ordinary_income_ils <= 0:
        return 0.0
    before = _annual_ni_health(salary_ytd_ils)
    after = _annual_ni_health(salary_ytd_ils + ordinary_income_ils)
    return max(0.0, after - before)


def _annual_ni_health(income_ils: float) -> float:
    reduced_income = min(max(income_ils, 0.0), NII_REDUCED_ANNUAL_THRESHOLD_ILS)
    full_income = min(
        max(income_ils - NII_REDUCED_ANNUAL_THRESHOLD_ILS, 0.0),
        NII_ANNUAL_CAP_ILS - NII_REDUCED_ANNUAL_THRESHOLD_ILS,
    )
    return reduced_income * NII_REDUCED_EMPLOYEE_RATE + full_income * NII_FULL_EMPLOYEE_RATE


def _validate_inputs(inputs: RsuTaxInputs) -> None:
    _require_finite(inputs.shares, "shares")
    _require_finite(inputs.grant_price_usd, "grant price")
    _require_finite(inputs.sale_price_usd, "sale price")
    _require_finite(inputs.fx_usd_ils, "fx_usd_ils")
    _require_finite(inputs.sale_fees_ils, "sale fees")
    _require_finite(inputs.capital_gain_offset_ils, "capital gain offset")
    if inputs.salary_ytd_ils is not None:
        _require_finite(inputs.salary_ytd_ils, "salary_ytd_ils")
    if inputs.shares <= 0:
        raise ValueError("shares must be greater than zero")
    if inputs.grant_price_usd < 0:
        raise ValueError("grant price cannot be negative")
    if inputs.sale_price_usd < 0:
        raise ValueError("sale price cannot be negative")
    if inputs.fx_usd_ils <= 0:
        raise ValueError("fx_usd_ils must be greater than zero")
    if inputs.sale_fees_ils < 0:
        raise ValueError("sale fees cannot be negative")
    if inputs.capital_gain_offset_ils < 0:
        raise ValueError("capital gain offset cannot be negative")
    inputs.ordinary_tax_rate = normalize_rate(inputs.ordinary_tax_rate)
    if inputs.selected_scenario is not None:
        inputs.selected_scenario = _normalize_scenario(inputs.selected_scenario)
    if inputs.grant_date is not None and inputs.sale_date is not None and inputs.sale_date < inputs.grant_date:
        raise ValueError("sale date cannot be before grant date")


def _require_finite(value: float, label: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")


def _normalize_scenario(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"qualified", "compliant", "qualified-102"}:
        return SCENARIO_QUALIFIED
    if normalized in {"early", "early-sale", "non-compliant", "noncompliant"}:
        return SCENARIO_EARLY
    raise ValueError("selected scenario must be 'qualified' or 'early'")


def _input_lines(inputs: RsuTaxInputs) -> list[str]:
    rows = [
        ("Ticker", inputs.ticker or "n/a"),
        ("Shares", f"{inputs.shares:g}"),
        ("Grant date", inputs.grant_date.isoformat() if inputs.grant_date else "n/a"),
        ("Sale date", inputs.sale_date.isoformat() if inputs.sale_date else "today / n/a"),
        ("Grant baseline", _with_source(_usd(inputs.grant_price_usd), inputs.grant_price_source)),
        ("Sale price", _with_source(_usd(inputs.sale_price_usd), inputs.sale_price_source)),
        ("USD/ILS FX", _with_source(f"{inputs.fx_usd_ils:.4f}", inputs.fx_source)),
        ("Ordinary tax rate", _pct(inputs.ordinary_tax_rate)),
        ("Sale fees", _ils(inputs.sale_fees_ils)),
        ("Capital-gain offset", _ils(inputs.capital_gain_offset_ils)),
        (
            "Salary/YTD NI+health",
            _ils(inputs.salary_ytd_ils) if inputs.salary_ytd_ils is not None else "not supplied",
        ),
    ]
    if inputs.scenario_source:
        rows.append(("Scenario source", inputs.scenario_source))
    return ["Inputs", *_kv_lines(rows)]


def _scenario_lines(scenario: RsuScenarioResult, include_name: bool = False) -> list[str]:
    tax_rows = [
        ("Gross sale", _ils(scenario.gross_sale_ils)),
        ("Grant value baseline", _ils(scenario.grant_value_ils)),
        ("Net sale before tax", _ils(scenario.net_sale_before_tax_ils)),
        ("Ordinary-income component", _ils(scenario.ordinary_component_ils)),
        ("Capital-gain component", _ils(scenario.capital_gain_ils)),
        ("Taxable capital gain", _ils(scenario.capital_gain_taxable_ils)),
        ("Ordinary income tax", _ils(scenario.ordinary_tax_ils)),
        ("Capital gains tax", _ils(scenario.capital_gains_tax_ils)),
        (
            "Estimated NI + health",
            _ils(scenario.ni_health_ils) if scenario.ni_health_ils is not None else "not calculated",
        ),
    ]
    bottom_rows = [
        ("Total tax estimate", _ils(scenario.total_tax_ils)),
        ("Estimated net proceeds", _ils(scenario.net_proceeds_ils)),
        ("Effective tax rate", _pct(scenario.effective_tax_rate)),
    ]
    lines = [
        "Tax Breakdown",
        *_kv_lines(tax_rows),
        "",
        "Bottom Line",
        *_kv_lines(bottom_rows),
    ]
    if include_name:
        return [scenario.name, "", *lines]
    return lines


def _kv_lines(rows: list[tuple[str, str]]) -> list[str]:
    width = max(len(label) for label, _ in rows)
    return [f"{label:<{width}}  {value}" for label, value in rows]


def _price_row_value(row: dict[str, Any]) -> tuple[date | None, float | None]:
    row_date = _parse_row_date(row.get("date"))
    close = row.get("close")
    if close in (None, ""):
        close = row.get("adjustedClose")
    try:
        close_value = float(close)
    except (TypeError, ValueError):
        close_value = None
    if close_value is not None and (not math.isfinite(close_value) or close_value <= 0):
        close_value = None
    return row_date, close_value


def _parse_row_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _with_source(value: str, source: str | None) -> str:
    if not source:
        return value
    return f"{value} ({source})"


def _ils(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"ILS {value:,.2f}"


def _usd(value: float) -> str:
    return f"USD {value:,.4f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_jsonable(item) for item in value)
    if isinstance(value, date):
        return value.isoformat()
    return value
