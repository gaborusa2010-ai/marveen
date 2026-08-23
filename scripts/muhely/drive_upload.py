#!/usr/bin/env python3
"""Upload a file to Google Drive, into a named folder, replacing same-name files.

Dependency-free (urllib only), reuses the OAuth token from google_auth.py.

    python3 scripts/muhely/drive_upload.py <file> [--folder "Marveen Backups"]

Why replace rather than append: Drive happily keeps many files with the same
name in one folder, so a weekly job would silently pile up copies. We keep the
newest N (default 8) and delete the rest, so the folder stays readable and the
quota does not creep.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_auth as g  # noqa: E402

DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def find_or_create_folder(name, token):
    q = ("mimeType='application/vnd.google-apps.folder' and trashed=false and "
         f"name='{name}'")
    r = g.call(DRIVE, params={"q": q, "fields": "files(id,name)"}, token=token)
    files = r.get("files") or []
    if files:
        return files[0]["id"]
    made = g.call(DRIVE, method="POST", token=token, body={
        "name": name, "mimeType": "application/vnd.google-apps.folder"})
    return made["id"]


def upload(path, folder_id, token):
    """Multipart upload. urlencode/json helpers cannot do this: the body is a
    MIME envelope with a binary part, so it is built by hand."""
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    meta = {"name": name, "parents": [folder_id]}
    boundary = f"================{uuid.uuid4().hex}=="
    with open(path, "rb") as fh:
        payload = fh.read()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        json.dumps(meta).encode(), b"\r\n",
        f"--{boundary}\r\n".encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        payload, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"{UPLOAD}?uploadType=multipart&fields=id,name,size",
        data=body, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Drive feltoltes hiba {e.code}:\n"
                         f"{e.read().decode('utf-8', 'replace')[:600]}")


def prune(folder_id, keep, token):
    r = g.call(DRIVE, params={
        "q": f"'{folder_id}' in parents and trashed=false",
        "orderBy": "createdTime desc",
        "fields": "files(id,name,createdTime)"}, token=token)
    files = r.get("files") or []
    removed = []
    for f in files[keep:]:
        g.call(f"{DRIVE}/{f['id']}", method="DELETE", token=token)
        removed.append(f["name"])
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--folder", default="Marveen Backups")
    ap.add_argument("--keep", type=int, default=8)
    a = ap.parse_args()

    if not os.path.exists(a.file):
        raise SystemExit(f"nincs ilyen fajl: {a.file}")
    size = os.path.getsize(a.file)
    if size < 100_000:
        raise SystemExit(f"gyanusan kicsi fajl ({size} bajt), nem toltom fel")

    token = g.access_token()
    g.require("https://www.googleapis.com/auth/drive")
    folder = find_or_create_folder(a.folder, token)
    up = upload(a.file, folder, token)
    print(f"feltoltve: {up.get('name')} ({up.get('size')} bajt) -> {a.folder}")
    gone = prune(folder, a.keep, token)
    if gone:
        print(f"torolve (retencio {a.keep}): {', '.join(gone)}")


if __name__ == "__main__":
    main()
