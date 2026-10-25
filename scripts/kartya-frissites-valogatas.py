#!/usr/bin/env python3
"""Pick the open kanban cards whose description must be refreshed tonight.

Owner tg 9285/9287 (2026-10-25): a card's DESCRIPTION has to tell the current truth,
a comment underneath is not enough, and this must be done every evening before the
21:00 close (esti-kanban-atnezes).

A card is picked when, since the last refresh (store/kartya-frissites-utolso.json, written
with --mark after the helpers finished; fallback today 00:00 Europe/London; --since overrides):
  - it got a non-automated comment, or
  - its status changed (last_status_at), or
  - it was created, or
  - today's docs/GAZDA-DONTESEK.md lines mention its #seq or hex id.

Output: JSON batches written to --out (batch1.json ...), counts on stdout.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

ROOT = '/home/gabor/marveen'
BASE = 'http://localhost:3420'
TZ = ZoneInfo('Europe/London')
STATE = f'{ROOT}/store/kartya-frissites-utolso.json'


def get(path):
    token = open(f'{ROOT}/store/.dashboard-token').read().strip()
    req = urllib.request.Request(BASE + path, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='YYYY-MM-DD (local), default today')
    ap.add_argument('--out', help='directory for batchN.json')
    ap.add_argument('--mark', action='store_true', help='record now as the last refresh and exit')
    ap.add_argument('--batch-size', type=int, default=25)
    a = ap.parse_args()

    if a.mark:
        with open(STATE, 'w') as f:
            json.dump({'last_refresh': int(dt.datetime.now(TZ).timestamp())}, f)
        print(f'marked {STATE}')
        return
    if not a.out:
        ap.error('--out is required')

    today = dt.datetime.now(TZ).date()
    if a.since:
        since = int(dt.datetime.combine(dt.date.fromisoformat(a.since), dt.time(), TZ).timestamp())
    else:
        since = int(dt.datetime.combine(today, dt.time(), TZ).timestamp())
        try:
            since = max(since, int(json.load(open(STATE))['last_refresh']))
        except (OSError, ValueError, KeyError):
            pass
    stamp = today.isoformat()

    decisions = ''
    try:
        with open(f'{ROOT}/docs/GAZDA-DONTESEK.md', encoding='utf-8') as f:
            decisions = '\n'.join(l for l in f if l.startswith(f'- {stamp}'))
    except OSError as e:
        print(f'WARN: decision log unreadable: {e}', file=sys.stderr)

    try:
        cards = [c for c in get('/api/kanban') if c.get('status') != 'done']
    except Exception as e:  # network / dashboard down: say it loudly, pick nothing
        print(f'FAIL: kanban list unreadable: {e}', file=sys.stderr)
        sys.exit(2)

    picked, reasons = [], {}
    for c in cards:
        why = []
        if (c.get('created_at') or 0) >= since:
            why.append('new')
        if (c.get('last_status_at') or 0) >= since:
            why.append('status')
        if re.search(rf'#{c["seq"]}\b|\b{re.escape(c["id"][:8])}\b', decisions):
            why.append('decision')
        try:
            comments = get(f'/api/kanban/{c["id"]}/comments')
        except Exception as e:
            why.append(f'comments-unreadable:{e.__class__.__name__}')
            comments = []
        if any((m.get('created_at') or 0) >= since and not m.get('automated') for m in comments):
            why.append('comment')
        if why:
            picked.append(c)
            reasons[c['seq']] = why

    os.makedirs(a.out, exist_ok=True)
    n = 0
    for i in range(0, len(picked), a.batch_size):
        n += 1
        batch = [{k: c.get(k) for k in ('seq', 'id', 'title', 'status', 'assignee')} for c in picked[i:i + a.batch_size]]
        with open(os.path.join(a.out, f'batch{n}.json'), 'w', encoding='utf-8') as f:
            json.dump(batch, f, ensure_ascii=False, indent=0)
    since_txt = dt.datetime.fromtimestamp(since, TZ).strftime('%Y-%m-%d %H:%M')
    print(f'open={len(cards)} picked={len(picked)} batches={n} since={since_txt}')
    for seq in sorted(reasons):
        print(f'  #{seq}: {",".join(reasons[seq])}')


if __name__ == '__main__':
    main()
