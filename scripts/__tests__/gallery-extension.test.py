#!/usr/bin/env python3
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gallery-extension.py"
TOKEN = ROOT / "store" / ".dashboard-token"

class Handler(BaseHTTPRequestHandler):
    fail_create = False
    requests = []
    def log_message(self, *_): pass
    def send_json(self, status, body):
        data = json.dumps(body).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).requests.append(("POST", self.path, body, self.headers.get("Authorization")))
        if type(self).fail_create: self.send_json(400, {"error": "no"})
        else: self.send_json(201, {"id": 2, "key": "mvdk_NEW_SECRET", "name": body["name"]})
    def do_GET(self):
        type(self).requests.append(("GET", self.path, None, self.headers.get("Authorization")))
        self.send_json(200, {"keys": [{"id": 1, "name": "pinterest-gallery", "scope": "gallery"}, {"id": 2, "name": "pinterest-gallery", "scope": "gallery"}, {"id": 3, "name": "other", "scope": "gallery"}, {"id": 4, "name": "pinterest-gallery", "scope": "full"}]})
    def do_DELETE(self):
        type(self).requests.append(("DELETE", self.path, None, self.headers.get("Authorization"))); self.send_json(200, {"ok": True})

class GalleryExtensionBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = TOKEN.read_bytes() if TOKEN.exists() else None
        TOKEN.parent.mkdir(parents=True, exist_ok=True); TOKEN.write_text("dashboard-test-token\n")
    @classmethod
    def tearDownClass(cls):
        if cls.original is None: TOKEN.unlink(missing_ok=True)
        else: TOKEN.write_bytes(cls.original)
    def setUp(self): Handler.requests = []; Handler.fail_create = False
    def server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start(); return srv
    def run_build(self, out, server):
        return subprocess.run(["python3", str(SCRIPT), "build", "--out", str(out), "--server", server], capture_output=True, text=True)
    def test_happy_path_and_marker_rebuild(self):
        srv = self.server()
        try:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "extension"; result = self.run_build(out, f"http://127.0.0.1:{srv.server_port}")
                self.assertEqual(result.returncode, 0, result.stderr); self.assertTrue((out / ".marveen-gallery-build").is_file()); self.assertTrue((out / "manifest.json").is_file())
                self.assertTrue((out / "lib.js").is_file()); self.assertFalse((out / "lib.d.ts").exists()); self.assertFalse((out / "config.example.js").exists())
                config = (out / "config.js").read_text(); self.assertIn("mvdk_NEW_SECRET", config); self.assertNotIn("mvdk_NEW_SECRET", result.stdout + result.stderr)
                self.assertIn(("DELETE", "/api/auth/device-keys/1", None, "Bearer dashboard-test-token"), Handler.requests)
                deleted = [r[1] for r in Handler.requests if r[0] == "DELETE"]
                self.assertEqual(deleted, ["/api/auth/device-keys/1"]); self.assertIn("Old keys revoked: 1", result.stdout)
                self.assertEqual(self.run_build(out, f"http://127.0.0.1:{srv.server_port}").returncode, 0)
        finally: srv.shutdown()
    def test_server_400_leaves_output_untouched(self):
        srv = self.server(); Handler.fail_create = True
        try:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "extension"; result = self.run_build(out, f"http://127.0.0.1:{srv.server_port}")
                self.assertEqual(result.returncode, 1); self.assertFalse(out.exists())
        finally: srv.shutdown()
    def test_unreachable_server(self):
        with tempfile.TemporaryDirectory() as td: self.assertEqual(self.run_build(Path(td) / "out", "http://127.0.0.1:1").returncode, 1)
    def test_refuses_foreign_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td); (out / "foreign").write_text("x")
            result = self.run_build(out, "http://127.0.0.1:1"); self.assertEqual(result.returncode, 1); self.assertTrue((out / "foreign").is_file())

if __name__ == "__main__": unittest.main()
