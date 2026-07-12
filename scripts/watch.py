#!/usr/bin/env python3
"""Terminal driver for the add-a-stock flow — same state machine as SMS.

Each invocation feeds one message into commands.handle_inbound(), exactly as
an inbound text would, so the draft -> pending_add -> OK/cancel/edit cycle
(and its confirm-before-write safety) is identical across channels.

Usage (on the agent, via maritime exec):
    watch.py watch TSLA          draft pillars, hold as pending
    watch.py ok                  commit the pending draft to the watchlist
    watch.py cancel              discard the pending draft
    watch.py drop autonomy, add an energy pillar    (any other text = edit)
    watch.py list | status | mute NVDA | unmute NVDA
"""

import os
import sys
from pathlib import Path

_APP = os.environ.get("SENTINEL_APP_DIR", "/opt/data/app")
sys.path.insert(0, os.path.join(_APP, "vendor"))
sys.path.insert(0, _APP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel import commands, drafting, edgar, state as state_mod


def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print(__doc__)
        return

    reply = commands.handle_inbound(
        text,
        state=state_mod.load_state(),
        watchlist=state_mod.load_watchlist(),
        ticker_map=edgar.load_ticker_map(),
        draft_pillars=drafting.draft_pillars,
    )
    print(reply if reply is not None else f"(not a sentinel command: {text!r})")


if __name__ == "__main__":
    main()
