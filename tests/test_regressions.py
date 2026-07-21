"""Regression suite — every real-world failure this system has had, frozen as
a test so it can never silently return. Runs in CI on every push (tests.yml);
no network, no credentials.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PP_SQLITE_PATH", "/tmp/pp_test_regress.db")
os.environ["ANTHROPIC_API_KEY"] = ""

import numpy as np
import pandas as pd
import pytest

from portfolio_pulse.ingest.matching import (match_symbol, mention_is_attributive,
                                             text_mentions_symbol)
from portfolio_pulse.summarize.extractive import extract
from portfolio_pulse.summarize.guardrail import summarize, ungrounded_numbers
from portfolio_pulse.summarize.templates import corporate_action_summary
from portfolio_pulse.signals import dma

NAMES = {  # broker-abbreviated names, as Kite/Upstox really return them
    "ADANIPORTS": "ADANI PORT & SEZ", "BAJFINANCE": "BAJAJ FINANCE",
    "TATAPOWER": "TATA POWER CO", "SONACOMS": "SONA BLW PRECISION FRGS L",
    "NUVAMA": "NUVAMA WEALTH MANAGE", "KMEW": "KNOWLEDGE MARINE & EN W L",
    "ZAGGLE": "ZAGGLE PREPA OCEAN SER L", "SJS": "SJS ENTERPRISES LIMITED",
    "AURIONPRO": "AURIONPRO SOLN LTD", "BLS": "BLS INTL SERVS LTD",
    "SCHNEIDER": "SCHNEIDER ELECTRIC INFRA", "ITC": "ITC",
    "RELIANCE": "RELIANCE INDUSTRIES", "DIXON": "DIXON TECHNO (INDIA) LTD",
    "IDFCFIRSTB": "IDFC FIRST BANK",
}


class TestMatcher:
    # The Tata Capital incident: wrong-company attribution must stay dead.
    @pytest.mark.parametrize("legal,expected", [
        ("Adani Ports and Special Economic Zone Limited", "ADANIPORTS"),
        ("Sona BLW Precision Forgings Limited", "SONACOMS"),
        ("Knowledge Marine & Engineering Works Limited", "KMEW"),
        ("Zaggle Prepaid Ocean Services Limited", "ZAGGLE"),
        ("S.J.S. Enterprises Limited", "SJS"),              # dotted initialism
        ("Aurionpro Solutions Limited", "AURIONPRO"),        # vowel contraction
        ("BLS International Services Limited", "BLS"),       # double contraction
        ("The Tata Power Company Limited", "TATAPOWER"),
        ("Nuvama Wealth Management Limited", "NUVAMA"),
        ("ITC Limited", "ITC"),
        ("Dixon Technologies (India) Limited", "DIXON"),
    ])
    def test_real_names_match(self, legal, expected):
        assert match_symbol(legal, NAMES) == expected

    @pytest.mark.parametrize("trap", [
        "Tata Capital Limited",                  # the original incident
        "Tata Capital Limited - Ex-Date: 27-Jul-2026",
        "Tata Motors Limited", "Adani Power Limited", "Adani Enterprises Limited",
        "Bajaj Finserv Limited", "ITC Hotels Limited", "Reliance Power Limited",
        "Schneider Electric President Systems Limited",
        "BLS E-Services Limited", "SJVN Limited", "Dixcy Textiles Limited",
        "Sona Machinery Limited", "HDFC Life Insurance Company Limited",
    ])
    def test_lookalikes_reject(self, trap):
        assert match_symbol(trap, NAMES) is None

    def test_ticker_fallback_never_mislabels(self):
        tickers = {s: s for s in NAMES}
        assert match_symbol("Tata Capital Limited - Ex-Date: 27-Jul-2026",
                            tickers) is None


class TestAttributiveFilter:
    NUVAMA = "NUVAMA WEALTH MANAGE"

    @pytest.mark.parametrize("text", [
        "Nuvama maintains buy on Swiggy, sets target of Rs 500",
        "Nuvama initiates coverage on Tata Steel with reduce rating",
        "According to Nuvama, IT sector earnings may disappoint",
        "Nuvama Institutional Equities sees 20% upside in banks",
    ])
    def test_analyst_quotes_dropped(self, text):
        assert mention_is_attributive(text, self.NUVAMA)

    @pytest.mark.parametrize("text", [
        "Nuvama Wealth Management Q1 profit rises 38% YoY",
        "Motilal Oswal sets target of Rs 8000 on Nuvama",
        "SEBI issues warning letter to Nuvama Wealth Management",
        "Nuvama shares surge 8% after block deal",
    ])
    def test_genuine_news_kept(self, text):
        assert not mention_is_attributive(text, self.NUVAMA)


class TestExtractive:
    def test_prose_filing(self):
        text = ("The Company has informed the Exchange. Letter of Award issued by "
                "SECI Limited is for providing energy storage service from Pumped "
                "Storage Plant of 324MW capacity for 40 Years at Rs. 351.3 crore. "
                "Thanking you, yours faithfully, for The Company.")
        got = extract(text, "Order/Contract Win")
        assert got and "SECI" in got["summary"]
        assert got["impact_direction"] == "positive"

    def test_tabular_filing_no_punctuation(self):
        # The AAVAS incident: a table-style PDF with no sentence periods
        text = ("Sub Outcome of Board Meeting approval for issuance of upto 20,000 "
                "Senior Secured Rated Listed Redeemable Non-Convertible Debentures "
                "having face value of Rs 1,00,000 each of the aggregate nominal "
                "value of up to Rs 2,00,00,00,000 Rupees Two Hundred Crores only "
                "on private placement basis in one or more tranches ") * 2
        got = extract(text, "Announcement")
        assert got and "Debentures" in got["summary"]

    def test_rs_abbreviation_not_split(self):
        text = ("Board approved dividend payment. The order is worth Rs. 500 crore "
                "from the state utility and adds to the growing order book pipeline.")
        got = extract(text, "Order/Contract Win")
        assert got and "Rs. 500 crore" in got["summary"]

    def test_boilerplate_never_quoted(self):
        text = ("Dear Sirs, kindly take the same on record as per Regulation 30 of "
                "the SEBI Listing Obligations and Disclosure Requirements thanking "
                "you yours faithfully authorised signatory company secretary.") * 3
        got = extract(text, "Announcement")
        assert got is None  # honest silence beats quoted boilerplate


class TestTemplates:
    def test_dividend(self):
        ca = ("X Ltd - Ex-Date: 27-Jul-2026: SERIES:EQ |PURPOSE:DIVIDEND - RE 0.57 "
              "PER SHARE |FACE VALUE:10 |RECORD DATE:27-Jul-2026")
        got = corporate_action_summary(ca)
        assert got["summary"] == "Dividend of ₹0.57 per share · Record date 27-Jul-2026."
        assert got["impact_direction"] == "positive"

    def test_bonus(self):
        got = corporate_action_summary(
            "X - Ex-Date: 05-Aug-2026: SERIES:EQ |PURPOSE:BONUS 1:1 "
            "|RECORD DATE:05-Aug-2026")
        assert "Bonus issue 1:1" in got["summary"]


class TestGuardrail:
    SRC = ("Reliance Industries Limited board approved a dividend of 10 per share "
           "and capex of 75000 crore for the next financial year period.")

    def test_hallucinated_number_discarded(self):
        fake = lambda s, h, c: {"insufficient": False, "about_company": True,
                                "summary": "Dividend of 999 per share approved.",
                                "impact_direction": "positive", "confidence": "high"}
        assert summarize(self.SRC, "headline", company="X",
                         llm=fake).qc_status == "PARTIAL"

    def test_grounded_number_kept(self):
        fake = lambda s, h, c: {"insufficient": False, "about_company": True,
                                "summary": "Dividend of 10 per share approved.",
                                "impact_direction": "positive", "confidence": "high"}
        assert summarize(self.SRC, "headline", company="X",
                         llm=fake).qc_status == "CONFIRMED"

    def test_irrelevant_item_dropped(self):
        fake = lambda s, h, c: {"insufficient": False, "about_company": False,
                                "summary": "x", "impact_direction": "neutral",
                                "confidence": "high"}
        assert summarize(self.SRC, "headline", company="X", llm=fake).relevant is False

    def test_thin_source_never_calls_model(self):
        calls = []
        spy = lambda *a: calls.append(1)
        s = summarize("short", "headline", llm=spy)
        assert s.qc_status == "INSUFFICIENT" and not calls

    def test_ungrounded_helper(self):
        assert ungrounded_numbers("value 999", self.SRC) == ["999"]
        assert ungrounded_numbers("value 10", self.SRC) == []


class TestDMA:
    def test_death_cross_fires_once_each_stage(self):
        idx = pd.date_range("2024-01-01", periods=320, freq="D")
        prices = pd.Series(
            np.concatenate([np.linspace(100, 200, 220), np.linspace(200, 120, 100)]),
            index=idx)
        relation, events = None, []
        for i in range(dma.config.DMA_LONG + 10, len(prices) + 1):
            sig, relation, _ = dma.evaluate("T", prices.iloc[:i], relation)
            if sig:
                events.append(sig.alert_type)
        assert events == ["dma_forming", "dma_confirmed"]
        sig, _, _ = dma.evaluate("T", prices, relation)
        assert sig is None  # never repeats


class TestHoldings:
    def _store(self, tmp_path):
        from portfolio_pulse.store.db import SQLiteStore
        return SQLiteStore(str(tmp_path / "t.db"))

    class FakeBroker:
        def __init__(self, rows):
            self._rows = rows

        def holdings(self):
            return self._rows

    def test_multi_broker_weighted_average(self, tmp_path):
        from portfolio_pulse.broker import holdings
        store = self._store(tmp_path)
        holdings.sync(store, self.FakeBroker(
            [{"tradingsymbol": "RELIANCE", "quantity": 10, "average_price": 2000,
              "last_price": 2600}]), broker="zerodha")
        holdings.sync(store, self.FakeBroker(
            [{"tradingsymbol": "RELIANCE", "quantity": 30, "average_price": 2400,
              "last_price": 2610}]), broker="upstox")
        agg = {r["symbol"]: r for r in store.get_holdings()}
        assert agg["RELIANCE"]["qty"] == 40
        assert abs(agg["RELIANCE"]["avg_price"] - 2300) < 0.01

    def test_sold_stock_demotes_but_keeps_alerts(self, tmp_path):
        from portfolio_pulse.broker import holdings
        store = self._store(tmp_path)
        holdings.sync(store, self.FakeBroker(
            [{"tradingsymbol": "A", "quantity": 1, "average_price": 1, "last_price": 1},
             {"tradingsymbol": "B", "quantity": 1, "average_price": 1, "last_price": 1}]),
            broker="zerodha")
        holdings.sync(store, self.FakeBroker(
            [{"tradingsymbol": "A", "quantity": 1, "average_price": 1, "last_price": 1}]),
            broker="zerodha")
        kinds = {w.symbol: w.kind for w in store.list_watch()}
        assert kinds == {"A": "holding", "B": "watch"}  # B sold -> watch, tracked

    def test_empty_broker_reply_is_ignored(self, tmp_path):
        from portfolio_pulse.broker import holdings
        store = self._store(tmp_path)
        holdings.sync(store, self.FakeBroker(
            [{"tradingsymbol": "A", "quantity": 1, "average_price": 1, "last_price": 1}]),
            broker="zerodha")
        holdings.sync(store, self.FakeBroker([]), broker="zerodha")
        assert [r["symbol"] for r in store.get_holdings()] == ["A"]


class TestNewsMatching:
    def test_symbol_word_match(self):
        assert text_mentions_symbol("BEL wins defence order", "BEL",
                                    "BHARAT ELECTRONICS")

    def test_abbreviated_name_matches_fuller_text(self):
        assert text_mentions_symbol("Nuvama Wealth Management posts record profit",
                                    "NUVAMA", "NUVAMA WEALTH MANAGE")

    def test_unrelated_text_rejected(self):
        assert not text_mentions_symbol("Tata Motors launches new EV", "TATAPOWER",
                                        "TATA POWER CO")


class TestNoiseControls:
    def test_analyst_meet_filings_muted(self):
        from portfolio_pulse import config
        for subject in ("Analyst/Investor Meet Para A-XBRL",
                        "Analysts/Institutional Investor Meet/Con. Call Updates",
                        "Intimation of Conference Call", "Audio Call Intimation"):
            assert any(k in subject.lower() for k in config.NSE_ROUTINE_SUBJECTS), subject
        for subject in ("Bagging/Receiving of orders/contracts", "Financial Results",
                        "Outcome of Board Meeting", "Dividend"):
            assert not any(k in subject.lower()
                           for k in config.NSE_ROUTINE_SUBJECTS), subject

    def test_pdf_xbrl_twin_deduped(self, tmp_path):
        # The AAVAS incident: same event alerted twice via PDF + XBRL twins
        from datetime import datetime, timezone
        from portfolio_pulse.jobs._common import recently_alerted
        from portfolio_pulse.store.db import Alert, SQLiteStore
        store = SQLiteStore(str(tmp_path / "d.db"))
        store.record_alert(Alert(
            None, "RATNAVEER", "filing",
            "Ratnaveer Precision Engineering Limited: Board Meeting Intimation "
            "|Meeting Date: 24-Jul-2026", "", "", "", "Exchange Filing", "PARTIAL",
            datetime.now(timezone.utc).isoformat(), True))
        assert recently_alerted(
            store, "RATNAVEER",
            "Ratnaveer Precision Engineering Limited: Board Meeting Intimation")
        # a genuinely different filing must NOT be suppressed
        assert not recently_alerted(
            store, "RATNAVEER",
            "Ratnaveer Precision Engineering Limited: Resignation of CFO")
        # and a different symbol is never affected
        assert not recently_alerted(
            store, "AAVAS", "Aavas Financiers Limited: Board Meeting Intimation")
