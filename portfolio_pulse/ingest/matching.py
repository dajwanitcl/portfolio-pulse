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
    """Normalized tokens for matching.

    Runs of consecutive single-letter fragments merge into one token, so the
    dotted initialism 'S.J.S. Enterprises' becomes ['sjs', 'enterprises']
    instead of losing its identity. Isolated single letters (truncation debris
    like the trailing 'L' in broker names) are dropped as before.
    """
    raw = normalize_name(name).split()
    out: list[str] = []
    run: list[str] = []
    for t in raw + [""]:  # sentinel flushes the last run
        if len(t) == 1 and not t.isdigit():
            run.append(t)
            continue
        if len(run) >= 2:
            out.append("".join(run))
        run = []
        if t:
            out.append(t)
    return out


def _tokens_compatible(map_tokens: list[str], item_tokens: list[str]) -> bool:
    """True if a tracked name (map_tokens) matches a filing's company (item_tokens).

    Broker instrument names are abbreviated ('ADANI PORT & SEZ', 'ZAGGLE PREPA
    OCEAN SER L') while exchange filings carry full legal names ('Adani Ports and
    Special Economic Zone Limited'). Every tracked-name token must pair with the
    filing token at the same position: equal, or the tracked token is a PREFIX of
    the filing word ('port'->'ports', 'manage'->'management'). The reverse
    direction is deliberately forbidden — allowing the filing word to prefix the
    tracked token once let the bare ticker 'tatapower' pair with the word 'tata'
    and mis-attribute a Tata Capital filing to TATAPOWER. One unpaired token is
    forgiven only after two paired ones (acronym tails like 'SEZ'); filing-name
    tail tokens beyond the tracked name ('special economic zone...') are free.
    """
    if not map_tokens or not item_tokens:
        return False
    # A single-token tracked name must equal the WHOLE normalized company name.
    # First-word-only matching let 'ITC' claim 'ITC Hotels Limited' and would
    # let 'RELIANCE' claim 'Reliance Power Limited' — different companies.
    if len(map_tokens) == 1:
        return len(item_tokens) == 1 and map_tokens[0] == item_tokens[0]
    paired = 0
    missed_at: list[int] = []
    for i, mtok in enumerate(map_tokens):
        itok = item_tokens[i] if i < len(item_tokens) else ""
        if (mtok == itok
                or (len(mtok) >= 2 and itok.startswith(mtok))
                or _is_contraction(mtok, itok)):
            paired += 1
        else:
            missed_at.append(i)
    if not missed_at:
        return True
    if len(missed_at) > 1 or paired < 2:
        return False
    # One forgivable miss — but ONLY for abbreviation debris in the tracked
    # name's LAST token: an acronym of the remaining legal words ('SEZ' for
    # 'Special Economic Zone') or a short truncation stub (<=4 chars: 'FRGS',
    # 'WL'). A real distinguishing word never qualifies — this is what stops
    # 'SCHNEIDER ELECTRIC INFRA' from claiming 'Schneider Electric President
    # Systems', a different listed company.
    i = missed_at[0]
    if i != len(map_tokens) - 1:
        return False
    mtok = map_tokens[i]
    initials = "".join(t[0] for t in item_tokens[i:])
    return mtok == initials or len(mtok) <= 4


def _is_contraction(short: str, full: str) -> bool:
    """True if `short` is a vowel-dropping contraction of `full` — the other way
    brokers abbreviate ('SOLN'→'solutions', 'INTL'→'international',
    'FRGS'→'forgings'): same first letter and every character of `short`
    appearing in `full` in order. Only the tracked-name token may be the
    contraction, mirroring the prefix rule's direction.
    """
    if len(short) < 3 or len(short) >= len(full) or short[0] != full[0]:
        return False
    pos = 0
    for ch in short:
        pos = full.find(ch, pos)
        if pos == -1:
            return False
        pos += 1
    return True


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
