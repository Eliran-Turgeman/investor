from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from .app import AppContext, InvestorApplication
from .logging_utils import close_logging
from .portfolio import render_portfolio_summary
from .providers import ExchangeRateProvider, ProviderError, StooqMarketDataProvider
from .rsu_tax import (
    SCENARIO_EARLY,
    SCENARIO_QUALIFIED,
    RsuTaxInputs,
    add_years,
    average_grant_price,
    calculate_rsu_tax,
    infer_102_scenario,
    latest_sale_price,
    normalize_rate,
    render_rsu_tax_summary,
)
from .storage import ResearchStorage
from .utils import normalize_ticker, parse_iso_date
from .valuation import (
    SUPPORTED_MODELS,
    render_comparison,
    render_valuation_result,
)
from .app.schemas import OperationResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="investor",
        description="Local-first investor toolkit.",
    )
    parser.add_argument(
        "--research-root",
        help="Directory that contains ticker research folders. Defaults to ./research.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    quickstart = subparsers.add_parser(
        "quickstart",
        help="Bootstrap a ticker research folder and print agent-ready next steps.",
    )
    quickstart.add_argument("ticker")
    quickstart.add_argument("--offline", action="store_true", help="Create local artifacts without network calls.")
    quickstart.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    quickstart.add_argument("--research-root", default=argparse.SUPPRESS, help="Directory for ticker research folders.")

    research = subparsers.add_parser("research", help="Research data ingestion and metrics commands.")
    research_subparsers = research.add_subparsers(dest="research_command", required=True)

    start = research_subparsers.add_parser("start", help="Initialize local research for a ticker.")
    start.add_argument("ticker")
    start.add_argument("--offline", action="store_true", help="Create local artifacts without network calls.")
    start.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    start.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    ingest = research_subparsers.add_parser(
        "ingest",
        help="Refresh source data and fetch SEC filings filed in the last 2 years.",
    )
    ingest.add_argument("ticker")
    ingest.add_argument("--offline", action="store_true", help="Rebuild from local cached files only.")
    ingest.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    ingest.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    metrics = research_subparsers.add_parser("metrics", help="Calculate and write financial metrics.")
    metrics.add_argument("ticker")
    metrics.add_argument("--offline", action="store_true", help="Accepted for symmetry; metrics uses local data.")
    metrics.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    assumptions = subparsers.add_parser("assumptions", help="Create and validate valuation assumptions files.")
    assumptions_subparsers = assumptions.add_subparsers(dest="assumptions_command", required=True)

    assumptions_init = assumptions_subparsers.add_parser("init", help="Create a valuation assumptions template.")
    assumptions_init.add_argument("ticker")
    assumptions_init.add_argument("--model", choices=SUPPORTED_MODELS, required=True)
    assumptions_init.add_argument("--scenario", default="base")
    assumptions_init.add_argument("--output", required=True)
    assumptions_init.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    assumptions_validate = assumptions_subparsers.add_parser("validate", help="Validate a valuation assumptions file.")
    assumptions_validate.add_argument("path")
    assumptions_validate.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    value = subparsers.add_parser("value", help="Run deterministic intrinsic valuation models.")
    value.add_argument("target", help="Ticker to value, or 'compare'.")
    value.add_argument("compare_ticker", nargs="?", help="Ticker when using 'value compare'.")
    value.add_argument("--assumptions", action="append", help="Path to assumptions JSON. Repeat for compare.")
    value.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    value.add_argument("--output")
    value.add_argument("--include-sensitivity", action="store_true")
    value.add_argument("--include-debug", action="store_true")
    value.add_argument("--export-agent-context", action="store_true")
    value.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    reverse_dcf = subparsers.add_parser("reverse-dcf", help="Run a reverse DCF valuation.")
    reverse_dcf.add_argument("ticker")
    reverse_dcf.add_argument("--assumptions", required=True)
    reverse_dcf.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    reverse_dcf.add_argument("--output")
    reverse_dcf.add_argument("--include-debug", action="store_true")
    reverse_dcf.add_argument("--export-agent-context", action="store_true")
    reverse_dcf.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    onboarding = subparsers.add_parser("onboarding", help="Lightweight investor profile onboarding.")
    onboarding_subparsers = onboarding.add_subparsers(dest="onboarding_command", required=True)

    onboarding_init = onboarding_subparsers.add_parser(
        "init",
        help="Create starter investor profile and policy artifacts.",
    )
    onboarding_init.add_argument("--portfolio-dir", default="portfolio", help="Directory for profile artifacts.")
    onboarding_init.add_argument("--benchmark", default="S&P 500")
    onboarding_init.add_argument("--horizon", default="5-10", help="Investment horizon, for example 5-10 or 10.")
    onboarding_init.add_argument("--ideas-per-month", type=int, default=3)
    onboarding_init.add_argument("--margin-of-safety", default="30%", help="Default required margin of safety.")
    onboarding_init.add_argument("--max-position-size", default="30%", help="Maximum single-stock active portfolio size.")
    onboarding_init.add_argument("--focus", action="append", default=[], help="Business area the user understands. Repeatable.")
    onboarding_init.add_argument("--avoid", action="append", default=[], help="Business area, geography, or risk to avoid. Repeatable.")
    onboarding_init.add_argument(
        "--external-exposure",
        action="append",
        default=[],
        help="External exposure as TICKER:AMOUNT:CURRENCY[:TYPE], for example MSFT:50000:USD:RSU.",
    )
    onboarding_init.add_argument(
        "--other-portfolio",
        action="append",
        default=[],
        help="Other portfolio as NAME:AMOUNT:CURRENCY, for example index:250000:NIS.",
    )
    onboarding_init.add_argument(
        "--external-exposure-affects-active-portfolio",
        action="store_true",
        help="Count external exposure when thinking about active portfolio sizing.",
    )
    onboarding_init.add_argument("--overwrite", action="store_true", help="Overwrite existing profile artifacts.")
    onboarding_init.add_argument(
        "--interactive",
        action="store_true",
        help="Ask a few broad questions before writing profile artifacts.",
    )

    portfolio = subparsers.add_parser("portfolio", help="Portfolio workbook and deterministic signal commands.")
    portfolio_subparsers = portfolio.add_subparsers(dest="portfolio_command", required=True)

    portfolio_init = portfolio_subparsers.add_parser("init", help="Create a portfolio workbook and JSON templates.")
    portfolio_init.add_argument("--output", default="portfolio/portfolio.xlsx", help="Workbook path to create.")
    portfolio_init.add_argument("--portfolio-dir", help="Directory for portfolio JSON artifacts.")

    portfolio_import = portfolio_subparsers.add_parser("import", help="Import user-edited workbook inputs into JSON.")
    portfolio_import.add_argument("--workbook", default="portfolio/portfolio.xlsx")
    portfolio_import.add_argument("--portfolio-dir", help="Directory for portfolio JSON artifacts.")

    portfolio_export = portfolio_subparsers.add_parser("export", help="Export portfolio JSON, valuations, and signals to XLSX.")
    portfolio_export.add_argument("--workbook", default="portfolio/portfolio.xlsx")
    portfolio_export.add_argument("--portfolio-dir", help="Directory for portfolio JSON artifacts.")
    portfolio_export.add_argument("--assumptions-dir", default="assumptions")
    portfolio_export.add_argument("--valuations-dir", default="valuations")
    portfolio_export.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    portfolio_value = portfolio_subparsers.add_parser("value", help="Run valuations for portfolio tickers with existing assumptions.")
    portfolio_value.add_argument("--portfolio-dir", default="portfolio")
    portfolio_value.add_argument("--assumptions-dir", default="assumptions")
    portfolio_value.add_argument("--valuations-dir", default="valuations")
    portfolio_value.add_argument("--include-sensitivity", action="store_true")
    portfolio_value.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    portfolio_signals = portfolio_subparsers.add_parser("signals", help="Build deterministic portfolio signal JSON.")
    portfolio_signals.add_argument("--portfolio-dir", default="portfolio")
    portfolio_signals.add_argument("--assumptions-dir", default="assumptions")
    portfolio_signals.add_argument("--valuations-dir", default="valuations")
    portfolio_signals.add_argument("--workbook", help="Also export the workbook after writing signals.")
    portfolio_signals.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    portfolio_refresh = portfolio_subparsers.add_parser("refresh", help="Refresh local research, valuations, signals, and workbook.")
    portfolio_refresh.add_argument("--portfolio-dir", default="portfolio")
    portfolio_refresh.add_argument("--workbook", default="portfolio/portfolio.xlsx")
    portfolio_refresh.add_argument("--assumptions-dir", default="assumptions")
    portfolio_refresh.add_argument("--valuations-dir", default="valuations")
    portfolio_refresh.add_argument("--offline", action="store_true", help="Use only local cached research data.")
    portfolio_refresh.add_argument("--refresh", action="store_true", help="Refresh provider caches during online research.")
    portfolio_refresh.add_argument("--include-sensitivity", action="store_true")
    portfolio_refresh.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    rsu_tax = subparsers.add_parser("rsu-tax", help="Estimate Israeli Section 102 RSU sale taxes.")
    rsu_tax.add_argument("--ticker", help="Stock ticker used to fetch grant and sale prices.")
    rsu_tax.add_argument("--grant-date", help="Grant date as YYYY-MM-DD. Used for grant baseline and 2-year test.")
    rsu_tax.add_argument("--shares", type=float)
    rsu_tax.add_argument("--grant-price-usd", type=float, help="Manual grant baseline override.")
    rsu_tax.add_argument("--sale-price-usd", type=float, help="Manual sale price override.")
    rsu_tax.add_argument("--fx-usd-ils", type=float, help="Manual USD/ILS override.")
    rsu_tax.add_argument("--ordinary-tax-rate", type=float, help="Marginal ordinary tax rate, e.g. 0.47 or 47.")
    rsu_tax.add_argument("--sale-fees-ils", type=float, default=0.0)
    rsu_tax.add_argument("--capital-gain-offset-ils", type=float, default=0.0)
    rsu_tax.add_argument("--salary-ytd-ils", type=float)
    scenario_group = rsu_tax.add_mutually_exclusive_group()
    scenario_group.add_argument("--qualified-102", action="store_true", help="Force qualified Section 102 output.")
    scenario_group.add_argument("--early-sale", action="store_true", help="Force early/non-compliant sale output.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    research_root = getattr(args, "research_root", None) or os.getenv("RESEARCH_HOME")
    try:
        if args.command == "quickstart":
            app = _app(args, research_root=research_root)
            result = app.research.quickstart(args.ticker, offline=args.offline, refresh=args.refresh)
            _print_research_result(result)
            _print_quickstart_next_steps(result, offline=args.offline)
        elif args.command == "research":
            app = _app(args, research_root=research_root)
            if args.research_command == "start":
                result = app.research.start(args.ticker, offline=args.offline, refresh=args.refresh)
                _print_research_result(result)
            elif args.research_command == "ingest":
                result = app.research.ingest(args.ticker, offline=args.offline, refresh=args.refresh)
                _print_research_result(result)
            elif args.research_command == "metrics":
                _print_research_result(app.research.metrics(args.ticker))
            else:
                parser.error(f"Unknown research command: {args.research_command}")
        elif args.command == "assumptions":
            app = _app(args, research_root=research_root)
            if args.assumptions_command == "init":
                result = app.valuation.init_assumptions(
                    args.ticker,
                    model=args.model,
                    scenario=args.scenario,
                    output_path=args.output,
                )
                print(f"Wrote assumptions template: {result.data['assumptionsPath']}")
            elif args.assumptions_command == "validate":
                result = app.valuation.validate_assumptions(args.path)
                print(_render_validation_result(args.path, result))
                if result.errors:
                    return 2
            else:
                parser.error(f"Unknown assumptions command: {args.assumptions_command}")
        elif args.command == "value":
            app = _app(args, research_root=research_root)
            if args.target == "compare":
                if not args.compare_ticker:
                    parser.error("value compare requires a ticker")
                if not args.assumptions:
                    parser.error("value compare requires at least two --assumptions files")
                result = app.valuation.compare(
                    args.compare_ticker,
                    args.assumptions,
                    include_sensitivity=args.include_sensitivity,
                )
                _write_or_print(render_comparison(result.data, args.format), args.output)
            else:
                if args.compare_ticker:
                    raise ValueError("value accepts only one ticker unless using 'value compare <ticker>'")
                if not args.assumptions or len(args.assumptions) != 1:
                    parser.error("value requires exactly one --assumptions file")
                result = app.valuation.run(
                    args.target,
                    args.assumptions[0],
                    include_sensitivity=args.include_sensitivity,
                    include_debug=args.include_debug,
                    export_context=args.export_agent_context,
                )
                _write_or_print(render_valuation_result(result.data, args.format), args.output)
        elif args.command == "reverse-dcf":
            app = _app(args, research_root=research_root)
            result = app.valuation.run(
                args.ticker,
                args.assumptions,
                include_sensitivity=False,
                include_debug=args.include_debug,
                export_context=args.export_agent_context,
            )
            if result.data.get("model") != "reverse-dcf":
                raise ValueError("reverse-dcf requires an assumptions file with model reverse-dcf")
            _write_or_print(render_valuation_result(result.data, args.format), args.output)
        elif args.command == "onboarding":
            if args.onboarding_command == "init":
                _resolve_interactive_onboarding(args)
                horizon_min, horizon_max = _parse_horizon_range(args.horizon)
                app = _app(args, research_root=research_root, portfolio_dir=args.portfolio_dir)
                result = app.profile.init(
                    benchmark=args.benchmark,
                    horizon_min_years=horizon_min,
                    horizon_max_years=horizon_max,
                    ideas_per_month=args.ideas_per_month,
                    required_margin_of_safety=_parse_cli_rate(args.margin_of_safety, "margin-of-safety"),
                    max_position_size=_parse_cli_rate(args.max_position_size, "max-position-size"),
                    focus_areas=args.focus,
                    avoid_areas=args.avoid,
                    external_exposures=[_parse_external_exposure(value) for value in args.external_exposure],
                    other_portfolios=[_parse_other_portfolio(value) for value in args.other_portfolio],
                    external_exposure_affects_active_portfolio=args.external_exposure_affects_active_portfolio,
                    overwrite=args.overwrite,
                )
                print(render_portfolio_summary(result.data), end="")
            else:
                parser.error(f"Unknown onboarding command: {args.onboarding_command}")
        elif args.command == "portfolio":
            app = _portfolio_app(args, research_root=research_root)
            if args.portfolio_command == "init":
                result = app.portfolio.init(args.output)
                print(render_portfolio_summary(result.data), end="")
            elif args.portfolio_command == "import":
                result = app.portfolio.import_workbook(args.workbook)
                print(render_portfolio_summary(result.data), end="")
            elif args.portfolio_command == "export":
                result = app.portfolio.export_workbook(args.workbook)
                print(render_portfolio_summary(result.data), end="")
            elif args.portfolio_command == "value":
                result = app.portfolio.value(include_sensitivity=args.include_sensitivity)
                print(render_portfolio_summary({"message": "portfolio valuations completed", **result.data}), end="")
                if result.errors:
                    return 2
            elif args.portfolio_command == "signals":
                result = app.portfolio.signals(write=True, workbook_path=args.workbook)
                print(
                    render_portfolio_summary(
                        {
                            "message": "portfolio signals completed",
                            "signals": len(result.data.get("rows", [])),
                            "blocked": sum(1 for row in result.data.get("rows", []) if row.get("dataQuality") == "blocked"),
                        }
                    ),
                    end="",
                )
            elif args.portfolio_command == "refresh":
                result = app.portfolio.refresh(
                    workbook_path=args.workbook,
                    offline=args.offline,
                    refresh=args.refresh,
                    include_sensitivity=args.include_sensitivity,
                )
                print(
                    render_portfolio_summary(
                        {
                            "message": "portfolio refresh completed",
                            "research": len(result.data.get("research", [])),
                            "valued": result.data.get("valuation", {}).get("valuedCount", 0),
                            "signals": result.data.get("signals", {}).get("count", 0),
                            "errors": result.errors,
                        }
                    ),
                    end="",
                )
                if result.errors:
                    return 2
            else:
                parser.error(f"Unknown portfolio command: {args.portfolio_command}")
        elif args.command == "rsu-tax":
            inputs = _rsu_inputs_from_args(args, research_root=research_root)
            print(render_rsu_tax_summary(calculate_rsu_tax(inputs)))
        else:
            parser.error(f"Unknown command: {args.command}")
        return 0
    except (ProviderError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        close_logging()


def _print_research_result(result: OperationResult) -> None:
    for message in result.data.get("messages", []):
        print(message)
    for warning in result.warnings:
        print(f"Warning: {warning.message}", file=sys.stderr)


def _print_quickstart_next_steps(result: OperationResult, offline: bool = False) -> None:
    ticker = result.data["ticker"]
    company_dir = Path(result.data["companyDir"])
    print()
    print("Quickstart artifact paths:")
    print(f"- Research folder: {company_dir}")
    print(f"- Company identity: {company_dir / 'company.json'}")
    print(f"- Metrics summary: {company_dir / 'metrics' / 'metrics.md'}")
    print(f"- Extracted filing sections: {company_dir / 'extracted'}")
    print(f"- Filing chunk index: {company_dir / 'index' / 'filing_chunks.jsonl'}")
    if offline:
        print()
        print("Offline mode only created the local workspace. Run online quickstart or ingest to fetch data.")
    print()
    print("Copy-ready agent prompts:")
    print(f"- Use the investor-toolkit skill. Refresh local data for {ticker}, then summarize the latest filing risks with citations.")
    print(f"- Use the investor-toolkit skill. Build a business quality memo for {ticker} from local filings and metrics.")
    print(f"- Use the investor-toolkit skill. Draft a bear case for {ticker} and separate evidence from interpretation.")


def _render_validation_result(path: str | Path, result: OperationResult) -> str:
    if result.errors:
        lines = [f"Invalid assumptions file: {path}", "", "Errors:"]
        lines.extend(f"- {error}" for error in result.errors)
    else:
        lines = [f"Valid assumptions file: {path}"]
    warnings = result.data.get("warnings", [])
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning.get('message')}" for warning in warnings)
    return "\n".join(lines)


def _app(
    args: argparse.Namespace,
    research_root: str | Path | None = None,
    portfolio_dir: str | Path | None = None,
    assumptions_dir: str | Path | None = None,
    valuations_dir: str | Path | None = None,
) -> InvestorApplication:
    context = AppContext.from_env(
        cwd=Path.cwd(),
        research_root=research_root,
        portfolio_dir=portfolio_dir,
        assumptions_dir=assumptions_dir,
        valuations_dir=valuations_dir,
    )
    return InvestorApplication(context)


def _portfolio_app(args: argparse.Namespace, research_root: str | Path | None = None) -> InvestorApplication:
    portfolio_dir = getattr(args, "portfolio_dir", None)
    if not portfolio_dir and getattr(args, "portfolio_command", "") in {"init", "import", "export"}:
        workbook = getattr(args, "output", None) or getattr(args, "workbook", None)
        if workbook:
            portfolio_dir = Path(workbook).parent
    return _app(
        args,
        research_root=research_root,
        portfolio_dir=portfolio_dir,
        assumptions_dir=getattr(args, "assumptions_dir", None),
        valuations_dir=getattr(args, "valuations_dir", None),
    )


def _write_or_print(content: str, output_path: str | None) -> None:
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Wrote output: {path}")
    else:
        print(content, end="")


def _resolve_interactive_onboarding(args: argparse.Namespace) -> None:
    if not getattr(args, "interactive", False):
        return
    if not sys.stdin.isatty():
        raise ValueError("--interactive requires a terminal")
    args.benchmark = _prompt_default("Benchmark", args.benchmark)
    args.horizon = _prompt_default("Time horizon in years", args.horizon)
    focus = _prompt_default("Businesses you understand, comma-separated", ", ".join(args.focus or ["software", "AI infrastructure"]))
    args.focus = [item.strip() for item in focus.split(",") if item.strip()]
    args.ideas_per_month = int(_prompt_default("High-signal ideas per month", str(args.ideas_per_month)))
    args.external_exposure_affects_active_portfolio = _prompt_yes_no(
        "Should external RSUs/portfolios affect this active portfolio?",
        args.external_exposure_affects_active_portfolio,
    )


def _prompt_default(label: str, default: str) -> str:
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_yes_no(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "true", "1"}


def _parse_horizon_range(value: str) -> tuple[int, int]:
    cleaned = str(value or "").strip().replace(" ", "")
    if not cleaned:
        raise ValueError("horizon cannot be empty")
    if "-" in cleaned:
        left, right = cleaned.split("-", 1)
        minimum = int(left)
        maximum = int(right)
    else:
        minimum = maximum = int(cleaned)
    if minimum < 1 or maximum < 1 or maximum < minimum:
        raise ValueError("horizon must be a positive year or range, for example 5-10")
    return minimum, maximum


def _parse_cli_rate(value: str | float | int, flag_name: str) -> float:
    if isinstance(value, str):
        text = value.strip()
        is_percent = text.endswith("%")
        if is_percent:
            text = text[:-1]
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(f"{flag_name} must be numeric or a percentage") from exc
        rate = parsed / 100 if is_percent or parsed > 1 else parsed
    else:
        rate = float(value)
        if rate > 1:
            rate = rate / 100
    if rate < 0 or rate > 1:
        raise ValueError(f"{flag_name} must be between 0 and 100%")
    return rate


def _parse_external_exposure(value: str) -> dict[str, object]:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) not in {3, 4}:
        raise ValueError("external-exposure must be TICKER:AMOUNT:CURRENCY[:TYPE]")
    ticker, amount, currency = parts[:3]
    exposure_type = parts[3] if len(parts) == 4 else "external_stock"
    return {
        "ticker": normalize_ticker(ticker),
        "amount": float(amount.replace(",", "")),
        "currency": currency.upper(),
        "type": exposure_type or "external_stock",
        "includeInActivePortfolio": False,
    }


