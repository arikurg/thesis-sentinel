#!/usr/bin/env python3
"""Cron entry point: flush the day's digest queue as one email (spec §9).

Only useful with SENTINEL_DIGEST=1. Register at market close:

    hermes cron create "0 22 * * 1-5" --script /opt/data/app/scripts/run_digest.py \
        --no-agent --name thesis-sentinel-digest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from sentinel.notify import flush_digest

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"flushed {flush_digest()} digest finding(s)")
