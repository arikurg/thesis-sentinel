"""Thesis-impact judgment (spec section 6): one LLM call per new material filing.

Returns a validated verdict dict, or None on any malformed output — the
caller never alerts on None.
"""

from __future__ import annotations

import logging

from . import config, llm, prompts

logger = logging.getLogger("sentinel.analysis")

VERDICT_KEYS = (
    "happened",
    "pillars_touched",
    "severity",
    "confidence",
    "watch_next",
    "one_line_for_sms",
)


def analyze_filing(entry: dict, filing: dict, filing_text: str) -> dict | None:
    """Judge one filing against one ticker's pillars. None on malformed output."""
    messages = prompts.thesis_impact_messages(entry, filing, filing_text)
    raw = llm.complete(messages)
    verdict = llm.parse_strict_json(
        raw, VERDICT_KEYS, context=f"thesis-impact {entry['ticker']} {filing['accession_number']}"
    )
    if verdict is None:
        return None
    if not _valid_verdict(verdict, entry):
        llm._log_failure(
            f"thesis-impact {entry['ticker']} {filing['accession_number']}",
            "schema validation failed",
            raw,
        )
        return None
    return verdict


def _valid_verdict(verdict: dict, entry: dict) -> bool:
    if verdict["severity"] not in config.SEVERITY_ORDER:
        return False
    touched = verdict["pillars_touched"]
    if not isinstance(touched, list):
        return False
    known_ids = {p["id"] for p in entry.get("pillars", [])}
    for pillar in touched:
        if not isinstance(pillar, dict) or "id" not in pillar:
            return False
        if known_ids and pillar["id"] not in known_ids:
            logger.warning("verdict names unknown pillar id %r", pillar.get("id"))
    return True
