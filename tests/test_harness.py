"""Stage 1: deterministic harness — fetch -> parse -> dedupe -> persist.

All EDGAR access is fixture-backed; no network. The run-twice invariant and
the first-run guard are the load-bearing tests.
"""

import copy
import json

from sentinel import config, edgar, state as state_mod
from sentinel.poll import run_poll


def test_parse_recent_filings(nvda_submissions):
    filings = edgar.parse_recent_filings(nvda_submissions)
    assert len(filings) == 40
    first_8k = next(f for f in filings if f["form"] == "8-K")
    assert first_8k["accession_number"] == "0001045810-26-000060"
    assert first_8k["filing_date"] == "2026-07-02"
    assert first_8k["primary_document"] == "nvda-20260628.htm"
    assert first_8k["items"] == "5.02"


def test_archives_url_dash_handling():
    url = edgar.archives_url("0001045810", "0001045810-26-000060", "nvda-20260628.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000060/nvda-20260628.htm"
    )


def test_html_to_text_strips_tags(nvda_8k_html):
    text = edgar.html_to_text(nvda_8k_html, 40000)
    assert "<" not in text[:2000]
    assert "NVIDIA" in text
    assert len(text) <= 40000 + len("\n[truncated]")


def test_material_form_filter():
    assert edgar.is_material({"form": "8-K"})
    assert edgar.is_material({"form": "10-Q"})
    assert not edgar.is_material({"form": "4"})
    assert not edgar.is_material({"form": "144"})
    assert not edgar.is_material({"form": "DEF 14A"})


def test_user_agent_required(monkeypatch):
    monkeypatch.setattr(config, "SEC_USER_AGENT", "")
    try:
        edgar._get("https://data.sec.gov/whatever")
        assert False, "should have raised"
    except edgar.EdgarError as e:
        assert "SEC_USER_AGENT" in str(e)


def _poll_kwargs(nvda_submissions, watchlist, state):
    """Fixture-backed run_poll kwargs; analyze/alert record their calls."""
    calls = {"analyzed": [], "alerted": []}

    def fake_analyze(entry, filing, text):
        calls["analyzed"].append(filing["accession_number"])
        return {
            "happened": "test",
            "pillars_touched": [],
            "severity": "negligible",
            "confidence": "high",
            "watch_next": "",
            "one_line_for_sms": "",
        }

    def fake_alert(entry, filing, verdict):
        calls["alerted"].append(filing["accession_number"])

    kwargs = dict(
        watchlist=watchlist,
        state=state,
        fetch_submissions=lambda cik: copy.deepcopy(nvda_submissions),
        fetch_filing_text=lambda cik, filing: "filing body text",
        analyze=fake_analyze,
        alert=fake_alert,
        save=False,
    )
    return kwargs, calls


def test_first_run_guard_is_silent(nvda_submissions, nvda_watchlist):
    state = {
        "seen_accessions": {}, "sent_today": {}, "pending_add": None,
        "last_poll_utc": None, "day": None,
    }
    kwargs, calls = _poll_kwargs(nvda_submissions, nvda_watchlist, state)

    summary = run_poll(**kwargs)

    assert summary["first_runs"] == 1
    assert summary["analyzed"] == 0
    assert summary["alerted"] == 0
    assert calls["analyzed"] == []
    # Every current accession recorded as seen.
    assert len(state["seen_accessions"]["0001045810"]) == 40


def test_second_run_finds_nothing_new(nvda_submissions, nvda_watchlist):
    state = {
        "seen_accessions": {}, "sent_today": {}, "pending_add": None,
        "last_poll_utc": None, "day": None,
    }
    kwargs, calls = _poll_kwargs(nvda_submissions, nvda_watchlist, state)

    run_poll(**kwargs)  # first run: seeds silently
    summary2 = run_poll(**kwargs)  # second run against identical feed

    assert summary2["first_runs"] == 0
    assert summary2["new_filings"] == 0
    assert summary2["analyzed"] == 0
    assert summary2["alerted"] == 0


def test_new_filing_is_analyzed_once_ever(nvda_submissions, nvda_watchlist):
    state = {
        "seen_accessions": {}, "sent_today": {}, "pending_add": None,
        "last_poll_utc": None, "day": None,
    }
    kwargs, calls = _poll_kwargs(nvda_submissions, nvda_watchlist, state)
    run_poll(**kwargs)

    # Simulate a brand-new 8-K landing in the feed.
    updated = copy.deepcopy(nvda_submissions)
    recent = updated["filings"]["recent"]
    for col, value in [
        ("accessionNumber", "0001045810-26-000099"),
        ("form", "8-K"),
        ("filingDate", "2026-07-11"),
        ("primaryDocument", "nvda-new.htm"),
        ("items", "2.02"),
    ]:
        recent[col].insert(0, value)
    for col in recent:
        if col not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items"):
            recent[col].insert(0, recent[col][0] if recent[col] else "")

    kwargs["fetch_submissions"] = lambda cik: copy.deepcopy(updated)
    summary = run_poll(**kwargs)
    assert summary["new_filings"] == 1
    assert calls["analyzed"] == ["0001045810-26-000099"]

    # Third run, same feed: dedupe by accession holds.
    summary3 = run_poll(**kwargs)
    assert summary3["new_filings"] == 0
    assert calls["analyzed"] == ["0001045810-26-000099"]


def test_non_material_new_filing_recorded_but_not_analyzed(nvda_submissions, nvda_watchlist):
    state = {
        "seen_accessions": {}, "sent_today": {}, "pending_add": None,
        "last_poll_utc": None, "day": None,
    }
    kwargs, calls = _poll_kwargs(nvda_submissions, nvda_watchlist, state)
    run_poll(**kwargs)

    updated = copy.deepcopy(nvda_submissions)
    recent = updated["filings"]["recent"]
    for col, value in [
        ("accessionNumber", "0001045810-26-000100"),
        ("form", "4"),
        ("filingDate", "2026-07-11"),
        ("primaryDocument", "form4.xml"),
        ("items", ""),
    ]:
        recent[col].insert(0, value)
    for col in recent:
        if col not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items"):
            recent[col].insert(0, recent[col][0] if recent[col] else "")

    kwargs["fetch_submissions"] = lambda cik: copy.deepcopy(updated)
    summary = run_poll(**kwargs)
    assert summary["new_filings"] == 1
    assert summary["analyzed"] == 0
    assert "0001045810-26-000100" in state["seen_accessions"]["0001045810"]


def test_state_persistence_round_trip(data_dir):
    state = {
        "seen_accessions": {"0001045810": ["a", "b"]},
        "sent_today": {"NVDA": 1},
        "pending_add": None,
        "last_poll_utc": "2026-07-11T18:00:00Z",
        "day": "2026-07-11",
    }
    state_mod.save_state(state)
    loaded = state_mod.load_state()
    assert loaded == state
    # Atomic write leaves no tmp litter.
    assert [p.name for p in data_dir.iterdir()] == ["state.json"]


def test_day_rollover_resets_sent_today():
    state = {"seen_accessions": {}, "sent_today": {"NVDA": 3}, "pending_add": None,
             "last_poll_utc": None, "day": "2026-07-10"}
    state_mod.roll_day_if_needed(state, today="2026-07-11")
    assert state["sent_today"] == {}
    assert state["day"] == "2026-07-11"
