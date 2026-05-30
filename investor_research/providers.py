from __future__ import annotations

import csv
import io
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import CompanyIdentity, FilingMetadata
from .utils import parse_iso_date, read_json, utc_now_iso, write_json, write_text


class ProviderError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, user_agent: str | None = None, timeout: int = 30) -> None:
        self.user_agent = user_agent or os.getenv(
            "SEC_USER_AGENT",
            "InvestorResearchAssistant/0.1 research@example.com",
        )
        self.timeout = timeout
        self._last_request = 0.0

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        elapsed = time.monotonic() - self._last_request
        if elapsed < 0.12:
            time.sleep(0.12 - elapsed)
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._last_request = time.monotonic()
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"HTTP {exc.code} while requesting {url}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Network error while requesting {url}: {exc.reason}") from exc

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return self.get_bytes(url, headers=headers).decode("utf-8", errors="replace")

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        import json

        try:
            return json.loads(self.get_text(url, headers=headers))
        except ValueError as exc:
            raise ProviderError(f"Invalid JSON response from {url}") from exc


class SecProvider:
    def __init__(self, root: Path, http: HttpClient | None = None) -> None:
        self.root = Path(root)
        self.http = http or HttpClient()
        self.global_cache = self.root / "research" / "_cache" / "sec"
        self.global_cache.mkdir(parents=True, exist_ok=True)

    def resolve_company(self, ticker: str, refresh: bool = False) -> CompanyIdentity:
        ticker = ticker.upper()
        data = self._cached_json(
            "company_tickers",
            "https://www.sec.gov/files/company_tickers.json",
            self.global_cache / "company_tickers.json",
            refresh=refresh,
        )
        rows = data.values() if isinstance(data, dict) else data
        for row in rows:
            if str(row.get("ticker", "")).upper() == ticker:
                return CompanyIdentity(
                    ticker=ticker,
                    name=str(row.get("title", ticker)),
                    cik=str(row.get("cik_str", "")).zfill(10),
                )
        raise ProviderError(f"Unknown SEC ticker: {ticker}")

    def get_submissions(self, company: CompanyIdentity, company_dir: Path, refresh: bool = False) -> dict[str, Any]:
        if not company.cik:
            raise ProviderError(f"Cannot fetch submissions for {company.ticker}: missing CIK")
        cache = company_dir / "data" / "provider_responses" / "sec" / "submissions.json"
        url = f"https://data.sec.gov/submissions/CIK{company.cik}.json"
        data = self._cached_json("submissions", url, cache, refresh=refresh)
        write_json(company_dir / "filings" / "metadata" / "submissions.json", data)
        return data

    def get_filings(
        self,
        company: CompanyIdentity,
        company_dir: Path,
        years: int = 2,
        forms: tuple[str, ...] = ("10-K", "10-Q"),
        refresh: bool = False,
    ) -> list[FilingMetadata]:
        submissions = self.get_submissions(company, company_dir, refresh=refresh)
        recent = submissions.get("filings", {}).get("recent", {})
        cutoff = date.today() - timedelta(days=365 * years)
        rows: list[FilingMetadata] = []
        forms_set = set(forms)
        length = len(recent.get("accessionNumber", []))
        for index in range(length):
            form_type = str(recent.get("form", [""] * length)[index])
            if form_type not in forms_set:
                continue
            filing_date = str(recent.get("filingDate", [""] * length)[index])
            parsed_date = parse_iso_date(filing_date)
            if parsed_date and parsed_date < cutoff:
                continue
            accession = str(recent.get("accessionNumber", [""] * length)[index])
            report_date = str(recent.get("reportDate", [""] * length)[index] or "")
            primary_document = str(recent.get("primaryDocument", [""] * length)[index] or "")
            fiscal_year = int(report_date[:4]) if report_date[:4].isdigit() else (
                int(filing_date[:4]) if filing_date[:4].isdigit() else None
            )
            fiscal_period = "FY" if form_type == "10-K" else str(
                recent.get("period", [""] * length)[index] or ""
            )
            accession_path = accession.replace("-", "")
            cik_no_zero = str(int(company.cik)) if company.cik else ""
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_no_zero}/"
                f"{accession_path}/{primary_document}"
            )
            label_year = str(fiscal_year or filing_date[:4] or "unknown")
            label_suffix = "10K" if form_type == "10-K" else f"{fiscal_period or 'Q'}-10Q"
            rows.append(
                FilingMetadata(
                    ticker=company.ticker,
                    cik=company.cik,
                    accessionNumber=accession,
                    formType=form_type,
                    filingDate=filing_date,
                    reportDate=report_date,
                    fiscalYear=fiscal_year,
                    fiscalPeriod=fiscal_period,
                    url=url,
                    primaryDocument=primary_document,
                    localLabel=f"{label_year}-{label_suffix}",
                )
            )
        write_json(
            company_dir / "filings" / "metadata" / "filings.json",
            [row.to_dict() for row in rows],
        )
        return rows

    def download_filing(
        self, filing: FilingMetadata, company_dir: Path, refresh: bool = False
    ) -> Path:
        suffix = ".html" if filing.primaryDocument.lower().endswith((".htm", ".html")) else ".txt"
        path = company_dir / "filings" / "raw" / f"{filing.localLabel}{suffix}"
        if path.exists() and not refresh:
            return path
        text = self.http.get_text(filing.url)
        write_text(path, text)
        return path

    def get_company_facts(
        self, company: CompanyIdentity, company_dir: Path, refresh: bool = False
    ) -> dict[str, Any]:
        if not company.cik:
            raise ProviderError(f"Cannot fetch company facts for {company.ticker}: missing CIK")
        cache = company_dir / "data" / "provider_responses" / "sec" / "company_facts.json"
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json"
        data = self._cached_json("company_facts", url, cache, refresh=refresh)
        write_json(company_dir / "data" / "company_facts.json", data)
        return data

    def _cached_json(self, name: str, url: str, cache: Path, refresh: bool = False) -> Any:
        cached = read_json(cache, None)
        if cached is not None and not refresh:
            if isinstance(cached, dict) and "response" in cached and "provider" in cached:
                return cached["response"]
            return cached
        response = self.http.get_json(url)
        write_json(
            cache,
            {
                "provider": "SEC",
                "fetchedAt": utc_now_iso(),
                "request": {"name": name, "url": url},
                "response": response,
            },
        )
        return response


