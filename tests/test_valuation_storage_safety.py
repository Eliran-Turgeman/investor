import importlib
import json
import tempfile
import unittest
from pathlib import Path


class ValuationStorageSafetyTests(unittest.TestCase):
    def test_latest_value_treats_numeric_zero_as_present(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="ZERO",
            company_name="Zero Corp",
            company_dir=Path("."),
            financial_rows=[
                {
                    "period": "2025-FY",
                    "fiscalYear": 2025,
                    "revenue": 100.0,
                    "operatingIncome": 20.0,
                    "netIncome": 10.0,
                    "operatingCashFlow": 15.0,
                    "capitalExpenditures": 5.0,
                    "cash": 25.0,
                    "totalDebt": 30.0,
                    "dilutedShares": 40.0,
                }
            ],
            prices=[],
            metric_periods=[
                {
                    "period": "2025-FY",
                    "revenue": 200.0,
                    "operating_income": 30.0,
                    "net_income": 15.0,
                    "free_cash_flow": 12.0,
                    "cash_and_equivalents": 35.0,
                    "total_debt": 45.0,
                    "shares_outstanding": 50.0,
                }
            ],
            latest_ttm={
                "revenue": 0,
                "operatingIncome": 0,
                "netIncome": 0,
                "freeCashFlow": 0,
                "cash": 0,
                "totalDebt": 0,
                "dilutedShares": 0,
            },
        )

        for field_name in (
            "revenue",
            "operating_income",
            "net_income",
            "free_cash_flow",
            "cash_and_equivalents",
            "total_debt",
            "shares_outstanding",
        ):
            with self.subTest(field_name=field_name):
                self.assertEqual(data.latest_value(field_name), 0.0)

    def test_latest_period_prefers_full_fiscal_year_over_interim_same_year(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="ACME",
            company_name="Acme Corp",
            company_dir=Path("."),
            financial_rows=[
                {"period": "2024-FY", "fiscalYear": 2024, "revenue": 100.0},
                {"period": "2024-Q3", "fiscalYear": 2024, "revenue": 75.0},
            ],
            prices=[],
            metric_periods=[],
        )

        self.assertEqual(data.latest_financial["period"], "2024-FY")
        self.assertEqual(data.latest_value("revenue"), 100.0)

    def test_latest_price_prefers_raw_close_over_adjusted_close(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="DIV",
            company_name="Dividend Corp",
            company_dir=Path("."),
            financial_rows=[],
            prices=[
                {
                    "ticker": "DIV",
                    "date": "2026-05-29",
                    "close": 100.0,
                    "adjustedClose": 80.0,
                }
            ],
            metric_periods=[],
        )

        self.assertEqual(data.latest_price, 100.0)
        self.assertEqual(data.latest_value("current_share_price"), 100.0)

    def test_latest_price_ignores_rows_without_valid_iso_dates(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="DATE",
            company_name="Date Corp",
            company_dir=Path("."),
            financial_rows=[],
            prices=[
                {"ticker": "DATE", "date": "2026-05-29", "close": 100.0},
                {"ticker": "DATE", "date": "not-a-date", "close": 999.0},
            ],
            metric_periods=[],
        )

        self.assertEqual(data.latest_price, 100.0)
        self.assertEqual(data.latest_price_date, "2026-05-29")

    def test_latest_price_ignores_non_positive_provider_prices(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="BADPX",
            company_name="Bad Price Corp",
            company_dir=Path("."),
            financial_rows=[],
            prices=[
                {"ticker": "BADPX", "date": "2026-05-28", "close": 100.0},
                {"ticker": "BADPX", "date": "2026-05-29", "close": 0.0},
                {"ticker": "BADPX", "date": "2026-05-30", "close": -5.0},
            ],
            metric_periods=[],
        )

        self.assertEqual(data.latest_price, 100.0)
        self.assertEqual(data.latest_price_date, "2026-05-28")

    def test_latest_price_ignores_rows_for_other_tickers(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")
        data = valuation.LocalFinancialData(
            ticker="ACME",
            company_name="Acme Corp",
            company_dir=Path("."),
            financial_rows=[],
            prices=[
                {"ticker": "ACME", "date": "2026-05-28", "close": 100.0},
                {"ticker": "MSFT", "date": "2026-05-29", "close": 999.0},
            ],
            metric_periods=[],
        )

        self.assertEqual(data.latest_price, 100.0)
        self.assertEqual(data.latest_price_date, "2026-05-28")

    def test_export_agent_context_sanitizes_ticker_and_scenario_filename_parts(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp) / "context" / "valuations"
            result = {
                "ticker": "../EVIL",
                "scenario": r"..\base/../../pwn",
                "model": "fcff-dcf",
                "valuationDate": "2026-05-31",
                "currency": "USD",
                "valuation": {
                    "currentSharePrice": 10.0,
                    "fairValuePerShare": 12.0,
                    "marginOfSafety": 0.2,
                },
                "drivers": {},
                "warnings": [],
            }

            paths = valuation.export_agent_context(result, {"ticker": "../EVIL"}, cwd=tmp)

            for path_text in paths.values():
                with self.subTest(path=path_text):
                    path = Path(path_text).resolve()
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.parent, base_dir.resolve())

    def test_company_file_helpers_reject_paths_that_escape_ticker_directory(self):
        storage_module = importlib.import_module("investor_toolkit.storage")

        with tempfile.TemporaryDirectory() as tmp:
            storage = storage_module.ResearchStorage(tmp)
            storage.ensure_company_dirs("ACME")
            safe_path = storage.write_company_file("ACME", "notes/safe.txt", "ok")
            self.assertTrue(safe_path.is_file())

            bad_path = Path("data") / ".." / ".." / "outside.txt"
            helpers = (
                lambda: storage.write_company_file("ACME", bad_path, "bad"),
                lambda: storage.append_company_file("ACME", bad_path, "bad"),
                lambda: storage.write_company_json("ACME", bad_path, {"bad": True}),
                lambda: storage.read_company_json("ACME", bad_path, default={}),
            )
            for helper in helpers:
                with self.subTest(helper=helper):
                    with self.assertRaises(ValueError):
                        helper()

            self.assertFalse((Path(tmp) / "research" / "outside.txt").exists())

    def test_company_json_helpers_allow_safe_nested_relative_paths(self):
        storage_module = importlib.import_module("investor_toolkit.storage")

        with tempfile.TemporaryDirectory() as tmp:
            storage = storage_module.ResearchStorage(tmp)
            storage.ensure_company_dirs("ACME")

            path = storage.write_company_json("ACME", Path("data") / "safe.json", {"ok": True})
            data = storage.read_company_json("ACME", Path("data") / "safe.json")

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(data, {"ok": True})

    def test_write_json_rejects_non_finite_numbers(self):
        utils = importlib.import_module("investor_toolkit.utils")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"

            with self.assertRaises(ValueError):
                utils.write_json(path, {"bad": float("nan")})

            self.assertFalse(path.exists())

    def test_relative_research_root_resolves_against_workspace_root(self):
        storage_module = importlib.import_module("investor_toolkit.storage")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            storage = storage_module.ResearchStorage(workspace, research_root="custom-research")

            self.assertEqual(storage.research_root, (workspace / "custom-research").resolve())

    def test_ticker_directories_are_normalized_before_path_joining(self):
        storage_module = importlib.import_module("investor_toolkit.storage")

        with tempfile.TemporaryDirectory() as tmp:
            storage = storage_module.ResearchStorage(tmp)
            company_dir = storage.ensure_company_dirs(r"..\evil")

            self.assertEqual(company_dir.name, "..EVIL")
            company_dir.resolve().relative_to(storage.research_root.resolve())
            self.assertFalse((Path(tmp) / "evil").exists())

    def test_dot_only_tickers_are_rejected_before_storage_paths(self):
        storage_module = importlib.import_module("investor_toolkit.storage")

        with tempfile.TemporaryDirectory() as tmp:
            storage = storage_module.ResearchStorage(tmp)

            for ticker in (".", "..", "...", "-"):
                with self.subTest(ticker=ticker):
                    with self.assertRaises(ValueError):
                        storage.ensure_company_dirs(ticker)


if __name__ == "__main__":
    unittest.main()
