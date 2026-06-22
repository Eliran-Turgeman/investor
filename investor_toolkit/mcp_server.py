from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .app import AppContext, InvestorApplication
from .app.schemas import OperationResult
from .utils import normalize_ticker
from .valuation import SUPPORTED_MODELS


PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-11-25"}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
MCP_RESOURCE_NOT_FOUND = -32002


@dataclass(slots=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    read_only: bool
    destructive: bool = False
    open_world: bool = False
    output_schema: dict[str, Any] | None = None

    def descriptor(self) -> dict[str, Any]:
        descriptor = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "openWorldHint": self.open_world,
            },
        }
        if self.output_schema is not None:
            descriptor["outputSchema"] = self.output_schema
        return descriptor


class InvestorMcpServer:
    def __init__(self, app: InvestorApplication) -> None:
        self.app = app
        self.tools = self._build_tools()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, JSONRPC_INVALID_REQUEST, "Invalid JSON-RPC request.")
        method = message.get("method")
        request_id = message.get("id")
        if "id" in message and request_id is None:
            return self._error(None, JSONRPC_INVALID_REQUEST, "JSON-RPC request id cannot be null.")
        params = message.get("params") or {}
        if not isinstance(method, str):
            return self._error(request_id, JSONRPC_INVALID_REQUEST, "Missing JSON-RPC method.")
        try:
            result = self._dispatch(method, params if isinstance(params, dict) else {})
        except JsonRpcError as exc:
            return self._error(request_id, exc.code, exc.message, exc.data)
        except Exception:
            return self._error(
                request_id,
                JSONRPC_INTERNAL_ERROR,
                "Internal MCP server error.",
            )
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.tools:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            payload = self.tools[name].handler(arguments or {})
            return _tool_result(payload)
        except (ValueError, FileNotFoundError) as exc:
            return _tool_error(str(exc) or "Tool execution failed.")
        except Exception:
            return _tool_error("Tool execution failed.")

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"tools": [tool.descriptor() for tool in self.tools.values()]}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Tool arguments must be an object.")
            return self.call_tool(name, arguments)
        if method == "resources/list":
            return {"resources": [_resource_descriptor(ref.to_dict()) for ref in self.app.artifacts.all_existing_resources()]}
        if method == "resources/templates/list":
            return {"resourceTemplates": _resource_templates()}
        if method == "resources/read":
            uri = str(params.get("uri") or "")
            if not uri:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "resources/read requires uri.")
            try:
                content = self.app.artifacts.read(uri)
            except FileNotFoundError as exc:
                raise JsonRpcError(MCP_RESOURCE_NOT_FOUND, str(exc)) from exc
            return {"contents": [content.to_resource_content()]}
        if method == "prompts/list":
            return {"prompts": _prompt_descriptors()}
        if method == "prompts/get":
            return self._prompt(params)
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"Unknown method: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "investor-toolkit",
                "version": __version__,
            },
        }

    def _prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Prompt arguments must be an object.")
        prompts = {
            "investor_onboarding": _investor_onboarding_prompt,
            "portfolio_review": _portfolio_review_prompt,
            "company_deep_dive": _company_deep_dive_prompt,
            "thesis_challenge": _thesis_challenge_prompt,
            "candidate_brief": _candidate_brief_prompt,
        }
        if name not in prompts:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown prompt: {name}")
        return {
            "description": next(item["description"] for item in _prompt_descriptors() if item["name"] == name),
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompts[name](arguments)},
                }
            ],
        }

    def _build_tools(self) -> dict[str, ToolSpec]:
        specs = [
            ToolSpec(
                name="get_portfolio_context",
                title="Get portfolio context",
                description=(
                    "Use this when the user asks about their current holdings, watchlist, portfolio rules, "
                    "or existing valuation outputs. This is read-only and returns normalized local portfolio "
                    "inputs plus known valuation rows, artifact references, and profileStatus. If "
                    "profileStatus.onboardingRequired is true, run lightweight onboarding before personalized "
                    "portfolio analysis or candidate generation."
                ),
                input_schema=_object_schema({}, required=[]),
                output_schema=_operation_schema(),
                read_only=True,
                handler=lambda args: self.app.portfolio.context_snapshot().to_dict(),
            ),
            ToolSpec(
                name="get_profile_status",
                title="Get profile status",
                description=(
                    "Use this at the start of a personalized investor workflow. It reports whether profile "
                    "artifacts exist, which ones are missing, and whether init_investor_profile should be run "
                    "before portfolio review or stock candidate generation."
                ),
                input_schema=_object_schema({}, required=[]),
                output_schema=_operation_schema(),
                read_only=True,
                handler=lambda args: self.app.profile.status().to_dict(),
            ),
            ToolSpec(
                name="init_investor_profile",
                title="Initialize investor profile",
                description=(
                    "Use this during lightweight onboarding to create local investor profile and policy artifacts. "
                    "The tool writes broad defaults from simple inputs; it does not ask an expert questionnaire and "
                    "does not make investment recommendations."
                ),
                input_schema=_object_schema(
                    {
                        "benchmark": {"type": "string", "default": "S&P 500"},
                        "horizonMinYears": {"type": "integer", "default": 5, "minimum": 1},
                        "horizonMaxYears": {"type": "integer", "default": 10, "minimum": 1},
                        "ideasPerMonth": {"type": "integer", "default": 3, "minimum": 1},
                        "requiredMarginOfSafety": {"type": "number", "default": 0.30, "minimum": 0, "maximum": 0.8},
                        "maxPositionSize": {"type": "number", "default": 0.30, "minimum": 0, "maximum": 1},
                        "focusAreas": {"type": "array", "items": {"type": "string"}, "default": []},
                        "avoidAreas": {"type": "array", "items": {"type": "string"}, "default": []},
                        "externalExposures": {
                            "type": "array",
                            "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ticker": {"type": "string"},
                                    "type": {"type": "string"},
                                    "amount": {"type": "number"},
                                    "currency": {"type": "string"},
                                    "notes": {"type": "string"},
                                    "includeInActivePortfolio": {"type": "boolean"},
                                },
                                "required": ["ticker"],
                                "additionalProperties": True,
                            },
                        },
                        "otherPortfolios": {
                            "type": "array",
                            "default": [],
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "amount": {"type": "number"},
                                    "currency": {"type": "string"},
                                    "notes": {"type": "string"},
                                },
                                "required": ["name"],
                                "additionalProperties": True,
                            },
                        },
                        "externalExposureAffectsActivePortfolio": {"type": "boolean", "default": False},
                        "overwrite": {"type": "boolean", "default": False},
                    },
                    required=[],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                handler=self._init_profile,
            ),
            ToolSpec(
                name="list_company_artifacts",
                title="List company artifacts",
                description=(
                    "Use this when you need to discover local research files for a specific ticker before "
                    "reading metrics, filings, extracted sections, prices, or normalized financial data. "
                    "Set includeMissing only when diagnosing setup problems."
                ),
                input_schema=_object_schema(
                    {
                        "ticker": _string("US-listed stock ticker, for example MSFT."),
                        "includeMissing": {"type": "boolean", "default": False},
                    },
                    required=["ticker"],
                ),
                output_schema=_operation_schema(),
                read_only=True,
                handler=self._list_company_artifacts,
            ),
            ToolSpec(
                name="refresh_company_research",
                title="Refresh company research",
                description=(
                    "Use this when local source data for one ticker needs to be created or refreshed before "
                    "analysis. This writes local research artifacts and may call SEC and market-data providers "
                    "unless offline is true; it does not make investment recommendations."
                ),
                input_schema=_object_schema(
                    {
                        "ticker": _string("US-listed stock ticker, for example MSFT."),
                        "offline": {"type": "boolean", "default": False, "description": "Use only local cached data."},
                        "refresh": {"type": "boolean", "default": False, "description": "Refresh provider caches when online."},
                    },
                    required=["ticker"],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                open_world=True,
                handler=lambda args: self.app.research.ingest(
                    args["ticker"],
                    offline=bool(args.get("offline", False)),
                    refresh=bool(args.get("refresh", False)),
                ).to_dict(),
            ),
            ToolSpec(
                name="init_valuation_assumptions",
                title="Initialize valuation assumptions",
                description=(
                    "Use this to create an explicit assumptions JSON file before a valuation. The file is "
                    "prefilled with deterministic local values where available, while judgment fields remain "
                    "null for the user or agent to fill."
                ),
                input_schema=_object_schema(
                    {
                        "ticker": _string("US-listed stock ticker, for example MSFT."),
                        "model": {
                            "type": "string",
                            "enum": list(SUPPORTED_MODELS),
                            "description": "Deterministic valuation model to template.",
                        },
                        "scenario": {"type": "string", "default": "base"},
                        "outputPath": _string("Workspace/configured-root path for the assumptions JSON."),
                    },
                    required=["ticker", "model", "outputPath"],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                handler=lambda args: self.app.valuation.init_assumptions(
                    args["ticker"],
                    model=args["model"],
                    scenario=str(args.get("scenario") or "base"),
                    output_path=self._mcp_path(args["outputPath"], "outputPath"),
                ).to_dict(),
            ),
            ToolSpec(
                name="validate_assumptions",
                title="Validate assumptions",
                description=(
                    "Use this before any deterministic valuation. It checks an assumptions JSON file for "
                    "required fields, numeric guardrails, ticker mismatch, and local-data compatibility. "
                    "It returns blocked status with actionable errors when the assumptions need repair."
                ),
                input_schema=_object_schema(
                    {
                        "path": _string("Workspace/configured-root assumptions JSON path."),
                        "expectedTicker": _string("Optional ticker that must match the file.", nullable=True),
                    },
                    required=["path"],
                ),
                output_schema=_operation_schema(),
                read_only=True,
                handler=lambda args: self.app.valuation.validate_assumptions(
                    self._mcp_path(args["path"], "path"),
                    expected_ticker=args.get("expectedTicker"),
                ).to_dict(),
            ),
            ToolSpec(
                name="run_valuation",
                title="Run valuation",
                description=(
                    "Use this after assumptions have been explicitly filled and validated. It calculates a "
                    "deterministic valuation from local data and the supplied assumptions file. It can write "
                    "a result JSON when outputPath is provided; otherwise it only returns the result."
                ),
                input_schema=_object_schema(
                    {
                        "ticker": _string("US-listed stock ticker, for example MSFT."),
                        "assumptionsPath": _string("Workspace/configured-root assumptions JSON path."),
                        "includeSensitivity": {"type": "boolean", "default": False},
                        "includeDebug": {"type": "boolean", "default": False},
                        "outputPath": _string("Optional workspace/configured-root result JSON path.", nullable=True),
                        "exportAgentContext": {"type": "boolean", "default": False},
                    },
                    required=["ticker", "assumptionsPath"],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                handler=self._run_valuation,
            ),
            ToolSpec(
                name="compare_valuation_scenarios",
                title="Compare valuation scenarios",
                description=(
                    "Use this to compare two or more existing valuation assumptions files for one ticker. "
                    "It is read-only unless a later workflow writes a memo from the returned comparison."
                ),
                input_schema=_object_schema(
                    {
                        "ticker": _string("US-listed stock ticker, for example MSFT."),
                        "assumptionsPaths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": "Two or more workspace/configured-root assumptions JSON paths.",
                        },
                        "includeSensitivity": {"type": "boolean", "default": False},
                    },
                    required=["ticker", "assumptionsPaths"],
                ),
                output_schema=_operation_schema(),
                read_only=True,
                handler=lambda args: self.app.valuation.compare(
                    args["ticker"],
                    self._mcp_paths(list(args["assumptionsPaths"]), "assumptionsPaths"),
                    include_sensitivity=bool(args.get("includeSensitivity", False)),
                ).to_dict(),
            ),
            ToolSpec(
                name="run_portfolio_valuations",
                title="Run portfolio valuations",
                description=(
                    "Use this to recalculate deterministic valuation result files for all portfolio/watchlist "
                    "tickers with existing assumptions files. The tool writes valuation outputs and an audit file."
                ),
                input_schema=_object_schema(
                    {"includeSensitivity": {"type": "boolean", "default": False}},
                    required=[],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                handler=lambda args: self.app.portfolio.value(
                    include_sensitivity=bool(args.get("includeSensitivity", False))
                ).to_dict(),
            ),
            ToolSpec(
                name="build_portfolio_signals",
                title="Build portfolio signals",
                description=(
                    "Use this after portfolio valuations or user fair values exist. It creates rule-based "
                    "diagnostic signals such as Opportunity, Watch, Review, or No decision. These are not "
                    "buy/sell/hold recommendations."
                ),
                input_schema=_object_schema(
                    {
                        "write": {"type": "boolean", "default": True},
                        "workbookPath": _string(
                            "Optional workspace/configured-root workbook path to export after writing signals.",
                            nullable=True,
                        ),
                    },
                    required=[],
                ),
                output_schema=_operation_schema(),
                read_only=False,
                destructive=True,
                handler=self._build_portfolio_signals,
            ),
        ]
        return {spec.name: spec for spec in specs}

    def _run_valuation(self, args: dict[str, Any]) -> dict[str, Any]:
        assumptions_path = self._mcp_path(args["assumptionsPath"], "assumptionsPath")
        output_path = self._mcp_optional_path(args.get("outputPath"), "outputPath")
        return self.app.valuation.run(
            args["ticker"],
            assumptions_path,
            include_sensitivity=bool(args.get("includeSensitivity", False)),
            include_debug=bool(args.get("includeDebug", False)),
            output_path=output_path,
            export_context=bool(args.get("exportAgentContext", False)),
        ).to_dict()

    def _build_portfolio_signals(self, args: dict[str, Any]) -> dict[str, Any]:
        workbook_path = self._mcp_optional_path(args.get("workbookPath"), "workbookPath")
        return self.app.portfolio.signals(
            write=bool(args.get("write", True)),
            workbook_path=workbook_path,
        ).to_dict()

    def _init_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.app.profile.init(
            benchmark=str(args.get("benchmark") or "S&P 500"),
            horizon_min_years=int(args.get("horizonMinYears") or 5),
            horizon_max_years=int(args.get("horizonMaxYears") or 10),
            ideas_per_month=int(args.get("ideasPerMonth") or 3),
            required_margin_of_safety=float(args.get("requiredMarginOfSafety", 0.30)),
            max_position_size=float(args.get("maxPositionSize", 0.30)),
            focus_areas=list(args.get("focusAreas") or []),
            avoid_areas=list(args.get("avoidAreas") or []),
            external_exposures=list(args.get("externalExposures") or []),
            other_portfolios=list(args.get("otherPortfolios") or []),
            external_exposure_affects_active_portfolio=bool(
                args.get("externalExposureAffectsActivePortfolio", False)
            ),
            overwrite=bool(args.get("overwrite", False)),
        )
        return result.to_dict()

    def _list_company_artifacts(self, args: dict[str, Any]) -> dict[str, Any]:
        ticker = normalize_ticker(str(args.get("ticker") or ""))
        refs = self.app.artifacts.company_artifacts(ticker)
        if not args.get("includeMissing", False):
            refs = [ref for ref in refs if ref.exists]
        return OperationResult(
            operation="artifacts.list_company",
            data={"ticker": ticker, "artifacts": [ref.to_dict() for ref in refs]},
            artifacts=refs,
            sourcePaths=[ref.path for ref in refs if ref.exists],
        ).to_dict()

    def _mcp_optional_path(self, value: Any, field_name: str) -> str | None:
        if value is None:
            return None
        return self._mcp_path(value, field_name)

    def _mcp_paths(self, values: list[Any], field_name: str) -> list[str]:
        return [self._mcp_path(value, f"{field_name}[{index}]") for index, value in enumerate(values)]

    def _mcp_path(self, value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty workspace/configured-root path.")
        raw_path = Path(value)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (self.app.context.workspace_root / raw_path).resolve()
        if not self._is_allowed_mcp_path(resolved):
            raise ValueError(f"{field_name} escapes the MCP workspace/configured roots: {value}")
        return str(resolved)

    def _is_allowed_mcp_path(self, path: Path) -> bool:
        for root in self._mcp_allowed_roots():
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _mcp_allowed_roots(self) -> list[Path]:
        context = self.app.context
        roots = [
            context.workspace_root,
            context.research_root,
            context.portfolio_dir,
            context.assumptions_dir,
            context.valuations_dir,
        ]
        unique_roots: list[Path] = []
        for root in roots:
            resolved = Path(root).resolve()
            if resolved not in unique_roots:
                unique_roots.append(resolved)
        return unique_roots

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Investor Toolkit MCP stdio server.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root for local artifacts.")
    parser.add_argument("--research-root", help="Research artifact root. Defaults to RESEARCH_HOME or ./research.")
    parser.add_argument("--portfolio-dir", default="portfolio")
    parser.add_argument("--assumptions-dir", default="assumptions")
    parser.add_argument("--valuations-dir", default="valuations")
    args = parser.parse_args(argv)
    context = AppContext.from_env(
        cwd=Path(args.workspace_root),
        research_root=args.research_root,
        portfolio_dir=args.portfolio_dir,
        assumptions_dir=args.assumptions_dir,
        valuations_dir=args.valuations_dir,
    )
    server = InvestorMcpServer(InvestorApplication(context))
    return run_stdio(server)


def run_stdio(server: InvestorMcpServer) -> int:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            response = InvestorMcpServer._error(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}")
        else:
            response = server.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
            sys.stdout.flush()
    return 0


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)}],
        "structuredContent": payload,
        "isError": payload.get("status") == "error",
    }


