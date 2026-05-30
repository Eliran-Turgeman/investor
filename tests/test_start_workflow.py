import importlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class OfflineStartWorkflowTests(unittest.TestCase):
    def test_start_bootstraps_data_folder_without_network(self):
        cli = importlib.import_module("investor_research.cli")

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"

            with mock.patch.object(
                socket,
                "socket",
                side_effect=AssertionError("offline start must not open network sockets"),
            ):
                exit_code = cli.main(
                    [
                        "start",
                        "msft",
                        "--offline",
                        "--research-root",
                        str(research_root),
                    ]
                )

            self.assertIn(exit_code, (None, 0))

            ticker_dir = research_root / "MSFT"
            self.assertTrue(ticker_dir.is_dir())

            expected_dirs = [
                "filings/raw",
                "filings/metadata",
                "extracted",
                "data/provider_responses",
                "data/provider_responses/sec",
                "data/provider_responses/yahoo",
                "metrics",
                "index",
            ]
            for relative_path in expected_dirs:
                with self.subTest(path=relative_path):
                    self.assertTrue((ticker_dir / relative_path).is_dir())

            self.assertTrue((ticker_dir / "company.json").is_file())

            company = json.loads((ticker_dir / "company.json").read_text(encoding="utf-8"))
            self.assertEqual(company["ticker"], "MSFT")

            self.assertFalse((ticker_dir / "memo.md").exists())
            self.assertFalse((ticker_dir / "thesis-log.md").exists())
            self.assertFalse((ticker_dir / "questions.md").exists())

    def test_start_is_idempotent_and_preserves_agent_owned_files(self):
        cli = importlib.import_module("investor_research.cli")

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"

            args = ["start", "MSFT", "--offline", "--research-root", str(research_root)]
            with mock.patch.object(
                socket,
                "socket",
                side_effect=AssertionError("offline start must not open network sockets"),
            ):
                self.assertIn(cli.main(args), (None, 0))

            memo_path = research_root / "MSFT" / "memo.md"
            memo_path.write_text(
                "# Investment Memo: MSFT\n\nUser note: inspect cloud margin durability.\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                socket,
                "socket",
                side_effect=AssertionError("offline start must not open network sockets"),
            ):
                self.assertIn(cli.main(args), (None, 0))

            memo = memo_path.read_text(encoding="utf-8")
            self.assertIn("User note: inspect cloud margin durability.", memo)


if __name__ == "__main__":
    unittest.main()
