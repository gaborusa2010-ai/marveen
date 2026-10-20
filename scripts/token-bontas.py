#!/usr/bin/env python3
"""
Where the fleet's tokens actually go, by what TRIGGERED the work.

The existing measurement (the `token-fogyasztas-merese` skill) answers "which model,
which day, which session". This answers a different question: which KIND OF INPUT set
the work in motion. On 2026-09-16 that turned out to be the question that mattered --
scheduled sentries were 12 percent of the traffic while agent-to-agent mail was 49.

WHY COVERAGE IS THE FIRST THING TO CHECK
----------------------------------------
The first version of this measurement classified only 238M tokens out of a measured
7958M and looked perfectly plausible: a sorted table, sensible names, percentages that
summed to 100 -- of the 238M. The percentages were of the wrong denominator. Every mode
here therefore prints its total against the fleet total, and a run whose total is far
below it has silently dropped work, not found less of it.

The trap was concrete: the first version treated an `[Inbox]` nudge as a new input,
which cut the chain of a scheduled task in half and left the rest unattributed.

MODES
-----
  token-bontas.py fajta     # by input type: peer mail, scheduled, owner, nudge...
  token-bontas.py peer      # peer mail split into receipts vs substantive, by sender
  token-bontas.py feladat   # per scheduled task (partial coverage BY DESIGN: only
                            # turns a task actually started)
  --napok N                 # window, default 7

A turn's cost is every token of every assistant call it caused: cache reads included,
because that is what the bill is made of (98.5 percent of it on the week measured).
"""
import argparse, glob, json, os, re, time
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The main agent writes under its own CLAUDE_CONFIG_DIR (.channels-config), not under
# ~/.claude. Without this root the "whole fleet" total silently missed its traffic:
# 46 percent of the week 09-16..09-23 (card 686774c2; same fix as MAIN_AGENT_PROJECTS
# in agent-budget-guard.py, BLINDSPOT901).
ROOTS = [os.path.expanduser("~/.claude/projects"),
         os.path.join(REPO, ".channels-config", "projects")] + [
    os.path.join(REPO, "agents", a, ".claude-config", "projects")
    for a in ("anorak", "cody", "forgacs", "margo", "roxy")]
SCHED = re.compile(r"scheduled-task:([a-z0-9-]+)")
SENDER = re.compile(r"Uzenet @([a-z-]+)-tol")


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def is_tool_result(c):
    return isinstance(c, list) and any(
        b.get("type") == "tool_result" for b in c if isinstance(b, dict))


def cost(u):
    return ((u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            + (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0))


def kind(t):
    if "scheduled-task:" in t:
        return "utemezett feladat"
    if t.startswith("[Inbox]"):
        return "Inbox-nudge"
    if "<trusted-peer" in t or "Uzenet @" in t:
        return "peer-uzenet"
    if "<channel source=" in t:
        return "gazda (csatorna)"
    if "<untrusted" in t:
        return "untrusted / ledger"
    if t.startswith("[CONTEXT") or "SYSTEM-DIREKTIVA" in t:
        return "rendszer-direktiva"
    if not t.strip():
        return "(ures)"
    return "egyeb / kezi"


def label(mode, t):
    """Returns the bucket for this input, or None to leave the previous one running."""
    if mode == "fajta":
        return kind(t)
    if mode == "peer":
        if "<trusted-peer" in t or "Uzenet @" in t:
            who = SENDER.search(t)
            recv = "[Eredmény]" in t or "[Eredmeny]" in t
            return f"{who.group(1) if who else '?':10} {'nyugta' if recv else 'erdemi'}"
        return None
    m = SCHED.search(t)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fajta", "peer", "feladat"])
    ap.add_argument("--napok", type=int, default=7)
    a = ap.parse_args()

    cut = time.time() - a.napok * 86400
    seen, agg, teljes = set(), defaultdict(lambda: {"tok": 0, "n": 0, "hiv": 0}), 0
    for root in ROOTS:
        for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            try:
                st = os.stat(p)
            except OSError:
                continue
            k = (st.st_dev, st.st_ino)
            if k in seen or st.st_mtime < cut:
                continue
            seen.add(k)
            cur = "(session eleje)" if a.mode == "fajta" else None
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    m = d.get("message")
                    if not isinstance(m, dict):
                        continue
                    if m.get("role") == "user":
                        c = m.get("content")
                        if is_tool_result(c):
                            continue          # not a new input, part of the running turn
                        new = label(a.mode, text_of(c))
                        if new is not None:
                            cur = new
                            agg[cur]["n"] += 1
                        elif a.mode != "fajta":
                            cur = None        # a different input ends the tracked one
                    elif m.get("role") == "assistant":
                        c = cost(m.get("usage") or {})
                        teljes += c
                        if cur:
                            agg[cur]["tok"] += c
                            agg[cur]["hiv"] += 1

    besorolt = sum(v["tok"] for v in agg.values())
    print(f"{'bucket':34} {'darab':>6} {'hivas':>7} {'token':>10} {'hivas/db':>9} {'%':>7}")
    for n, v in sorted(agg.items(), key=lambda kv: -kv[1]["tok"]):
        if not v["tok"]:
            continue
        print(f"{n[:34]:34} {v['n']:6d} {v['hiv']:7d} {v['tok']/1e6:9.0f}M "
              f"{v['hiv']/max(1, v['n']):9.1f} {100*v['tok']/besorolt:6.1f}%")
    print()
    print(f"fajlok: {len(seen)} | besorolt: {besorolt/1e6:.0f}M | a flotta teljes forgalma: {teljes/1e6:.0f}M")
    fed = 100 * besorolt / teljes if teljes else 0
    print(f"LEFEDETTSEG: {fed:.1f}%", end="")
    # Only "fajta" claims to cover everything, so only there is a low number a finding.
    # Flagging it in the other two modes would be an alarm that fires on every correct
    # run -- and an alarm that is always red is one nobody reads.
    RESZLEGES = {"feladat": "csak az utemezett korok",
                 "peer": "csak a peer-uzenetek"}
    if a.mode in RESZLEGES:
        print(f"  (ebben a modban a reszleges lefedettseg VARHATO: {RESZLEGES[a.mode]})")
    elif fed < 95:
        print("  <- GYANUS: a szazalekok egy szuk nevezore vonatkoznak, nezd meg mi esett ki")
    else:
        print()


main()
