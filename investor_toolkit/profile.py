from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .utils import normalize_ticker, write_json, write_text


SCHEMA_VERSION = "1.0"
DEFAULT_FOCUS_AREAS = ("software", "ai_related_hardware_or_hardware_adjacent_businesses")
DEFAULT_AVOID_AREAS = ("China",)


@dataclass(frozen=True, slots=True)
class ProfileInitRequest:
    portfolio_dir: Path
    today: date
    benchmark: str = "S&P 500"
    horizon_min_years: int = 5
    horizon_max_years: int = 10
    ideas_per_month: int = 3
    required_margin_of_safety: float = 0.30
    max_position_size: float = 0.30
    focus_areas: tuple[str, ...] = DEFAULT_FOCUS_AREAS
    avoid_areas: tuple[str, ...] = DEFAULT_AVOID_AREAS
    external_exposures: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    other_portfolios: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    external_exposure_affects_active_portfolio: bool = False
    overwrite: bool = False


def init_profile(request: ProfileInitRequest) -> dict[str, Any]:
    portfolio_dir = request.portfolio_dir.resolve()
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    today = request.today.isoformat()
    artifacts = _profile_artifact_payloads(request, today)

    written: list[str] = []
    skipped: list[str] = []
    for filename, payload in artifacts.items():
        path = portfolio_dir / filename
        if path.exists() and not request.overwrite:
            skipped.append(str(path))
            continue
        if filename.endswith(".json"):
            write_json(path, payload)
        else:
            write_text(path, str(payload))
        written.append(str(path))

    for folder, readme in _profile_folder_readmes().items():
        path = portfolio_dir / folder / "README.md"
        if path.exists() and not request.overwrite:
            skipped.append(str(path))
            continue
        write_text(path, readme)
        written.append(str(path))

    return {
        "schemaVersion": SCHEMA_VERSION,
        "message": "investor profile initialized",
        "portfolioDir": str(portfolio_dir),
        "writtenCount": len(written),
        "skippedCount": len(skipped),
        "filesWritten": written,
        "filesSkipped": skipped,
        "resources": [
            "investor://profile/policy",
            "investor://profile/goals",
            "investor://profile/preferences",
            "investor://profile/position-sizing",
            "investor://profile/valuation-policy",
            "investor://profile/risk-policy",
            "investor://profile/decision-process",
            "investor://profile/operating-preferences",
            "investor://profile/external-exposure",
            "investor://profile/onboarding-notes",
            "investor://profile/thesis-template",
            "investor://profile/bear-case-template",
        ],
    }


