#!/usr/bin/env python3
"""One-call gather for the folyamatos-ellenorzes heartbeat (#636).

Runs every measurement step of ~/.claude/scheduled-tasks/folyamatos-ellenorzes/SKILL.md
and prints a compact report; the model only judges what to say.

    python3 scripts/folyamatos-ellenorzes-kor.py            # gather + report, state untouched
    python3 scripts/folyamatos-ellenorzes-kor.py --zaras    # end of round: commit seen ids + last_run_at
    python3 scripts/folyamatos-ellenorzes-kor.py --zaras --nyitas-kerdezve
    python3 scripts/folyamatos-ellenorzes-kor.py --zaras --jelentett ID,ID   # Gmail ids reported to the owner

The state file is only written by --zaras, so a round that dies between gather and
report does not swallow the new mails. The gathered ids and the gather time wait in a
pending sidecar; last_run_at is the GATHER time, not the close time.

Exit: 0 = every measurement ran, 3 = at least one step is MERES-HIBA (report it).
"""
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.parse

ROOT = "/home/gabor/marveen"
STATE = os.path.join(ROOT, "store/folyamatos-ellenorzes-seen.json")
PENDING = os.path.join(ROOT, "store/folyamatos-ellenorzes-pending.json")
YAHOO_SEEN = os.path.join(ROOT, "store/yahoo-pinterest-seen.txt")
# One line per new Gmail mail and round: was it reported or kept silent (#460: the
# local-model shadow comparison had to reconstruct this from Telegram text).
VERDICTS = os.path.join(ROOT, "store/folyamatos-ellenorzes-verdicts.jsonl")

sys.path.insert(0, os.path.join(ROOT, "scripts/muhely"))

hibak = []
uj_meta = []


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"ids": []}


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def run(cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout + p.stderr).strip()


def naptar(call):
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {"timeMin": now.isoformat(), "timeMax": (now + datetime.timedelta(hours=1)).isoformat(),
              "singleEvents": "true", "orderBy": "startTime"}
    try:
        cals = call("https://www.googleapis.com/calendar/v3/users/me/calendarList").get("items", [])
    except Exception as ex:
        hibak.append("naptar-lista: %s" % ex)
        print("1 NAPTAR: MERES-HIBA (calendarList) %s" % ex)
        return
    esemenyek = []
    for c in cals:
        # The UK holiday calendar id has '#': unquoted it 404s and looks like a permission error.
        cid = urllib.parse.quote(c["id"], safe="")
        try:
            r = call("https://www.googleapis.com/calendar/v3/calendars/%s/events" % cid, params=params)
        except Exception as ex:
            hibak.append("naptar %s: %s" % (c.get("summary"), ex))
            print("1 NAPTAR-HIBA %s: %s" % (c.get("summary"), ex))
            continue  # one bad calendar must not swallow the others
        for e in r.get("items", []):
            st = e.get("start", {})
            esemenyek.append("%s | %s | %s" % (st.get("dateTime") or st.get("date"), c.get("summary"), e.get("summary")))
    print("1 NAPTAR: %d naptar, %d esemeny egy oran belul" % (len(cals), len(esemenyek)))
    for x in esemenyek:
        print("  EV " + x)


