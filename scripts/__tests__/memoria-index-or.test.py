#!/usr/bin/env python3
"""memoria-index-or.py (card #763): the sentinel says one of five things and never
stays quiet on a missing, empty or truncating index.

Cases: under target -> no message; over target -> FIGYELEM with candidates and
WITHOUT the archive pointer; over 180/23000 -> SURGOS; a 3000-line file -> CSONKUL
and a capped list; empty file and missing file -> HIBA, exit 2, message still
produced; dry-run prints the payload and posts nothing; a closed port -> exit 3;
a real POST reaches a local server with the bearer token and the content;
the same level is not re-posted within 7 days, a higher level is, --force is,
and HIBA always is.
"""
import importlib.util, json, os, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OR = os.path.join(HERE, "..", "memoria-index-or.py")
spec = importlib.util.spec_from_file_location("g", os.path.join(HERE, "..", "memory-index-guard.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

fails = 0
def check(name, cond, got=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f" -- got: {got!r}" if not cond and got != "" else ""))
    fails += 0 if cond else 1

def page(d, name, typ):
    open(os.path.join(d, name), "w").write(f"---\nname: {name[:-3]}\nmetadata:\n  type: {typ}\n---\nbody\n")
    t = time.time() - 30 * 86400
    os.utime(os.path.join(d, name), (t, t))

def build(d, n_ref, pad=0, archive=True):
    lines = ["# Index", ""]
    if archive:
        page(d, "MEMORY-archivum.md", "reference")
        lines.append("- [MEMORY-archivum](MEMORY-archivum.md) — ritkan kello lapok")
    for i in range(n_ref):
        page(d, f"ref{i}.md", "reference")
        lines.append(f"- [ref{i}](ref{i}.md) — mutato " + "x" * pad)
    open(os.path.join(d, "MEMORY.md"), "w").write("\n".join(lines) + "\n")

def run(d, *args, env=None):
    e = dict(os.environ, MEMORY_INDEX_DIR=d, MEMORIA_INDEX_OR_STATE=os.path.join(d, "state.json"),
             DASHBOARD_TOKEN_FILE=os.path.join(d, "token"))
    e.update(env or {})
    open(os.path.join(d, "token"), "w").write("tok-123\n")
    r = subprocess.run([sys.executable, OR, *args], capture_output=True, text=True, env=e)
    return r.returncode, r.stdout, r.stderr

def payload(out):
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    return None

def state(d):
    return json.load(open(os.path.join(d, "state.json")))

# 1. under target: rendben, no payload, state written
with tempfile.TemporaryDirectory() as d:
    build(d, 10)
    rc, out, err = run(d, "--dry-run")
    check("under target -> rc 0, 'rendben', no payload", rc == 0 and out.startswith("rendben") and payload(out) is None, out)
    check("state records level and numbers", state(d)["level"] == "rendben" and state(d)["lines"] > 0, state(d))

# 2. over target: FIGYELEM, candidates listed, archive pointer absent
with tempfile.TemporaryDirectory() as d:
    build(d, g.TARGET_LINES + 5)
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("over target -> rc 0 and a FIGYELEM payload", rc == 0 and p and p["content"].startswith("[MEMORIA-INDEX] FIGYELEM"), out[:200])
    check("payload goes marveen -> marveen by default", p and p["from"] == "marveen" and p["to"] == "marveen", p)
    check("payload names candidates", p and "ref" in p["content"] and "sort mozgatna" in p["content"], p and p["content"])
    check("payload never names the archive pointer", p and "MEMORY-archivum" not in p["content"])
    check("--to overrides the recipient", payload(run(d, "--dry-run", "--to", "cody")[1])["to"] == "cody")

# 3. over URGENT by chars
with tempfile.TemporaryDirectory() as d:
    build(d, 30, pad=790)   # ~30*800 = 24000 chars, 33 lines
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("over 23000 chars -> SURGOS", p and "SURGOS" in p["content"], out[:120])

# 4. huge file: CSONKUL, capped list
with tempfile.TemporaryDirectory() as d:
    build(d, 3000)
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("3000 lines -> CSONKUL, 'MAR VAG'", p and "CSONKUL" in p["content"] and "MAR VAG" in p["content"], out[:160])
    check("candidate list is capped (+N tovabbi), message under 2000 chars",
          p and "tovabbi" in p["content"] and len(p["content"]) < 2000, p and len(p["content"]))

# 5. empty file: HIBA, exit 2, payload present
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "MEMORY.md"), "w").close()
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("empty index -> rc 2 and HIBA payload", rc == 2 and p and "HIBA" in p["content"] and "URES" in p["content"], (rc, out[:160]))

