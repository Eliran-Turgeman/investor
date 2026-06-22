from __future__ import annotations

import csv
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import AuditLedger, file_hash
from .utils import normalize_ticker, parse_iso_date, utc_now_iso, write_json


SCHEMA_VERSION = "1.1"
PROVIDER_COLUMN = "provider"

ALLOWED_CURRENCIES = {
    "AED",
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CNY",
    "DKK",
    "EUR",
    "GBP",
    "HKD",
    "ILS",
    "INR",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "SEK",
    "SGD",
    "TWD",
    "USD",
    "ZAR",
}
ALLOWED_UNITS = {
    "bps",
    "billions",
    "millions",
    "multiple",
    "ones",
    "per_share",
    "percent",
    "ratio",
    "shares",
    "thousands",
}
ALLOWED_PRICE_ADJUSTMENTS = {
    "adjusted",
    "raw",
    "split_adjusted",
    "split_dividend_adjusted",
    "total_return",
}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIOD_PATTERN = re.compile(r"^(?:\d{4}-(?:FY|Q[1-4]|H[1-2])|\d{4}-\d{2}|\d{4}-\d{2}-\d{2})$")

IMPORT_SCHEMAS: dict[str, dict[str, Any]] = {
    "company_master": {
        "required": ["ticker", "name", "exchange", "country", "sector", "industry", PROVIDER_COLUMN],
        "primaryKey": ["ticker"],
        "dateFields": [],
        "numericFields": [],
    },
    "prices": {
        "required": ["ticker", "date", "close", "currency", "adjustment", PROVIDER_COLUMN],
        "primaryKey": ["ticker", "date"],
        "dateFields": ["date"],
        "numericFields": ["close"],
        "currencyFields": ["currency"],
        "adjustmentField": "adjustment",
    },
    "fundamentals": {
        "required": ["ticker", "period", "metric", "value", "currency", "unit", PROVIDER_COLUMN],
        "primaryKey": ["ticker", "period", "metric"],
        "dateFields": [],
        "numericFields": ["value"],
        "currencyFields": ["currency"],
        "periodFields": ["period"],
        "unitFields": ["unit"],
    },
    "estimates": {
        "required": ["ticker", "period", "metric", "estimate", "currency", "unit", PROVIDER_COLUMN],
        "primaryKey": ["ticker", "period", "metric"],
        "dateFields": [],
        "numericFields": ["estimate"],
        "currencyFields": ["currency"],
        "periodFields": ["period"],
        "unitFields": ["unit"],
    },
    "multiples": {
        "required": ["ticker", "date", "metric", "value", "unit", PROVIDER_COLUMN],
        "primaryKey": ["ticker", "date", "metric"],
        "dateFields": ["date"],
        "numericFields": ["value"],
        "unitFields": ["unit"],
    },
    "ownership": {
        "required": ["ticker", "date", "holder", "shares", "unit", PROVIDER_COLUMN],
        "primaryKey": ["ticker", "date", "holder"],
        "dateFields": ["date"],
        "numericFields": ["shares"],
        "unitFields": ["unit"],
    },
    "sector_taxonomy": {
        "required": ["ticker", "sector", "industry", PROVIDER_COLUMN],
        "primaryKey": ["ticker"],
        "dateFields": [],
        "numericFields": [],
    },
}


