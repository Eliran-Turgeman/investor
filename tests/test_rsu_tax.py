import importlib
import io
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from unittest.mock import patch


class RsuTaxTests(unittest.TestCase):
    def test_compliant_sale_splits_ordinary_and_capital_gain(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        result = rsu_tax.calculate_rsu_tax(
            rsu_tax.RsuTaxInputs(
                shares=100,
                grant_price_usd=10,
                sale_price_usd=30,
                fx_usd_ils=4,
                ordinary_tax_rate=0.47,
            )
        )

        self.assertAlmostEqual(result.compliant.gross_sale_ils, 12_000)
        self.assertAlmostEqual(result.compliant.grant_value_ils, 4_000)
        self.assertAlmostEqual(result.compliant.ordinary_component_ils, 4_000)
        self.assertAlmostEqual(result.compliant.capital_gain_ils, 8_000)
        self.assertAlmostEqual(result.compliant.ordinary_tax_ils, 1_880)
        self.assertAlmostEqual(result.compliant.capital_gains_tax_ils, 2_000)
        self.assertAlmostEqual(result.compliant.net_proceeds_ils, 8_120)

    def test_sale_below_grant_value_has_no_capital_gain(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        result = rsu_tax.calculate_rsu_tax(
            rsu_tax.RsuTaxInputs(
                shares=100,
                grant_price_usd=30,
                sale_price_usd=10,
                fx_usd_ils=4,
                ordinary_tax_rate=47,
            )
        )

        self.assertAlmostEqual(result.compliant.ordinary_component_ils, 4_000)
        self.assertAlmostEqual(result.compliant.capital_gain_ils, 0)
        self.assertAlmostEqual(result.compliant.ordinary_tax_ils, 1_880)
        self.assertAlmostEqual(result.compliant.capital_gains_tax_ils, 0)

    def test_early_sale_treats_net_sale_as_ordinary_income(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        result = rsu_tax.calculate_rsu_tax(
            rsu_tax.RsuTaxInputs(
                shares=100,
                grant_price_usd=10,
                sale_price_usd=30,
                fx_usd_ils=4,
                ordinary_tax_rate=0.47,
                sale_fees_ils=100,
            )
        )

        self.assertAlmostEqual(result.early_sale.net_sale_before_tax_ils, 11_900)
        self.assertAlmostEqual(result.early_sale.ordinary_component_ils, 11_900)
        self.assertAlmostEqual(result.early_sale.capital_gain_ils, 0)
        self.assertAlmostEqual(result.early_sale.ordinary_tax_ils, 5_593)

    def test_ni_health_is_estimated_when_salary_ytd_supplied(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        self.assertAlmostEqual(rsu_tax.estimate_ni_health(0, 10_000), 427)
        self.assertAlmostEqual(rsu_tax.estimate_ni_health(100_000, 10_000), 1_217)
        self.assertAlmostEqual(rsu_tax.estimate_ni_health(700_000, 10_000), 0)

    def test_average_grant_price_uses_30_calendar_day_window(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")
        grant_date = date(2024, 1, 31)
        rows = [
            {"date": "2024-01-01", "close": 99, "source": "YAHOO"},
            {"date": "2024-01-02", "close": 10, "source": "YAHOO"},
            {"date": "2024-01-15", "close": 20, "source": "YAHOO"},
            {"date": "2024-01-31", "close": 30, "source": "YAHOO"},
        ]

        result = rsu_tax.average_grant_price(rows, grant_date)

        self.assertAlmostEqual(result.price_usd, 20)
        self.assertEqual(result.row_count, 3)
        self.assertIn("30-calendar-day average close", result.source)

    def test_latest_sale_price_uses_latest_row_on_or_before_sale_date(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        result = rsu_tax.latest_sale_price(
            [
                {"date": "2024-01-01", "close": 10, "source": "YAHOO"},
                {"date": "2024-01-03", "close": 30, "source": "YAHOO"},
                {"date": "2024-01-04", "close": 40, "source": "YAHOO"},
            ],
            date(2024, 1, 3),
        )

        self.assertAlmostEqual(result.price_usd, 30)
        self.assertEqual(result.source, "YAHOO close on 2024-01-03")

    def test_market_price_resolution_ignores_non_positive_provider_prices(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")
        rows = [
            {"date": "2024-01-01", "close": 0, "source": "YAHOO"},
            {"date": "2024-01-02", "close": -5, "source": "YAHOO"},
            {"date": "2024-01-03", "close": "NaN", "source": "YAHOO"},
            {"date": "2024-01-04", "close": "Infinity", "source": "YAHOO"},
            {"date": "2024-01-05", "close": 30, "source": "YAHOO"},
        ]

        average = rsu_tax.average_grant_price(rows, date(2024, 1, 5))
        sale = rsu_tax.latest_sale_price(rows, date(2024, 1, 5))

        self.assertEqual(average.row_count, 1)
        self.assertAlmostEqual(average.price_usd, 30)
        self.assertAlmostEqual(sale.price_usd, 30)

    def test_rsu_tax_rejects_non_finite_inputs(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        invalid_inputs = [
            {"shares": math.nan},
            {"grant_price_usd": math.inf},
            {"sale_price_usd": math.nan},
            {"fx_usd_ils": math.inf},
            {"ordinary_tax_rate": math.nan},
            {"sale_fees_ils": math.inf},
            {"capital_gain_offset_ils": math.nan},
            {"salary_ytd_ils": math.inf},
        ]

        for override in invalid_inputs:
            with self.subTest(override=override):
                values = {
                    "shares": 100.0,
                    "grant_price_usd": 10.0,
                    "sale_price_usd": 30.0,
                    "fx_usd_ils": 4.0,
                    "ordinary_tax_rate": 0.47,
                    "sale_fees_ils": 0.0,
                    "capital_gain_offset_ils": 0.0,
                    "salary_ytd_ils": 0.0,
                }
                values.update(override)
                with self.assertRaises(ValueError):
                    rsu_tax.calculate_rsu_tax(rsu_tax.RsuTaxInputs(**values))

    def test_rsu_tax_rejects_sale_dates_before_grant_dates(self):
        rsu_tax = importlib.import_module("investor_toolkit.rsu_tax")

        with self.assertRaisesRegex(ValueError, "sale date cannot be before grant date"):
            rsu_tax.calculate_rsu_tax(
                rsu_tax.RsuTaxInputs(
                    shares=100.0,
                    grant_price_usd=10.0,
                    sale_price_usd=30.0,
                    fx_usd_ils=4.0,
                    ordinary_tax_rate=0.47,
                    grant_date=date(2025, 1, 1),
                    sale_date=date(2024, 12, 31),
                )
            )

    def test_exchange_rate_provider_parses_usd_ils(self):
        providers = importlib.import_module("investor_toolkit.providers")

        class FakeHttp:
            def get_json(self, url, headers=None):
                return {
                    "result": "success",
                    "time_last_update_utc": "Sat, 30 May 2026 00:00:01 +0000",
                    "rates": {"ILS": 3.7},
                }

        with tempfile.TemporaryDirectory() as tmp:
            rate = providers.ExchangeRateProvider(tmp, http=FakeHttp()).get_usd_ils_rate()

        self.assertAlmostEqual(rate.rate, 3.7)
        self.assertIn("ExchangeRate-API latest USD/ILS", rate.source)

    def test_exchange_rate_provider_rejects_non_finite_rates(self):
        providers = importlib.import_module("investor_toolkit.providers")

        class FakeHttp:
            def get_json(self, url, headers=None):
                return {
                    "result": "success",
                    "rates": {"ILS": "NaN"},
                }

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(providers.ProviderError):
                providers.ExchangeRateProvider(tmp, http=FakeHttp()).get_usd_ils_rate()

    def test_missing_required_cli_flags_fail_non_interactively(self):
        cli = importlib.import_module("investor_toolkit.cli")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(["rsu-tax", "--shares", "10", "--ordinary-tax-rate", "47"])

        self.assertEqual(exit_code, 2)
        self.assertIn("missing required --grant-price-usd", stderr.getvalue())

    def test_manual_cli_outputs_legacy_comparison(self):
        cli = importlib.import_module("investor_toolkit.cli")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli.main(
                [
                    "rsu-tax",
                    "--shares",
                    "100",
                    "--grant-price-usd",
                    "10",
                    "--sale-price-usd",
                    "30",
                    "--fx-usd-ils",
                    "4",
                    "--ordinary-tax-rate",
                    "47",
                ]
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Israeli Section 102 RSU Tax Estimate", output)
        self.assertIn("Scenario: not selected - showing comparison", output)
        self.assertIn("Qualified Section 102 capital-gains track", output)
        self.assertIn("Early / non-compliant sale estimate", output)

    def test_ticker_grant_date_cli_infers_qualified_scenario(self):
        cli = importlib.import_module("investor_toolkit.cli")
        grant_date = date.today() - timedelta(days=365 * 3)
        rows = _market_rows(grant_date, date.today())

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            research_root = f"{tmp}/research"
            with _fake_rsu_providers(cli, rows, fx_rate=4):
                with redirect_stdout(stdout):
                    exit_code = cli.main(
                        [
                            "--research-root",
                            research_root,
                            "rsu-tax",
                            "--ticker",
                            "MSFT",
                            "--grant-date",
                            grant_date.isoformat(),
                            "--shares",
                            "100",
                            "--ordinary-tax-rate",
                            "47",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scenario: Qualified Section 102 capital-gains track", output)
        self.assertIn("Ticker", output)
        self.assertIn("MSFT", output)
        self.assertIn("ExchangeRate-API test USD/ILS", output)
        self.assertNotIn("Scenario: Early / non-compliant sale estimate", output)

    def test_interactive_cli_prompts_for_ticker_and_grant_date_first(self):
        cli = importlib.import_module("investor_toolkit.cli")
        grant_date = date.today() - timedelta(days=365 * 3)
        rows = _market_rows(grant_date, date.today())

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            research_root = f"{tmp}/research"
            stdin = _InteractiveStdin(f"MSFT\n{grant_date.isoformat()}\n100\n47\n")
            with _fake_rsu_providers(cli, rows, fx_rate=4):
                with patch("sys.stdin", stdin), redirect_stdout(stdout):
                    exit_code = cli.main(["--research-root", research_root, "rsu-tax"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Ticker (blank for manual price entry):", output)
        self.assertIn("Grant date (YYYY-MM-DD, blank for manual grant price):", output)
        self.assertIn("Shares:", output)
        self.assertIn("Marginal ordinary tax rate", output)
        self.assertNotIn("Grant FMV / 30-day average per share in USD:", output)
        self.assertNotIn("Sale price per share in USD:", output)
        self.assertIn("Scenario: Qualified Section 102 capital-gains track", output)
        self.assertIn("30-calendar-day average close", output)
        self.assertIn("YAHOO close on", output)

    def test_interactive_cli_manual_price_fallback_after_market_failure(self):
        cli = importlib.import_module("investor_toolkit.cli")
        grant_date = date.today() - timedelta(days=365 * 3)

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            research_root = f"{tmp}/research"
            stdin = _InteractiveStdin(f"MSFT\n{grant_date.isoformat()}\n100\n47\n10\n30\n")
            with _fake_rsu_providers(cli, None, fx_rate=4):
                with patch("sys.stdin", stdin), redirect_stdout(stdout):
                    exit_code = cli.main(["--research-root", research_root, "rsu-tax"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Could not fetch market prices automatically", output)
        self.assertIn("Grant FMV / 30-day average per share in USD:", output)
        self.assertIn("Sale price per share in USD:", output)
        self.assertIn("manual input after market-data fetch failure", output)

    def test_ticker_grant_date_cli_infers_early_sale_scenario(self):
        cli = importlib.import_module("investor_toolkit.cli")
        grant_date = date.today() - timedelta(days=365)
        rows = _market_rows(grant_date, date.today())

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            research_root = f"{tmp}/research"
            with _fake_rsu_providers(cli, rows, fx_rate=4):
                with redirect_stdout(stdout):
                    exit_code = cli.main(
                        [
                            "--research-root",
                            research_root,
                            "rsu-tax",
                            "--ticker",
                            "MSFT",
                            "--grant-date",
                            grant_date.isoformat(),
                            "--shares",
                            "100",
                            "--ordinary-tax-rate",
                            "47",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertIn("Scenario: Early / non-compliant sale estimate", stdout.getvalue())

    def test_cli_scenario_override_wins_over_date_inference(self):
        cli = importlib.import_module("investor_toolkit.cli")
        grant_date = date.today() - timedelta(days=365 * 3)
        rows = _market_rows(grant_date, date.today())

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            research_root = f"{tmp}/research"
            with _fake_rsu_providers(cli, rows, fx_rate=4):
                with redirect_stdout(stdout):
                    exit_code = cli.main(
                        [
                            "--research-root",
                            research_root,
                            "rsu-tax",
                            "--ticker",
                            "MSFT",
                            "--grant-date",
                            grant_date.isoformat(),
                            "--shares",
                            "100",
                            "--ordinary-tax-rate",
                            "47",
                            "--early-sale",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scenario: Early / non-compliant sale estimate", output)
        self.assertIn("forced by --early-sale", output)

def _market_rows(grant_date: date, sale_date: date) -> list[dict[str, object]]:
    return [
        {"date": (grant_date - timedelta(days=20)).isoformat(), "close": 10, "source": "YAHOO"},
        {"date": (grant_date - timedelta(days=10)).isoformat(), "close": 20, "source": "YAHOO"},
        {"date": grant_date.isoformat(), "close": 30, "source": "YAHOO"},
        {"date": sale_date.isoformat(), "close": 50, "source": "YAHOO"},
    ]


class _FakeMarketProvider:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def get_historical_prices(self, ticker, company_dir, start=None, end=None, refresh=False):
        if self.rows is None:
            providers = importlib.import_module("investor_toolkit.providers")
            raise providers.ProviderError("market unavailable")
        return self.rows


class _FakeFxProvider:
    def __init__(self, rate: float) -> None:
        self.rate = rate

    def get_usd_ils_rate(self):
        providers = importlib.import_module("investor_toolkit.providers")
        return providers.FxRate(
            base_currency="USD",
            target_currency="ILS",
            rate=self.rate,
            provider="ExchangeRate-API",
            fetched_at="2026-05-30T00:00:00Z",
            source="ExchangeRate-API test USD/ILS",
        )


def _fake_rsu_providers(cli, rows: list[dict[str, object]], fx_rate: float):
    return patch.multiple(
        cli,
        StooqMarketDataProvider=lambda: _FakeMarketProvider(rows),
        ExchangeRateProvider=lambda root, research_root=None: _FakeFxProvider(fx_rate),
    )


class _InteractiveStdin(io.StringIO):
    def isatty(self):
        return True


if __name__ == "__main__":
    unittest.main()
