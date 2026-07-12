"""An alert send failure must not abort the sweep or corrupt state."""

import copy


def _feed_with_new_8k(nvda_submissions, accession):
    updated = copy.deepcopy(nvda_submissions)
    recent = updated["filings"]["recent"]
    for col, value in [
        ("accessionNumber", accession),
        ("form", "8-K"),
        ("filingDate", "2026-07-12"),
        ("primaryDocument", "x.htm"),
        ("items", "2.02"),
    ]:
        recent[col].insert(0, value)
    for col in recent:
        if col not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items"):
            recent[col].insert(0, recent[col][0] if recent[col] else "")
    return updated


def test_alert_exception_does_not_abort_sweep(nvda_submissions, nvda_watchlist):
    from sentinel.poll import run_poll

    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": None, "day": None}

    def good_verdict(entry, filing, text):
        return {"happened": "x", "pillars_touched": [], "severity": "material",
                "confidence": "high", "watch_next": "", "one_line_for_sms": ""}

    def failing_alert(entry, filing, verdict):
        raise RuntimeError("409 sender_sms_pending")

    kwargs = dict(
        watchlist=nvda_watchlist, state=state,
        fetch_submissions=lambda cik: copy.deepcopy(nvda_submissions),
        fetch_filing_text=lambda cik, filing: "body",
        analyze=good_verdict, alert=failing_alert, save=False,
    )
    run_poll(**kwargs)  # seed

    updated = _feed_with_new_8k(nvda_submissions, "0001045810-26-000888")
    kwargs["fetch_submissions"] = lambda cik: copy.deepcopy(updated)

    summary = run_poll(**kwargs)  # alert raises; sweep must survive
    assert summary["analyzed"] == 1
    assert summary["alerted"] == 0
    # Accession recorded and loop completed: last_poll got stamped.
    assert "0001045810-26-000888" in state["seen_accessions"]["0001045810"]
    assert state["last_poll_utc"] is not None
    # sent_today not incremented for a failed send.
    assert state["sent_today"].get("NVDA", 0) == 0