# 6. missing file: HIBA, exit 2, payload present
with tempfile.TemporaryDirectory() as d:
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("missing index -> rc 2 and HIBA payload naming the path", rc == 2 and p and "HIBA" in p["content"] and d in p["content"], (rc, out[:160]))

# 6b. review fixes (marveen 22:57): invalid UTF-8 and a header-only index are HIBA
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "MEMORY.md"), "wb").write(b"# Index\n- [a](a.md) \xff\xfe\n")
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("invalid UTF-8 index -> rc 2, HIBA payload, no traceback", rc == 2 and p and "HIBA" in p["content"] and "Traceback" not in err, (rc, out[:120], err[-160:]))
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "MEMORY.md"), "w").write("# Index\n\n<!-- fejlec -->\n   \n")
    rc, out, err = run(d, "--dry-run")
    p = payload(out)
    check("header-only index (bytes, zero entries) -> rc 2, HIBA 'NINCS egyetlen bejegyzes'", rc == 2 and p and "NINCS egyetlen bejegyzes" in p["content"], (rc, out[:160]))
    check("header-only index state is HIBA, not rendben", state(d)["level"] == "HIBA", state(d))

# 7. closed port: exit 3, stderr says so, state keeps the error
with tempfile.TemporaryDirectory() as d:
    build(d, g.TARGET_LINES + 5)
    rc, out, err = run(d, env={"DASHBOARD_URL": "http://127.0.0.1:9"})
    check("closed port -> rc 3 and a stderr line", rc == 3 and "nem ment el" in err, (rc, err[:160]))
    check("state keeps post_error", "post_error" in state(d), state(d))

