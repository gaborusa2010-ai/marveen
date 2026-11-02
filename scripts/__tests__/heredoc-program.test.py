#!/usr/bin/env python3
"""The heredoc-program twin, Python side (card #748): does the shell RUN a heredoc body?

`_heredoc_runs_body` in scripts/hooks/destructive-gate.py and `heredocRunsBody` in
scripts/hooks/lib/heredoc-program.mjs (child-claude guard, bash egress gate) must give
the same answer for every command. Both are checked against ONE case file,
scripts/__tests__/heredoc-program-cases.json: this test for Python,
src/__tests__/heredoc-program-parity.test.ts for JS. The file's `expect` is written by
hand (the intent), and `bash_runs` records what bash itself did when the file was
built, so a wrong expectation is visible in the file, not hidden by a regeneration.

Run: python3 <this file>    exit 0 = green
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CASES = os.path.join(HERE, "heredoc-program-cases.json")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = load("dgate", os.path.join(ROOT, "scripts", "hooks", "destructive-gate.py"))
scan = load("heredoc_scan", os.path.join(ROOT, "scripts", "hooks", "heredoc_scan.py"))

PAYLOAD = "echo PWNED_MARK"


def main():
    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    failed = []
    for c in cases:
        cmd = c["cmd"].replace("X", PAYLOAD)
        heredocs, _certain = scan.find(cmd)
        got = [bool(gate._heredoc_runs_body(cmd, h)) for h in heredocs]
        if got != c["expect"]:
            failed.append("%s: expected %s, got %s" % (c["name"], c["expect"], got))
        # The file's own consistency: where bash was asked, it must agree with the intent.
        if c.get("bash_runs") is not None and c["bash_runs"] != any(c["expect"]):
            failed.append("%s: expect %s disagrees with bash_runs=%s" % (c["name"], c["expect"], c["bash_runs"]))
    must_run = sum(1 for c in cases if any(c["expect"]))
    print("heredoc-program cases: %d (%d must-run, %d data), failures: %d"
          % (len(cases), must_run, len(cases) - must_run, len(failed)))
    for f in failed:
        print("  FAIL", f)
    if len(cases) < 80 or must_run < 40:
        print("  FAIL case file too small: %d cases / %d must-run" % (len(cases), must_run))
        failed.append("size")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
