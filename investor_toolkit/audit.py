from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .utils import utc_now_iso


SCHEMA_VERSION = "1.1"
ZERO_HASH = "0" * 64
CHAIN_COLUMNS = {"id", "sequence", "previous_hash", "row_hash"}


AUDIT_TABLES: dict[str, list[tuple[str, str]]] = {
    "runs": [
        ("run_id", "text not null"),
        ("command", "text not null"),
        ("generated_at", "text not null"),
        ("code_version", "text not null"),
        ("config_hash", "text not null"),
        ("prompt_version", "text not null"),
        ("provider", "text not null"),
        ("model", "text not null"),
        ("input_hash", "text not null"),
        ("output_hash", "text not null"),
        ("token_usage_json", "text not null"),
        ("warnings_json", "text not null"),
        ("status", "text not null"),
    ],
    "agent_calls": [
        ("run_id", "text not null"),
        ("ticker", "text not null"),
        ("agent_name", "text not null"),
        ("provider", "text not null"),
        ("model", "text not null"),
        ("prompt_version", "text not null"),
        ("input_hash", "text not null"),
        ("output_hash", "text not null"),
        ("usage_json", "text not null"),
        ("status", "text not null"),
        ("error", "text not null"),
        ("generated_at", "text not null"),
    ],
    "tool_calls": [
        ("run_id", "text not null"),
        ("tool_name", "text not null"),
        ("input_hash", "text not null"),
        ("output_hash", "text not null"),
        ("status", "text not null"),
        ("error", "text not null"),
        ("generated_at", "text not null"),
    ],
    "candidate_events": [
        ("run_id", "text not null"),
        ("ticker", "text not null"),
        ("event_type", "text not null"),
        ("before_state", "text not null"),
        ("after_state", "text not null"),
        ("payload_json", "text not null"),
        ("generated_at", "text not null"),
    ],
    "claim_checks": [
        ("run_id", "text not null"),
        ("ticker", "text not null"),
        ("agent_name", "text not null"),
        ("claim_json", "text not null"),
        ("status", "text not null"),
        ("reason", "text not null"),
        ("source_path", "text not null"),
        ("uri", "text not null"),
        ("generated_at", "text not null"),
    ],
    "approvals": [
        ("ticker", "text not null"),
        ("reviewer", "text not null"),
        ("state", "text not null"),
        ("reason", "text not null"),
        ("source_hashes_json", "text not null"),
        ("path", "text not null"),
        ("generated_at", "text not null"),
    ],
    "eval_runs": [
        ("run_id", "text not null"),
        ("suite", "text not null"),
        ("metrics_json", "text not null"),
        ("result_path", "text not null"),
        ("status", "text not null"),
        ("generated_at", "text not null"),
    ],
}


class AuditLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def record_run(
        self,
        *,
        run_id: str,
        command: str,
        provider: str = "",
        model: str = "",
        prompt_version: str = "",
        config: Any = None,
        inputs: Any = None,
        outputs: Any = None,
        token_usage: Any = None,
        warnings: list[str] | None = None,
        status: str = "ok",
    ) -> int:
        return self._insert(
            "runs",
            {
                "run_id": run_id,
                "command": command,
                "generated_at": utc_now_iso(),
                "code_version": __version__,
                "config_hash": stable_hash(config),
                "prompt_version": prompt_version,
                "provider": provider,
                "model": model,
                "input_hash": stable_hash(inputs),
                "output_hash": stable_hash(outputs),
                "token_usage_json": _json(token_usage or {}),
                "warnings_json": _json(warnings or []),
                "status": status,
            },
        )

    def record_agent_call(
        self,
        *,
        run_id: str,
        ticker: str,
        agent_name: str,
        provider: str,
        model: str,
        prompt_version: str,
        inputs: Any,
        outputs: Any,
        usage: Any,
        status: str = "ok",
        error: str = "",
    ) -> int:
        return self._insert(
            "agent_calls",
            {
                "run_id": run_id,
                "ticker": ticker,
                "agent_name": agent_name,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "input_hash": stable_hash(inputs),
                "output_hash": stable_hash(outputs),
                "usage_json": _json(usage or {}),
                "status": status,
                "error": error,
                "generated_at": utc_now_iso(),
            },
        )

    def record_tool_call(
        self,
        *,
        run_id: str,
        tool_name: str,
        inputs: Any,
        outputs: Any,
        status: str = "ok",
        error: str = "",
    ) -> int:
        return self._insert(
            "tool_calls",
            {
                "run_id": run_id,
                "tool_name": tool_name,
                "input_hash": stable_hash(inputs),
                "output_hash": stable_hash(outputs),
                "status": status,
                "error": error,
                "generated_at": utc_now_iso(),
            },
        )

    def record_candidate_event(
        self,
        *,
        run_id: str,
        ticker: str,
        event_type: str,
        before_state: str = "",
        after_state: str = "",
        payload: Any = None,
    ) -> int:
        return self._insert(
            "candidate_events",
            {
                "run_id": run_id,
                "ticker": ticker,
                "event_type": event_type,
                "before_state": before_state,
                "after_state": after_state,
                "payload_json": _json(payload or {}),
                "generated_at": utc_now_iso(),
            },
        )

    def record_claim_check(
        self,
        *,
        run_id: str,
        ticker: str,
        agent_name: str,
        claim: Any,
        status: str,
        reason: str,
        source_path: str = "",
        uri: str = "",
    ) -> int:
        return self._insert(
            "claim_checks",
            {
                "run_id": run_id,
                "ticker": ticker,
                "agent_name": agent_name,
                "claim_json": _json(claim or {}),
                "status": status,
                "reason": reason,
                "source_path": source_path,
                "uri": uri,
                "generated_at": utc_now_iso(),
            },
        )

    def record_approval(
        self,
        *,
        ticker: str,
        reviewer: str,
        state: str,
        reason: str,
        source_hashes: Any,
        path: str,
    ) -> int:
        return self._insert(
            "approvals",
            {
                "ticker": ticker,
                "reviewer": reviewer,
                "state": state,
                "reason": reason,
                "source_hashes_json": _json(source_hashes or {}),
                "path": path,
                "generated_at": utc_now_iso(),
            },
        )

    def record_eval_run(
        self,
        *,
        run_id: str,
        suite: str,
        metrics: Any,
        result_path: str,
        status: str,
    ) -> int:
        return self._insert(
            "eval_runs",
            {
                "run_id": run_id,
                "suite": suite,
                "metrics_json": _json(metrics or {}),
                "result_path": result_path,
                "status": status,
                "generated_at": utc_now_iso(),
            },
        )

    def table_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {table: int(conn.execute(f"select count(*) from {table}").fetchone()[0]) for table in AUDIT_TABLES}

    def verify(self) -> dict[str, Any]:
        return _verify_existing_ledger(self.path)

    def _insert(self, table: str, values: dict[str, Any]) -> int:
        if table not in AUDIT_TABLES:
            raise ValueError(f"Unknown audit table: {table}")
        value_columns = list(values)
        sql_columns = ["sequence", "previous_hash", "row_hash", *value_columns]
        placeholders = ", ".join("?" for _ in sql_columns)
        sql = f"insert into {table} ({', '.join(sql_columns)}) values ({placeholders})"
        with self._connect() as conn:
            conn.execute("begin immediate")
            failures: list[str] = []
            for audit_table in AUDIT_TABLES:
                _verify_table(conn, audit_table, failures)
            if failures:
                raise ValueError("Audit ledger integrity check failed before append: " + "; ".join(failures))
            sequence, previous_hash = _next_chain_position(conn, table)
            row_hash = _row_hash(table=table, sequence=sequence, previous_hash=previous_hash, payload=values)
            cursor = conn.execute(sql, [sequence, previous_hash, row_hash, *[values[column] for column in value_columns]])
            conn.execute(
                """
                insert into ledger_meta (table_name, schema_version, head_hash, row_count, updated_at)
                values (?, ?, ?, ?, ?)
                on conflict(table_name) do update set
                    schema_version = excluded.schema_version,
                    head_hash = excluded.head_hash,
                    row_count = excluded.row_count,
                    updated_at = excluded.updated_at
                """,
                (table, SCHEMA_VERSION, row_hash, sequence, utc_now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists ledger_meta (
                    table_name text primary key,
                    schema_version text not null,
                    head_hash text not null,
                    row_count integer not null,
                    updated_at text not null
                )
                """
            )
            for table, columns in AUDIT_TABLES.items():
                _drop_append_only_triggers(conn, table)
                if _table_exists(conn, table):
                    needs_backfill = _migrate_table(conn, table)
                else:
                    conn.execute(_create_table_sql(table, columns))
                    needs_backfill = False
                if needs_backfill or _table_has_unchained_rows(conn, table):
                    _backfill_table_chain(conn, table)
                else:
                    _ensure_ledger_meta(conn, table)
                conn.execute(f"create unique index if not exists idx_{table}_sequence on {table}(sequence)")
                _create_append_only_triggers(conn, table)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("pragma busy_timeout=30000")
            yield conn
        finally:
            conn.close()


def verify_audit_ledger(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.exists():
        return {
            "status": "fail",
            "path": str(resolved),
            "schemaVersion": SCHEMA_VERSION,
            "tables": [],
            "failures": [f"audit ledger does not exist: {resolved}"],
        }
    return _verify_existing_ledger(resolved)


def _verify_existing_ledger(path: Path) -> dict[str, Any]:
    failures: list[str] = []
    tables: list[dict[str, Any]] = []
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout=30000")
        if not _table_exists(conn, "ledger_meta"):
            failures.append("missing ledger_meta table")
        for table in AUDIT_TABLES:
            table_result = _verify_table(conn, table, failures)
            tables.append(table_result)
    finally:
        conn.close()
    return {
        "status": "ok" if not failures else "fail",
        "path": str(path),
        "schemaVersion": SCHEMA_VERSION,
        "tables": tables,
        "failures": failures,
    }


def _verify_table(conn: sqlite3.Connection, table: str, failures: list[str]) -> dict[str, Any]:
    if not _table_exists(conn, table):
        failures.append(f"{table}: missing audit table")
        return {"table": table, "status": "fail", "rowCount": 0, "headHash": ZERO_HASH}

    columns = set(_table_columns(conn, table))
    missing_chain_columns = sorted(CHAIN_COLUMNS - columns)
    if missing_chain_columns:
        failures.append(f"{table}: missing chain column(s): {', '.join(missing_chain_columns)}")
        return {"table": table, "status": "fail", "rowCount": 0, "headHash": ZERO_HASH}

    expected_triggers = set(_trigger_names(table))
    triggers = {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'trigger' and tbl_name = ?",
            (table,),
        ).fetchall()
    }
    missing_triggers = sorted(expected_triggers - triggers)
    if missing_triggers:
        failures.append(f"{table}: missing append-only trigger(s): {', '.join(missing_triggers)}")

    rows = conn.execute(f"select * from {table} order by sequence, id").fetchall()
    expected_previous_hash = ZERO_HASH
    expected_sequence = 1
    table_failures_before = len(failures)
    for row in rows:
        sequence = int(row["sequence"] or 0)
        if sequence != expected_sequence:
            failures.append(f"{table}: expected sequence {expected_sequence}, found {sequence}")
            expected_sequence = sequence
        if row["previous_hash"] != expected_previous_hash:
            failures.append(f"{table}: sequence {sequence} has previous_hash mismatch")
        payload = _row_payload(row)
        expected_hash = _row_hash(
            table=table,
            sequence=sequence,
            previous_hash=str(row["previous_hash"]),
            payload=payload,
        )
        if row["row_hash"] != expected_hash:
            failures.append(f"{table}: sequence {sequence} has row_hash mismatch")
        expected_previous_hash = str(row["row_hash"])
        expected_sequence += 1

    head_hash = rows[-1]["row_hash"] if rows else ZERO_HASH
    if _table_exists(conn, "ledger_meta"):
        meta = conn.execute(
            "select head_hash, row_count, schema_version from ledger_meta where table_name = ?",
            (table,),
        ).fetchone()
        if meta is None:
            failures.append(f"{table}: missing ledger_meta row")
        else:
            if int(meta["row_count"]) != len(rows):
                failures.append(f"{table}: ledger_meta row_count mismatch")
            if meta["head_hash"] != head_hash:
                failures.append(f"{table}: ledger_meta head_hash mismatch")
            if meta["schema_version"] != SCHEMA_VERSION:
                failures.append(f"{table}: ledger_meta schema_version mismatch")

    return {
        "table": table,
        "status": "ok" if len(failures) == table_failures_before else "fail",
        "rowCount": len(rows),
        "headHash": head_hash,
    }


def _create_table_sql(table: str, columns: list[tuple[str, str]]) -> str:
    definitions = [
        "id integer primary key autoincrement",
        "sequence integer not null",
        "previous_hash text not null",
        "row_hash text not null",
        *[f"{name} {definition}" for name, definition in columns],
    ]
    return f"create table if not exists {table} ({', '.join(definitions)})"


def _migrate_table(conn: sqlite3.Connection, table: str) -> bool:
    columns = set(_table_columns(conn, table))
    needs_backfill = False
    if "sequence" not in columns:
        conn.execute(f"alter table {table} add column sequence integer")
        needs_backfill = True
    if "previous_hash" not in columns:
        conn.execute(f"alter table {table} add column previous_hash text")
        needs_backfill = True
    if "row_hash" not in columns:
        conn.execute(f"alter table {table} add column row_hash text")
        needs_backfill = True
    return needs_backfill


def _backfill_table_chain(conn: sqlite3.Connection, table: str) -> None:
    rows = conn.execute(f"select * from {table} order by coalesce(sequence, id), id").fetchall()
    previous_hash = ZERO_HASH
    row_count = 0
    for row_count, row in enumerate(rows, start=1):
        payload = _row_payload(row)
        row_hash = _row_hash(table=table, sequence=row_count, previous_hash=previous_hash, payload=payload)
        if row["sequence"] != row_count or row["previous_hash"] != previous_hash or row["row_hash"] != row_hash:
            conn.execute(
                f"update {table} set sequence = ?, previous_hash = ?, row_hash = ? where id = ?",
                (row_count, previous_hash, row_hash, row["id"]),
            )
        previous_hash = row_hash
    conn.execute(
        """
        insert into ledger_meta (table_name, schema_version, head_hash, row_count, updated_at)
        values (?, ?, ?, ?, ?)
        on conflict(table_name) do update set
            schema_version = excluded.schema_version,
            head_hash = excluded.head_hash,
            row_count = excluded.row_count,
            updated_at = excluded.updated_at
        """,
        (table, SCHEMA_VERSION, previous_hash, row_count, utc_now_iso()),
    )


def _ensure_ledger_meta(conn: sqlite3.Connection, table: str) -> None:
    row = conn.execute(f"select sequence, row_hash from {table} order by sequence desc limit 1").fetchone()
    row_count = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    head_hash = str(row["row_hash"]) if row else ZERO_HASH
    conn.execute(
        """
        insert into ledger_meta (table_name, schema_version, head_hash, row_count, updated_at)
        values (?, ?, ?, ?, ?)
        on conflict(table_name) do nothing
        """,
        (table, SCHEMA_VERSION, head_hash, row_count, utc_now_iso()),
    )


def _table_has_unchained_rows(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            f"""
            select 1 from {table}
            where sequence is null or previous_hash is null or row_hash is null
            limit 1
            """
        ).fetchone()
        is not None
    )


def _next_chain_position(conn: sqlite3.Connection, table: str) -> tuple[int, str]:
    row = conn.execute(f"select sequence, row_hash from {table} order by sequence desc limit 1").fetchone()
    if row is None:
        return 1, ZERO_HASH
    return int(row[0]) + 1, str(row[1])


def _row_hash(*, table: str, sequence: int, previous_hash: str, payload: dict[str, Any]) -> str:
    return stable_hash(
        {
            "table": table,
            "sequence": sequence,
            "previousHash": previous_hash,
            "payload": payload,
        }
    )


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys() if key not in CHAIN_COLUMNS}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def _trigger_names(table: str) -> tuple[str, str]:
    return (f"audit_{table}_no_update", f"audit_{table}_no_delete")


def _drop_append_only_triggers(conn: sqlite3.Connection, table: str) -> None:
    for trigger in _trigger_names(table):
        conn.execute(f"drop trigger if exists {trigger}")


def _create_append_only_triggers(conn: sqlite3.Connection, table: str) -> None:
    update_trigger, delete_trigger = _trigger_names(table)
    conn.execute(
        f"""
        create trigger if not exists {update_trigger}
        before update on {table}
        begin
            select raise(abort, 'audit table {table} is append-only');
        end
        """
    )
    conn.execute(
        f"""
        create trigger if not exists {delete_trigger}
        before delete on {table}
        begin
            select raise(abort, 'audit table {table} is append-only');
        end
        """
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def file_hash(path: str | Path) -> str:
    resolved = Path(path)
    if not resolved.exists() or not resolved.is_file():
        return ""
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, default=str, separators=(",", ":"))