def _profile_artifact_payloads(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    focus_areas = _clean_list(request.focus_areas, DEFAULT_FOCUS_AREAS)
    avoid_areas = _clean_list(request.avoid_areas, DEFAULT_AVOID_AREAS)
    return {
        "investor_policy.md": _policy_md(request, today, focus_areas, avoid_areas),
        "goals.json": _goals(request, today),
        "preferences.json": _preferences(today, focus_areas, avoid_areas),
        "position_sizing.json": _position_sizing(request, today),
        "valuation_policy.json": _valuation_policy(request, today),
        "risk_policy.json": _risk_policy(today, avoid_areas),
        "decision_process.json": _decision_process(request, today),
        "operating_preferences.json": _operating_preferences(request, today),
        "external_exposure.json": _external_exposure(request, today),
        "onboarding_notes.md": _onboarding_notes(request, today),
        "thesis_template.md": _thesis_template(),
        "bear_case_template.md": _bear_case_template(),
    }


def _goals(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "primaryObjective": "outperform_sp500",
        "benchmark": request.benchmark,
        "context": {
            "hasSeparateIndexPortfolio": True,
            "thisPortfolioRole": "Active long-term stock selection portfolio intended to outperform the benchmark.",
            "activePortfolioStartingPoint": "No holdings are assumed. Build the active portfolio deliberately from explicit user input.",
            "externalExposurePolicy": (
                "External exposures are context only and should not affect active portfolio sizing by default."
                if not request.external_exposure_affects_active_portfolio
                else "External exposures may be considered in active portfolio sizing."
            ),
        },
        "timeHorizonYears": {
            "minimum": request.horizon_min_years,
            "maximum": request.horizon_max_years,
        },
        "returnTarget": {
            "type": "not_explicit",
            "description": "Aim as high as reasonably possible while respecting valuation, risk, and evidence quality.",
        },
        "contributionCadence": "monthly",
        "tradingCadence": "low_frequency",
        "optimizationPriorities": [
            "long_term_outperformance",
            "quality_of_business",
            "valuation_discipline",
            "portfolio_fit",
            "evidence_quality",
            "high_signal_idea_flow",
        ],
    }


def _preferences(today: str, focus_areas: tuple[str, ...], avoid_areas: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "investingStyle": [
            "value_oriented",
            "quality_compounder_at_fair_price",
            "selective_growth_when_supported_by_market_dynamics",
        ],
        "preferredBusinessTypes": [
            {"name": area, "source": "simple_onboarding"} for area in focus_areas
        ],
        "avoidanceRules": [
            {
                "name": "outside_circle_of_competence",
                "description": "Avoid or defer companies that the user and assistant cannot explain clearly.",
            },
            {
                "name": "cheap_without_quality_or_clear_mispricing",
                "description": "Do not treat a low multiple alone as enough.",
            },
            {
                "name": "unclear_business_or_valuation_drivers",
                "description": "Avoid situations where business model, competition, or valuation drivers remain unclear.",
            },
        ],
        "geographyPreference": {
            "avoid": list(avoid_areas),
            "otherwiseAllowed": True,
        },
        "assistantChallengeStyle": {
            "level": "aggressive",
            "description": "Challenge thoroughly, but not for the sake of challenging.",
            "requiredBehaviors": [
                "separate_facts_from_judgment",
                "identify_weak_assumptions",
                "test_downside_cases",
                "compare_against_portfolio_and_opportunity_cost",
                "say_when_evidence_is_missing",
            ],
        },
        "recommendationBoundary": "Do not provide direct buy/sell/hold instructions.",
    }


def _position_sizing(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "targetNumberOfStocks": {
            "approximate": 10,
            "description": "Around 10 stocks can be enough for a concentrated long-term portfolio.",
        },
        "concentrationPreference": "concentrated_but_risk_adjusted",
        "ideaQualityPreference": "prefer_fewer_excellent_ideas",
        "activePortfolioScope": {
            "externalExposureAffectsSizing": request.external_exposure_affects_active_portfolio,
            "externalExposureAffectsConcentrationLimit": request.external_exposure_affects_active_portfolio,
            "description": (
                "External exposure should not affect active portfolio sizing or concentration limits by default."
                if not request.external_exposure_affects_active_portfolio
                else "External exposure may affect active portfolio sizing and concentration limits."
            ),
        },
        "positionSizingPrinciples": [
            "Position size should vary by opportunity quality.",
            "Position size should vary by downside risk.",
            "Position size should vary by valuation gap.",
            "Position size should vary by conviction and evidence quality.",
            "Higher-risk growth companies should have limited exposure unless evidence is unusually strong.",
            "Equal weighting is not required.",
        ],
        "starterPositionSize": {"value": None, "status": "not_yet_defined"},
        "highConvictionPositionSize": {"value": None, "status": "not_yet_defined"},
        "maxPositionSize": {
            "value": request.max_position_size,
            "description": "Maximum single-stock allocation in the active portfolio.",
        },
        "averageDownPolicy": {
            "stance": "do_not_sell_if_thesis_intact",
            "description": "If price falls but thesis remains intact, selling is not automatic. Additional buying still requires updated evidence and valuation.",
        },
        "sellOrReduceTriggers": [
            {"name": "better_opportunity", "description": "Opportunity cost is a valid reason to reduce."},
            {"name": "thesis_broken", "description": "Reduce or exit consideration when core thesis is broken."},
            {"name": "valuation_too_demanding", "description": "Challenge holding or adding when valuation becomes demanding."},
        ],
        "openQuestions": [
            "What starter position size feels comfortable?",
            "What high-conviction position size feels comfortable?",
            "What dilution, SBC, or leverage level should cap position size?",
        ],
    }


def _valuation_policy(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "defaultRequiredMarginOfSafety": request.required_margin_of_safety,
        "marginOfSafetyRanges": {
            "exceptional_compounder": {"minimum": 0.20, "maximum": 0.25},
            "normal_opportunity": {"minimum": request.required_margin_of_safety, "maximum": request.required_margin_of_safety},
            "higher_risk": {"minimum": 0.40, "maximum": 0.50},
        },
        "methodPolicy": {
            "stance": "assistant_selects_appropriate_method",
            "allowedMethods": ["simple_multiples", "dcf", "reverse_dcf", "scenario_ranges"],
            "description": "Use the valuation method that fits the business and data; explain on request.",
        },
        "requirements": [
            "Separate facts, assumptions, deterministic calculations, and judgment.",
            "Write explicit assumptions JSON before deterministic valuation.",
            "Use reverse DCF when market expectations need to be tested.",
            "Do not invent valuation outputs.",
        ],
    }


def _risk_policy(today: str, avoid_areas: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "negativeFreeCashFlowGrowth": {
            "stance": "allowed_but_limited",
            "description": "Can be considered when upside and evidence justify it, but requires stricter sizing and a larger margin of safety.",
            "implications": [
                "require higher margin of safety",
                "limit position size",
                "stress-test dilution and funding needs",
                "verify path to durable free cash flow",
            ],
        },
        "dilutionAndStockBasedCompensation": {"stance": "not_yet_calibrated"},
        "founderDependence": {"stance": "allowed"},
        "geography": {"avoid": list(avoid_areas), "allowedByDefault": ["United States", "Europe", "Israel", "other geographies unless company-specific risk suggests otherwise"]},
        "leverage": {"stance": "not_yet_calibrated"},
        "ideaQualityPreference": "prefer_fewer_excellent_ideas_over_many_decent_ideas",
    }


def _decision_process(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "candidateEvaluationDimensions": [
            {"name": "business_quality", "importance": "required"},
            {"name": "valuation", "importance": "required"},
            {"name": "growth_runway", "importance": "required"},
            {"name": "management_and_capital_allocation", "importance": "required"},
            {"name": "downside_protection", "importance": "required"},
            {"name": "portfolio_fit_and_opportunity_cost", "importance": "required"},
        ],
        "ideaFlow": {"preference": "few_high_signal_ideas", "targetIdeasPerMonth": request.ideas_per_month},
        "activePortfolioStartingPoint": {
            "status": "empty_until_user_supplies_holdings",
            "description": "No holdings or watchlist names are assumed during onboarding.",
        },
        "monthlyWorkflow": {
            "steps": [
                "review_existing_watchlist_and_current_best_opportunities",
                "search_for_new_high_signal_ideas",
                "present_short_briefs",
                "perform_deep_dive_only_when_user_requests_it",
            ],
            "description": "Combine review and discovery while keeping default output lightweight.",
        },
        "seriousCandidateRequirements": [
            "formal_thesis",
            "formal_bear_case",
            "valuation_or_reverse_dcf",
            "comparison_against_best_current_opportunity",
        ],
        "tooHardRule": {
            "skipQuicklyAllowed": True,
            "conditions": [
                "assistant gives a concrete reason",
                "assistant states what evidence would make the idea worth revisiting",
                "assistant logs the rejection or deferral",
            ],
            "rationale": "Protect research time and avoid false precision outside the user's circle of competence.",
        },
        "rejectedIdeas": {"logRejectedIdeas": True},
        "reviewTriggers": {
            "quarterlyEarningsAndFilings": True,
            "description": "Quarterly earnings and filings should trigger thesis reviews in future workflows.",
        },
        "onboardingPrinciple": {
            "description": "Ask broad questions and infer detailed defaults. Label inferred values separately from explicit answers.",
        },
    }


def _operating_preferences(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "contributionCadence": {"frequency": "monthly"},
        "ideaFlow": {
            "preference": "few_high_signal_ideas",
            "targetIdeasPerMonth": request.ideas_per_month,
            "description": "Prefer a small set of high-signal ideas over a large research queue.",
        },
        "researchDepth": {
            "sequence": ["brief", "deep_dive_on_request"],
            "description": "Produce a short brief first. Go deep only when the user asks or the brief clearly supports it.",
        },
        "monthlyWorkflow": {
            "startingPoint": [
                "review_existing_watchlist_and_current_best_opportunities",
                "search_for_new_high_signal_ideas",
            ],
            "defaultOutput": "short_brief",
            "deepDivePolicy": "on_request_after_brief",
        },
        "reviewTriggers": {"quarterlyFilings": True, "earnings": True},
        "valuationComplexity": {
            "stance": "assistant_selects_appropriate_method",
            "allowedMethods": ["simple_multiples", "dcf", "reverse_dcf", "scenario_ranges"],
        },
        "teachingPreference": {"stance": "on_request"},
        "tooHardSkipPolicy": {
            "stance": "allowed_with_reason",
            "rationale": "Protect time and avoid false precision outside the user's circle of competence.",
        },
        "trustBuilders": [
            "show_data_supporting_claims",
            "show_reasoning",
            "cite_local_artifacts",
            "separate_facts_from_judgment",
            "state_uncertainty",
        ],
        "workflowAntiPatterns": [
            "too_heavy",
            "process_that_does_not_serve_researching_stocks",
            "process_that_does_not_help_build_a_personalized_portfolio",
        ],
    }


def _external_exposure(request: ProfileInitRequest, today: str) -> dict[str, Any]:
    exposures = [_normalize_external_exposure(item, request.external_exposure_affects_active_portfolio) for item in request.external_exposures]
    other_portfolios = [_normalize_other_portfolio(item) for item in request.other_portfolios]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": today,
        "source": "lightweight onboarding",
        "description": "External exposure that may be useful context but is not automatically part of the active portfolio.",
        "exposures": exposures,
        "otherPortfolios": other_portfolios,
        "policy": {
            "defaultTreatment": (
                "include_when_calculating_total_economic_concentration"
                if request.external_exposure_affects_active_portfolio
                else "do_not_count_as_active_portfolio_holding_or_active_portfolio_concentration"
            ),
            "activePortfolioImpact": (
                ["may_affect_position_sizing", "may_affect_concentration_limit"]
                if request.external_exposure_affects_active_portfolio
                else [
                    "do_not_count_against_the_single_stock_limit",
                    "do_not_block_or_resize_active_portfolio_ideas",
                    "show_separately_only_when_total_economic_exposure_is_relevant",
                ]
            ),
            "portfolioUse": [
                "record total economic exposure when explicitly useful",
                "do not invent share counts or values",
                "ask for details before tax, allocation, or concentration calculations",
            ],
        },
        "vestedRsuDisposition": {
            "defaultAssumption": "case_by_case_no_immediate_sale",
            "notes": "Do not assume immediate sale or diversification after vesting.",
        },
        "openQuestions": [
            "Should the toolkit eventually support a separate total-wealth view?",
            "What tax rules should apply before making RSU sale or hold comparisons?",
        ],
    }


