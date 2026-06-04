import importlib
import json
import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


class ProviderFinancialRegressionTests(unittest.TestCase):
    def test_sec_filing_parser_defaults_to_all_forms_in_two_year_window(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        recent_8k = date.today() - timedelta(days=1)
        recent_10q = date.today() - timedelta(days=30)
        recent_form4 = date.today() - timedelta(days=45)
        old_10k = date.today() - timedelta(days=800)
        submissions = {
            "fiscalYearEnd": "1231",
            "filings": {
                "recent": {
                    "accessionNumber": [
                        "0000000000-26-000001",
                        "0000000000-26-000002",
                        "0000000000-26-000003",
                        "0000000000-23-000004",
                    ],
                    "form": ["8-K", "10-Q", "4", "10-K"],
                    "filingDate": [
                        recent_8k.isoformat(),
                        recent_10q.isoformat(),
                        recent_form4.isoformat(),
                        old_10k.isoformat(),
                    ],
                    "reportDate": [
                        recent_8k.isoformat(),
                        recent_10q.isoformat(),
                        recent_form4.isoformat(),
                        old_10k.isoformat(),
                    ],
                    "primaryDocument": ["eightk.htm", "quarter.htm", "ownership.xml", "annual.htm"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"
            company_dir = research_root / "ACME"
            provider = providers.SecProvider(
                Path(tmpdir),
                http=_StaticJsonHttp(submissions),
                research_root=research_root,
            )

            filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=2,
            )
            quarterly_filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=2,
                forms=("10-Q",),
            )

        self.assertEqual([filing.formType for filing in filings], ["8-K", "10-Q", "4"])
        self.assertEqual(filings[0].localLabel, f"{recent_8k.isoformat()}-8K")
        self.assertEqual(filings[2].localLabel, f"{recent_form4.isoformat()}-4")
        self.assertEqual(filings[0].fiscalPeriod, "")
        self.assertEqual([filing.formType for filing in quarterly_filings], ["10-Q"])

    def test_sec_filing_parser_reads_archives_when_recent_does_not_cover_two_year_window(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        recent_date = date.today() - timedelta(days=1)
        archived_date = date.today() - timedelta(days=500)
        submissions = {
            "fiscalYearEnd": "1231",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000000-26-000001"],
                    "form": ["8-K"],
                    "filingDate": [recent_date.isoformat()],
                    "reportDate": [recent_date.isoformat()],
                    "primaryDocument": ["eightk.htm"],
                },
                "files": [
                    {
                        "name": "CIK0000000000-submissions-001.json",
                        "filingFrom": archived_date.isoformat(),
                        "filingTo": archived_date.isoformat(),
                        "filingCount": 1,
                    }
                ],
            },
        }
        archive = {
            "accessionNumber": ["0000000000-25-000002"],
            "form": ["10-Q"],
            "filingDate": [archived_date.isoformat()],
            "reportDate": [archived_date.isoformat()],
            "primaryDocument": ["quarter.htm"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"
            company_dir = research_root / "ACME"
            http = _SecSubmissionsHttp(submissions, {"CIK0000000000-submissions-001.json": archive})
            provider = providers.SecProvider(
                Path(tmpdir),
                http=http,
                research_root=research_root,
            )

            filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=2,
            )

        self.assertEqual([filing.formType for filing in filings], ["8-K", "10-Q"])
        self.assertIn("CIK0000000000-submissions-001.json", http.urls[-1])

    def test_10q_labels_use_actual_fiscal_quarters(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        submissions = {
            "fiscalYearEnd": "0630",
            "filings": {
                "recent": {
                    "accessionNumber": [
                        "0000000000-24-000001",
                        "0000000000-25-000002",
                        "0000000000-25-000003",
                        "0000000000-25-000004",
                    ],
                    "form": ["10-Q", "10-Q", "10-Q", "10-K"],
                    "filingDate": ["2024-10-20", "2025-01-20", "2025-04-20", "2025-07-30"],
                    "reportDate": ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"],
                    "primaryDocument": ["q1.htm", "q2.htm", "q3.htm", "k.htm"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "custom-research"
            company_dir = research_root / "ACME"
            provider = providers.SecProvider(
                Path(tmpdir),
                http=_StaticJsonHttp(submissions),
                research_root=research_root,
            )

            filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=20,
            )

        self.assertEqual(
            [filing.localLabel for filing in filings],
            ["2025-Q1-10Q", "2025-Q2-10Q", "2025-Q3-10Q", "2025-10K"],
        )
        self.assertEqual([filing.fiscalPeriod for filing in filings], ["Q1", "Q2", "Q3", "FY"])
        self.assertEqual(len({filing.localLabel for filing in filings}), len(filings))

    def test_financial_normalizer_merges_partial_fallback_concepts(self):
        financials = importlib.import_module("investor_toolkit.financials")

        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"USD": [_annual_fact(2024, 120.0, "new-revenue")]}
                    },
                    "Revenues": {
                        "units": {
                            "USD": [
                                _annual_fact(2022, 90.0, "old-revenue"),
                                _annual_fact(2023, 100.0, "old-revenue"),
                                _annual_fact(2024, 110.0, "old-revenue"),
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {"USD": [_annual_fact(2024, 20.0, "new-net-income")]}
                    },
                    "ProfitLoss": {
                        "units": {"USD": [_annual_fact(2023, 15.0, "old-net-income")]}
                    },
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")
        by_year = {row["fiscalYear"]: row for row in rows}

        self.assertEqual(by_year[2022]["revenue"], 90.0)
        self.assertEqual(by_year[2023]["revenue"], 100.0)
        self.assertEqual(by_year[2024]["revenue"], 120.0)
        self.assertEqual(by_year[2023]["netIncome"], 15.0)
        self.assertEqual(by_year[2024]["netIncome"], 20.0)
        self.assertEqual(
            by_year[2024]["sources"]["revenue"]["concept"],
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        )
        self.assertEqual(by_year[2023]["sources"]["revenue"]["concept"], "Revenues")

    def test_financial_normalizer_does_not_double_count_total_debt_concept(self):
        financials = importlib.import_module("investor_toolkit.financials")

        facts = {
            "facts": {
                "us-gaap": {
                    "LongTermDebtCurrent": {
                        "units": {"USD": [_instant_fact(2024, "2024-12-31", 10.0, "current")]}
                    },
                    "LongTermDebt": {
                        "units": {"USD": [_instant_fact(2024, "2024-12-31", 100.0, "total")]}
                    },
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")

        self.assertEqual(rows[0]["totalDebt"], 100.0)
        self.assertEqual(rows[0]["shortTermDebt"], 10.0)
        self.assertNotIn("longTermDebt", rows[0])
        self.assertEqual(rows[0]["sources"]["totalDebt"]["concept"], "LongTermDebt")

    def test_financial_normalizer_rejects_malformed_duration_facts_without_start_dates(self):
        financials = importlib.import_module("investor_toolkit.financials")

        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "fy": 2024,
                                    "form": "10-K",
                                    "val": 25.0,
                                    "end": "2024-03-31",
                                    "filed": "2025-02-01",
                                    "accn": "quarter-missing-start",
                                },
                                _annual_fact(2024, 100.0, "valid-annual"),
                            ]
                        }
                    },
                    "CashAndCashEquivalentsAtCarryingValue": {
                        "units": {
                            "USD": [
                                _instant_fact(2024, "2024-12-31", 50.0, "valid-cash"),
                                {
                                    "fy": 2024,
                                    "form": "10-K",
                                    "val": 999.0,
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "filed": "2025-03-01",
                                    "accn": "duration-cash",
                                },
                            ]
                        }
                    },
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")

        self.assertEqual(rows[0]["revenue"], 100.0)
        self.assertEqual(rows[0]["cash"], 50.0)
        self.assertEqual(rows[0]["sources"]["revenue"]["accessionNumber"], "valid-annual")
        self.assertEqual(rows[0]["sources"]["cash"]["accessionNumber"], "valid-cash")

    def test_financial_normalizer_does_not_treat_non_usd_units_as_usd(self):
        financials = importlib.import_module("investor_toolkit.financials")

        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"EUR": [_annual_fact(2024, 100.0, "eur-revenue")]}
                    },
                    "WeightedAverageNumberOfDilutedSharesOutstanding": {
                        "units": {"USD": [_annual_fact(2024, 10.0, "usd-not-shares")]}
                    },
                    "NetIncomeLoss": {
                        "units": {"USD": [_annual_fact(2024, 20.0, "usd-net-income")]}
                    },
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")

        self.assertEqual(rows[0]["netIncome"], 20.0)
        self.assertNotIn("revenue", rows[0])
        self.assertNotIn("dilutedShares", rows[0])

    def test_market_cache_ignores_wrong_ticker_for_requested_range(self):
        providers = importlib.import_module("investor_toolkit.providers")

        with tempfile.TemporaryDirectory() as tmpdir:
            company_dir = Path(tmpdir) / "ACME"
            _write_cache(
                company_dir,
                "yahoo",
                request={"ticker": "MSFT", "from": "2024-01-01", "to": "2024-01-10"},
                response=[_price_row("MSFT", "2024-01-05", 10.0)],
            )
            http = _YahooChartHttp([date(2024, 1, 5)])
            provider = providers.StooqMarketDataProvider(http=http)

            with patch.dict(os.environ, {"STOOQ_API_KEY": ""}):
                rows = provider.get_historical_prices(
                    "AAPL",
                    company_dir,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 10),
                )

        self.assertEqual(http.json_calls, 1)
        self.assertEqual(rows[0]["ticker"], "AAPL")

    def test_market_cache_ignores_date_range_that_does_not_cover_request(self):
        providers = importlib.import_module("investor_toolkit.providers")

        with tempfile.TemporaryDirectory() as tmpdir:
            company_dir = Path(tmpdir) / "ACME"
            _write_cache(
                company_dir,
                "yahoo",
                request={"ticker": "AAPL", "from": "2024-01-01", "to": "2024-01-05"},
                response=[_price_row("AAPL", "2024-01-05", 10.0)],
            )
            http = _YahooChartHttp([date(2024, 1, 10)])
            provider = providers.StooqMarketDataProvider(http=http)

            with patch.dict(os.environ, {"STOOQ_API_KEY": ""}):
                rows = provider.get_historical_prices(
                    "AAPL",
                    company_dir,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 10),
                )

        self.assertEqual(http.json_calls, 1)
        self.assertEqual(rows[-1]["date"], "2024-01-10")

    def test_market_provider_failure_does_not_cache_empty_price_results(self):
        providers = importlib.import_module("investor_toolkit.providers")

        with tempfile.TemporaryDirectory() as tmpdir:
            company_dir = Path(tmpdir) / "ACME"
            provider = providers.StooqMarketDataProvider(http=_FailingHttp(providers.ProviderError))

            with patch.dict(os.environ, {"STOOQ_API_KEY": ""}):
                with self.assertRaises(providers.ProviderError):
                    provider.get_historical_prices(
                        "AAPL",
                        company_dir,
                        start=date(2024, 1, 1),
                        end=date(2024, 1, 10),
                    )

            self.assertFalse(
                (company_dir / "data" / "provider_responses" / "yahoo" / "historical_prices.json").exists()
            )
            self.assertFalse((company_dir / "data" / "prices.json").exists())

    def test_market_provider_parsers_skip_non_positive_closes(self):
        providers = importlib.import_module("investor_toolkit.providers")

        stooq_rows = providers.StooqMarketDataProvider._parse_stooq_csv(
            "ACME",
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-01,1,1,1,0,100\n"
            "2024-01-02,1,1,1,-5,100\n"
            "2024-01-03,1,1,1,NaN,100\n"
            "2024-01-04,1,1,1,Infinity,100\n"
            "2024-01-05,1,1,1,10,100\n",
        )
        yahoo_rows = providers.StooqMarketDataProvider._parse_yahoo_chart(
            "ACME",
            _yahoo_response_with_closes(
                [
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                ],
                [0.0, -5.0, float("nan"), float("inf"), 10.0],
            ),
        )

        self.assertEqual([row["date"] for row in stooq_rows], ["2024-01-05"])
        self.assertEqual([row["date"] for row in yahoo_rows], ["2024-01-05"])

    def test_yahoo_chart_url_requests_end_date_inclusively(self):
        providers = importlib.import_module("investor_toolkit.providers")

        url = providers.StooqMarketDataProvider._yahoo_url(
            "AAPL",
            start=date(2024, 1, 1),
            end=date(2024, 1, 10),
        )

        expected_period2 = int(datetime(2024, 1, 11, tzinfo=UTC).timestamp())
        self.assertIn(f"period2={expected_period2}", url)

    def test_workflow_uses_custom_research_root_for_sec_global_cache(self):
        workflow_module = importlib.import_module("investor_toolkit.workflow")
        logging_utils = importlib.import_module("investor_toolkit.logging_utils")

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_root = Path(tmpdir) / "custom-research"
            try:
                workflow = workflow_module.ResearchWorkflow(Path(tmpdir) / "workspace", research_root=custom_root)
                self.assertEqual(
                    workflow.sec.global_cache.resolve(),
                    (custom_root / "_cache" / "sec").resolve(),
                )
                self.assertTrue((custom_root / "_cache" / "sec").is_dir())
                self.assertFalse((Path(tmpdir) / "research" / "_cache" / "sec").exists())
            finally:
                logging_utils.close_logging()

    def test_sec_filing_parser_tolerates_short_provider_arrays(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        submissions = {
            "fiscalYearEnd": "1231",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000000-25-000001", "0000000000-25-000002"],
                    "form": ["10-K", "10-Q"],
                    "filingDate": ["2025-02-01", "2025-05-01"],
                    "reportDate": ["2024-12-31"],
                    "primaryDocument": ["annual.htm"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"
            company_dir = research_root / "ACME"
            provider = providers.SecProvider(
                Path(tmpdir),
                http=_StaticJsonHttp(submissions),
                research_root=research_root,
            )

            filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=20,
            )

        self.assertEqual(len(filings), 2)
        self.assertEqual(filings[1].reportDate, "")
        self.assertEqual(filings[1].primaryDocument, "")

    def test_sec_provider_rejects_malformed_cik_before_building_urls(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"
            company_dir = research_root / "ACME"
            provider = providers.SecProvider(
                Path(tmpdir),
                http=_StaticJsonHttp({}),
                research_root=research_root,
            )

            with self.assertRaisesRegex(providers.ProviderError, "invalid CIK"):
                provider.get_filings(
                    models.CompanyIdentity(ticker="ACME", name="Acme", cik="not-a-cik"),
                    company_dir,
                    years=20,
                )

    def test_sec_filing_parser_ignores_impossible_fiscal_year_end_dates(self):
        providers = importlib.import_module("investor_toolkit.providers")
        models = importlib.import_module("investor_toolkit.models")

        submissions = {
            "fiscalYearEnd": "0231",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000000-25-000001"],
                    "form": ["10-Q"],
                    "filingDate": ["2025-03-01"],
                    "reportDate": ["2025-02-28"],
                    "primaryDocument": ["q1.htm"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            research_root = Path(tmpdir) / "research"
            company_dir = research_root / "ACME"
            provider = providers.SecProvider(
                Path(tmpdir),
                http=_StaticJsonHttp(submissions),
                research_root=research_root,
            )

            filings = provider.get_filings(
                models.CompanyIdentity(ticker="ACME", name="Acme", cik="0000000000"),
                company_dir,
                years=20,
            )

        self.assertEqual(filings[0].fiscalPeriod, "Q1")
        self.assertEqual(filings[0].localLabel, "2025-Q1-10Q")

    def test_financial_normalizer_ignores_non_finite_fact_values(self):
        financials = importlib.import_module("investor_toolkit.financials")

        valid = _annual_fact(2024, 100.0, "valid-revenue")
        valid["filed"] = "2025-02-01"
        invalid = _annual_fact(2024, float("nan"), "nan-revenue")
        invalid["filed"] = "2025-03-01"
        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"USD": [valid, invalid]}
                    }
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")

        self.assertEqual(rows[0]["revenue"], 100.0)
        self.assertEqual(rows[0]["sources"]["revenue"]["accessionNumber"], "valid-revenue")

    def test_financial_normalizer_rejects_malformed_fact_dates(self):
        financials = importlib.import_module("investor_toolkit.financials")

        malformed_revenue = _annual_fact(2024, 999.0, "bad-revenue")
        malformed_revenue["start"] = "not-a-date"
        malformed_revenue["end"] = "also-bad"
        malformed_cash = _instant_fact(2024, "not-a-date", 999.0, "bad-cash")
        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"USD": [malformed_revenue, _annual_fact(2024, 100.0, "valid-revenue")]}
                    },
                    "CashAndCashEquivalentsAtCarryingValue": {
                        "units": {"USD": [malformed_cash, _instant_fact(2024, "2024-12-31", 50.0, "valid-cash")]}
                    },
                }
            }
        }

        rows = financials.FinancialNormalizer().normalize_company_facts(facts, ticker="ACME")

        self.assertEqual(rows[0]["revenue"], 100.0)
        self.assertEqual(rows[0]["cash"], 50.0)
        self.assertEqual(rows[0]["sources"]["revenue"]["accessionNumber"], "valid-revenue")
        self.assertEqual(rows[0]["sources"]["cash"]["accessionNumber"], "valid-cash")


