#!/usr/bin/env python3
"""Tartja a kozos memoria-indexet a betoltesi hatar ALATT, magatol.

MIERT: a MEMORY.md-t a harness egy meret-hataron CSONKOLJA, es a hianyzo sorok
helyen SEMMILYEN jel nincs -- aki a lapot olvassa, nem latja, hogy csonka.
2026-09-09-en Cody merte ki eloszor (27 197 karakter a 24 986-os hatarnal, 13
bejegyzes veszett el, mind a legujabb). A vagas HALASZTAS: aznap 02:13-kor mar
levagta valaki, es tiz uj bejegyzes alatt ujra tullepte. Kartya: acb30fbe.

EZ A SCRIPT NEM TOROL. Egy index-SORT mozgat at a MEMORY-archivum.md-be; a
lapFAJL a helyen marad es keresessel ugyanugy megtalalhato. Egy archivalt lap
sora barmikor visszateheto, a fajlhoz nem kell nyulni.

A SORREND, amiben archival (a legkevesbe fajotol a legjobban fajoig):
  1. reference          -- kulso mutatok, keresessel elojonnek
  2. lezart project     -- a description LEZARVA-val kezdodik
  3. regi project       -- N napja nem valtozott
Csak annyit mozgat, amennyi a cel-meret alá vitelehez kell, es SOHA nem nyul
`user` vagy `feedback` tipusu laphoz: azok mondjak meg, ki a gazda es hogyan
kell dolgozni.

KET PLAFON VAN, ES A SZUK KERESZTMETSZET A SOR-SZAM (Anorak merte a harness
2.1.267 kodjabol, 2026-09-10; a MEMORY.md fejlece orzi):
  200 SOR es 25 000 KARAKTER (JS .length), MIND A KETTO a HTML-kommentek NELKUL --
  a betolto a kommentet kiszedi, MIELOTT szamolna. Vagasnal az ELSO 200 sor marad,
  a fajl VEGE esik ki.

EZT ELoSZOR EN MAGAM MERTEM ROSSZUL, 2026-09-19-en: a TELJES fajlt merlegeltem
(fejleccel egyutt) egy 24 986-os bajt-hatarhoz, es 2 433 karakteres tullepest
jelentettem. A valodi tullepes 218 KARAKTER volt -- es a kotoelem nem a karakter,
hanem a 217 > 200 SOR. A kovetkeztetes (tullepve, a fajl vege esik ki) igaz maradt,
de rossz alapon allt, es egy rossz alapra a kovetkezo ember ra fog epiteni: a
"rovidebb sorok" javaslatom KARAKTERT nyer es SORT NEM, tehat a szuk korlatot nem
mozditja. Ezert szamol ez a script MIND A KETToT, es a SORT nezi elsonek.
"""
import os, re, glob, time, sys, argparse

def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_SLUG = project_slug(REPO)
M = os.path.join(os.path.expanduser("~"), ".claude", "projects", PROJECT_SLUG, "memory")
LIMIT_LINES = 200
LIMIT_CHARS = 25000
# Cel: a plafon 80 szazaleka, hogy a napi 10-15 uj bejegyzes ne vigye at azonnal.
TARGET_LINES = int(LIMIT_LINES * 0.80)   # 160
TARGET_CHARS = int(LIMIT_CHARS * 0.80)   # 20000
OLD_PROJECT_DAYS = 14


def loaded_size(path):
    """Amit a betolto TENYLEG lat: HTML-komment nelkul, ures sorok nelkul."""
    s = open(path, encoding="utf-8").read()
    body = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    lines = [l for l in body.split("\n") if l.strip()]
    idx = [l for l in lines if l.startswith("- [")]
    return len(idx), len("\n".join(lines))

def meta(path):
    s = open(path, encoding="utf-8", errors="ignore").read()
    t = re.search(r"^\s*type:\s*(\S+)", s, re.M)
    d = re.search(r"^description:\s*(.+)$", s, re.M)
    return ((t.group(1) if t else "").strip(),
            (d.group(1) if d else "").strip())