def _parse_other_portfolio(value: str) -> dict[str, object]:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != 3:
        raise ValueError("other-portfolio must be NAME:AMOUNT:CURRENCY")
    name, amount, currency = parts
    return {
        "name": name,
        "amount": float(amount.replace(",", "")),
        "currency": currency.upper(),
        "includeInActivePortfolio": False,
    }


def _rsu_inputs_from_args(args: argparse.Namespace, research_root: str | None = None) -> RsuTaxInputs:
    ticker = _resolve_interactive_ticker(args)
    grant_date = _resolve_interactive_grant_date(args, ticker)
    shares = _required_number(args.shares, "shares", "Shares")
    ordinary_tax_rate = normalize_rate(
        _required_number(
            args.ordinary_tax_rate,
            "ordinary-tax-rate",
            "Marginal ordinary tax rate, e.g. 0.47 or 47",
        )
    )
    sale_date = date.today()
    warnings: list[str] = []

    grant_price_usd = args.grant_price_usd
    sale_price_usd = args.sale_price_usd
    grant_price_source = "manual --grant-price-usd" if grant_price_usd is not None else None
    sale_price_source = "manual --sale-price-usd" if sale_price_usd is not None else None

    if grant_price_usd is None or sale_price_usd is None:
        if ticker is None:
            grant_price_usd = _required_number(
                grant_price_usd,
                "grant-price-usd",
                "Grant FMV / 30-day average per share in USD",
            )
            grant_price_source = "manual input"
            sale_price_usd = _required_number(
                sale_price_usd,
                "sale-price-usd",
                "Sale price per share in USD",
            )
            sale_price_source = "manual input"
        else:
            if grant_price_usd is None and grant_date is None:
                if not sys.stdin.isatty():
                    raise ValueError("missing required --grant-date or --grant-price-usd")
                grant_price_usd = _required_number(
                    None,
                    "grant-price-usd",
                    "Grant FMV / 30-day average per share in USD",
                )
                grant_price_source = "manual input"
            try:
                market = _resolve_market_prices(
                    ticker=ticker,
                    grant_date=grant_date,
                    sale_date=sale_date,
                    research_root=research_root,
                    need_grant=grant_price_usd is None,
                    need_sale=sale_price_usd is None,
                )
            except (ProviderError, ValueError) as exc:
                if not sys.stdin.isatty():
                    raise
                warnings.append(f"Could not fetch market prices automatically: {exc}")
                market = {}
            if grant_price_usd is None:
                if "grant_price_usd" in market:
                    grant_price_usd = market["grant_price_usd"]
                    grant_price_source = market["grant_price_source"]
                else:
                    grant_price_usd = _required_number(
                        None,
                        "grant-price-usd",
                        "Grant FMV / 30-day average per share in USD",
                    )
                    grant_price_source = "manual input after market-data fetch failure"
            if sale_price_usd is None:
                if "sale_price_usd" in market:
                    sale_price_usd = market["sale_price_usd"]
                    sale_price_source = market["sale_price_source"]
                else:
                    sale_price_usd = _required_number(
                        None,
                        "sale-price-usd",
                        "Sale price per share in USD",
                    )
                    sale_price_source = "manual input after market-data fetch failure"

    fx_usd_ils = args.fx_usd_ils
    fx_source = "manual --fx-usd-ils" if fx_usd_ils is not None else None
    if fx_usd_ils is None:
        try:
            storage = ResearchStorage(Path.cwd(), research_root=research_root)
            fx_rate = ExchangeRateProvider(
                storage.research_root.parent,
                research_root=storage.research_root,
            ).get_usd_ils_rate()
            fx_usd_ils = fx_rate.rate
            fx_source = fx_rate.source
        except ProviderError as exc:
            if sys.stdin.isatty():
                warnings.append(f"Could not fetch USD/ILS FX automatically: {exc}")
                fx_usd_ils = _required_number(None, "fx-usd-ils", "USD/ILS exchange rate")
                fx_source = "manual input after FX fetch failure"
            else:
                raise ValueError(f"could not fetch USD/ILS FX automatically; pass --fx-usd-ils ({exc})") from exc

    selected_scenario, scenario_source = _resolve_rsu_scenario(args, grant_date, sale_date)
    return RsuTaxInputs(
        shares=shares,
        grant_price_usd=grant_price_usd,
        sale_price_usd=sale_price_usd,
        fx_usd_ils=fx_usd_ils,
        ordinary_tax_rate=ordinary_tax_rate,
        sale_fees_ils=args.sale_fees_ils or 0.0,
        capital_gain_offset_ils=args.capital_gain_offset_ils or 0.0,
        salary_ytd_ils=args.salary_ytd_ils,
        ticker=ticker,
        grant_date=grant_date,
        sale_date=sale_date,
        grant_price_source=grant_price_source,
        sale_price_source=sale_price_source,
        fx_source=fx_source,
        selected_scenario=selected_scenario,
        scenario_source=scenario_source,
        warnings=tuple(warnings),
    )


