#!/usr/bin/env python3
"""Standalone integration tests for scripts/tabla.py."""
import datetime as dt
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
TABLA = os.path.abspath(os.path.join(HERE, "..", "tabla.py"))
fails = 0


def check(name, condition):
    global fails
    print(("  ok   " if condition else "  FAIL ") + name)
    fails += 0 if condition else 1


cards = [
    {"id": "b8acdfb6aaaa", "seq": 730, "title": "Normal", "status": "planned", "priority": "normal", "assignee": "tester", "description": "MOSTANI ALLAPOT old\n---\nDetails", "description_updated_at": 10},
    {"id": "abc11111", "seq": 2, "title": "Low", "status": "waiting", "priority": "low", "assignee": "tester", "description": "No block"},
    {"id": "abc22222", "seq": 3, "title": "High", "status": "in_progress", "priority": "high", "assignee": "tester", "description": "x"},
    {"id": "urgent00", "seq": 1, "title": "Urgent", "status": "testing", "priority": "urgent", "assignee": "other", "description": "x"},
    {"id": "intro000", "seq": 5, "title": "Intro", "status": "planned", "priority": "normal", "assignee": "other", "description": "Intro text\n---\nmore"},
    {"id": "done0000", "seq": 4, "title": "Done", "status": "done", "priority": "urgent", "assignee": "tester", "description": "x"},
]
requests = []
memory_header = "strict=true; relaxed=true"
messages = [{"id": 7, "from_agent": "alice", "to_agent": "tester", "status": "pending", "created_at": 100, "content": "hello"}]


def message_page(query):
    # Mirrors src/web/routes/messages.ts: newest first, limit default 50 capped at 200,
    # `before` as an id cursor; the pending path returns every pending row and ignores
    # limit and cursor, yet the truncation headers still follow length >= limit.
    status = query.get("status", [""])[0]
    limit = min(int(query.get("limit", ["50"])[0]), 200)
    rows = sorted(messages, key=lambda m: m["id"], reverse=True)
    if status == "pending":
        rows = [m for m in rows if m["status"] == "pending"]
    else:
        if status:
            rows = [m for m in rows if m["status"] == status]
        if "before" in query:
            rows = [m for m in rows if m["id"] < int(query["before"][0])]
        rows = rows[:limit]
    headers = {}
    if rows and len(rows) >= limit:
        headers = {"X-Result-Truncated": "true", "X-Result-Limit": str(limit), "X-Next-Before": str(rows[-1]["id"])}
    return rows, headers


def msg(mid, status, to="tester", sender="alice"):
    return {"id": mid, "from_agent": sender, "to_agent": to, "status": status, "created_at": 100, "content": "m%d" % mid}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def handle_request(self):
        global memory_header
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        path = parsed.path
        if path == "/api/kanban" and self.command == "GET":
            return self.send(200, cards)
        if path.startswith("/api/kanban/") and path.endswith("/comments"):
            if self.command == "GET":
                return self.send(200, [{"id": 9, "author": "a", "content": "later", "created_at": 20}])
            return self.send(200, {"ok": True, "id": 99})
        if path.startswith("/api/kanban/") and path.endswith("/move"):
            if body and body.get("status") == "waiting" and "/b8acdfb6aaaa/" in path:
                return self.send(409, {"error": "blocked move hint"})
            return self.send(200, {"ok": True})
        if path.startswith("/api/kanban/"):
            card_id = path.rsplit("/", 1)[-1]
            card = next((item for item in cards if item["id"] == card_id), None)
            if self.command == "PUT":
                if card:
                    card.update(body)
                return self.send(200, {"ok": True})
            return self.send(200, card or {"error": "not found"})
        if path.startswith("/api/messages/") and self.command == "PUT":
            if path.endswith("/409"):
                return self.send(409, {"error": "Message already closed"})
            if path.endswith("/400"):
                return self.send(400, {"error": "bad", "hint": "use a real message"})
            return self.send(200, {"ok": True})
        if path.startswith("/api/messages/"):
            return self.send(200, {"id": 7, "from_agent": "alice", "to_agent": "tester", "status": "pending", "created_at": 100, "content": "full message"})
        if path == "/api/messages":
            return self.send(200, *message_page(urllib.parse.parse_qs(parsed.query)))
        if path == "/api/memories" and self.command == "GET":
            headers = {"X-Memory-Search": memory_header} if memory_header else {}
            return self.send(200, [{"id": 5, "category": "warm", "content": "hit"}], headers)
        if path == "/api/memories" and self.command == "POST":
            return self.send(200, {"ok": True, "id": 5})
        if path.startswith("/api/memories/"):
            return self.send(200, {"ok": True, "owner_mismatch": {"owner": "other"}})
        if path == "/api/daily-log":
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "missing"})

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


temp = tempfile.TemporaryDirectory()
store = os.path.join(temp.name, "store")
os.mkdir(store)
with open(os.path.join(store, ".dashboard-token"), "w", encoding="utf-8") as handle:
    handle.write("test-token")
