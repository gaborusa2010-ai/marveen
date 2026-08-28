#!/usr/bin/env python3
"""
Put workshop files on the owner's Google Drive, in folders, without duplicating.

`google_auth.call` speaks JSON, and a file upload is multipart, so that helper
cannot do it. This adds the two operations the workshop actually needs: make a
folder if it is not already there, and upload a file into it.

IDEMPOTENT ON PURPOSE. Both operations look for an existing item of the same
name in the same parent first. Drive is happy to hold five folders called
"Rajzok" and five copies of the same PDF, and a second run of a sync script is
exactly how that happens. A tidy-up job that untidies on the second run is
worse than no job.

Usage
-----
  drive_up.py mkdir "Name" [--parent ID]        -> prints the folder id
  drive_up.py put FILE [--parent ID] [--name N] -> prints the file id
  drive_up.py ls [--parent ID]
  drive_up.py mv FILE_ID --to PARENT_ID           -> move, does NOT copy or delete
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_auth as g  # noqa: E402

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"


def _q(name, parent, folder_only=False):
    """Drive query for a name in a parent. Apostrophes have to be escaped or a
    file called Gabor's drawing silently terminates the query string."""
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    if folder_only:
        q += f" and mimeType = '{FOLDER}'"
    return q


def find(name, parent=None, folder_only=False):
    r = g.call(API, params={"q": _q(name, parent, folder_only),
                            "fields": "files(id,name)", "pageSize": 2})
    fs = r.get("files", [])
    return fs[0]["id"] if fs else None


def mkdir(name, parent=None):
    existing = find(name, parent, folder_only=True)
    if existing:
        return existing, False
    body = {"name": name, "mimeType": FOLDER}
    if parent:
        body["parents"] = [parent]
    return g.call(API, method="POST", body=body)["id"], True


def put(path, parent=None, name=None):
    name = name or os.path.basename(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        data = fh.read()

    existing = find(name, parent)
    meta = {"name": name}
    if parent and not existing:
        meta["parents"] = [parent]

    boundary = "----muhely-drive-boundary"
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(meta).encode(), b"\r\n",
        f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode(),
        data, b"\r\n", f"--{boundary}--\r\n".encode(),
    ])
    url = f"{UPLOAD}/{existing}?uploadType=multipart" if existing else f"{UPLOAD}?uploadType=multipart"
    req = urllib.request.Request(
        url, data=body, method="PATCH" if existing else "POST",
        headers={"Authorization": f"Bearer {g.access_token()}",
                 "Content-Type": f"multipart/related; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["id"], not existing
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Drive {e.code}: {e.read()[:300].decode(errors='replace')}") from None


def mv(file_id, new_parent):
    """Re-parent a file. Drive moves by editing parents, so nothing is copied and
    nothing is deleted -- which is why tidying up is not a destructive act here."""
    cur = g.call(f"{API}/{file_id}", params={"fields": "parents,name"})
    old = ",".join(cur.get("parents", []))
    g.call(f"{API}/{file_id}", method="PATCH",
           params={"addParents": new_parent, "removeParents": old, "fields": "id,parents"})
    return cur.get("name", file_id)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mkdir"); m.add_argument("name"); m.add_argument("--parent")
    p = sub.add_parser("put"); p.add_argument("file"); p.add_argument("--parent"); p.add_argument("--name")
    l = sub.add_parser("ls"); l.add_argument("--parent")
    v = sub.add_parser("mv"); v.add_argument("file_id"); v.add_argument("--to", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "mkdir":
        fid, made = mkdir(a.name, a.parent)
        print(f"{'letrehozva' if made else 'mar letezett'}: {a.name}  {fid}")
    elif a.cmd == "put":
        fid, made = put(a.file, a.parent, a.name)
        print(f"{'feltoltve' if made else 'frissitve '}: {a.name or os.path.basename(a.file)}  {fid}")
    elif a.cmd == "mv":
        print(f"athelyezve: {mv(a.file_id, a.to)}")
    else:
        q = f"'{a.parent}' in parents and trashed = false" if a.parent else "trashed = false"
        r = g.call(API, params={"q": q, "fields": "files(id,name,mimeType)", "pageSize": 100,
                                "orderBy": "name"})
        for f in r.get("files", []):
            kind = "mappa" if f["mimeType"] == FOLDER else ""
            print(f"  {f['id']}  {f['name']}  {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
