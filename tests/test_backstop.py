"""Deterministic item-code backstop: unconditionally material 8-K items
(bankruptcy, impairment, delisting, auditor change, restatement) alert
regardless of the LLM verdict, the severity floor, the cap, or quiet hours."""

import copy
from datetime import datetime

from sentinel.poll import backstop_items, run_poll


def _feed_with_8k(nvda_submissions, accession, items):
    updated = copy.deepcopy(nvda_submissions)
    recent = updated["filings"]["recent"]
    for col, value in [
        ("accessionNumber", accession),
        ("form", "8-K"),
        ("filingDate", "2026-07-13"),
        ("primaryDocument", "x.htm"),
        ("items", items),
    ]:
        recent[col].insert(0, value)
    for col in recent:
        if col not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items"):
            recent[col].insert(0, recent[col][0] if recent[col] else "")
    return updated


def _fresh_state():
    return {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
            "last_poll_utc": None, "day": None}


def _run(nvda_submissions, nvda_watchlist, items, analyze, state=None, now=None):
    state = state or _fresh_state()
    alerts = []
    kwargs = dict(
        watchlist=nvda_watchlist, state=state,
        fetch_submissions=lambda cik: copy.deepcopy(nvda_submissions),
        fetch_filing_text=lambda cik, filing: "body",
        analyze=analyze, alert=lambda e, f, v: alerts.append(v), save=False,
    )
    run_poll(**kwargs)  # seed
    updated = _feed_with_8k(nvda_submissions, "0001045810-26-000999", items)
    kwargs["fetch_submissions"] = lambda cik: copy.deepcopy(updated)
    summary = run_poll(now=now, **kwargs)
    return summary, alerts, state


def _verdict(severity):
    return {"happened": "x", "pillars_touched": [], "severity": severity,
            "confidence": "high", "watch_next": "", "one_line_for_sms": ""}


def test_backstop_items_parsing():
    assert backstop_items({"items": "1.03"}) == ["1.03"]
    assert backstop_items({"items": "2.02,4.02,9.01"}) == ["4.02"]
    assert backstop_items({"items": "2.02,9.01"}) == []
    assert backstop_items({"items": ""}) == []


def test_bankruptcy_alerts_despite_negligible_verdict(nvda_submissions, nvda_watchlist):
    summary, alerts, _ = _run(
        nvda_submissions, nvda_watchlist, "1.03",
        analyze=lambda e, f, t: _verdict("negligible"),
    )
    assert summary["alerted"] == 1
    assert alerts[0]["severity"] == "negligible"  # honest verdict, forced through


def test_bankruptcy_alerts_despite_malformed_llm_output(nvda_submissions, nvda_watchlist, data_dir):
    summary, alerts, _ = _run(
        nvda_submissions, nvda_watchlist, "1.03",
        analyze=lambda e, f, t: None,  # parse failure
    )
    assert summary["alerted"] == 1
    assert alerts[0]["severity"] == "material"
    assert "analysis unavailable" in alerts[0]["happened"].lower()


def test_backstop_ignores_cap_and_quiet_hours(nvda_submissions, nvda_watchlist):
    state = _fresh_state()
    state["sent_today"] = {"NVDA": 99}  # far past the cap
    summary, alerts, _ = _run(
        nvda_submissions, nvda_watchlist, "4.02",
        analyze=lambda e, f, t: _verdict("negligible"),
        state=state,
        now=datetime(2026, 7, 13, 23, 30),  # inside default quiet hours
    )
    assert summary["alerted"] == 1


def test_routine_8k_still_gated_normally(nvda_submissions, nvda_watchlist, data_dir):
    # Non-backstop items: negligible verdict and malformed output both stay silent.
    summary, alerts, _ = _run(
        nvda_submissions, nvda_watchlist, "2.02,9.01",
        analyze=lambda e, f, t: _verdict("negligible"),
    )
    assert summary["alerted"] == 0

    summary, alerts, _ = _run(
        nvda_submissions, nvda_watchlist, "2.02",
        analyze=lambda e, f, t: None,
    )
    assert summary["alerted"] == 0
