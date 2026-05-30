from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import money, percent, read_json, safe_divide, write_json, write_text


ALIASES: dict[str, tuple[str, ...]] = {
    "period": ("period",),
    "revenue": ("revenue",),
    "gross_profit": ("gross_profit", "grossProfit"),
    "operating_income": ("operating_income", "operatingIncome"),
    "net_income": ("net_income", "netIncome"),
    "interest_expense": ("interest_expense", "interestExpense"),
    "weighted_average_diluted_shares": (
        "weighted_average_diluted_shares",
        "weightedAverageDilutedShares",
        "dilutedShares",
    ),
    "pretax_income": ("pretax_income", "pretaxIncome"),
    "income_tax_expense": ("income_tax_expense", "incomeTaxExpense"),
    "cash_and_equivalents": ("cash_and_equivalents", "cash", "cashAndEquivalents"),
    "total_debt": ("total_debt", "totalDebt"),
    "total_equity": ("total_equity", "equity", "stockholdersEquity"),
    "total_assets": ("total_assets", "assets", "totalAssets"),
    "operating_cash_flow": ("operating_cash_flow", "operatingCashFlow"),
    "capital_expenditures": ("capital_expenditures", "capitalExpenditures"),
    "dividends_paid": ("dividends_paid", "dividends"),
    "share_repurchases": ("share_repurchases", "buybacks"),
    "stock_based_compensation": ("stock_based_compensation", "stockBasedCompensation"),
    "price": ("price", "close", "adjustedClose"),
    "shares_outstanding": ("shares_outstanding", "sharesOutstanding"),
    "market_cap": ("market_cap", "marketCap"),
}