def _tool_error(message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "schemaVersion": "1.0",
        "operation": "mcp.tool",
        "status": "error",
        "errors": [message],
        "warnings": [],
        "sourcePaths": [],
        "artifacts": [],
        "nextActions": [],
    }
    if data:
        payload["data"] = data
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": payload,
        "isError": True,
    }


def _resource_descriptor(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "uri": ref["uri"],
        "name": ref["name"],
        "description": ref.get("description") or f"{ref.get('kind', 'artifact')} artifact",
        "mimeType": ref.get("mimeType") or "application/json",
    }


def _resource_templates() -> list[dict[str, Any]]:
    return [
        {
            "uriTemplate": "investor://profile/{artifact}",
            "name": "Investor profile artifact",
            "description": "Investor profile artifact such as policy, goals, preferences, risk policy, or onboarding notes.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "investor://portfolio/{artifact}",
            "name": "Portfolio artifact",
            "description": "Portfolio artifact such as holdings, watchlist, signals, or valuation audit.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "investor://company/{ticker}/{artifact}",
            "name": "Company research artifact",
            "description": "Company research artifact such as metrics, financials, prices, filings, or extracted sections.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "investor://valuation/{ticker}/{artifact}",
            "name": "Valuation result artifact",
            "description": "Deterministic valuation result JSON artifact.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "investor://portfolio/agent-{artifact}/{id}",
            "name": "Agent harness artifact",
            "description": "LLM-backed agent harness run, review, or brief artifact.",
            "mimeType": "application/json",
        },
    ]


