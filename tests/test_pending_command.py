"""The `pending` command re-displays the held draft without an LLM call."""

from sentinel.commands import handle_inbound

TICKER_MAP = {"TSLA": {"cik": "0001318605", "company": "Tesla, Inc."}}
DRAFT = [{"id": "demand", "claim": "c", "breaks_if": "b"}]


def _send(text, state, calls):
    def draft(ticker, company, prior_pillars=None, edit_text=None):
        calls.append(text)
        return DRAFT

    return handle_inbound(
        text, state=state, watchlist={"tickers": []}, ticker_map=TICKER_MAP,
        draft_pillars=draft, save=lambda s, w: None,
    )


def test_pending_shows_draft_without_llm_call():
    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": None, "day": None}
    calls = []
    _send("watch TSLA", state, calls)
    assert calls == ["watch TSLA"]

    reply = _send("pending", state, calls)
    assert reply.startswith("Drafted 1 pillars for TSLA:")
    assert "demand" in reply
    assert calls == ["watch TSLA"]  # no re-draft happened


def test_pending_with_nothing_held():
    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": None, "day": None}
    assert _send("pending", state, []) == "Nothing pending."


def test_show_prints_full_thesis():
    watchlist = {"tickers": [{
        "ticker": "TSLA", "company": "Tesla, Inc.", "cik": "0001318605",
        "min_severity": "minor", "pillars": DRAFT,
    }]}
    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": None, "day": None}
    reply = handle_inbound("show tsla", state=state, watchlist=watchlist,
                           ticker_map=TICKER_MAP, draft_pillars=None,
                           save=lambda s, w: None)
    assert "TSLA — Tesla, Inc." in reply
    assert "alerts at minor+" in reply
    assert "1. demand — c" in reply
    assert "breaks: b" in reply

    reply = handle_inbound("show ZZZZ", state=state, watchlist=watchlist,
                           ticker_map=TICKER_MAP, draft_pillars=None,
                           save=lambda s, w: None)
    assert reply == "ZZZZ is not on the watchlist."