def _policy_md(
    request: ProfileInitRequest,
    today: str,
    focus_areas: tuple[str, ...],
    avoid_areas: tuple[str, ...],
) -> str:
    focus = "\n".join(f"- {area}" for area in focus_areas)
    avoid = "\n".join(f"- {area}" for area in avoid_areas)
    external_policy = (
        "External exposure may be considered in active portfolio concentration and sizing."
        if request.external_exposure_affects_active_portfolio
        else "External exposure is tracked separately and should not constrain active portfolio sizing by default."
    )
    return f"""# Investor Policy

Updated: {today}

## Purpose

This portfolio is intended to outperform {request.benchmark} over a {request.horizon_min_years}-{request.horizon_max_years} year horizon.

The portfolio is active, selective, and long-term. It is not designed for frequent trading, low tracking error, or income generation.

## Investing Philosophy

The portfolio follows a value-oriented approach with room for quality and growth when valuation is reasonable.

Attractive ideas may include:

- Low-multiple companies where the market price appears too pessimistic.
- Quality compounders available at a fair or attractive price.
- Companies with high growth potential where market dynamics support a long runway.

"Cheap" is not sufficient by itself. A low multiple must be paired with business quality, durability, or a clear reason the market may be wrong.

## Circle Of Competence

Preferred areas:

{focus}

Avoid or de-prioritize:

- Businesses outside the user's circle of competence.
- Businesses that cannot be explained clearly.
{avoid}

## Margin Of Safety

Default required margin of safety: {request.required_margin_of_safety:.0%}.

This is a default hurdle, not a rigid rule. Higher uncertainty should require a larger discount to estimated fair value. Exceptional, durable, easy-to-understand compounders may justify a smaller discount.

Working ranges:

- 20-25% may be acceptable for exceptional durable compounders.
- {request.required_margin_of_safety:.0%} is the default for normal opportunities.
- 40-50% should be required for higher-risk opportunities.

## Portfolio Construction

Working target:

- Around 10 stocks can be enough.
- Hard maximum position size: {request.max_position_size:.0%} of the active portfolio.
- Position size should vary by opportunity quality, downside risk, valuation gap, conviction, and evidence quality.
- Prefer fewer excellent ideas over many decent ideas.

{external_policy}

## Decision Process

Every serious candidate should be evaluated across:

- Business quality.
- Valuation.
- Growth runway.
- Management and capital allocation.
- Downside protection.
- Portfolio fit and opportunity cost.

Monthly work should review existing opportunities and search for new high-signal ideas. Default output should be about {request.ideas_per_month} short briefs. Deep dives happen on request or when a brief clearly merits more work.

Before an idea becomes a serious candidate, write a formal thesis and a formal bear case.

The assistant may say "too hard, skip" only with a concrete reason, a revisit condition, and a rejection log entry.

## Assistant Behavior

Challenge aggressively and thoroughly, but not for its own sake. Separate evidence from interpretation, cite local artifacts for material claims, state uncertainty, and never provide direct buy/sell/hold instructions.
"""


