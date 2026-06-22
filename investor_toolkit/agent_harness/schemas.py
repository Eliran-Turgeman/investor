from __future__ import annotations

from typing import Any

from ..discovery.schemas import SchemaValidationError, validate_json_schema


SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "institutional-pilot-v1"

ROLE_VERDICTS = ("pass", "concern", "block", "needs_review")
CHAIR_STATES = ("research_more", "defer", "reject", "promote_candidate")
CLAIM_TYPES = ("numeric", "factual")


CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claimType": {"type": "string", "enum": list(CLAIM_TYPES)},
        "statement": {"type": "string"},
        "sourcePath": {"type": "string"},
        "uri": {"type": "string"},
        "metric": {"type": "string"},
        "value": {"type": ["number", "null"]},
    },
    "required": ["claimType", "statement", "sourcePath", "uri", "metric", "value"],
    "additionalProperties": False,
}

ROLE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "verdict": {"type": "string", "enum": list(ROLE_VERDICTS)},
        "summary": {"type": "string"},
        "claims": {"type": "array", "items": CLAIM_SCHEMA},
        "risks": {"type": "array", "items": {"type": "string"}},
        "missingEvidence": {"type": "array", "items": {"type": "string"}},
        "nextActions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["agent", "verdict", "summary", "claims", "risks", "missingEvidence", "nextActions", "confidence"],
    "additionalProperties": False,
}

CHAIR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "suggestedState": {"type": "string", "enum": list(CHAIR_STATES)},
        "judgmentSummary": {"type": "string"},
        "promotionRationale": {"type": "string"},
        "claims": {"type": "array", "items": CLAIM_SCHEMA},
        "keyRisks": {"type": "array", "items": {"type": "string"}},
        "missingEvidence": {"type": "array", "items": {"type": "string"}},
        "nextActions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "agent",
        "suggestedState",
        "judgmentSummary",
        "promotionRationale",
        "claims",
        "keyRisks",
        "missingEvidence",
        "nextActions",
        "confidence",
    ],
    "additionalProperties": False,
}


def schema_for_agent(agent_name: str) -> dict[str, Any]:
    return CHAIR_RESPONSE_SCHEMA if agent_name == "committee_chair" else ROLE_RESPONSE_SCHEMA


def validate_agent_response(agent_name: str, data: dict[str, Any]) -> None:
    validate_json_schema(data, schema_for_agent(agent_name))
    if str(data.get("agent")) != agent_name:
        raise SchemaValidationError(f"$.agent must be {agent_name}")


def structured_output_format(agent_name: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": f"{agent_name}_response",
        "schema": schema_for_agent(agent_name),
        "strict": True,
    }


def blocked_agent_content(agent_name: str, message: str) -> dict[str, Any]:
    if agent_name == "committee_chair":
        return {
            "agent": agent_name,
            "suggestedState": "research_more",
            "judgmentSummary": f"Blocked: {message}",
            "promotionRationale": "Blocked reviews cannot support promotion.",
            "claims": [],
            "keyRisks": [message],
            "missingEvidence": [message],
            "nextActions": ["Repair schema or evidence before using this review."],
            "confidence": 0.0,
        }
    return {
        "agent": agent_name,
        "verdict": "block",
        "summary": f"Blocked: {message}",
        "claims": [],
        "risks": [message],
        "missingEvidence": [message],
        "nextActions": ["Repair schema or evidence before using this review."],
        "confidence": 0.0,
    }
