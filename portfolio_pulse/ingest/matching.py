"""Company-name normalization and symbol matching.

NSE announcement items are keyed by *company name* (the RSS <title>), not by
trading symbol. Matching a filing to a user's holding therefore needs a
symbol -> official-company-name map (built from the Kite instruments dump in
broker/holdings.py). This module holds the normalization + matching logic so
both the filings and news ingesters share one, well-tested implementation.
"""

from __future__ import annotations

import re

# Corporate suffixes / filler that carry no identifying signal.
_NOISE = re.compile(
    r"\b(limited|ltd|private|pvt|the|company|co|corporation|corp|"
    r"industries|india|indian)\b",
    re.IGNORECASE,
)
_NONWORD = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation and common corporate filler, collapse spaces.

    'EID Parry (India) Limited' -> 'eid parry'
    'Reliance Industries Ltd.'  -> 'reliance'
    """
    s = name.lower()
    s = _NONWORD.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def _tokens(name: str) -> list[str]:
    """Normalized tokens, minus single-letter fragments ('L', 'W' in Kite names)."""
    return [t for t in normalize_name(name).split() if len(t) > 1 or t.isdigit()]


def _tokens_compatible(a: list[str], b: list[str]) -> bool:
    """True if token lists refer to the same company, tolerating abbreviations.

    Broker instrument names are abbreviated ('ADANI PORT & SEZ', 'ZAGGLE PREPA
    OCEAN SER L') while exchange filings carry full legal names ('Adani Ports and
    Special Economic Zone Limited'). Rule: pair tokens positionally on the
    shorter list; a pair matches if equal or one is a prefix of the other
    (>=2 chars). One unpaired token is forgiven only after two exact-or-prefix
    pairs have already matched (handles acronym tails like 'SEZ'), which keeps
    'TATA POWER' from ever matching 'Tata Motors' (only 1 leading pair).
    """
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    paired = 0
    misses = 0
    for i, tok in enumerate(short):
        other = long[i] if i < len(long) else ""
        if tok == other or (len(tok) >= 2 and other.startswith(tok)) or (
            len(other) >= 2 and tok.startswith(other)
        ):
            paired += 1
        else:
            misses += 1
    if misses == 0:
        return True
    return misses == 1 and paired >= 2


def match_symbol(item_company: str, symbol_names: dict[str, str]) -> str | None:
    """Return the tracked symbol whose company name matches `item_company`, else None.

    `symbol_names` maps SYMBOL -> company name (possibly broker-abbreviated).
    Exact normalized equality wins outright; otherwise abbreviation-tolerant
    token pairing (see _tokens_compatible) with the longest candidate preferred.
    A bare substring match is deliberately NOT used ('rane' would wrongly hit
    several unrelated 'Rane ...' companies).
    """
    target = normalize_name(item_company)
    if not target:
        return None
    target_tokens = _tokens(item_company)

    best: str | None = None
    best_len = 0
    for symbol, cname in symbol_names.items():
        cand = normalize_name(cname)
        if not cand:
            continue
        if cand == target:
            return symbol  # exact normalized match wins outright
        if _tokens_compatible(_tokens(cname), target_tokens):
            if len(cand) > best_len:
                best, best_len = symbol, len(cand)
    return best


def text_mentions_symbol(text: str, symbol: str, company_name: str) -> bool:
    """Loose match for news items: does free text mention the symbol or company?

    Used only for news (where we already require a whitelisted source). The
    symbol must appear as a whole word, OR the company's first two identifying
    tokens must both appear as word-prefixes in the text (so the abbreviated
    'NUVAMA WEALTH MANAGE' still matches 'Nuvama Wealth Management shares...').
    """
    lowered = text.lower()
    if re.search(rf"\b{re.escape(symbol.lower())}\b", lowered):
        return True
    name_toks = _tokens(company_name)[:2]
    if not name_toks:
        return False
    if len(name_toks) == 1 and len(name_toks[0]) < 4:
        return False  # one short token is too weak an identifier for news
    text_toks = set(_tokens(text))
    for tok in name_toks:
        if not any(t == tok or t.startswith(tok) for t in text_toks):
            return False
    return True