def _prompt_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "name": "investor_onboarding",
            "title": "Investor Onboarding",
            "description": "Check profile status and run the lightweight investor onboarding flow when needed.",
        },
        {
            "name": "portfolio_review",
            "title": "Portfolio Review",
            "description": "Review holdings, watchlist, valuation outputs, signals, and data-quality blockers.",
        },
        {
            "name": "company_deep_dive",
            "title": "Company Deep Dive",
            "description": "Research one company from local filings, metrics, and explicit valuation assumptions.",
            "arguments": [{"name": "ticker", "description": "US-listed ticker to research.", "required": True}],
        },
        {
            "name": "thesis_challenge",
            "title": "Thesis Challenge",
            "description": "Challenge an existing or draft thesis using local evidence and valuation assumptions.",
            "arguments": [{"name": "ticker", "description": "US-listed ticker to challenge.", "required": True}],
        },
        {
            "name": "candidate_brief",
            "title": "Candidate Brief",
            "description": "Prepare a value-investor candidate brief for a ticker before a deeper decision memo.",
            "arguments": [{"name": "ticker", "description": "US-listed ticker to brief.", "required": True}],
        },
    ]


def _portfolio_review_prompt(_args: dict[str, Any]) -> str:
    return (
        "Use the investor MCP tools and resources. First call get_profile_status. If onboardingRequired is true, "
        "run lightweight onboarding before personalized analysis. Then get portfolio context, inspect signals and "
        "valuation audit artifacts if present, and summarize data quality, valuation gaps, and the highest-priority "
        "review items. Separate source facts, deterministic calculations, and judgment. Do not give direct buy/sell/hold instructions."
    )


