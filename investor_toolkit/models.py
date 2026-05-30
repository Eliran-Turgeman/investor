from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CompanyIdentity:
    ticker: str
    name: str
    cik: str = ""
    exchange: str = ""
    sector: str = ""
    industry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanyIdentity":
        return cls(
            ticker=str(data.get("ticker", "")).upper(),
            name=str(data.get("name") or data.get("title") or data.get("ticker", "")).strip(),
            cik=str(data.get("cik", "") or data.get("cik_str", "")).zfill(10)
            if data.get("cik") or data.get("cik_str")
            else "",
            exchange=str(data.get("exchange", "")),
            sector=str(data.get("sector", "")),
            industry=str(data.get("industry", "")),
        )


@dataclass(slots=True)
class FilingMetadata:
    ticker: str
    cik: str
    accessionNumber: str
    formType: str
    filingDate: str
    reportDate: str
    fiscalYear: int | None
    fiscalPeriod: str
    url: str
    primaryDocument: str = ""
    localLabel: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FilingMetadata":
        return cls(
            ticker=str(data.get("ticker", "")).upper(),
            cik=str(data.get("cik", "")).zfill(10) if data.get("cik") else "",
            accessionNumber=str(data.get("accessionNumber", "")),
            formType=str(data.get("formType", "")),
            filingDate=str(data.get("filingDate", "")),
            reportDate=str(data.get("reportDate", "")),
            fiscalYear=data.get("fiscalYear"),
            fiscalPeriod=str(data.get("fiscalPeriod", "")),
            url=str(data.get("url", "")),
            primaryDocument=str(data.get("primaryDocument", "")),
            localLabel=str(data.get("localLabel", "")),
        )


@dataclass(slots=True)
class ExtractionResult:
    section: str
    filename: str
    status: str
    confidence: str
    startHeading: str = ""
    endHeading: str = ""
    reason: str = ""
    sourcePath: str = ""
    extractedPath: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