def import_vendor_drop(
    *,
    kind: str,
    path: str | Path,
    provider: str,
    cwd: str | Path = ".",
    output_root: str | Path = "data_imports",
    run_id: str | None = None,
    portfolio_dir: str | Path = "portfolio",
    max_price_age_days: int | None = None,
    block_stale_prices: bool = False,
) -> dict[str, Any]:
    kind = kind.strip()
    if kind not in IMPORT_SCHEMAS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(IMPORT_SCHEMAS))}")
    provider = _safe_part(provider)
    if not provider:
        raise ValueError("provider cannot be empty")
    if max_price_age_days is not None and max_price_age_days < 0:
        raise ValueError("max_price_age_days cannot be negative")
    root = Path(cwd).resolve()
    source = _resolve(path, root)
    if not source.exists():
        raise FileNotFoundError(f"Data import source not found: {source}")
    resolved_run_id = run_id or _generated_run_id(kind)
    rows = _read_rows(source)
    schema = IMPORT_SCHEMAS[kind]
    errors, warnings, normalized = _validate_rows(
        rows,
        schema=schema,
        kind=kind,
        provider=provider,
        max_price_age_days=max_price_age_days,
        block_stale_prices=block_stale_prices,
    )
    status = "blocked" if errors else "ok"
    out_dir = _resolve(output_root, root) / provider
    normalized_path = out_dir / f"{resolved_run_id}.{kind}.jsonl"
    manifest_path = out_dir / f"{resolved_run_id}.json"
    if not errors:
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text(
            "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in normalized),
            encoding="utf-8",
            newline="\n",
        )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": resolved_run_id,
        "generatedAt": utc_now_iso(),
        "kind": kind,
        "provider": provider,
        "sourcePath": str(source),
        "sourceHash": file_hash(source),
        "requiredColumns": schema["required"],
        "contract": {
            "providerColumn": PROVIDER_COLUMN,
            "primaryKey": schema.get("primaryKey", []),
            "currencyFields": schema.get("currencyFields", []),
            "unitFields": schema.get("unitFields", []),
            "periodFields": schema.get("periodFields", []),
            "adjustmentField": schema.get("adjustmentField", ""),
            "allowedCurrencies": sorted(ALLOWED_CURRENCIES),
            "allowedUnits": sorted(ALLOWED_UNITS),
            "allowedPriceAdjustments": sorted(ALLOWED_PRICE_ADJUSTMENTS),
            "maxPriceAgeDays": max_price_age_days,
            "blockStalePrices": block_stale_prices,
        },
        "providerProvenance": {
            "argument": provider,
            "inputColumn": PROVIDER_COLUMN,
            "enforced": True,
        },
        "rowCount": len(rows),
        "normalizedRowCount": len(normalized) if not errors else 0,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "normalizedPath": str(normalized_path) if not errors else "",
    }
    write_json(manifest_path, manifest)
    portfolio = _resolve(portfolio_dir, root)
    AuditLedger(portfolio / "audit.db").record_run(
        run_id=resolved_run_id,
        command="data.import",
        provider=provider,
        config={"kind": kind, "sourcePath": str(source)},
        inputs={"sourceHash": file_hash(source), "rowCount": len(rows)},
        outputs=manifest,
        warnings=warnings + errors,
        status=status,
    )
    return manifest


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".parquet":
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:
            raise ValueError("Parquet imports require optional pandas/pyarrow support; use CSV in the dependency-free CLI.") from exc
        frame = pd.read_parquet(path)
        return [dict(row) for row in frame.to_dict(orient="records")]
    raise ValueError("Data import path must be .csv or .parquet")


