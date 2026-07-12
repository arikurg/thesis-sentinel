# Thesis Sentinel

A multi-ticker SEC-filings watcher on Maritime (Hermes Identity template).
Deterministic Python does the fetching, parsing, and dedupe; the LLM does one
scoped job — judging whether a filing moves your stated thesis pillars. Built
from `thesis-sentinel-spec.md`.

## Layout

```
sentinel/            the package (stdlib-only, no runtime deps)
  config.py          env, paths, severity model, alert budget
  edgar.py           SEC fetches (User-Agent enforced), parse, archives URL, html→text
  state.py           watchlist.json + state.json, atomic writes, first-run guard
  poll.py            the cron-wake loop (spec §4) — injectable deps
  prompts.py         the two prompts, verbatim from the spec (§6, §7)
  llm.py             single LLM seam + shared strict-JSON parse guard
  analysis.py        thesis-impact call (§6)
  drafting.py        pillar-drafting call (§7)
  commands.py        inbound router + draft→pending_add→OK/cancel/edit machine (§7)
  notify.py          notify(channel, subject, body) — the one outbound seam (§8)
hermes_plugin/thesis-sentinel/
                     Hermes plugin: pre_gateway_dispatch inbound-SMS router
scripts/run_poll.py  no-agent cron entry point
tests/               48 tests, all fixture-backed (no network, no LLM, no SMS)
```

## Resolved [CONFIRM #1] (verified against repo source, 2026-07-11)

- **Send** (`inkbox-ai/inkbox`, Python SDK, bundled in the Hermes image):
  `Inkbox()` reads `INKBOX_API_KEY` from env → `client.get_identity(handle)` →
  `identity.send_text(to="+1...", text=...)` /
  `identity.send_email(to=[...], subject=..., body_text=...)`.
  Wrapped in exactly one function: `sentinel.notify.notify()`.
- **Inbound SMS** (`inkbox-ai/hermes-agent`): the gateway subscribes a
  `text.received` webhook and builds a `MessageEvent` per inbound text
  (`gateway/platforms/inkbox.py:_on_text_received`). A plugin hook
  `pre_gateway_dispatch` (declared in `hermes_cli/plugins.py` VALID_HOOKS,
  honored in `gateway/run.py`) fires before auth/agent dispatch; returning
  `{"action": "skip"}` suppresses the agent turn. The raw envelope on
  `event.raw_message` carries `data.text_message.{text, remote_phone_number,
  direction}`. This is how bare-word commands (`watch TSLA`, `OK`) route
  deterministically — no LLM in the command path.
- **Cron routine**: Hermes cron jobs are data records
  (`cron/jobs.py`; scheduler `tick()` runs due jobs). A job with a script and
  `no_agent=True` runs the script directly — the poll loop is one of those.
- **Plugin LLM billing**: the plugin routes pillar-drafting calls through
  `ctx.llm.complete(messages)` (`agent/plugin_llm.py`) — host-owned, billed to
  Maritime credits. The cron script uses `OPENAI_API_KEY`/`OPENAI_BASE_URL` if
  set, else `agent.auxiliary_client.call_llm` inside the image.

## Deploy (Maritime)

```bash
maritime create thesis-sentinel --template hermes_identity
maritime env set thesis-sentinel SEC_USER_AGENT="ThesisSentinel YourName you@email.com" --no-secret
maritime env set thesis-sentinel SENTINEL_OWNER_PHONE="+1..." --no-secret
maritime env set thesis-sentinel SENTINEL_OWNER_EMAIL="you@email.com" --no-secret
# Leave OPENAI_API_KEY unset to bill LLM calls to Maritime credits.
maritime deploy thesis-sentinel
```

On the agent:

```bash
# 1. App code lives on the persistent volume
cp -r thesis-sentinel /opt/data/app && pip install -e /opt/data/app

# 2. Inbound command router
cp -r /opt/data/app/hermes_plugin/thesis-sentinel ~/.hermes/plugins/

# 3. Seed the watchlist (or just text `watch NVDA` once live)
cp /opt/data/app/watchlist.example.json /opt/data/watchlist.json

# 4. Polling loop: every 30 min, weekdays, market hours + evening sweep (ET)
hermes cron create "*/30 13-23 * * 1-5" --script /opt/data/app/scripts/run_poll.py \
    --no-agent --name thesis-sentinel-poll
# Optional digest flush at ~6pm ET if SENTINEL_DIGEST=1
hermes cron create "0 22 * * 1-5" --script /opt/data/app/scripts/run_digest.py \
    --no-agent --name thesis-sentinel-digest
```

Then in the Maritime dashboard confirm the identity panel shows a phone
number, and **text `START` to it** (10DLC opt-in). Fresh numbers take ~10-15
min before outbound SMS works (`409 sender_sms_pending`); email works from
the first deploy, so test with email first:

```bash
python -c "from sentinel.notify import notify; notify('email','sentinel test','hello')"
```

The first poll run seeds every current accession silently (first-run guard);
alerts start with the next genuinely new filing.

## Config knobs (env)

| Var | Default | Meaning |
|---|---|---|
| `SENTINEL_DATA_DIR` | `/opt/data` | state/watchlist location |
| `SEC_USER_AGENT` | *(required)* | `Name email` — SEC rejects without it |
| `SENTINEL_MATERIAL_FORMS` | `8-K,10-K,10-Q` | forms sent to the model |
| `SENTINEL_DEFAULT_MIN_SEVERITY` | `minor` | floor for new tickers |
| `SENTINEL_SMS_MIN_SEVERITY` | `material` | separate SMS floor (email unaffected) |
| `SENTINEL_DAILY_CAP` | `3` | alerts per ticker per day (thesis_threatening exempt) |
| `SENTINEL_QUIET_START/END` | `22`/`8` | quiet hours, local (thesis_threatening exempt) |
| `SENTINEL_DIGEST` | off | batch findings into one daily email |
| `SENTINEL_OWNER_PHONE/EMAIL` | — | where alerts go |

## Texting it

`watch TSLA` (or just `TSLA`) → drafts pillars → reply `OK` to commit,
`cancel` to drop, or free text to revise the draft. Also `list`,
`mute NVDA` / `unmute NVDA`, `status`. Anything else falls through to the
normal Hermes agent chat.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest tests/ -q
```

Fixtures are real EDGAR captures (NVDA submissions + the 2026-07-02 8-K).
The LLM, SMS, and network layers are injectable and mocked throughout; the
guard tests prove malformed model output can never produce an alert or a
watchlist write.
