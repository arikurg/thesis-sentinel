"""The single LLM seam: one call function, one strict-JSON guard.

Both prompts (thesis-impact and pillar-drafting) go through complete() and
parse_strict_json(). On a parse failure the caller gets None and the raw
output is logged to the data dir — never alert, never write the watchlist,
on malformed model output.

Backend resolution order:
  1. A caller-injected function (tests, or the Hermes plugin passing ctx.llm).
  2. OPENAI_API_KEY set -> OpenAI-compatible /chat/completions via urllib
     (OPENAI_BASE_URL overrides the endpoint; Maritime injects one when you
     leave the key blank and bill against credits).
  3. Inside the Hermes image -> agent.auxiliary_client.call_llm, the host's
     own provider router (signature confirmed against inkbox-ai/hermes-agent).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone

from . import config

logger = logging.getLogger("sentinel.llm")

# Injectable for tests / the Hermes plugin: fn(messages) -> str
_override_complete = None


def set_complete_fn(fn) -> None:
    global _override_complete
    _override_complete = fn


def complete(messages: list[dict], *, max_tokens: int = 2000, timeout: float = 120.0) -> str:
    """Run one chat completion and return the raw text output."""
    if _override_complete is not None:
        return _override_complete(messages)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        return _openai_compatible(messages, api_key, max_tokens=max_tokens, timeout=timeout)

    try:
        from agent.auxiliary_client import call_llm  # available inside the Hermes image
    except ImportError as exc:
        raise RuntimeError(
            "No LLM backend: set OPENAI_API_KEY (and optionally OPENAI_BASE_URL), "
            "or run inside the Hermes image, or inject one via set_complete_fn()."
        ) from exc
    response = call_llm(messages=messages, max_tokens=max_tokens, timeout=timeout)
    return response.choices[0].message.content or ""


def _openai_compatible(
    messages: list[dict], api_key: str, *, max_tokens: int, timeout: float
) -> str:
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    # On Maritime, HERMES_INFERENCE_MODEL names the model its LLM proxy serves.
    model = (
        os.environ.get("SENTINEL_LLM_MODEL")
        or os.environ.get("HERMES_INFERENCE_MODEL")
        or "gpt-4o-mini"
    )
    payload = json.dumps(
        {"model": model, "messages": messages, "max_tokens": max_tokens}
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    return body["choices"][0]["message"]["content"] or ""


# --- strict-JSON guard --------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


def parse_strict_json(raw: str, required_keys: tuple[str, ...], context: str) -> dict | None:
    """Strip code fences, parse JSON, check required keys.

    Returns None on any failure and logs the raw output to LLM_FAILURE_LOG.
    The caller must treat None as "do nothing" — no alert, no state write.
    """
    cleaned = _FENCE_RE.sub("", (raw or "").strip()).strip()
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError(f"expected object, got {type(data).__name__}")
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"missing keys: {missing}")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        _log_failure(context, str(exc), raw)
        return None


def _log_failure(context: str, error: str, raw: str) -> None:
    logger.warning("LLM output rejected (%s): %s", context, error)
    try:
        config.LLM_FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LLM_FAILURE_LOG, "a") as f:
            stamp = datetime.now(timezone.utc).isoformat()
            f.write(f"--- {stamp} {context}: {error}\n{raw}\n")
    except OSError:
        logger.exception("could not write llm_failures.log")
