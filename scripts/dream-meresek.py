#!/usr/bin/env python3
"""The Dream Engine's nightly measurements, in one place.

Written to a file on 2026-09-21 rather than pasted inline each night, for the reason
the owner approved hours earlier (Telegram msg 3279): a third of a session's context
turned out to be the commands I type. This script runs every night; inline it would
cost its own length every night, for ever.

Every query here has been RUN, not just written -- the page it comes from records four
separate cases of a command that named something which never existed (`daily_log` for
`daily_logs`, `last_status_at` as a column, `pinned:` in skills, a `scripts/` subdir
proxy). This file exists partly so the fifth one is caught once instead of nightly.
"""
import sqlite3, time, sys, json, os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO, "store", "claudeclaw.db")
NAP = 86400


def q(sql, args=()):
    c = sqlite3.connect(DB).cursor()
    c.execute(sql, args)
    return c.fetchall()


def main():
    t24 = int(time.time()) - NAP
    print("=== 1. EMLEKEK, 24h, MINDEN kategoria ===")
    sorok = q("SELECT agent_id,category,content,keywords FROM memories "
              "WHERE created_at > ? ORDER BY agent_id,created_at", (t24,))
    print(f"osszesen: {len(sorok)}")
    for a, cat, cont, kw in sorok:
        print(f"  [{a}/{cat}] {(cont or '')[:100]}")

    print("\n=== 1b. KATEGORIA-MEGOSZLAS (jelentendo adat, nem kapcsolo) ===")
    print(dict(q("SELECT category,count(*) FROM memories WHERE created_at > ? "
                 "GROUP BY category", (t24,))))

    print("\n=== 2. VEKTORIZALTSAG ===")
    print(q("SELECT COUNT(*),COUNT(embedding) FROM memories")[0])

    print("\n=== 2b. ELAVULT HOT (>7 nap erintetlen) ===")
    h = q("SELECT id,substr(content,1,70),accessed_at FROM memories "
          "WHERE category='hot' AND accessed_at < ?", (int(time.time()) - 7 * NAP,))
    print(f"{len(h)} db")
    for r in h[:10]:
        print("  ", r)

    print("\n=== 3. NAPI NAPLO, 7 nap (tabla: daily_logs, TOBBES) ===")
    print(q("SELECT agent_id,count(*) FROM daily_logs WHERE created_at > ? "
            "GROUP BY agent_id", (int(time.time()) - 7 * NAP,)))

    print("\n=== 3b. NYITOTT KARTYAK projekt es prioritas szerint ===")
    k = q("SELECT id,title,status,project,priority,assignee FROM kanban_cards "
          "WHERE status IN ('planned','in_progress','waiting') AND archived_at IS NULL "
          "ORDER BY project,priority DESC")
    print(f"{len(k)} nyitott")
    proj = {}
    for _id, cim, st, pr, prio, asg in k:
        proj.setdefault(pr or "(nincs projekt)", []).append((prio, st, cim, asg))
    for p, items in sorted(proj.items(), key=lambda x: -len(x[1]))[:6]:
        print(f"  {p}: {len(items)} kartya")

    print("\n=== 5. SKILL-HASZNALAT, SESSIONONKENT (nem hivasonkent) ===")
    s = q("SELECT skill_name, COUNT(DISTINCT session_id) AS sessions, MAX(created_at) "
          "FROM skill_usage GROUP BY skill_name ORDER BY sessions DESC")
    elso = q("SELECT MIN(created_at) FROM skill_usage")[0][0]
    print("a meres kezdete:", time.strftime("%Y-%m-%d", time.localtime(elso)) if elso else "nincs adat")
    print(f"{len(s)} skillnek VAN skill_usage sora")
    for r in s[:12]:
        print(f"  {r[0]:<34}{r[1]:>3} session   utoljara {time.strftime('%m-%d', time.localtime(r[2]))}")

    # A tabla a skill SCRIPTJENEK futtatasat nem latja: a hook (scripts/hooks/
    # skill-usage-capture.py) ket esemenyt ir, a `Skill` tool hivasat es egy
    # SKILL.md Read-jet. Ezert a "nincs sora" halmaz NEM azonos a "senki nem
    # hasznalja" halmazzal -- a kulonbseget a gazda 2026-09-06-i dontese es az
    # 519-es warm emlek mar rogzitette, MEGIS kikerult egy ejszakai riportba
    # rossz cimkevel (2026-09-21). Ezert all a cimke MOST MAR A SZAMMAL EGY
    # SOROZATBAN, es ezert fut vele egy allando kontroll: ha egy bizonyithatoan
    # hasznalt skill a "nincs sora" listan all, a cimke maga a lelet.
    roster = set()
    for d in (os.path.expanduser("~/.claude/" + "skills"), os.path.join(REPO, ".claude", "skills")):
        try:
            roster |= {n for n in os.listdir(d) if not n.startswith(".")}
        except OSError:
            pass
    van_sora = {r[0] for r in s}
    nincs_sora = sorted(roster - van_sora)
    rogzitve_de_nincs_mappaja = sorted(van_sora - roster)
    print(f"\n{len(roster)} skill-konyvtar all a ket mappaban")
    print(f"{len(nincs_sora)} skillnek NINCS skill_usage sora")
    print('  A HELYES CIMKE: "nincs skill_usage sora", NEM "senki nem hasznalja".')
    print("  (A gazda dontese 2026-09-06 09:18, es az 519-es warm emlek.)")
    KONTROLL = ("fleet-helper", "google-hozzaferes")
    talalt = [k for k in KONTROLL if k in nincs_sora]
    if talalt:
        print(f"  KONTROLL ELSULT: {', '.join(talalt)} a listan all, pedig hasznaljuk.")
        print("  Tehat a szam a HOOK LEFEDETTSEGET meri, nem a skillek hasznossagat.")
    else:
        print("  KONTROLL: a ket ismerten hasznalt skill nincs a listan.")
    if rogzitve_de_nincs_mappaja:
        print(f"  {len(rogzitve_de_nincs_mappaja)} rogzitett nev nincs a nevsoron "
              f"(tehat a ket halmaz nevezoje sem kozos): {', '.join(rogzitve_de_nincs_mappaja[:8])}")


if __name__ == "__main__":
    sys.exit(main())
