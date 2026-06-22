import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from investor_toolkit.agent_harness import AgentHarness, AgentLlmResponse, TokenUsage
from investor_toolkit.audit import AuditLedger
from investor_toolkit.cli import main as cli_main
from tests.test_discovery_harness import _write_discovery_fixture


class AgentHarnessTests(unittest.TestCase):
    def test_agent_harness_persists_reviews_briefs_and_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            watchlist_before = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")
            holdings_before = (paths["portfolio_dir"] / "holdings.json").read_text(encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=ScriptedLlmClient(suggested_state="promote_candidate"),
            )

            result = harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="agent-run",
                include_default_screens=False,
            )

            review_path = paths["portfolio_dir"] / "agent_reviews" / "ACME.json"
            brief_path = paths["portfolio_dir"] / "agent_briefs" / "ACME.md"
            run_path = paths["portfolio_dir"] / "agent_runs" / "agent-run.json"
            run_exists = run_path.is_file()
            review_exists = review_path.is_file()
            brief_exists = brief_path.is_file()
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            candidate = queue["candidates"][0]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            run_log = json.loads(run_path.read_text(encoding="utf-8"))
            ledger_counts = AuditLedger(paths["portfolio_dir"] / "audit.db").table_counts()
            watchlist_after = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")
            holdings_after = (paths["portfolio_dir"] / "holdings.json").read_text(encoding="utf-8")

        self.assertTrue(run_exists)
        self.assertTrue(review_exists)
        self.assertTrue(brief_exists)
        self.assertEqual(result["tokenUsage"]["totalTokens"], 600)
        self.assertEqual(review["committeeChair"]["content"]["suggestedState"], "promote_candidate")
        self.assertEqual(candidate["agentSuggestedState"], "promote_candidate")
        self.assertEqual(run_log["promptVersion"], "institutional-pilot-v1")
        self.assertGreaterEqual(ledger_counts["runs"], 1)
        self.assertEqual(ledger_counts["agent_calls"], 5)
        self.assertGreaterEqual(ledger_counts["tool_calls"], 3)
        self.assertGreaterEqual(ledger_counts["claim_checks"], 5)
        self.assertGreaterEqual(ledger_counts["candidate_events"], 1)
        self.assertIn("agentPromotionRationale", candidate)
        self.assertEqual(watchlist_after, watchlist_before)
        self.assertEqual(holdings_after, holdings_before)

    def test_apply_agent_states_is_ignored_for_agent_authority_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=ScriptedLlmClient(suggested_state="reject"),
            )

            result = harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="agent-reject",
                include_default_screens=False,
                apply_agent_states=True,
            )
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            candidate = queue["candidates"][0]

        self.assertEqual(candidate["agentSuggestedState"], "reject")
        self.assertNotEqual(candidate["state"], "rejected")
        self.assertIn("applyAgentStates is ignored", "\n".join(result["warnings"]))

    def test_cli_agents_run_dry_run_writes_agent_artifacts_without_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "agents",
                        "run",
                        "--ticker",
                        "ACME",
                        "--provider",
                        "dry-run",
                        "--run-id",
                        "cli-agent",
                        "--no-default-screens",
                        "--portfolio-dir",
                        str(paths["portfolio_dir"]),
                        "--research-root",
                        str(paths["research_root"]),
                        "--valuations-dir",
                        str(paths["valuations_dir"]),
                    ]
                )

            run_path = paths["portfolio_dir"] / "agent_runs" / "cli-agent.json"
            run_exists = run_path.is_file()

        self.assertEqual(exit_code, 0)
        self.assertTrue(run_exists)
        self.assertIn("agents.run_discovery_research: ok", stdout.getvalue())
        self.assertIn("Provider/model: dry-run / dry-run", stdout.getvalue())


class ScriptedLlmClient:
    provider = "scripted"
    model = "scripted-model"

    def __init__(self, suggested_state: str) -> None:
        self.suggested_state = suggested_state

    def complete_json(self, *, agent_name, instructions, input_text, schema_hint):
        content = {
            "agent": agent_name,
            "verdict": "pass",
            "summary": f"{agent_name} summary",
            "claims": [
                {
                    "claimType": "numeric",
                    "statement": "Revenue growth was 22%.",
                    "sourcePath": "",
                    "uri": "investor://company/ACME/metrics-json",
                    "metric": "revenue_growth_yoy",
                    "value": 0.22,
                }
            ],
            "risks": ["fixture risk"],
            "missingEvidence": [],
            "nextActions": ["continue research"],
            "confidence": 0.7,
        }
        if agent_name == "committee_chair":
            content.update(
                {
                    "suggestedState": self.suggested_state,
                    "judgmentSummary": "Committee fixture summary.",
                    "promotionRationale": "Fixture promotion rationale.",
                    "claims": [
                        {
                            "claimType": "numeric",
                            "statement": "Base fair value per share is 20.",
                            "sourcePath": "",
                            "uri": "investor://valuation/ACME/ACME.base.fcff-dcf.result",
                            "metric": "fairValuePerShare",
                            "value": 20.0,
                        }
                    ],
                    "keyRisks": ["fixture risk"],
                }
            )
            content.pop("verdict", None)
            content.pop("summary", None)
            content.pop("risks", None)
        return AgentLlmResponse(
            content=content,
            rawText=json.dumps(content),
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(inputTokens=80, outputTokens=40, totalTokens=120),
        )


if __name__ == "__main__":
    unittest.main()
