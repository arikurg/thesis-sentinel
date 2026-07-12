"""Stage 2: thesis-impact call against the saved 8-K fixture.

The LLM is mocked via llm.set_complete_fn; the tests prove prompt
construction, the strict-JSON guard, and that malformed output never
produces a verdict (and therefore never an alert).
"""

import json

import pytest

from sentinel import edgar, llm
from sentinel.analysis import analyze_filing

NVDA_8K_FILING = {
    "accession_number": "0001045810-26-000060",
    "form": "8-K",
    "filing_date": "2026-07-02",
    "primary_document": "nvda-20260628.htm",
    "items": "5.02",
}

GOOD_VERDICT = {
    "happened": "A director retired from the board effective June 28, 2026.",
    "pillars_touched": [
        {
            "id": "execution",
            "mechanism": "board composition change",
            "bull": "orderly, planned transition",
            "bear": "loss of experienced oversight",
        }
    ],
    "severity": "minor",
    "confidence": "high",
    "watch_next": "whether further board or C-suite departures follow",
    "one_line_for_sms": "Board member retired, orderly transition. Touches execution.",
}


@pytest.fixture(autouse=True)
def _reset_llm():
    yield
    llm.set_complete_fn(None)


@pytest.fixture
def nvda_entry(nvda_watchlist):
    return nvda_watchlist["tickers"][0]


@pytest.fixture
def filing_text(nvda_8k_html):
    return edgar.html_to_text(nvda_8k_html, 40000)


def test_prompt_carries_pillars_and_filing_body(nvda_entry, filing_text):
    captured = {}

    def fake(messages):
        captured["messages"] = messages
        return json.dumps(GOOD_VERDICT)

    llm.set_complete_fn(fake)
    analyze_filing(nvda_entry, NVDA_8K_FILING, filing_text)

    system, user = captured["messages"]
    assert system["role"] == "system"
    assert "never say buy, sell" in system["content"]
    assert "moat — CUDA + software lock-in" in user["content"]
    assert "execution — Current management executes" in user["content"]
    assert "form: 8-K" in user["content"]
    assert "items: 5.02" in user["content"]
    assert "NVIDIA" in user["content"]  # real filing body made it in
    assert "STRICT JSON" in user["content"]


def test_clean_json_verdict_accepted(nvda_entry, filing_text):
    llm.set_complete_fn(lambda m: json.dumps(GOOD_VERDICT))
    verdict = analyze_filing(nvda_entry, NVDA_8K_FILING, filing_text)
    assert verdict == GOOD_VERDICT


def test_fenced_json_is_stripped(nvda_entry, filing_text):
    llm.set_complete_fn(lambda m: f"```json\n{json.dumps(GOOD_VERDICT)}\n```")
    verdict = analyze_filing(nvda_entry, NVDA_8K_FILING, filing_text)
    assert verdict == GOOD_VERDICT


@pytest.mark.parametrize(
    "bad_output",
    [
        "The filing looks routine to me, nothing to report.",  # prose
        '{"happened": "x", "severity": "minor"}',  # missing keys
        '{"happened": "x", "pillars_touched": [], "severity": "catastrophic", '
        '"confidence": "high", "watch_next": "", "one_line_for_sms": ""}',  # bad severity
        '{"happened": "x", "pillars_touched": "execution", "severity": "minor", '
        '"confidence": "high", "watch_next": "", "one_line_for_sms": ""}',  # touched not a list
        '{broken json',
        "",
        '["a", "list"]',
    ],
)
def test_malformed_output_returns_none_and_logs(
    bad_output, nvda_entry, filing_text, data_dir
):
    llm.set_complete_fn(lambda m: bad_output)
    verdict = analyze_filing(nvda_entry, NVDA_8K_FILING, filing_text)
    assert verdict is None
    log = data_dir / "llm_failures.log"
    assert log.exists()
    assert bad_output in log.read_text()


def test_malformed_output_never_alerts(nvda_submissions, nvda_watchlist, data_dir):
    """End-to-end through the poll loop: parse failure -> zero alerts."""
    import copy

    from sentinel.poll import run_poll

    llm.set_complete_fn(lambda m: "sorry, here's my analysis in plain english...")

    state = {"seen_accessions": {}, "sent_today": {}, "pending_add": None,
             "last_poll_utc": None, "day": None}
    alerts = []
    kwargs = dict(
        watchlist=nvda_watchlist,
        state=state,
        fetch_submissions=lambda cik: copy.deepcopy(nvda_submissions),
        fetch_filing_text=lambda cik, filing: "body",
        alert=lambda e, f, v: alerts.append(v),
        save=False,
    )
    run_poll(**kwargs)  # seed

    updated = copy.deepcopy(nvda_submissions)
    recent = updated["filings"]["recent"]
    for col, value in [
        ("accessionNumber", "0001045810-26-000777"),
        ("form", "8-K"),
        ("filingDate", "2026-07-11"),
        ("primaryDocument", "x.htm"),
        ("items", "2.02"),
    ]:
        recent[col].insert(0, value)
    for col in recent:
        if col not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items"):
            recent[col].insert(0, recent[col][0] if recent[col] else "")
    kwargs["fetch_submissions"] = lambda cik: copy.deepcopy(updated)

    summary = run_poll(**kwargs)
    assert summary["analyzed"] == 1
    assert summary["alerted"] == 0
    assert alerts == []
    # And the accession is still recorded — no retry storm on the next wake.
    assert "0001045810-26-000777" in state["seen_accessions"]["0001045810"]
