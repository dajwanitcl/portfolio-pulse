"""Deterministic template summaries for structured filing types.

NSE's corporate-action feed encodes its facts in a fixed 'FIELD:value |'
format, so no language model is needed — parse the fields, compose an exact
sentence. Zero cost, zero hallucination risk, full precision.
"""

from __future__ import annotations

import re
from typing import Optional


def corporate_action_summary(description: str) -> Optional[dict]:
    """Parse 'PURPOSE:DIVIDEND - RE 0.57 PER SHARE |RECORD DATE:27-Jul-2026 …'
    into a clean summary. None if the pattern isn't recognisable."""
    desc = description or ""
    fields = {}
    for part in desc.split("|"):
        if ":" in part:
            k, _, v = part.partition(":")
            fields[k.strip().upper()] = v.strip()
    purpose = fields.get("PURPOSE", "")
    if not purpose:
        return None

    p = purpose.upper()
    bits: list[str] = []
    impact = "neutral"

    if "DIVIDEND" in p:
        m = re.search(r"(?:RS?\.?|RE\.?|₹)\s*([\d.]+)", p)
        amt = f"₹{m.group(1)} per share" if m else purpose.title()
        kind = "Interim dividend" if "INTERIM" in p else \
               "Final dividend" if "FINAL" in p else "Dividend"
        bits.append(f"{kind} of {amt}")
        impact = "positive"
    elif "BONUS" in p:
        m = re.search(r"(\d+)\s*:\s*(\d+)", p)
        bits.append(f"Bonus issue {m.group(1)}:{m.group(2)}" if m else "Bonus issue")
        impact = "positive"
    elif "SPLIT" in p or "SUB-DIVISION" in p or "SUBDIVISION" in p:
        bits.append(f"Stock split — {purpose.title()}")
    elif "BUYBACK" in p or "BUY BACK" in p:
        bits.append("Share buyback")
        impact = "positive"
    elif "RIGHTS" in p:
        bits.append(f"Rights issue — {purpose.title()}")
    elif "AGM" in p or "EGM" in p:
        bits.append(purpose.title())
    else:
        bits.append(purpose.title())

    for label, key in (("Ex-date", "EX-DATE"), ("Record date", "RECORD DATE")):
        if fields.get(key):
            bits.append(f"{label} {fields[key]}")
    # The feed's title line often carries 'Ex-Date: <d>' instead of a field.
    if len(bits) == 1:
        m = re.search(r"Ex-Date:\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", desc)
        if m:
            bits.append(f"Ex-date {m.group(1)}")

    return {"summary": " · ".join(bits) + ".", "impact_direction": impact}
