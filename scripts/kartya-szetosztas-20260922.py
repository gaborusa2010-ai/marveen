#!/usr/bin/env python3
"""One-off: hand 38 of marveen's cards to the four agents whose profile they fit.

The owner asked for it (Telegram msg 3525, 2026-09-22 19:25: "neked nagyon sok
kartyad van,nem tudsz atadni parat a csapatnak?"). Measured that evening: 142
open cards on the board, 113 of them marveen's, 105 planned and 107 low -- a
pile of findings written down and never handed out, not a workload.

Dry run by default. `--go` writes.

WHY A SCRIPT AND NOT 38 CURL CALLS: every move has to carry a comment saying WHO
moved it, WHEN, and on whose instruction, or the recipient opens a card that
silently changed owner overnight. Doing that by hand 38 times is where the
thirty-eighth one loses its comment.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "http://localhost:3420/api/kanban"
TOKEN = open("/home/gabor/marveen/store/.dashboard-token").read().strip()

# seq -> (agent, why this agent). The seq is what the owner sees on the
# dashboard; the hex id is our anchor and is looked up from the live board, not
# written down here, because a hardcoded id is the kind of label that quietly
# stops matching.
PLAN = {
    # anorak: research, market, fleet development, night shift
    4: "anorak", 5: "anorak", 67: "anorak", 77: "anorak", 78: "anorak",
    80: "anorak", 87: "anorak", 115: "anorak", 163: "anorak", 174: "anorak",
    176: "anorak", 284: "anorak", 285: "anorak", 291: "anorak", 301: "anorak",
    302: "anorak",
    # cody: code and the worklog app
    101: "cody", 114: "cody", 124: "cody", 130: "cody", 139: "cody",
    154: "cody", 185: "cody", 186: "cody", 215: "cody", 236: "cody",
    242: "cody", 286: "cody", 294: "cody",
    # forgacs: workshop, invoicing, furniture, HMRC
    71: "forgacs", 196: "forgacs", 212: "forgacs", 217: "forgacs",
    258: "forgacs", 288: "forgacs",
    # roxy: books, KDP, pictures
    31: "roxy", 81: "roxy", 287: "roxy",
}

COMMENT = (
    "ATADVA: marveen -> {agent}, 2026-09-22 este.\n"
    "\n"
    "A gazda kerese, Telegram msg 3525: \"neked nagyon sok kartyad van,nem tudsz "
    "atadni parat a csapatnak?\". A listat elotte megmutattam neki (msg 3543), es "
    "a szetosztas az o jovahagyasa utan indult.\n"
    "\n"
    "Miert hozzad kerult: {why}\n"
    "\n"
    "A kartya allapotat es prioritasat NEM valtoztattam meg, csak a gazdajat. Ha "
    "ugy latod, hogy nem a tied, told vissza marveen-re egy kommenttel -- az "
    "visszautasitas, nem hiba."
)

WHY = {
    "anorak": "kutatas, piac es flotta-fejlesztes, ami az ejszakai muszakod targya.",
    "cody": "kod es a munkanaplo-app, ami a te terepe.",
    "forgacs": "muhely, szamla, butor es HMRC, ami a te terepe.",
    "roxy": "konyv, KDP es kepanyag, ami a te terepe.",
}


def call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req) as r:
        return r.read().decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="actually write (default: dry run)")
    a = ap.parse_args()

    board = {c["seq"]: c for c in json.loads(call(API))}
    plan, refused = [], []
    for seq, agent in sorted(PLAN.items()):
        c = board.get(seq)
        if c is None:
            refused.append((seq, "nincs a tablan"))
            continue
        # Refuse anything that is not still mine: if someone moved it since the
        # list was drawn up, their move wins over this stale plan.
        if c.get("assignee") != "marveen":
            refused.append((seq, "mar nem marveene: %s" % c.get("assignee")))
            continue
        plan.append((seq, c["id"], agent, c["title"]))

    for seq, cid, agent, title in plan:
        print("#%-4d %-8s %-8s %s" % (seq, agent, cid, title[:58]))
    print("\natadando: %d   visszautasitva: %d" % (len(plan), len(refused)))
    for seq, reason in refused:
        print("  KIHAGYVA #%d -- %s" % (seq, reason))

    if not a.go:
        print("\nSZARAZ FUTAS. Iras: --go")
        return 0

    done, failed = 0, []
    for seq, cid, agent, title in plan:
        try:
            # The comment goes FIRST: if the assignee write then fails, the card
            # still carries the note saying what was attempted. The other order
            # leaves a silently moved card with no explanation on it.
            call("%s/%s/comments" % (API, cid), "POST",
                 {"author": "marveen",
                  "content": COMMENT.format(agent=agent, why=WHY[agent])})
            call("%s/%s" % (API, cid), "PUT", {"assignee": agent})
            done += 1
        except urllib.error.HTTPError as e:
            failed.append((seq, "%s %s" % (e.code, e.reason)))
        except Exception as e:  # noqa: BLE001
            failed.append((seq, str(e)))
        time.sleep(0.1)

    # Read back, because the PUT response does not return the fields it set.
    board = {c["seq"]: c for c in json.loads(call(API))}
    wrong = [(seq, board.get(seq, {}).get("assignee")) for seq, _, agent, _ in plan
             if board.get(seq, {}).get("assignee") != agent]
    print("\natadva: %d   hiba: %d   visszaolvasva rossz: %d" % (done, len(failed), len(wrong)))
    for seq, why in failed:
        print("  HIBA #%d: %s" % (seq, why))
    for seq, got in wrong:
        print("  VISSZAOLVASVA ROSSZ #%d: %s" % (seq, got))
    return 1 if (failed or wrong) else 0


if __name__ == "__main__":
    sys.exit(main())
