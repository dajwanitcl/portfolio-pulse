"""Source-grounded summariser — the core of the 'no incorrect information' rule.

The LLM is only ever allowed to *compress* text that was actually fetched. Three
guardrails, enforced in code (not merely asked for in the prompt):

  1. Thin-source gate: if the source text is below a minimum length, we never
     call the model — we return INSUFFICIENT and the caller sends headline+link.
  2. Structured extractive prompt: Haiku must summarise only what's stated and
     may flag `insufficient=true`; the impact label is explicitly a mechanical
     interpretation, not advice.
  3. Numeric-grounding check: every number appearing in the generated summary
     must also appear in the source text. If the model invents a figure, we
     discard the summary and fall back to the verbatim headline (PARTIAL).

qc_status outcomes (all deliver something truthful; none fabricate):
  CONFIRMED    — grounded model summary passed all checks.
  PARTIAL      — a guard tripped; we fall back to the verbatim source headline.
  INSUFFICIENT — source too thin; headline+link only, no generated prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from portfolio_pulse import config

_NUM = re.compile(r"\d[\d,]*\.?\d*")

_SYSTEM_PROMPT = (
    "You summarise Indian-market corporate exchange filings and news items for an "
    "investor. You will be given ONLY the text of one item. Rules:\n"
    "1. Summarise strictly and only what is explicitly stated in the provided text, "
    "in at most two plain sentences.\n"
    "2. Never add any fact, number, figure, date, name, or context that is not "
    "present in the provided text. Do not use outside knowledge.\n"
    "3. If the text is too short or has no substantive content to summarise, set "
    "insufficient=true.\n"
    "4. Classify the likely near-term impact for shareholders based SOLELY on the "
    "provided text: positive, negative, neutral, or unclear. This is a mechanical "
    "reading of the text, not investment advice."
)

_TOOL = {
    "name": "record_summary",
    "description": "Record the grounded summary and mechanical impact reading.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "<=2 sentences, extractive"},
            "impact_direction": {
                "type": "string",
                "enum": ["positive", "negative", "neutral", "unclear"],
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "insufficient": {"type": "boolean"},
        },
        "required": ["summary", "impact_direction", "confidence", "insufficient"],
    },
}

_IMPACT_LABEL = {
    "positive": "Potential impact: positive (model reading of the filing text — not advice)",
    "negative": "Potential impact: negative (model reading of the filing text — not advice)",
    "neutral": "Potential impact: neutral (model reading of the filing text — not advice)",
    "unclear": "Potential impact: unclear from the text",
}


@dataclass
class Summary:
    text: str
    impact_direction: str      # positive | negative | neutral | unclear
    impact_note: str           # hedged, human-readable
    confidence: str            # high | medium | low
    qc_status: str             # CONFIRMED | PARTIAL | INSUFFICIENT


def _numbers(text: str) -> set[str]:
    """Numeric tokens, comma-stripped, for grounding comparison."""
    return {m.replace(",", "").rstrip(".") for m in _NUM.findall(text or "")}


def ungrounded_numbers(summary: str, source: str) -> list[str]:
    """Numbers in `summary` that do not appear in `source` (empty = grounded)."""
    src = _numbers(source)
    return [n for n in _numbers(summary) if n and n not in src]


def _insufficient(headline: str) -> Summary:
    return Summary(
        text=headline.strip(),
        impact_direction="unclear",
        impact_note="Potential impact: unclear from the text",
        confidence="low",
        qc_status="INSUFFICIENT",
    )


def _partial(headline: str) -> Summary:
    return Summary(
        text=headline.strip(),
        impact_direction="unclear",
        impact_note="Potential impact: unclear from the text",
        confidence="low",
        qc_status="PARTIAL",
    )


def _call_haiku(source_text: str, headline: str) -> dict:
    """Call Claude Haiku with the extractive tool. Returns the tool input dict.

    Isolated so verification can inject a fake without an API key or network.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    user = f"HEADLINE: {headline}\n\nITEM TEXT:\n{source_text}"
    resp = client.messages.create(
        model=config.SUMMARY_MODEL,
        max_tokens=config.SUMMARY_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_summary"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return {"insufficient": True, "summary": "", "impact_direction": "unclear",
            "confidence": "low"}


def summarize(
    source_text: str,
    headline: str,
    llm: Optional[Callable[[str, str], dict]] = None,
) -> Summary:
    """Produce a source-grounded Summary. `llm` overrides the model call for tests.

    Guardrail order: thin-source gate -> model call -> insufficient flag ->
    numeric grounding. Any failure degrades gracefully to headline-only output;
    the pipeline never emits a fabricated fact.
    """
    source_text = (source_text or "").strip()
    headline = (headline or "").strip()

    # Guard 1: thin source -> never even call the model.
    if len(source_text) < config.SUMMARY_MIN_SOURCE_CHARS:
        return _insufficient(headline or source_text)

    # No API key configured and no injected model -> deliver the verbatim headline
    # rather than attempting (and failing) a call. Still truthful, never fabricated.
    if llm is None and not config.ANTHROPIC_API_KEY:
        return _partial(headline or source_text[:120])

    call = llm or _call_haiku
    try:
        out = call(source_text, headline)
    except Exception:
        # On any model/transport error, degrade to the verbatim headline.
        return _partial(headline or source_text[:120])

    if out.get("insufficient"):
        return _insufficient(headline or source_text[:120])

    summary = (out.get("summary") or "").strip()
    if not summary:
        return _partial(headline or source_text[:120])

    # Guard 3: numeric grounding — discard summaries that invent figures.
    if ungrounded_numbers(summary, source_text):
        return _partial(headline or source_text[:120])

    direction = out.get("impact_direction", "unclear")
    if direction not in _IMPACT_LABEL:
        direction = "unclear"
    return Summary(
        text=summary,
        impact_direction=direction,
        impact_note=_IMPACT_LABEL[direction],
        confidence=out.get("confidence", "low"),
        qc_status="CONFIRMED",
    )
