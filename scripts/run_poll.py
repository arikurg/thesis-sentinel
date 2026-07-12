#!/usr/bin/env python3
"""Cron entry point for the Maritime/Hermes no-agent cron job.

Register on the deployed agent:

    hermes cron create "*/30 13-23 * * 1-5" --script /opt/data/app/scripts/run_poll.py \
        --no-agent --name thesis-sentinel-poll

Runs the deterministic polling loop (sentinel/poll.py). Works even when the
sentinel package isn't pip-installed by adding the app dir to sys.path.
"""

import os
import sys
from pathlib import Path

# Hermes cron copies scripts into ~/.hermes/scripts/, so the app dir can't be
# derived from __file__ there; prefer the env var / Maritime default. The
# vendor dir holds pip deps (inkbox) that survive image restarts.
_APP = os.environ.get("SENTINEL_APP_DIR", "/opt/data/app")
sys.path.insert(0, os.path.join(_APP, "vendor"))
sys.path.insert(0, _APP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.poll import main

if __name__ == "__main__":
    main()
