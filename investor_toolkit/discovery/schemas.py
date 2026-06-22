from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = "1.0"

CANDIDATE_STATES = (
    "discovered",
    "screened",
    "refreshed",
    "briefed",
    "deferred",
    "rejected",
    "promote_candidate",
    "promoted_to_watchlist",
    "agent_reviewed",
    "analyst_approved",
    "analyst_rejected",
    "needs_more_evidence",
)

COMPONENT_SCORE_KEYS = (
    "profile_fit",
    "business_quality",
    "growth_runway",
    "valuation_sanity",
    "balance_sheet",
    "downside_risk",
    "evidence_freshness",
    "portfolio_fit",
)


class SchemaValidationError(ValueError):
    """Raised when a persisted discovery artifact does not match its schema."""


SCORE_VALUE_SCHEMA: dict[str, Any] = {
    "type": ["number", "null"],
    "minimum": 0,
    "maximum": 100,
}

ARTIFACT_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "path": {"type": "string"},
        "uri": {"type": "string"},
        "exists": {"type": "boolean"},
    },
    "required": ["kind", "path", "uri", "exists"],
    "additionalProperties": True,
}

SOURCE_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "value": {},
        "sourcePath": {"type": "string"},
        "uri": {"type": "string"},
    },
    "required": ["label", "sourcePath", "uri"],
    "additionalProperties": True,
}

SOURCE_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "sourceType": {"type": "string"},
        "description": {"type": "string"},
        "surfacedAt": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["name", "sourceType", "surfacedAt"],
    "additionalProperties": True,
}

CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "companyName": {"type": "string"},
        "state": {"type": "string", "enum": list(CANDIDATE_STATES)},
        "firstDiscoveredAt": {"type": "string"},
        "lastSeenAt": {"type": ["string", "null"]},
        "lastUpdatedAt": {"type": "string"},
        "lastRefreshedAt": {"type": ["string", "null"]},
        "lastScoredAt": {"type": ["string", "null"]},
        "lastBriefedAt": {"type": ["string", "null"]},
        "sources": {"type": "array", "items": SOURCE_REF_SCHEMA},
        "seenInRuns": {"type": "array", "items": {"type": "string"}},
        "artifactRefs": {"type": "array", "items": ARTIFACT_REF_SCHEMA},
        "sourceFacts": {"type": "array", "items": SOURCE_FACT_SCHEMA},
        "deterministicCalculations": {"type": "object"},
        "componentScores": {
            "type": "object",
            "properties": {key: SCORE_VALUE_SCHEMA for key in COMPONENT_SCORE_KEYS},
            "additionalProperties": False,
        },
        "totalScore": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "judgmentSummary": {"type": "string"},
        "keyRisks": {"type": "array", "items": {"type": "string"}},
        "missingEvidence": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "nextAction": {"type": "string"},
        "watchlistPromotionCandidate": {"type": "boolean"},
        "watchlistPromotionRationale": {"type": "string"},
    },
    "required": [
        "ticker",
        "companyName",
        "state",
        "firstDiscoveredAt",
        "lastUpdatedAt",
        "sources",
        "seenInRuns",
        "artifactRefs",
        "sourceFacts",
        "deterministicCalculations",
        "componentScores",
        "totalScore",
        "judgmentSummary",
        "keyRisks",
        "missingEvidence",
        "warnings",
        "nextAction",
        "watchlistPromotionCandidate",
        "watchlistPromotionRationale",
    ],
    "additionalProperties": True,
}

CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string", "enum": [SCHEMA_VERSION]},
        "generatedAt": {"type": "string"},
        "updatedAt": {"type": "string"},
        "candidates": {"type": "array", "items": CANDIDATE_SCHEMA},
    },
    "required": ["schemaVersion", "generatedAt", "updatedAt", "candidates"],
    "additionalProperties": False,
}

DISCOVERY_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string", "enum": [SCHEMA_VERSION]},
        "runId": {"type": "string"},
        "generatedAt": {"type": "string"},
        "command": {"type": "string"},
        "screens": {"type": "array", "items": {"type": "object"}},
        "discovered": {"type": "array", "items": {"type": "string"}},
        "updated": {"type": "array", "items": {"type": "string"}},
        "suppressed": {"type": "array", "items": {"type": "string"}},
        "skipped": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "object"},
    },
    "required": [
        "schemaVersion",
        "runId",
        "generatedAt",
        "command",
        "screens",
        "discovered",
        "updated",
        "suppressed",
        "skipped",
        "warnings",
        "artifacts",
    ],
    "additionalProperties": True,
}

TOP_OPPORTUNITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string", "enum": [SCHEMA_VERSION]},
        "generatedAt": {"type": "string"},
        "selection": {"type": "string"},
        "rows": {"type": "array", "items": CANDIDATE_SCHEMA},
    },
    "required": ["schemaVersion", "generatedAt", "selection", "rows"],
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "candidates": CANDIDATES_SCHEMA,
    "discovery_run": DISCOVERY_RUN_SCHEMA,
    "top_opportunities": TOP_OPPORTUNITIES_SCHEMA,
}


def validate_discovery_artifact(name: str, data: Any) -> None:
    if name not in SCHEMAS:
        raise ValueError(f"Unknown discovery artifact schema: {name}")
    validate_json_schema(data, SCHEMAS[name])


def validate_json_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(instance, expected_type):
        raise SchemaValidationError(f"{path} must be {_type_label(expected_type)}")

    if "enum" in schema and instance not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        raise SchemaValidationError(f"{path} must be one of: {allowed}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and instance < minimum:
            raise SchemaValidationError(f"{path} must be >= {minimum}")
        if maximum is not None and instance > maximum:
            raise SchemaValidationError(f"{path} must be <= {maximum}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaValidationError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_json_schema(value, properties[key], child_path)
            elif additional is False:
                raise SchemaValidationError(f"{child_path} is not allowed")
            elif isinstance(additional, dict):
                validate_json_schema(value, additional, child_path)

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < int(min_items):
            raise SchemaValidationError(f"{path} must contain at least {min_items} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                validate_json_schema(item, item_schema, f"{path}[{index}]")


def _matches_type(instance: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(instance, item) for item in expected)
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(float(instance))
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _type_label(expected: str | list[str]) -> str:
    if isinstance(expected, list):
        return " or ".join(expected)
    return expected