def _annual_fact(fiscal_year, value, accession):
    return {
        "fy": fiscal_year,
        "form": "10-K",
        "val": value,
        "start": f"{fiscal_year}-01-01",
        "end": f"{fiscal_year}-12-31",
        "filed": f"{fiscal_year + 1}-02-01",
        "accn": accession,
    }


def _instant_fact(fiscal_year, end, value, accession):
    return {
        "fy": fiscal_year,
        "form": "10-K",
        "val": value,
        "end": end,
        "filed": f"{fiscal_year + 1}-02-01",
        "accn": accession,
    }


def _price_row(ticker, row_date, close):
    return {
        "ticker": ticker,
        "date": row_date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adjustedClose": close,
        "volume": 100,
        "source": "YAHOO",
    }


def _write_cache(company_dir, provider, request, response):
    path = company_dir / "data" / "provider_responses" / provider / "historical_prices.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "provider": provider.upper(),
                "fetchedAt": "2024-01-11T00:00:00Z",
                "request": request,
                "response": response,
            }
        ),
        encoding="utf-8",
    )


def _yahoo_response(row_dates):
    timestamps = [
        int(datetime(row_date.year, row_date.month, row_date.day, tzinfo=UTC).timestamp())
        for row_date in row_dates
    ]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0 for _ in row_dates],
                                "high": [2.0 for _ in row_dates],
                                "low": [0.5 for _ in row_dates],
                                "close": [1.5 for _ in row_dates],
                                "volume": [100 for _ in row_dates],
                            }
                        ],
                        "adjclose": [{"adjclose": [1.4 for _ in row_dates]}],
                    },
                }
            ]
        }
    }


