"""LLM-backed multi-agent stock discovery and research harness."""

from .engine import AgentHarness, AgentHarnessPaths
from .approvals import approve_candidate
from .claim_verifier import verify_review_claims, verify_review_file
from .llm import (
    AgentLlmClient,
    AgentLlmResponse,
    DeterministicDryRunClient,
    OpenAIResponsesClient,
    TokenUsage,
)
from .schemas import validate_agent_response

__all__ = [
    "AgentHarness",
    "AgentHarnessPaths",
    "AgentLlmClient",
    "AgentLlmResponse",
    "DeterministicDryRunClient",
    "OpenAIResponsesClient",
    "TokenUsage",
    "approve_candidate",
    "validate_agent_response",
    "verify_review_claims",
    "verify_review_file",
]
