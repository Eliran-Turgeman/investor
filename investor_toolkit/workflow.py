from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .financials import FinancialNormalizer
from .filings import FilingExtractor, filing_output_dir, load_filing_metadata
from .indexing import build_index
from .logging_utils import configure_logging
from .metrics.engine import write_metrics
from .models import CompanyIdentity, FilingMetadata
from .providers import ProviderError, SecProvider, StooqMarketDataProvider
from .storage import ResearchStorage
from .utils import normalize_ticker, read_json


@dataclass
class WorkflowResult:
    ticker: str
    company_dir: Path
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.messages.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class ResearchWorkflow:
    def __init__(self, cwd: str | Path = ".", research_root: str | Path | None = None) -> None:
        self.cwd = Path(cwd).resolve()
        self.storage = ResearchStorage(self.cwd, research_root=research_root)
        self.logger = configure_logging(self.storage.research_root.parent)
        self.sec = SecProvider(
            self.storage.research_root.parent,
            research_root=self.storage.research_root,
        )
        self.market = StooqMarketDataProvider()
        self.normalizer = FinancialNormalizer()
        self.extractor = FilingExtractor()

    def start(self, ticker: str, offline: bool = False, refresh: bool = False) -> WorkflowResult:
        ticker = normalize_ticker(ticker)
        existing = self.storage.load_company(ticker)
        if offline:
            company = existing or CompanyIdentity(ticker=ticker, name=ticker)
        else:
            company = self.sec.resolve_company(ticker, refresh=refresh)
        self.storage.write_company(company)
        result = WorkflowResult(ticker=ticker, company_dir=self.storage.company_dir(ticker))
        result.add(f"Created local research folder: {result.company_dir}")
        self.logger.info("ResearchStarted ticker=%s offline=%s", ticker, offline)
        if offline:
            result.add("Offline mode: skipped SEC and market-data network calls.")
            return result
        try:
            ingest_result = self.ingest(ticker, refresh=refresh, offline=False)
            result.messages.extend(ingest_result.messages)
            result.warnings.extend(ingest_result.warnings)
        except ProviderError as exc:
            result.warn(f"Research folder was created, but ingestion did not complete: {exc}")
        return result

    def ingest(self, ticker: str, refresh: bool = False, offline: bool = False) -> WorkflowResult:
        ticker = normalize_ticker(ticker)
        company = self.storage.load_company(ticker)
        if company is None:
            if offline:
                company = CompanyIdentity(ticker=ticker, name=ticker)
            else:
                company = self.sec.resolve_company(ticker, refresh=refresh)
            self.storage.write_company(company)
        company_dir = self.storage.ensure_company_dirs(ticker)
        result = WorkflowResult(ticker=ticker, company_dir=company_dir)

        filings: list[FilingMetadata] = load_filing_metadata(company_dir)
        if not offline:
            self.logger.info("ProviderRequest provider=SEC event=GetFilings ticker=%s", ticker)
            filings = self.sec.get_filings(company, company_dir, years=2, refresh=refresh)
            result.add(f"Fetched metadata for {len(filings)} recent 10-K/10-Q filings.")
            downloaded = 0
            for filing in filings:
                try:
                    self.sec.download_filing(filing, company_dir, refresh=refresh)
                    downloaded += 1
                    self.logger.info(
                        "FilingDownloaded ticker=%s formType=%s accession=%s",
                        ticker,
                        filing.formType,
                        filing.accessionNumber,
                    )
                except ProviderError as exc:
                    result.warn(f"Could not download {filing.formType} {filing.filingDate}: {exc}")
            result.add(f"Downloaded or reused {downloaded} raw filings.")

            try:
                facts = self.sec.get_company_facts(company, company_dir, refresh=refresh)
                rows = self.normalizer.normalize_company_facts(facts, ticker=ticker)
                self.normalizer.write_normalized(company_dir, rows)
                result.add(f"Normalized {len(rows)} annual SEC financial periods.")
            except ProviderError as exc:
                result.warn(f"Could not import SEC company facts: {exc}")

            try:
                prices = self.market.get_historical_prices(
                    ticker,
                    company_dir,
                    start=date.today() - timedelta(days=365 * 5),
                    end=date.today(),
                    refresh=refresh,
                )
                provider = prices[-1].get("source", "market provider") if prices else "market provider"
                result.add(f"Imported {len(prices)} historical price rows from {provider}.")
            except ProviderError as exc:
                result.warn(f"Could not import market data: {exc}")
        else:
            facts = read_json(company_dir / "data" / "company_facts.json", None)
            if facts:
                rows = self.normalizer.normalize_company_facts(facts, ticker=ticker)
                self.normalizer.write_normalized(company_dir, rows)
                result.add(f"Offline mode: rebuilt {len(rows)} annual SEC financial periods from local facts.")

        extracted = self.extract_local_filings(ticker, filings)
        result.add(f"Extracted filing sections for {extracted} local filings.")
        chunks = build_index(company_dir, ticker)
        result.add(f"Indexed {len(chunks)} local filing chunks.")
        try:
            metrics = write_metrics(company_dir, ticker)
            result.add(f"Calculated metrics for {len(metrics.get('periods', []))} periods.")
        except Exception as exc:
            result.warn(f"Could not calculate metrics yet: {exc}")
        self.logger.info("ResearchIngested ticker=%s warnings=%s", ticker, len(result.warnings))
        return result

    def extract_local_filings(self, ticker: str, filings: list[FilingMetadata] | None = None) -> int:
        company_dir = self.storage.company_dir(ticker)
        filings = filings or load_filing_metadata(company_dir)
        if not filings:
            return 0
        count = 0
        for filing in filings:
            raw_path = self._raw_path_for_filing(company_dir, filing)
            if raw_path is None:
                continue
            out_dir = filing_output_dir(company_dir, filing)
            results = self.extractor.extract(filing, raw_path, out_dir, root=self.storage.root)
            extracted = sum(1 for item in results if item.status == "Extracted")
            self.logger.info(
                "FilingExtracted ticker=%s accession=%s sections=%s",
                ticker,
                filing.accessionNumber,
                extracted,
            )
            count += 1
        return count

    def metrics(self, ticker: str) -> WorkflowResult:
        ticker = normalize_ticker(ticker)
        company_dir = self.storage.company_dir(ticker)
        result = WorkflowResult(ticker=ticker, company_dir=company_dir)
        metrics = write_metrics(company_dir, ticker)
        result.add(f"Wrote metrics for {len(metrics.get('periods', []))} periods.")
        return result

    @staticmethod
    def _raw_path_for_filing(company_dir: Path, filing: FilingMetadata) -> Path | None:
        label = filing.localLabel
        if not label:
            return None
        matches = sorted((company_dir / "filings" / "raw").glob(f"{label}.*"))
        return matches[0] if matches else None
