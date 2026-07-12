"""SEC EDGAR access: submissions feed, ticker->CIK map, filing bodies.

Deterministic plumbing only — no LLM anywhere in this module. Every request
carries the mandatory User-Agent header (403 + temp IP block without it) and
calls are spaced ~150ms to stay polite under the 10 req/s limit.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import config

_last_request_at = 0.0


class EdgarError(RuntimeError):
    pass


def _get(url: str, timeout: float = 30.0) -> bytes:
    if not config.SEC_USER_AGENT:
        raise EdgarError(
            "SEC_USER_AGENT is not set. SEC requires a 'Name contact@email' "
            "User-Agent on every request."
        )
    global _last_request_at
    wait = config.SEC_REQUEST_SPACING_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": config.SEC_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    finally:
        _last_request_at = time.monotonic()
    return body


def fetch_submissions(cik: str) -> dict:
    """GET https://data.sec.gov/submissions/CIK{10-digit}.json for one company."""
    cik10 = str(cik).lstrip("0").zfill(10) if str(cik).strip() else ""
    if not cik10.isdigit():
        raise EdgarError(f"Bad CIK: {cik!r}")
    return json.loads(_get(config.SUBMISSIONS_URL.format(cik10=cik10)))


def parse_recent_filings(submissions: dict) -> list[dict]:
    """Flatten filings.recent parallel arrays into a list of filing dicts.

    Returned dicts carry the fields the loop needs: accession_number, form,
    filing_date, primary_document, items. Feed order (newest first) is kept.
    """
    recent = (submissions.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    docs = recent.get("primaryDocument") or []
    items = recent.get("items") or []
    n = len(accessions)
    if not all(len(col) == n for col in (forms, dates, docs)):
        raise EdgarError("filings.recent parallel arrays have mismatched lengths")
    return [
        {
            "accession_number": accessions[i],
            "form": forms[i],
            "filing_date": dates[i],
            "primary_document": docs[i],
            "items": items[i] if i < len(items) else "",
        }
        for i in range(n)
    ]


def is_material(filing: dict) -> bool:
    return filing.get("form") in config.MATERIAL_FORMS


def archives_url(cik: str, accession_number: str, primary_document: str) -> str:
    """Build the filing-body URL: dashes stripped in the path, kept in the doc name."""
    return config.ARCHIVES_URL.format(
        cik_no_zeros=str(cik).lstrip("0"),
        accession_no_dashes=accession_number.replace("-", ""),
        primary_document=primary_document,
    )


def fetch_filing_text(cik: str, filing: dict, max_chars: int | None = None) -> str:
    """Fetch a filing's primary document and return it as truncated plain text."""
    url = archives_url(cik, filing["accession_number"], filing["primary_document"])
    raw = _get(url).decode("utf-8", errors="replace")
    return html_to_text(raw, max_chars or config.FILING_TEXT_MAX_CHARS)


# --- ticker -> CIK map -------------------------------------------------------


def load_ticker_map(cache_path: Path | None = None, fetch: bool = True) -> dict:
    """Return {TICKER: {"cik": zero-padded str, "company": name}}.

    Fetched once from SEC and cached in the data dir per the spec.
    """
    cache_path = cache_path or config.TICKER_MAP_PATH
    if cache_path.exists():
        with open(cache_path) as f:
            raw = json.load(f)
    elif fetch:
        raw = json.loads(_get(config.TICKER_MAP_URL))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(raw, f)
    else:
        return {}
    # SEC shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    out = {}
    for row in raw.values():
        out[row["ticker"].upper()] = {
            "cik": str(row["cik_str"]).zfill(10),
            "company": row["title"],
        }
    return out


def resolve_ticker(ticker: str, ticker_map: dict) -> dict | None:
    """Resolve a ticker to {"cik", "company"} or None if not an SEC filer."""
    return ticker_map.get(ticker.upper())


# --- HTML -> text ------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "head", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data)


def html_to_text(html: str, max_chars: int) -> str:
    """Strip tags to whitespace-normalized text, truncated to max_chars."""
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser._chunks)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text
