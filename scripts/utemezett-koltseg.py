#!/usr/bin/env python3
"""Morning check: which scheduled task cost too much in the last 24 hours.

Why (owner, Telegram msg 8294, 2026-10-18): "valaki teged is leellenorizhetne
idonkent". The idle-card round (uresjarat-sajat-kartya, every 20 min) spent
169M tokens in its first ~20 hours and nobody noticed until the weekly window
was measured by hand. This makes that a number in the morning report instead
of something that depends on the main agent happening to look.

Reads `token-bontas.py feladat --napok 1` (every token of every call a task's
turn caused, cache reads included) and flags each task over the threshold.

Threshold: 20M tokens/day by default. Measured 2026-10-18: the weekly window
was 70% used at 2034M fleet tokens, so 1 percentage point of the week is
roughly 29M; 20M is about 0.7 point a day for ONE task.

Exit: 0 nothing over the line (stay silent), 1 something is over (print it in
the morning report), 2 the measurement failed (say so, do not stay silent).
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)M\s")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-m", type=int, default=20, help="napi plafon feladatonkent, millio token")
    a = ap.parse_args(argv)
    try:
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "token-bontas.py"), "feladat", "--napok", "1"],
            capture_output=True, text=True, timeout=600).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        print("utemezett-koltseg: a meres nem futott le: %s" % e)
        return 2
    rows = [(m.group(1), int(m.group(2)), int(m.group(4)))
            for m in (ROW.match(l) for l in out.splitlines()) if m]
    if not rows:
        print("utemezett-koltseg: a token-bontas kimenetebol egy sor sem olvashato")
        return 2
    total = sum(t for _, _, t in rows)
    over = [(n, runs, t) for n, runs, t in rows if t > a.limit_m]
    if not over:
        return 0
    print("Utemezett feladat %dM token/nap folott (24 ora, az osszes utemezett: %dM):" % (a.limit_m, total))
    for n, runs, t in over:
        print("- %s: %dM, %d kor" % (n, t, runs))
    return 1


if __name__ == "__main__":
    sys.exit(main())