port = free_port()
server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base_env = dict(os.environ, CLAW_DIR=temp.name, CLAW_BASE=f"http://127.0.0.1:{port}")


def run(*args, cwd=None, input_text=None, identity=True, env=None):
    command = [sys.executable, TABLA]
    if identity:
        command += ["--ki", "tester"]
    command += list(args)
    return subprocess.run(command, cwd=cwd, env=env or base_env, input=input_text,
                          capture_output=True, text=True, timeout=10)


# Identity derivation from an /agents/<name>/ cwd.
agent_cwd = os.path.join(temp.name, "agents", "derived", "work")
os.makedirs(agent_cwd)
r = run("lista", cwd=agent_cwd, identity=False)
check("identity from cwd", r.returncode == 0 and requests[-1]["headers"].get("X-Agent-Id") == "derived")

r = run("kartya", "730")
check("seq ref resolution and card view", r.returncode == 0 and "#730" in r.stdout and "later" in r.stdout)
r = run("kartya", "b8ac")
check("hex prefix resolution", r.returncode == 0 and "Normal" in r.stdout)
before = len(requests)
r = run("kartya", "abc")
check("ambiguous ref is usage error", r.returncode == 2 and "ambiguous" in r.stderr and len(requests) == before + 1)

r = run("lista")
lines = [line for line in r.stdout.splitlines() if line.startswith("#")]
check("lista priority sorting", [line.split()[0] for line in lines] == ["#3", "#730", "#2"])
r = run("lista", "--mind", "--statusz", "testing")
check("lista all/filter", "#1" in r.stdout and "#3" not in r.stdout)

r = run("komment", "730", "comment", "--allapot", "fresh")
puts = [q for q in requests if q["method"] == "PUT" and q["path"] == "/api/kanban/b8acdfb6aaaa"]
desc = puts[-1]["body"]["description"]
check("komment replaces state block", r.returncode == 0 and desc.startswith("MOSTANI ALLAPOT (frissitve ") and ": fresh\n---\nDetails" in desc and "old" not in desc)
r = run("komment", "2", "comment", "--allapot", "new")
desc = [q for q in requests if q["method"] == "PUT" and q["path"] == "/api/kanban/abc11111"][-1]["body"]["description"]
check("komment prepends state block", ": new\n---\nNo block" in desc)
r = run("komment", "5", "comment", "--allapot", "kept")
desc = [q for q in requests if q["method"] == "PUT" and q["path"] == "/api/kanban/intro000"][-1]["body"]["description"]
check("komment keeps text above a --- line that is not a state block", desc.endswith(": kept\n---\nIntro text\n---\nmore"))
check("description PUT names the actor", all(q["body"].get("actor") == "tester" for q in requests if q["method"] == "PUT" and q["path"].startswith("/api/kanban/")))

r = run("mozgat", "730", "waiting", "--ok", "x")
check("mozgat 409", r.returncode == 1 and "blocked move hint" in r.stderr)


def last_move():
    return [q for q in requests if q["path"].endswith("/move")][-1]["body"]


london = ZoneInfo("Europe/London")
r = run("mozgat", "2", "waiting", "--ok", "marveen review", "--hatarido", "2026-11-02")
until = int(dt.datetime(2026, 11, 2, 23, 59, tzinfo=london).timestamp())
check("waiting with a date sends a date wait at 23:59 London",
      r.returncode == 0 and last_move().get("wait") == {"kind": "date", "note": "marveen review", "until": until})
r = run("mozgat", "2", "waiting", "--ok", "x", "--hatarido", "2026-10-20 14:30")
check("date with a time is London local time (BST)",
      r.returncode == 0 and last_move()["wait"]["until"] == int(dt.datetime(2026, 10, 20, 13, 30, tzinfo=dt.timezone.utc).timestamp()))
r = run("mozgat", "2", "waiting", "--ok", "  Ariel  ")
check("waiting without a date sends an external wait",
      r.returncode == 0 and last_move().get("wait") == {"kind": "external", "note": "Ariel"})
r = run("mozgat", "2", "testing")
check("a non-waiting move sends no wait", r.returncode == 0 and "wait" not in last_move())
for name, extra in (("waiting without --ok", ("waiting",)),
                    ("--ok on a non-waiting move", ("testing", "--ok", "x")),
                    ("--hatarido on a non-waiting move", ("testing", "--hatarido", "2026-11-02")),
                    ("a bad date", ("waiting", "--ok", "x", "--hatarido", "2026-13-01")),
                    ("a blank --ok", ("waiting", "--ok", "   ")),
                    ("an --ok over 200 characters", ("waiting", "--ok", "y" * 201))):
    before = len(requests)
    r = run("mozgat", "2", *extra)
    check(name + " is a usage error with no request", r.returncode == 2 and len(requests) == before)
r = run("lezar", "409")
check("lezar 409 is success", r.returncode == 0 and "already closed" in r.stdout)
r = run("lezar", "8", "--csendes")
check("lezar sends boolean false", r.returncode == 0 and requests[-1]["body"].get("notify") is False)

