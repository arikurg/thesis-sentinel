"""Pillar drafting (spec section 7): the second and last LLM prompt.

Same strict-JSON discipline as analysis.py — on any malformed output the
caller gets None and nothing is ever written to the watchlist.
"""

from __future__ import annotations

from . import llm, prompts


def draft_pillars(
    ticker: str,
    company: str,
    prior_pillars: list[dict] | None = None,
    edit_text: str | None = None,
) -> list[dict] | None:
    """Draft (or revise) 3-4 thesis pillars. None on malformed output."""
    messages = prompts.pillar_draft_messages(ticker, company, prior_pillars, edit_text)
    raw = llm.complete(messages)
    data = llm.parse_strict_json(raw, ("pillars",), context=f"pillar-draft {ticker}")
    if data is None:
        return None
    pillars = data["pillars"]
    if not _valid_pillars(pillars):
        llm._log_failure(f"pillar-draft {ticker}", "schema validation failed", raw)
        return None
    return pillars


def _valid_pillars(pillars) -> bool:
    if not isinstance(pillars, list) or not 1 <= len(pillars) <= 6:
        return False
    for p in pillars:
        if not isinstance(p, dict):
            return False
        if not all(isinstance(p.get(k), str) and p.get(k) for k in ("id", "claim", "breaks_if")):
            return False
    return True