def _yahoo_response_with_closes(row_dates, closes):
    timestamps = [
        int(datetime(row_date.year, row_date.month, row_date.day, tzinfo=UTC).timestamp())
        for row_date in row_dates
    ]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0 for _ in row_dates],
                                "high": [2.0 for _ in row_dates],
                                "low": [0.5 for _ in row_dates],
                                "close": closes,
                                "volume": [100 for _ in row_dates],
                            }
                        ],
                        "adjclose": [{"adjclose": closes}],
                    },
                }
            ]
        }
    }


class _StaticJsonHttp:
    def __init__(self, data):
        self.data = data

    def get_json(self, url, headers=None):
        return self.data


class _SecSubmissionsHttp:
    def __init__(self, submissions, archives):
        self.submissions = submissions
        self.archives = archives
        self.urls = []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        for name, data in self.archives.items():
            if url.endswith(name):
                return data
        return self.submissions


class _YahooChartHttp:
    def __init__(self, row_dates):
        self.row_dates = row_dates
        self.json_calls = 0

    def get_json(self, url, headers=None):
        self.json_calls += 1
        return _yahoo_response(self.row_dates)


class _FailingHttp:
    def __init__(self, error_type):
        self.error_type = error_type

    def get_json(self, url, headers=None):
        raise self.error_type("market unavailable")


if __name__ == "__main__":
    unittest.main()
