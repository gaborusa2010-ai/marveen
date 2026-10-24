#!/usr/bin/env python3
"""Pre-gather for the folyamatos-ellenorzes heartbeat, so a quiet round wakes no model (#648).

The gather (scripts/folyamatos-ellenorzes-kor.py) takes ~20 s, over the runner's 10 s
preCheck limit, and the cron loop awaits each preCheck in turn. So the slow part runs
from a systemd timer a few minutes BEFORE the task, and the preCheck only reads the
decision it left behind.

    folyamatos-ellenorzes-elozetes.py gyujt      # timer (:02): gather, decide, close quiet rounds
    folyamatos-ellenorzes-elozetes.py precheck   # task preCheck (:07): SKIP, or the report as prefix

A round is QUIET only when every line falls under a SKILL.md silence rule:
no MERES-HIBA, no new Yahoo mail, no new emoji hit, no Gmail mail other than a parcel
or auction notice (2c), no calendar event or due card that was not already put in front
of the model today, and no open 3c workday question. Anything else wakes the model with
the report, and the model judges it as before. Whatever goes wrong here (no decision
file, a stale one, an exception) leaves the preCheck silent, so the model runs the
round itself: fail-open, like the runner's own preCheck contract.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time

ROOT = "/home/gabor/marveen"
KOR = os.path.join(ROOT, "scripts/folyamatos-ellenorzes-kor.py")
DECISION = os.path.join(ROOT, "store/folyamatos-ellenorzes-elozetes.json")
SHOWN = os.path.join(ROOT, "store/folyamatos-ellenorzes-mutatva.json")
# The timer fires at :02, the task at :07. The answer is NOT consumed: a busy-session retry
# re-runs the preCheck, and an empty answer then would make the model gather again, which
# swallows the Yahoo mail this gather already marked seen. The next :02 replaces it.
MAX_AGE_S = 55 * 60

# SKILL.md 2c: parcel and auction notices go to seen, in silence.
CSEND_LEVEL = re.compile(
    r"royal ?mail|evri|inpost|\bdpd\b|parcel|has been delivered|out for delivery|"
    r"has shipped|order has shipped|jobalots|outbid|auction", re.I)


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load_shown():
    today = time.strftime("%Y-%m-%d")
    try:
        d = json.load(open(SHOWN))
        if d.get("date") == today:
            return d
    except Exception:
        pass
    return {"date": today, "lines": []}


def nyitas_csend(report):
    """3c: True when SKILL.md says stay silent on the workday question."""
    if "3c NYITAS: MA MAR KERDEZTUK" in report:
        return True
    now = datetime.datetime.now()
    if now.weekday() >= 5 or (now.hour, now.minute) < (9, 30):
        return True
    m = re.search(r"^3c NYITAS:.*\n((?:  .*\n?)*)", report, re.M)
    tail = m.group(1) if m else ""
    today = now.strftime("%Y-%m-%d")
    for line in tail.splitlines():
        if re.search(r"\bfut\b", line) or (re.search(r"\bkesz\b", line) and today in line):
            return True
    return False


def decide(report, rc, shown):
    """Return (quiet, reasons, fingerprints): fingerprints = EV/DUE lines shown on wake."""
    why = []
    if rc != 0 or "MERES-HIBA" in report or "HIBAK: nincs" not in report:
        why.append("meres-hiba")
    if not re.search(r"^2d YAHOO: nincs uj", report, re.M):
        why.append("yahoo")
    if not re.search(r"^3d EMOJI: nincs UJ", report, re.M):
        why.append("emoji")
    for line in re.findall(r"^  NEW .*$", report, re.M):
        if not CSEND_LEVEL.search(line):
            why.append("gmail")
            break
    fps = [l.strip() for l in re.findall(r"^  (?:EV|DUE) .*$", report, re.M)]
    if any(f not in shown["lines"] for f in fps):
        why.append("naptar/kanban")
    if not nyitas_csend(report):
        why.append("3c")
    return not why, why, fps


def gyujt():
    p = subprocess.run(["python3", KOR], cwd=ROOT, capture_output=True, text=True, timeout=240)
    report = (p.stdout + p.stderr).strip()
    quiet, why, fps = decide(report, p.returncode, load_shown())
    dec = {"at": int(time.time()), "quiet": quiet, "why": why, "fingerprints": fps,
           "report": report}
    if quiet:
        z = subprocess.run(["python3", KOR, "--zaras"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        dec["zaras"] = (z.stdout + z.stderr).strip()
        if z.returncode != 0:
            # The seen ids were not committed: let the model run the round itself.
            dec["quiet"], dec["why"] = False, ["zaras exit %d" % z.returncode]
            dec["report"] = ""
    atomic_write(DECISION, dec)
    print("%s | %s" % ("CSEND" if dec["quiet"] else "EBRESZT", ",".join(dec["why"]) or "-"))
    return 0


def precheck():
    try:
        dec = json.load(open(DECISION))
    except Exception:
        return 0
    if time.time() - dec.get("at", 0) > MAX_AGE_S:
        return 0
    if dec.get("quiet"):
        print("SKIP")
        return 0
    if not dec.get("report"):
        return 0
    shown = load_shown()
    shown["lines"] = sorted(set(shown["lines"]) | set(dec.get("fingerprints") or []))
    atomic_write(SHOWN, shown)
    print("[ELOZETES GYUJTES %s, ok: %s] A 0. lepes MAR LEFUTOTT, NE futtasd ujra (a Yahoo seen-fajl "
          "mar felvette az uj leveleket). Ez a riportja; itelj a szabalyok szerint, a kor vegen a "
          "--zaras ugyanugy kell.\n%s" % (
              time.strftime("%H:%M", time.localtime(dec["at"])), ",".join(dec.get("why") or []), dec["report"]))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "gyujt":
        sys.exit(gyujt())
    if mode == "precheck":
        try:
            sys.exit(precheck())
        except Exception:
            sys.exit(0)
    print(__doc__)
    sys.exit(2)