class StooqMarketDataProvider:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    def get_historical_prices(
        self,
        ticker: str,
        company_dir: Path,
        start: date | None = None,
        end: date | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        start = start or (date.today() - timedelta(days=365 * 5))
        end = end or date.today()
        for provider_name in ("yahoo", "stooq"):
            cache = company_dir / "data" / "provider_responses" / provider_name / "historical_prices.json"
            cached = read_json(cache, None)
            if cached is not None and not refresh:
                response = cached.get("response", cached) if isinstance(cached, dict) else cached
                if isinstance(response, list):
                    write_json(company_dir / "data" / "prices.json", response)
                    self._write_prices_csv(company_dir / "data" / "prices.csv", response)
                    return response

        rows: list[dict[str, Any]] = []
        provider = "YAHOO"
        request: dict[str, Any] = {"ticker": ticker, "from": start.isoformat(), "to": end.isoformat()}
        stooq_key = os.getenv("STOOQ_API_KEY")
        if stooq_key:
            symbol = urllib.parse.quote(f"{ticker.lower()}.us")
            url = (
                "https://stooq.com/q/d/l/?"
                f"s={symbol}&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}&i=d"
                f"&apikey={urllib.parse.quote(stooq_key)}"
            )
            try:
                csv_text = self.http.get_text(url, headers={"User-Agent": "Mozilla/5.0"})
                rows = self._parse_stooq_csv(ticker, csv_text)
                provider = "STOOQ"
                request["url"] = url.replace(stooq_key, "<redacted>")
            except ProviderError:
                rows = []
        if not rows:
            url = self._yahoo_url(ticker, start, end)
            try:
                rows = self._parse_yahoo_chart(ticker, self.http.get_json(url, headers={"User-Agent": "Mozilla/5.0"}))
                provider = "YAHOO"
                request["url"] = url
            except ProviderError:
                rows = []
        cache = company_dir / "data" / "provider_responses" / provider.lower() / "historical_prices.json"
        write_json(
            cache,
            {
                "provider": provider,
                "fetchedAt": utc_now_iso(),
                "request": request,
                "response": rows,
            },
        )
        write_json(company_dir / "data" / "prices.json", rows)
        self._write_prices_csv(company_dir / "data" / "prices.csv", rows)
        return rows

    @staticmethod
    def _parse_stooq_csv(ticker: str, text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if not row.get("Date") or row.get("Close") in (None, "", "N/D"):
                continue
            try:
                rows.append(
                    {
                        "ticker": ticker.upper(),
                        "date": row["Date"],
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "adjustedClose": float(row["Close"]),
                        "volume": int(float(row["Volume"])),
                        "source": "STOOQ",
                    }
                )
            except (TypeError, ValueError):
                continue
        return rows

    @staticmethod
    def _yahoo_url(ticker: str, start: date, end: date) -> str:
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp())
        symbol = urllib.parse.quote(ticker.upper())
        return (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
        )

    @staticmethod
    def _parse_yahoo_chart(ticker: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        quote = (indicators.get("quote") or [{}])[0]
        adjclose = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            close = _list_get(quote.get("close"), index)
            if close is None:
                continue
            adjusted_close = _list_get(adjclose, index)
            rows.append(
                {
                    "ticker": ticker.upper(),
                    "date": datetime.fromtimestamp(int(timestamp), UTC).date().isoformat(),
                    "open": _list_get(quote.get("open"), index),
                    "high": _list_get(quote.get("high"), index),
                    "low": _list_get(quote.get("low"), index),
                    "close": close,
                    "adjustedClose": adjusted_close if adjusted_close is not None else close,
                    "volume": int(_list_get(quote.get("volume"), index) or 0),
                    "source": "YAHOO",
                }
            )
        return rows

    @staticmethod
    def _write_prices_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["ticker", "date", "open", "high", "low", "close", "adjustedClose", "volume", "source"]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})


def _list_get(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]
