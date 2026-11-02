#!/usr/bin/env python3
"""Card #664: the decision-log lookup (scripts/dontes-kereso.py) and its gate
(scripts/hooks/dontes-lookup-gate.py).

Branches proven, on disposable fixtures (no prod store is read or written):
  lookup:
    - empty, huge and too-many-term input exit 2 and record nothing;
    - accent- and case-insensitive both ways ("írói" finds "IROI", "iroi" finds "írói");
    - AND by default, --any for OR;
    - a hit prints date, message_id, "Mire vonatkozik", the quote and file:line;
    - closed topics (by text and by the topic's own regex), skill grants and the
      grants doc are searched;
    - a missing decision file or a broken JSON source warns and the run goes on;
    - NINCS TALALAT exits 1 and is still recorded.
  gate:
    - broken / empty / non-dict payload, missing transcript: fail-open (exit 0);
    - a question with no lookup BLOCKS; the same payload after a lookup PASSES
      (the negative control: the gate says both no and yes on one input);
    - a lookup BEFORE the owner's last message (also a mid-turn queued one) BLOCKS;
    - a non-question, a '?' only inside a URL or code, and tg-valasztas --valasz PASS;
    - a tg-valasztas question blocks without a lookup;
    - a run recorded under another agent name but this session id PASSES.

Run: python3 <thisfile>   Exit 0 = all pass.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
KERESO = os.path.join(SCRIPTS, "dontes-kereso.py")
GATE = os.path.join(SCRIPTS, "hooks", "dontes-lookup-gate.py")

failed = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failed.append(name)


DECISIONS = """# GAZDA-DONTESEK

## Az alak

```
### <rovid cim>
- **Mire vonatkozik:** template, never a hit
```

---

# DONTESEK, legfrissebb elol

### KDP írói név: Gabriel S. Fields
- **Mikor:** 2026-10-04 11:19 | **Hol:** Telegram msg 5740
- **Amit mondott:**
  > Gabriel S. Fields
- **Mire vonatkozik:** a KDP konyvek szerzoi neve.

## 2026-10-22 09:21 BST, Telegram msg 8819 -- RUST FORDITO LETOLTESE
Szo szerint: "Igen, mehet"
Mire vonatkozik: a static.rust-lang.org felvetele a letoltesi listara.
Beirta: Marveen.

- 2026-10-25 09:29 GMT, Telegram msg 9183. Szo szerint: "30 perc". Mire: a leallito program csend-ideje. DONTES: 30 perc utan leall. Beirta: Marveen.

### IROI NEV: elobb a gazda latja a logot
- **Mikor:** 2026-10-04 11:21 | **Hol:** Telegram msg 5744
- **Mire vonatkozik:** a logo.
"""

TOPICS = {"_doc": "x", "ugyek": [
    {"id": "kdp-jogi-ut", "pattern": "small\\s+claims|arbitrat", "decision": "tg 8809, 2026-10-22: 'Felejtsd el...'",
     "what": "KDP account: legal route"},
    {"id": "bad-regex", "pattern": "(", "decision": "x", "what": "broken pattern must not crash"},
]}
GRANTS = {"_doc": "x", "grants": [
    {"agent": "forgacs", "skills": ["munkanaplo"], "reason": "r", "approved_at": "2026-09-01",
     "source": "Telegram: \"Forgacsnak mehet az engedely a skill-ekhez\""}]}
GRANTS_DOC = """# Vedett fa

### ES EGY NEGYEDIK: COMMIT + PUSH
A gazda dontese 2026-10-14, Telegram msg 7801, szo szerint: *"Mindez + GitHub feltöltés"*.

