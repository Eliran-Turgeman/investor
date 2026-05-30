from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .logging_utils import close_logging
from .providers import ProviderError
from .workflow import ResearchWorkflow, WorkflowResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research",
        description="Local-first value investing research data provider.",
    )
    parser.add_argument(
        "--research-root",
        help="Directory that contains ticker research folders. Defaults to ./research.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Initialize local research for a ticker.")
    start.add_argument("ticker")
    start.add_argument("--offline", action="store_true", help="Create local artifacts without network calls.")
    start.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    start.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    ingest = subparsers.add_parser("ingest", help="Refresh source data for a ticker.")
    ingest.add_argument("ticker")
    ingest.add_argument("--offline", action="store_true", help="Rebuild from local cached files only.")
    ingest.add_argument("--refresh", action="store_true", help="Refresh cached provider responses.")
    ingest.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    metrics = subparsers.add_parser("metrics", help="Calculate and write financial metrics.")
    metrics.add_argument("ticker")
    metrics.add_argument("--offline", action="store_true", help="Accepted for symmetry; metrics uses local data.")
    metrics.add_argument("--research-root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    research_root = getattr(args, "research_root", None) or os.getenv("RESEARCH_HOME")
    workflow = ResearchWorkflow(Path.cwd(), research_root=research_root)
    try:
        if args.command == "start":
            result = workflow.start(args.ticker, offline=args.offline, refresh=args.refresh)
            _print_result(result)
        elif args.command == "ingest":
            result = workflow.ingest(args.ticker, offline=args.offline, refresh=args.refresh)
            _print_result(result)
        elif args.command == "metrics":
            _print_result(workflow.metrics(args.ticker))
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


if __name__ == "__main__":
    raise SystemExit(main())
