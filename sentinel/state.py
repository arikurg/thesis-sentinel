"""Persistent state: watchlist.json and state.json under the data dir.

Writes are atomic (tmp file + rename) so a crash mid-write never corrupts
state. All functions take/return plain dicts matching the spec's data model.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config


def _empty_state() -> dict:
    return {
        "seen_accessions": {},
        "sent_today": {},
        "pending_add": None,
        "last_poll_utc": None,
        "day": None,
    }


def _empty_watchlist() -> dict:
    return {"tickers": []}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
        # mkstemp creates 0600 files owned by the current user; the poll
        # cron (uid 10000) and root exec shells both touch these files, so
        # keep them world-readable or they brick each other.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(path: Path | None = None) -> dict:
    path = path or config.STATE_PATH
    if not path.exists():
        return _empty_state()
    with open(path) as f:
        state = json.load(f)
    # Backfill keys so older state files survive schema additions.
    for key, value in _empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    _atomic_write_json(path or config.STATE_PATH, state)


def load_watchlist(path: Path | None = None) -> dict:
    path = path or config.WATCHLIST_PATH
    if not path.exists():
        return _empty_watchlist()
    with open(path) as f:
        return json.load(f)


def save_watchlist(watchlist: dict, path: Path | None = None) -> None:
    _atomic_write_json(path or config.WATCHLIST_PATH, watchlist)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def roll_day_if_needed(state: dict, today: str | None = None) -> dict:
    """Reset the per-day send counters when the day has rolled over."""
    today = today or today_str()
    if state.get("day") != today:
        state["day"] = today
        state["sent_today"] = {}
    return state


def is_first_run(state: dict, cik: str) -> bool:
    """True when we have never recorded any accession for this CIK.

    On first run every current accession is recorded as seen without
    alerting — otherwise the first wake texts the entire recent history.
    """
    return not state.get("seen_accessions", {}).get(cik)


def new_accessions(state: dict, cik: str, accessions: list[str]) -> list[str]:
    """Accessions not yet recorded for this CIK, in feed order (newest first)."""
    seen = set(state.get("seen_accessions", {}).get(cik, []))
    return [a for a in accessions if a not in seen]


def record_accessions(state: dict, cik: str, accessions: list[str]) -> None:
    """Record accessions as seen (dedupe key per spec — globally unique)."""
    bucket = state.setdefault("seen_accessions", {}).setdefault(cik, [])
    seen = set(bucket)
    for a in accessions:
        if a not in seen:
            bucket.append(a)
            seen.add(a)
