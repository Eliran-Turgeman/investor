import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from investor_toolkit.cli import main as cli_main
from investor_toolkit.discovery import DiscoveryHarness, SchemaValidationError, validate_discovery_artifact


class DiscoveryHarnessTests(unittest.TestCase):
    def test_discover_score_brief_reject_and_suppressed_rediscovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                today=date.today(),
            )

            first = harness.discover(tickers=["acme"], include_default_screens=False, run_id="manual-run")
            score = harness.score("ACME")
            brief = harness.brief("ACME")
            rejected = harness.reject("ACME", "Outside current research priority.")
            second = harness.discover(tickers=["ACME"], include_default_screens=False, run_id="manual-run")
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            candidate = queue["candidates"][0]
            brief_exists = Path(brief["briefPath"]).is_file()
            brief_text = Path(brief["briefPath"]).read_text(encoding="utf-8")
            rejection_exists = Path(rejected["rejectionPath"]).is_file()
            first_run_exists = (paths["portfolio_dir"] / "discovery_runs" / "manual-run.json").is_file()
            second_run_exists = (paths["portfolio_dir"] / "discovery_runs" / "manual-run-2.json").is_file()

        self.assertEqual(first["discovered"], ["ACME"])
        self.assertEqual(score["candidate"]["state"], "promote_candidate")
        self.assertTrue(score["candidate"]["watchlistPromotionCandidate"])
        self.assertTrue(brief_exists)
        self.assertIn("## Source Facts", brief_text)
        self.assertIn("## Watchlist Promotion Assessment", brief_text)
        self.assertEqual(candidate["state"], "rejected")
        self.assertEqual(second["suppressed"], ["ACME"])
        self.assertTrue(rejection_exists)
        self.assertTrue(first_run_exists)
        self.assertTrue(second_run_exists)

    def test_schema_validation_rejects_invalid_candidates_artifact(self):
        with self.assertRaises(SchemaValidationError):
            validate_discovery_artifact("candidates", {"schemaVersion": "1.0", "candidates": []})

        valid = {
            "schemaVersion": "1.0",
            "generatedAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "candidates": [],
        }
        validate_discovery_artifact("candidates", valid)

    def test_scoring_uses_component_scores_and_local_source_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                today=date.today(),
            )
            harness.discover(tickers=["ACME"], include_default_screens=False)
            result = harness.score("ACME")
            candidate = result["candidate"]

        self.assertEqual(set(candidate["componentScores"]), set(_score_keys()))
        self.assertGreaterEqual(candidate["componentScores"]["business_quality"], 80)
        self.assertGreaterEqual(candidate["componentScores"]["profile_fit"], 70)
        self.assertGreater(candidate["totalScore"], 70)
        fact_labels = {fact["label"] for fact in candidate["sourceFacts"]}
        self.assertIn("Revenue growth YoY", fact_labels)
        self.assertIn("base fair value per share", fact_labels)
        self.assertTrue(any("metrics.json" in fact["sourcePath"] for fact in candidate["sourceFacts"]))
        self.assertEqual(candidate["missingEvidence"], [])

    def test_refresh_uses_injected_runner_and_records_warnings(self):
        calls = []

        def fake_runner(ticker, offline, refresh):
            calls.append((ticker, offline, refresh))
            return {"messages": ["mock refresh"], "warnings": ["mock warning"]}

        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                research_runner=fake_runner,
            )
            result = harness.refresh("acme", offline=True, refresh=False)
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            candidate = queue["candidates"][0]

        self.assertEqual(calls, [("ACME", True, False)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(candidate["state"], "refreshed")
        self.assertIn("mock warning", candidate["warnings"])

    def test_promote_requires_approval_and_never_mutates_holdings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            holdings_path = paths["portfolio_dir"] / "holdings.json"
            watchlist_path = paths["portfolio_dir"] / "watchlist.json"
            holdings_before = holdings_path.read_text(encoding="utf-8")
            watchlist_before = watchlist_path.read_text(encoding="utf-8")
            harness = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            harness.discover(tickers=["ACME"], include_default_screens=False)
            harness.score("ACME")

            with self.assertRaises(ValueError):
                harness.promote("ACME", approved=False)

            self.assertEqual(holdings_path.read_text(encoding="utf-8"), holdings_before)
            self.assertEqual(watchlist_path.read_text(encoding="utf-8"), watchlist_before)
            with self.assertRaisesRegex(ValueError, "analyst_approved"):
                harness.promote("ACME", approved=True)
            holdings_after = holdings_path.read_text(encoding="utf-8")
            watchlist_after = watchlist_path.read_text(encoding="utf-8")

        self.assertEqual(holdings_after, holdings_before)
        self.assertEqual(watchlist_after, watchlist_before)

    def test_cli_discovery_discover_writes_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "discovery",
                        "discover",
                        "--ticker",
                        "acme",
                        "--no-default-screens",
                        "--portfolio-dir",
                        str(portfolio_dir),
                        "--run-id",
                        "cli-run",
                    ]
                )
            candidates = json.loads((portfolio_dir / "candidates.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(candidates["candidates"][0]["ticker"], "ACME")
        self.assertIn("discovery.discover: ok", stdout.getvalue())


def _write_discovery_fixture(tmp: str) -> dict[str, Path]:
    root = Path(tmp)
    portfolio_dir = root / "portfolio"
    research_root = root / "research"
    valuations_dir = root / "valuations"
    ticker_dir = research_root / "ACME"
    portfolio_dir.mkdir()
    valuations_dir.mkdir()
    (ticker_dir / "metrics").mkdir(parents=True)
    (ticker_dir / "data").mkdir()
    (ticker_dir / "filings" / "metadata").mkdir(parents=True)
    (ticker_dir / "extracted" / "2026-10K").mkdir(parents=True)
    (portfolio_dir / "holdings.json").write_text(
        json.dumps({"schemaVersion": "1.0", "holdings": [{"ticker": "BETA", "shares": 1}]}),
        encoding="utf-8",
    )
    (portfolio_dir / "watchlist.json").write_text(
        json.dumps({"schemaVersion": "1.0", "watchlist": [{"ticker": "BETA", "priority": "review"}]}),
        encoding="utf-8",
    )
    (portfolio_dir / "rules.json").write_text(
        json.dumps({"schemaVersion": "1.0", "signals": {"stalePriceDays": 10}}),
        encoding="utf-8",
    )
    (ticker_dir / "company.json").write_text(
        json.dumps({"ticker": "ACME", "name": "Acme Cloud Security", "sector": "Technology", "industry": "Software"}),
        encoding="utf-8",
    )
    (ticker_dir / "metrics" / "metrics.json").write_text(
        json.dumps(
            {
                "ticker": "ACME",
                "periods": [
                    {
                        "period": "2026-FY",
                        "revenue_growth_yoy": 0.22,
                        "operating_margin": 0.25,
                        "free_cash_flow": 220.0,
                        "fcf_margin": 0.22,
                        "fcf_conversion_from_net_income": 1.1,
                        "roic": 0.24,
                        "debt_to_equity": 0.2,
                        "interest_coverage": 12.0,
                        "cash_and_equivalents": 500.0,
                        "total_debt": 100.0,
                        "sbc_percent_revenue": 0.04,
                        "share_count_change": 0.01,
                        "price_to_free_cash_flow": 20.0,
                        "price_to_earnings": 28.0,
                        "ev_to_revenue": 8.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ticker_dir / "data" / "prices.json").write_text(
        json.dumps([{"ticker": "ACME", "date": date.today().isoformat(), "close": 10.0, "adjustedClose": 10.0}]),
        encoding="utf-8",
    )
    (ticker_dir / "filings" / "metadata" / "filings.json").write_text(
        json.dumps(
            [
                {
                    "ticker": "ACME",
                    "formType": "10-K",
                    "filingDate": date.today().isoformat(),
                    "accessionNumber": "0000000000-26-000001",
                }
            ]
        ),
        encoding="utf-8",
    )
    (ticker_dir / "extracted" / "2026-10K" / "business.md").write_text(
        "Acme provides enterprise software, cloud cybersecurity, AI infrastructure, and data center observability.",
        encoding="utf-8",
    )
    (ticker_dir / "extracted" / "2026-10K" / "risk-factors.md").write_text(
        "The company faces competition from larger cloud security and software vendors.",
        encoding="utf-8",
    )
    (valuations_dir / "ACME.base.fcff-dcf.result.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "ticker": "ACME",
                "scenario": "base",
                "model": "fcff-dcf",
                "market": {"priceDate": date.today().isoformat()},
                "valuation": {
                    "fairValuePerShare": 20.0,
                    "currentSharePrice": 10.0,
                    "marginOfSafety": 0.5,
                },
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    return {"portfolio_dir": portfolio_dir, "research_root": research_root, "valuations_dir": valuations_dir}


def _score_keys():
    return [
        "profile_fit",
        "business_quality",
        "growth_runway",
        "valuation_sanity",
        "balance_sheet",
        "downside_risk",
        "evidence_freshness",
        "portfolio_fit",
    ]


if __name__ == "__main__":
    unittest.main()
