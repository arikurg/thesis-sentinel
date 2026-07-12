"""Stage 3/4 wiring: the Hermes pre_gateway_dispatch plugin against mocked
inbound events, and notify()'s severity/channel routing with a mock identity.

The plugin module is loaded the same way Hermes loads it (importlib from
path), so these tests exercise the real registration surface.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from sentinel import config, llm, notify

PLUGIN_PATH = Path(__file__).parent.parent / "hermes_plugin" / "thesis-sentinel" / "__init__.py"

DRAFT_JSON = json.dumps(
    {
        "pillars": [
            {"id": "demand", "claim": "c1", "breaks_if": "b1"},
            {"id": "margins", "claim": "c2", "breaks_if": "b2"},
        ]
    }
)


class FakeIdentity:
    def __init__(self):
        self.texts = []
        self.emails = []

    def send_text(self, *, to, text):
        self.texts.append((to, text))

    def send_email(self, *, to, subject, body_text):
        self.emails.append((to, subject, body_text))


class FakeCtxLlm:
    def complete(self, messages):
        return types.SimpleNamespace(text=DRAFT_JSON)


class FakeCtx:
    def __init__(self):
        self.hooks = {}
        self.llm = FakeCtxLlm()

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def load_plugin():
    spec = importlib.util.spec_from_file_location("hermes_hook_thesis_sentinel_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sms_event(body, remote="+15550001111"):
    return types.SimpleNamespace(
        text=f"[inkbox:sms from={remote} | contact]\n{body}",
        raw_message={
            "event_type": "text.received",
            "data": {
                "text_message": {
                    "id": "t1",
                    "direction": "inbound",
                    "remote_phone_number": remote,
                    "text": body,
                }
            },
        },
    )


@pytest.fixture
def plugin(data_dir, monkeypatch):
    """Registered plugin with fake ctx, fake identity, cached ticker map."""
    # Cache a ticker map so no network fetch happens.
    with open(config.TICKER_MAP_PATH, "w") as f:
        json.dump({"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}}, f)

    fake_identity = FakeIdentity()
    notify.set_identity(fake_identity)
    monkeypatch.setattr(config, "OWNER_PHONE", "+15559990000")
    monkeypatch.setattr(config, "OWNER_EMAIL", "me@example.com")

    module = load_plugin()
    ctx = FakeCtx()
    module.register(ctx)

    yield types.SimpleNamespace(
        hook=ctx.hooks["pre_gateway_dispatch"], identity=fake_identity, module=module
    )
    llm.set_complete_fn(None)
    notify.set_identity(None)


def test_register_wires_hook_and_llm(plugin):
    assert plugin.hook is not None


def test_command_sms_is_handled_and_skipped(plugin, data_dir):
    result = plugin.hook(event=sms_event("watch TSLA"), gateway=None, session_store=None)
    assert result == {"action": "skip", "reason": "handled by thesis-sentinel"}
    # Reply went back to the sender's number.
    (to, text), = plugin.identity.texts
    assert to == "+15550001111"
    assert text.startswith("Drafted 2 pillars for TSLA:")
    # pending_add persisted; watchlist untouched.
    state = json.loads((data_dir / "state.json").read_text())
    assert state["pending_add"]["ticker"] == "TSLA"
    assert not (data_dir / "watchlist.json").exists() or not json.loads(
        (data_dir / "watchlist.json").read_text()
    ).get("tickers")


def test_full_confirm_cycle_over_sms(plugin, data_dir):
    plugin.hook(event=sms_event("watch TSLA"))
    result = plugin.hook(event=sms_event("OK"))
    assert result["action"] == "skip"
    assert plugin.identity.texts[-1][1] == "Watching TSLA from the next sweep."
    watchlist = json.loads((data_dir / "watchlist.json").read_text())
    assert watchlist["tickers"][0]["ticker"] == "TSLA"
    assert watchlist["tickers"][0]["cik"] == "0001318605"
    state = json.loads((data_dir / "state.json").read_text())
    assert state["pending_add"] is None


def test_free_chat_falls_through_to_agent(plugin):
    assert plugin.hook(event=sms_event("hey what's up with the market")) is None
    assert plugin.identity.texts == []


def test_non_sms_event_ignored(plugin):
    email_event = types.SimpleNamespace(text="plain email", raw_message={"data": {}})
    assert plugin.hook(event=email_event) is None
    assert plugin.hook(event=None) is None


def test_outbound_sms_status_callback_ignored(plugin):
    ev = sms_event("watch TSLA")
    ev.raw_message["data"]["text_message"]["direction"] = "outbound"
    assert plugin.hook(event=ev) is None


# --- notify severity routing (section 8) ---------------------------------------


ENTRY = {"ticker": "NVDA", "company": "NVIDIA Corp", "cik": "0001045810",
         "min_severity": "minor", "pillars": []}
FILING = {"accession_number": "0001045810-26-000060", "form": "8-K",
          "filing_date": "2026-07-02", "primary_document": "x.htm", "items": "5.02"}


def make_verdict(severity):
    return {
        "happened": "CFO departure announced.",
        "pillars_touched": [{"id": "execution", "mechanism": "m", "bull": "b", "bear": "r"}],
        "severity": severity,
        "confidence": "med",
        "watch_next": "successor quality",
        "one_line_for_sms": "CFO out in 60d, internal successor named.",
    }


@pytest.fixture
def channels(data_dir, monkeypatch):
    fake = FakeIdentity()
    notify.set_identity(fake)
    monkeypatch.setattr(config, "OWNER_PHONE", "+15559990000")
    monkeypatch.setattr(config, "OWNER_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "DIGEST_MODE", False)
    yield fake
    notify.set_identity(None)


def test_minor_sends_email_only(channels):
    notify.alert_filing(ENTRY, FILING, make_verdict("minor"))
    assert len(channels.emails) == 1
    assert channels.texts == []
    to, subject, body = channels.emails[0]
    assert to == ["me@example.com"]
    assert subject == "NVDA 8-K · minor · touches execution"
    assert "Bull read:" in body and "Bear read:" in body


def test_material_sends_sms_and_email(channels):
    notify.alert_filing(ENTRY, FILING, make_verdict("material"))
    assert len(channels.emails) == 1
    assert len(channels.texts) == 1
    to, text = channels.texts[0]
    assert to == "+15559990000"
    assert text == (
        "NVDA 8-K · material\n"
        "CFO out in 60d, internal successor named.\n"
        "watch: successor quality"
    )


def test_digest_mode_queues_instead_of_sending(channels, monkeypatch):
    monkeypatch.setattr(config, "DIGEST_MODE", True)
    notify.alert_filing(ENTRY, FILING, make_verdict("material"))
    assert channels.emails == [] and channels.texts == []

    # thesis_threatening ignores digest mode and always sends.
    notify.alert_filing(ENTRY, FILING, make_verdict("thesis_threatening"))
    assert len(channels.emails) == 1 and len(channels.texts) == 1

    # Flush sends the queued finding as one digest email.
    sent = notify.flush_digest()
    assert sent == 1
    assert "digest" in channels.emails[-1][1]
    assert notify.flush_digest() == 0  # queue cleared
