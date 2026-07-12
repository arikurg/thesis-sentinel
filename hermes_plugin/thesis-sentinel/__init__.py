"""Hermes plugin: deterministic inbound-SMS command routing for Thesis Sentinel.

Install: copy this directory to ~/.hermes/plugins/thesis-sentinel/ on the
Maritime agent (and make the `sentinel` package importable, e.g. pip install
-e /opt/data/app or drop it on PYTHONPATH).

Integration points (confirmed against inkbox-ai/hermes-agent source):
  - ctx.register_hook("pre_gateway_dispatch", fn) — fires once per incoming
    MessageEvent BEFORE auth and agent dispatch (hermes_cli/plugins.py,
    VALID_HOOKS; dispatch honored in gateway/run.py).
  - Returning {"action": "skip", "reason": ...} drops the agent turn; None
    lets normal dispatch proceed.
  - Inbound SMS events carry the raw Inkbox webhook envelope on
    event.raw_message: data.text_message.{text, remote_phone_number, direction}
    (gateway/platforms/inkbox.py, _on_text_received / _build_sms_text_event).
  - LLM calls for pillar drafting go through ctx.llm.complete(messages) —
    host-owned, billed to the user's active model/credits (agent/plugin_llm.py).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("thesis-sentinel-plugin")

_ctx = None


def register(ctx) -> None:
    global _ctx
    _ctx = ctx
    # Route pillar-drafting LLM calls through the host (Maritime credits).
    try:
        from sentinel import llm as sentinel_llm

        sentinel_llm.set_complete_fn(lambda messages: ctx.llm.complete(messages).text)
    except ImportError:
        logger.exception(
            "sentinel package not importable — install it (pip install -e) "
            "or add it to PYTHONPATH; inbound commands disabled"
        )
        return
    ctx.register_hook("pre_gateway_dispatch", _on_pre_dispatch)
    logger.info("thesis-sentinel inbound command router registered")


def _extract_inbound_sms(event) -> tuple[str, str] | None:
    """Return (body, remote_phone_number) for an inbound Inkbox SMS, else None."""
    envelope = getattr(event, "raw_message", None)
    if not isinstance(envelope, dict):
        return None
    text_msg = (envelope.get("data") or {}).get("text_message") or {}
    if not text_msg:
        return None
    direction = str(text_msg.get("direction") or "").lower()
    if direction and direction != "inbound":
        return None
    remote = (text_msg.get("remote_phone_number") or "").strip()
    body = (text_msg.get("text") or "").strip()
    if not remote or not body:
        return None
    return body, remote


def _on_pre_dispatch(*, event=None, gateway=None, session_store=None, **kwargs):
    if event is None:
        return None
    inbound = _extract_inbound_sms(event)
    if inbound is None:
        return None  # not an inbound SMS — not ours
    body, remote = inbound

    try:
        from sentinel import commands, drafting, edgar, state as state_mod

        state = state_mod.load_state()
        watchlist = state_mod.load_watchlist()
        ticker_map = edgar.load_ticker_map()

        reply = commands.handle_inbound(
            body,
            state=state,
            watchlist=watchlist,
            ticker_map=ticker_map,
            draft_pillars=drafting.draft_pillars,
        )
    except Exception:
        logger.exception("thesis-sentinel inbound handling failed; passing to agent")
        return None

    if reply is None:
        return None  # free chat — normal Hermes dispatch handles it

    try:
        from sentinel.notify import send_sms_reply

        send_sms_reply(reply, to=remote)
    except Exception:
        logger.exception("thesis-sentinel reply send failed")
        # Still skip: the command was executed against state; letting the
        # agent also process it would double-handle the mutation.

    return {"action": "skip", "reason": "handled by thesis-sentinel"}