r = run("emlek", "keres", "árvíztűrő")
search = requests[-1]
check("memory query is percent encoded", "%C3%A1rv%C3%ADzt%C5%B1r%C5%91" in search["path"] and "árvíztűrő" not in search["path"])
check("relaxed warning and label", "relaxed=true" in r.stdout and "approximate" in r.stderr)
memory_header = ""
r = run("emlek", "keres", "x")
check("missing search header warning", "missing" in r.stderr)
memory_header = "strict=true; relaxed=true"

before = len(requests)
r = run("naplo", "")
check("empty text rejected without request", r.returncode == 2 and len(requests) == before)
before = len(requests)
r = run("naplo", "x" * 100001)
check("long text rejected without request", r.returncode == 2 and len(requests) == before)

down_env = dict(base_env, CLAW_BASE=f"http://127.0.0.1:{free_port()}")
r = run("lista", env=down_env)
check("server down is exit 3", r.returncode == 3)
r = run("lezar", "400")
check("400 body hint on stderr", r.returncode == 1 and "use a real message" in r.stderr)

check("X-Agent-Id on every recorded request", all(q["headers"].get("X-Agent-Id") for q in requests))
r = run("--json", "lista")
try:
    parsed = json.loads(r.stdout)
    json_ok = isinstance(parsed, list)
except ValueError:
    json_ok = False
check("--json output parses", r.returncode == 0 and json_ok)
r = run("kartya", "730", "--json")
try:
    json_ok = "card" in json.loads(r.stdout)
except ValueError:
    json_ok = False
check("--json accepted after the command", r.returncode == 0 and json_ok)
r = run("emlek", "keres", "x", "--json")
try:
    json_ok = isinstance(json.loads(r.stdout), list)
except ValueError:
    json_ok = False
check("emlek keres --json keeps stdout parseable", r.returncode == 0 and json_ok and "relaxed=true" in r.stderr)


def message_gets(status=None):
    gets = [q for q in requests if q["method"] == "GET" and q["path"].startswith("/api/messages?")]
    return [q for q in gets if status is None or ("status=%s" % status) in q["path"]]


def json_rows(result):
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


# --nyitott reads past one page: 450 open to tester, 300 closed, 120 sent by tester, 3 pending.
messages[:] = ([msg(i, "delivered") for i in range(1000, 1450)] + [msg(i, "done") for i in range(2000, 2300)]
               + [msg(i, "delivered", to="alice", sender="tester") for i in range(3000, 3120)]
               + [msg(i, "pending") for i in (5, 6, 4000)])
r = run("uzenetek", "--nyitott", "--json")
rows = json_rows(r) or []
ids = [m["id"] for m in rows]
check("--nyitott reads every page of open messages to me",
      r.returncode == 0 and len(rows) == 453 and set(ids) == set(range(1000, 1450)) | {5, 6, 4000})
check("--nyitott is sorted newest first", ids == sorted(ids, reverse=True))
check("--nyitott complete list gives no warning", "warning" not in r.stderr)
r = run("uzenetek", "--nyitott")
check("--nyitott text output has one line per message", len(r.stdout.splitlines()) == 453)

before_count = len(message_gets())
r = run("uzenetek", "--json")
check("plain uzenetek is one page", len(message_gets()) == before_count + 1)
check("a cut plain list warns with the cursor", r.returncode == 0 and "--before 3071" in r.stderr)
r = run("uzenetek", "--before", "1100", "--limit", "10", "--json")
check("--before is passed to the server", "before=1100" in message_gets()[-1]["path"]
      and [m["id"] for m in json_rows(r)] == list(range(1099, 1089, -1)))
messages[:] = [msg(i, "delivered") for i in range(1, 11)]
r = run("uzenetek")
check("a short plain list gives no warning", r.returncode == 0 and "warning" not in r.stderr and len(r.stdout.splitlines()) == 10)

# Pending path ignores the cursor: 260 pending must not loop or warn.
messages[:] = [msg(i, "pending") for i in range(1, 261)]
before_count = len(message_gets("pending"))
r = run("uzenetek", "--nyitott", "--json")
check("over 200 pending rows end after one repeat page",
      r.returncode == 0 and len(json_rows(r) or []) == 260 and len(message_gets("pending")) == before_count + 2
      and "warning" not in r.stderr)

# The page cap: 5200 open rows stop at MAX_PAGES and say so.
messages[:] = [msg(i, "delivered") for i in range(1, 5201)]
r = run("uzenetek", "--nyitott", "--json")
check("the page cap stops with a warning and the cursor",
      r.returncode == 0 and len(json_rows(r) or []) == 5000 and "stopped after 25 pages" in r.stderr and "--before 201" in r.stderr)

no_root = tempfile.mkdtemp()
r = run("lista", cwd=no_root, env={k: v for k, v in base_env.items() if k != "CLAW_DIR"})
check("no token above cwd is usage error, not a traceback", r.returncode == 2 and "Traceback" not in r.stderr)
os.rmdir(no_root)

server.shutdown()
server.server_close()
temp.cleanup()
print("PASS" if not fails else "FAIL %d" % fails)
sys.exit(1 if fails else 0)
