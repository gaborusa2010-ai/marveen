#!/usr/bin/env python3
"""Standalone integration tests for scripts/tabla.py."""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse

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
            if body and body.get("status") == "waiting":
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
            return self.send(200, [{"id": 7, "from_agent": "alice", "to_agent": "tester", "status": "pending", "created_at": 100, "content": "hello"}])
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

r = run("mozgat", "730", "waiting")
check("mozgat 409", r.returncode == 1 and "blocked move hint" in r.stderr)
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
no_root = tempfile.mkdtemp()
r = run("lista", cwd=no_root, env={k: v for k, v in base_env.items() if k != "CLAW_DIR"})
check("no token above cwd is usage error, not a traceback", r.returncode == 2 and "Traceback" not in r.stderr)
os.rmdir(no_root)

server.shutdown()
server.server_close()
temp.cleanup()
print("PASS" if not fails else "FAIL %d" % fails)
sys.exit(1 if fails else 0)
