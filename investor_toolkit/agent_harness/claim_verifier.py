from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils import read_json, write_json


SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
CITATION_MISSING = "citation_missing"
SOURCE_MISSING = "source_missing"
METRIC_MISMATCH = "metric_mismatch"
STALE_DATA = "stale_data"
NOT_MACHINE_VERIFIABLE = "not_machine_verifiable"

UNSUPPORTED_STATUSES = {
    UNSUPPORTED,
    CITATION_MISSING,
    SOURCE_MISSING,
    METRIC_MISMATCH,
    STALE_DATA,
    NOT_MACHINE_VERIFIABLE,
}

STATUS_PRIORITY = (
    CITATION_MISSING,
    SOURCE_MISSING,
    METRIC_MISMATCH,
    STALE_DATA,
    NOT_MACHINE_VERIFIABLE,
    UNSUPPORTED,
)


@dataclass(slots=True)
class ClaimVerificationSummary:
    ticker: str
    generatedAt: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def unsupportedCount(self) -> int:
        return sum(1 for item in self.checks if _is_unsupported_status(str(item.get("status") or "")))

    @property
    def numericUnsupportedCount(self) -> int:
        return sum(
            1
            for item in self.checks
            if _is_unsupported_status(str(item.get("status") or ""))
            and item.get("claim", {}).get("claimType") == "numeric"
        )

    @property
    def citationCoverage(self) -> float:
        if not self.checks:
            return 1.0
        cited = sum(1 for item in self.checks if item.get("claim", {}).get("sourcePath") or item.get("claim", {}).get("uri"))
        return cited / len(self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "generatedAt": self.generatedAt,
            "unsupportedCount": self.unsupportedCount,
            "numericUnsupportedCount": self.numericUnsupportedCount,
            "citationCoverage": self.citationCoverage,
            "unsupportedStatusCounts": _status_counts(self.checks),
            "checks": self.checks,
        }


@dataclass(slots=True)
class ResolvedSource:
    source_path: str
    uri: str
    path: Path | None = None
    exists: bool = False
    structured: Any = None
    text: str = ""
    machine_readable: bool = False


def verify_review_claims(review: dict[str, Any], cwd: str | Path = ".") -> dict[str, Any]:
    from ..utils import utc_now_iso

    ticker = str(review.get("ticker") or "")
    candidate = review.get("deterministicCandidate", {}) if isinstance(review.get("deterministicCandidate"), dict) else {}
    checks = []
    for agent_name, claim in _iter_claims(review):
        check = _verify_claim(agent_name, claim, candidate=candidate, cwd=Path(cwd))
        checks.append(check)
    checks.extend(_data_quality_checks(candidate, cwd=Path(cwd)))
    summary = ClaimVerificationSummary(ticker=ticker, generatedAt=utc_now_iso(), checks=checks).to_dict()
    return summary


def verify_review_file(path: str | Path, cwd: str | Path = ".") -> dict[str, Any]:
    review_path = Path(path)
    review = read_json(review_path, None)
    if not isinstance(review, dict):
        raise ValueError(f"Agent review must contain a JSON object: {review_path}")
    summary = verify_review_claims(review, cwd=cwd)
    review["claimVerification"] = summary
    write_json(review_path, review)
    return summary


