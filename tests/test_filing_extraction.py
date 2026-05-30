import importlib
import unittest


class FilingExtractionTests(unittest.TestCase):
    def test_extracts_10k_sections_from_representative_html(self):
        extractor = importlib.import_module("investor_toolkit.filings.extractor")

        html = """
        <html>
          <body>
            <h1>Item 1. Business</h1>
            <p>We sell cloud software and productivity tools to enterprise customers.</p>

            <h1>Item 1A. Risk Factors</h1>
            <p>Competition, cybersecurity incidents, and infrastructure constraints could harm results.</p>

            <h1>Item 1B. Unresolved Staff Comments</h1>
            <p>None.</p>

            <h1>Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations</h1>
            <p>Revenue increased due to cloud demand, while operating margin narrowed from AI infrastructure spend.</p>

            <h1>Item 8. Financial Statements and Supplementary Data</h1>
            <p>Consolidated Statements of Operations and Consolidated Balance Sheets are included.</p>
            <h2>Notes to Consolidated Financial Statements</h2>
            <p>Revenue is recognized when control of promised services transfers to customers.</p>

            <h1>Item 9A. Controls and Procedures</h1>
            <p>Disclosure controls and procedures were effective.</p>
          </body>
        </html>
        """

        sections = extractor.extract_sections(
            document=html,
            form_type="10-K",
            source_path="filings/raw/2025-10K.html",
        )

        self.assertEqual(sections["business"]["status"], "Extracted")
        self.assertIn("cloud software", sections["business"]["text"])

        risk_factors = sections["risk_factors"]
        self.assertEqual(risk_factors["status"], "Extracted")
        self.assertEqual(risk_factors["confidence"], "High")
        self.assertEqual(risk_factors["start_heading"], "Item 1A. Risk Factors")
        self.assertEqual(risk_factors["end_heading"], "Item 1B. Unresolved Staff Comments")
        self.assertIn("cybersecurity incidents", risk_factors["text"])
        self.assertNotIn("Unresolved Staff Comments", risk_factors["text"])

        mdna = sections["mdna"]
        self.assertEqual(mdna["status"], "Extracted")
        self.assertIn("operating margin narrowed", mdna["text"])
        self.assertNotIn("Consolidated Statements", mdna["text"])

        financials = sections["financial_statements"]
        self.assertEqual(financials["status"], "Extracted")
        self.assertIn("Consolidated Statements of Operations", financials["text"])

        notes = sections["notes_to_financial_statements"]
        self.assertEqual(notes["status"], "Extracted")
        self.assertIn("Revenue is recognized", notes["text"])

    def test_extracts_10q_sections_from_representative_text(self):
        extractor = importlib.import_module("investor_toolkit.filings.extractor")

        text = """
        PART I - FINANCIAL INFORMATION

        Item 1. Financial Statements
        Condensed Consolidated Balance Sheets and Statements of Cash Flows are presented.

        Notes to Condensed Consolidated Financial Statements
        The company recognizes revenue over time for subscription services.

        Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations
        Operating income improved because revenue growth exceeded expense growth.

        Item 3. Quantitative and Qualitative Disclosures About Market Risk
        Interest rate exposure was not material.

        PART II - OTHER INFORMATION

        Item 1. Legal Proceedings
        The company is subject to routine litigation.

        Item 1A. Risk Factors
        There have been no material changes to the risk factors previously disclosed.

        Item 6. Exhibits
        The exhibits are listed in the exhibit index.
        """

        sections = extractor.extract_sections(
            document=text,
            form_type="10-Q",
            source_path="filings/raw/2025-Q3-10Q.txt",
        )

        self.assertEqual(sections["financial_statements"]["status"], "Extracted")
        self.assertIn("Balance Sheets", sections["financial_statements"]["text"])

        self.assertEqual(sections["notes_to_financial_statements"]["status"], "Extracted")
        self.assertIn("subscription services", sections["notes_to_financial_statements"]["text"])

        self.assertEqual(sections["mdna"]["status"], "Extracted")
        self.assertIn("Operating income improved", sections["mdna"]["text"])
        self.assertNotIn("Market Risk", sections["mdna"]["text"])

        self.assertEqual(sections["risk_factors"]["status"], "Extracted")
        self.assertIn("no material changes", sections["risk_factors"]["text"])
        self.assertNotIn("Exhibits", sections["risk_factors"]["text"])

    def test_missing_section_returns_failed_status(self):
        extractor = importlib.import_module("investor_toolkit.filings.extractor")

        sections = extractor.extract_sections(
            document="Item 1. Business\nOnly business text is present.",
            form_type="10-K",
            source_path="filings/raw/incomplete-10K.txt",
        )

        self.assertEqual(sections["risk_factors"]["status"], "Failed")
        self.assertIn("reason", sections["risk_factors"])


if __name__ == "__main__":
    unittest.main()
