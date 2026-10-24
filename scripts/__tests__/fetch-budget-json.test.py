#!/usr/bin/env python3
"""--budget --json: the machine-readable line the dashboard's gallery counter
reads (#616). It must carry the same count as the text mode, the moment the
next slot frees up, and keep the selftest skip visible instead of dropping it.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.normpath(os.path.join(HERE, "..", "fetch-budget.py"))
fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def stamp(when):
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def line(when, verdict="ALLOWED_IMAGE", source="gallery"):
    label = f' source="{source}"' if source else ""
    return f'{stamp(when)} {verdict} url="https://i.pinimg.com/a.jpg" reason="test" agent_type="test"{label}\n'


def run(lines, args):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "egress-blocked.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        return subprocess.run([sys.executable, TOOL, "--log", p] + args, capture_output=True, text=True)


now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
ARGS = ["--budget", "i.pinimg.com", "--source", "gallery", "--json"]

oldest = now - dt.timedelta(hours=20)
lines = [line(oldest), line(now - dt.timedelta(hours=2)), line(now - dt.timedelta(minutes=1))]
lines += [line(now - dt.timedelta(hours=30))]                      # outside the window
lines += [line(now - dt.timedelta(minutes=3), source=None)]        # agents' bucket
lines += [line(now - dt.timedelta(minutes=4), verdict="SELFTEST_ALLOWED_IMAGE")]
r = run(lines, ARGS + ["--cap", "5"])
try:
    out = json.loads(r.stdout)
except ValueError:
    out = {}
check("stdout egyetlen JSON sor", r.stdout.count("\n") == 1 and bool(out), r.stdout[:200])
check("used = 3 (ablakon kivuli, ugynoki es selftest sor nelkul)", out.get("used") == 3, str(out))
check("left = 2, exit 0", out.get("left") == 2 and r.returncode == 0, f"{out} exit {r.returncode}")
check("next_free_at = legregebbi + 24 ora",
      out.get("next_free_at") == (oldest + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"), str(out))
check("selftest_skipped atjon", out.get("selftest_skipped") == 1, str(out))

r = run(lines, ARGS + ["--cap", "3"])
out = json.loads(r.stdout or "{}")
check("elfogyott: left 0, exit 1", out.get("left") == 0 and r.returncode == 1, f"{r.stdout} exit {r.returncode}")

r = run(lines, ARGS + ["--cap", "2"])
out = json.loads(r.stdout or "{}")
check("tullepve: left nem negativ", out.get("left") == 0 and out.get("used") == 3, r.stdout)

r = run([], ARGS + ["--cap", "200"])
out = json.loads(r.stdout or "{}")
check("ures naplo: 0 used, next_free_at null", out.get("used") == 0 and out.get("next_free_at") is None, r.stdout)

r = subprocess.run([sys.executable, TOOL, "--log", "/nonexistent/x.log"] + ARGS, capture_output=True, text=True)
check("hianyzo naplo: nem omlik ossze", r.returncode == 0 and json.loads(r.stdout or "{}").get("used") == 0, r.stdout + r.stderr)

r = subprocess.run([sys.executable, TOOL, "--json"], capture_output=True, text=True)
check("--json --budget nelkul: hasznalati hiba", r.returncode == 2, r.stderr)

print(f"\n{len(fails)} FAIL")
sys.exit(1 if fails else 0)
