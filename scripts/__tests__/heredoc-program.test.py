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
C_CASES = os.path.join(HERE, "shell-c-program-cases.json")
O_CASES = os.path.join(HERE, "shell-out-cases.json")


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
        records, _certain = scan.find_all(cmd)          # heredocs and here-strings (#777)
        got = [bool(gate._heredoc_runs_body(cmd, h)) for h in records]
        if got != c["expect"]:
            failed.append("%s: expected %s, got %s" % (c["name"], c["expect"], got))
        # The file's own consistency: where bash was asked, it must agree with the intent.
        # bash_note: the disagreement is explained in the file (oracle artefact, accepted over-block, known gap)
        if c.get("bash_runs") is not None and not c.get("bash_note") and c["bash_runs"] != any(c["expect"]):
            failed.append("%s: expect %s disagrees with bash_runs=%s" % (c["name"], c["expect"], c["bash_runs"]))
    # #777: the literal `sh -c PROGRAM` words, the same file pins the JS shellCPrograms
    c_cases = json.load(open(C_CASES, encoding="utf-8"))["cases"]
    for c in c_cases:
        got = gate.shell_c_programs(c["cmd"])
        if got != c["programs"]:
            failed.append("-c program %r: expected %s, got %s" % (c["cmd"], c["programs"], got))
    if len(c_cases) < 30 or sum(1 for c in c_cases if c["programs"]) < 20:
        failed.append("shell-c-program case file too small: %d" % len(c_cases))
    print("shell -c program cases: %d" % len(c_cases))
    # #779: shell commands handed to a shell by a code payload; the same file pins JS embeddedCommands
    o_cases = json.load(open(O_CASES, encoding="utf-8"))["cases"]
    for c in o_cases:
        got = gate.embedded_commands(c["cmd"])
        if got != c["commands"]:
            failed.append("shell-out %s: expected %s, got %s" % (c["name"], c["commands"], got))
    neg = [c for c in o_cases if c["name"].startswith("neg/")]
    if len(o_cases) < 300 or sum(1 for c in o_cases if c["commands"]) < 250 or len(neg) < 20:
        failed.append("shell-out case file too small: %d" % len(o_cases))
    # a benign negative may hand a harmless command (ls) to a shell, never the blocked words
    for c in neg:
        if any(w in " ".join(c["commands"]) for w in ("claude", "curl", "rm -rf")):
            failed.append("shell-out negative %s reads a blocked command: %s" % (c["name"], c["commands"]))
    if not any(c.get("note", "").startswith("known limit") for c in o_cases):
        failed.append("shell-out case file has no known-limit entry")
    print("shell-out cases: %d (%d negatives, %d known limits)"
          % (len(o_cases), len(neg), sum(1 for c in o_cases if c.get("note"))))
    must_run = sum(1 for c in cases if any(c["expect"]))
    noted = sum(1 for c in cases if c.get("bash_note"))
    print("heredoc-program cases: %d (%d must-run, %d data, %d with a bash_note), failures: %d"
          % (len(cases), must_run, len(cases) - must_run, noted, len(failed)))
    for f in failed:
        print("  FAIL", f)
    if len(cases) < 400 or must_run < 200:
        print("  FAIL case file too small: %d cases / %d must-run" % (len(cases), must_run))
        failed.append("size")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
