#!/usr/bin/env python3
"""Build a configured Marveen Gallery Chrome extension directory."""
import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "extensions" / "pinterest-gallery"
TOKEN_FILE = REPO_ROOT / "store" / ".dashboard-token"
MARKER = ".marveen-gallery-build"
EXTENSION_ID = "fcbpplcgmhgbeandlnfflfcendpkmkbb"

def request(server, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server.rstrip("/") + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"dashboard returned HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"dashboard is unreachable: {exc}") from exc

def build(args):
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise RuntimeError(f"refusing non-empty directory without {MARKER}: {out}")
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read dashboard token: {TOKEN_FILE}") from exc
    if not token:
        raise RuntimeError("dashboard token is empty")

    status, minted = request(args.server, token, "POST", "/api/auth/device-keys", {"name": args.name, "scope": "gallery"})
    key = minted.get("key") if isinstance(minted, dict) else None
    if status != 201 or not isinstance(key, str) or not key.startswith("mvdk_"):
        raise RuntimeError("dashboard did not return a gallery device key")

    out.mkdir(parents=True, exist_ok=True)
    for source in SOURCE.rglob("*"):
        if not source.is_file() or source.name in {"config.js", "config.example.js", "lib.d.ts"}: continue
        target = out / source.relative_to(SOURCE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (out / "config.js").write_text(f"export const SERVER = {json.dumps(args.server)}\nexport const KEY = {json.dumps(key)}\n", encoding="utf-8")
    (out / MARKER).write_text("Marveen Gallery build\n", encoding="utf-8")

    revoked = 0
    try:
        _, listed = request(args.server, token, "GET", "/api/auth/device-keys")
        for old in listed.get("keys", []):
            # Same name is not enough: a full or operator key named alike is not ours to revoke.
            if old.get("name") == args.name and old.get("scope") == "gallery" and old.get("id") != minted.get("id"):
                request(args.server, token, "DELETE", f"/api/auth/device-keys/{old['id']}")
                revoked += 1
    except RuntimeError as exc:
        print(f"warning: old keys were not fully revoked: {exc}", file=sys.stderr)
    print(f"Output: {out}")
    print(f"Extension ID: {EXTENSION_ID}")
    print(f"Old keys revoked: {revoked}")

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("build")
    command.add_argument("--out", required=True)
    command.add_argument("--server", default="http://localhost:3420")
    command.add_argument("--name", default="pinterest-gallery")
    args = parser.parse_args()
    try:
        build(args)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
