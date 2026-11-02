#!/usr/bin/env python3
"""Shadow-mode email triage with the local Ollama model (card #460, 2efc3399).

Fetches mail since the last successful run (same window as the heartbeat), asks the local model REPORT/SILENT for each
message not seen before, and appends the verdict to store/helyi-triage-shadow.jsonl.
It never notifies anyone: the point is to compare its verdicts with what the
folyamatos-ellenorzes heartbeat actually reported, before switching over.
"""
import json, os, sys, time, urllib.request

ROOT = "/home/gabor/marveen"
sys.path.insert(0, os.path.join(ROOT, "scripts/muhely"))
from google_auth import call  # noqa: E402

MODEL = os.environ.get("TRIAGE_MODEL", "qwen3:8b")
SEEN = os.path.join(ROOT, "store/helyi-triage-seen.json")
LOG = os.path.join(ROOT, "store/helyi-triage-shadow.jsonl")
STATE = os.path.join(ROOT, "store/helyi-triage-state.json")
SYS = (
    "You triage emails for a self-employed UK carpenter. Answer with JSON "
    "{\"decision\": \"REPORT\" or \"SILENT\", \"reason\": short}. "
    "REPORT only if the owner must know or act soon: a client or person writing to him, "
    "calendar change, tax problem, deadline. "
    "SILENT for: newsletters, marketing (also from government or banks), promotions, credits, "
    "job alerts, statements ready, bank, payment and account-security notices, parcel/delivery/shipping notices, ANY auction mail "
    "(outbid, won, ending soon, results), cashback, courses. "
    "REPORT also: a human's reply or review on the owner's own pull request, and any notice "
    "that the owner's account or content was restricted, disabled or removed."
)
OWN = "gaborusa2010@gmail.com"
# Deterministic pre-rules (2026-10-17, after the 10-09..10-16 shadow comparison):
# the model missed replies in threads the owner wrote in, and flagged routine
# account notices the heartbeat rule keeps silent.
# 2026-10-31 (shadow comparison #3): newsletter/promo senders that made up the noise REPORTs.
QUIET_SENDERS = ("no-reply@accounts.google.com", "noreply-accounts@google.com",
                 "@learningcrypto.com", "totallymoney.com", "withplum.com", "axminstertools.com")
# 2026-11-02 (#460 comparison #4): the model let two personal mails from a gmail
# address go SILENT; a person writing from a consumer mailbox is never bulk mail.
CONSUMER_DOMAINS = ("@gmail.com", "@googlemail.com", "@yahoo.", "@hotmail.", "@outlook.",
                    "@live.", "@icloud.com", "@me.com", "@aol.", "@btinternet.com")
QUIET_SUBJECTS = ("did you recently sign into", "you've been outbid", "auction won:",
                  "auction results:", "auction remaining weight", "participated auction ending")


def rule(m, frm, subj):
    """Return (decision, reason) from a fixed rule, or None to ask the model."""
    labels = m.get("labelIds") or []
    if "SENT" in labels or OWN in frm.lower():
        return "SILENT", "rule: owner's own outgoing mail"
    if any(q in frm.lower() for q in QUIET_SENDERS) or any(q in subj.lower() for q in QUIET_SUBJECTS):
        return "SILENT", "rule: routine account notice"
    if any(d in frm.lower() for d in CONSUMER_DOMAINS):
        return "REPORT", "rule: a person writing from a consumer mailbox"
    try:
        t = call(f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{m['threadId']}",
                 params={"format": "minimal"})
        if any("SENT" in (x.get("labelIds") or []) for x in t.get("messages", []) if x["id"] != m["id"]):
            return "REPORT", "rule: reply in a thread the owner wrote in"
    except Exception:
        pass
    return None


def log(rec):
    rec["ts"] = int(time.time())
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def classify(frm, subj, snippet):
    body = json.dumps({
        "model": MODEL, "stream": False, "think": False, "format": "json",
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": f"From: {frm}\nSubject: {subj}\nSnippet: {snippet}"}],
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body,
                                 {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    d = json.loads(r["message"]["content"])
    return d.get("decision", "?"), d.get("reason", "")


def main():
    try:
        seen = set(json.load(open(SEEN)))
    except Exception:
        seen = set()
    # 2026-11-02 (#460): "newer_than:1h" lost 12 of 14 mails in the 10-30 outage;
    # search from the last successful run instead, like the heartbeat does.
    try:
        last = int(json.load(open(STATE))["last_ok"])
    except Exception:
        last = int(time.time()) - 3600
    started = int(time.time())
    try:
        msgs, page = [], None
        while len(msgs) < 500:
            params = {"q": f"after:{last - 300}", "maxResults": "100"}
            if page:
                params["pageToken"] = page
            r = call("https://gmail.googleapis.com/gmail/v1/users/me/messages", params=params)
            msgs += r.get("messages") or []
            page = r.get("nextPageToken")
            if not page:
                break
    except Exception as e:
        log({"error": f"gmail: {e}"})
        return 1
    new, failed = 0, False
    for m in msgs:
        if m["id"] in seen:
            continue
        try:
            d = call(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
                     params={"format": "metadata", "metadataHeaders": ["From", "Subject"]})
            h = {x["name"]: x["value"] for x in d["payload"]["headers"]}
            t = time.time()
            r = rule(d, h.get("From", ""), h.get("Subject", ""))
            dec, why = r or classify(h.get("From", ""), h.get("Subject", ""), d.get("snippet", "")[:300])
            log({"by": "rule" if r else "model", "id": m["id"], "from": h.get("From", ""), "subject": h.get("Subject", ""),
                 "decision": dec, "reason": why, "secs": round(time.time() - t, 2), "model": MODEL})
            seen.add(m["id"])
            new += 1
        except Exception as e:
            log({"id": m["id"], "error": str(e)})
            failed = True
    json.dump(sorted(seen)[-2000:], open(SEEN, "w"))
    if not failed:  # a failed mail stays in the next window
        json.dump({"last_ok": started}, open(STATE, "w"))
    log({"run": "ok", "fetched": len(msgs), "classified": new})
    return 0


if __name__ == "__main__":
    sys.exit(main())