def _resolve_interactive_ticker(args: argparse.Namespace) -> str | None:
    if args.ticker:
        return normalize_ticker(args.ticker)
    if args.grant_price_usd is not None and args.sale_price_usd is not None:
        return None
    raw = _optional_text(None, "ticker", "Ticker (blank for manual price entry)")
    return normalize_ticker(raw) if raw else None


def _resolve_interactive_grant_date(args: argparse.Namespace, ticker: str | None) -> date | None:
    grant_date = _optional_date(args.grant_date, "grant-date")
    if grant_date is not None or ticker is None:
        return grant_date
    if args.grant_price_usd is not None and args.sale_price_usd is not None:
        return grant_date
    return _optional_date(
        _optional_text(None, "grant-date", "Grant date (YYYY-MM-DD, blank for manual grant price)"),
        "grant-date",
    )


def _resolve_market_prices(
    ticker: str,
    grant_date: date | None,
    sale_date: date,
    research_root: str | None,
    need_grant: bool = True,
    need_sale: bool = True,
) -> dict[str, float | str]:
    storage = ResearchStorage(Path.cwd(), research_root=research_root)
    company_dir = storage.ensure_company_dirs(ticker)
    start = (
        grant_date - timedelta(days=29)
        if need_grant and grant_date is not None
        else sale_date - timedelta(days=14)
    )
    end = sale_date + timedelta(days=1)
    market = StooqMarketDataProvider()
    prices = market.get_historical_prices(ticker, company_dir, start=start, end=end)
    try:
        grant = average_grant_price(prices, grant_date) if need_grant and grant_date is not None else None
        sale = latest_sale_price(prices, sale_date) if need_sale else None
    except ValueError:
        prices = market.get_historical_prices(ticker, company_dir, start=start, end=end, refresh=True)
        grant = average_grant_price(prices, grant_date) if need_grant and grant_date is not None else None
        sale = latest_sale_price(prices, sale_date) if need_sale else None
    resolved: dict[str, float | str] = {}
    if grant is not None:
        resolved["grant_price_usd"] = grant.price_usd
        resolved["grant_price_source"] = grant.source
    if sale is not None:
        resolved["sale_price_usd"] = sale.price_usd
        resolved["sale_price_source"] = sale.source
    return resolved


