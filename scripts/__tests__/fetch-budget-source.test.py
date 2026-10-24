#!/usr/bin/env python3
"""--budget --source: the owner's gallery saves (#616) and the agents' fetches
spend separate buckets on the same host.

Before this, a gallery save wrote the same unlabelled line an agent fetch does,
so a busy afternoon of Pinterest saves would have used up the agents' 12 per 24h
on i.pinimg.com, and an agent sweep would have eaten into the owner's 100.

The last case writes the line with fetch-image.py's own log_fetch(), so the
writer's format and the reader's regex are checked against each other, not
against a copy of the format kept in this test.
"""
import datetime as dt
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(ROOT, "scripts", "fetch-budget.py")
FETCH_IMAGE = os.path.join(ROOT, "scripts", "fetch-image.py")

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def log_line(when, host, source=None):
    ts = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    label = f' source="{source}"' if source else ""
    return f'{ts} ALLOWED_IMAGE url="https://{host}/a.jpg" reason="test" agent_type="test"{label}\n'


def run_log(path, args):
    return subprocess.run([sys.executable, TOOL, "--log", path] + args,
                          capture_output=True, text=True)


def run(lines, args):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "egress-blocked.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        return run_log(p, args)


now = dt.datetime.now(dt.timezone.utc)
HOST = "i.pinimg.com"

# --- 1. gallery lines do not spend the agents' bucket -----------------------
lines = [log_line(now - dt.timedelta(minutes=m), HOST, "gallery") for m in range(1, 31)]
lines += [log_line(now - dt.timedelta(minutes=5), HOST)]
r = run(lines, ["--budget", HOST, "--cap", "12"])
check("30 galeria-mentes NEM fogy az ugynoki vodorbol", "1 letoltes" in r.stdout, r.stdout.strip()[-160:])
check("az ugynoki vodorbol meg MEHET 11", "MEHET meg 11" in r.stdout and r.returncode == 0, r.stdout.strip()[-160:])

# --- 2. and the other way round ---------------------------------------------
r = run(lines, ["--budget", HOST, "--cap", "100", "--source", "gallery"])
check("a galeria vodor csak a sajat 30 sorat latja", "30 letoltes" in r.stdout, r.stdout.strip()[-160:])
check("es megnevezi a vodret", "(gallery vodor)" in r.stdout, r.stdout.strip()[-160:])

# --- 3. NEGATIVE CONTROL: the gallery bucket can still say NO ---------------
r = run(lines, ["--budget", HOST, "--cap", "30", "--source", "gallery"])
check("a betelt galeria vodorre NEMET mond", "ELFOGYOTT" in r.stdout and r.returncode == 1,
      f"exit {r.returncode} {r.stdout.strip()[-160:]}")

# --- 4. a different label is a different bucket -----------------------------
r = run(lines, ["--budget", HOST, "--cap", "12", "--source", "masik"])
check("idegen cimke vodre ures", "0 letoltes" in r.stdout, r.stdout.strip()[-160:])

# --- 5. a malformed --source is refused, by both tools ----------------------
for bad in ("Gallery", "a b", 'x"y', ""):
    r = run([], ["--budget", HOST, "--source", bad])
    check(f"fetch-budget elutasitja: {bad!r}", r.returncode == 2 and "--source" in r.stderr, f"exit {r.returncode}")
r = subprocess.run([sys.executable, FETCH_IMAGE, "https://i.pinimg.com/a.jpg", "--source", 'x" y'],
                   capture_output=True, text=True)
check("fetch-image elutasitja a hibas --source-t letoltes elott", r.returncode == 2 and "--source" in r.stderr,
      f"exit {r.returncode} {r.stderr.strip()[-160:]}")

# --- 6. writer and reader agree: fetch-image's own log line is counted ------
spec = importlib.util.spec_from_file_location("fetch_image", FETCH_IMAGE)
fetch_image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_image)
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "egress-blocked.log")
    fetch_image.LOG = p
    fetch_image.log_fetch(f"https://{HOST}/736x/a.jpg", source="gallery")
    fetch_image.log_fetch(f"https://{HOST}/736x/b.jpg")
    r_gallery = run_log(p, ["--budget", HOST, "--source", "gallery"])
    r_agents = run_log(p, ["--budget", HOST])
check("a fetch-image galeria-sora a galeria vodorbe esik", "1 letoltes" in r_gallery.stdout, r_gallery.stdout.strip()[-160:])
check("a cimke nelkuli sora az ugynokibe", "1 letoltes" in r_agents.stdout, r_agents.stdout.strip()[-160:])

print()
print(f"{len(fails)} FAIL" if fails else "MIND ATMENT")
sys.exit(1 if fails else 0)
