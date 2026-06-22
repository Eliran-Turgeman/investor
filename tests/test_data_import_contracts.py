import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from investor_toolkit.cli import main as cli_main
from investor_toolkit.data_import import import_vendor_drop


class DataImportContractTests(unittest.TestCase):
    def test_duplicate_ticker_date_price_rows_block_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "prices.csv"
            source.write_text(
                "ticker,date,close,currency,adjustment,provider\n"
                "acme,2026-01-02,10,USD,split_adjusted,TestVendor\n"
                "ACME,2026-01-02,11,USD,split_adjusted,TestVendor\n",
                encoding="utf-8",
            )

            result = import_vendor_drop(
                kind="prices",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="duplicate-prices",
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("duplicate primary key for prices", "\n".join(result["errors"]))
        self.assertEqual(result["normalizedPath"], "")

    def test_invalid_currency_blocks_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fundamentals.csv"
            source.write_text(
                "ticker,period,metric,value,currency,unit,provider\n"
                "ACME,2026-FY,revenue,100,USDD,millions,TestVendor\n",
                encoding="utf-8",
            )

            result = import_vendor_drop(
                kind="fundamentals",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="bad-currency",
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("currency must be one of", "\n".join(result["errors"]))

    def test_missing_unit_and_provider_metadata_block_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fundamentals.csv"
            source.write_text(
                "ticker,period,metric,value,currency\n"
                "ACME,2026-FY,revenue,100,USD\n",
                encoding="utf-8",
            )

            result = import_vendor_drop(
                kind="fundamentals",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="missing-metadata",
            )

        errors = "\n".join(result["errors"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("missing required column(s): unit, provider", errors)

    def test_invalid_period_blocks_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fundamentals.csv"
            source.write_text(
                "ticker,period,metric,value,currency,unit,provider\n"
                "ACME,FY2026,revenue,100,USD,millions,TestVendor\n",
                encoding="utf-8",
            )

            result = import_vendor_drop(
                kind="fundamentals",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="bad-period",
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("period must be", "\n".join(result["errors"]))

    def test_missing_price_adjustment_metadata_blocks_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "prices.csv"
            source.write_text(
                "ticker,date,close,currency,provider\n"
                "ACME,2026-01-02,10,USD,TestVendor\n",
                encoding="utf-8",
            )

            result = import_vendor_drop(
                kind="prices",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="missing-adjustment",
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("missing required column(s): adjustment", "\n".join(result["errors"]))

    def test_stale_prices_warn_or_block_by_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "prices.csv"
            source.write_text(
                "ticker,date,close,currency,adjustment,provider\n"
                "ACME,2000-01-02,10,USD,split_adjusted,TestVendor\n",
                encoding="utf-8",
            )

            warning_only = import_vendor_drop(
                kind="prices",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="stale-warning",
                max_price_age_days=1,
            )
            blocked = import_vendor_drop(
                kind="prices",
                path=source,
                provider="TestVendor",
                cwd=root,
                portfolio_dir=root / "portfolio",
                run_id="stale-block",
                max_price_age_days=1,
                block_stale_prices=True,
            )

        self.assertEqual(warning_only["status"], "ok")
        self.assertIn("stale price date", "\n".join(warning_only["warnings"]))
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("stale price date", "\n".join(blocked["errors"]))

    def test_cli_can_block_stale_price_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "prices.csv"
            source.write_text(
                "ticker,date,close,currency,adjustment,provider\n"
                "ACME,2000-01-02,10,USD,split_adjusted,TestVendor\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "data",
                        "import",
                        "--kind",
                        "prices",
                        "--path",
                        str(source),
                        "--provider",
                        "TestVendor",
                        "--run-id",
                        "cli-stale-block",
                        "--output-root",
                        str(root / "data_imports"),
                        "--portfolio-dir",
                        str(root / "portfolio"),
                        "--max-price-age-days",
                        "1",
                        "--block-stale-prices",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("data.import: blocked", stdout.getvalue())
        self.assertIn("stale price date", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
