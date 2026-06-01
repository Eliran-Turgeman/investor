import importlib
import math
import unittest


class MetricsEngineTests(unittest.TestCase):
    def test_calculates_core_metrics_from_normalized_records(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        income_statements = [
            {
                "period": "2023-FY",
                "revenue": 1000.0,
                "gross_profit": 600.0,
                "operating_income": 200.0,
                "net_income": 120.0,
                "interest_expense": 25.0,
                "weighted_average_diluted_shares": 100.0,
            },
            {
                "period": "2024-FY",
                "revenue": 1250.0,
                "gross_profit": 750.0,
                "operating_income": 275.0,
                "net_income": 175.0,
                "interest_expense": 25.0,
                "weighted_average_diluted_shares": 95.0,
            },
        ]
        balance_sheets = [
            {
                "period": "2023-FY",
                "cash_and_equivalents": 100.0,
                "total_debt": 300.0,
                "total_equity": 500.0,
                "total_assets": 1000.0,
            },
            {
                "period": "2024-FY",
                "cash_and_equivalents": 150.0,
                "total_debt": 250.0,
                "total_equity": 650.0,
                "total_assets": 1200.0,
            },
        ]
        cash_flows = [
            {
                "period": "2023-FY",
                "operating_cash_flow": 180.0,
                "capital_expenditures": -50.0,
                "dividends_paid": -20.0,
                "share_repurchases": -30.0,
                "stock_based_compensation": 40.0,
            },
            {
                "period": "2024-FY",
                "operating_cash_flow": 240.0,
                "capital_expenditures": -60.0,
                "dividends_paid": -25.0,
                "share_repurchases": -50.0,
                "stock_based_compensation": 45.0,
            },
        ]
        market_data = [
            {
                "period": "2024-FY",
                "price": 20.0,
                "shares_outstanding": 95.0,
            }
        ]

        result = metrics_engine.calculate_metrics(
            ticker="ACME",
            income_statements=income_statements,
            balance_sheets=balance_sheets,
            cash_flows=cash_flows,
            market_data=market_data,
        )

        self.assertEqual(result["ticker"], "ACME")
        periods = {period["period"]: period for period in result["periods"]}
        current = periods["2024-FY"]

        self.assertAlmostEqual(current["revenue_growth_yoy"], 0.25)
        self.assertAlmostEqual(current["gross_margin"], 0.60)
        self.assertAlmostEqual(current["operating_margin"], 0.22)
        self.assertAlmostEqual(current["net_margin"], 0.14)

        self.assertAlmostEqual(current["free_cash_flow"], 180.0)
        self.assertAlmostEqual(current["fcf_margin"], 0.144)
        self.assertAlmostEqual(current["fcf_conversion_from_net_income"], 180.0 / 175.0)

        self.assertAlmostEqual(current["net_debt"], 100.0)
        self.assertAlmostEqual(current["debt_to_equity"], 250.0 / 650.0)
        self.assertAlmostEqual(current["interest_coverage"], 11.0)

        self.assertAlmostEqual(current["share_count_change"], -0.05)
        self.assertAlmostEqual(current["buybacks"], 50.0)
        self.assertAlmostEqual(current["dividends"], 25.0)
        self.assertAlmostEqual(current["sbc_percent_revenue"], 45.0 / 1250.0)
        self.assertAlmostEqual(current["sbc_percent_operating_cash_flow"], 45.0 / 240.0)

        self.assertAlmostEqual(current["return_on_equity"], 175.0 / 575.0)
        self.assertAlmostEqual(current["return_on_assets"], 175.0 / 1100.0)

        self.assertAlmostEqual(current["market_cap"], 1900.0)
        self.assertAlmostEqual(current["enterprise_value"], 2000.0)
        self.assertAlmostEqual(current["price_to_free_cash_flow"], 1900.0 / 180.0)
        self.assertAlmostEqual(current["price_to_earnings"], 1900.0 / 175.0)
        self.assertAlmostEqual(current["ev_to_revenue"], 2000.0 / 1250.0)
        self.assertAlmostEqual(current["ev_to_ebit"], 2000.0 / 275.0)

    def test_missing_denominator_metrics_are_none_instead_of_zero_division(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_metrics(
            ticker="ZERO",
            income_statements=[
                {
                    "period": "2024-FY",
                    "revenue": 0.0,
                    "gross_profit": 10.0,
                    "operating_income": 0.0,
                    "net_income": 0.0,
                    "interest_expense": 0.0,
                }
            ],
            balance_sheets=[
                {
                    "period": "2024-FY",
                    "cash_and_equivalents": 0.0,
                    "total_debt": 0.0,
                    "total_equity": 0.0,
                    "total_assets": 0.0,
                }
            ],
            cash_flows=[
                {
                    "period": "2024-FY",
                    "operating_cash_flow": 0.0,
                    "capital_expenditures": 0.0,
                    "stock_based_compensation": 0.0,
                }
            ],
            market_data=[],
        )

        current = result["periods"][0]
        self.assertIsNone(current["gross_margin"])
        self.assertIsNone(current["fcf_margin"])
        self.assertIsNone(current["fcf_conversion_from_net_income"])
        self.assertIsNone(current["debt_to_equity"])
        self.assertIsNone(current["interest_coverage"])
        self.assertIsNone(current["return_on_equity"])
        self.assertIsNone(current["return_on_assets"])

    def test_non_finite_metric_inputs_are_treated_as_missing(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_metrics(
            ticker="BAD",
            income_statements=[
                {
                    "period": "2024-FY",
                    "revenue": "NaN",
                    "gross_profit": 10.0,
                    "net_income": "Infinity",
                }
            ],
            cash_flows=[
                {
                    "period": "2024-FY",
                    "operating_cash_flow": 20.0,
                    "capital_expenditures": "-Infinity",
                }
            ],
        )

        current = result["periods"][0]
        self.assertIsNone(current["revenue"])
        self.assertIsNone(current["net_income"])
        self.assertIsNone(current["free_cash_flow"])
        self.assertIsNone(current["gross_margin"])
        for key, value in current.items():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), key)

    def test_bad_price_rows_do_not_prevent_metrics_calculation(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "ACME",
            [
                {
                    "period": "2024-FY",
                    "revenue": 100.0,
                    "netIncome": 10.0,
                    "dilutedShares": 5.0,
                }
            ],
            prices=[
                {"date": "2024-12-30", "close": "not numeric", "source": "YAHOO"},
                {"date": "2024-12-31", "adjustedClose": 12.0, "source": "YAHOO"},
            ],
        )

        current = result["periods"][0]
        self.assertAlmostEqual(current["market_cap"], 60.0)
        self.assertAlmostEqual(current["price_to_earnings"], 6.0)

    def test_market_cap_uses_raw_close_when_adjusted_close_differs(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "DIV",
            [
                {
                    "period": "2024-FY",
                    "netIncome": 10.0,
                    "dilutedShares": 5.0,
                }
            ],
            prices=[
                {
                    "date": "2024-12-31",
                    "close": 100.0,
                    "adjustedClose": 80.0,
                    "source": "YAHOO",
                }
            ],
        )

        current = result["periods"][0]
        self.assertAlmostEqual(current["market_cap"], 500.0)
        self.assertAlmostEqual(current["price_to_earnings"], 50.0)

    def test_market_cap_ignores_price_rows_without_valid_iso_dates(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "DATE",
            [
                {
                    "period": "2024-FY",
                    "netIncome": 10.0,
                    "dilutedShares": 5.0,
                }
            ],
            prices=[
                {"date": "2024-12-31", "close": 100.0, "source": "YAHOO"},
                {"date": "not-a-date", "close": 999.0, "source": "YAHOO"},
            ],
        )

        current = result["periods"][0]
        self.assertAlmostEqual(current["market_cap"], 500.0)
        self.assertAlmostEqual(current["price_to_earnings"], 50.0)

    def test_market_cap_ignores_non_positive_provider_prices(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "BADPX",
            [
                {
                    "period": "2024-FY",
                    "netIncome": 10.0,
                    "dilutedShares": 5.0,
                }
            ],
            prices=[
                {"date": "2024-12-29", "close": 100.0, "source": "YAHOO"},
                {"date": "2024-12-30", "close": 0.0, "source": "YAHOO"},
                {"date": "2024-12-31", "close": -5.0, "source": "YAHOO"},
            ],
        )

        current = result["periods"][0]
        self.assertAlmostEqual(current["market_cap"], 500.0)
        self.assertAlmostEqual(current["price_to_earnings"], 50.0)

    def test_market_cap_ignores_prices_for_other_tickers(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "ACME",
            [
                {
                    "period": "2024-FY",
                    "netIncome": 10.0,
                    "dilutedShares": 5.0,
                }
            ],
            prices=[
                {"ticker": "ACME", "date": "2024-12-30", "close": 100.0, "source": "YAHOO"},
                {"ticker": "MSFT", "date": "2024-12-31", "close": 999.0, "source": "YAHOO"},
            ],
        )

        current = result["periods"][0]
        self.assertAlmostEqual(current["market_cap"], 500.0)
        self.assertAlmostEqual(current["price_to_earnings"], 50.0)

    def test_market_cap_requires_positive_price_and_share_count(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_metrics(
            ticker="BADSH",
            income_statements=[
                {
                    "period": "2024-FY",
                    "net_income": 10.0,
                    "weighted_average_diluted_shares": -5.0,
                }
            ],
            market_data=[{"period": "2024-FY", "price": 20.0}],
        )

        current = result["periods"][0]
        self.assertIsNone(current["weighted_average_diluted_shares"])
        self.assertIsNone(current["market_cap"])
        self.assertIsNone(current["price_to_earnings"])

    def test_market_data_ignores_non_positive_direct_values(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_metrics(
            ticker="BADPX",
            income_statements=[
                {
                    "period": "2024-FY",
                    "net_income": 10.0,
                    "weighted_average_diluted_shares": 5.0,
                }
            ],
            market_data=[
                {
                    "period": "2024-FY",
                    "price": -20.0,
                    "shares_outstanding": 5.0,
                    "market_cap": -100.0,
                }
            ],
        )

        current = result["periods"][0]
        self.assertIsNone(current["market_cap"])
        self.assertIsNone(current["enterprise_value"])
        self.assertIsNone(current["price_to_earnings"])

    def test_metrics_period_order_places_full_year_after_interims(self):
        metrics_engine = importlib.import_module("investor_toolkit.metrics.engine")

        result = metrics_engine.calculate_from_financial_rows(
            "ACME",
            [
                {
                    "period": "2024-FY",
                    "netIncome": 10.0,
                    "dilutedShares": 10.0,
                },
                {
                    "period": "2024-Q3",
                    "netIncome": 7.0,
                    "dilutedShares": 1.0,
                },
            ],
            prices=[{"ticker": "ACME", "date": "2024-12-31", "close": 100.0, "source": "YAHOO"}],
        )

        self.assertEqual([row["period"] for row in result["periods"]], ["2024-Q3", "2024-FY"])
        latest = result["periods"][-1]
        self.assertAlmostEqual(latest["market_cap"], 1000.0)
        self.assertAlmostEqual(latest["price_to_earnings"], 100.0)


if __name__ == "__main__":
    unittest.main()
