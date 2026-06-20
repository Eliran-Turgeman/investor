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

    def test_mcp_profile_status_requires_onboarding_when_profile_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(tmp)

            resources = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
            status_resource = next(item for item in resources if item["uri"] == "investor://profile/status")
            read_result = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": "investor://profile/status"},
                }
            )["result"]
            status_from_resource = json.loads(read_result["contents"][0]["text"])
            status_from_tool = _call_tool(server, "get_profile_status", {})
            portfolio_context = _call_tool(server, "get_portfolio_context", {})

        self.assertEqual(status_resource["mimeType"], "application/json")
        self.assertTrue(status_from_resource["onboardingRequired"])
        self.assertFalse(status_from_resource["profileExists"])
        self.assertIn("investor://profile/policy", {item["uri"] for item in status_from_resource["missingProfileArtifacts"]})
        self.assertTrue(status_from_tool["data"]["onboardingRequired"])
        self.assertTrue(portfolio_context["data"]["profileStatus"]["onboardingRequired"])
        self.assertIn("init_investor_profile", "\n".join(portfolio_context["nextActions"]))

    def test_mcp_lists_and_reads_profile_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            server = _server(tmp, portfolio_dir=portfolio_dir)
            _call_tool(server, "init_investor_profile", {})

            resources = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
            policy_resource = next(item for item in resources if item["uri"] == "investor://profile/policy")
            goals_resource = next(item for item in resources if item["uri"] == "investor://profile/goals")
            read_result = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": goals_resource["uri"]},
                }
            )["result"]
            status_result = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "resources/read",
                    "params": {"uri": "investor://profile/status"},
                }
            )["result"]
            status = json.loads(status_result["contents"][0]["text"])

        self.assertEqual(policy_resource["mimeType"], "text/markdown")
        self.assertEqual(goals_resource["mimeType"], "application/json")
        self.assertIn('"primaryObjective": "outperform_sp500"', read_result["contents"][0]["text"])
        self.assertFalse(status["onboardingRequired"])
        self.assertTrue(status["profileExists"])

    def test_portfolio_context_surfaces_existing_profile_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            server = _server(tmp, portfolio_dir=portfolio_dir)
            _call_tool(server, "init_investor_profile", {})

            context = _call_tool(server, "get_portfolio_context", {})

        artifact_uris = {item["uri"] for item in context["artifacts"]}
        profile_artifact_uris = {item["uri"] for item in context["data"]["profileArtifacts"]}
        self.assertFalse(context["data"]["profileStatus"]["onboardingRequired"])
        self.assertIn("investor://profile/preferences", artifact_uris)
        self.assertIn("investor://profile/operating-preferences", artifact_uris)
        self.assertIn("investor://profile/preferences", profile_artifact_uris)
        self.assertIn(str((portfolio_dir / "preferences.json").resolve()), context["sourcePaths"])

    def test_profile_onboarding_cli_and_mcp_write_equivalent_policy_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli_dir = Path(tmp) / "cli" / "portfolio"
            mcp_dir = Path(tmp) / "mcp" / "portfolio"
            cli_stdout = io.StringIO()

            with redirect_stdout(cli_stdout):
                exit_code = cli_main(
                    [
                        "onboarding",
                        "init",
                        "--portfolio-dir",
                        str(cli_dir),
                        "--benchmark",
                        "S&P 500",
                        "--horizon",
                        "5-10",
                        "--ideas-per-month",
                        "3",
                        "--margin-of-safety",
                        "30%",
                        "--max-position-size",
                        "30%",
                        "--focus",
                        "software",
                        "--focus",
                        "ai_related_hardware_or_hardware_adjacent_businesses",
                        "--external-exposure",
                        "MSFT:50000:USD:RSU",
                        "--external-exposure",
                        "PANW:75000:USD:RSU",
                        "--other-portfolio",
                        "other_personal_portfolio:250000:NIS",
                    ]
                )

            mcp_payload = _call_tool(
                _server(Path(tmp) / "mcp", portfolio_dir=mcp_dir),
                "init_investor_profile",
                {
                    "benchmark": "S&P 500",
                    "horizonMinYears": 5,
                    "horizonMaxYears": 10,
                    "ideasPerMonth": 3,
                    "requiredMarginOfSafety": 0.30,
                    "maxPositionSize": 0.30,
                    "focusAreas": ["software", "ai_related_hardware_or_hardware_adjacent_businesses"],
                    "externalExposures": [
                        {"ticker": "MSFT", "amount": 50000, "currency": "USD", "type": "RSU"},
                        {"ticker": "PANW", "amount": 75000, "currency": "USD", "type": "RSU"},
                    ],
                    "otherPortfolios": [
                        {"name": "other_personal_portfolio", "amount": 250000, "currency": "NIS"},
                    ],
                },
            )

            cli_goals = json.loads((cli_dir / "goals.json").read_text(encoding="utf-8"))
            mcp_goals = json.loads((mcp_dir / "goals.json").read_text(encoding="utf-8"))
            cli_external = json.loads((cli_dir / "external_exposure.json").read_text(encoding="utf-8"))
            mcp_external = json.loads((mcp_dir / "external_exposure.json").read_text(encoding="utf-8"))
            cli_operating = json.loads((cli_dir / "operating_preferences.json").read_text(encoding="utf-8"))
            mcp_operating = json.loads((mcp_dir / "operating_preferences.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(cli_goals, mcp_goals)
        self.assertEqual(cli_external, mcp_external)
        self.assertEqual(cli_operating, mcp_operating)
        self.assertEqual(mcp_payload["data"]["writtenCount"], 15)

    def test_mcp_initialize_exposes_tools_resources_and_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(tmp)
            init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
            prompts = server.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})["result"]["prompts"]
            templates = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/templates/list"})["result"][
                "resourceTemplates"
            ]
            onboarding_prompt = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "prompts/get",
                    "params": {"name": "investor_onboarding"},
                }
            )["result"]

        self.assertIn("tools", init["capabilities"])
        self.assertIn("resources", init["capabilities"])
        self.assertIn("prompts", init["capabilities"])
        self.assertIn("run_valuation", {tool["name"] for tool in tools})
        self.assertIn("get_profile_status", {tool["name"] for tool in tools})
        self.assertIn("init_investor_profile", {tool["name"] for tool in tools})
        self.assertIn("investor_onboarding", {prompt["name"] for prompt in prompts})
        self.assertIn("portfolio_review", {prompt["name"] for prompt in prompts})
        self.assertIn("investor://profile/{artifact}", {template["uriTemplate"] for template in templates})
        self.assertIn("get_profile_status", onboarding_prompt["messages"][0]["content"]["text"])

    def test_mcp_path_arguments_cannot_escape_configured_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside_json = Path(tmp) / "outside.json"
            outside_xlsx = Path(tmp) / "outside.xlsx"
            server = _server(workspace)

            cases = [
                (
                    "init_valuation_assumptions",
                    {"ticker": "ACME", "model": "fcff-dcf", "outputPath": str(outside_json)},
                    outside_json,
                ),
                ("validate_assumptions", {"path": str(outside_json)}, None),
                ("run_valuation", {"ticker": "ACME", "assumptionsPath": str(outside_json)}, None),
                (
                    "run_valuation",
                    {"ticker": "ACME", "assumptionsPath": "assumptions/ACME.base.json", "outputPath": str(outside_json)},
                    outside_json,
                ),
                (
                    "compare_valuation_scenarios",
                    {"ticker": "ACME", "assumptionsPaths": ["assumptions/ACME.base.json", str(outside_json)]},
                    None,
                ),
                ("build_portfolio_signals", {"workbookPath": str(outside_xlsx)}, outside_xlsx),
            ]

            for tool_name, args, forbidden_output in cases:
                with self.subTest(tool=tool_name):
                    result = server.call_tool(tool_name, args)
                    self.assertTrue(result["isError"], result)
                    self.assertIn("escapes the MCP workspace/configured roots", result["content"][0]["text"])
                    if forbidden_output is not None:
                        self.assertFalse(forbidden_output.exists())

    def test_mcp_path_arguments_allow_workspace_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_root = _write_research_fixture(tmp)
            output_path = Path(tmp) / "assumptions" / "ACME.base.json"
            payload = _call_tool(
                _server(tmp, research_root=research_root),
                "init_valuation_assumptions",
                {"ticker": "ACME", "model": "fcff-dcf", "outputPath": "assumptions/ACME.base.json"},
            )

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["data"]["ticker"], "ACME")
            self.assertTrue(output_path.is_file())

    def test_mcp_internal_errors_do_not_expose_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(tmp)

            def raise_internal_error(method, params):
                raise RuntimeError("sensitive implementation detail")

            server._dispatch = raise_internal_error
            response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            encoded = json.dumps(response)

        self.assertEqual(response["error"]["message"], "Internal MCP server error.")
        self.assertNotIn("traceback", encoded.lower())
        self.assertNotIn("sensitive implementation detail", encoded)

    def test_mcp_tool_errors_do_not_expose_exception_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = _server(tmp)

            def raise_tool_error(args):
                raise RuntimeError("sensitive tool detail")

            server.tools["get_profile_status"].handler = raise_tool_error
            result = server.call_tool("get_profile_status", {})
            encoded = json.dumps(result)

        self.assertTrue(result["isError"], result)
        self.assertEqual(result["content"][0]["text"], "Tool execution failed.")
        self.assertNotIn("exception", encoded.lower())
        self.assertNotIn("traceback", encoded.lower())
        self.assertNotIn("RuntimeError", encoded)
        self.assertNotIn("sensitive tool detail", encoded)

    def test_mcp_tool_annotations_reflect_writes_and_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = _server(tmp).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
            by_name = {tool["name"]: tool["annotations"] for tool in tools}

        mutating_tools = {
            "init_investor_profile",
            "refresh_company_research",
            "init_valuation_assumptions",
            "run_valuation",
            "run_portfolio_valuations",
            "build_portfolio_signals",
        }
        read_only_tools = {
            "get_portfolio_context",
            "get_profile_status",
            "list_company_artifacts",
            "validate_assumptions",
            "compare_valuation_scenarios",
        }
        for tool_name in mutating_tools:
            with self.subTest(tool=tool_name):
                self.assertFalse(by_name[tool_name]["readOnlyHint"])
                self.assertTrue(by_name[tool_name]["destructiveHint"])
        for tool_name in read_only_tools:
            with self.subTest(tool=tool_name):
                self.assertTrue(by_name[tool_name]["readOnlyHint"])
                self.assertFalse(by_name[tool_name]["destructiveHint"])
                self.assertFalse(by_name[tool_name]["openWorldHint"])
        self.assertTrue(by_name["refresh_company_research"]["openWorldHint"])


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
