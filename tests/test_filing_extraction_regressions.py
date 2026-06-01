import json

from investor_toolkit.filings.extractor import FilingExtractor, extract_sections
from investor_toolkit.indexing import build_index
from investor_toolkit.models import FilingMetadata


def test_extract_sections_ignores_page_numbered_toc_item_headings():
    document = """
    FORM 10-K
    INDEX
    Page
    PART I
    Item 1. Business
    3
    This table-of-contents entry has enough descriptive words to satisfy the old
    section-length heuristic even though it is only a pointer to the real section.
    Item 1A. Risk Factors
    12
    This table-of-contents entry also has enough descriptive words to look like
    section text unless the extractor recognizes the index block.
    Item 1B. Unresolved Staff Comments
    20
    Item 2. Properties
    21

    PART I
    ITEM 1. BUSINESS
    Actual business operations include software subscriptions, cloud services,
    consulting support, and product development across multiple geographies.

    ITEM 1A. RISK FACTORS
    Actual risk factors include competition, cybersecurity incidents, supplier
    disruption, customer concentration, and regulatory compliance requirements.

    ITEM 1B. UNRESOLVED STAFF COMMENTS
    None.

    ITEM 2. PROPERTIES
    The company leases offices and data center space.
    """

    sections = extract_sections(document=document, form_type="10-K")

    assert sections["business"]["status"] == "Extracted"
    assert "Actual business operations" in sections["business"]["text"]
    assert "table-of-contents entry" not in sections["business"]["text"]

    assert sections["risk_factors"]["status"] == "Extracted"
    assert "Actual risk factors" in sections["risk_factors"]["text"]
    assert "table-of-contents entry" not in sections["risk_factors"]["text"]


def test_reextract_removes_stale_markdown_when_section_fails(tmp_path):
    filing = _filing_metadata()
    raw_path = tmp_path / "2025-10K.html"
    output_dir = tmp_path / "extracted" / "2025-10K"
    extractor = FilingExtractor()

    raw_path.write_text(
        """
        Item 1. Business
        Business text contains enough words to create a section on the first pass.

        Item 1A. Risk Factors
        Risk factors include competition, cybersecurity incidents, and regulation.

        Item 1B. Unresolved Staff Comments
        None.
        """,
        encoding="utf-8",
    )
    extractor.extract(filing, raw_path, output_dir, root=tmp_path)
    stale_path = output_dir / "risk-factors.md"
    assert stale_path.exists()

    raw_path.write_text(
        """
        Item 1. Business
        Business text still exists, but this revision no longer has risk factors.

        Item 2. Properties
        The company leases office space.
        """,
        encoding="utf-8",
    )
    results = extractor.extract(filing, raw_path, output_dir, root=tmp_path)

    risk_result = next(result for result in results if result.filename == "risk-factors.md")
    assert risk_result.status == "Failed"
    assert not stale_path.exists()


def test_index_skips_markdown_not_marked_extracted(tmp_path):
    company_dir = tmp_path / "research" / "TST"
    filing_dir = company_dir / "extracted" / "2025-10K"
    filing_dir.mkdir(parents=True)
    (filing_dir / "business.md").write_text(
        """
        # Business

        Ticker: TST

        Source: 10-K filed 2025-01-31, accession 0000000000-25-000001

        ---

        Current business section text should be indexed.
        """,
        encoding="utf-8",
    )
    (filing_dir / "risk-factors.md").write_text(
        """
        # Risk Factors

        Ticker: TST

        Source: 10-K filed 2025-01-31, accession 0000000000-25-000001

        ---

        Stale risk factor text should not be indexed.
        """,
        encoding="utf-8",
    )
    (filing_dir / "extraction.json").write_text(
        json.dumps(
            [
                {"filename": "business.md", "status": "Extracted"},
                {"filename": "risk-factors.md", "status": "Failed"},
            ]
        ),
        encoding="utf-8",
    )

    chunks = build_index(company_dir, "TST")

    assert {chunk["section"] for chunk in chunks} == {"business"}
    assert all("Stale risk factor text" not in chunk["text"] for chunk in chunks)


def test_index_keeps_legacy_markdown_when_extraction_json_is_corrupt(tmp_path):
    company_dir = tmp_path / "research" / "TST"
    filing_dir = company_dir / "extracted" / "2025-10K"
    filing_dir.mkdir(parents=True)
    (filing_dir / "business.md").write_text(
        """
        # Business

        Ticker: TST

        Source: 10-K filed 2025-01-31, accession 0000000000-25-000001

        ---

        Legacy business section text should remain indexable.
        """,
        encoding="utf-8",
    )
    (filing_dir / "extraction.json").write_text("{not valid json", encoding="utf-8")

    chunks = build_index(company_dir, "TST")

    assert len(chunks) == 1
    assert chunks[0]["section"] == "business"
    assert "Legacy business section text" in chunks[0]["text"]


def _filing_metadata() -> FilingMetadata:
    return FilingMetadata(
        ticker="TST",
        cik="0000000000",
        accessionNumber="0000000000-25-000001",
        formType="10-K",
        filingDate="2025-01-31",
        reportDate="2024-12-31",
        fiscalYear=2024,
        fiscalPeriod="FY",
        url="",
        primaryDocument="2025-10K.html",
        localLabel="2025-10K",
    )
