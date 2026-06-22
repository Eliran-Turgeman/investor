import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from investor_toolkit.agent_harness import AgentHarness, AgentLlmResponse, TokenUsage
from investor_toolkit.agent_harness.approvals import approve_candidate
from investor_toolkit.agent_harness.claim_verifier import verify_review_claims
from investor_toolkit.audit import AuditLedger
from investor_toolkit.data_import import import_vendor_drop
from investor_toolkit.discovery import DiscoveryHarness
from investor_toolkit.evals import run_eval_suite
from tests.test_discovery_harness import _write_discovery_fixture


class InstitutionalAgentHarnessTests(unittest.TestCase):
    def test_invalid_structured_output_blocks_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=InvalidLlmClient(),
            )

            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="invalid-schema",
                include_default_screens=False,
            )

            review = json.loads((paths["portfolio_dir"] / "agent_reviews" / "ACME.json").read_text(encoding="utf-8"))
            with closing(sqlite3.connect(paths["portfolio_dir"] / "audit.db")) as conn:
                statuses = [row[0] for row in conn.execute("select status from agent_calls order by id").fetchall()]

        self.assertTrue(statuses)
        self.assertEqual(set(statuses), {"blocked"})
        self.assertTrue(all(output["content"]["verdict"] == "block" for output in review["roleOutputs"]))
        self.assertEqual(review["committeeChair"]["content"]["suggestedState"], "research_more")
        self.assertIn("business_quality", "\n".join(review["committeeChair"]["content"]["missingEvidence"]))

    def test_unsupported_claims_and_stale_data_are_explicit_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            discovery.discover(tickers=["ACME"], include_default_screens=False)
            candidate = discovery.score("ACME")["candidate"]
            candidate["warnings"] = ["ACME: stale price data (42 days old)"]
            review = {
                "ticker": "ACME",
                "deterministicCandidate": candidate,
                "roleOutputs": [
                    {
                        "agent": "business_quality",
                        "content": {
                            "claims": [
                                {
                                    "claimType": "factual",
                                    "statement": "ACME has an uncited moat claim.",
                                    "sourcePath": "",
                                    "uri": "",
                                },
                                {
                                    "claimType": "numeric",
                                    "statement": "Revenue growth was 999%.",
                                    "sourcePath": "",
                                    "uri": "investor://company/ACME/metrics-json",
                                    "metric": "revenue_growth_yoy",
                                    "value": 9.99,
                                },
                            ]
                        },
                    }
                ],
                "committeeChair": {"content": {"claims": []}},
            }

            summary = verify_review_claims(review, cwd=tmp)
            reasons = "\n".join(check["reason"] for check in summary["checks"])

        self.assertEqual(summary["unsupportedCount"], 3)
        self.assertEqual(summary["numericUnsupportedCount"], 1)
        self.assertIn("missing source citation", reasons)
        self.assertIn("numeric value not found", reasons)
        self.assertIn("deterministic data warning blocks", reasons)

    def test_numeric_claim_value_elsewhere_does_not_pass_wrong_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            discovery.discover(tickers=["ACME"], include_default_screens=False)
            candidate = discovery.score("ACME")["candidate"]
            review = {
                "ticker": "ACME",
                "deterministicCandidate": candidate,
                "roleOutputs": [
                    {
                        "agent": "business_quality",
                        "content": {
                            "claims": [
                                {
                                    "claimType": "numeric",
                                    "statement": "Revenue growth was 20.",
                                    "sourcePath": "",
                                    "uri": "investor://company/ACME/metrics-json",
                                    "metric": "revenue_growth_yoy",
                                    "value": 20.0,
                                }
                            ]
                        },
                    }
                ],
                "committeeChair": {"content": {"claims": []}},
            }

            summary = verify_review_claims(review, cwd=tmp)
            check = summary["checks"][0]

        self.assertEqual(summary["numericUnsupportedCount"], 1)
        self.assertEqual(check["status"], "metric_mismatch")
        self.assertEqual(check["reasonCode"], "metric_mismatch")
        self.assertIn("numeric value not found for cited metric", check["reason"])
        self.assertTrue(check["sourceExists"])

    def test_unsupported_promotion_claims_block_agent_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            watchlist_before = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=UnsupportedPromotionLlmClient(),
            )

            result = harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="unsupported-promotion",
                include_default_screens=False,
            )
            review = json.loads((paths["portfolio_dir"] / "agent_reviews" / "ACME.json").read_text(encoding="utf-8"))
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            watchlist_after = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")

        self.assertGreater(review["claimVerification"]["numericUnsupportedCount"], 0)
        self.assertEqual(review["committeeChair"]["content"]["suggestedState"], "research_more")
        self.assertEqual(queue["candidates"][0]["agentSuggestedState"], "research_more")
        self.assertIn("promotion blocked by claim verification", "\n".join(result["warnings"]))
        self.assertEqual(watchlist_after, watchlist_before)

    def test_analyst_approval_records_state_without_watchlist_or_holdings_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            holdings_before = (paths["portfolio_dir"] / "holdings.json").read_text(encoding="utf-8")
            watchlist_before = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="approval-source",
                include_default_screens=False,
            )

            approval = approve_candidate(
                ticker="ACME",
                state="analyst_approved",
                reason="Approved for explicit watchlist-promotion review.",
                reviewer="analyst-a",
                portfolio_dir=paths["portfolio_dir"],
                cwd=tmp,
            )
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))
            holdings_after = (paths["portfolio_dir"] / "holdings.json").read_text(encoding="utf-8")
            watchlist_after = (paths["portfolio_dir"] / "watchlist.json").read_text(encoding="utf-8")
            counts = AuditLedger(paths["portfolio_dir"] / "audit.db").table_counts()
            review_exists = Path(approval["agentReviewPath"]).is_file()
            brief_exists = Path(approval["agentBriefPath"]).is_file()
            approvals_dir_exists = (paths["portfolio_dir"] / "approvals").exists()

        self.assertTrue(review_exists)
        self.assertTrue(brief_exists)
        self.assertTrue(approvals_dir_exists)
        self.assertEqual(queue["candidates"][0]["state"], "analyst_approved")
        self.assertEqual(holdings_after, holdings_before)
        self.assertEqual(watchlist_after, watchlist_before)
        self.assertEqual(counts["approvals"], 1)
        self.assertGreaterEqual(counts["candidate_events"], 2)

    def test_analyst_approval_requires_review_brief_and_clean_claim_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            discovery.discover(tickers=["ACME"], include_default_screens=False)
            discovery.score("ACME")
            with self.assertRaises(FileNotFoundError):
                approve_candidate(
                    ticker="ACME",
                    state="analyst_approved",
                    reason="Cannot approve without review artifacts.",
                    reviewer="analyst-a",
                    portfolio_dir=paths["portfolio_dir"],
                    cwd=tmp,
                )

            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=UnsupportedPromotionLlmClient(),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="dirty-approval-source",
                include_default_screens=False,
            )
            with self.assertRaisesRegex(ValueError, "unsupportedCount==0"):
                approve_candidate(
                    ticker="ACME",
                    state="analyst_approved",
                    reason="Cannot approve unsupported claims.",
                    reviewer="analyst-a",
                    portfolio_dir=paths["portfolio_dir"],
                    cwd=tmp,
                )
            queue = json.loads((paths["portfolio_dir"] / "candidates.json").read_text(encoding="utf-8"))

        self.assertNotEqual(queue["candidates"][0]["state"], "analyst_approved")

    def test_discovery_promote_requires_current_clean_analyst_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            watchlist_path = paths["portfolio_dir"] / "watchlist.json"
            watchlist_before = watchlist_path.read_text(encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="promotion-gate-source",
                include_default_screens=False,
            )
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            with self.assertRaisesRegex(ValueError, "analyst_approved"):
                discovery.promote("ACME", approved=True)
            self.assertEqual(watchlist_path.read_text(encoding="utf-8"), watchlist_before)

            approval = approve_candidate(
                ticker="ACME",
                state="analyst_approved",
                reason="Approved for explicit watchlist-promotion review.",
                reviewer="analyst-a",
                portfolio_dir=paths["portfolio_dir"],
                cwd=tmp,
            )
            promoted = discovery.promote("ACME", approved=True)
            watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))

        self.assertEqual(promoted["approvalPath"], approval["approvalPath"])
        self.assertIn("ACME", {item["ticker"] for item in watchlist["watchlist"]})

    def test_discovery_promote_rejects_stale_approval_source_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            watchlist_path = paths["portfolio_dir"] / "watchlist.json"
            watchlist_before = watchlist_path.read_text(encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="stale-approval-source",
                include_default_screens=False,
            )
            approve_candidate(
                ticker="ACME",
                state="analyst_approved",
                reason="Approved for explicit watchlist-promotion review.",
                reviewer="analyst-a",
                portfolio_dir=paths["portfolio_dir"],
                cwd=tmp,
            )
            review_path = paths["portfolio_dir"] / "agent_reviews" / "ACME.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["tamper"] = True
            review_path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )

            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                discovery.promote("ACME", approved=True)
            watchlist_after = watchlist_path.read_text(encoding="utf-8")

        self.assertEqual(watchlist_after, watchlist_before)

    def test_casual_agent_selection_and_promotion_proposals_skip_blocked_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            discovery = DiscoveryHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
            )
            discovery.discover(tickers=["ACME", "BETA", "GAMMA", "DELTA"], include_default_screens=False)
            queue_path = paths["portfolio_dir"] / "candidates.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            states = {
                "ACME": "deferred",
                "BETA": "analyst_rejected",
                "GAMMA": "needs_more_evidence",
                "DELTA": "screened",
            }
            for candidate in queue["candidates"]:
                candidate["state"] = states[candidate["ticker"]]
                candidate["totalScore"] = 90
                candidate["watchlistPromotionCandidate"] = True
            queue_path.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )

            selected = harness._select_candidates([], limit=10)
            proposed = discovery.propose_promotions(limit=10)["rows"]

        self.assertEqual(selected, ["DELTA"])
        self.assertEqual([row["ticker"] for row in proposed], ["DELTA"])

    def test_vendor_csv_import_validates_contract_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio = root / "portfolio"
            good_csv = root / "fundamentals.csv"
            good_csv.write_text(
                "ticker,period,metric,value,currency,unit,provider,restated\n"
                "acme,2026-FY,revenue,100,USD,millions,TestVendor,true\n",
                encoding="utf-8",
            )
            bad_csv = root / "bad_fundamentals.csv"
            bad_csv.write_text(
                "ticker,period,metric,currency,unit,provider\n"
                "acme,2026-FY,revenue,USD,millions,TestVendor\n",
                encoding="utf-8",
            )

            good = import_vendor_drop(
                kind="fundamentals",
                path=good_csv,
                provider="TestVendor",
                cwd=root,
                output_root="data_imports",
                run_id="good-import",
                portfolio_dir=portfolio,
            )
            bad = import_vendor_drop(
                kind="fundamentals",
                path=bad_csv,
                provider="TestVendor",
                cwd=root,
                output_root="data_imports",
                run_id="bad-import",
                portfolio_dir=portfolio,
            )
            normalized_exists = Path(good["normalizedPath"]).is_file()

        self.assertEqual(good["status"], "ok")
        self.assertTrue(normalized_exists)
        self.assertIn("restated data flagged", "\n".join(good["warnings"]))
        self.assertEqual(bad["status"], "blocked")
        self.assertIn("missing required column(s): value", "\n".join(bad["errors"]))
        self.assertEqual(bad["normalizedPath"], "")

    def test_gold_eval_runner_computes_metrics_and_flags_known_reject_resurfacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="eval-source",
                include_default_screens=False,
            )
            evals_dir = Path(tmp) / "evals"
            evals_dir.mkdir()
            (evals_dir / "gold_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "ticker": "ACME",
                        "expectedState": "promote_candidate",
                        "requiredEvidence": ["Base fair value"],
                        "knownReject": False,
                        "analystAccepted": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (evals_dir / "known_rejects.jsonl").write_text(
                json.dumps(
                    {
                        "ticker": "ACME",
                        "expectedState": "reject",
                        "requiredEvidence": [],
                        "knownReject": True,
                        "analystAccepted": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            ok = run_eval_suite(suite="gold_candidates", cwd=tmp, portfolio_dir=paths["portfolio_dir"], run_id="eval-ok")
            fail = run_eval_suite(suite="known_rejects", cwd=tmp, portfolio_dir=paths["portfolio_dir"], run_id="eval-fail")

        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["metrics"]["schemaValidRate"], 1)
        self.assertEqual(ok["metrics"]["unsupportedNumericClaimRate"], 0)
        self.assertGreaterEqual(ok["metrics"]["citationCoverage"], 0.95)
        self.assertEqual(fail["status"], "fail")
        self.assertEqual(fail["metrics"]["rejectedIdeaResurfacingRate"], 1)

    def test_gold_eval_runner_fails_when_required_evidence_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_discovery_fixture(tmp)
            harness = AgentHarness(
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                research_root=paths["research_root"],
                valuations_dir=paths["valuations_dir"],
                llm_client=SupportedLlmClient(suggested_state="promote_candidate"),
            )
            harness.run_discovery_research(
                tickers=["ACME"],
                limit=1,
                run_id="eval-missing-evidence-source",
                include_default_screens=False,
            )
            evals_dir = Path(tmp) / "evals"
            evals_dir.mkdir()
            (evals_dir / "missing_evidence.jsonl").write_text(
                json.dumps(
                    {
                        "ticker": "ACME",
                        "expectedState": "promote_candidate",
                        "requiredEvidence": ["customer concentration evidence that is absent"],
                        "knownReject": False,
                        "analystAccepted": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_eval_suite(
                suite="missing_evidence",
                cwd=tmp,
                portfolio_dir=paths["portfolio_dir"],
                run_id="eval-missing-evidence",
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["metrics"]["failedRowCount"], 1)
        self.assertEqual(result["metrics"]["missingRequiredEvidenceCount"], 1)
        self.assertEqual(result["rows"][0]["status"], "fail")
        self.assertEqual(result["rows"][0]["missingRequiredEvidence"], ["customer concentration evidence that is absent"])


class InvalidLlmClient:
    provider = "invalid"
    model = "invalid-model"

    def complete_json(self, *, agent_name, instructions, input_text, schema_hint):
        content = {"agent": agent_name}
        return AgentLlmResponse(
            content=content,
            rawText=json.dumps(content),
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(inputTokens=1, outputTokens=1, totalTokens=2),
        )


class UnsupportedPromotionLlmClient:
    provider = "unsupported"
    model = "unsupported-model"

    def complete_json(self, *, agent_name, instructions, input_text, schema_hint):
        claim = {
            "claimType": "numeric",
            "statement": "Fixture unsupported metric is 999.",
            "sourcePath": "",
            "uri": "investor://company/ACME/metrics-json",
            "metric": "fixture_unsupported_metric",
            "value": 999.0,
        }
        if agent_name == "committee_chair":
            content = {
                "agent": agent_name,
                "suggestedState": "promote_candidate",
                "judgmentSummary": "Unsupported promotion fixture.",
                "promotionRationale": "Unsupported promotion rationale.",
                "claims": [claim],
                "keyRisks": ["fixture risk"],
                "missingEvidence": [],
                "nextActions": ["continue"],
                "confidence": 0.8,
            }
        else:
            content = {
                "agent": agent_name,
                "verdict": "pass",
                "summary": "Unsupported role fixture.",
                "claims": [claim],
                "risks": ["fixture risk"],
                "missingEvidence": [],
                "nextActions": ["continue"],
                "confidence": 0.8,
            }
        return AgentLlmResponse(
            content=content,
            rawText=json.dumps(content),
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(inputTokens=10, outputTokens=5, totalTokens=15),
        )


class SupportedLlmClient:
    provider = "supported"
    model = "supported-model"

    def __init__(self, suggested_state: str) -> None:
        self.suggested_state = suggested_state

    def complete_json(self, *, agent_name, instructions, input_text, schema_hint):
        if agent_name == "committee_chair":
            content = {
                "agent": agent_name,
                "suggestedState": self.suggested_state,
                "judgmentSummary": "Supported committee fixture.",
                "promotionRationale": "Base fair value and local metrics are cited.",
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
                "missingEvidence": [],
                "nextActions": ["continue"],
                "confidence": 0.8,
            }
        else:
            content = {
                "agent": agent_name,
                "verdict": "pass",
                "summary": "Supported role fixture.",
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
                "nextActions": ["continue"],
                "confidence": 0.8,
            }
        return AgentLlmResponse(
            content=content,
            rawText=json.dumps(content),
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(inputTokens=10, outputTokens=5, totalTokens=15),
        )


if __name__ == "__main__":
    unittest.main()