def _onboarding_notes(request: ProfileInitRequest, today: str) -> str:
    return f"""# Onboarding Notes

Updated: {today}

## Product Principle

Onboarding should stay simple. Ask broad questions, infer detailed defaults, and label inferred values clearly.

Do not ask the user expert-level investing questions upfront. The first-run flow should collect only enough information to start researching stocks and building a personalized portfolio.

## Minimal Questions

1. What is this portfolio trying to accomplish?
2. What kinds of businesses do you understand best?
3. What kinds of businesses should we avoid?
4. How concentrated are you comfortable being?
5. Should external exposure affect this active portfolio?
6. How many high-signal ideas should be shown each month?
7. Do you want short briefs first, or deep research by default?

## Defaults Created By This Onboarding

- Benchmark: {request.benchmark}.
- Horizon: {request.horizon_min_years}-{request.horizon_max_years} years.
- Default margin of safety: {request.required_margin_of_safety:.0%}.
- Maximum active position size: {request.max_position_size:.0%}.
- Ideas per month: {request.ideas_per_month}.
- Short briefs first, deep dives on request.
- External exposure affects active sizing: {str(request.external_exposure_affects_active_portfolio).lower()}.

## MCP Resources

Profile files are exposed through `investor://profile/...` resources so assistants can read policy context without guessing.
"""


