from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..models import ExtractionResult, FilingMetadata
from ..utils import slugify, write_json, write_text


class _HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "ix:header"}:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "ix:header"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


SECTION_SPECS: dict[str, dict[str, tuple[str, str, list[str], list[str]]]] = {
    "10-K": {
        "business": (
            "Business",
            "business.md",
            [r"item\s+1\s*[\.\-:]*\s*business"],
            [r"item\s+1a\s*[\.\-:]*\s*risk\s+factors"],
        ),
        "risk-factors": (
            "Risk Factors",
            "risk-factors.md",
            [r"item\s+1a\s*[\.\-:]*\s*risk\s+factors"],
            [
                r"item\s+1b\s*[\.\-:]*\s*unresolved\s+staff\s+comments",
                r"item\s+1b\s*[\.\-:]*",
                r"item\s+2\s*[\.\-:]*\s*properties",
            ],
        ),
        "properties": (
            "Properties",
            "properties.md",
            [r"item\s+2\s*[\.\-:]*\s*properties"],
            [r"item\s+3\s*[\.\-:]*\s*legal\s+proceedings"],
        ),
        "legal-proceedings": (
            "Legal Proceedings",
            "legal-proceedings.md",
            [r"item\s+3\s*[\.\-:]*\s*legal\s+proceedings"],
            [r"item\s+4\s*[\.\-:]*"],
        ),
        "mdna": (
            "MD&A",
            "mdna.md",
            [
                r"item\s+7\s*[\.\-:]*\s*management[’']?s\s+discussion\s+and\s+analysis",
                r"item\s+7\s*[\.\-:]*\s*management\s+discussion\s+and\s+analysis",
            ],
            [r"item\s+7a\s*[\.\-:]*", r"item\s+8\s*[\.\-:]*\s*financial\s+statements"],
        ),
        "financial-statements": (
            "Financial Statements",
            "financial-statements.md",
            [r"item\s+8\s*[\.\-:]*\s*financial\s+statements"],
            [r"item\s+9\s*[\.\-:]*", r"item\s+9a\s*[\.\-:]*"],
        ),
        "notes-to-financial-statements": (
            "Notes to Financial Statements",
            "notes.md",
            [
                r"notes\s+to\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements",
                r"item\s+8\s*[\.\-:]*\s*financial\s+statements",
            ],
            [r"item\s+9\s*[\.\-:]*", r"item\s+9a\s*[\.\-:]*"],
        ),
        "controls-and-procedures": (
            "Controls and Procedures",
            "controls-and-procedures.md",
            [r"item\s+9a\s*[\.\-:]*\s*controls\s+and\s+procedures"],
            [r"item\s+9b\s*[\.\-:]*", r"item\s+10\s*[\.\-:]*"],
        ),
    },
    "10-Q": {
        "financial-statements": (
            "Financial Statements",
            "financial-statements.md",
            [r"item\s+1\s*[\.\-:]*\s*financial\s+statements"],
            [r"item\s+2\s*[\.\-:]*\s*management[’']?s\s+discussion\s+and\s+analysis"],
        ),
        "notes-to-financial-statements": (
            "Notes to Financial Statements",
            "notes.md",
            [
                r"notes\s+to\s+(?:the\s+)?(?:condensed\s+)?(?:consolidated\s+)?financial\s+statements",
                r"item\s+1\s*[\.\-:]*\s*financial\s+statements",
            ],
            [r"item\s+2\s*[\.\-:]*\s*management[’']?s\s+discussion\s+and\s+analysis"],
        ),
        "mdna": (
            "MD&A",
            "mdna.md",
            [r"item\s+2\s*[\.\-:]*\s*management[’']?s\s+discussion\s+and\s+analysis"],
            [r"item\s+3\s*[\.\-:]*", r"item\s+4\s*[\.\-:]*\s*controls\s+and\s+procedures"],
        ),
        "risk-factors": (
            "Risk Factors",
            "risk-factors.md",
            [r"item\s+1a\s*[\.\-:]*\s*risk\s+factors"],
            [r"item\s+2\s*[\.\-:]*", r"item\s+5\s*[\.\-:]*", r"item\s+6\s*[\.\-:]*"],
        ),
        "legal-proceedings": (
            "Legal Proceedings",
            "legal-proceedings.md",
            [r"item\s+1\s*[\.\-:]*\s*legal\s+proceedings"],
            [r"item\s+1a\s*[\.\-:]*\s*risk\s+factors"],
        ),
        "controls-and-procedures": (
            "Controls and Procedures",
            "controls-and-procedures.md",
            [r"item\s+4\s*[\.\-:]*\s*controls\s+and\s+procedures"],
            [r"part\s+ii", r"item\s+1\s*[\.\-:]*\s*legal\s+proceedings"],
        ),
    },
}