def _investor_onboarding_prompt(_args: dict[str, Any]) -> str:
    return (
        "Use the investor MCP profile gate. First call get_profile_status. If onboardingRequired is false, read "
        "investor://profile/policy and summarize that the profile is ready. If onboardingRequired is true, ask at "
        "most five broad questions: portfolio objective and horizon, businesses the user understands, areas to avoid, "
        "whether external RSUs or other portfolios should affect this active portfolio, and preferred monthly idea "
        "flow. Do not ask an expert questionnaire. Then call init_investor_profile with the answers and reasonable "
        "defaults, read investor://profile/status, and tell the user which profile artifacts were created. Do not "
        "provide investment recommendations."
    )


def _company_deep_dive_prompt(args: dict[str, Any]) -> str:
    ticker = normalize_ticker(str(args.get("ticker") or ""))
    return (
        f"Use the investor MCP tools and resources for {ticker}. Refresh or list local company artifacts as needed, "
        "read metrics and the latest extracted filing sections, then build a concise business-quality and valuation "
        "research brief with citations to artifact URIs or paths. Separate evidence from interpretation."
    )


def _thesis_challenge_prompt(args: dict[str, Any]) -> str:
    ticker = normalize_ticker(str(args.get("ticker") or ""))
    return (
        f"Use the investor MCP tools and resources for {ticker}. Read local filings, metrics, and any existing valuation "
        "outputs, then write a bear-case challenge. Identify fragile assumptions, missing evidence, and what would change "
        "the thesis. Do not make a direct trading recommendation."
    )


def _candidate_brief_prompt(args: dict[str, Any]) -> str:
    ticker = normalize_ticker(str(args.get("ticker") or ""))
    return (
        "First call get_profile_status. If onboardingRequired is true, run lightweight onboarding before a personalized "
        f"candidate brief. Then use the investor MCP tools and resources for {ticker}. Produce a value-investor candidate "
        "brief covering business understandability, financial quality, valuation setup, portfolio fit, open questions, "
        "and next deterministic actions."
    )


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _string(description: str, nullable: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if nullable:
        schema = {"anyOf": [schema, {"type": "null"}], "description": description}
    return schema


def _operation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "schemaVersion": {"type": "string"},
            "operation": {"type": "string"},
            "status": {"type": "string", "enum": ["ok", "blocked", "error"]},
            "generatedAt": {"type": "string"},
            "data": {"type": "object"},
            "warnings": {"type": "array"},
            "errors": {"type": "array"},
            "sourcePaths": {"type": "array", "items": {"type": "string"}},
            "artifacts": {"type": "array"},
            "nextActions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schemaVersion", "operation", "status", "generatedAt", "data"],
        "additionalProperties": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
