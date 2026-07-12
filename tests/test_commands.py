"""Stage 3: inbound-command routing and the draft -> pending_add -> OK/cancel/edit
state machine, driven entirely by mocked inbound texts. No SMS, no network."""

import pytest

from sentinel.commands import handle_inbound

TICKER_MAP = {
    "TSLA": {"cik": "0001318605", "company": "Tesla, Inc."},
    "NVDA": {"cik": "0001045810", "company": "NVIDIA Corp"},
}

DRAFT = [
    {"id": "demand", "claim": "delivery growth stays ahead of price cuts",
     "breaks_if": "deliveries miss, guidance cut"},
    {"id": "margins", "claim": "auto gross margin holds its floor",
     "breaks_if": "margin compression in quarterly results"},
    {"id": "autonomy", "claim": "FSD / robotaxi optionality stays credible",
     "breaks_if": "regulatory block, recall, timeline slip"},
]

REVISED = [
    {"id": "energy", "claim": "energy storage becomes a second growth engine",
     "breaks_if": "deployment growth stalls in quarterly results"},
] + DRAFT[:2]


@pytest.fixture
def env():
    """Fresh state + watchlist + call recorder around handle_inbound."""
    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": "2026-07-11T18:00:00Z", "day": "2026-07-11"}
    watchlist = {"tickers": [{
        "ticker": "NVDA", "company": "NVIDIA Corp", "cik": "0001045810",
        "min_severity": "minor", "pillars": DRAFT,
    }]}
    calls = {"draft": [], "saves": 0}

    def draft_pillars(ticker, company, prior_pillars=None, edit_text=None):
        calls["draft"].append((ticker, company, prior_pillars, edit_text))
        return REVISED if edit_text else DRAFT

    def send(text):
        return handle_inbound(
            text,
            state=state,
            watchlist=watchlist,
            ticker_map=TICKER_MAP,
            draft_pillars=draft_pillars,
            save=lambda s, w: calls.__setitem__("saves", calls["saves"] + 1),
        )

    class Env:
        pass

    e = Env()
    e.state, e.watchlist, e.calls, e.send = state, watchlist, calls, send
    return e


# --- add-a-stock happy path ---------------------------------------------------


def test_watch_drafts_and_holds_pending(env):
    reply = env.send("watch TSLA")
    assert reply.startswith("Drafted 3 pillars for TSLA:")
    assert "1. demand — delivery growth stays ahead of price cuts" in reply
    assert "   breaks: deliveries miss, guidance cut" in reply
    assert "Reply OK to watch, cancel to drop" in reply
    # Draft is pending, NOT in the watchlist.
    assert env.state["pending_add"]["ticker"] == "TSLA"
    assert env.state["pending_add"]["cik"] == "0001318605"
    assert [t["ticker"] for t in env.watchlist["tickers"]] == ["NVDA"]


def test_ok_commits_pending_to_watchlist(env):
    env.send("watch TSLA")
    reply = env.send("OK")
    assert reply == "Watching TSLA from the next sweep."
    assert env.state["pending_add"] is None
    tsla = next(t for t in env.watchlist["tickers"] if t["ticker"] == "TSLA")
    assert tsla["pillars"] == DRAFT
    assert tsla["cik"] == "0001318605"
    assert tsla["min_severity"]  # default applied


def test_cancel_discards_pending(env):
    env.send("watch TSLA")
    reply = env.send("cancel")
    assert reply == "Discarded."
    assert env.state["pending_add"] is None
    assert [t["ticker"] for t in env.watchlist["tickers"]] == ["NVDA"]


def test_free_text_while_pending_is_edit_instruction(env):
    env.send("watch TSLA")
    reply = env.send("drop autonomy, add an energy storage pillar")
    # Edit call got the prior draft and the instructions.
    ticker, company, prior, edit = env.calls["draft"][-1]
    assert (ticker, company) == ("TSLA", "Tesla, Inc.")
    assert prior == DRAFT
    assert edit == "drop autonomy, add an energy storage pillar"
    # Revised draft replaces the pending one, still awaiting OK.
    assert env.state["pending_add"]["pillars"] == REVISED
    assert reply.startswith("Drafted 3 pillars for TSLA:")
    assert "energy" in reply
    assert env.state["pending_add"] is not None