def _iter_claims(review: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for output in review.get("roleOutputs", []):
        if not isinstance(output, dict):
            continue
        agent_name = str(output.get("agent") or output.get("content", {}).get("agent") or "")
        content = output.get("content", {}) if isinstance(output.get("content"), dict) else {}
        for claim in content.get("claims", []) if isinstance(content.get("claims"), list) else []:
            if isinstance(claim, dict):
                rows.append((agent_name, claim))
    chair = review.get("committeeChair", {})
    chair_content = chair.get("content", {}) if isinstance(chair, dict) and isinstance(chair.get("content"), dict) else {}
    for claim in chair_content.get("claims", []) if isinstance(chair_content.get("claims"), list) else []:
        if isinstance(claim, dict):
            rows.append(("committee_chair", claim))
    return rows


def _verify_claim(agent_name: str, claim: dict[str, Any], candidate: dict[str, Any], cwd: Path) -> dict[str, Any]:
    source_path = str(claim.get("sourcePath") or "")
    uri = str(claim.get("uri") or "")
    reason_codes: list[str] = []
    reasons: list[str] = []
    if not source_path and not uri:
        _add_reason(reason_codes, reasons, CITATION_MISSING, "missing source citation")
    source = _resolve_source(source_path=source_path, uri=uri, candidate=candidate, cwd=cwd)
    if (source_path or uri) and not source.exists:
        _add_reason(reason_codes, reasons, SOURCE_MISSING, "cited source is missing or could not be resolved locally")
    claim_type = str(claim.get("claimType") or "")
    if claim_type == "numeric":
        value = _num(claim.get("value"))
        metric = str(claim.get("metric") or "")
        if value is None:
            _add_reason(reason_codes, reasons, METRIC_MISMATCH, "numeric claim is missing numeric value")
        if not metric:
            _add_reason(reason_codes, reasons, METRIC_MISMATCH, "numeric claim is missing metric")
        if source.exists and value is not None and metric:
            if not source.machine_readable:
                _add_reason(
                    reason_codes,
                    reasons,
                    NOT_MACHINE_VERIFIABLE,
                    "numeric claim cites a source that is not machine-verifiable JSON/JSONL",
                )
            else:
                metric_values = _values_for_metric(source.structured, metric)
                if not metric_values:
                    _add_reason(reason_codes, reasons, METRIC_MISMATCH, f"metric {metric!r} not found in cited source")
                elif not _matches_any_number(value, metric_values):
                    _add_reason(
                        reason_codes,
                        reasons,
                        METRIC_MISMATCH,
                        "numeric value not found for cited metric "
                        f"{metric!r}; cited source values are {_format_numbers(metric_values)}",
                    )
    elif claim_type == "factual":
        if not source_path and not uri:
            _add_reason(reason_codes, reasons, CITATION_MISSING, "factual claim has no citation")
    else:
        _add_reason(reason_codes, reasons, NOT_MACHINE_VERIFIABLE, "unknown claimType")
    status = _primary_status(reason_codes)
    return {
        "agent": agent_name,
        "claim": claim,
        "status": status,
        "reasonCode": status,
        "reasonCodes": reason_codes,
        "reason": "; ".join(reasons) if reasons else "claim has citation and deterministic support",
        "sourcePath": source_path,
        "uri": uri,
        "resolvedSourcePath": str(source.path) if source.path else "",
        "sourceExists": source.exists,
        "machineVerifiable": source.machine_readable,
    }


def _data_quality_checks(candidate: dict[str, Any], cwd: Path) -> list[dict[str, Any]]:
    checks = []
    blocking_terms = (
        "stale price",
        "missing prices",
        "missing filing",
        "missing metrics",
        "failed refresh",
        "invalid assumption",
        "latest price date is missing or invalid",
    )
    for warning in candidate.get("warnings", []):
        text = str(warning)
        if not any(term in text.lower() for term in blocking_terms):
            continue
        source_path, uri = _warning_source(candidate, text)
        resolved = _resolve_path(source_path, cwd) if source_path else None
        checks.append(
            {
                "agent": "deterministic_data_quality",
                "claim": {
                    "claimType": "data_quality",
                    "statement": text,
                    "sourcePath": source_path,
                    "uri": uri,
                    "metric": "",
                    "value": None,
                },
                "status": STALE_DATA,
                "reasonCode": STALE_DATA,
                "reasonCodes": [STALE_DATA],
                "reason": "deterministic data warning blocks institutional promotion review",
                "sourcePath": source_path,
                "uri": uri,
                "resolvedSourcePath": str(resolved) if resolved else "",
                "sourceExists": bool(resolved and resolved.exists()),
                "machineVerifiable": False,
            }
        )
    return checks


def _warning_source(candidate: dict[str, Any], warning: str) -> tuple[str, str]:
    warning_lower = warning.lower()
    wanted_kind = ""
    if "price" in warning_lower:
        wanted_kind = "prices"
    elif "filing" in warning_lower:
        wanted_kind = "filings"
    elif "metrics" in warning_lower:
        wanted_kind = "metrics"
    for artifact in candidate.get("artifactRefs", []):
        if not isinstance(artifact, dict):
            continue
        kind = str(artifact.get("kind") or "")
        if kind == wanted_kind:
            return str(artifact.get("path") or ""), str(artifact.get("uri") or "")
    return "", ""


def _resolve_source(source_path: str, uri: str, candidate: dict[str, Any], cwd: Path) -> ResolvedSource:
    path = _resolve_path(source_path, cwd) if source_path else None
    if path is None or not path.exists():
        artifact_path = _source_path_for_uri(uri, candidate, cwd)
        if artifact_path is not None:
            path = artifact_path
    resolved = ResolvedSource(source_path=source_path, uri=uri, path=path)
    if path is None or not path.exists() or not path.is_file():
        return resolved
    resolved.exists = True
    try:
        resolved.text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        resolved.text = ""
        return resolved
    parsed = _parse_machine_readable(path, resolved.text)
    if parsed is not None:
        resolved.structured = parsed
        resolved.machine_readable = True
    return resolved


def _source_path_for_uri(uri: str, candidate: dict[str, Any], cwd: Path) -> Path | None:
    if not uri:
        return None
    clean_uri = uri.split("#", 1)[0]
    for artifact in candidate.get("artifactRefs", []):
        if not isinstance(artifact, dict):
            continue
        artifact_uri = str(artifact.get("uri") or "").split("#", 1)[0]
        if artifact_uri == clean_uri and artifact.get("path"):
            return _resolve_path(str(artifact["path"]), cwd)
    if clean_uri.startswith("investor://valuation/"):
        wanted_stem = clean_uri.rstrip("/").rsplit("/", 1)[-1]
        calculations = candidate.get("deterministicCalculations", {})
        outputs = calculations.get("valuationOutputs", []) if isinstance(calculations, dict) else []
        for output in outputs:
            if not isinstance(output, dict) or not output.get("path"):
                continue
            path = _resolve_path(str(output["path"]), cwd)
            if path.stem == wanted_stem:
                return path
        ticker = clean_uri.rstrip("/").split("/")[-2] if "/" in clean_uri else ""
        if ticker and wanted_stem:
            return cwd / "valuations" / f"{wanted_stem}.json"
    if clean_uri.startswith("investor://company/"):
        parts = clean_uri.split("/")
        if len(parts) >= 5:
            ticker = parts[3]
            kind = parts[4]
            inferred = {
                "company": cwd / "research" / ticker / "company.json",
                "filings": cwd / "research" / ticker / "filings" / "metadata" / "filings.json",
                "metrics-json": cwd / "research" / ticker / "metrics" / "metrics.json",
                "financials": cwd / "research" / ticker / "data" / "financials.json",
                "prices": cwd / "research" / ticker / "data" / "prices.json",
            }.get(kind)
            if inferred is not None:
                return inferred
    return None


def _parse_machine_readable(path: Path, text: str) -> Any:
    if not text:
        return None
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    if suffix == ".jsonl":
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                return None
        return rows
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def _values_for_metric(data: Any, metric: str) -> list[float]:
    values: list[float] = []
    dotted = _value_at_dotted_path(data, metric)
    parsed = _num(dotted)
    if parsed is not None:
        values.append(parsed)
    _walk_metric_values(data, metric, values)
    unique: list[float] = []
    for value in values:
        if not _matches_any_number(value, unique):
            unique.append(value)
    return unique


def _walk_metric_values(data: Any, metric: str, output: list[float]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key == metric:
                parsed = _num(value)
                if parsed is not None:
                    output.append(parsed)
            _walk_metric_values(value, metric, output)
    elif isinstance(data, list):
        for item in data:
            _walk_metric_values(item, metric, output)


def _value_at_dotted_path(data: Any, metric: str) -> Any:
    if not metric or "." not in metric:
        return None
    current = data
    for part in metric.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _matches_any_number(value: float, numbers: list[float]) -> bool:
    for known in numbers:
        tolerance = max(0.0001, abs(known) * 0.0001)
        if abs(value - known) <= tolerance:
            return True
    return False


def _format_numbers(values: list[float]) -> str:
    if len(values) <= 5:
        return ", ".join(f"{value:g}" for value in values)
    return ", ".join(f"{value:g}" for value in values[:5]) + f", ... ({len(values)} values)"


def _add_reason(codes: list[str], reasons: list[str], code: str, reason: str) -> None:
    if code not in codes:
        codes.append(code)
    reasons.append(reason)


def _primary_status(reason_codes: list[str]) -> str:
    if not reason_codes:
        return SUPPORTED
    for status in STATUS_PRIORITY:
        if status in reason_codes:
            return status
    return UNSUPPORTED


def _status_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        status = str(check.get("status") or "")
        if not _is_unsupported_status(status):
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _is_unsupported_status(status: str) -> bool:
    return status in UNSUPPORTED_STATUSES or (bool(status) and status != SUPPORTED)


def _resolve_path(path: str | Path, cwd: Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = cwd / resolved
    return resolved.resolve()


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