def rank(fname, age_days):
    """Kisebb szam = elobb archivalando. None = SOHA ne archivald."""
    t, desc = meta(fname)
    if t == "reference":
        return (0, -age_days)
    if t == "project":
        if desc.upper().startswith(("LEZARVA", "LEZÁRVA")):
            return (1, -age_days)
        if age_days >= OLD_PROJECT_DAYS:
            return (2, -age_days)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="tenylegesen irjon; enelkul csak jelent")
    a = ap.parse_args()
    os.chdir(M)
    idx_path, arc_path = "MEMORY.md", "MEMORY-archivum.md"
    n_lines, n_chars = loaded_size(idx_path)
    print(f"betoltodo allapot: {n_lines} sor (plafon {LIMIT_LINES}), "
          f"{n_chars} karakter (plafon {LIMIT_CHARS})")
    if n_lines <= TARGET_LINES and n_chars <= TARGET_CHARS:
        print(f"cel: {TARGET_LINES} sor / {TARGET_CHARS} karakter -- nincs teendo")
        return 0

    lines = open(idx_path, encoding="utf-8").read().split("\n")
    head, entries = [], []
    for l in lines:
        m = re.search(r"\]\(([^)]+\.md)\)", l)
        if l.startswith("- [") and m:
            entries.append((l, m.group(1)))
        else:
            head.append(l)

    now = time.time()
    cands = []
    for line, f in entries:
        if not os.path.exists(f):
            continue
        r = rank(f, (now - os.path.getmtime(f)) / 86400)
        if r is not None:
            cands.append((r, line, f))
    cands.sort(key=lambda x: x[0])

    move, cur_l, cur_c = [], n_lines, n_chars
    for _, line, f in cands:
        if cur_l <= TARGET_LINES and cur_c <= TARGET_CHARS:
            break
        move.append((line, f))
        cur_l -= 1
        cur_c -= len(line) + 1

    if not move:
        print(f"{n_lines} sor / {n_chars} karakter > cel, de NINCS archivalhato jelolt "
              f"(csak user/feedback maradt). Ez emberi dontest kivan: kartya acb30fbe.")
        return 2

    print(f"MEMORY.md {n_lines} sor/{n_chars} kar -> {cur_l} sor/{cur_c} kar "
          f"(cel {TARGET_LINES}/{TARGET_CHARS}), {len(move)} sor mozogna:")
    for line, f in move:
        print(f"  {f}")
    if not a.apply:
        print("\n(csak jelentes -- `--apply` kell az irashoz)")
        return 0

    moved = {f for _, f in move}
    keep = [l for l, f in entries if f not in moved]
    open(idx_path, "w", encoding="utf-8").write("\n".join(head + keep).rstrip("\n") + "\n")
    with open(arc_path, "a", encoding="utf-8") as fh:
        fh.write(f"\n<!-- {time.strftime('%Y-%m-%d %H:%M')} automatikus archivalas, "
                 f"{len(move)} sor, memory-index-guard.py -->\n")
        for line, _ in move:
            fh.write(line + "\n")

    new_l, new_c = loaded_size(idx_path)
    arc_refs = {m.group(1) for m in re.finditer(r"\]\(([^)]+\.md)\)", open(arc_path, encoding="utf-8").read())}
    keep_refs = {m.group(1) for m in re.finditer(r"\]\(([^)]+\.md)\)", open(idx_path, encoding="utf-8").read())}
    files = {f for f in glob.glob("*.md") if not f.startswith("MEMORY")}
    ok = (keep_refs | arc_refs) >= files and not (keep_refs & arc_refs)
    print(f"\nkesz: {new_l} sor / {new_c} karakter | aktiv {len(keep_refs)} | archivum {len(arc_refs)} | "
          f"halmaz-ellenorzes: {'rendben' if ok else 'ELTERES'}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
