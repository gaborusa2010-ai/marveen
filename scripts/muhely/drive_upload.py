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
import time
import urllib.error
import urllib.parse
import urllib.request

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


# 32 MB. Drive megkoveteli, hogy a nem-utolso darab 256 kB tobbszorose legyen.
# Miert nem nagyobb: egy megszakadt darabot ujra kell kuldeni, tehat a darab merete
# az, amit egy halozati hiba visszavet. Miert nem kisebb: minden darab egy kulon
# HTTP-kor.
CHUNK = 32 * 1024 * 1024


def _start_resumable(name, ctype, size, folder_id, token):
    """Megnyit egy feltoltesi sessiont, es visszaadja a session URI-t."""
    body = json.dumps({"name": name, "parents": [folder_id]}).encode()
    req = urllib.request.Request(
        f"{UPLOAD}?uploadType=resumable&fields=id,name,size",
        data=body, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": ctype,
            "X-Upload-Content-Length": str(size),
        })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            loc = r.headers.get("Location")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Drive session-nyitas hiba {e.code}:\n"
                         f"{e.read().decode('utf-8', 'replace')[:600]}")
    if not loc:
        raise SystemExit("Drive: a session-nyitas nem adott Location fejlecet")
    return loc


def _received(session, total, token):
    """Megkerdezi a Drive-tol, hany bajtja van MAR meg. Nulla hosszu PUT
    'bytes */total' tartomannyal -- ez a dokumentalt mod. A 308 azt jelenti,
    hogy a session el, es a Range fejlec mondja meg az utolso megkapott bajtot.
    Visszateres: (kovetkezo_kezdet, kesz_valasz_vagy_None)."""
    req = urllib.request.Request(session, data=b"", method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Length": "0",
        "Content-Range": f"bytes */{total}",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return total, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 308:
            rng = e.headers.get("Range")
            return (int(rng.split("-")[1]) + 1 if rng else 0), None
        raise SystemExit(f"Drive session-lekerdezes hiba {e.code}:\n"
                         f"{e.read().decode('utf-8', 'replace')[:600]}")


def upload(path, folder_id, token, chunk=CHUNK, verbose=True):
    """Resumable (darabolt) feltoltes.

    MIERT NEM MULTIPART, ahogy 2026-09-08-ig volt: a regi valtozat az EGESZ
    fajlt beolvasta a memoriaba es egyetlen POST-ban kuldte el, 300 masodperces
    korlattal. Merve 2026-09-08 20:37: az 1,4 GB-os esti archivumon
    "URLError: The write operation timed out", es a mentes gepen kivuli masolat
    nelkul maradt. Az archivum reggel 833 MB volt, este 1,4 GB -- vagyis nem
    egyszeri kiugras, hanem a novekedes ert utol egy fix korlatot.

    A darabolas harom dolgot old meg egyszerre: nincs teljes fajl a memoriaban,
    egy darab hatarideje nem az egesz fajle, es egy megszakadt feltoltes ott
    folytatodik, ahol abbamaradt (a Drive megmondja, mennyit kapott meg)."""
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    total = os.path.getsize(path)
    session = _start_resumable(name, ctype, total, folder_id, token)

    start = 0
    hibak = 0
    with open(path, "rb") as fh:
        while start < total:
            fh.seek(start)
            data = fh.read(min(chunk, total - start))
            end = start + len(data) - 1
            req = urllib.request.Request(session, data=data, method="PUT",
                                         headers={
                "Authorization": f"Bearer {token}",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{total}",
            })
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    kesz = json.loads(r.read() or b"{}")
                if kesz:
                    return kesz
                start = end + 1
            except urllib.error.HTTPError as e:
                if e.code == 308:
                    rng = e.headers.get("Range")
                    start = int(rng.split("-")[1]) + 1 if rng else end + 1
                elif e.code in (500, 502, 503, 504):
                    hibak += 1
                    if hibak > 5:
                        raise SystemExit(f"Drive feltoltes hiba {e.code}, "
                                         f"{hibak} probalkozas utan feladom")
                    time.sleep(2 ** hibak)
                    start, kesz = _received(session, total, token)
                    if kesz:
                        return kesz
                else:
                    raise SystemExit(f"Drive feltoltes hiba {e.code}:\n"
                                     f"{e.read().decode('utf-8', 'replace')[:600]}")
            except urllib.error.URLError:
                # Idotullepes vagy megszakadt kapcsolat: megkerdezzuk, meddig
                # jutott, es onnan folytatjuk. EZ az az ag, ami a 2026-09-08-i
                # bukast megfogja.
                hibak += 1
                if hibak > 5:
                    raise SystemExit("Drive feltoltes: halozati hiba, "
                                     f"{hibak} probalkozas utan feladom")
                time.sleep(2 ** hibak)
                start, kesz = _received(session, total, token)
                if kesz:
                    return kesz
            if verbose:
                print(f"  ... {start / 1048576:.0f} / {total / 1048576:.0f} MB",
                      flush=True)
    # Ha a ciklus ugy ert veget, hogy nem kaptunk kesz valaszt, kerdezzuk meg.
    _, kesz = _received(session, total, token)
    if not kesz:
        raise SystemExit("Drive: a feltoltes vegen sem jott kesz valasz")
    return kesz


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
