from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CompanyIdentity
from .utils import append_text, normalize_ticker, read_json, utc_now_iso, write_json, write_text


class ResearchStorage:
    def __init__(self, root: str | Path = ".", research_root: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        if research_root is None:
            self.research_root = self.root / "research"
        else:
            configured_root = Path(research_root)
            self.research_root = (
                configured_root.resolve()
                if configured_root.is_absolute()
                else (self.root / configured_root).resolve()
            )

    def ensure_workspace(self) -> None:
        self.research_root.mkdir(parents=True, exist_ok=True)
        config = self.research_root.parent / "research.config.json"
        if not config.exists():
            write_json(
                config,
                {
                    "createdAt": utc_now_iso(),
                    "researchRoot": "research",
                    "secUserAgent": "InvestorResearchAssistant/0.1 (set SEC_USER_AGENT)",
                    "marketDataProvider": "yahoo",
                },
            )

    def company_dir(self, ticker: str) -> Path:
        return self.research_root / normalize_ticker(ticker)

    def ensure_company_dirs(self, ticker: str) -> Path:
        self.ensure_workspace()
        base = self.company_dir(ticker)
        dirs = [
            base / "filings" / "raw",
            base / "filings" / "metadata",
            base / "extracted",
            base / "data" / "provider_responses" / "sec",
            base / "data" / "provider_responses" / "yahoo",
            base / "data" / "provider_responses" / "stooq",
            base / "metrics",
            base / "index",
        ]
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
        return base

    def write_company(self, company: CompanyIdentity) -> Path:
        base = self.ensure_company_dirs(company.ticker)
        path = base / "company.json"
        write_json(path, company.to_dict())
        return path

    def load_company(self, ticker: str) -> CompanyIdentity | None:
        path = self.company_dir(ticker) / "company.json"
        if not path.exists():
            return None
        return CompanyIdentity.from_dict(read_json(path, {}))

    def write_company_file(self, ticker: str, relative_path: str | Path, content: str) -> Path:
        path = self.company_path(ticker, relative_path)
        write_text(path, content)
        return path

    def append_company_file(self, ticker: str, relative_path: str | Path, content: str) -> Path:
        path = self.company_path(ticker, relative_path)
        append_text(path, content)
        return path

    def read_company_json(self, ticker: str, relative_path: str | Path, default: Any = None) -> Any:
        return read_json(self.company_path(ticker, relative_path), default)

    def write_company_json(self, ticker: str, relative_path: str | Path, data: Any) -> Path:
        path = self.company_path(ticker, relative_path)
        write_json(path, data)
        return path

    def company_path(self, ticker: str, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError(f"Company path must be relative: {relative_path}")
        base = self.company_dir(ticker).resolve()
        path = (base / relative).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"Company path escapes ticker directory: {relative_path}") from exc
        return path

    def relative_to_root(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def upsert_generated_block(
        self,
        path: Path,
        block_name: str,
        content: str,
        append_if_missing: bool = True,
    ) -> None:
        start = f"<!-- BEGIN GENERATED: {block_name} -->"
        end = f"<!-- END GENERATED: {block_name} -->"
        block = f"{start}\n{content.rstrip()}\n{end}\n"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if start in existing and end in existing:
            before = existing.split(start, 1)[0]
            after = existing.split(end, 1)[1]
            write_text(path, before.rstrip() + "\n\n" + block + after.lstrip())
        elif append_if_missing:
            suffix = "" if not existing or existing.endswith("\n") else "\n"
            write_text(path, existing + suffix + "\n" + block)
        else:
            write_text(path, block)
