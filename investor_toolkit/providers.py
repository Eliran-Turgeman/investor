from __future__ import annotations

import csv
import io
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import CompanyIdentity, FilingMetadata
from .utils import parse_iso_date, read_json, utc_now_iso, write_json, write_text


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class FxRate:
    base_currency: str
    target_currency: str
    rate: float
    provider: str
    fetched_at: str | None
    source: str


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


class ExchangeRateProvider:
    OPEN_ACCESS_URL = "https://open.er-api.com/v6/latest/USD"

    def __init__(
        self,
        root: Path,
        http: HttpClient | None = None,
        research_root: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.http = http or HttpClient()
        cache_root = Path(research_root) if research_root is not None else self.root / "research"
        self.global_cache = cache_root / "_cache" / "fx"
        self.global_cache.mkdir(parents=True, exist_ok=True)

    def get_usd_ils_rate(self, refresh: bool = False) -> FxRate:
        cache = self.global_cache / "usd_ils.json"
        cached = read_json(cache, None)
        if cached is not None and not refresh and self._is_same_day_cache(cached):
            response = cached.get("response", cached) if isinstance(cached, dict) else cached
            fetched_at = cached.get("fetchedAt") if isinstance(cached, dict) else None
            return self._rate_from_response(response, fetched_at)

        response = self.http.get_json(self.OPEN_ACCESS_URL, headers={"User-Agent": "InvestorToolkit/0.1"})
        rate = self._rate_from_response(response, None)
        fetched_at = utc_now_iso()
        write_json(
            cache,
            {
                "provider": "ExchangeRate-API",
                "fetchedAt": fetched_at,
                "request": {"url": self.OPEN_ACCESS_URL},
                "response": response,
            },
        )
        return FxRate(
            base_currency=rate.base_currency,
            target_currency=rate.target_currency,
            rate=rate.rate,
            provider=rate.provider,
            fetched_at=fetched_at,
            source=rate.source,
        )

    @staticmethod
    def _is_same_day_cache(cached: Any) -> bool:
        if not isinstance(cached, dict):
            return False
        fetched_at = str(cached.get("fetchedAt", ""))
        return fetched_at[:10] == date.today().isoformat()

    @staticmethod
    def _rate_from_response(response: Any, fetched_at: str | None) -> FxRate:
        if not isinstance(response, dict):
            raise ProviderError("Invalid ExchangeRate-API response")
        if str(response.get("result", "success")).lower() not in {"success", ""}:
            raise ProviderError("ExchangeRate-API did not return a successful response")
        rates = response.get("rates")
        if not isinstance(rates, dict) or "ILS" not in rates:
            raise ProviderError("ExchangeRate-API response did not include USD/ILS")
        try:
            rate = float(rates["ILS"])
        except (TypeError, ValueError) as exc:
            raise ProviderError("ExchangeRate-API USD/ILS rate was not numeric") from exc
        if not math.isfinite(rate) or rate <= 0:
            raise ProviderError("ExchangeRate-API USD/ILS rate was not positive")
        provider = "ExchangeRate-API"
        date_label = response.get("time_last_update_utc") or fetched_at
        source = f"{provider} latest USD/ILS"
        if date_label:
            source = f"{source} ({date_label})"
        return FxRate(
            base_currency="USD",
            target_currency="ILS",
            rate=rate,
            provider=provider,
            fetched_at=fetched_at,
            source=source,
        )


class SecProvider:
    def __init__(
        self,
        root: Path,
        http: HttpClient | None = None,
        research_root: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.http = http or HttpClient()
        cache_root = Path(research_root) if research_root is not None else self.root / "research"
        self.global_cache = cache_root / "_cache" / "sec"
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
        cik = self._normalized_cik(company)
        cache = company_dir / "data" / "provider_responses" / "sec" / "submissions.json"
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
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
        fiscal_year_end = str(submissions.get("fiscalYearEnd", "1231") or "1231")
        cutoff = date.today() - timedelta(days=365 * years)
        rows: list[FilingMetadata] = []
        forms_set = set(forms)
        length = len(recent.get("accessionNumber", []))
        used_labels: set[str] = set()
        for index in range(length):
            form_type = str(_list_get(recent.get("form"), index) or "")
            if form_type not in forms_set:
                continue
            filing_date = str(_list_get(recent.get("filingDate"), index) or "")
            parsed_date = parse_iso_date(filing_date)
            if parsed_date and parsed_date < cutoff:
                continue
            accession = str(_list_get(recent.get("accessionNumber"), index) or "")
            report_date = str(_list_get(recent.get("reportDate"), index) or "")
            primary_document = str(_list_get(recent.get("primaryDocument"), index) or "")
            explicit_fiscal_year = _list_get(recent.get("fiscalYear"), index)
            explicit_fiscal_period = str(_list_get(recent.get("fiscalPeriod"), index) or "")
            if not explicit_fiscal_period:
                explicit_fiscal_period = str(_list_get(recent.get("period"), index) or "")
            fallback_year = int(filing_date[:4]) if filing_date[:4].isdigit() else None
            fiscal_year, fiscal_period = self._fiscal_year_and_period(
                form_type=form_type,
                report_date=report_date,
                fiscal_year_end=fiscal_year_end,
                explicit_fiscal_year=explicit_fiscal_year,
                explicit_fiscal_period=explicit_fiscal_period,
                fallback_year=fallback_year,
            )
            accession_path = accession.replace("-", "")
            cik_no_zero = str(int(self._normalized_cik(company)))
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_no_zero}/"
                f"{accession_path}/{primary_document}"
            )
            label_year = str(fiscal_year or filing_date[:4] or "unknown")
            label_suffix = "10K" if form_type == "10-K" else f"{fiscal_period or 'Q'}-10Q"
            local_label = self._unique_local_label(
                f"{label_year}-{label_suffix}",
                used_labels,
                accession,
            )
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
                    localLabel=local_label,
                )
            )
        write_json(
            company_dir / "filings" / "metadata" / "filings.json",
            [row.to_dict() for row in rows],
        )
        return rows

    @staticmethod
    def _fiscal_year_and_period(
        form_type: str,
        report_date: str,
        fiscal_year_end: str,
        explicit_fiscal_year: Any = None,
        explicit_fiscal_period: str = "",
        fallback_year: int | None = None,
    ) -> tuple[int | None, str]:
        parsed_report_date = parse_iso_date(report_date)
        fiscal_year = int(explicit_fiscal_year) if str(explicit_fiscal_year).isdigit() else None
        if fiscal_year is None and parsed_report_date is not None:
            fiscal_year = SecProvider._fiscal_year_from_report_date(parsed_report_date, fiscal_year_end)
        if fiscal_year is None:
            fiscal_year = fallback_year

        if form_type == "10-K":
            return fiscal_year, "FY"

        fiscal_period = SecProvider._normalize_fiscal_period(explicit_fiscal_period)
        if not fiscal_period and parsed_report_date is not None:
            fiscal_period = SecProvider._quarter_from_report_date(parsed_report_date, fiscal_year_end)
        return fiscal_year, fiscal_period

    @staticmethod
    def _fiscal_year_from_report_date(report_date: date, fiscal_year_end: str) -> int:
        fye_month, fye_day = SecProvider._parse_fiscal_year_end(fiscal_year_end)
        fiscal_year = report_date.year
        if (report_date.month, report_date.day) > (fye_month, fye_day):
            fiscal_year += 1
        return fiscal_year

    @staticmethod
    def _quarter_from_report_date(report_date: date, fiscal_year_end: str) -> str:
        fye_month, fye_day = SecProvider._parse_fiscal_year_end(fiscal_year_end)
        months_after_fiscal_year_end = (report_date.month - fye_month) % 12
        if months_after_fiscal_year_end == 0:
            months_after_fiscal_year_end = 1 if report_date.day > fye_day else 12
        quarter = ((months_after_fiscal_year_end - 1) // 3) + 1
        return f"Q{min(max(quarter, 1), 3)}"

    @staticmethod
    def _parse_fiscal_year_end(fiscal_year_end: str) -> tuple[int, int]:
        compact = "".join(char for char in str(fiscal_year_end) if char.isdigit())
        if len(compact) >= 4:
            month = int(compact[:2])
            day = int(compact[2:4])
            try:
                date(2000, month, day)
            except ValueError:
                pass
            else:
                return month, day
        return 12, 31

    @staticmethod
    def _normalize_fiscal_period(value: str) -> str:
        period = value.strip().upper().replace(" ", "")
        if period in {"Q1", "Q2", "Q3", "FY"}:
            return period
        if period in {"1", "I"}:
            return "Q1"
        if period in {"2", "II"}:
            return "Q2"
        if period in {"3", "III"}:
            return "Q3"
        return ""

    @staticmethod
    def _unique_local_label(label: str, used_labels: set[str], accession: str) -> str:
        if label not in used_labels:
            used_labels.add(label)
            return label
        accession_suffix = accession.replace("-", "")[-8:] or str(len(used_labels) + 1)
        unique_label = f"{label}-{accession_suffix}"
        counter = 2
        while unique_label in used_labels:
            unique_label = f"{label}-{accession_suffix}-{counter}"
            counter += 1
        used_labels.add(unique_label)
        return unique_label

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
        cik = self._normalized_cik(company)
        cache = company_dir / "data" / "provider_responses" / "sec" / "company_facts.json"
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        data = self._cached_json("company_facts", url, cache, refresh=refresh)
        write_json(company_dir / "data" / "company_facts.json", data)
        return data

    @staticmethod
    def _normalized_cik(company: CompanyIdentity) -> str:
        compact = str(company.cik or "").strip()
        if not compact:
            raise ProviderError(f"Cannot fetch SEC data for {company.ticker}: missing CIK")
        if not compact.isdigit():
            raise ProviderError(f"Cannot fetch SEC data for {company.ticker}: invalid CIK")
        return compact.zfill(10)

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
                response = self._cached_price_response(cached, ticker, start, end)
                if response is not None:
                    write_json(company_dir / "data" / "prices.json", response)
                    self._write_prices_csv(company_dir / "data" / "prices.csv", response)
                    return response

        rows: list[dict[str, Any]] = []
        provider = "YAHOO"
        request: dict[str, Any] = {"ticker": ticker, "from": start.isoformat(), "to": end.isoformat()}
        errors: list[str] = []
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
                if rows:
                    provider = "STOOQ"
                    request["url"] = url.replace(stooq_key, "<redacted>")
                else:
                    errors.append("STOOQ returned no rows")
            except ProviderError as exc:
                errors.append(f"STOOQ: {exc}")
                rows = []
        if not rows:
            url = self._yahoo_url(ticker, start, end)
            try:
                rows = self._parse_yahoo_chart(ticker, self.http.get_json(url, headers={"User-Agent": "Mozilla/5.0"}))
                if rows:
                    provider = "YAHOO"
                    request["url"] = url
                else:
                    errors.append("YAHOO returned no rows")
            except ProviderError as exc:
                errors.append(f"YAHOO: {exc}")
                rows = []
        if not rows:
            detail = "; ".join(errors) if errors else "no provider returned market data"
            raise ProviderError(
                f"No historical prices for {ticker.upper()} from {start.isoformat()} to {end.isoformat()}: {detail}"
            )
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

    @classmethod
    def _cached_price_response(
        cls,
        cached: Any,
        ticker: str,
        start: date,
        end: date,
    ) -> list[dict[str, Any]] | None:
        response = cached.get("response", cached) if isinstance(cached, dict) else cached
        if not isinstance(response, list) or not response:
            return None
        request = cached.get("request", {}) if isinstance(cached, dict) else {}
        if request:
            if not cls._request_covers_prices(request, ticker, start, end):
                return None
        elif not cls._rows_cover_prices(response, ticker, start, end):
            return None
        filtered = cls._filter_prices(response, ticker, start, end)
        return filtered or None

    @staticmethod
    def _request_covers_prices(request: dict[str, Any], ticker: str, start: date, end: date) -> bool:
        if str(request.get("ticker", "")).upper() != ticker.upper():
            return False
        cached_start = parse_iso_date(str(request.get("from", "")))
        cached_end = parse_iso_date(str(request.get("to", "")))
        if cached_start is None or cached_end is None:
            return False
        return cached_start <= start and cached_end >= end

    @classmethod
    def _rows_cover_prices(cls, rows: list[dict[str, Any]], ticker: str, start: date, end: date) -> bool:
        filtered = cls._filter_prices(rows, ticker, start, end)
        if not filtered:
            return False
        dates = [
            parse_iso_date(str(row.get("date", "")))
            for row in rows
            if str(row.get("ticker", "")).upper() in {"", ticker.upper()}
        ]
        dates = [row_date for row_date in dates if row_date is not None]
        return bool(dates) and min(dates) <= start and max(dates) >= end

    @staticmethod
    def _filter_prices(
        rows: list[dict[str, Any]],
        ticker: str,
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("ticker", "")).upper() not in {"", ticker.upper()}:
                continue
            row_date = parse_iso_date(str(row.get("date", "")))
            if row_date is None or row_date < start or row_date > end:
                continue
            filtered.append(row)
        return filtered

    @staticmethod
    def _parse_stooq_csv(ticker: str, text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if not row.get("Date") or row.get("Close") in (None, "", "N/D"):
                continue
            try:
                close = float(row["Close"])
                if not math.isfinite(close) or close <= 0:
                    continue
                rows.append(
                    {
                        "ticker": ticker.upper(),
                        "date": row["Date"],
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": close,
                        "adjustedClose": close,
                        "volume": int(float(row["Volume"])),
                        "source": "STOOQ",
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rows

    @staticmethod
    def _yahoo_url(ticker: str, start: date, end: date) -> str:
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        inclusive_end = end + timedelta(days=1)
        period2 = int(datetime(inclusive_end.year, inclusive_end.month, inclusive_end.day, tzinfo=UTC).timestamp())
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
            close = _positive_float(_list_get(quote.get("close"), index))
            if close is None:
                continue
            adjusted_close = _positive_float(_list_get(adjclose, index))
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


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None
