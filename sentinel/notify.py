"""Outbound notifications (spec section 8): one isolated seam.

    notify(channel, subject, body)   # channel: "sms" | "email"

Everything outbound goes through here — swapping channels is a routing
decision, not a refactor. Sends use the Inkbox Python SDK (bundled in the
Hermes image; signatures confirmed against inkbox-ai/inkbox):

    identity = Inkbox(api_key=...).get_identity(handle)
    identity.send_text(to="+1...", text=...)
    identity.send_email(to=[...], subject=..., body_text=...)

Severity routing:
  - below the ticker's min_severity: silent everywhere (gated in poll.py)
  - material / thesis_threatening: SMS buzz + full email
  - minor: email only
  - SMS additionally gated by the separate SMS_MIN_SEVERITY floor
  - digest mode queues findings for one daily email instead
"""

from __future__ import annotations

import json
import logging

from . import config

logger = logging.getLogger("sentinel.notify")

def _digest_queue_path():
    return config.DATA_DIR / "digest_pending.json"

# Injectable for tests: object with send_text/send_email.
_identity_override = None


def set_identity(identity) -> None:
    global _identity_override
    _identity_override = identity


def _identity():
    if _identity_override is not None:
        return _identity_override
    from inkbox import Inkbox  # bundled in the Hermes image

    client = Inkbox()  # resolves INKBOX_API_KEY from env
    return client.get_identity(config.INKBOX_IDENTITY)


def notify(channel: str, subject: str, body: str, *, to: str | None = None) -> None:
    """Send one outbound message. channel: "sms" | "email"."""
    identity = _identity()
    if channel == "sms":
        dest = to or config.OWNER_PHONE
        if not dest:
            logger.warning("no SMS destination (SENTINEL_OWNER_PHONE unset); dropping")
            return
        identity.send_text(to=dest, text=body)
    elif channel == "email":
        dest = to or config.OWNER_EMAIL
        if not dest:
            logger.warning("no email destination (SENTINEL_OWNER_EMAIL unset); dropping")
            return
        identity.send_email(to=[dest], subject=subject, body_text=body)
    else:
        raise ValueError(f"unknown channel: {channel!r}")


def send_sms_reply(text: str, to: str) -> None:
    """Reply to an inbound command text (used by the Hermes plugin)."""
    notify("sms", "", text, to=to)


# --- alert routing (section 8) --------------------------------------------------


def alert_filing(entry: dict, filing: dict, verdict: dict) -> None:
    """Route one verdict to channels by severity. Called from the poll loop
    only after the ticker's min_severity gate has passed."""
    severity = verdict["severity"]

    if config.DIGEST_MODE and severity != "thesis_threatening":
        _queue_digest(entry, filing, verdict)
        return

    subject = email_subject(entry, filing, verdict)
    notify("email", subject, format_email(entry, filing, verdict))

    if config.severity_rank(severity) >= config.severity_rank(config.SMS_MIN_SEVERITY):
        notify("sms", subject, format_sms(entry, filing, verdict))


def format_sms(entry: dict, filing: dict, verdict: dict) -> str:
    return (
        f"{entry['ticker']} {filing['form']} · {verdict['severity']}\n"
        f"{verdict['one_line_for_sms']}\n"
        f"watch: {verdict['watch_next']}"
    )


def email_subject(entry: dict, filing: dict, verdict: dict) -> str:
    pillar_ids = ", ".join(p["id"] for p in verdict["pillars_touched"]) or "no pillars"
    return f"{entry['ticker']} {filing['form']} · {verdict['severity']} · touches {pillar_ids}"


def format_email(entry: dict, filing: dict, verdict: dict) -> str:
    """The full section-6 analysis, readable — this is the depth channel."""
    lines = [
        f"{entry['ticker']} ({entry.get('company', '')}) — {filing['form']} filed {filing['filing_date']}",
        f"Items: {filing.get('items') or '—'}",
        f"Accession: {filing['accession_number']}",
        "",
        f"What happened: {verdict['happened']}",
        "",
    ]
    if verdict["pillars_touched"]:
        for p in verdict["pillars_touched"]:
            lines += [
                f"Pillar: {p['id']}",
                f"  Mechanism: {p.get('mechanism', '')}",
                f"  Bull read: {p.get('bull', '')}",
                f"  Bear read: {p.get('bear', '')}",
                "",
            ]
    else:
        lines += ["No thesis pillars touched.", ""]
    lines += [
        f"Severity: {verdict['severity']}   Confidence: {verdict['confidence']}",
        f"Watch next: {verdict['watch_next']}",
        "",
        "Not advice. This informs your read of your own thesis.",
    ]
    return "\n".join(lines)


# --- digest mode -----------------------------------------------------------------


def _queue_digest(entry: dict, filing: dict, verdict: dict) -> None:
    path = _digest_queue_path()
    queue = []
    if path.exists():
        with open(path) as f:
            queue = json.load(f)
    queue.append({"entry": entry, "filing": filing, "verdict": verdict})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(queue, f, indent=1)


def flush_digest() -> int:
    """Send the day's queued findings as one email. Returns count sent.

    Schedule as a second no-agent cron job at market close:
        python -m sentinel.notify
    """
    path = _digest_queue_path()
    if not path.exists():
        return 0
    with open(path) as f:
        queue = json.load(f)
    if not queue:
        return 0
    sections = [
        format_email(item["entry"], item["filing"], item["verdict"]) for item in queue
    ]
    body = ("\n\n" + "=" * 60 + "\n\n").join(sections)
    notify("email", f"Thesis Sentinel digest — {len(queue)} finding(s)", body)
    path.unlink()
    return len(queue)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"flushed {flush_digest()} digest finding(s)")
