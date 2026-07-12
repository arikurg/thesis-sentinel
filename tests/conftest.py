import json
import os
from pathlib import Path

# Point the data dir at a temp location BEFORE sentinel.config is imported.
os.environ.setdefault("SENTINEL_DATA_DIR", "/tmp/sentinel-test-unset")
os.environ.setdefault("SEC_USER_AGENT", "ThesisSentinel Test test@example.com")

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated data dir per test, patched into sentinel.config."""
    from sentinel import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(config, "TICKER_MAP_PATH", tmp_path / "company_tickers.json")
    monkeypatch.setattr(config, "LLM_FAILURE_LOG", tmp_path / "llm_failures.log")
    return tmp_path


@pytest.fixture
def nvda_submissions():
    with open(FIXTURES / "submissions_nvda.json") as f:
        return json.load(f)


@pytest.fixture
def nvda_8k_html():
    with open(FIXTURES / "8k_nvda_2026-07-02.htm") as f:
        return f.read()


@pytest.fixture
def nvda_watchlist():
    return {
        "tickers": [
            {
                "ticker": "NVDA",
                "company": "NVIDIA Corp",
                "cik": "0001045810",
                "min_severity": "minor",
                "pillars": [
                    {
                        "id": "moat",
                        "claim": "CUDA + software lock-in keeps switching costs high",
                        "breaks_if": "major customer ships a competitive non-CUDA stack at scale",
                    },
                    {
                        "id": "execution",
                        "claim": "Current management executes on margins and roadmap",
                        "breaks_if": "exec turnover, guidance miss, restatement",
                    },
                ],
            }
        ]
    }
