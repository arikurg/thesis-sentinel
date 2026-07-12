"""The cron-wake polling loop (spec section 4).

Registered on Maritime as a Hermes no-agent cron job:

    hermes cron create "*/30 13-23 * * 1-5" --script /opt/data/app/run_poll.py \
        --no-agent --name thesis-sentinel-poll

Deterministic plumbing throughout; the only LLM touchpoint is the injected
`analyze` function (section 6). Dependencies are injectable for testing.

Run manually: python -m sentinel.poll
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import config, edgar, state as state_mod

logger = logging.getLogger("sentinel.poll")


def in_quiet_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    start, end = config.QUIET_HOURS_START, config.QUIET_HOURS_END
    if start == end:
        return False
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end  # window wraps midnight


def run_poll(
    *,
    watchlist: dict | None = None,
    state: dict | None = None,
    fetch_submissions=None,
    fetch_filing_text=None,
    analyze=None,
    alert=None,
    now: datetime | None = None,
    save: bool = True,
) -> dict:
    """One cron wake. Returns a summary dict for logging/testing.

    Injectable deps (all default to the real implementations):
      fetch_submissions(cik) -> submissions dict
      fetch_filing_text(cik, filing) -> str
      analyze(ticker_entry, filing, filing_text) -> verdict dict | None
      alert(ticker_entry, filing, verdict) -> None   (severity routing inside)
    """
    watchlist = watchlist if watchlist is not None else state_mod.load_watchlist()
    state = state if state is not None else state_mod.load_state()
    fetch_submissions = fetch_submissions or edgar.fetch_submissions
    fetch_filing_text = fetch_filing_text or edgar.fetch_filing_text

    if analyze is None:
        from .analysis import analyze_filing as analyze
    if alert is None:
        from .notify import alert_filing as alert

    state_mod.roll_day_if_needed(state)

    summary = {"tickers": 0, "new_filings": 0, "analyzed": 0, "alerted": 0, "first_runs": 0}

    for entry in watchlist.get("tickers", []):
        if entry.get("muted"):
            continue
        ticker, cik = entry["ticker"], entry["cik"]
        summary["tickers"] += 1

        try:
            submissions = fetch_submissions(cik)
            filings = edgar.parse_recent_filings(submissions)
        except Exception:
            logger.exception("EDGAR fetch/parse failed for %s; skipping", ticker)
            continue

        all_accessions = [f["accession_number"] for f in filings]

        # First-run guard: record everything as seen, alert on nothing.
        if state_mod.is_first_run(state, cik):
            state_mod.record_accessions(state, cik, all_accessions)
            summary["first_runs"] += 1
            logger.info("%s: first run, seeded %d accessions silently", ticker, len(all_accessions))
            continue

        fresh = set(state_mod.new_accessions(state, cik, all_accessions))
        new_filings = [f for f in filings if f["accession_number"] in fresh]
        summary["new_filings"] += len(new_filings)

        for filing in new_filings:
            # Record no matter the verdict — each filing is heard about once, ever.
            state_mod.record_accessions(state, cik, [filing["accession_number"]])

            if not edgar.is_material(filing):
                continue

            try:
                filing_text = fetch_filing_text(cik, filing)
            except Exception:
                logger.exception("%s: body fetch failed for %s", ticker, filing["accession_number"])
                continue

            verdict = analyze(entry, filing, filing_text)
            summary["analyzed"] += 1
            if verdict is None:
                continue  # parse-failure guard: never alert on malformed output

            severity = verdict.get("severity", "")
            floor = entry.get("min_severity", config.DEFAULT_MIN_SEVERITY)
            if config.severity_rank(severity) < config.severity_rank(floor):
                continue

            urgent = severity == "thesis_threatening"
            cap_hit = state["sent_today"].get(ticker, 0) >= config.DAILY_CAP_PER_TICKER
            quiet = in_quiet_hours(now)
            if not urgent and (cap_hit or quiet):
                logger.info(
                    "%s: holding %s alert (cap_hit=%s quiet=%s)",
                    ticker, severity, cap_hit, quiet,
                )
                continue

            try:
                alert(entry, filing, verdict)
            except Exception:
                # A send failure must not abort the sweep: the accession is
                # already recorded, and crashing here would lose the whole
                # wake's in-memory state and re-alert everything next run.
                logger.exception("%s: alert send failed for %s", ticker, filing["accession_number"])
                continue
            state["sent_today"][ticker] = state["sent_today"].get(ticker, 0) + 1
            summary["alerted"] += 1

    state["last_poll_utc"] = state_mod.utc_now_iso()
    if save:
        state_mod.save_state(state)
    logger.info("poll done: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run_poll()


if __name__ == "__main__":
    main()
