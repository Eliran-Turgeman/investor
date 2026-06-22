import io
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

from investor_toolkit.audit import AuditLedger, verify_audit_ledger
from investor_toolkit.cli import main as cli_main
from investor_toolkit.utils import write_json


class AuditLedgerIntegrityTests(unittest.TestCase):
    def test_normal_hash_chain_verifies_and_blocks_direct_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "portfolio" / "audit.db"
            ledger = AuditLedger(db_path)
            ledger.record_run(run_id="run-1", command="agents.run", inputs={"ticker": "ACME"}, outputs={"ok": True})
            ledger.record_tool_call(run_id="run-1", tool_name="fixture", inputs={}, outputs={"status": "ok"})

            result = ledger.verify()
            with closing(sqlite3.connect(db_path)) as conn:
                run_chain = conn.execute(
                    "select sequence, previous_hash, length(row_hash) from runs order by sequence"
                ).fetchone()
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("update runs set status = 'tampered' where run_id = 'run-1'")
                conn.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("delete from runs where run_id = 'run-1'")
                conn.rollback()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(run_chain, (1, "0" * 64, 64))

    def test_verify_detects_tampered_row_when_trigger_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "portfolio" / "audit.db"
            ledger = AuditLedger(db_path)
            ledger.record_run(run_id="run-1", command="agents.run", outputs={"ok": True})

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("drop trigger audit_runs_no_update")
                conn.execute("update runs set status = 'tampered' where run_id = 'run-1'")
                conn.commit()

            result = verify_audit_ledger(db_path)
            with self.assertRaises(ValueError):
                AuditLedger(db_path).record_tool_call(run_id="run-2", tool_name="fixture", inputs={}, outputs={})

        self.assertEqual(result["status"], "fail")
        self.assertIn("runs: missing append-only trigger(s): audit_runs_no_update", result["failures"])
        self.assertIn("runs: sequence 1 has row_hash mismatch", result["failures"])

    def test_cli_audit_verify_reports_valid_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "portfolio" / "audit.db"
            AuditLedger(db_path).record_run(run_id="run-1", command="agents.run")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = cli_main(["audit", "verify", "--path", str(db_path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("audit.verify: ok", stdout.getvalue())
        self.assertIn("- runs: ok", stdout.getvalue())


class AtomicJsonWriteTests(unittest.TestCase):
    def test_write_json_replace_failure_preserves_existing_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            write_json(path, {"old": True})
            original = path.read_text(encoding="utf-8")

            with mock.patch("investor_toolkit.utils.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    write_json(path, {"new": True})

            leftovers = list(Path(tmp).glob(".artifact.json.*.tmp"))
            current = path.read_text(encoding="utf-8")

        self.assertEqual(current, original)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
