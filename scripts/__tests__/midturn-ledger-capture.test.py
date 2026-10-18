"""MIDTURN901 regressziós teszt: a Stop-hook back-fill viselkedese, izolalt DB-n."""
import importlib.util, json, os, subprocess, sqlite3, tempfile, sys

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
ok = True
def check(label, cond):
    global ok
    print(("OK  " if cond else "HIBA"), label)
    if not cond: ok = False

with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "t.db")
    tp = os.path.join(d, "t.jsonl")

    env = dict(os.environ, LEDGER_DB_PATH=db)

    def env_block(mid, ts, text, extra=""):
        return ('<channel source="plugin:telegram:telegram" chat_id="999" '
                f'message_id="{mid}" ts="{ts}"{extra}>{text}</channel>')

    lines = [
      {"type": "user", "message": {"role": "user", "content": env_block("1", "2026-09-01T10:00:00.000Z", "fordulat-inditó")}},
      {"type": "queue-operation", "content": env_block("2", "2026-09-01T10:05:00.000Z", "menet kozben erkezett")},
      # sajat idezet: NEM szabad bejovonek szamitania
      {"type": "assistant", "message": {"role": "assistant", "content": [
          {"text": "Idezem a jelentesemben: " + env_block("999", "2026-09-01T10:09:00.000Z", "ez az EN szovegem")}]}},
      {"type": "queue-operation", "content": env_block("3", "2026-09-01T10:07:00.000Z", "kep felirat",
          ' image_path="/tmp/k.jpg"')},
    ]
    with open(tp, "w", encoding="utf-8") as fh:
        for l in lines: fh.write(json.dumps(l) + "\n")

    payload = json.dumps({"transcript_path": tp, "cwd": os.path.dirname(os.path.dirname(HOOKS))})
    r = subprocess.run([sys.executable, os.path.join(HOOKS, "ledger-midturn-capture.py")],
                       input=payload, capture_output=True, text=True, env=env)
    check("a hook exit 0 (fail-open)", r.returncode == 0)

    con = sqlite3.connect(db)
    rows = {m: (ca, ts, kind, path) for m, ca, ts, kind, path in con.execute(
        "SELECT message_id, created_at, ts, attachment_kind, attachment_path"
        " FROM conversation_log WHERE direction='in'")}
    check("a fordulat-inditot rogziti (1)", "1" in rows)
    check("a MENET KOZBENIT rogziti (2)", "2" in rows)
    check("a kepet rogziti, kind=photo + ut (3)",
          rows.get("3", (0,0,None,None))[2] == "photo" and rows["3"][3] == "/tmp/k.jpg")
    check("a SAJAT idezetet NEM rogziti (999)", "999" not in rows)
    check("a created_at a VALODI erkezesi ido, nem a back-fill ideje",
          rows["2"][0] == 1788257100)   # 2026-09-01T10:05:00Z
    check("az idorend helyes (1 < 3 < 2 nem, hanem 1 < 2 < 3)",
          rows["1"][0] < rows["2"][0] < rows["3"][0])

    before = con.execute("SELECT count(*) FROM conversation_log").fetchone()[0]
    subprocess.run([sys.executable, os.path.join(HOOKS, "ledger-midturn-capture.py")],
                   input=payload, capture_output=True, text=True, env=env)
    after = sqlite3.connect(db).execute("SELECT count(*) FROM conversation_log").fetchone()[0]
    check("idempotens (masodik futas nem duplikal)", before == after)

    r2 = subprocess.run([sys.executable, os.path.join(HOOKS, "ledger-midturn-capture.py")],
                        input="nem json", capture_output=True, text=True, env=env)
    check("rossz payloadra is exit 0", r2.returncode == 0)
    r3 = subprocess.run([sys.executable, os.path.join(HOOKS, "ledger-midturn-capture.py")],
                        input=json.dumps({"transcript_path": "/nincs/ilyen.jsonl"}),
                        capture_output=True, text=True, env=env)
    check("nemletezo transcriptre is exit 0", r3.returncode == 0)

print("\nEREDMENY:", "MINDEN OK" if ok else "VAN BUKAS")
sys.exit(0 if ok else 1)
