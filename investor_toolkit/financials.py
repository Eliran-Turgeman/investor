from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path
from typing import Any

from .utils import write_json


CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "grossProfit": ("GrossProfit",),
    "operatingIncome": ("OperatingIncomeLoss",),
    "netIncome": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "shortTermDebt": (
        "ShortTermBorrowings",
        "ShortTermDebt",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
    ),
    "longTermDebt": (
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtNoncurrent",
    ),
    "totalDebt": ("LongTermDebt",),
    "operatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capitalExpenditures": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "dividends": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"),
    "stockBasedCompensation": ("ShareBasedCompensation", "ShareBasedCompensationExpense"),
    "dilutedShares": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingDiluted",
    ),
    "interestExpense": ("InterestExpenseNonOperating", "InterestExpense"),
    "incomeTaxExpense": ("IncomeTaxExpenseBenefit",),
    "pretaxIncome": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
}

BALANCE_SHEET_FIELDS = {"assets", "equity", "cash", "shortTermDebt", "longTermDebt", "totalDebt"}
SHARE_FIELDS = {"dilutedShares"}


class FinancialNormalizer:
    def normalize_company_facts(
        self, facts: dict[str, Any], ticker: str | None = None
    ) -> list[dict[str, Any]]:
        ticker = (ticker or facts.get("entityName") or "").upper()
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        periods: dict[int, dict[str, Any]] = {}
        sources: dict[int, dict[str, Any]] = {}
        for field, concepts in CONCEPTS.items():
            picked_years: set[int] = set()
            for concept in concepts:
                concept_data = us_gaap.get(concept)
                if not concept_data:
                    continue
                unit_keys = self._unit_preference(field, concept_data.get("units", {}))
                for unit in unit_keys:
                    facts_for_unit = concept_data.get("units", {}).get(unit, [])
                    annual = self._annual_facts(facts_for_unit, point_in_time=field in BALANCE_SHEET_FIELDS)
                    for fiscal_year, fact in annual.items():
                        if fiscal_year in picked_years:
                            continue
                        periods.setdefault(fiscal_year, {"ticker": ticker, "fiscalYear": fiscal_year, "period": f"{fiscal_year}-FY"})
                        periods[fiscal_year][field] = fact.get("val")
                        periods[fiscal_year][f"{field}Unit"] = unit
                        sources.setdefault(fiscal_year, {})[field] = {
                            "concept": concept,
                            "unit": unit,
                            "accessionNumber": fact.get("accn", ""),
                            "filed": fact.get("filed", ""),
                            "end": fact.get("end", ""),
                            "form": fact.get("form", ""),
                        }
                        picked_years.add(fiscal_year)
        rows = []
        for fiscal_year in sorted(periods):
            row = periods[fiscal_year]
            total_debt = row.pop("totalDebt", None)
            short_debt = row.pop("shortTermDebt", None)
            long_debt = row.pop("longTermDebt", None)
            row["totalDebt"] = total_debt if total_debt is not None else self._sum_optional(short_debt, long_debt)
            if short_debt is not None:
                row["shortTermDebt"] = short_debt
            if long_debt is not None:
                row["longTermDebt"] = long_debt
            row["sources"] = sources.get(fiscal_year, {})
            rows.append(row)
        return rows

    def write_normalized(self, company_dir: Path, rows: list[dict[str, Any]]) -> None:
        data_dir = company_dir / "data"
        write_json(data_dir / "financials.json", rows)
        self._write_csv(data_dir / "financials.csv", rows)
        self._write_statement_csv(
            data_dir / "income_statement.csv",
            rows,
            ["period", "revenue", "grossProfit", "operatingIncome", "pretaxIncome", "incomeTaxExpense", "netIncome", "dilutedShares"],
        )
        self._write_statement_csv(
            data_dir / "balance_sheet.csv",
            rows,
            ["period", "cash", "assets", "totalDebt", "shortTermDebt", "longTermDebt", "equity"],
        )
        self._write_statement_csv(
            data_dir / "cash_flow.csv",
            rows,
            ["period", "operatingCashFlow", "capitalExpenditures", "dividends", "buybacks", "stockBasedCompensation"],
        )

    @staticmethod
    def _unit_preference(field: str, units: dict[str, Any]) -> list[str]:
        if field in SHARE_FIELDS:
            preferred = ["shares"]
        else:
            preferred = ["USD", "usd"]
        return [unit for unit in preferred if unit in units]

    def _annual_facts(self, facts: list[dict[str, Any]], point_in_time: bool = False) -> dict[int, dict[str, Any]]:
        candidates: dict[int, list[dict[str, Any]]] = {}
        for fact in facts:
            form = str(fact.get("form", ""))
            if form not in {"10-K", "10-K/A"}:
                continue
            fiscal_year = fact.get("fy")
            if not isinstance(fiscal_year, int):
                end = str(fact.get("end", ""))
                fiscal_year = int(end[:4]) if end[:4].isdigit() else None
            if not fiscal_year:
                continue
            if point_in_time:
                if fact.get("start") or not self._has_valid_iso_date(fact.get("end")):
                    continue
            elif not self._looks_annual_duration(fact):
                continue
            value = self._finite_number(fact.get("val"))
            if value is None:
                continue
            candidate = dict(fact)
            candidate["val"] = value
            candidates.setdefault(fiscal_year, []).append(candidate)
        picked: dict[int, dict[str, Any]] = {}
        for fiscal_year, items in candidates.items():
            picked[fiscal_year] = sorted(
                items,
                key=lambda item: (
                    str(item.get("filed", "")),
                    str(item.get("end", "")),
                    str(item.get("accn", "")),
                ),
            )[-1]
        return picked

    @staticmethod
    def _looks_annual_duration(fact: dict[str, Any]) -> bool:
        start = fact.get("start")
        end = fact.get("end")
        if not start or not end:
            return False
        try:
            days = (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days
        except ValueError:
            return False
        return 300 <= days <= 380

    @staticmethod
    def _sum_optional(*values: Any) -> float | int | None:
        present = [
            parsed
            for value in values
            if (parsed := FinancialNormalizer._finite_number(value)) is not None
        ]
        return sum(present) if present else None

    @staticmethod
    def _finite_number(value: Any) -> float | int | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _has_valid_iso_date(value: Any) -> bool:
        text = str(value or "")
        if not text:
            return False
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return False
        return True

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        fields: list[str] = []
        for row in rows:
            for key in row:
                if key != "sources" and key not in fields:
                    fields.append(key)
        FinancialNormalizer._write_statement_csv(path, rows, fields)

    @staticmethod
    def _write_statement_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})