Masik bekezdes, semmi kozos.
"""


def fixture(td, decisions=True, topics_raw=None):
    os.makedirs(os.path.join(td, "docs", "tudas"), exist_ok=True)
    os.makedirs(os.path.join(td, "store"), exist_ok=True)
    if decisions:
        open(os.path.join(td, "docs", "GAZDA-DONTESEK.md"), "w", encoding="utf-8").write(DECISIONS)
    open(os.path.join(td, "store", "lezart-ugyek.json"), "w", encoding="utf-8").write(
        topics_raw if topics_raw is not None else json.dumps(TOPICS, indent=1))
    open(os.path.join(td, "store", "skill-write-grants.json"), "w", encoding="utf-8").write(json.dumps(GRANTS, indent=1))
    open(os.path.join(td, "docs", "tudas", "vedett-fa-es-engedelyek.md"), "w", encoding="utf-8").write(GRANTS_DOC)


def env_for(td, agent="marveen", session="sess-A"):
    env = {k: v for k, v in os.environ.items() if k not in (
        "MARVEEN_AGENT", "TELEGRAM_STATE_DIR", "DONTES_FILE", "CLOSED_TOPICS_FILE", "SKILL_GRANTS_FILE",
        "GRANTS_DOC_FILE")}
    env.update({"DONTES_ROOT": td, "DONTES_STATE_DIR": os.path.join(td, "state"),
                "DONTES_GATE_LOG": os.path.join(td, "gate.log"), "MARVEEN_AGENT": agent,
                "CLAUDE_CODE_SESSION_ID": session})
    return env


def lookup(td, *args, **kw):
    env = env_for(td, **kw)
    return subprocess.run([sys.executable, KERESO, *args], capture_output=True, text=True, env=env, cwd=td, timeout=60)


def runs(td, agent="marveen"):
    p = os.path.join(td, "state", agent + ".jsonl")
    return [json.loads(ln) for ln in open(p, encoding="utf-8")] if os.path.isfile(p) else []


def gate(td, stdin, **kw):
    return subprocess.run([sys.executable, GATE], input=stdin, capture_output=True, text=True,
                          env=env_for(td, **kw), cwd=td, timeout=60)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def transcript(td, owner_at, queued_at=None, name="t.jsonl"):
    recs = [
        {"type": "user", "timestamp": iso(owner_at - 30), "message": {"role": "user", "content": "plain prompt"}},
        {"type": "user", "timestamp": iso(owner_at), "message": {"role": "user", "content":
            '<channel source="plugin:telegram:telegram" chat_id="1" message_id="9" user="1" ts="x">\nmi a helyzet?\n</channel>'}},
        # a tool result that merely CONTAINS a channel tag (a grep of a transcript) is not an inbound message
        {"type": "user", "timestamp": iso(owner_at + 3600), "message": {"role": "user", "content": [
            {"type": "tool_result", "content": '<channel source="plugin:telegram:telegram">'}]}},
    ]
    if queued_at:
        recs.append({"type": "attachment", "timestamp": iso(queued_at), "attachment": {"type": "queued_command",
                     "prompt": '<channel source="plugin:telegram:telegram" chat_id="1" message_id="10">\nmeg valami\n</channel>'}})
    p = os.path.join(td, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("not json\n")
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return p


def reply(path, text, session="sess-A", tool="mcp__plugin_telegram_telegram__reply"):
    return json.dumps({"tool_name": tool, "tool_input": {"chat_id": "1", "text": text},
                       "transcript_path": path, "session_id": session, "cwd": "/home/x/marveen"})


def bash(path, cmd, session="sess-A"):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                       "transcript_path": path, "session_id": session, "cwd": "/home/x/marveen"})


def test_lookup():
    print("lookup (dontes-kereso.py)")
    with tempfile.TemporaryDirectory() as td:
        fixture(td)
        r = lookup(td, "")
        check("empty input -> exit 2", r.returncode == 2, r.stderr)
        r = lookup(td)
        check("no argument -> exit 2", r.returncode == 2, r.stderr)
        r = lookup(td, "x" * 5000)
        check("huge input (5000 chars) -> exit 2", r.returncode == 2, r.stderr)
        r = lookup(td, " ".join(f"szo{i}" for i in range(25)))
        check("25 terms -> exit 2", r.returncode == 2, r.stderr)
        r = lookup(td, "--max", "abc", "rust")
        check("bad --max -> exit 2", r.returncode == 2, r.stderr)
        check("bad input records nothing", runs(td) == [], str(runs(td)))

        r = lookup(td, "írói név")
        check("accented query finds ASCII entry (IROI NEV) and accented one", r.returncode == 0
              and "elobb a gazda latja" in r.stdout and "Gabriel S. Fields" in r.stdout, r.stdout)
        r = lookup(td, "IROI", "nev")
        check("ASCII query finds the accented entry (írói név)", r.returncode == 0 and "KDP írói név" in r.stdout, r.stdout)
        check("hit prints file:line, date, msg, Mire, quote",
              "docs/GAZDA-DONTESEK.md:14] 2026-10-04 11:19 | msg 5740 |" in r.stdout
              and "Mire vonatkozik: a KDP konyvek szerzoi neve." in r.stdout
              and "Szo szerint: Gabriel S. Fields" in r.stdout, r.stdout)
        check("template inside the code fence is not an entry", "template" not in r.stdout, r.stdout)

        r = lookup(td, "rust csend")
        check("AND: rust + csend in no single entry -> NINCS TALALAT, exit 1",
              r.returncode == 1 and "NINCS TALALAT" in r.stdout, r.stdout)
        r = lookup(td, "--any", "rust csend")
        check("--any: rust OR csend -> both entries", r.returncode == 0 and "RUST FORDITO" in r.stdout
              and "msg 9183" in r.stdout and "Mire vonatkozik: a leallito program csend-ideje." in r.stdout, r.stdout)
        check("'## date -- title' entry: quote and Mire parsed",
              'Szo szerint: "Igen, mehet"' in r.stdout and "static.rust-lang.org" in r.stdout, r.stdout)

        r = lookup(td, "small claims")
        check("closed topic found by its own regex", r.returncode == 0 and "LEZART UGY kdp-jogi-ut" in r.stdout
              and "lezart-ugyek.json:" in r.stdout and "msg 8809" in r.stdout, r.stdout)
        r = lookup(td, "forgacs munkanaplo")
        check("standing skill grant found", r.returncode == 0 and "ALLANDO SKILL-IRAS ENGEDELY: forgacs" in r.stdout, r.stdout)
        r = lookup(td, "github feltoltes")
        check("grants doc paragraph found with its heading and quote", r.returncode == 0
              and "COMMIT + PUSH" in r.stdout and "msg 7801" in r.stdout and "Mindez + GitHub" in r.stdout, r.stdout)

        rr = runs(td)
        check("every valid run recorded (7), with terms, hits, session", len(rr) == 7
              and rr[-1]["terms"] == ["github", "feltoltes"] and rr[-1]["hits"] == 1
              and rr[-1]["session"] == "sess-A" and rr[2]["hits"] == 0, json.dumps(rr[-1]))
        r = lookup(td, "rust", agent="cody")
        check("agent from MARVEEN_AGENT -> own state file", len(runs(td, "cody")) == 1, str(os.listdir(os.path.join(td, "state"))))

    with tempfile.TemporaryDirectory() as td:
        fixture(td, decisions=False, topics_raw="{ broken json")
        r = lookup(td, "forgacs")
        check("missing decision file + broken topics: warns, goes on, grants still searched",
              r.returncode == 0 and "hianyzo forras" in r.stderr and "olvashatatlan forras" in r.stderr
              and "forgacs" in r.stdout, r.stderr + r.stdout)
        check("the run with missing sources is recorded with them", len(runs(td)) == 1
              and len(runs(td)[0].get("missing", [])) == 2, str(runs(td)))
        r = lookup(td, "zzqx")
        check("missing decision file, no hit -> exit 1", r.returncode == 1, r.stdout + r.stderr)


def test_gate():
    print("gate (dontes-lookup-gate.py)")
    with tempfile.TemporaryDirectory() as td:
        fixture(td)
        now = time.time()
        t = transcript(td, owner_at=now - 60)
        q = reply(t, "Melyiket kered, az A-t vagy a B-t?")

        for label, stdin in (("broken JSON payload", "{not json"), ("empty stdin", ""),
                             ("non-dict tool_input", json.dumps({"tool_name": "Bash", "tool_input": ["x"]})),
                             ("JSON list payload", "[1,2]")):
            r = gate(td, stdin)
            check(f"{label} -> fail-open exit 0", r.returncode == 0, r.stderr)
        r = gate(td, reply(os.path.join(td, "nope.jsonl"), "kerdes?"))
        check("missing transcript -> fail-open exit 0", r.returncode == 0, r.stderr)

        r = gate(td, q)
        check("question, no lookup -> BLOCK (exit 2)", r.returncode == 2 and "dontes-kereso.py" in r.stderr, r.stderr)
        check("block message names the command to run", "python3 " in r.stderr and "kulcsszo" in r.stderr, r.stderr)
        r = gate(td, reply(t, "Kesz: a build lefutott, 3 teszt zold."))
        check("non-question, no lookup -> pass", r.returncode == 0, r.stderr)
        r = gate(td, reply(t, "A lap itt van: https://example.com/a?b=1 , es `x?y` a kod."))
        check("'?' only inside a URL / inline code -> pass", r.returncode == 0, r.stderr)
        r = gate(td, reply(t, "Jo igy?" + " szoveg" * 20000))
        check("huge question text -> still BLOCK, no crash", r.returncode == 2, r.stderr[:200])
        r = gate(td, reply(t, "Igy jo?", tool="mcp__plugin_telegram_telegram__edit_message"))
        check("edit_message question, no lookup -> BLOCK", r.returncode == 2, r.stderr)
        r = gate(td, bash(t, 'python3 scripts/tg-valasztas.py "Mehet?" "Igen" "Nem"'))
        check("tg-valasztas question, no lookup -> BLOCK", r.returncode == 2, r.stderr)
        r = gate(td, bash(t, 'python3 /home/x/marveen/scripts/tg-valasztas.py --valasz "$(cat /tmp/x)"'))
        check("tg-valasztas --valasz -> pass", r.returncode == 0, r.stderr)
        r = gate(td, bash(t, 'python3 scripts/tg-valasztas.py --agent cody --valasz "Rendben."'))
        check("tg-valasztas --agent X --valasz -> pass", r.returncode == 0, r.stderr)
        r = gate(td, bash(t, "grep -n valasz scripts/tg-valasztas.py"))
        check("a Bash call that only names tg-valasztas -> pass", r.returncode == 0, r.stderr)

        lk = lookup(td, "melyik")
        check("(lookup ran)", lk.returncode in (0, 1), lk.stderr)
        r = gate(td, q)
        check("NEGATIVE CONTROL: the SAME question after the lookup -> pass", r.returncode == 0, r.stderr)
        r = gate(td, bash(t, 'python3 scripts/tg-valasztas.py "Mehet?" "Igen" "Nem"'))
        check("tg-valasztas question after the lookup -> pass", r.returncode == 0, r.stderr)

        t2 = transcript(td, owner_at=time.time() + 5, name="t2.jsonl")
        r = gate(td, reply(t2, "Melyiket kered?"))
        check("lookup BEFORE the owner's last message -> BLOCK", r.returncode == 2
              and "ELOTT" in r.stderr, r.stderr)
        t3 = transcript(td, owner_at=now - 60, queued_at=time.time() + 5, name="t3.jsonl")
        r = gate(td, reply(t3, "Melyiket kered?"))
        check("owner message queued mid-turn AFTER the lookup -> BLOCK", r.returncode == 2, r.stderr)

    with tempfile.TemporaryDirectory() as td:
        fixture(td)
        t = transcript(td, owner_at=time.time() - 60)
        lookup(td, "rust", agent="marveen", session="sess-B")
        r = gate(td, reply(t, "Mehet?", session="sess-B"), agent="cody")
        check("run under another agent name but THIS session id -> pass", r.returncode == 0, r.stderr)
        r = gate(td, reply(t, "Mehet?", session="sess-C"), agent="cody")
        check("other agent, other session -> BLOCK", r.returncode == 2, r.stderr)
        log = [json.loads(ln) for ln in open(os.path.join(td, "gate.log"), encoding="utf-8")]
        check("gate logs allow and block outcomes", [e["outcome"] for e in log] == ["allow-looked-up", "block"], str(log))


def main():
    test_lookup()
    test_gate()
    print(f"\n{'FAIL' if failed else 'OK'}: {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
