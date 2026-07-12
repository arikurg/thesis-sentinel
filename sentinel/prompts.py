"""The two LLM prompts, verbatim from the spec (sections 6 and 7).

These are the only LLM touchpoints in the system. Both demand strict JSON
and share the parse-failure guard in llm.py.
"""

from __future__ import annotations

# --- section 6: thesis-impact ------------------------------------------------

THESIS_IMPACT_SYSTEM = """\
You are a filings analyst. You assess how one SEC filing bears on a specific,
pre-stated investment thesis. You reason from the filing text, not from prior
beliefs about the company. You never recommend a trade and never say buy, sell,
or hold. Most filings are routine and move no pillar; say so plainly when true."""

THESIS_IMPACT_USER = """\
Ticker: {ticker} ({company})
Thesis pillars:
{pillars_block}

Filing:
  form: {form}
  date: {filing_date}
  items: {items}
  body (may be truncated):
  \"\"\"{filing_text}\"\"\"

TASK:
1. happened: one factual sentence grounded in the filing text. Quote at most one
   short phrase from the filing.
2. pillars_touched: which pillar ids this event bears on, if any. If none, return
   an empty list and set severity to "negligible".
3. For each touched pillar give:
   - mechanism: how this event could move that pillar
   - bull: the constructive read of the same event
   - bear: the adverse read of the same event
4. severity: one of negligible | minor | material | thesis_threatening.
   Justify from specifics: magnitude, permanence, isolated event vs pattern.
5. confidence: low | med | high.
6. watch_next: the single fact that would most change this read.
7. one_line_for_sms: <=140 chars, plain text, no ticker prefix.

Return STRICT JSON only, no prose, no markdown:
{{
  "happened": "",
  "pillars_touched": [{{"id":"","mechanism":"","bull":"","bear":""}}],
  "severity": "",
  "confidence": "",
  "watch_next": "",
  "one_line_for_sms": ""
}}

RULES:
- Never invent facts absent from the filing text.
- Routine or immaterial filing: pillars_touched = [], severity = "negligible".
- Do not output buy/sell/hold or a price target."""


def format_pillars_block(pillars: list[dict]) -> str:
    return "\n".join(
        f"- {p['id']} — {p['claim']} — breaks_if: {p['breaks_if']}" for p in pillars
    )


def thesis_impact_messages(entry: dict, filing: dict, filing_text: str) -> list[dict]:
    user = THESIS_IMPACT_USER.format(
        ticker=entry["ticker"],
        company=entry.get("company", entry["ticker"]),
        pillars_block=format_pillars_block(entry.get("pillars", [])),
        form=filing["form"],
        filing_date=filing["filing_date"],
        items=filing.get("items", ""),
        filing_text=filing_text,
    )
    return [
        {"role": "system", "content": THESIS_IMPACT_SYSTEM},
        {"role": "user", "content": user},
    ]


# --- section 7: pillar drafting ------------------------------------------------

PILLAR_DRAFT_SYSTEM = """\
You draft a starter investment thesis for one company as structured pillars,
for a filings-monitoring agent to reason against later. You are not recommending
the stock. You produce the load-bearing claims a holder of this company would
track, phrased so a single SEC filing could plausibly confirm or threaten each."""

PILLAR_DRAFT_USER = """\
Company: {company} ({ticker})
{edit_block}
TASK:
Produce 3 to 4 pillars. Each pillar:
- id: short lowercase slug (e.g. "moat", "demand", "execution")
- claim: the specific bull-case assumption, one sentence
- breaks_if: the concrete event or filing that would undercut it

Cover the distinct load-bearing assumptions for THIS company. Do not pad to four
if three carry the thesis. Make breaks_if specific enough to match against a
filing (name the kind of event: guidance cut, exec departure, customer loss,
covenant breach), not vague like "bad news".

Return STRICT JSON only, no prose, no markdown:
{{"pillars":[{{"id":"","claim":"","breaks_if":""}}]}}"""


def pillar_draft_messages(
    ticker: str,
    company: str,
    prior_pillars: list[dict] | None = None,
    user_edit_text: str | None = None,
) -> list[dict]:
    edit_block = ""
    if prior_pillars and user_edit_text:
        import json

        edit_block = (
            f"Prior draft: {json.dumps(prior_pillars)}. "
            f"Apply these changes: {user_edit_text}\n"
        )
    user = PILLAR_DRAFT_USER.format(company=company, ticker=ticker, edit_block=edit_block)
    return [
        {"role": "system", "content": PILLAR_DRAFT_SYSTEM},
        {"role": "user", "content": user},
    ]
