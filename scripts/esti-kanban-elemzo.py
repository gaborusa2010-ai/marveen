#!/usr/bin/env python3
"""Evening kanban close: the five categories, with the filters already applied.

Written to a file rather than pasted inline because the owner approved that change
on 2026-09-20 (Telegram msg 3279): a third of a session's context turned out to be
the commands I type, and an inline script costs its full length every single evening.

Reads the board JSON on stdin or from a path. Prints the five categories.
The owner-assignee filter and the `GAZDA-DONTES` / `FELRETEVE` / `VAR:` searches are
applied HERE, not left to the reader -- that is the whole point of the 2026-09-20
correction (Telegram msg 3105).
"""
import json, sys, time

GAZDA = {"Fejes Istvan Gabor", "gazda"}
JELOLOK = ("GAZDA-DONTES", "FELRETEVE", "VAR:")


def kor(c, now):
    st = c.get("last_status_at") or c.get("updated_at") or now
    return (now - st) / 3600.0


def felretett(c):
    d = (c.get("description") or "")
    return [j for j in JELOLOK if j in d]


def audit(rows, now, last, state):
    """A 4 oras audit nezete. Mas, mint az esti zaras: itt a LAST-hoz kepest merunk,
    es a 3b-re csillapitas all. A ket nezet szandekosan egy fajlban van, mert a
    szurok (gazda-assignee, jelolok) kozosek -- ket masolat ket helyen driftelne."""
    print(f"LAST_AUDIT: {time.strftime('%m-%d %H:%M', time.localtime(last)) if last else 'nincs'}\n")

    arch = [c for c in rows if c.get("status") == "done" and not c.get("archived_at")
            and (c.get("updated_at") or 0) < now - 7 * 86400]
    print(f"2. ARCHIVALANDO (7+ napos done): {len(arch)}")
    for i in arch[:10]:
        print("   ", i["id"], "|", (i.get("title") or "")[:50])

    beakadt = [c for c in rows if c.get("status") == "in_progress" and not c.get("archived_at")
               and (c.get("last_status_at") or c.get("updated_at") or 0) < last]
    print(f"\n3. BEAKADT JELOLT: {len(beakadt)}")
    for c in sorted(beakadt, key=lambda x: kor(x, now), reverse=True):
        print(f"   {c['id']} | {(c.get('assignee') or '-'):<9} | {kor(c,now):6.1f} h | {(c.get('title') or '')[:44]}")

    rep = state.get("waiting_unblocked_reported") or {}
    uj_w = []
    for c in rows:
        if c.get("status") != "waiting" or c.get("archived_at"): continue
        if [b for b in (c.get("blockers") or []) if b.get("status") != "done"]: continue
        if (c.get("assignee") or "").strip() in GAZDA: continue
        st = c.get("last_status_at") or c.get("updated_at") or now
        if rep.get(c["id"]) == st: continue
        uj_w.append((c["id"], c.get("assignee") or "-", kor(c, now), (c.get("title") or "")[:44], st))
    print(f"\n3b. WAITING nyitott blokkolo nelkul, a GAZDA-szuro ES a csillapitas UTAN: {len(uj_w)}")
    for i, a, h, t, _ in uj_w:
        print(f"   {i} | {a:<9} | {h:6.1f} h | {t}")

    deleg = [c for c in rows if c.get("status") in ("in_progress", "waiting", "planned")
             and not c.get("archived_at") and not (c.get("assignee") or "").strip()]
    print(f"\n6. DELEGALATLAN: {len(deleg)}")
    return arch, beakadt, uj_w


def main():
    args = [a for a in sys.argv[1:] if a != "--audit"]
    audit_mod = "--audit" in sys.argv
    p = args[0] if args else None
    rows = json.load(open(p) if p else sys.stdin)
    now = int(time.time())
    if audit_mod:
        try:
            repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            st = json.load(open(os.path.join(repo, "store", "kanban-audit-state.json")))
        except Exception:
            st = {}
        audit([c for c in rows if not c.get("archived_at")], now, st.get("last_audit_at") or 0, st)
        return
    el = [c for c in rows if not c.get("archived_at")]

    print(f"AKTIV KARTYA: {len(el)}\n")

    print("1. BESZORULT (waiting, nincs nyitott blokkolo, NEM a gazdae, nincs jelolve)")
    jeloltek = []
    for c in el:
        if c.get("status") != "waiting": continue
        if [b for b in (c.get("blockers") or []) if b.get("status") != "done"]: continue
        if (c.get("assignee") or "").strip() in GAZDA: continue
        if felretett(c): continue
        jeloltek.append(c)
    for c in sorted(jeloltek, key=lambda x: -kor(x, now))[:6]:
        print(f"   {c['id']} | {(c.get('assignee') or '-'):<9} | {kor(c,now)/24:5.1f} nap | {(c.get('title') or '')[:46]}")
    print(f"   (osszesen {len(jeloltek)})\n")

    print("2. MEGALLT (in_progress, 48+ ora)")
    m = [c for c in el if c.get("status") == "in_progress" and kor(c, now) >= 48]
    for c in sorted(m, key=lambda x: -kor(x, now))[:3]:
        print(f"   {c['id']} | {(c.get('assignee') or '-'):<9} | {kor(c,now)/24:5.1f} nap | {(c.get('title') or '')[:46]}")
    print(f"   (osszesen {len(m)})\n")

    print("3. ELFELEJTETT (planned, high/urgent, 7+ nap)")
    f = [c for c in el if c.get("status") == "planned"
         and (c.get("priority") in ("high", "urgent")) and kor(c, now) >= 168]
    for c in sorted(f, key=lambda x: -kor(x, now))[:3]:
        print(f"   {c['id']} | {c.get('priority'):<6} | {kor(c,now)/24:5.1f} nap | {(c.get('title') or '')[:46]}")
    print(f"   (osszesen {len(f)})\n")

    print("4. A GAZDA SAJAT KARTYAI (3+ nap)")
    g = [c for c in el if (c.get("assignee") or "").strip() in GAZDA
         and kor(c, now) >= 72 and c.get("status") != "done"]
    for c in sorted(g, key=lambda x: -kor(x, now))[:4]:
        jel = ",".join(felretett(c)) or "-"
        print(f"   {c['id']} | {kor(c,now)/24:5.1f} nap | {jel:<13} | {(c.get('title') or '')[:42]}")
    print(f"   (osszesen {len(g)})\n")

    print("5. AMI MA MOZDULT")
    ma = time.strftime("%Y-%m-%d")
    mai = [c for c in el if time.strftime("%Y-%m-%d", time.localtime(
        c.get("last_status_at") or 0)) == ma]
    for c in sorted(mai, key=lambda x: -(x.get("last_status_at") or 0))[:8]:
        print(f"   {c['id']} | {c.get('status'):<12} | {(c.get('title') or '')[:52]}")
    print(f"   (osszesen {len(mai)})")


if __name__ == "__main__":
    main()