def test_edit_then_ok_commits_revision(env):
    env.send("watch TSLA")
    env.send("add an energy pillar")
    env.send("OK")
    tsla = next(t for t in env.watchlist["tickers"] if t["ticker"] == "TSLA")
    assert tsla["pillars"] == REVISED


def test_bare_ticker_is_watch_shorthand(env):
    reply = env.send("TSLA")
    assert reply.startswith("Drafted 3 pillars for TSLA:")
    assert env.state["pending_add"]["ticker"] == "TSLA"


# --- guards ---------------------------------------------------------------------


def test_unknown_symbol(env):
    reply = env.send("watch ZZZZZ")
    assert reply == "ZZZZZ not found as an SEC filer"
    assert env.state["pending_add"] is None


def test_already_watching(env):
    reply = env.send("watch NVDA")
    assert reply == "Already watching NVDA"
    assert env.state["pending_add"] is None


def test_draft_failure_writes_nothing(env):
    def failing_draft(ticker, company, prior_pillars=None, edit_text=None):
        return None  # strict-JSON guard rejected the model output

    reply = handle_inbound(
        "watch TSLA", state=env.state, watchlist=env.watchlist,
        ticker_map=TICKER_MAP, draft_pillars=failing_draft, save=lambda s, w: None,
    )
    assert reply == "Could not draft TSLA, try again"
    assert env.state["pending_add"] is None
    assert [t["ticker"] for t in env.watchlist["tickers"]] == ["NVDA"]


def test_edit_failure_keeps_prior_draft(env):
    env.send("watch TSLA")

    def failing_draft(ticker, company, prior_pillars=None, edit_text=None):
        return None

    reply = handle_inbound(
        "make it about robots", state=env.state, watchlist=env.watchlist,
        ticker_map=TICKER_MAP, draft_pillars=failing_draft, save=lambda s, w: None,
    )
    assert reply == "Could not draft TSLA, try again"
    assert env.state["pending_add"]["pillars"] == DRAFT  # prior draft survives


def test_ok_and_cancel_with_nothing_pending(env):
    assert env.send("OK") == "Nothing pending."
    assert env.send("cancel") == "Nothing pending."


def test_non_command_chat_falls_through_to_agent(env):
    assert env.send("what do you think about the market today?") is None
    assert env.send("hello") is None  # not a resolvable ticker
    assert env.send("") is None


# --- simple commands ---------------------------------------------------------------


def test_list(env):
    reply = env.send("list")
    assert "NVDA" in reply and "minor+" in reply


def test_mute_unmute_and_loop_skips_muted(env):
    assert env.send("mute NVDA") == "Muted NVDA."
    assert env.watchlist["tickers"][0]["muted"] is True

    # The poll loop skips muted tickers entirely.
    from sentinel.poll import run_poll
    summary = run_poll(
        watchlist=env.watchlist, state=env.state,
        fetch_submissions=lambda cik: (_ for _ in ()).throw(AssertionError("should not fetch")),
        analyze=lambda *a: None, alert=lambda *a: None, save=False,
    )
    assert summary["tickers"] == 0

    assert env.send("unmute NVDA") == "Unmuted NVDA."
    assert env.watchlist["tickers"][0]["muted"] is False


def test_mute_unknown_ticker(env):
    assert env.send("mute TSLA") == "TSLA is not on the watchlist."


def test_status(env):
    env.state["sent_today"] = {"NVDA": 2}
    reply = env.send("status")
    assert "2026-07-11T18:00:00Z" in reply
    assert "Watching 1: NVDA" in reply
    assert "NVDA:2" in reply


def test_commands_are_case_insensitive(env):
    assert env.send("WATCH tsla").startswith("Drafted")
    assert env.send("ok") == "Watching TSLA from the next sweep."
