"""Configuration: paths, env vars, constants.

Everything the spec makes tunable lives here. On Maritime the data dir is
/opt/data (persists across sleep/wake); locally override with SENTINEL_DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- paths -----------------------------------------------------------------

DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/opt/data"))

WATCHLIST_PATH = DATA_DIR / "watchlist.json"
STATE_PATH = DATA_DIR / "state.json"
TICKER_MAP_PATH = DATA_DIR / "company_tickers.json"
LLM_FAILURE_LOG = DATA_DIR / "llm_failures.log"

# --- SEC / EDGAR -----------------------------------------------------------

# Mandatory on every SEC request. Format: "Name contact@email". No header -> 403.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "{cik_no_zeros}/{accession_no_dashes}/{primary_document}"
)

# Be a good citizen: space SEC calls (limit is 10 req/s; we sit far under).
SEC_REQUEST_SPACING_SECONDS = 0.15

# Forms worth analyzing. Primary target is 8-K per the spec; 10-K/10-Q/4
# are the optional widening.
MATERIAL_FORMS = tuple(
    f.strip()
    for f in os.environ.get("SENTINEL_MATERIAL_FORMS", "8-K,10-K,10-Q").split(",")
    if f.strip()
)

# Truncation budget for filing text fed to the model (characters).
FILING_TEXT_MAX_CHARS = int(os.environ.get("SENTINEL_FILING_MAX_CHARS", "40000"))

# Deterministic backstop: 8-K item codes that alert regardless of the
# LLM verdict or the ticker's severity floor. Kept to codes that are
# unconditionally grave for any thesis:
#   1.03 bankruptcy/receivership, 2.06 material impairment,
#   3.01 delisting notice, 4.01 auditor change, 4.02 restatement warning.
# (5.02 is deliberately excluded — it covers routine board changes too.)
ALWAYS_MATERIAL_ITEMS = tuple(
    c.strip()
    for c in os.environ.get(
        "SENTINEL_BACKSTOP_ITEMS", "1.03,2.06,3.01,4.01,4.02"
    ).split(",")
    if c.strip()
)

# --- severity model ----------------------------------------------------------

SEVERITY_ORDER = ("negligible", "minor", "material", "thesis_threatening")

DEFAULT_MIN_SEVERITY = os.environ.get("SENTINEL_DEFAULT_MIN_SEVERITY", "minor")

# Separate SMS floor (section 8): raise it without muting email.
SMS_MIN_SEVERITY = os.environ.get("SENTINEL_SMS_MIN_SEVERITY", "material")

# --- alert budget ------------------------------------------------------------

DAILY_CAP_PER_TICKER = int(os.environ.get("SENTINEL_DAILY_CAP", "3"))

# Quiet hours in local hours [start, end) — non-urgent alerts held.
QUIET_HOURS_START = int(os.environ.get("SENTINEL_QUIET_START", "22"))
QUIET_HOURS_END = int(os.environ.get("SENTINEL_QUIET_END", "8"))

# Digest mode: batch the day's findings into one email at the final sweep.
DIGEST_MODE = os.environ.get("SENTINEL_DIGEST", "").lower() in ("1", "true", "yes")

# --- notifications -----------------------------------------------------------

# Who the alerts go to (your phone / email, not the agent's own).
OWNER_PHONE = os.environ.get("SENTINEL_OWNER_PHONE", "")
OWNER_EMAIL = os.environ.get("SENTINEL_OWNER_EMAIL", "")

# Inkbox identity handle (injected as INKBOX_IDENTITY on the Hermes template).
INKBOX_IDENTITY = os.environ.get("INKBOX_IDENTITY", "")


def severity_rank(severity: str) -> int:
    """Rank a severity for >= comparisons; unknown strings rank lowest."""
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return -1
