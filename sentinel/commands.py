"""Inbound-command handler (spec section 7): route on the first word.

Pure state-machine logic — no SMS I/O in this module. The Hermes plugin
(hermes_plugin/) feeds inbound text in and sends the returned reply back.

handle_inbound() returns:
  - a reply string  -> the plugin texts it back and skips the agent turn
  - None            -> not ours; let normal Hermes dispatch proceed

The add-a-stock flow: `watch TSLA` (or a bare ticker) drafts pillars into
state.pending_add. Nothing enters the watchlist until an explicit OK.
Free text while a pending add exists is treated as edit instructions.
"""

from __future__ import annotations

import re

from . import config, edgar, state as state_mod

_TICKER_RE = re.compile(r"^[A-Za-z]{1,5}([.-][A-Za-z]{1,2})?$")

CONFIRM_SUFFIX = "Reply OK to watch, cancel to drop, or tell me what to change."


def handle_inbound(
    text: str,
    *,
    state: dict,
    watchlist: dict,
    ticker_map: dict,
    draft_pillars,
    save=None,
) -> str | None:
    """Route one inbound text. Mutates state/watchlist; persists via save().

    save(state, watchlist) is called after any mutation; defaults to writing
    both files to the data dir.
    """
    if save is None:
        save = _default_save

    words = (text or "").strip().split()
    if not words:
        return None
    first = words[0].lower()
    rest = words[1:]

    if first == "watch" and rest:
        reply = _start_add(rest[0], state, watchlist, ticker_map, draft_pillars)
        save(state, watchlist)
        return reply

    if first == "ok" and not rest:
        reply = _confirm_add(state, watchlist)
        save(state, watchlist)
        return reply

    if first == "cancel" and not rest:
        reply = _cancel_add(state)
        save(state, watchlist)
        return reply

    if first == "list" and not rest:
        return _list(watchlist)

    if first in ("mute", "unmute") and rest:
        reply = _set_muted(rest[0], watchlist, muted=(first == "mute"))
        save(state, watchlist)
        return reply

    if first == "status" and not rest:
        return _status(state, watchlist)

    # Free text while a draft is pending: edit instructions, one bounded call.
    if state.get("pending_add"):
        reply = _edit_add(text.strip(), state, draft_pillars)
        save(state, watchlist)
        return reply

    # Bare ticker ("TSLA") = shorthand for watch, but only if it resolves —
    # anything else is not ours and falls through to normal agent dispatch.
    if len(words) == 1 and _TICKER_RE.match(words[0]):
        if edgar.resolve_ticker(words[0], ticker_map):
            reply = _start_add(words[0], state, watchlist, ticker_map, draft_pillars)
            save(state, watchlist)
            return reply

    return None


# --- add-a-stock state machine -------------------------------------------------


def _start_add(raw_ticker: str, state, watchlist, ticker_map, draft_pillars) -> str:
    ticker = raw_ticker.upper()
    resolved = edgar.resolve_ticker(ticker, ticker_map)
    if resolved is None:
        return f"{ticker} not found as an SEC filer"
    if any(t["ticker"] == ticker for t in watchlist.get("tickers", [])):
        return f"Already watching {ticker}"

    pillars = draft_pillars(ticker, resolved["company"])
    if pillars is None:
        return f"Could not draft {ticker}, try again"

    state["pending_add"] = {
        "ticker": ticker,
        "company": resolved["company"],
        "cik": resolved["cik"],
        "pillars": pillars,
    }
    return format_confirmation(ticker, pillars)


def _confirm_add(state, watchlist) -> str:
    pending = state.get("pending_add")
    if not pending:
        return "Nothing pending."
    watchlist.setdefault("tickers", []).append(
        {
            "ticker": pending["ticker"],
            "company": pending["company"],
            "cik": pending["cik"],
            "min_severity": config.DEFAULT_MIN_SEVERITY,
            "pillars": pending["pillars"],
        }
    )
    state["pending_add"] = None
    return f"Watching {pending['ticker']} from the next sweep."


def _cancel_add(state) -> str:
    if not state.get("pending_add"):
        return "Nothing pending."
    state["pending_add"] = None
    return "Discarded."


def _edit_add(edit_text: str, state, draft_pillars) -> str:
    pending = state["pending_add"]
    revised = draft_pillars(
        pending["ticker"],
        pending["company"],
        prior_pillars=pending["pillars"],
        edit_text=edit_text,
    )
    if revised is None:
        # Keep the prior draft pending rather than writing garbage.
        return f"Could not draft {pending['ticker']}, try again"
    pending["pillars"] = revised
    return format_confirmation(pending["ticker"], revised)


def format_confirmation(ticker: str, pillars: list[dict]) -> str:
    lines = [f"Drafted {len(pillars)} pillars for {ticker}:"]
    for i, p in enumerate(pillars, 1):
        lines.append(f"{i}. {p['id']} — {p['claim']}")
        lines.append(f"   breaks: {p['breaks_if']}")
    lines.append(CONFIRM_SUFFIX)
    return "\n".join(lines)


# --- simple commands -------------------------------------------------------------


def _list(watchlist) -> str:
    tickers = watchlist.get("tickers", [])
    if not tickers:
        return "Watchlist is empty. Text 'watch TSLA' to add one."
    lines = []
    for t in tickers:
        flags = f" ({t['min_severity']}+" + (", muted)" if t.get("muted") else ")")
        lines.append(f"{t['ticker']}{flags}")
    return "Watching: " + ", ".join(lines)


def _set_muted(raw_ticker: str, watchlist, *, muted: bool) -> str:
    ticker = raw_ticker.upper()
    for t in watchlist.get("tickers", []):
        if t["ticker"] == ticker:
            t["muted"] = muted
            return f"{'Muted' if muted else 'Unmuted'} {ticker}."
    return f"{ticker} is not on the watchlist."


def _status(state, watchlist) -> str:
    tickers = [t["ticker"] for t in watchlist.get("tickers", [])]
    sent = state.get("sent_today", {})
    sent_line = ", ".join(f"{k}:{v}" for k, v in sent.items()) or "none"
    return (
        f"Last poll: {state.get('last_poll_utc') or 'never'}. "
        f"Watching {len(tickers)}: {', '.join(tickers) or 'none'}. "
        f"Texts today: {sent_line}."
    )


def _default_save(state, watchlist) -> None:
    state_mod.save_state(state)
    state_mod.save_watchlist(watchlist)
