"""Keyless summarisation: verbatim sentence extraction + rule-based impact.

No AI involved — so nothing can be made up. The summary is composed of the 1–2
most information-dense sentences taken VERBATIM from the filing/news text
(scored by the presence of amounts, percentages, dates and category keywords,
with boilerplate filtered out), followed by mechanically-extracted key figures.
Impact is classified by transparent keyword rules and labelled as such.

This is the default experience for installs without an ANTHROPIC_API_KEY; with
a key, the model path in guardrail.py takes over.
"""

from __future__ import annotations

import re
from typing import Optional

_AMOUNT = re.compile(
    r"(?:Rs\.?|₹|INR)\s?[\d,]+(?:\.\d+)?\s*(?:crores?|cr\b|lakhs?|million|mn\b|billion|bn\b)?",
    re.IGNORECASE)
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?%")
_DATE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\.?,?\s+\d{4}\b|\b\d{1,2}-[A-Za-z]{3}-\d{4}\b", re.IGNORECASE)

_SIGNAL_WORDS = (
    "order", "contract", "awarded", "bagged", "received", "dividend", "bonus",
    "buyback", "acquisition", "acquire", "merger", "demerger", "expansion",
    "capacity", "commissioned", "approved", "profit", "revenue", "ebitda",
    "loss", "penalty", "warning", "resign", "appointed", "stake", "investment",
    "agreement", "partnership", "settlement", "won", "supply",
)

_BOILERPLATE = re.compile(
    r"dear sir|yours faithfully|kind attention|listing obligations|"
    r"regulation \d+|sebi \(listing|registered office|corporate office|"
    r"website|www\.|@|cin[:\s]|scrip code|symbol[:\s]|isin|"
    r"this is to inform you that the trading window|take the same on record|"
    r"thanking you|authorised signatory|company secretary|encl",
    re.IGNORECASE)

_POSITIVE = ("order", "contract", "awarded", "bagged", "won", "dividend",
             "bonus", "buyback", "expansion", "commissioned", "profit rise",
             "profit up", "revenue growth", "acquisition completed", "supply")
_NEGATIVE = ("penalty", "warning letter", "show cause", "resignation",
             "resigned", "pledge", "default", "downgrade", "loss widened",
             "profit fell", "profit down", "fire", "strike", "litigation",
             "insolvency", "fraud")


_FORM_LABEL = re.compile(
    r"^[A-Z][^;:0-9₹]{10,90}(?:, in brief)?[;:]\s+(?=[A-Z₹0-9])")


def _clean(sentence: str) -> str:
    """Strip leading disclosure-form labels ('Significant terms and conditions
    of order(s)/contract(s) awarded, in brief;') so the quoted fact reads clean."""
    return _FORM_LABEL.sub("", sentence).strip()


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    # Don't split after common abbreviations ('Rs. 200 crore', 'Ref. No.', …).
    # The lookbehinds must include the dot — they assert at the position AFTER it.
    raw = re.split(
        r"(?<!Rs\.)(?<!No\.)(?<!Ref\.)(?<!Ltd\.)(?<!Pvt\.)(?<!Mr\.)(?<!Dr\.)"
        r"(?<!Ms\.)(?<!vs\.)(?<=[.!?])\s+(?=[A-Z0-9₹])", text)
    return [_clean(s) for s in raw if 40 <= len(_clean(s.strip())) <= 350]


def _chunks(text: str, size: int = 300) -> list[str]:
    """Fallback units for tabular PDFs with no sentence punctuation: the text
    hard-wrapped at word boundaries into quotable ~300-char windows."""
    text = re.sub(r"\s+", " ", text or "").strip()
    words, out, cur, ln = text.split(), [], [], 0
    for w in words:
        cur.append(w)
        ln += len(w) + 1
        if ln >= size:
            out.append(" ".join(cur))
            cur, ln = [], 0
    if cur:
        out.append(" ".join(cur))
    return [c for c in out if len(c) >= 60]


def _score(sentence: str) -> int:
    s = sentence.lower()
    score = 0
    score += 3 * len(_AMOUNT.findall(sentence))
    score += 2 * len(_PERCENT.findall(sentence))
    score += 1 * len(_DATE.findall(sentence))
    score += sum(2 for w in _SIGNAL_WORDS if w in s)
    if _BOILERPLATE.search(sentence):
        score -= 6
    return score


def key_figures(text: str) -> list[str]:
    """Unique amounts and percentages, in order of appearance (max 4)."""
    seen: list[str] = []
    for m in _AMOUNT.finditer(text or ""):
        v = re.sub(r"\s+", " ", m.group(0)).strip()
        if v not in seen and len(v) > 4:
            seen.append(v)
    for m in _PERCENT.finditer(text or ""):
        v = m.group(0)
        if v not in seen:
            seen.append(v)
    return seen[:4]


def rule_impact(text: str, category: str = "") -> str:
    """positive | negative | neutral | unclear — transparent keyword rules."""
    s = (text or "").lower()
    neg = sum(1 for w in _NEGATIVE if w in s)
    pos = sum(1 for w in _POSITIVE if w in s)
    cat = (category or "").lower()
    if "order" in cat or "contract" in cat:
        pos += 2
    if neg > pos:
        return "negative"
    if pos > neg and pos > 0:
        return "positive"
    if "result" in cat or "board" in cat or "corporate action" in cat:
        return "neutral"
    return "unclear"


def extract(text: str, category: str = "") -> Optional[dict]:
    """Best-effort verbatim summary. None when nothing scores above noise."""
    cands = [(s, _score(s)) for s in _sentences(text)]
    cands = [c for c in cands if c[1] >= 3]
    if not cands:
        # Tabular filings (no sentence punctuation) — quote the densest windows.
        cands = [(c, _score(c)) for c in _chunks(text)]
        cands = [c for c in cands if c[1] >= 4]
    if not cands:
        return None
    top = sorted(cands, key=lambda c: -c[1])[:2]
    # keep original document order for readability
    ordered = [s for s, _ in sorted(top, key=lambda c: text.find(c[0]))]
    summary = " ".join(ordered)
    figs = key_figures(text)
    if figs:
        summary += "\nKey figures: " + " · ".join(figs)
    return {"summary": summary, "impact_direction": rule_impact(text, category)}
