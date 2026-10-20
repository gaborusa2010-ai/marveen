#!/usr/bin/env python3
"""
Does the guard count one API response once, and does the exemption follow the counted day?

Claude Code writes one JSONL line per content block, each carrying the response's full
usage under the same message.id. Summed per line, the guard stopped margo and cody at
roughly half their cap (Anorak retro 2026-10-14). The exemption compared with the
calendar date, so it lapsed at midnight while the 06:00-anchored day was still running.

Nothing here touches a live agent.
"""
import datetime
import importlib.util
import json
import os
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "agent-budget-guard.py")
spec = importlib.util.spec_from_file_location("guard", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        fails.append(name)


def line(when, tokens, mid=None, rid=None):
    rec = {"timestamp": when.astimezone(datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%S.000Z"),
           "message": {"role": "assistant",
                       "usage": {"cache_read_input_tokens": tokens, "input_tokens": 0,
                                 "output_tokens": 0, "cache_creation_input_tokens": 0}}}
    if mid:
        rec["message"]["id"] = mid
    if rid:
        rec["requestId"] = rid
    return json.dumps(rec) + "\n"


def main():
    now = datetime.datetime.now()
    with tempfile.TemporaryDirectory() as td:
        mod.PROJECTS = td
        d = os.path.join(td, mod.project_slug(os.path.join(mod.REPO, "agents", "proba")))
        os.makedirs(d)
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for _ in range(6):                      # one response, six blocks
                fh.write(line(now, 1000, mid="msg_A", rid="req_A"))
            for _ in range(2):                      # requestId only
                fh.write(line(now, 100, rid="req_B"))
            for _ in range(2):                      # no id at all: counted per line
                fh.write(line(now, 10))
        os.utime(p, (time.time(), time.time()))

        print("1. Egy valasz egyszer szamit")
        total, _, _, _ = mod.window_tokens("proba", 24, 1.0)
        check("1000 + 100 + 2*10", total == 1120, f"kapott {total}")

        print("2. Ugyanaz a message.id ket fajlban is egyszer")
        p2 = os.path.join(d, "t.jsonl")
        with open(p2, "w", encoding="utf-8") as fh:
            fh.write(line(now, 1000, mid="msg_A"))
        total, _, _, _ = mod.window_tokens("proba", 24, 1.0)
        check("tovabbra is 1120", total == 1120, f"kapott {total}")

        print("3. A felmentes a SZAMOLT naphoz kotodik")
        mod.OVERRIDE = os.path.join(td, "ov.json")
        counted = datetime.datetime.fromtimestamp(mod.day_start(6)).strftime("%Y-%m-%d")
        with open(mod.OVERRIDE, "w") as fh:
            json.dump({"proba": counted}, fh)
        check("napi ablakban ervenyes", mod.override_today("proba", 6))
        cal = now.strftime("%Y-%m-%d")
        with open(mod.OVERRIDE, "w") as fh:
            json.dump({"proba": cal}, fh)
        check("gordulo ablakban a naptari nap szamit", mod.override_today("proba"))
        if now.hour < 6:
            check("00-06 kozott a naptari nap NEM a szamolt nap",
                  not mod.override_today("proba", 6))
        else:
            print("     (a 00-06 eset csak hajnalban konstrualhato, kihagyva)")

    print()
    print("Minden eset atment." if not fails else f"BUKOTT: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