def _thesis_template() -> str:
    return """# Thesis Template

## Company

- Ticker:
- Date:
- Status: draft

## One-Sentence Thesis

## Business Quality

## Growth Runway

## Valuation Setup

## Portfolio Fit

## Key Assumptions

## Evidence

Use local filing, metrics, valuation, and portfolio artifact citations.

## What Would Change My Mind
"""


def _bear_case_template() -> str:
    return """# Bear Case Template

## Company

- Ticker:
- Date:
- Status: draft

## Strongest Bear Case

## Thesis Breakers

## Valuation Fragility

## Competitive Risks

## Balance Sheet, Dilution, Or Funding Risks

## Missing Evidence

## Revisit Conditions
"""


def _profile_folder_readmes() -> dict[str, str]:
    return {
        "theses": "# Theses\n\nStore agent-owned thesis notes here.\n",
        "rejected": "# Rejected Ideas\n\nLog skipped, deferred, and rejected ideas here with reasons and revisit conditions.\n",
        "decisions": "# Decisions\n\nStore decision notes, monthly reviews, and portfolio process records here.\n",
    }


def _normalize_external_exposure(item: dict[str, Any], affects_active_portfolio: bool) -> dict[str, Any]:
    ticker = normalize_ticker(str(item.get("ticker") or ""))
    exposure: dict[str, Any] = {
        "ticker": ticker,
        "type": str(item.get("type") or "external_stock"),
        "includeInActivePortfolio": bool(item.get("includeInActivePortfolio", affects_active_portfolio)),
        "status": str(item.get("status") or "known_external_exposure"),
        "notes": str(item.get("notes") or "").strip(),
    }
    amount = item.get("amount")
    currency = str(item.get("currency") or "").strip().upper()
    if amount is None and isinstance(item.get("approximateMarketValue"), dict):
        amount = item["approximateMarketValue"].get("amount")
        currency = currency or str(item["approximateMarketValue"].get("currency") or "").strip().upper()
    if amount is not None:
        exposure["approximateMarketValue"] = {"amount": float(amount), "currency": currency or "USD"}
    return exposure


def _normalize_other_portfolio(item: dict[str, Any]) -> dict[str, Any]:
    portfolio: dict[str, Any] = {
        "name": str(item.get("name") or "other_portfolio"),
        "includeInActivePortfolio": bool(item.get("includeInActivePortfolio", False)),
        "notes": str(item.get("notes") or "").strip(),
    }
    amount = item.get("amount")
    currency = str(item.get("currency") or "").strip().upper()
    if amount is not None:
        portfolio["approximateMarketValue"] = {"amount": float(amount), "currency": currency or "USD"}
    return portfolio


def _clean_list(values: tuple[str, ...], defaults: tuple[str, ...]) -> tuple[str, ...]:
    cleaned = tuple(value.strip() for value in values if str(value).strip())
    return cleaned or defaults
