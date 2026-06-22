from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .schemas import structured_output_format


DEFAULT_OPENAI_MODEL = "gpt-5.5"


@dataclass(slots=True)
class TokenUsage:
    inputTokens: int = 0
    outputTokens: int = 0
    totalTokens: int = 0
    cachedInputTokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.inputTokens += other.inputTokens
        self.outputTokens += other.outputTokens
        self.totalTokens += other.totalTokens
        self.cachedInputTokens += other.cachedInputTokens

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class AgentLlmResponse:
    content: dict[str, Any]
    rawText: str
    model: str
    provider: str
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "rawText": self.rawText,
            "model": self.model,
            "provider": self.provider,
            "usage": self.usage.to_dict(),
        }


class AgentLlmClient(Protocol):
    provider: str
    model: str

    def complete_json(
        self,
        *,
        agent_name: str,
        instructions: str,
        input_text: str,
        schema_hint: dict[str, Any],
    ) -> AgentLlmResponse:
        ...


class DeterministicDryRunClient:
    provider = "dry-run"

    def __init__(self, model: str = "dry-run") -> None:
        self.model = model

    def complete_json(
        self,
        *,
        agent_name: str,
        instructions: str,
        input_text: str,
        schema_hint: dict[str, Any],
    ) -> AgentLlmResponse:
        content = {
            "agent": agent_name,
            "verdict": "needs_review",
            "summary": "Dry-run provider did not call an LLM; use an LLM provider for substantive analysis.",
            "claims": [],
            "risks": ["No LLM-backed assessment was performed."],
            "missingEvidence": ["Run with provider=openai for agent analysis."],
            "nextActions": ["Configure OPENAI_API_KEY and rerun the agent harness."],
            "confidence": 0.0,
        }
        if agent_name == "committee_chair":
            content.update(
                {
                    "suggestedState": "defer",
                    "promotionRationale": "Dry-run output cannot support watchlist promotion.",
                    "judgmentSummary": "No-token dry run completed the workflow only.",
                    "claims": [],
                    "keyRisks": ["No LLM-backed assessment was performed."],
                }
            )
            content.pop("verdict", None)
            content.pop("summary", None)
            content.pop("risks", None)
        return AgentLlmResponse(
            content=content,
            rawText=json.dumps(content, sort_keys=True),
            model=self.model,
            provider=self.provider,
            usage=TokenUsage(),
        )


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
        reasoning_effort: str = "low",
        verbosity: str = "low",
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for provider=openai.")

    def complete_json(
        self,
        *,
        agent_name: str,
        instructions: str,
        input_text: str,
        schema_hint: dict[str, Any],
    ) -> AgentLlmResponse:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": _agent_input(agent_name, input_text, schema_hint),
            "reasoning": {"effort": self.reasoning_effort},
            "text": {
                "verbosity": self.verbosity,
                "format": structured_output_format(agent_name),
            },
        }
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"OpenAI Responses API request failed ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"OpenAI Responses API request failed: {exc}") from exc

        raw_text = _extract_output_text(response_data)
        parsed = _parse_json_object(raw_text)
        return AgentLlmResponse(
            content=parsed,
            rawText=raw_text,
            model=str(response_data.get("model") or self.model),
            provider=self.provider,
            usage=_usage_from_response(response_data),
        )


def _agent_input(agent_name: str, input_text: str, schema_hint: dict[str, Any]) -> str:
    return (
        f"Agent role: {agent_name}\n\n"
        "Return one JSON object only. Do not include markdown fences.\n"
        "Schema hint:\n"
        f"{json.dumps(schema_hint, indent=2, sort_keys=True)}\n\n"
        "Evidence packet:\n"
        f"{input_text}"
    )


def _extract_output_text(response_data: dict[str, Any]) -> str:
    direct = response_data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in response_data.get("output", []) if isinstance(response_data.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError("OpenAI response did not contain output text.")
    return text


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response was not valid JSON: {exc}: {raw_text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object.")
    return parsed


def _usage_from_response(response_data: dict[str, Any]) -> TokenUsage:
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        return TokenUsage()
    input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    cached = _int(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else 0
    return TokenUsage(
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        totalTokens=total_tokens,
        cachedInputTokens=cached,
    )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