# 8. real POST to a local server: bearer token, JSON body, id returned, rc 0
got = {}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        got["path"] = self.path
        got["auth"] = self.headers.get("Authorization")
        got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"id": 4242}')
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory() as d:
    build(d, g.TARGET_LINES + 5)
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("real POST -> rc 0, 'id=4242' printed", rc == 0 and "id=4242" in out, (rc, out[-120:], err[-120:]))
    check("POST hits /api/messages with the bearer token", got.get("path") == "/api/messages" and got.get("auth") == "Bearer tok-123", got)
    check("POST body is the FIGYELEM message marveen -> marveen",
          got.get("body", {}).get("from") == "marveen" and got["body"]["to"] == "marveen" and got["body"]["content"].startswith("[MEMORIA-INDEX] FIGYELEM"), got.get("body"))
    check("state keeps message_id", state(d).get("message_id") == 4242, state(d))
    check("state keeps last_level/last_message_at", state(d).get("last_level") == "FIGYELEM" and state(d).get("last_message_at"), state(d))
    # 9. same level again the same week: no second POST, state still measured
    got.clear()
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("same level within 7 days -> no POST, 'mar jelezve', rc 0", rc == 0 and "mar jelezve" in out and not got, (rc, out[-160:], got))
    check("repeat run still records the measurement", state(d)["measured_at"] > 0 and state(d)["last_message_id"] == 4242, state(d))
    # 10. --force posts again
    rc, out, err = run(d, "--force", env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("--force posts again", rc == 0 and got.get("body"), (rc, out[-120:]))
    # 11. escalation to CSONKUL posts at once
    got.clear()
    build(d, 3000)
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("higher level (CSONKUL) within the week -> POST at once", got.get("body", {}).get("content", "").startswith("[MEMORIA-INDEX] CSONKUL"), (rc, out[-120:], got))
    # 12. 8-day-old last message at the same level: posts again
    got.clear()
    st = state(d); st["last_message_at"] -= 8 * 86400; json.dump(st, open(os.path.join(d, "state.json"), "w"))
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("same level after 8 days -> POST again", got.get("body", {}).get("content", "").startswith("[MEMORIA-INDEX] CSONKUL"), (rc, out[-120:]))
    # 13. HIBA is never suppressed
    got.clear()
    os.remove(os.path.join(d, "MEMORY.md"))
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("HIBA right after a message -> still posts, rc 2", rc == 2 and "HIBA" in got.get("body", {}).get("content", ""), (rc, out[-120:], got))
    # 13b. rendben clears the quiet week: a fresh rise posts again at once
    got.clear()
    build(d, 10)
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("rendben clears last_level/last_message_at", rc == 0 and "last_message_at" not in state(d), state(d))
    build(d, g.TARGET_LINES + 5)
    rc, out, err = run(d, env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("rise after rendben within the week -> POST again", got.get("body", {}).get("content", "").startswith("[MEMORIA-INDEX] FIGYELEM"), (rc, out[-120:]))
    # 13c. --report-failure: fresh state with message_id -> silent; stale/absent state -> one HIBA message, rc 0
    got.clear()
    rc, out, err = run(d, "--report-failure", env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("--report-failure right after a posted run -> no POST, rc 0", rc == 0 and not got and "mar jelzett" in out, (rc, out[-120:], got))
    st = state(d); st["measured_at"] -= 2 * 3600; json.dump(st, open(os.path.join(d, "state.json"), "w"))
    rc, out, err = run(d, "--report-failure", env={"DASHBOARD_URL": f"http://127.0.0.1:{srv.server_port}"})
    check("--report-failure with a stale state -> HIBA 'elhalt' POST, rc 0", rc == 0 and "elhalt" in got.get("body", {}).get("content", ""), (rc, out[-120:], got))
    os.remove(os.path.join(d, "state.json"))
    rc, out, err = run(d, "--report-failure", "--dry-run")
    check("--report-failure without state -> payload says no state written", rc == 0 and "nem irt state-et" in (payload(out) or {}).get("content", ""), out[:200])
    rc, out, err = run(d, "--report-failure", env={"DASHBOARD_URL": "http://127.0.0.1:9"})
    check("--report-failure on a closed port -> stderr, still rc 0 (no loop)", rc == 0 and "sem ment el" in err, (rc, err[-120:]))
    # 13d. (#774) non-integer measured_at in a hand-edited state: no crash, treated as stale
    json.dump({"measured_at": "tegnap", "message_id": 1}, open(os.path.join(d, "state.json"), "w"))
    rc, out, err = run(d, "--report-failure", "--dry-run")
    check("--report-failure with a non-integer measured_at -> payload, rc 0, no traceback", rc == 0 and payload(out) and "Traceback" not in err, (rc, out[:120], err[-120:]))
    # 14. corrupt state file is not fatal
    open(os.path.join(d, "state.json"), "w").write("{not json")
    build(d, 10)
    rc, out, err = run(d, "--dry-run")
    check("corrupt state file -> still measures, rc 0", rc == 0 and out.startswith("rendben"), (rc, out[:120], err[-120:]))
srv.shutdown()

# 15. (#774) the guard does not import: the main run dies, the failure hook still speaks
import shutil
with tempfile.TemporaryDirectory() as d:
    shutil.copy(OR, os.path.join(d, "memoria-index-or.py"))
    open(os.path.join(d, "memory-index-guard.py"), "w").write("import os\nraise ImportError('bad merge')\n")
    e = dict(os.environ, MEMORY_INDEX_DIR=d, MEMORIA_INDEX_OR_STATE=os.path.join(d, "state.json"))
    r1 = subprocess.run([sys.executable, os.path.join(d, "memoria-index-or.py"), "--dry-run"], capture_output=True, text=True, env=e)
    check("broken guard: the main run fails non-zero (what OnFailure sees)", r1.returncode != 0, r1.returncode)
    r2 = subprocess.run([sys.executable, os.path.join(d, "memoria-index-or.py"), "--report-failure", "--dry-run"], capture_output=True, text=True, env=e)
    check("broken guard: --report-failure still produces the HIBA payload, rc 0", r2.returncode == 0 and "elhalt" in (payload(r2.stdout) or {}).get("content", ""), (r2.returncode, r2.stdout[:120], r2.stderr[-160:]))

print(f"\n{'PASS' if not fails else 'FAIL'} memoria-index-or ({fails} failing)")
sys.exit(1 if fails else 0)
