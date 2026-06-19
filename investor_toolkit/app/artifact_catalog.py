from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import normalize_ticker, utc_now_iso
from .context import AppContext
from .schemas import SCHEMA_VERSION, ArtifactReference


PROFILE_STATUS_URI = "investor://profile/status"


@dataclass(slots=True)
class ArtifactContent:
    uri: str
    mimeType: str
    text: str
    path: str

    def to_resource_content(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "mimeType": self.mimeType,
            "text": self.text,
        }


class ArtifactCatalog:
    def __init__(self, context: AppContext) -> None:
        self.context = context

    def portfolio_artifacts(self) -> list[ArtifactReference]:
        base = self.context.portfolio_dir
        return [
            self._ref("investor://portfolio/holdings", "holdings.json", base / "holdings.json", "portfolio"),
            self._ref("investor://portfolio/watchlist", "watchlist.json", base / "watchlist.json", "portfolio"),
            self._ref(
                "investor://portfolio/assumption-overrides",
                "assumption_overrides.json",
                base / "assumption_overrides.json",
                "portfolio",
            ),
            self._ref("investor://portfolio/rules", "rules.json", base / "rules.json", "portfolio"),
            self._ref("investor://portfolio/signals", "signals.json", base / "signals.json", "portfolio"),
            self._ref(
                "investor://portfolio/valuation-audit",
                "valuation_audit.json",
                base / "valuation_audit.json",
                "portfolio",
            ),
        ]

    def profile_artifacts(self) -> list[ArtifactReference]:
        base = self.context.portfolio_dir
        return [
            self._ref(
                "investor://profile/policy",
                "investor_policy.md",
                base / "investor_policy.md",
                "profile",
                mime_type="text/markdown",
                description="Investor policy and assistant behavior guardrails.",
            ),
            self._ref(
                "investor://profile/goals",
                "goals.json",
                base / "goals.json",
                "profile",
                description="Portfolio goals, benchmark, horizon, and optimization priorities.",
            ),
            self._ref(
                "investor://profile/preferences",
                "preferences.json",
                base / "preferences.json",
                "profile",
                description="Investing style, circle of competence, and assistant challenge style.",
            ),
            self._ref(
                "investor://profile/position-sizing",
                "position_sizing.json",
                base / "position_sizing.json",
                "profile",
                description="Position sizing policy and active portfolio concentration rules.",
            ),
            self._ref(
                "investor://profile/valuation-policy",
                "valuation_policy.json",
                base / "valuation_policy.json",
                "profile",
                description="Margin of safety and valuation method policy.",
            ),
            self._ref(
                "investor://profile/risk-policy",
                "risk_policy.json",
                base / "risk_policy.json",
                "profile",
                description="Risk preferences and higher-risk opportunity handling.",
            ),
            self._ref(
                "investor://profile/decision-process",
                "decision_process.json",
                base / "decision_process.json",
                "profile",
                description="Candidate evaluation, monthly workflow, and rejection rules.",
            ),
            self._ref(
                "investor://profile/operating-preferences",
                "operating_preferences.json",
                base / "operating_preferences.json",
                "profile",
                description="Research cadence, output depth, and workflow preferences.",
            ),
            self._ref(
                "investor://profile/external-exposure",
                "external_exposure.json",
                base / "external_exposure.json",
                "profile",
                description="External RSUs and other portfolios tracked outside active portfolio construction.",
            ),
            self._ref(
                "investor://profile/onboarding-notes",
                "onboarding_notes.md",
                base / "onboarding_notes.md",
                "profile",
                mime_type="text/markdown",
                description="Onboarding design notes and inferred-default policy.",
            ),
            self._ref(
                "investor://profile/thesis-template",
                "thesis_template.md",
                base / "thesis_template.md",
                "profile",
                mime_type="text/markdown",
                description="Agent-owned thesis memo template.",
            ),
            self._ref(
                "investor://profile/bear-case-template",
                "bear_case_template.md",
                base / "bear_case_template.md",
                "profile",
                mime_type="text/markdown",
                description="Agent-owned bear-case memo template.",
            ),
        ]

    def profile_status_resource(self) -> ArtifactReference:
        return ArtifactReference(
            uri=PROFILE_STATUS_URI,
            name="profile_status.json",
            path=str((self.context.portfolio_dir / "profile_status.virtual.json").resolve()),
            kind="profile-status",
            mimeType="application/json",
            description="Virtual profile onboarding status. Exists even before onboarding files are written.",
            exists=True,
        )

    def profile_status(self) -> dict[str, Any]:
        artifacts = self.profile_artifacts()
        existing = [ref for ref in artifacts if ref.exists]
        missing = [ref for ref in artifacts if not ref.exists]
        onboarding_required = bool(missing)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now_iso(),
            "profileExists": not onboarding_required,
            "onboardingRequired": onboarding_required,
            "portfolioDir": str(self.context.portfolio_dir),
            "statusResource": PROFILE_STATUS_URI,
            "requiredProfileArtifacts": [ref.to_dict() for ref in artifacts],
            "existingProfileArtifacts": [ref.to_dict() for ref in existing],
            "missingProfileArtifacts": [ref.to_dict() for ref in missing],
            "nextActions": _profile_status_next_actions(onboarding_required),
        }

    def company_artifacts(self, ticker: str) -> list[ArtifactReference]:
        ticker = normalize_ticker(ticker)
        base = self.context.research_root / ticker
        refs = [
            self._ref(f"investor://company/{ticker}/company", "company.json", base / "company.json", "company"),
            self._ref(
                f"investor://company/{ticker}/filings",
                "filings.json",
                base / "filings" / "metadata" / "filings.json",
                "filings",
            ),
            self._ref(
                f"investor://company/{ticker}/submissions",
                "submissions.json",
                base / "filings" / "metadata" / "submissions.json",
                "filings",
            ),
            self._ref(
                f"investor://company/{ticker}/metrics-json",
                "metrics.json",
                base / "metrics" / "metrics.json",
                "metrics",
            ),
            self._ref(
                f"investor://company/{ticker}/metrics-md",
                "metrics.md",
                base / "metrics" / "metrics.md",
                "metrics",
                mime_type="text/markdown",
            ),
            self._ref(
                f"investor://company/{ticker}/financials",
                "financials.json",
                base / "data" / "financials.json",
                "financials",
            ),
            self._ref(
                f"investor://company/{ticker}/prices",
                "prices.json",
                base / "data" / "prices.json",
                "market-data",
            ),
            self._ref(
                f"investor://company/{ticker}/filing-index",
                "filing_chunks.jsonl",
                base / "index" / "filing_chunks.jsonl",
                "index",
                mime_type="application/jsonl",
            ),
        ]
        refs.extend(self._extracted_section_refs(ticker, base))
        return refs

    def all_existing_resources(self) -> list[ArtifactReference]:
        resources = [self.profile_status_resource()]
        resources.extend(ref for ref in self.portfolio_artifacts() if ref.exists)
        resources.extend(ref for ref in self.profile_artifacts() if ref.exists)
        if self.context.research_root.exists():
            for child in sorted(self.context.research_root.iterdir()):
                if child.is_dir():
                    try:
                        resources.extend(ref for ref in self.company_artifacts(child.name) if ref.exists)
                    except ValueError:
                        continue
        return resources

    def read(self, uri: str) -> ArtifactContent:
        ref = self.resolve(uri)
        if ref.uri == PROFILE_STATUS_URI:
            text = json.dumps(self.profile_status(), indent=2, sort_keys=True, allow_nan=False) + "\n"
            return ArtifactContent(uri=ref.uri, mimeType=ref.mimeType, text=text, path=ref.path)
        path = Path(ref.path)
        if not ref.exists or not path.exists():
            raise FileNotFoundError(f"Artifact does not exist: {uri}")
        if not self._is_safe_workspace_path(path):
            raise ValueError(f"Artifact path escapes workspace: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if ref.mimeType == "application/json":
            parsed = json.loads(text)
            text = json.dumps(parsed, indent=2, sort_keys=True, allow_nan=False) + "\n"
        return ArtifactContent(uri=ref.uri, mimeType=ref.mimeType, text=text, path=ref.path)

    def resolve(self, uri: str) -> ArtifactReference:
        for ref in self.all_existing_resources():
            if ref.uri == uri:
                return ref
        if uri.startswith("investor://portfolio/"):
            for ref in self.portfolio_artifacts():
                if ref.uri == uri:
                    return ref
        if uri.startswith("investor://profile/"):
            if uri == PROFILE_STATUS_URI:
                return self.profile_status_resource()
            for ref in self.profile_artifacts():
                if ref.uri == uri:
                    return ref
        if uri.startswith("investor://company/"):
            parts = uri.split("/")
            if len(parts) >= 4:
                ticker = parts[3]
                for ref in self.company_artifacts(ticker):
                    if ref.uri == uri:
                        return ref
        raise FileNotFoundError(f"Unknown artifact URI: {uri}")

    def _extracted_section_refs(self, ticker: str, base: Path) -> list[ArtifactReference]:
        extracted_root = base / "extracted"
        refs: list[ArtifactReference] = []
        if not extracted_root.exists():
            return refs
        for path in sorted(extracted_root.rglob("*.md")):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(extracted_root)
            except ValueError:
                continue
            uri_path = "/".join(_uri_part(part) for part in relative.with_suffix("").parts)
            refs.append(
                self._ref(
                    f"investor://company/{ticker}/extracted/{uri_path}",
                    relative.as_posix(),
                    path,
                    "extracted-filing-section",
                    mime_type="text/markdown",
                )
            )
        return refs

    def _ref(
        self,
        uri: str,
        name: str,
        path: Path,
        kind: str,
        mime_type: str = "application/json",
        description: str = "",
    ) -> ArtifactReference:
        resolved = path.resolve()
        return ArtifactReference(
            uri=uri,
            name=name,
            path=str(resolved),
            kind=kind,
            mimeType=mime_type,
            description=description,
            exists=resolved.exists(),
        )

    def _is_safe_workspace_path(self, path: Path) -> bool:
        resolved = path.resolve()
        roots = [
            self.context.workspace_root,
            self.context.research_root,
            self.context.portfolio_dir,
            self.context.assumptions_dir,
            self.context.valuations_dir,
        ]
        for root in roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False


def _uri_part(value: str) -> str:
    return "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "-" for char in value)


def _profile_status_next_actions(onboarding_required: bool) -> list[str]:
    if onboarding_required:
        return [
            "Run init_investor_profile before personalized portfolio review or candidate generation.",
            "Ask only broad onboarding questions; do not run a long questionnaire.",
            "After onboarding, read investor://profile/policy and investor://profile/decision-process.",
        ]
    return [
        "Read investor://profile/policy before personalized portfolio analysis.",
        "Use investor://profile/decision-process and investor://profile/operating-preferences to keep research lightweight.",
    ]
