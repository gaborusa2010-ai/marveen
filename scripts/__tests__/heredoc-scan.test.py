#!/usr/bin/env python3
"""heredoc_scan.py against the shared case file (card #729).

The Python scanner (destructive-gate.py) and its JS twin
(scripts/hooks/lib/heredoc-scan.mjs, the egress and skill gates) must read every
command the same way. Both are tested against ONE file of expected results:
this test for Python, src/__tests__/heredoc-scan-parity.test.ts for JS. A change
to one scanner fails the other side's test until both agree again.

Run: python3 <this file>          exit 0 = green
     python3 <this file> --write  recompute the expected values from Python
                                  (after a deliberate change; then make the JS match)
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CASES = os.path.join(HERE, "heredoc-scan-cases.json")
FIELDS = [("start", "start"), ("tail_start", "tailStart"), ("line_end", "lineEnd"), ("tag", "tag"),
          ("quoted", "quoted"), ("dash", "dash"), ("body_start", "bodyStart"), ("body_end", "bodyEnd"),
          ("end", "end"), ("closed", "closed")]

spec = importlib.util.spec_from_file_location("heredoc_scan", os.path.join(ROOT, "scripts", "hooks", "heredoc_scan.py"))
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)


HS_FIELDS = [("start", "start"), ("tail_start", "tailStart"), ("line_end", "lineEnd"), ("text", "text"),
             ("dynamic", "dynamic")]


def scan(cmd):
    heredocs, certain = hs.find(cmd)
    # #777: the here-strings from find_all, separately; its `certain` is certainAll and may be
    # false where find()'s is true (an unreadable here-string word).
    records, certain_all = hs.find_all(cmd)
    return {"heredocs": [{js: getattr(h, py) for py, js in FIELDS} for h in heredocs], "certain": certain,
            "herestrings": [{js: getattr(h, py) for py, js in HS_FIELDS} for h in records if h.kind == "herestring"],
            "certainAll": certain_all}


def main():
    cases = json.load(open(CASES, encoding="utf-8"))
    if "--write" in sys.argv:
        for c in cases:
            c.update(scan(c["cmd"]))
        with open(CASES, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print("wrote", len(cases))
        return 0
    # offsets are code points here and UTF-16 units in JS: keep the file to the BMP
    assert all(ord(ch) <= 0xFFFF for c in cases for ch in c["cmd"]), "astral-plane character in a case"
    assert len(cases) >= 300, "case file shrank"
    bad = [c["cmd"] for c in cases if scan(c["cmd"]) != {"heredocs": c["heredocs"], "certain": c["certain"],
                                                         "herestrings": c["herestrings"], "certainAll": c["certainAll"]}]
    for cmd in bad[:5]:
        print("MISMATCH", repr(cmd))
    print("cases", len(cases), "mismatches", len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
