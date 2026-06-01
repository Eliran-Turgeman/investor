from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from .logging_utils import close_logging
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
    compare_valuations,
    export_agent_context,
    init_assumptions_file,
    load_assumptions,
    render_comparison,
    render_validation_report,
    render_valuation_result,
    run_valuation,
    validate_assumptions_file,
)
from .workflow import ResearchWorkflow, WorkflowResult


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

    research = subparsers.add_parser("research", help="Research data ingestion and metrics commands.")
    research_subparsers = research.add_subparsers(dest="research_command", required=True)

    start = research_subparsers.add_parser("start", help="Initialize local research for a ticker.")
    start.add_argument("ticker")
    start.add_argument("--offline", action="store_true", help="Create local artifacts without network calls.")
    start.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    start.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    ingest = research_subparsers.add_parser("ingest", help="Refresh source data for a ticker.")
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
        if args.command == "research":
            workflow = ResearchWorkflow(Path.cwd(), research_root=research_root)
            if args.research_command == "start":
                result = workflow.start(args.ticker, offline=args.offline, refresh=args.refresh)
                _print_result(result)
            elif args.research_command == "ingest":
                result = workflow.ingest(args.ticker, offline=args.offline, refresh=args.refresh)
                _print_result(result)
            elif args.research_command == "metrics":
                _print_result(workflow.metrics(args.ticker))
            else:
                parser.error(f"Unknown research command: {args.research_command}")
        elif args.command == "assumptions":
            if args.assumptions_command == "init":
                path = init_assumptions_file(
                    args.ticker,
                    model=args.model,
                    scenario=args.scenario,
                    output_path=args.output,
                    cwd=Path.cwd(),
                    research_root=research_root,
                )
                print(f"Wrote assumptions template: {path}")
            elif args.assumptions_command == "validate":
                report = validate_assumptions_file(args.path, cwd=Path.cwd(), research_root=research_root)
                print(render_validation_report(args.path, report))
                if report.errors:
                    return 2
            else:
                parser.error(f"Unknown assumptions command: {args.assumptions_command}")
        elif args.command == "value":
            if args.target == "compare":
                if not args.compare_ticker:
                    parser.error("value compare requires a ticker")
                if not args.assumptions:
                    parser.error("value compare requires at least two --assumptions files")
                comparison = compare_valuations(
                    args.compare_ticker,
                    args.assumptions,
                    cwd=Path.cwd(),
                    research_root=research_root,
                    include_sensitivity=args.include_sensitivity,
                )
                _write_or_print(render_comparison(comparison, args.format), args.output)
            else:
                if args.compare_ticker:
                    raise ValueError("value accepts only one ticker unless using 'value compare <ticker>'")
                if not args.assumptions or len(args.assumptions) != 1:
                    parser.error("value requires exactly one --assumptions file")
                result = run_valuation(
                    args.target,
                    args.assumptions[0],
                    cwd=Path.cwd(),
                    research_root=research_root,
                    include_sensitivity=args.include_sensitivity,
                    include_debug=args.include_debug,
                )
                if args.export_agent_context:
                    paths = export_agent_context(
                        result,
                        load_assumptions(args.assumptions[0], cwd=Path.cwd()),
                        cwd=Path.cwd(),
                    )
                    result["agentContext"] = paths
                _write_or_print(render_valuation_result(result, args.format), args.output)
        elif args.command == "reverse-dcf":
            result = run_valuation(
                args.ticker,
                args.assumptions,
                cwd=Path.cwd(),
                research_root=research_root,
                include_sensitivity=False,
                include_debug=args.include_debug,
            )
            if result.get("model") != "reverse-dcf":
                raise ValueError("reverse-dcf requires an assumptions file with model reverse-dcf")
            if args.export_agent_context:
                paths = export_agent_context(
                    result,
                    load_assumptions(args.assumptions, cwd=Path.cwd()),
                    cwd=Path.cwd(),
                )
                result["agentContext"] = paths
            _write_or_print(render_valuation_result(result, args.format), args.output)
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


def _print_result(result: WorkflowResult) -> None:
    for message in result.messages:
        print(message)
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)


def _write_or_print(content: str, output_path: str | None) -> None:
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Wrote output: {path}")
    else:
        print(content, end="")


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
