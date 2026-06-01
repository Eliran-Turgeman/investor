import copy
import importlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


class ValuationEngineTests(unittest.TestCase):
    def test_assumptions_init_writes_template_with_judgment_nulls(self):
        cli = importlib.import_module("investor_toolkit.cli")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            output = Path(tmp) / "assumptions" / "ACME.base.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "--research-root",
                        str(research_root),
                        "assumptions",
                        "init",
                        "acme",
                        "--model",
                        "fcff-dcf",
                        "--scenario",
                        "base",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            assumptions = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(assumptions["ticker"], "ACME")
            self.assertEqual(assumptions["capitalStructureAdjustments"]["cashAndEquivalents"], 100.0)
            self.assertIsNone(assumptions["businessAssumptions"]["targetOperatingMargin"])
            self.assertIn("Wrote assumptions template", stdout.getvalue())

    def test_validation_rejects_terminal_growth_at_or_above_discount_rate(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            path = Path(tmp) / "bad.json"
            assumptions = _base_fcff_assumptions()
            assumptions["businessAssumptions"]["terminalGrowthRate"] = 0.10
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

        self.assertFalse(report.ok)
        self.assertTrue(any("terminalGrowthRate" in error for error in report.errors))

    def test_validation_rejects_non_positive_dcf_discount_rates(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        for model, business in [
            ("fcff-dcf", None),
            (
                "owner-earnings-dcf",
                {
                    "ownerEarningsBase": 100.0,
                    "ownerEarningsGrowth": [{"year": 1, "value": 0.03}, {"year": 2, "value": 0.03}],
                    "maintenanceCapexAssumption": 0.0,
                    "terminalGrowthRate": -0.01,
                },
            ),
        ]:
            with self.subTest(model=model):
                with tempfile.TemporaryDirectory() as tmp:
                    research_root = _write_research_fixture(tmp)
                    path = Path(tmp) / f"{model}.json"
                    assumptions = _base_fcff_assumptions()
                    assumptions["model"] = model
                    assumptions["businessAssumptions"]["terminalGrowthRate"] = -0.01
                    if business is not None:
                        assumptions["businessAssumptions"] = business
                    assumptions["discountingAssumptions"]["discountRate"] = 0.0
                    path.write_text(json.dumps(assumptions), encoding="utf-8")

                    report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

                self.assertFalse(report.ok)
                self.assertTrue(any("discountingAssumptions.discountRate" in error for error in report.errors))

    def test_validation_rejects_invalid_fcff_dilution_assumptions(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        for dilution in ("not numeric", -1.0):
            with self.subTest(dilution=dilution):
                with tempfile.TemporaryDirectory() as tmp:
                    research_root = _write_research_fixture(tmp)
                    path = Path(tmp) / "bad-dilution.json"
                    assumptions = _base_fcff_assumptions()
                    assumptions["shareAssumptions"]["annualDilutionRate"] = dilution
                    path.write_text(json.dumps(assumptions), encoding="utf-8")

                    report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

                self.assertFalse(report.ok)
                self.assertTrue(any("annualDilutionRate" in error for error in report.errors))

    def test_validation_rejects_invalid_capital_structure_adjustments(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        cases = [
            (
                "fcff-minority-nonnumeric",
                lambda item: item["capitalStructureAdjustments"].update({"minorityInterest": "bad"}),
                "capitalStructureAdjustments.minorityInterest",
            ),
            (
                "fcff-non-operating-negative",
                lambda item: item["capitalStructureAdjustments"].update({"nonOperatingAssets": -1.0}),
                "capitalStructureAdjustments.nonOperatingAssets",
            ),
            (
                "owner-cash-nonnumeric",
                lambda item: (
                    item.update(
                        {
                            "model": "owner-earnings-dcf",
                            "businessAssumptions": {
                                "ownerEarningsBase": 100.0,
                                "ownerEarningsGrowth": [{"year": 1, "value": 0.03}, {"year": 2, "value": 0.03}],
                                "maintenanceCapexAssumption": 0.0,
                                "terminalGrowthRate": 0.02,
                            },
                        }
                    ),
                    item["capitalStructureAdjustments"].update({"cashAndEquivalents": "bad"}),
                ),
                "capitalStructureAdjustments.cashAndEquivalents",
            ),
            (
                "owner-debt-negative",
                lambda item: (
                    item.update(
                        {
                            "model": "owner-earnings-dcf",
                            "businessAssumptions": {
                                "ownerEarningsBase": 100.0,
                                "ownerEarningsGrowth": [{"year": 1, "value": 0.03}, {"year": 2, "value": 0.03}],
                                "maintenanceCapexAssumption": 0.0,
                                "terminalGrowthRate": 0.02,
                            },
                        }
                    ),
                    item["capitalStructureAdjustments"].update({"totalDebt": -1.0}),
                ),
                "capitalStructureAdjustments.totalDebt",
            ),
            (
                "epv-non-operating-nonnumeric",
                lambda item: (
                    item.update(
                        {
                            "model": "epv",
                            "businessAssumptions": {"normalizedOperatingEarnings": 100.0, "taxRate": 0.25},
                        }
                    ),
                    item["capitalStructureAdjustments"].update({"nonOperatingAssets": "bad"}),
                ),
                "capitalStructureAdjustments.nonOperatingAssets",
            ),
            (
                "multiples-minority-negative",
                lambda item: (
                    item.update(
                        {
                            "model": "multiples",
                            "businessAssumptions": {
                                "normalizedMetric": 100.0,
                                "fairMultiple": 15.0,
                                "metricType": "earnings",
                            },
                        }
                    ),
                    item["capitalStructureAdjustments"].update({"minorityInterest": -1.0}),
                ),
                "capitalStructureAdjustments.minorityInterest",
            ),
        ]

        for name, mutate, expected_error in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    research_root = _write_research_fixture(tmp)
                    path = Path(tmp) / f"{name}.json"
                    assumptions = _base_fcff_assumptions()
                    mutate(assumptions)
                    path.write_text(json.dumps(assumptions), encoding="utf-8")

                    report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

                self.assertFalse(report.ok)
                self.assertTrue(any(expected_error in error for error in report.errors), report.errors)

    def test_owner_earnings_scalar_growth_must_be_finite(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            path = Path(tmp) / "owner-nan-growth.json"
            assumptions = _base_fcff_assumptions()
            assumptions["model"] = "owner-earnings-dcf"
            assumptions["businessAssumptions"] = {
                "ownerEarningsBase": 100.0,
                "ownerEarningsGrowth": float("nan"),
                "maintenanceCapexAssumption": 0.0,
                "terminalGrowthRate": 0.02,
            }
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

        self.assertFalse(report.ok)
        self.assertTrue(any("businessAssumptions.ownerEarningsGrowth" in error for error in report.errors))

    def test_validation_rejects_boolean_numeric_assumptions(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            path = Path(tmp) / "bool-assumption.json"
            assumptions = _base_fcff_assumptions()
            assumptions["businessAssumptions"]["targetOperatingMargin"] = True
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

        self.assertFalse(report.ok)
        self.assertTrue(any("businessAssumptions.targetOperatingMargin" in error for error in report.errors))

    def test_validation_rejects_growth_rates_at_or_below_negative_one_hundred_percent(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        cases = [
            ("fcff-growth", "fcff-dcf", "businessAssumptions.revenueGrowth year 1", lambda item: item["businessAssumptions"]["revenueGrowth"][0].update({"value": -1.0})),
            ("fcff-terminal", "fcff-dcf", "businessAssumptions.terminalGrowthRate", lambda item: item["businessAssumptions"].update({"terminalGrowthRate": -1.0})),
            (
                "owner-growth",
                "owner-earnings-dcf",
                "businessAssumptions.ownerEarningsGrowth year 1",
                lambda item: (
                    item.update(
                        {
                            "model": "owner-earnings-dcf",
                            "businessAssumptions": {
                                "ownerEarningsBase": 100.0,
                                "ownerEarningsGrowth": [{"year": 1, "value": -1.0}, {"year": 2, "value": 0.03}],
                                "maintenanceCapexAssumption": 0.0,
                                "terminalGrowthRate": 0.02,
                            },
                        }
                    )
                ),
            ),
            (
                "owner-terminal",
                "owner-earnings-dcf",
                "businessAssumptions.terminalGrowthRate",
                lambda item: (
                    item.update(
                        {
                            "model": "owner-earnings-dcf",
                            "businessAssumptions": {
                                "ownerEarningsBase": 100.0,
                                "ownerEarningsGrowth": [{"year": 1, "value": 0.03}, {"year": 2, "value": 0.03}],
                                "maintenanceCapexAssumption": 0.0,
                                "terminalGrowthRate": -1.0,
                            },
                        }
                    )
                ),
            ),
        ]
        for name, _model, expected_error, mutate in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    research_root = _write_research_fixture(tmp)
                    path = Path(tmp) / f"{name}.json"
                    assumptions = _base_fcff_assumptions()
                    mutate(assumptions)
                    path.write_text(json.dumps(assumptions), encoding="utf-8")

                    report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

                self.assertFalse(report.ok)
                self.assertTrue(any(expected_error in error for error in report.errors), report.errors)

    def test_validation_rejects_non_positive_local_revenue_for_fcff(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp, latest_revenue=0.0)
            path = Path(tmp) / "base.json"
            path.write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")

            report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

        self.assertFalse(report.ok)
        self.assertTrue(any("latest revenue must be positive" in error for error in report.errors))

    def test_fcff_dcf_calculation_and_sensitivity_direction(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            path = Path(tmp) / "base.json"
            assumptions = _base_fcff_assumptions()
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            result = valuation.run_valuation(
                "ACME",
                path,
                cwd=tmp,
                research_root=research_root,
                include_sensitivity=True,
                include_debug=True,
            )

            high_discount = copy.deepcopy(assumptions)
            high_discount["discountingAssumptions"]["discountRate"] = 0.12
            high_discount_path = Path(tmp) / "high-discount.json"
            high_discount_path.write_text(json.dumps(high_discount), encoding="utf-8")
            high_discount_result = valuation.run_valuation("ACME", high_discount_path, cwd=tmp, research_root=research_root)

            high_growth = copy.deepcopy(assumptions)
            high_growth["businessAssumptions"]["terminalGrowthRate"] = 0.03
            high_growth_path = Path(tmp) / "high-growth.json"
            high_growth_path.write_text(json.dumps(high_growth), encoding="utf-8")
            high_growth_result = valuation.run_valuation("ACME", high_growth_path, cwd=tmp, research_root=research_root)

        fair_value = result["valuation"]["fairValuePerShare"]
        self.assertAlmostEqual(fair_value, 17.45, places=2)
        self.assertLess(high_discount_result["valuation"]["fairValuePerShare"], fair_value)
        self.assertGreater(high_growth_result["valuation"]["fairValuePerShare"], fair_value)
        self.assertIn("discountRateVsTerminalGrowthRate", result["sensitivity"])
        self.assertIn("projectedYears", result["debug"])

    def test_local_data_prefers_company_facts_ttm_when_available(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            _write_company_facts_fixture(research_root / "ACME")

            data = valuation.load_local_financial_data("ACME", cwd=tmp, research_root=research_root)

        self.assertEqual(data.latest_ttm["period"], "2025-Q3-TTM")
        self.assertAlmostEqual(data.latest_value("revenue"), 1150.0)
        self.assertAlmostEqual(data.latest_value("operating_income"), 250.0)
        self.assertAlmostEqual(data.latest_value("free_cash_flow"), 135.0)
        self.assertAlmostEqual(data.latest_value("cash_and_equivalents"), 120.0)
        self.assertAlmostEqual(data.latest_value("total_debt"), 60.0)
        self.assertAlmostEqual(data.latest_value("shares_outstanding"), 90.0)

    def test_local_data_uses_q1_company_facts_for_ttm_base(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            _write_company_facts_q1_fixture(research_root / "ACME")

            data = valuation.load_local_financial_data("ACME", cwd=tmp, research_root=research_root)

        self.assertEqual(data.latest_ttm["period"], "2025-Q1-TTM")
        self.assertAlmostEqual(data.latest_value("revenue"), 1050.0)
        self.assertAlmostEqual(data.latest_value("operating_income"), 215.0)
        self.assertAlmostEqual(data.latest_value("free_cash_flow"), 114.0)

    def test_ttm_prefers_ytd_10q_fact_over_same_period_standalone_quarter(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            _write_company_facts_with_quarter_and_ytd_fixture(research_root / "ACME")

            data = valuation.load_local_financial_data("ACME", cwd=tmp, research_root=research_root)

        self.assertEqual(data.latest_ttm["period"], "2025-Q3-TTM")
        self.assertAlmostEqual(data.latest_value("revenue"), 1150.0)
        self.assertAlmostEqual(data.latest_value("operating_income"), 250.0)
        self.assertAlmostEqual(data.latest_value("free_cash_flow"), 135.0)

    def test_local_market_cap_ignores_non_positive_metric_cache_values(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            metrics_path = research_root / "ACME" / "metrics" / "metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["periods"][-1]["market_cap"] = -100.0
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

            data = valuation.load_local_financial_data("ACME", cwd=tmp, research_root=research_root)

        self.assertAlmostEqual(data.latest_value("market_cap"), 1000.0)

    def test_multiples_validation_ignores_non_positive_historical_multiples(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            metrics_path = research_root / "ACME" / "metrics" / "metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["periods"][0]["price_to_earnings"] = -10.0
            metrics["periods"][1]["price_to_earnings"] = -5.0
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

            assumptions = _base_fcff_assumptions()
            assumptions["model"] = "multiples"
            assumptions["businessAssumptions"] = {
                "normalizedMetric": 100.0,
                "fairMultiple": 1.0,
                "metricType": "earnings",
            }
            path = Path(tmp) / "multiples.json"
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            report = valuation.validate_assumptions_file(path, cwd=tmp, research_root=research_root)

        self.assertTrue(report.ok)
        self.assertFalse(any(warning.code == "FAIR_MULTIPLE_ABOVE_HISTORY" for warning in report.warnings))

    def test_valuation_api_resolves_relative_paths_against_cwd(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            assumptions = _base_fcff_assumptions()
            Path(tmp, "base.json").write_text(json.dumps(assumptions), encoding="utf-8")

            result = valuation.run_valuation("ACME", "base.json", cwd=tmp, research_root=research_root)
            report = valuation.validate_assumptions_file("base.json", cwd=tmp, research_root=research_root)
            initialized = valuation.init_assumptions_file(
                "ACME",
                model="fcff-dcf",
                scenario="base",
                output_path="assumptions/relative.json",
                cwd=tmp,
                research_root=research_root,
            )
            initialized_exists = initialized.is_file()
            initialized_parent = initialized.parent

        self.assertEqual(result["ticker"], "ACME")
        self.assertTrue(report.ok)
        self.assertTrue(initialized_exists)
        self.assertEqual(initialized_parent, Path(tmp) / "assumptions")

    def test_epv_and_multiples_models(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            epv = _base_fcff_assumptions()
            epv["model"] = "epv"
            epv["businessAssumptions"] = {"normalizedOperatingEarnings": 100.0, "taxRate": 0.25}
            epv_path = Path(tmp) / "epv.json"
            epv_path.write_text(json.dumps(epv), encoding="utf-8")

            multiples = _base_fcff_assumptions()
            multiples["model"] = "multiples"
            multiples["businessAssumptions"] = {
                "normalizedMetric": 100.0,
                "fairMultiple": 15.0,
                "metricType": "earnings",
            }
            multiples_path = Path(tmp) / "multiples.json"
            multiples_path.write_text(json.dumps(multiples), encoding="utf-8")

            epv_result = valuation.run_valuation("ACME", epv_path, cwd=tmp, research_root=research_root)
            multiples_result = valuation.run_valuation("ACME", multiples_path, cwd=tmp, research_root=research_root)

        self.assertAlmostEqual(epv_result["valuation"]["fairValuePerShare"], 8.0)
        self.assertAlmostEqual(multiples_result["valuation"]["fairValuePerShare"], 15.0)

    def test_reverse_dcf_solves_one_unknown(self):
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            assumptions = _base_fcff_assumptions()
            assumptions["model"] = "reverse-dcf"
            assumptions["solveFor"] = "targetOperatingMargin"
            assumptions["targetValueBasis"] = "current_market_price"
            assumptions["currentSharePrice"] = 10.0
            assumptions["targetSharePrice"] = None
            assumptions["targetEnterpriseValue"] = None
            assumptions["businessAssumptions"]["targetOperatingMargin"] = None
            path = Path(tmp) / "reverse.json"
            path.write_text(json.dumps(assumptions), encoding="utf-8")

            result = valuation.run_valuation("ACME", path, cwd=tmp, research_root=research_root, include_debug=True)

        self.assertEqual(result["model"], "reverse-dcf")
        implied = result["drivers"]["impliedAssumption"]
        self.assertGreater(implied, 0)
        self.assertLess(implied, 0.8)
        self.assertAlmostEqual(result["valuation"]["fairValuePerShare"], 10.0, places=2)

    def test_json_output_schema_agent_context_and_scenario_compare(self):
        cli = importlib.import_module("investor_toolkit.cli")
        valuation = importlib.import_module("investor_toolkit.valuation.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            base_path = Path(tmp) / "base.json"
            base_path.write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")

            aggressive = _base_fcff_assumptions()
            aggressive["scenario"] = "aggressive"
            aggressive["businessAssumptions"]["targetOperatingMargin"] = 0.25
            aggressive_path = Path(tmp) / "aggressive.json"
            aggressive_path.write_text(json.dumps(aggressive), encoding="utf-8")

            output = Path(tmp) / "valuation.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "--research-root",
                        str(research_root),
                        "value",
                        "ACME",
                        "--assumptions",
                        str(base_path),
                        "--format",
                        "json",
                        "--output",
                        str(output),
                    ]
                )
            result = json.loads(output.read_text(encoding="utf-8"))
            paths = valuation.export_agent_context(result, json.loads(base_path.read_text(encoding="utf-8")), cwd=tmp)
            comparison = valuation.compare_valuations(
                "ACME",
                [base_path, aggressive_path],
                cwd=tmp,
                research_root=research_root,
            )
            context_markdown_exists = Path(paths["markdown"]).is_file()

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["schemaVersion"], "1.0")
        self.assertEqual(result["valuation"]["currentSharePrice"], 10.0)
        self.assertTrue(context_markdown_exists)
        self.assertEqual(len(comparison["scenarios"]), 2)
        self.assertGreater(
            comparison["scenarios"][1]["fairValuePerShare"],
            comparison["scenarios"][0]["fairValuePerShare"],
        )

    def test_value_rejects_extra_positional_ticker(self):
        cli = importlib.import_module("investor_toolkit.cli")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            base_path = Path(tmp) / "base.json"
            base_path.write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "--research-root",
                        str(research_root),
                        "value",
                        "ACME",
                        "EXTRA",
                        "--assumptions",
                        str(base_path),
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("value accepts only one ticker", stderr.getvalue())


def _write_research_fixture(tmp: str, latest_revenue: float = 1000.0) -> Path:
    research_root = Path(tmp) / "research"
    ticker_dir = research_root / "ACME"
    (ticker_dir / "data").mkdir(parents=True)
    (ticker_dir / "metrics").mkdir()
    (ticker_dir / "company.json").write_text(
        json.dumps({"ticker": "ACME", "name": "Acme Corp"}),
        encoding="utf-8",
    )
    (ticker_dir / "data" / "financials.json").write_text(
        json.dumps(
            [
                {
                    "ticker": "ACME",
                    "period": "2023-FY",
                    "fiscalYear": 2023,
                    "revenue": 900.0,
                    "operatingIncome": 162.0,
                    "pretaxIncome": 150.0,
                    "incomeTaxExpense": 37.5,
                    "netIncome": 112.5,
                    "operatingCashFlow": 140.0,
                    "capitalExpenditures": 30.0,
                    "cash": 80.0,
                    "totalDebt": 60.0,
                    "dilutedShares": 100.0,
                },
                {
                    "ticker": "ACME",
                    "period": "2024-FY",
                    "fiscalYear": 2024,
                    "revenue": latest_revenue,
                    "operatingIncome": 200.0,
                    "pretaxIncome": 180.0,
                    "incomeTaxExpense": 45.0,
                    "netIncome": 135.0,
                    "operatingCashFlow": 170.0,
                    "capitalExpenditures": 40.0,
                    "cash": 100.0,
                    "totalDebt": 50.0,
                    "dilutedShares": 100.0,
                },
            ]
        ),
        encoding="utf-8",
    )
    (ticker_dir / "data" / "prices.json").write_text(
        json.dumps([{"ticker": "ACME", "date": "2026-05-29", "close": 10.0, "adjustedClose": 10.0}]),
        encoding="utf-8",
    )
    (ticker_dir / "metrics" / "metrics.json").write_text(
        json.dumps(
            {
                "ticker": "ACME",
                "periods": [
                    {
                        "period": "2023-FY",
                        "revenue": 900.0,
                        "operating_margin": 0.18,
                        "free_cash_flow": 110.0,
                        "weighted_average_diluted_shares": 100.0,
                        "cash_and_equivalents": 80.0,
                        "total_debt": 60.0,
                        "market_cap": 1000.0,
                        "price_to_earnings": 8.9,
                    },
                    {
                        "period": "2024-FY",
                        "revenue": latest_revenue,
                        "operating_margin": 0.20,
                        "free_cash_flow": 130.0,
                        "weighted_average_diluted_shares": 100.0,
                        "cash_and_equivalents": 100.0,
                        "total_debt": 50.0,
                        "market_cap": 1000.0,
                        "price_to_earnings": 7.4,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return research_root


def _write_company_facts_fixture(ticker_dir: Path) -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 600.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 1000.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 750.0),
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 120.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 200.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 170.0),
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 90.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 160.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 130.0),
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 30.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 50.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 45.0),
                        ]
                    }
                },
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "units": {"shares": [_fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 90.0)]}
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {"USD": [_fact("2025", "Q3", "10-Q", None, "2025-03-31", 120.0)]}
                },
                "LongTermDebtCurrent": {
                    "units": {"USD": [_fact("2025", "Q3", "10-Q", None, "2025-03-31", 10.0)]}
                },
                "LongTermDebtNoncurrent": {
                    "units": {"USD": [_fact("2025", "Q3", "10-Q", None, "2025-03-31", 50.0)]}
                },
            }
        }
    }
    (ticker_dir / "data" / "company_facts.json").write_text(json.dumps(facts), encoding="utf-8")


def _write_company_facts_q1_fixture(ticker_dir: Path) -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q1", "10-Q", "2023-07-01", "2023-09-30", 200.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 1000.0),
                            _fact("2025", "Q1", "10-Q", "2024-07-01", "2024-09-30", 250.0),
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q1", "10-Q", "2023-07-01", "2023-09-30", 40.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 200.0),
                            _fact("2025", "Q1", "10-Q", "2024-07-01", "2024-09-30", 55.0),
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q1", "10-Q", "2023-07-01", "2023-09-30", 30.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 160.0),
                            _fact("2025", "Q1", "10-Q", "2024-07-01", "2024-09-30", 35.0),
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q1", "10-Q", "2023-07-01", "2023-09-30", 7.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 50.0),
                            _fact("2025", "Q1", "10-Q", "2024-07-01", "2024-09-30", 8.0),
                        ]
                    }
                },
            }
        }
    }
    (ticker_dir / "data" / "company_facts.json").write_text(json.dumps(facts), encoding="utf-8")


