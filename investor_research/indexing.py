from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def build_index(company_dir: Path, ticker: str) -> list[dict[str, Any]]:
    extracted_root = company_dir / "extracted"
    index_path = company_dir / "index" / "filing_chunks.jsonl"
    chunks: list[dict[str, Any]] = []
    if extracted_root.exists():
        for path in sorted(extracted_root.glob("**/*.md")):
            if path.name == "extraction.md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            metadata = _metadata_from_text(text, path)
            section = _section_from_path(path)
            form_type, filing_date, accession = _source_from_metadata(metadata)
            for chunk_index, chunk_text in enumerate(_chunk_text(text), start=1):
                chunks.append(
                    {
                        "chunkId": f"{ticker.upper()}-{path.parent.name}-{section}-{chunk_index:03d}",
                        "ticker": ticker.upper(),
                        "formType": form_type,
                        "filingDate": filing_date,
                        "accessionNumber": accession,
                        "section": section,
                        "text": chunk_text,
                        "sourcePath": _relative(path, company_dir.parent.parent),
                    }
                )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, sort_keys=True) + "\n")
    return chunks


def load_chunks(company_dir: Path) -> list[dict[str, Any]]:
    index_path = company_dir / "index" / "filing_chunks.jsonl"
    if not index_path.exists():
        return []
    chunks: list[dict[str, Any]] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return chunks


def _chunk_text(text: str, target_words: int = 500, overlap: int = 80) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = min(len(words), start + target_words)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _metadata_from_text(text: str, path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in text.splitlines()[:20]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    if "source" not in metadata:
        metadata["source"] = path.parent.name
    return metadata


def _section_from_path(path: Path) -> str:
    return path.stem.lower()


def _source_from_metadata(metadata: dict[str, str]) -> tuple[str, str, str]:
    source = metadata.get("source", "")
    form_match = re.search(r"\b(10-K|10-Q|8-K)\b", source, flags=re.IGNORECASE)
    date_match = re.search(r"filed\s+(\d{4}-\d{2}-\d{2})", source)
    accession_match = re.search(r"accession\s+([0-9\-]+)", source)
    return (
        form_match.group(1).upper() if form_match else "",
        date_match.group(1) if date_match else "",
        accession_match.group(1) if accession_match else "",
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