def gmail(call, state):
    seen = {i for i, _ in (state.get("ids") or [])}
    lr = state.get("last_run_at")
    ablak_q = ("after:%d" % max(lr - 300, time.time() - 86400)) if lr else "newer_than:1h"
    szamok, ids = {}, []
    for q in [ablak_q + " -category:promotions", "newer_than:6h", "newer_than:1d"]:
        ablak = q.startswith(ablak_q)
        m, token = [], None
        try:
            # The window is paged to the end; the two checks only need "not zero", so one page.
            while True:
                params = {"q": q, "maxResults": "50"}
                if token:
                    params["pageToken"] = token
                r = call("https://gmail.googleapis.com/gmail/v1/users/me/messages", params=params)
                m += r.get("messages") or []
                token = r.get("nextPageToken")
                if not token or not ablak:
                    break
        except Exception as ex:
            hibak.append("gmail %s: %s" % (q, ex))
            print("2 GMAIL: MERES-HIBA (%s) %s" % (q, ex))
            return [], False
        szamok[q] = "%d%s" % (len(m), "+" if token else "")
        if ablak:
            ids = [x["id"] for x in m]
    print("2 GMAIL: " + " | ".join("%s=%s" % kv for kv in szamok.items()))
    if not ids and all(v == "0" for v in szamok.values()):
        # Silence is the normal output, so an expired token looks exactly like a quiet hour.
        hibak.append("gmail: mindharom lekeres nulla")
        print("  MERES-HIBA: mindharom nulla, nem csend, hanem elromlott meres")
        return [], False
    uj = [i for i in ids if i not in seen]
    print("  uj (nincs a seen-ben): %d / ablak %d" % (len(uj), len(ids)))
    for i in uj:
        try:
            h = call("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + i,
                     params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        except Exception as ex:
            hibak.append("gmail metadata %s: %s" % (i, ex))
            print("  NEW %s MERES-HIBA %s" % (i, ex))
            # Not shown, so not seen: the next round must try it again.
            ids.remove(i)
            continue
        hd = {x["name"]: x["value"] for x in h.get("payload", {}).get("headers", [])}
        labels = ",".join(l for l in h.get("labelIds", []) if l.startswith("CATEGORY_") or l == "UNREAD")
        print("  NEW %s | %s | %s | %s | %s" % (i, hd.get("Date", ""), hd.get("From", ""), hd.get("Subject", ""), labels))
        uj_meta.append({"id": i, "from": hd.get("From", ""), "subject": hd.get("Subject", "")})
    return ids, True


def yahoo():
    rc, out = run(["python3", "scripts/yahoo-pinterest-check.py", "--all", "--days", "3", "--seen-file", YAHOO_SEEN])
    if rc == 2:
        hibak.append("yahoo exit 2 (belepes)")
        print("2d YAHOO: MERES-HIBA exit 2 (belepesi hiba, egyszer szolj)")
    elif rc != 0:
        hibak.append("yahoo exit %d" % rc)
        print("2d YAHOO: MERES-HIBA exit %d" % rc)
    else:
        print("2d YAHOO: %s" % ("nincs uj" if not out else "UJ:"))
    if out:
        for line in out.splitlines():
            print("  " + line)


def kanban():
    rc, out = run(["python3", "seed-skills/fleet-helper/scripts/fleet.py", "kanban-due"])
    try:
        cards = json.loads(out)
    except Exception:
        hibak.append("kanban-due exit %d" % rc)
        print("3 KANBAN: MERES-HIBA exit %d: %s" % (rc, out[:300]))
        return
    print("3 KANBAN: %d mai/lejart nyitott kartya" % len(cards))
    for c in cards:
        due = time.strftime("%Y-%m-%d", time.localtime(c["due_date"])) if c.get("due_date") else "-"
        print("  DUE %s | %s | %s | %s | %s" % (c["id"], due, c.get("assignee"), c.get("status"), c.get("title")))


def nyitas(state):
    if state.get("munkanap_nyitas_kerdezve") == time.strftime("%Y-%m-%d"):
        print("3c NYITAS: MA MAR KERDEZTUK, allj meg")
        return
    rc, out = run(["python3", "scripts/muhely/jobs.py", "list"])
    tail = out.splitlines()[-3:]
    print("3c NYITAS: meg nem kerdeztuk | %s %s | jobs.py exit %d" % (time.strftime("%a"), time.strftime("%H:%M"), rc))
    for line in tail:
        print("  " + line)


def emoji():
    rc, out = run(["python3", "scripts/emoji-figyelo.py", "--ember"])
    label = {0: "nincs UJ (csend)", 1: "UJ talalat: szolj, utana --jelentve <message_id>"}.get(rc)
    if label is None:
        hibak.append("emoji exit %d" % rc)
        label = "MERES-HIBA exit %d" % rc
    print("3d EMOJI: " + label)
    if rc != 0:
        for line in out.splitlines():
            print("  " + line)


def zaras(nyitas_kerdezve, jelentett=()):
    try:
        p = json.load(open(PENDING))
    except Exception:
        print("nincs pending fajl: elobb a gyujto futas kell (argumentum nelkul)")
        return 2
    d = load_state()
    now = int(time.time())
    seen = {i: t for i, t in (d.get("ids") or []) if now - t < 86400}
    for i in p.get("ids", []):
        seen[i] = now
    if nyitas_kerdezve:
        d["munkanap_nyitas_kerdezve"] = time.strftime("%Y-%m-%d")
    # A failed Gmail gather must not move the window, or the mail of that gap is never looked at.
    if p.get("gmail_ok", True):
        d["last_run_at"] = p["gathered_at"]
    # Keep the other keys (outbid_pending, last_outbid_report_at).
    d["ids"] = [[i, t] for i, t in seen.items()]
    atomic_write(STATE, d)
    os.remove(PENDING)
    ismeretlen = set(jelentett) - {x["id"] for x in p.get("new", [])}
    with open(VERDICTS, "a") as fh:
        for x in p.get("new", []):
            fh.write(json.dumps(dict(x, ts=now, gathered_at=p["gathered_at"],
                                     decision="REPORT" if x["id"] in jelentett else "SILENT"),
                                ensure_ascii=False) + "\n")
    print("dontesnaplo: %d uj level, %d jelentve%s" % (
        len(p.get("new", [])), len(set(jelentett) - ismeretlen),
        " | NEM ebben a korben latott id: %s" % ",".join(sorted(ismeretlen)) if ismeretlen else ""))
    print("allapot: %d id | last_run_at %s%s | egyeb kulcsok: %s" % (
        len(seen), time.strftime("%H:%M:%S", time.localtime(d["last_run_at"])),
        "" if p.get("gmail_ok", True) else " (NEM leptetve: a Gmail-gyujtes elbukott)", sorted(k for k in d if k != "ids")))
    return 0


def main():
    args = sys.argv[1:]
    if "--zaras" in args:
        jel = args[args.index("--jelentett") + 1] if "--jelentett" in args[:-1] else ""
        return zaras("--nyitas-kerdezve" in args, tuple(x.strip() for x in jel.split(",") if x.strip()))
    gathered_at = int(time.time())
    state = load_state()
    print("0 KOR %s | seen %d | last_run_at %s" % (
        time.strftime("%Y-%m-%d %H:%M %Z"), len(state.get("ids") or []),
        time.strftime("%H:%M", time.localtime(state["last_run_at"])) if state.get("last_run_at") else "-"))
    try:
        from google_auth import call as _gcall

        # google_auth reports HTTP and token errors as SystemExit, which the per-step
        # "except Exception" blocks would not catch: the whole round would exit 1 with
        # no MERES-HIBA line and skip yahoo/kanban/emoji (forgacs, card #636 comment).
        def call(*a, **k):
            try:
                return _gcall(*a, **k)
            except SystemExit as ex:
                raise RuntimeError("google_auth: %s" % ex) from None
    except Exception as ex:
        call = None
        hibak.append("google_auth import: %s" % ex)
        print("1-2 GOOGLE: MERES-HIBA import %s" % ex)
    ids, gmail_ok = [], False
    if call:
        naptar(call)
        ids, gmail_ok = gmail(call, state)
    yahoo()
    kanban()
    nyitas(state)
    emoji()
    atomic_write(PENDING, {"gathered_at": gathered_at, "ids": ids, "gmail_ok": gmail_ok, "new": uj_meta})
    print("HIBAK: %s" % ("; ".join(hibak) if hibak else "nincs"))
    print("Zaras a kor vegen: python3 scripts/folyamatos-ellenorzes-kor.py --zaras [--nyitas-kerdezve]")
    return 3 if hibak else 0


if __name__ == "__main__":
    sys.exit(main())