def calculate_metrics(
    ticker: str,
    income_statements: list[dict[str, Any]] | None = None,
    balance_sheets: list[dict[str, Any]] | None = None,
    cash_flows: list[dict[str, Any]] | None = None,
    market_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    income_by_period = _by_period(income_statements or [])
    balance_by_period = _by_period(balance_sheets or [])
    cash_by_period = _by_period(cash_flows or [])
    market_by_period = _by_period(market_data or [])
    periods = sorted(set(income_by_period) | set(balance_by_period) | set(cash_by_period) | set(market_by_period))
    rows: list[dict[str, Any]] = []
    for index, period in enumerate(periods):
        prior_period = periods[index - 1] if index > 0 else None
        income = income_by_period.get(period, {})
        balance = balance_by_period.get(period, {})
        cash = cash_by_period.get(period, {})
        market = market_by_period.get(period, {})
        prior_income = income_by_period.get(prior_period, {}) if prior_period else {}
        prior_balance = balance_by_period.get(prior_period, {}) if prior_period else {}

        revenue = _num(income, "revenue")
        gross_profit = _num(income, "gross_profit")
        operating_income = _num(income, "operating_income")
        net_income = _num(income, "net_income")
        interest_expense = _abs_or_none(_num(income, "interest_expense"))
        diluted_shares = _num(income, "weighted_average_diluted_shares")

        operating_cash_flow = _num(cash, "operating_cash_flow")
        capex_raw = _num(cash, "capital_expenditures")
        free_cash_flow = _free_cash_flow(operating_cash_flow, capex_raw)
        dividends = _abs_or_none(_num(cash, "dividends_paid"))
        buybacks = _abs_or_none(_num(cash, "share_repurchases"))
        sbc = _num(cash, "stock_based_compensation")

        cash_value = _num(balance, "cash_and_equivalents")
        total_debt = _num(balance, "total_debt")
        total_equity = _num(balance, "total_equity")
        total_assets = _num(balance, "total_assets")
        net_debt = _subtract_optional(total_debt, cash_value)

        prior_revenue = _num(prior_income, "revenue")
        prior_gross_profit = _num(prior_income, "gross_profit")
        prior_operating_income = _num(prior_income, "operating_income")
        prior_net_income = _num(prior_income, "net_income")
        prior_shares = _num(prior_income, "weighted_average_diluted_shares")
        prior_equity = _num(prior_balance, "total_equity")
        prior_assets = _num(prior_balance, "total_assets")

        market_cap = _num(market, "market_cap")
        price = _num(market, "price")
        shares_outstanding = _num(market, "shares_outstanding") or diluted_shares
        if market_cap is None and price is not None and shares_outstanding is not None:
            market_cap = price * shares_outstanding
        enterprise_value = _add_optional(market_cap, net_debt)

        row = {
            "period": period,
            "revenue": revenue,
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": net_income,
            "weighted_average_diluted_shares": diluted_shares,
            "revenue_growth_yoy": _growth(revenue, prior_revenue),
            "gross_profit_growth": _growth(gross_profit, prior_gross_profit),
            "operating_income_growth": _growth(operating_income, prior_operating_income),
            "net_income_growth": _growth(net_income, prior_net_income),
            "gross_margin": safe_divide(gross_profit, revenue),
            "operating_margin": safe_divide(operating_income, revenue),
            "net_margin": safe_divide(net_income, revenue),
            "operating_cash_flow": operating_cash_flow,
            "capital_expenditures": capex_raw,
            "free_cash_flow": free_cash_flow,
            "fcf_margin": safe_divide(free_cash_flow, revenue),
            "fcf_conversion_from_net_income": safe_divide(free_cash_flow, net_income),
            "cash_and_equivalents": cash_value,
            "total_debt": total_debt,
            "net_debt": net_debt,
            "debt_to_equity": safe_divide(total_debt, total_equity),
            "interest_coverage": safe_divide(operating_income, interest_expense),
            "share_count_change": _growth(diluted_shares, prior_shares),
            "buybacks": buybacks,
            "dividends": dividends,
            "stock_based_compensation": sbc,
            "sbc_percent_revenue": safe_divide(sbc, revenue),
            "sbc_percent_operating_cash_flow": safe_divide(sbc, operating_cash_flow),
            "return_on_equity": safe_divide(net_income, _average(total_equity, prior_equity)),
            "return_on_assets": safe_divide(net_income, _average(total_assets, prior_assets)),
            "market_cap": market_cap,
            "enterprise_value": enterprise_value,
            "price_to_free_cash_flow": safe_divide(market_cap, free_cash_flow),
            "price_to_earnings": safe_divide(market_cap, net_income),
            "ev_to_ebit": safe_divide(enterprise_value, operating_income),
            "ev_to_revenue": safe_divide(enterprise_value, revenue),
        }
        row["roic"] = _roic(row, income)
        rows.append(row)

    return {"ticker": ticker.upper(), "periods": rows}


def calculate_from_financial_rows(
    ticker: str, rows: list[dict[str, Any]], prices: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    income = []
    balance = []
    cash = []
    for row in rows:
        income.append(
            {
                "period": row.get("period"),
                "revenue": row.get("revenue"),
                "grossProfit": row.get("grossProfit"),
                "operatingIncome": row.get("operatingIncome"),
                "netIncome": row.get("netIncome"),
                "interestExpense": row.get("interestExpense"),
                "dilutedShares": row.get("dilutedShares"),
                "pretaxIncome": row.get("pretaxIncome"),
                "incomeTaxExpense": row.get("incomeTaxExpense"),
            }
        )
        balance.append(
            {
                "period": row.get("period"),
                "cash": row.get("cash"),
                "totalDebt": row.get("totalDebt"),
                "equity": row.get("equity"),
                "assets": row.get("assets"),
            }
        )
        cash.append(
            {
                "period": row.get("period"),
                "operatingCashFlow": row.get("operatingCashFlow"),
                "capitalExpenditures": row.get("capitalExpenditures"),
                "dividends": row.get("dividends"),
                "buybacks": row.get("buybacks"),
                "stockBasedCompensation": row.get("stockBasedCompensation"),
            }
        )
    market_data = _market_data_from_prices(rows, prices or [])
    return calculate_metrics(ticker, income, balance, cash, market_data)


def write_metrics(company_dir: Path, ticker: str) -> dict[str, Any]:
    financial_rows = read_json(company_dir / "data" / "financials.json", []) or []
    prices = read_json(company_dir / "data" / "prices.json", []) or []
    metrics = calculate_from_financial_rows(ticker, financial_rows, prices)
    write_json(company_dir / "metrics" / "metrics.json", metrics)
    write_text(company_dir / "metrics" / "metrics.md", render_metrics_markdown(metrics))
    return metrics


def render_metrics_markdown(metrics: dict[str, Any]) -> str:
    ticker = metrics.get("ticker", "")
    periods = metrics.get("periods", [])
    latest = periods[-1] if periods else {}
    lines = [
        f"# {ticker} Metrics Review",
        "",
        "## Growth",
        "",
        f"- Revenue: {money(latest.get('revenue'))}",
        f"- Revenue growth YoY: {percent(latest.get('revenue_growth_yoy'))}",
        f"- Operating income growth YoY: {percent(latest.get('operating_income_growth'))}",
        f"- Net income growth YoY: {percent(latest.get('net_income_growth'))}",
        "",
        "## Margins",
        "",
        f"- Gross margin: {percent(latest.get('gross_margin'))}",
        f"- Operating margin: {percent(latest.get('operating_margin'))}",
        f"- Net margin: {percent(latest.get('net_margin'))}",
        f"- FCF margin: {percent(latest.get('fcf_margin'))}",
        "",
        "## Cash Generation",
        "",
        f"- Operating cash flow: {money(latest.get('operating_cash_flow'))}",
        f"- Free cash flow: {money(latest.get('free_cash_flow'))}",
        f"- FCF conversion from net income: {percent(latest.get('fcf_conversion_from_net_income'))}",
        "",
        "## Balance Sheet",
        "",
        f"- Cash and equivalents: {money(latest.get('cash_and_equivalents'))}",
        f"- Total debt: {money(latest.get('total_debt'))}",
        f"- Net debt: {money(latest.get('net_debt'))}",
        f"- Debt/equity: {_ratio(latest.get('debt_to_equity'))}",
        f"- Interest coverage: {_ratio(latest.get('interest_coverage'))}",
        "",
        "## Capital Allocation",
        "",
        f"- Share count change: {percent(latest.get('share_count_change'))}",
        f"- Buybacks: {money(latest.get('buybacks'))}",
        f"- Dividends: {money(latest.get('dividends'))}",
        f"- Stock-based compensation: {money(latest.get('stock_based_compensation'))}",
        "",
        "## Returns",
        "",
        f"- ROE: {percent(latest.get('return_on_equity'))}",
        f"- ROA: {percent(latest.get('return_on_assets'))}",
        f"- ROIC: {percent(latest.get('roic'))}",
        "",
        "## Valuation Support",
        "",
        f"- Market cap: {money(latest.get('market_cap'))}",
        f"- Enterprise value: {money(latest.get('enterprise_value'))}",
        f"- Price/free cash flow: {_ratio(latest.get('price_to_free_cash_flow'))}",
        f"- Price/earnings: {_ratio(latest.get('price_to_earnings'))}",
        f"- EV/EBIT: {_ratio(latest.get('ev_to_ebit'))}",
        f"- EV/revenue: {_ratio(latest.get('ev_to_revenue'))}",
        "",
        "## Red Flags",
        "",
        *_red_flags(latest),
        "",
        "## Questions",
        "",
        "- Are reported margins supported by cash conversion?",
        "- What explains changes in share count, SBC, buybacks, and dividends?",
        "- Are valuation multiples consistent with durable growth and reinvestment needs?",
        "",
    ]
    return "\n".join(lines)


def _by_period(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = row.get("period")
        if period:
            result[str(period)] = row
    return result


def _get(row: dict[str, Any], canonical: str) -> Any:
    for key in ALIASES[canonical]:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _num(row: dict[str, Any], canonical: str) -> float | None:
    value = _get(row, canonical)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _abs_or_none(value: float | None) -> float | None:
    return abs(value) if value is not None else None


def _free_cash_flow(operating_cash_flow: float | None, capex: float | None) -> float | None:
    if operating_cash_flow is None or capex is None:
        return None
    return operating_cash_flow + capex if capex < 0 else operating_cash_flow - capex


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous) - 1


def _average(current: float | None, previous: float | None) -> float | None:
    if current is None:
        return None
    if previous is None:
        return current
    return (current + previous) / 2


def _subtract_optional(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return None
    return (left or 0.0) - (right or 0.0)


def _add_optional(left: float | None, right: float | None) -> float | None:
    if left is None:
        return None
    return left + (right or 0.0)


def _roic(row: dict[str, Any], income: dict[str, Any]) -> float | None:
    operating_income = row.get("operating_income")
    if operating_income is None:
        return None
    tax_expense = _num(income, "income_tax_expense")
    pretax_income = _num(income, "pretax_income")
    tax_rate = safe_divide(tax_expense, pretax_income)
    if tax_rate is None or tax_rate < 0 or tax_rate > 0.5:
        tax_rate = 0.21
    nopat = operating_income * (1 - tax_rate)
    invested_capital = None
    equity = row.get("total_equity")
    debt = row.get("total_debt")
    cash = row.get("cash_and_equivalents")
    if equity is not None or debt is not None or cash is not None:
        invested_capital = (equity or 0.0) + (debt or 0.0) - (cash or 0.0)
    return safe_divide(nopat, invested_capital)


def _market_data_from_prices(rows: list[dict[str, Any]], prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows or not prices:
        return []
    latest_price = None
    for price in sorted(prices, key=lambda item: str(item.get("date", ""))):
        close = price.get("adjustedClose", price.get("close"))
        if close not in (None, ""):
            latest_price = float(close)
    if latest_price is None:
        return []
    latest_period = sorted(str(row.get("period", "")) for row in rows if row.get("period"))[-1]
    latest_row = next((row for row in rows if row.get("period") == latest_period), {})
    shares = latest_row.get("dilutedShares")
    return [{"period": latest_period, "price": latest_price, "shares_outstanding": shares}]


def _ratio(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}x"


def _red_flags(latest: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if latest.get("free_cash_flow") is not None and latest.get("net_income") is not None:
        if latest["free_cash_flow"] < latest["net_income"] * 0.6:
            flags.append("- Free cash flow is materially below net income.")
    if latest.get("debt_to_equity") is not None and latest["debt_to_equity"] > 2:
        flags.append("- Debt/equity is elevated.")
    if latest.get("sbc_percent_revenue") is not None and latest["sbc_percent_revenue"] > 0.1:
        flags.append("- Stock-based compensation is high relative to revenue.")
    if not flags:
        flags.append("- No deterministic red flags detected from currently normalized data.")
    return flags

