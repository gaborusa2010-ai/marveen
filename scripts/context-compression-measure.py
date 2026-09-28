#!/usr/bin/env python3
"""Measure what a local, no-egress conversation compression would save.

Answers the owner's question (Telegram msg 3746, 2026-09-23): how big is the
catch if we drop OLD tool-call results and truncate the calls, leaving every
user/assistant word untouched?

Nothing leaves the machine. Reads only the local transcripts.

Denominator matters, so it is printed with every figure:
  - figures come from the message records' content blocks only
    (text / thinking / tool_use.input / tool_result.content). Harness
    bookkeeping records (attachment, system, file-history-snapshot, ...) are
    NOT part of the conversation body and are excluded.
  - THE UNIT IS ESTIMATED TOKENS, NOT BYTES, and that is not cosmetic. An
    image block is a half-megabyte of base64 but only ~1500 tokens, because
    the model bills an image by its dimensions. Measured 2026-09-23: 59 images
    were 80.7% of all tool_result BYTES and ~6.5% of its tokens. A byte-based
    answer to "what fills my context" is therefore wrong by an order of
    magnitude the moment any image is in play.
  - token figures come from the transcripts' own `usage` fields, so they are
    measured, not estimated from bytes.

Usage:  python3 scripts/context-compression-measure.py [--days 7] [--tail 0.20]
"""
import argparse, collections, datetime, glob, json, os, statistics

PROJECT_DIR = os.path.expanduser('~/.claude/projects/-home-gabor-marveen')


CHARS_PER_TOKEN = 3.5     # text; rough, and labelled as an estimate in the output
TOKENS_PER_IMAGE = 1500   # an image is billed by dimensions, not by base64 length


def blen(x):
    return len(json.dumps(x, ensure_ascii=False))


def tok_text(n_chars):
    return int(n_chars / CHARS_PER_TOKEN)


def tok_content(x):
    """Estimated tokens of a tool_result / tool_use payload.

    Image blocks are counted flat, never by their base64 length."""
    if isinstance(x, str):
        return tok_text(len(x))
    if isinstance(x, list):
        n = 0
        for b in x:
            if isinstance(b, dict) and b.get('type') == 'image':
                n += TOKENS_PER_IMAGE
            else:
                n += tok_text(blen(b))
        return n
    return tok_text(blen(x))


def blocks_of(path):
    """Yield (kind, bytes) for the conversation body of one transcript."""
    out = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get('message')
            if not isinstance(msg, dict):
                continue
            content = msg.get('content')
            if isinstance(content, str):
                out.append(('szoveg', tok_text(len(content))))
                continue
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get('type')
                if t == 'text':
                    out.append(('szoveg', tok_text(len(b.get('text') or ''))))
                elif t == 'thinking':
                    out.append(('szoveg', tok_text(len(b.get('thinking') or ''))))
                elif t == 'tool_use':
                    out.append(('tool_use', tok_content(b.get('input'))))
                elif t == 'tool_result':
                    out.append(('tool_result', tok_content(b.get('content'))))
    return out


def usage_of(path):
    """Yield the context size of each request, from the transcript's own usage."""
    sizes, agg = [], collections.Counter()
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get('message')
            u = msg.get('usage') if isinstance(msg, dict) else None
            if not isinstance(u, dict):
                continue
            for k, v in u.items():
                if isinstance(v, int):
                    agg[k] += v
            n = (u.get('cache_read_input_tokens', 0)
                 + u.get('cache_creation_input_tokens', 0)
                 + u.get('input_tokens', 0))
            if n > 0:
                sizes.append(n)
    return sizes, agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=7)
    ap.add_argument('--tail', type=float, default=0.20,
                    help='fraction of each session protected as "fresh"')
    ap.add_argument('--dir', default=PROJECT_DIR)
    args = ap.parse_args()

    cut = datetime.datetime.now().timestamp() - args.days * 86400
    # RECURSIVE on purpose: a project dir keeps per-session subdirectories with
    # sidechain transcripts, and a non-recursive glob silently drops them.
    # Measured 2026-09-23: 55 files at the top level, 89 with the subdirs.
    files = [f for f in glob.glob(os.path.join(args.dir, '**', '*.jsonl'),
                                  recursive=True)
             if os.path.getmtime(f) >= cut]

    body = collections.Counter()
    kept = collections.Counter()
    tokens = collections.Counter()
    firsts, per_req = [], []

    for f in files:
        bl = blocks_of(f)
        n = len(bl)
        tail = int(n * args.tail)
        for i, (k, v) in enumerate(bl):
            body[k] += v
            if k == 'szoveg' or i >= n - tail:
                kept[k] += v
            elif k == 'tool_use':
                kept[k] += min(v, 60)    # the call is truncated to ~60 tokens, not dropped
            # an old tool_result is dropped entirely
        sizes, agg = usage_of(f)
        tokens.update(agg)
        if len(sizes) >= 3:
            firsts.append(sizes[0])
            per_req.extend(sizes)

    T, K = sum(body.values()), sum(kept.values())
    print('NEVEZO: %s, utolso %g nap, %d fajl' % (args.dir, args.days, len(files)))
    if not T:
        print('Nincs adat.')
        return
    print()
    print('BESZELGETES-TORZS (becsult token): %d' % T)
    for k in ('szoveg', 'tool_use', 'tool_result'):
        print('  %-12s %12d  %5.1f%%' % (k, body[k], 100 * body[k] / T))
    print('  -> tool_use + tool_result egyutt: %.1f%%'
          % (100 * (body['tool_use'] + body['tool_result']) / T))
    print()
    print('TOMORITVE (vedett farok %.0f%%): %d token' % (args.tail * 100, K))
    print('MEGTAKARITAS a torzson: %d token = %.1f%%' % (T - K, 100 * (T - K) / T))

    if per_req:
        pre = statistics.median(firsts)
        med = statistics.median(per_req)
        print()
        print('VALODI TOKENEK a naplok usage mezoibol:')
        for k, v in tokens.most_common():
            print('  %-34s %14d' % (k, v))
        print()
        print('FIX ELoRESZ (elso keres kontextusa), median: %d token  [n=%d session]'
              % (pre, len(firsts)))
        print('MEDIAN KERES kontextusa:                     %d token  [n=%d keres]'
              % (med, len(per_req)))
        if med > pre:
            share = (med - pre) / med
            print('A torzs a median keres %.0f%%-a.' % (100 * share))
            print('A megtakaritas EGY KERESRE vetitve: %.1f%%'
                  % (100 * share * (T - K) / T))
    print()
    print('MEGKOTES: a "vedett farok" egyetlen parameter, es a szamot O hajtja.')
    print('Futtasd tobb ertekkel (--tail 0.10 / 0.40 / 0.60): sav jon ki, nem egy szam.')


if __name__ == '__main__':
    main()
