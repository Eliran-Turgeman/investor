from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audit import AuditLedger, file_hash, stable_hash
from ..utils import normalize_ticker, read_json, utc_now_iso, write_json


APPROVAL_STATES = ("analyst_approved", "analyst_rejected", "needs_more_evidence")


def approve_candidate(
    *,
    ticker: str,
    state: str,
    reason: str,
    reviewer: str,
    portfolio_dir: str | Path,
    cwd: str | Path = ".",
    run_id: str = "manual-approval",
) -> dict[str, Any]:
    ticker = normalize_ticker(ticker)
    if state not in APPROVAL_STATES:
        raise ValueError(f"approval state must be one of: {', '.join(APPROVAL_STATES)}")
    if not reason.strip():
        raise ValueError("approval reason cannot be empty")
    reviewer = reviewer.strip() or "analyst"
    root = Path(cwd).resolve()
    portfolio = _resolve(portfolio_dir, root)
    queue_path = portfolio / "candidates.json"
    queue = read_json(queue_path, None)
    if not isinstance(queue, dict):
        raise FileNotFoundError(f"Missing candidate queue: {queue_path}")
    candidate = next(
        (
            item
            for item in queue.get("candidates", [])
            if isinstance(item, dict) and str(item.get("ticker", "")).upper() == ticker
        ),
        None,
    )
    if candidate is None:
        raise ValueError(f"Candidate not found: {ticker}")
    now = utc_now_iso()
    before_state = str(candidate.get("state") or "")
    review_path = _artifact_path(candidate, "agentReviewPath", portfolio / "agent_reviews" / f"{ticker}.json", root)
    brief_path = _artifact_path(candidate, "agentBriefPath", portfolio / "agent_briefs" / f"{ticker}.md", root)
    review = _read_optional_review(review_path)
    if state == "analyst_approved":
        _require_approval_evidence(review_path=review_path, brief_path=brief_path, review=review)
    payload = {
        "schemaVersion": "1.0",
        "ticker": ticker,
        "state": state,
        "reason": reason,
        "reviewer": reviewer,
        "generatedAt": now,
        "agentSuggestedState": candidate.get("agentSuggestedState"),
        "agentReviewPath": str(review_path),
        "agentBriefPath": str(brief_path),
    }
    path = portfolio / "approvals" / f"{ticker}.{now.replace(':', '').replace('-', '')}.json"
    payload["approvalPath"] = str(path)
    candidate["state"] = state
    candidate["analystState"] = state
    candidate["analystReviewer"] = reviewer
    candidate["analystReason"] = reason
    candidate["analystApprovedAt"] = now
    candidate["approvalPath"] = str(path)
    candidate["lastUpdatedAt"] = now
    queue["updatedAt"] = now
    source_hashes = _approval_source_hashes(
        candidate=candidate,
        review_path=review_path,
        brief_path=brief_path,
        review=review,
    )
    payload["sourceHashes"] = source_hashes
    write_json(path, payload)
    write_json(queue_path, queue)
    AuditLedger(portfolio / "audit.db").record_approval(
        ticker=ticker,
        reviewer=reviewer,
        state=state,
        reason=reason,
        source_hashes=source_hashes,
        path=str(path),
    )
    AuditLedger(portfolio / "audit.db").record_candidate_event(
        run_id=run_id,
        ticker=ticker,
        event_type="analyst_approval",
        before_state=before_state,
        after_state=str(candidate.get("state") or ""),
        payload=payload,
    )
    return payload


def _resolve(path: str | Path, cwd: Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = cwd / resolved
    return resolved.resolve()


def _artifact_path(candidate: dict[str, Any], key: str, default: Path, cwd: Path) -> Path:
    raw = candidate.get(key) or default
    return _resolve(str(raw), cwd)


def _read_optional_review(path: Path) -> dict[str, Any]:
    data = read_json(path, None)
    return data if isinstance(data, dict) else {}


def _require_approval_evidence(*, review_path: Path, brief_path: Path, review: dict[str, Any]) -> None:
    if not review_path.is_file():
        raise FileNotFoundError(f"analyst_approved requires an existing agent review: {review_path}")
    if not brief_path.is_file():
        raise FileNotFoundError(f"analyst_approved requires an existing agent brief: {brief_path}")
    verification = review.get("claimVerification")
    if not isinstance(verification, dict):
        raise ValueError("analyst_approved requires claimVerification on the agent review")
    try:
        unsupported_count = int(verification.get("unsupportedCount"))
    except (TypeError, ValueError):
        raise ValueError("analyst_approved requires numeric claimVerification.unsupportedCount") from None
    if unsupported_count != 0:
        raise ValueError("analyst_approved requires clean claimVerification unsupportedCount==0")


def _approval_source_hashes(
    *,
    candidate: dict[str, Any],
    review_path: Path,
    brief_path: Path,
    review: dict[str, Any],
) -> dict[str, str]:
    return {
        "candidates": stable_hash(_candidate_hash_payload(candidate)),
        "agentReview": file_hash(review_path),
        "agentBrief": file_hash(brief_path),
        "claimVerification": stable_hash(review.get("claimVerification", {})),
    }


def _candidate_hash_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "approvalSourceHashes"}