class FilingExtractor:
    def extract_sections(self, content: str, form_type: str = "10-K") -> dict[str, str]:
        raw = extract_sections(content, form_type=form_type)
        return {
            key.replace("_", "-"): str(value.get("text", ""))
            for key, value in raw.items()
            if value.get("status") == "Extracted"
        }

    def extract(
        self,
        filing: FilingMetadata,
        raw_path: Path,
        output_dir: Path,
        root: Path | None = None,
    ) -> list[ExtractionResult]:
        text = to_text(raw_path.read_text(encoding="utf-8", errors="replace"))
        output_dir.mkdir(parents=True, exist_ok=True)
        specs = SECTION_SPECS.get(filing.formType, SECTION_SPECS["10-K"])
        results: list[ExtractionResult] = []
        for key, (title, filename, starts, ends) in specs.items():
            section_text, start_heading, end_heading, confidence = _extract_section(text, starts, ends)
            extracted_path = output_dir / filename
            source_path = _relative(raw_path, root)
            relative_extracted = _relative(extracted_path, root)
            if section_text:
                body = (
                    f"# {title}\n\n"
                    f"Ticker: {filing.ticker}\n\n"
                    f"Source: {filing.formType} filed {filing.filingDate}, accession {filing.accessionNumber}\n\n"
                    f"Source path: {source_path}\n\n"
                    f"Extraction confidence: {confidence}\n\n"
                    "---\n\n"
                    f"{section_text.strip()}\n"
                )
                write_text(extracted_path, body)
                results.append(
                    ExtractionResult(
                        section=title,
                        filename=filename,
                        status="Extracted",
                        confidence=confidence,
                        startHeading=start_heading,
                        endHeading=end_heading,
                        sourcePath=source_path,
                        extractedPath=relative_extracted,
                    )
                )
            else:
                results.append(
                    ExtractionResult(
                        section=title,
                        filename=filename,
                        status="Failed",
                        confidence="None",
                        reason="Could not detect section boundaries",
                        sourcePath=source_path,
                        extractedPath=relative_extracted,
                    )
                )
        write_json(output_dir / "extraction.json", [result.to_dict() for result in results])
        return results


def extract_sections(
    document: str,
    form_type: str = "10-K",
    source_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    text = to_text(document)
    specs = SECTION_SPECS.get(form_type, SECTION_SPECS["10-K"])
    results: dict[str, dict[str, Any]] = {}
    for key, (title, _filename, starts, ends) in specs.items():
        section_text, start_heading, end_heading, confidence = _extract_section(text, starts, ends)
        public_key = key.replace("-", "_")
        if section_text:
            results[public_key] = {
                "section": title,
                "status": "Extracted",
                "confidence": confidence,
                "start_heading": start_heading,
                "end_heading": end_heading,
                "text": section_text,
                "source_path": str(source_path or ""),
            }
        else:
            results[public_key] = {
                "section": title,
                "status": "Failed",
                "confidence": "None",
                "start_heading": start_heading,
                "end_heading": end_heading,
                "text": "",
                "source_path": str(source_path or ""),
                "reason": "Could not detect section boundaries",
            }
    return results


def to_text(content: str) -> str:
    if "<" in content and ">" in content:
        parser = _HTMLTextExtractor()
        parser.feed(content)
        text = parser.text()
    else:
        text = content
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r"(?i)(item\s+\d+[a-z]?\s*[\.\-:])", r"\n\1", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_section(
    text: str,
    start_patterns: list[str],
    end_patterns: list[str],
) -> tuple[str, str, str, str]:
    start_match = _choose_start(text, start_patterns, end_patterns)
    if not start_match:
        return "", "", "", "None"
    start_pos, start_heading = start_match
    end_match = _find_first_after(text, end_patterns, start_pos + 1)
    end_pos = end_match[0] if end_match else len(text)
    section = text[start_pos:end_pos].strip()
    word_count = len(section.split())
    if word_count < 8:
        return "", start_heading, end_match[1] if end_match else "", "None"
    confidence = "High" if end_match else "Medium"
    return section, start_heading, end_match[1] if end_match else "", confidence


def _choose_start(
    text: str,
    start_patterns: list[str],
    end_patterns: list[str],
) -> tuple[int, str] | None:
    matches: list[tuple[int, str]] = []
    for pattern in start_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            matches.append((match.start(), match.group(0).strip()))
    matches.sort(key=lambda item: item[0])
    if not matches:
        return None
    for pos, heading in matches:
        end = _find_first_after(text, end_patterns, pos + 1)
        excerpt = text[pos : end[0] if end else min(len(text), pos + 4000)]
        if len(excerpt.split()) >= 20:
            return pos, heading
    return matches[-1]


def _find_first_after(text: str, patterns: list[str], position: int) -> tuple[int, str] | None:
    found: list[tuple[int, str]] = []
    for pattern in patterns:
        match = re.search(pattern, text[position:], flags=re.IGNORECASE)
        if match:
            found.append((position + match.start(), match.group(0).strip()))
    return sorted(found, key=lambda item: item[0])[0] if found else None


def _normalize_for_search(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized


def _relative(path: Path, root: Path | None) -> str:
    if root is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def filing_output_dir(company_dir: Path, filing: FilingMetadata) -> Path:
    label = filing.localLabel or f"{filing.filingDate}-{slugify(filing.formType)}"
    return company_dir / "extracted" / label


def load_filing_metadata(company_dir: Path) -> list[FilingMetadata]:
    path = company_dir / "filings" / "metadata" / "filings.json"
    if not path.exists():
        return []
    return [FilingMetadata.from_dict(row) for row in json.loads(path.read_text(encoding="utf-8"))]
