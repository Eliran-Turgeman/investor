from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_harness.claim_verifier import verify_review_claims
from .agent_harness.schemas import validate_agent_response
from .audit import AuditLedger
from .utils import normalize_ticker, read_json, utc_now_iso, write_json


SCHEMA_VERSION = "1.0"


def run_eval_suite(
    *,
    suite: str = "gold_candidates",
    cwd: str | Path = ".",
    portfolio_dir: str | Path = "portfolio",
    run_id: str | None = None,
) -> dict[str, Any]:
    root = Path(cwd).resolve()
    portfolio = _resolve(portfolio_dir, root)
    suite_path = _suite_path(suite, root)
    rows = _read_jsonl(suite_path)
    resolved_run_id = run_id or _generated_run_id(suite_path.stem)
    results = []
    for row in rows:
        ticker = normalize_ticker(str(row.get("ticker", "")))
        review_path = portfolio / "agent_reviews" / f"{ticker}.json"
        review = read_json(review_path, None)
        if not isinstance(review, dict):
            results.append({"ticker": ticker, "status": "missing_review", "errors": [f"missing {review_path}"]})
            continue
        results.append(_evaluate_row(row, review, cwd=root))
    metrics = _metrics(results)
    status = _status(metrics, results)
    output = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": resolved_run_id,
        "generatedAt": utc_now_iso(),
        "suite": suite,
        "suitePath": str(suite_path),
        "status": status,
        "metrics": metrics,
        "rows": results,
    }
    path = root / "evals" / "results" / f"{resolved_run_id}.json"
    write_json(path, output)
    AuditLedger(portfolio / "audit.db").record_eval_run(
        run_id=resolved_run_id,
        suite=suite,
        metrics=metrics,
        result_path=str(path),
        status=status,
    )
    return output


def _evaluate_row(row: dict[str, Any], review: dict[str, Any], cwd: Path) -> dict[str, Any]:
    ticker = str(review.get("ticker") or row.get("ticker") or "")
    errors = []
    schema_valid = True
    for output in review.get("roleOutputs", []):
        if not isinstance(output, dict):
            continue
        agent = str(output.get("agent") or "")
        try:
            validate_agent_response(agent, output.get("content", {}))
        except Exception as exc:
            schema_valid = False
            errors.append(f"{agent}: {exc}")
    chair = review.get("committeeChair", {})
    chair_content = chair.get("content", {}) if isinstance(chair, dict) else {}
    try:
        validate_agent_response("committee_chair", chair_content)
    except Exception as exc:
        schema_valid = False
        errors.append(f"committee_chair: {exc}")
    verification = review.get("claimVerification")
    if not isinstance(verification, dict):
        verification = verify_review_claims(review, cwd=cwd)
    suggested = str(chair_content.get("suggestedState") or "")
    expected_state = str(row.get("expectedState") or "")
    required_evidence = [str(item) for item in row.get("requiredEvidence", []) if item]
    evidence_blob = json.dumps(review, sort_keys=True).lower()
    missing_required = [item for item in required_evidence if item.lower() not in evidence_blob]
    known_reject = bool(row.get("knownReject", False))
    analyst_accepted = row.get("analystAccepted")
    return {
        "ticker": ticker,
        "status": "ok" if not errors and not missing_required else "fail",
        "schemaValid": schema_valid,
        "expectedState": expected_state,
        "suggestedState": suggested,
        "correctTriageState": bool(expected_state and suggested == expected_state),
        "knownReject": known_reject,
        "rejectedIdeaResurfaced": known_reject and suggested == "promote_candidate",
        "analystAccepted": analyst_accepted,
        "missingRequiredEvidence": missing_required,
        "unsupportedClaimCount": int(verification.get("unsupportedCount", 0)),
        "unsupportedNumericClaimCount": int(verification.get("numericUnsupportedCount", 0)),
        "citationCoverage": float(verification.get("citationCoverage", 0.0)),
        "errors": errors,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if count == 0:
        return {
            "caseCount": 0,
            "failedRowCount": 0,
            "missingRequiredEvidenceCount": 0,
            "schemaValidRate": 0,
            "unsupportedClaimRate": 1,
            "unsupportedNumericClaimRate": 1,
            "citationCoverage": 0,
            "correctTriageRate": 0,
            "rejectedIdeaResurfacingRate": 0,
            "analystAcceptanceRate": None,
        }
    known_rejects = [row for row in rows if row.get("knownReject")]
    accepted_rows = [row for row in rows if row.get("analystAccepted") is not None]
    return {
        "caseCount": count,
        "failedRowCount": sum(1 for row in rows if row.get("status") != "ok"),
        "missingRequiredEvidenceCount": sum(len(row.get("missingRequiredEvidence", [])) for row in rows),
        "schemaValidRate": _rate(rows, lambda row: bool(row.get("schemaValid"))),
        "unsupportedClaimRate": sum(int(row.get("unsupportedClaimCount", 0)) for row in rows) / count,
        "unsupportedNumericClaimRate": sum(int(row.get("unsupportedNumericClaimCount", 0)) for row in rows) / count,
        "citationCoverage": sum(float(row.get("citationCoverage", 0.0)) for row in rows) / count,
        "correctTriageRate": _rate(rows, lambda row: bool(row.get("correctTriageState"))),
        "rejectedIdeaResurfacingRate": _rate(known_rejects, lambda row: bool(row.get("rejectedIdeaResurfaced"))) if known_rejects else 0,
        "analystAcceptanceRate": _rate(accepted_rows, lambda row: bool(row.get("analystAccepted"))) if accepted_rows else None,
    }


def _status(metrics: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    if any(row.get("status") != "ok" for row in rows):
        return "fail"
    if int(metrics.get("missingRequiredEvidenceCount", 0)) != 0:
        return "fail"
    if metrics.get("schemaValidRate") != 1:
        return "fail"
    if metrics.get("unsupportedNumericClaimRate") != 0:
        return "fail"
    if float(metrics.get("citationCoverage", 0)) < 0.95:
        return "fail"
    if metrics.get("rejectedIdeaResurfacingRate") != 0:
        return "fail"
    return "ok"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Eval suite not found: {path}")
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{index}: expected JSON object")
        rows.append(item)
    return rows


def _suite_path(suite: str, root: Path) -> Path:
    raw = Path(suite)
    if raw.suffix:
        return _resolve(raw, root)
    return root / "evals" / f"{suite}.jsonl"


def _rate(rows: list[dict[str, Any]], predicate: Any) -> float:
    return sum(1 for row in rows if predicate(row)) / len(rows) if rows else 0.0


def _generated_run_id(name: str) -> str:
    stamp = datetime.now(UTC).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{name}"


def _resolve(path: str | Path, cwd: Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = cwd / resolved
    return resolved.resolve()