def _validate_rows(
    rows: list[dict[str, Any]],
    *,
    schema: dict[str, Any],
    kind: str,
    provider: str,
    max_price_age_days: int | None = None,
    block_stale_prices: bool = False,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    normalized: list[dict[str, Any]] = []
    required = list(schema["required"])
    primary_key = list(schema.get("primaryKey", []))
    seen_keys: dict[tuple[str, ...], int] = {}
    if not rows:
        errors.append("source file contains no rows")
        return errors, warnings, normalized
    missing_columns = [column for column in required if column not in rows[0]]
    if missing_columns:
        errors.append("missing required column(s): " + ", ".join(missing_columns))
        return errors, warnings, normalized
    for index, row in enumerate(rows, start=2):
        normalized_row: dict[str, Any] = {"kind": kind}
        for column in required:
            value = str(row.get(column, "")).strip()
            if not value:
                errors.append(f"row {index}: {column} is required")
            normalized_row[column] = value
        row_provider = _safe_part(str(normalized_row.get(PROVIDER_COLUMN) or ""))
        if not row_provider:
            errors.append(f"row {index}: {PROVIDER_COLUMN} is required")
        elif row_provider != provider:
            errors.append(f"row {index}: {PROVIDER_COLUMN} must match import provider {provider}")
        normalized_row[PROVIDER_COLUMN] = row_provider
        if "ticker" in normalized_row and normalized_row["ticker"]:
            try:
                normalized_row["ticker"] = normalize_ticker(str(normalized_row["ticker"]))
            except ValueError as exc:
                errors.append(f"row {index}: invalid ticker: {exc}")
        parsed_dates = {}
        for field in schema.get("dateFields", []):
            raw_date = str(normalized_row.get(field) or "")
            parsed = parse_iso_date(raw_date) if DATE_PATTERN.fullmatch(raw_date) else None
            if normalized_row.get(field) and parsed is None:
                errors.append(f"row {index}: {field} must be YYYY-MM-DD")
            elif parsed is not None:
                parsed_dates[field] = parsed
        for field in schema.get("numericFields", []):
            try:
                numeric_value = float(str(normalized_row[field]).replace(",", ""))
                if not math.isfinite(numeric_value):
                    raise ValueError
                normalized_row[field] = numeric_value
            except (TypeError, ValueError):
                errors.append(f"row {index}: {field} must be numeric")
        for field in schema.get("currencyFields", []):
            currency = str(normalized_row.get(field) or "").strip().upper()
            if currency not in ALLOWED_CURRENCIES:
                errors.append(f"row {index}: {field} must be one of: {', '.join(sorted(ALLOWED_CURRENCIES))}")
            normalized_row[field] = currency
        for field in schema.get("unitFields", []):
            unit = _normalize_domain_value(str(normalized_row.get(field) or ""))
            if unit not in ALLOWED_UNITS:
                errors.append(f"row {index}: {field} must be one of: {', '.join(sorted(ALLOWED_UNITS))}")
            normalized_row[field] = unit
        adjustment_field = schema.get("adjustmentField")
        if adjustment_field:
            adjustment = _normalize_domain_value(str(normalized_row.get(adjustment_field) or ""))
            if adjustment not in ALLOWED_PRICE_ADJUSTMENTS:
                errors.append(
                    f"row {index}: {adjustment_field} must be one of: {', '.join(sorted(ALLOWED_PRICE_ADJUSTMENTS))}"
                )
            normalized_row[adjustment_field] = adjustment
        for field in schema.get("periodFields", []):
            period = str(normalized_row.get(field) or "").strip().upper()
            if not PERIOD_PATTERN.fullmatch(period):
                errors.append(f"row {index}: {field} must be YYYY-FY, YYYY-Q1..Q4, YYYY-H1..H2, YYYY-MM, or YYYY-MM-DD")
            normalized_row[field] = period
        if kind == "prices" and max_price_age_days is not None and "date" in parsed_dates:
            age_days = (datetime.now(UTC).date() - parsed_dates["date"]).days
            message = (
                f"row {index}: stale price date {parsed_dates['date'].isoformat()} is "
                f"{age_days} days old (max {max_price_age_days})"
            )
            if age_days < 0:
                errors.append(f"row {index}: date cannot be in the future")
            elif age_days > max_price_age_days:
                if block_stale_prices:
                    errors.append(message)
                else:
                    warnings.append(message)
        if primary_key and all(normalized_row.get(field) not in (None, "") for field in primary_key):
            key = tuple(str(normalized_row[field]) for field in primary_key)
            if key in seen_keys:
                errors.append(
                    f"row {index}: duplicate primary key for {kind}: "
                    f"{_format_key(primary_key, key)} (first seen row {seen_keys[key]})"
                )
            else:
                seen_keys[key] = index
        if str(row.get("restated", "")).strip().lower() in {"true", "1", "yes"}:
            warnings.append(f"row {index}: restated data flagged")
            normalized_row["restated"] = True
        normalized.append(normalized_row)
    return errors, sorted(set(warnings)), normalized


def _format_key(fields: list[str], values: tuple[str, ...]) -> str:
    return ", ".join(f"{field}={value}" for field, value in zip(fields, values, strict=True))


def _normalize_domain_value(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _generated_run_id(kind: str) -> str:
    stamp = datetime.now(UTC).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_safe_part(kind)}"


def _safe_part(value: str) -> str:
    cleaned = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "-" for char in value).strip("._-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned


def _resolve(path: str | Path, cwd: Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = cwd / resolved
    return resolved.resolve()