def _resolve_rsu_scenario(
    args: argparse.Namespace,
    grant_date: date | None,
    sale_date: date,
) -> tuple[str | None, str | None]:
    if getattr(args, "qualified_102", False):
        return SCENARIO_QUALIFIED, "forced by --qualified-102"
    if getattr(args, "early_sale", False):
        return SCENARIO_EARLY, "forced by --early-sale"
    if grant_date is None:
        return None, None
    selected = infer_102_scenario(grant_date, sale_date)
    anniversary = add_years(grant_date, 2)
    return selected, f"inferred from grant date; 2-year anniversary is {anniversary.isoformat()}"


def _optional_date(value: str | None, flag_name: str) -> date | None:
    parsed = parse_iso_date(value)
    if value and parsed is None:
        raise ValueError(f"{flag_name} must be YYYY-MM-DD")
    return parsed


def _optional_text(value: str | None, flag_name: str, prompt: str) -> str | None:
    if value:
        return value.strip()
    if not sys.stdin.isatty():
        return None
    try:
        raw = input(f"{prompt}: ").strip()
    except EOFError:
        raw = ""
    return raw or None


def _required_number(value: float | None, flag_name: str, prompt: str) -> float:
    if value is not None:
        return value
    if sys.stdin.isatty():
        try:
            raw = input(f"{prompt}: ").strip()
        except EOFError:
            raw = ""
        if raw:
            try:
                return float(raw.replace(",", ""))
            except ValueError as exc:
                raise ValueError(f"{flag_name} must be numeric") from exc
    raise ValueError(f"missing required --{flag_name}")


if __name__ == "__main__":
    raise SystemExit(main())
