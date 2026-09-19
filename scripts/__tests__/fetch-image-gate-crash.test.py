#!/usr/bin/env python3
"""A crashed egress gate must NOT read as an approval (measured 2026-09-19).

The gate distinguishes 'allowed' from 'crashed' ONLY by its exit code: both write
nothing to stdout. fetch-image.py used to branch on stdout alone, so any gate
failure after spawn -- a missing module, a bad node, an unreadable file -- turned
the egress check off silently and the download went out.

Negative control included on purpose: a genuine allow (exit 0, empty stdout) must
still be allowed, otherwise the fix would just break every fetch.
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("fetch_image", os.path.join(ROOT, "fetch-image.py"))
fi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fi)

FAILS = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


class FakeRun:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def with_gate(rc, out, err=""):
    real = subprocess.run
    subprocess.run = lambda *a, **k: FakeRun(rc, out, err)
    try:
        return fi.gate_allows("https://barmi.example/kep.jpg")
    finally:
        subprocess.run = real


allowed, reason = with_gate(1, "", "Error [ERR_MODULE_NOT_FOUND]: Cannot find module 'lib/url-prefix.mjs'")
check("osszeomlo kapu (exit 1, ures stdout) -> NEM engedely", allowed is False)
check("  es az indok megnevezi a kapu hibajat", "kapu" in (reason or "").lower())

allowed, _ = with_gate(0, "")
check("negativ kontroll: valodi engedely (exit 0, ures stdout) -> ATMEGY", allowed is True)

allowed, reason = with_gate(0, '{"hookSpecificOutput":{"permissionDecisionReason":"TILTOTT"}}')
check("tiltas (exit 0, deny JSON) -> NEM engedely", allowed is False and reason == "TILTOTT")

allowed, _ = with_gate(-9, "")
check("jelre meghalt kapu (exit -9) -> NEM engedely", allowed is False)

print("  BUKOTT" if FAILS else "  MIND RENDBEN")
sys.exit(1 if FAILS else 0)
