import importlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path


class PortfolioEngineTests(unittest.TestCase):
    def test_portfolio_init_creates_workbook_and_templates(self):
        cli = importlib.import_module("investor_toolkit.cli")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio" / "portfolio.xlsx"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["portfolio", "init", "--output", str(workbook)])

            sheets = xlsx.read_xlsx(workbook)
            self.assertEqual(exit_code, 0)
            self.assertTrue(workbook.is_file())
            self.assertTrue((workbook.parent / "holdings.json").is_file())
            self.assertTrue((workbook.parent / "watchlist.json").is_file())
            self.assertTrue((workbook.parent / "rules.json").is_file())
            self.assertIn("portfolio initialized", stdout.getvalue())
            self.assertIn("Holdings", sheets)
            self.assertIn("Signals", sheets)

    def test_workbook_import_round_trips_user_inputs(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            xlsx.write_xlsx(
                workbook,
                [
                    {
                        "name": "Holdings",
                        "rows": [
                            [
                                "Ticker",
                                "Shares",
                                "Cost Basis Per Share",
                                "Target Allocation",
                                "Max Allocation",
                                "Thesis Status",
                                "User Fair Value",
                                "Required Margin Of Safety",
                                "Notes",
                            ],
                            ["acme", 10, 8.5, "15%", "25%", "active", 18.0, "30%", "core holding"],
                        ],
                    },
                    {
                        "name": "Watchlist",
                        "rows": [
                            ["Ticker", "Target Entry Price", "Priority", "User Fair Value", "Required Margin Of Safety", "Notes"],
                            ["beta", 12, "high", 20, 0.2, "watch"],
                        ],
                    },
                    {
                        "name": "Assumptions",
                        "rows": [
                            [
                                "Ticker",
                                "Scenario",
                                "Model",
                                "Assumptions Path",
                                "Result Path",
                                "User Fair Value",
                                "Required Margin Of Safety",
                                "Notes",
                            ],
                            ["ACME", "base", "fcff-dcf", "assumptions/ACME.base.json", "", 19, "35%", "my value"],
                        ],
                    },
                ],
            )

            result = portfolio.import_portfolio_workbook(workbook, cwd=tmp)
            holdings = json.loads((Path(tmp) / "holdings.json").read_text(encoding="utf-8"))
            watchlist = json.loads((Path(tmp) / "watchlist.json").read_text(encoding="utf-8"))
            overrides = json.loads((Path(tmp) / "assumption_overrides.json").read_text(encoding="utf-8"))

        self.assertEqual(result["holdingsImported"], 1)
        self.assertEqual(holdings["holdings"][0]["ticker"], "ACME")
        self.assertEqual(holdings["holdings"][0]["targetAllocation"], 0.15)
        self.assertEqual(watchlist["watchlist"][0]["ticker"], "BETA")
        self.assertEqual(overrides["assumptions"][0]["requiredMarginOfSafety"], 0.35)
        self.assertNotIn("resultPath", overrides["assumptions"][0])

    def test_workbook_import_rejects_invalid_xlsx_with_clean_error(self):
        cli = importlib.import_module("investor_toolkit.cli")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            workbook.write_bytes(b"not an xlsx zip")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(["portfolio", "import", "--workbook", str(workbook)])

        self.assertEqual(exit_code, 2)
        self.assertIn("Invalid XLSX workbook", stderr.getvalue())

    def test_xlsx_writer_sanitizes_illegal_xml_text_characters(self):
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            xlsx.write_xlsx(
                workbook,
                [
                    {
                        "name": "Notes",
                        "rows": [["Ticker", "Notes"], ["ACME", "line\x0bnote\x00ok"]],
                    }
                ],
            )
            sheets = xlsx.read_xlsx(workbook)

        self.assertEqual(sheets["Notes"][1][1], "line note ok")

    def test_portfolio_value_and_signals_from_existing_assumptions(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.0",
                        "holdings": [
                            {
                                "ticker": "ACME",
                                "shares": 10,
                                "userFairValuePerShare": 18.0,
                                "requiredMarginOfSafety": 0.25,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")

            value_result = portfolio.run_portfolio_valuations(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                assumptions_dir=assumptions_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            signals = portfolio.build_portfolio_signals(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            export_result = portfolio.export_portfolio_workbook(
                Path(tmp) / "portfolio" / "portfolio.xlsx",
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            self.assertEqual(value_result["valuedCount"], 1)
            self.assertTrue((valuations_dir / "ACME.base.fcff-dcf.result.json").is_file())
            self.assertEqual(signals["rows"][0]["ticker"], "ACME")
            self.assertEqual(signals["rows"][0]["label"], "Opportunity")
            self.assertEqual(signals["rows"][0]["decisionValueSource"], "user fair value")
            self.assertTrue((portfolio_dir / "signals.json").is_file())
            self.assertTrue(Path(export_result["workbook"]).is_file())

    def test_stale_price_blocks_signal(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp, price_date="2020-01-01")
            portfolio_dir = Path(tmp) / "portfolio"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            valuations_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME", "userFairValuePerShare": 20.0}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")

            signals = portfolio.build_portfolio_signals(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )

        self.assertEqual(signals["rows"][0]["label"], "No decision")
        self.assertEqual(signals["rows"][0]["dataQuality"], "blocked")
        self.assertIn("stale", signals["rows"][0]["reason"])

    def test_portfolio_value_does_not_overwrite_same_scenario_different_models(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")
            epv = _base_fcff_assumptions()
            epv["model"] = "epv"
            epv["businessAssumptions"] = {"normalizedOperatingEarnings": 100.0, "taxRate": 0.25}
            (assumptions_dir / "ACME.base.epv.json").write_text(json.dumps(epv), encoding="utf-8")

            result = portfolio.run_portfolio_valuations(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                assumptions_dir=assumptions_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            written = sorted(path.name for path in valuations_dir.glob("ACME.*.result.json"))

        self.assertEqual(result["valuedCount"], 2)
        self.assertEqual(written, ["ACME.base.epv.result.json", "ACME.base.fcff-dcf.result.json"])

    def test_portfolio_value_does_not_overwrite_same_scenario_same_model(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            base = _base_fcff_assumptions()
            variant = _base_fcff_assumptions()
            variant["businessAssumptions"]["targetOperatingMargin"] = 0.25
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(base), encoding="utf-8")
            (assumptions_dir / "ACME.base.variant.json").write_text(json.dumps(variant), encoding="utf-8")

            result = portfolio.run_portfolio_valuations(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                assumptions_dir=assumptions_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            written = sorted(path.name for path in valuations_dir.glob("ACME.*.result.json"))

        self.assertEqual(result["valuedCount"], 2)
        self.assertEqual(written, ["ACME.base.fcff-dcf.2.result.json", "ACME.base.fcff-dcf.result.json"])

    def test_required_margin_zero_is_honored(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            portfolio_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.0",
                        "holdings": [
                            {
                                "ticker": "ACME",
                                "userFairValuePerShare": 10.0,
                                "requiredMarginOfSafety": 0.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")

            signals = portfolio.build_portfolio_signals(cwd=tmp, portfolio_dir=portfolio_dir, research_root=research_root)

        self.assertEqual(signals["rows"][0]["requiredMarginOfSafety"], 0.0)
        self.assertNotEqual(signals["rows"][0]["label"], "No decision")

    def test_import_rejects_missing_required_sheets_without_overwriting_json(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            holdings_path = Path(tmp) / "holdings.json"
            holdings_path.write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME", "shares": 1}]}),
                encoding="utf-8",
            )
            xlsx.write_xlsx(workbook, [{"name": "Some Other Sheet", "rows": [["Ticker"], ["BETA"]]}])

            with self.assertRaises(ValueError):
                portfolio.import_portfolio_workbook(workbook, cwd=tmp)
            holdings = json.loads(holdings_path.read_text(encoding="utf-8"))

        self.assertEqual(holdings["holdings"], [{"ticker": "ACME", "shares": 1}])

    def test_import_rejects_missing_ticker_column(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            xlsx.write_xlsx(
                workbook,
                [
                    {"name": "Holdings", "rows": [["Shares"], [10]]},
                    {"name": "Watchlist", "rows": [["Ticker"], ["ACME"]]},
                    {"name": "Assumptions", "rows": [["Ticker"], ["ACME"]]},
                ],
            )

            with self.assertRaises(ValueError):
                portfolio.import_portfolio_workbook(workbook, cwd=tmp)

    def test_export_uses_custom_valuations_dir_in_assumption_result_paths(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            workbook = portfolio_dir / "portfolio.xlsx"
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")

            portfolio.export_portfolio_workbook(
                workbook,
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir="custom-valuations",
                research_root=research_root,
            )
            sheets = xlsx.read_xlsx(workbook)

        assumptions_rows = sheets["Assumptions"]
        self.assertIn("custom-valuations", str(assumptions_rows[1][4]))

    def test_export_uses_custom_assumptions_dir_for_discovered_assumption_rows(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "custom-assumptions"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            workbook = portfolio_dir / "portfolio.xlsx"
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")

            portfolio.export_portfolio_workbook(
                workbook,
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                assumptions_dir=assumptions_dir,
                research_root=research_root,
            )
            sheets = xlsx.read_xlsx(workbook)

        assumptions_rows = sheets["Assumptions"]
        self.assertIn("custom-assumptions", str(assumptions_rows[1][3]))

    def test_signals_use_newest_available_price_date_across_valuation_results(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp, price_date="2020-01-01")
            portfolio_dir = Path(tmp) / "portfolio"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            valuations_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME", "userFairValuePerShare": 18.0}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            stale = _valuation_result("ACME", "aggressive", fair_value=25.0, price_date="2020-01-01")
            fresh = _valuation_result("ACME", "base", fair_value=18.0, price_date=date.today().isoformat())
            (valuations_dir / "ACME.aggressive.fcff-dcf.result.json").write_text(json.dumps(stale), encoding="utf-8")
            (valuations_dir / "ACME.base.fcff-dcf.result.json").write_text(json.dumps(fresh), encoding="utf-8")

            signals = portfolio.build_portfolio_signals(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )

        self.assertNotEqual(signals["rows"][0]["label"], "No decision")
        self.assertEqual(signals["rows"][0]["priceDate"], date.today().isoformat())

    def test_portfolio_refresh_returns_nonzero_for_valuation_errors(self):
        cli = importlib.import_module("investor_toolkit.cli")
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            bad = _base_fcff_assumptions()
            bad["businessAssumptions"]["terminalGrowthRate"] = 0.20
            (assumptions_dir / "ACME.base.json").write_text(json.dumps(bad), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = cli.main(
                        [
                            "portfolio",
                            "refresh",
                            "--offline",
                            "--portfolio-dir",
                            str(portfolio_dir),
                            "--assumptions-dir",
                            str(assumptions_dir),
                            "--valuations-dir",
                            str(valuations_dir),
                            "--research-root",
                            str(research_root),
                            "--workbook",
                            str(portfolio_dir / "portfolio.xlsx"),
                        ]
                    )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 2)
        self.assertIn("terminalGrowthRate", stdout.getvalue())

    def test_signal_warns_when_valuation_result_is_older_than_source_assumptions(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            portfolio_dir = Path(tmp) / "portfolio"
            assumptions_dir = Path(tmp) / "assumptions"
            valuations_dir = Path(tmp) / "valuations"
            portfolio_dir.mkdir()
            assumptions_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME"}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            (portfolio_dir / "rules.json").write_text(json.dumps(portfolio.default_rules()), encoding="utf-8")
            assumptions_path = assumptions_dir / "ACME.base.json"
            assumptions_path.write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")
            portfolio.run_portfolio_valuations(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                assumptions_dir=assumptions_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )
            result_path = valuations_dir / "ACME.base.fcff-dcf.result.json"
            os.utime(result_path, (1, 1))

            signals = portfolio.build_portfolio_signals(
                cwd=tmp,
                portfolio_dir=portfolio_dir,
                valuations_dir=valuations_dir,
                research_root=research_root,
            )

        self.assertIn("older than source assumptions", signals["rows"][0]["warnings"])

    def test_import_rejects_out_of_range_rates(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")
        xlsx = importlib.import_module("investor_toolkit.portfolio.xlsx")

        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "portfolio.xlsx"
            xlsx.write_xlsx(
                workbook,
                [
                    {
                        "name": "Holdings",
                        "rows": [
                            [
                                "Ticker",
                                "Shares",
                                "Cost Basis Per Share",
                                "Target Allocation",
                                "Max Allocation",
                                "Thesis Status",
                                "User Fair Value",
                                "Required Margin Of Safety",
                                "Notes",
                            ],
                            ["ACME", 1, 10, "-20%", "25%", "active", 18, "25%", ""],
                        ],
                    },
                    {"name": "Watchlist", "rows": [["Ticker"], ["BETA"]]},
                    {"name": "Assumptions", "rows": [["Ticker"], ["ACME"]]},
                ],
            )

            with self.assertRaises(ValueError):
                portfolio.import_portfolio_workbook(workbook, cwd=tmp)

    def test_invalid_signal_rules_are_rejected(self):
        portfolio = importlib.import_module("investor_toolkit.portfolio.engine")

        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            portfolio_dir.mkdir()
            (portfolio_dir / "holdings.json").write_text(
                json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "ACME", "userFairValuePerShare": 18.0}]}),
                encoding="utf-8",
            )
            (portfolio_dir / "watchlist.json").write_text(json.dumps({"schemaVersion": "1.0", "watchlist": []}), encoding="utf-8")
            (portfolio_dir / "assumption_overrides.json").write_text(
                json.dumps({"schemaVersion": "1.0", "assumptions": []}),
                encoding="utf-8",
            )
            rules = portfolio.default_rules()
            rules["signals"]["requiredMarginOfSafety"] = -0.20
            (portfolio_dir / "rules.json").write_text(json.dumps(rules), encoding="utf-8")

            with self.assertRaises(ValueError):
                portfolio.build_portfolio_signals(cwd=tmp, portfolio_dir=portfolio_dir)


def _write_research_fixture(tmp: str, price_date: str | None = None) -> Path:
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
                    "revenue": 1000.0,
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
        json.dumps([{"ticker": "ACME", "date": price_date or date.today().isoformat(), "close": 10.0, "adjustedClose": 10.0}]),
        encoding="utf-8",
    )
    (ticker_dir / "metrics" / "metrics.json").write_text(
        json.dumps(
            {
                "ticker": "ACME",
                "periods": [
                    {
                        "period": "2024-FY",
                        "revenue": 1000.0,
                        "revenue_growth_yoy": 0.11,
                        "operating_margin": 0.20,
                        "free_cash_flow": 130.0,
                        "fcf_margin": 0.13,
                        "fcf_conversion_from_net_income": 0.96,
                        "roic": 0.18,
                        "debt_to_equity": 0.3,
                        "weighted_average_diluted_shares": 100.0,
                        "cash_and_equivalents": 100.0,
                        "total_debt": 50.0,
                        "market_cap": 1000.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return research_root


def _base_fcff_assumptions() -> dict:
    return {
        "schemaVersion": "1.0",
        "ticker": "ACME",
        "companyName": "Acme Corp",
        "valuationDate": date.today().isoformat(),
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


def _valuation_result(ticker: str, scenario: str, fair_value: float, price_date: str) -> dict:
    return {
        "schemaVersion": "1.0",
        "ticker": ticker,
        "valuationDate": date.today().isoformat(),
        "scenario": scenario,
        "model": "fcff-dcf",
        "currency": "USD",
        "market": {"currentSharePrice": 10.0, "marketCap": 1000.0, "priceDate": price_date},
        "valuation": {
            "enterpriseValue": fair_value * 100,
            "equityValue": fair_value * 100,
            "fairValuePerShare": fair_value,
            "currentSharePrice": 10.0,
            "upsideDownside": (fair_value / 10.0) - 1,
            "marginOfSafety": (fair_value - 10.0) / fair_value,
            "requiredMarginOfSafety": 0.25,
            "meetsRequiredMarginOfSafety": True,
        },
        "drivers": {},
        "warnings": [],
    }


if __name__ == "__main__":
    unittest.main()
