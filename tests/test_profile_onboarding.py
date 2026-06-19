import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from investor_toolkit.app import AppContext, InvestorApplication
from investor_toolkit.cli import main as cli_main


class ProfileOnboardingTests(unittest.TestCase):
    def test_onboarding_init_creates_profile_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "onboarding",
                        "init",
                        "--portfolio-dir",
                        str(portfolio_dir),
                        "--focus",
                        "software",
                        "--focus",
                        "ai_related_hardware_or_hardware_adjacent_businesses",
                        "--external-exposure",
                        "MSFT:50000:USD:RSU",
                        "--other-portfolio",
                        "index_portfolio:250000:NIS",
                    ]
                )

            goals = json.loads((portfolio_dir / "goals.json").read_text(encoding="utf-8"))
            external = json.loads((portfolio_dir / "external_exposure.json").read_text(encoding="utf-8"))
            operating = json.loads((portfolio_dir / "operating_preferences.json").read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertTrue((portfolio_dir / "investor_policy.md").is_file())
            self.assertTrue((portfolio_dir / "thesis_template.md").is_file())
            self.assertTrue((portfolio_dir / "rejected" / "README.md").is_file())
            self.assertEqual(goals["timeHorizonYears"], {"minimum": 5, "maximum": 10})
            self.assertEqual(operating["ideaFlow"]["targetIdeasPerMonth"], 3)
            self.assertEqual(external["exposures"][0]["ticker"], "MSFT")
            self.assertEqual(external["exposures"][0]["approximateMarketValue"]["amount"], 50000.0)
            self.assertFalse(external["exposures"][0]["includeInActivePortfolio"])
            self.assertIn("investor profile initialized", stdout.getvalue())

    def test_profile_init_does_not_overwrite_existing_files_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            portfolio_dir.mkdir()
            policy = portfolio_dir / "investor_policy.md"
            policy.write_text("custom policy\n", encoding="utf-8")
            app = InvestorApplication(AppContext.from_env(cwd=tmp, portfolio_dir=portfolio_dir))

            result = app.profile.init()

            self.assertEqual(policy.read_text(encoding="utf-8"), "custom policy\n")
            self.assertGreater(result.data["skippedCount"], 0)
            self.assertIn(str(policy.resolve()), result.data["filesSkipped"])

    def test_profile_init_overwrite_replaces_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_dir = Path(tmp) / "portfolio"
            portfolio_dir.mkdir()
            policy = portfolio_dir / "investor_policy.md"
            policy.write_text("custom policy\n", encoding="utf-8")
            app = InvestorApplication(AppContext.from_env(cwd=tmp, portfolio_dir=portfolio_dir))

            result = app.profile.init(overwrite=True)

            self.assertIn("Investor Policy", policy.read_text(encoding="utf-8"))
            self.assertEqual(result.data["skippedCount"], 0)


if __name__ == "__main__":
    unittest.main()
