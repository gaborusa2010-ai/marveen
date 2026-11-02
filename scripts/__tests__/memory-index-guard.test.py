#!/usr/bin/env python3
"""memory-index-guard.py (card #763, 5f372007): report mode never writes, the
archive's own index line is never a candidate, under target means no move.

The bug this pins: on 2026-11-02 the live dry run listed MEMORY-archivum.md among
the 11 lines to move (its frontmatter says type: reference). Applying that would
have removed the only pointer to the archive from the index: the lines would still
exist on disk and nothing would lead to them, which is the exact silent loss the
guard exists to prevent.
"""
import importlib.util, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "..", "memory-index-guard.py")
spec = importlib.util.spec_from_file_location("g", GUARD)
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

fails = 0
def check(name, cond, got=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f" -- got: {got!r}" if not cond and got != "" else ""))
    fails += 0 if cond else 1

def page(d, name, typ, desc="x", age_days=0):
    p = os.path.join(d, name)
    open(p, "w").write(f"---\nname: {name[:-3]}\ndescription: {desc}\nmetadata:\n  type: {typ}\n---\nbody\n")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))

def build(d, n_ref=0, n_user=0, pad=0):
    lines = ["# Index", "", "- [MEMORY-archivum: ritkan kello](MEMORY-archivum.md) — 54 lap"]
    page(d, "MEMORY-archivum.md", "reference")
    for i in range(n_ref):
        page(d, f"ref{i}.md", "reference", age_days=30)
        lines.append(f"- [ref{i}](ref{i}.md) — kulso mutato " + "x" * pad)
    for i in range(n_user):
        page(d, f"user{i}.md", "user")
        lines.append(f"- [user{i}](user{i}.md) — ki a gazda " + "x" * pad)
    open(os.path.join(d, "MEMORY.md"), "w").write("\n".join(lines) + "\n")

def run(d, *args):
    r = subprocess.run([sys.executable, GUARD, *args], capture_output=True, text=True,
                       env=dict(os.environ, MEMORY_INDEX_DIR=d))
    return r.returncode, r.stdout + r.stderr

# 1. under target: no move, exit 0
with tempfile.TemporaryDirectory() as d:
    build(d, n_ref=5)
    p = g.plan(d)
    check("under target -> no candidates moved", p["move"] == [] and p["lines"] < g.TARGET_LINES)
    rc, out = run(d)
    check("under target -> 'nincs teendo', rc 0", rc == 0 and "nincs teendo" in out, out)

# 2. over the LINE target: archive pointer never among the candidates; report mode writes nothing
with tempfile.TemporaryDirectory() as d:
    build(d, n_ref=g.TARGET_LINES + 20)
    before = open(os.path.join(d, "MEMORY.md")).read()
    p = g.plan(d)
    check("over target -> candidates exist", len(p["move"]) > 0, p["move"][:2])
    check("archive pointer MEMORY-archivum.md is never a candidate",
          all(not f.startswith("MEMORY") for _, f in p["move"]), [f for _, f in p["move"] if f.startswith("MEMORY")])
    check("plan brings the index under target", p["after_lines"] <= g.TARGET_LINES, p["after_lines"])
    rc, out = run(d)
    check("report mode exits 0 and names --apply", rc == 0 and "--apply" in out, out[-200:])
    check("report mode does not touch MEMORY.md", open(os.path.join(d, "MEMORY.md")).read() == before)
    check("report mode does not append to the archive", "automatikus archivalas" not in open(os.path.join(d, "MEMORY-archivum.md")).read())

# 3. --apply: moves the candidates, keeps the archive pointer, set check passes
with tempfile.TemporaryDirectory() as d:
    build(d, n_ref=g.TARGET_LINES + 20)
    rc, out = run(d, "--apply")
    idx = open(os.path.join(d, "MEMORY.md")).read()
    arc = open(os.path.join(d, "MEMORY-archivum.md")).read()
    check("--apply exits 0 with 'rendben'", rc == 0 and "rendben" in out, out[-200:])
    check("--apply keeps the archive pointer in the index", "(MEMORY-archivum.md)" in idx)
    check("--apply does not list the archive in itself", "(MEMORY-archivum.md)" not in arc)
    check("--apply brings the loaded size under target", g.loaded_size(os.path.join(d, "MEMORY.md"))[0] <= g.TARGET_LINES)

# 4. over the CHAR target with only user pages: nothing movable, exit 2
with tempfile.TemporaryDirectory() as d:
    build(d, n_user=30, pad=800)
    rc, out = run(d)
    check("only user/feedback over target -> rc 2, human decision", rc == 2 and "emberi dontest" in out, (rc, out[-150:]))

print(f"\n{'PASS' if not fails else 'FAIL'} memory-index-guard ({fails} failing)")
sys.exit(1 if fails else 0)
