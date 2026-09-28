#!/usr/bin/env python3
"""--budget: does the counter survive local midnight, and can it say NO?

The bug this guards against was live on 2026-09-11 at 00:01 BST: the log stores
UTC instants, `dt.date.today()` returns a LOCAL date, and under BST the two
disagree for one hour every night. Three fetches made 16-33 minutes earlier
counted as ZERO, and the tool answered "MEHET meg 12" on a host that had already
been visited three times.

Every case runs against a synthetic log, so the test never touches the network
and never reads the fleet's real traffic.
"""
import datetime as dt
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(ROOT, "scripts", "fetch-budget.py")

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def log_line(when, host):
    ts = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return f'{ts} ALLOWED_QUARANTINE url="https://{host}/a" reason="test" agent_type="test"\n'


def run(lines, args, env_tz=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "egress-blocked.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        env = dict(os.environ, MARVEEN_EGRESS_LOG=p)
        if env_tz:
            env["TZ"] = env_tz
        r = subprocess.run([sys.executable, TOOL, "--log", p] + args,
                           capture_output=True, text=True, env=env)
        return r


now = dt.datetime.now(dt.timezone.utc)

# --- 1. the bug itself: three fetches a few hours ago must still count --------
lines = [log_line(now - dt.timedelta(hours=h), "example.org") for h in (0.6, 0.4, 0.3)]
r = run(lines, ["--budget", "example.org", "--cap", "12"])
check("a par oraval ezelotti lekeresek SZAMITANAK (a naptari nap nem szamit)",
      "3 letoltes" in r.stdout, r.stdout.strip()[-160:])
check("es a maradek helyes", "MEHET meg 9" in r.stdout, r.stdout.strip()[-160:])

# --- 2. NEGATIVE CONTROL: can it say NO at all? ------------------------------
lines = [log_line(now - dt.timedelta(minutes=m), "example.org") for m in range(1, 13)]
r = run(lines, ["--budget", "example.org", "--cap", "12"])
check("a betelt keretre NEMET mond", "ELFOGYOTT" in r.stdout, r.stdout.strip()[-160:])
check("es 1-gyel lep ki", r.returncode == 1, f"exit {r.returncode}")

# --- 3. the window really rolls: 25 hours old must NOT count -----------------
lines = [log_line(now - dt.timedelta(hours=25), "example.org")]
r = run(lines, ["--budget", "example.org", "--cap", "12"])
check("a 25 oras sor mar KIESIK az ablakbol", "0 letoltes" in r.stdout, r.stdout.strip()[-160:])

# --- 4. a foreign host must not be counted against ours ---------------------
lines = [log_line(now - dt.timedelta(hours=1), "masik.example") for _ in range(5)]
r = run(lines, ["--budget", "example.org", "--cap", "12"])
check("masik host forgalma NEM szamit bele", "0 letoltes" in r.stdout, r.stdout.strip()[-160:])

print()
print(f"{len(fails)} FAIL" if fails else "MIND ATMENT")
sys.exit(1 if fails else 0)
