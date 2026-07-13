#!/usr/bin/env bash
# Arm the agent for a demo take, or clean up after recording.
#
#   ./scripts/demo-reset.sh          arm a take: un-see the NVDA 8-K, zero
#                                    sent_today, drop NVDA floor to negligible
#   ./scripts/demo-reset.sh --done   after recording: restore floor to minor
#                                    and re-mark the 8-K as seen
#
# Runs against the deployed Maritime agent from your Mac. The staged filing
# is NVDA's real 2026-07-02 8-K (item 5.02, board retirement).

set -euo pipefail

AGENT="thesis-sentinel"
ACCESSION="0001045810-26-000060"
CIK="0001045810"

if [[ "${1:-}" == "--done" ]]; then
  maritime exec "$AGENT" -- sh -c "python3 -c \"
import json
w = json.load(open('/opt/data/watchlist.json'))
nvda = next(t for t in w['tickers'] if t['ticker'] == 'NVDA')
nvda['min_severity'] = 'minor'
json.dump(w, open('/opt/data/watchlist.json', 'w'), indent=1)
s = json.load(open('/opt/data/state.json'))
seen = s['seen_accessions']['$CIK']
if '$ACCESSION' not in seen:
    seen.append('$ACCESSION')
json.dump(s, open('/opt/data/state.json', 'w'), indent=1)
print('restored: NVDA floor=minor, $ACCESSION marked seen')\""
else
  maritime exec "$AGENT" -- sh -c "python3 -c \"
import json
w = json.load(open('/opt/data/watchlist.json'))
nvda = next(t for t in w['tickers'] if t['ticker'] == 'NVDA')
nvda['min_severity'] = 'negligible'
json.dump(w, open('/opt/data/watchlist.json', 'w'), indent=1)
s = json.load(open('/opt/data/state.json'))
seen = s['seen_accessions']['$CIK']
if '$ACCESSION' in seen:
    seen.remove('$ACCESSION')
s['sent_today'] = {}
json.dump(s, open('/opt/data/state.json', 'w'), indent=1)
print('armed: $ACCESSION un-seen, sent_today zeroed, NVDA floor=negligible')
print('next poll will rediscover the 8-K and email the analysis')\""
fi