def _write_company_facts_with_quarter_and_ytd_fixture(ticker_dir: Path) -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 600.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 1000.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 750.0),
                            _fact("2025", "Q3", "10-Q", "2025-01-01", "2025-03-31", 260.0),
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 120.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 200.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 170.0),
                            _fact("2025", "Q3", "10-Q", "2025-01-01", "2025-03-31", 60.0),
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 90.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 160.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 130.0),
                            _fact("2025", "Q3", "10-Q", "2025-01-01", "2025-03-31", 50.0),
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _fact("2024", "Q3", "10-Q", "2023-07-01", "2024-03-31", 30.0),
                            _fact("2024", "FY", "10-K", "2023-07-01", "2024-06-30", 50.0),
                            _fact("2025", "Q3", "10-Q", "2024-07-01", "2025-03-31", 45.0),
                            _fact("2025", "Q3", "10-Q", "2025-01-01", "2025-03-31", 18.0),
                        ]
                    }
                },
            }
        }
    }
    (ticker_dir / "data" / "company_facts.json").write_text(json.dumps(facts), encoding="utf-8")


def _fact(fy: str, fp: str, form: str, start: str | None, end: str, value: float) -> dict:
    return {
        "fy": int(fy),
        "fp": fp,
        "form": form,
        "filed": end,
        "start": start,
        "end": end,
        "val": value,
        "accn": f"{fy}-{fp}",
    }


def _base_fcff_assumptions() -> dict:
    return {
        "schemaVersion": "1.0",
        "ticker": "ACME",
        "companyName": "Acme Corp",
        "valuationDate": "2026-05-31",
        "scenario": "base",
        "model": "fcff-dcf",
        "currency": "USD",
        "projection": {"explicitYears": 2, "baseFiscalYear": 2024},
        "businessAssumptions": {
            "revenueGrowth": [{"year": 1, "value": 0.10}, {"year": 2, "value": 0.05}],
            "targetOperatingMargin": 0.20,
            "taxRate": 0.25,
            "reinvestmentRate": 0.20,
            "terminalGrowthRate": 0.02,
        },
        "discountingAssumptions": {"discountRate": 0.10},
        "capitalStructureAdjustments": {
            "cashAndEquivalents": 100.0,
            "totalDebt": 50.0,
            "minorityInterest": 0,
            "nonOperatingAssets": 0,
        },
        "shareAssumptions": {"sharesOutstanding": 100.0, "annualDilutionRate": 0.0},
        "marginOfSafety": {"required": 0.25},
        "metadata": {"createdBy": "test", "source": "unit-test", "notes": []},
    }


if __name__ == "__main__":
    unittest.main()
