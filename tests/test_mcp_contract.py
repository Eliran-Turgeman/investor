import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from investor_toolkit.app import AppContext, InvestorApplication
from investor_toolkit.cli import main as cli_main
from investor_toolkit.mcp_server import InvestorMcpServer


class McpContractTests(unittest.TestCase):
    def test_value_cli_mcp_and_service_match_for_acme_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            assumptions_path = Path(tmp) / "ACME.base.json"
            assumptions_path.write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")
            cli_stdout = io.StringIO()

            with redirect_stdout(cli_stdout):
                exit_code = cli_main(
                    [
                        "--research-root",
                        str(research_root),
                        "value",
                        "ACME",
                        "--assumptions",
                        str(assumptions_path),
                        "--format",
                        "json",
                    ]
                )

            server = _server(tmp, research_root=research_root)
            mcp_payload = _call_tool(
                server,
                "run_valuation",
                {"ticker": "ACME", "assumptionsPath": str(assumptions_path)},
            )
            service_payload = server.app.valuation.run("ACME", assumptions_path).to_dict()

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(cli_stdout.getvalue()), service_payload["data"])
        self.assertEqual(mcp_payload["data"], service_payload["data"])

    def test_validate_assumptions_cli_and_mcp_share_error_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            assumptions = _base_fcff_assumptions()
            assumptions["businessAssumptions"]["terminalGrowthRate"] = 0.20
            assumptions_path = Path(tmp) / "bad.json"
            assumptions_path.write_text(json.dumps(assumptions), encoding="utf-8")
            cli_stdout = io.StringIO()
            cli_stderr = io.StringIO()

            with redirect_stdout(cli_stdout), redirect_stderr(cli_stderr):
                exit_code = cli_main(
                    [
                        "--research-root",
                        str(research_root),
                        "assumptions",
                        "validate",
                        str(assumptions_path),
                    ]
                )

            mcp_payload = _call_tool(
                _server(tmp, research_root=research_root),
                "validate_assumptions",
                {"path": str(assumptions_path)},
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(mcp_payload["status"], "blocked")
        self.assertIn("terminalGrowthRate", "\n".join(mcp_payload["errors"]))
        self.assertIn("terminalGrowthRate", cli_stdout.getvalue())

    def test_portfolio_value_cli_and_mcp_share_audit_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_portfolio_fixture(tmp)
            cli_stdout = io.StringIO()

            with redirect_stdout(cli_stdout):
                exit_code = cli_main(
                    [
                        "--research-root",
                        str(paths["research_root"]),
                        "portfolio",
                        "value",
                        "--portfolio-dir",
                        str(paths["portfolio_dir"]),
                        "--assumptions-dir",
                        str(paths["assumptions_dir"]),
                        "--valuations-dir",
                        str(paths["valuations_dir"]),
                    ]
                )

            cli_audit = json.loads((paths["portfolio_dir"] / "valuation_audit.json").read_text(encoding="utf-8"))
            mcp_payload = _call_tool(
                _server(
                    tmp,
                    research_root=paths["research_root"],
                    portfolio_dir=paths["portfolio_dir"],
                    assumptions_dir=paths["assumptions_dir"],
                    valuations_dir=paths["valuations_dir"],
                ),
                "run_portfolio_valuations",
                {},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(mcp_payload["data"]["valuedCount"], cli_audit["valuedCount"])
        self.assertEqual(mcp_payload["data"]["errors"], cli_audit["errors"])
        self.assertEqual(
            [(row["ticker"], row["status"]) for row in mcp_payload["data"]["rows"]],
            [(row["ticker"], row["status"]) for row in cli_audit["rows"]],
        )

    def test_mcp_lists_and_reads_existing_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            server = _server(tmp, research_root=research_root)

            resources = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
            metrics_resource = next(item for item in resources if item["uri"] == "investor://company/ACME/metrics-json")
            read_result = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": metrics_resource["uri"]},
                }
            )["result"]

        self.assertEqual(metrics_resource["mimeType"], "application/json")
        self.assertIn('"ticker": "ACME"', read_result["contents"][0]["text"])

    def test_mcp_initialize_exposes_tools_resources_and_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(tmp)
            init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
            prompts = server.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})["result"]["prompts"]

        self.assertIn("tools", init["capabilities"])
        self.assertIn("resources", init["capabilities"])
        self.assertIn("prompts", init["capabilities"])
        self.assertIn("run_valuation", {tool["name"] for tool in tools})
        self.assertIn("portfolio_review", {prompt["name"] for prompt in prompts})


def _server(
    workspace_root: str | Path,
    research_root: str | Path | None = None,
    portfolio_dir: str | Path | None = None,
    assumptions_dir: str | Path | None = None,
    valuations_dir: str | Path | None = None,
) -> InvestorMcpServer:
    context = AppContext.from_env(
        cwd=workspace_root,
        research_root=research_root,
        portfolio_dir=portfolio_dir,
        assumptions_dir=assumptions_dir,
        valuations_dir=valuations_dir,
    )
    return InvestorMcpServer(InvestorApplication(context))


def _call_tool(server: InvestorMcpServer, name: str, arguments: dict) -> dict:
    result = server.call_tool(name, arguments)
    if result.get("isError"):
        raise AssertionError(result)
    return result["structuredContent"]


def _write_portfolio_fixture(tmp: str) -> dict[str, Path]:
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
    (portfolio_dir / "watchlist.json").write_text(
        json.dumps({"schemaVersion": "1.0", "watchlist": []}),
        encoding="utf-8",
    )
    (portfolio_dir / "assumption_overrides.json").write_text(
        json.dumps({"schemaVersion": "1.0", "assumptions": []}),
        encoding="utf-8",
    )
    (portfolio_dir / "rules.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
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
        ),
        encoding="utf-8",
    )
    (assumptions_dir / "ACME.base.json").write_text(json.dumps(_base_fcff_assumptions()), encoding="utf-8")
    return {
        "research_root": research_root,
        "portfolio_dir": portfolio_dir,
        "assumptions_dir": assumptions_dir,
        "valuations_dir": valuations_dir,
    }


def _write_research_fixture(tmp: str) -> Path:
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
        json.dumps([{"ticker": "ACME", "date": date.today().isoformat(), "close": 10.0, "adjustedClose": 10.0}]),
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


if __name__ == "__main__":
    unittest.main()
