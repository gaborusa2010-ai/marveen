#!/usr/bin/env python3
"""
Put every workshop file on Drive, dated and in its own folder.

Folders are named "YYYY-MM-DD job", so Drive's own name sort puts them in the
order the work happened. The Telegram filenames are replaced with names that say
what the sheet IS: a photo called 1787919232131-AQAD1A1rG5z7iVB-.jpg tells the
owner nothing when he opens the folder on his phone six months from now.

Re-runnable: drive_up only creates what is not already there, so running this
again after the next job adds that job and leaves the rest alone.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive_up as d  # noqa: E402
import drive_invoices as inv  # noqa: E402

ROOT_NAME = inv.ROOT_NAME
M = os.path.join(HERE, "..", "..", "store", "muhely")

# (folder, [(source path, name on Drive)]), subfolder given as "sub/" prefix
JOBS = [
    # Invoices live TOGETHER, not one per job folder: the owner asked for it and
    # he is right, because an invoice is looked up by number and by tax year, not
    # by which job it came off. Dated names so the folder sorts by itself.
    (inv.FOLDER_NAME, inv.entries()),
    ("2026-08-24 Lyford Road - TV unit (Rm B-01)", [
        ("lyford-road/lyford-road-tv-unit-cutting-list.pdf", "Szabaslista v1.pdf"),
        ("lyford-road/lyford-road-cutting-list-v2.pdf", "Szabaslista v2 - kliens valtozas utan.pdf"),
        ("lyford-road/lyford-road-cutplan.pdf", "Vagasterv.pdf"),
        ("lyford-road/lyford-road-nesting.pdf", "Nesting - tablakiosztas.pdf"),
        ("lyford-road/lyford-road-drawing-review-transcript.pdf", "Rajz-atbeszeles atirat.pdf"),
        ("lyford-road/drawing-photos-p3/p3-01-teljes-lap.jpg", "Rajzok/P3 - teljes lap.jpg"),
        ("lyford-road/drawing-photos-p3/p3-02-front-elevation.jpg", "Rajzok/P3 - front elevation.jpg"),
        ("lyford-road/drawing-photos-p3/p3-03-plan-section-a.jpg", "Rajzok/P3 - plan es section A.jpg"),
        ("lyford-road/drawing-photos-p3/p3-04-details-titleblock.jpg", "Rajzok/P3 - details es titleblock.jpg"),
        ("lyford-road/drawing-photos/photo-1.jpg", "Rajzok/Korabbi rajzfoto 1.jpg"),
        ("lyford-road/drawing-photos/photo-2.jpg", "Rajzok/Korabbi rajzfoto 2.jpg"),
        ("lyford-road/drawing-photos/photo-3.jpg", "Rajzok/Korabbi rajzfoto 3.jpg"),
        ("lyford-road/drawing-photos/photo-4.jpg", "Rajzok/Korabbi rajzfoto 4.jpg"),
        ("lyford-road/drawing-photos/photo-5.jpg", "Rajzok/Korabbi rajzfoto 5.jpg"),
    ]),
    ("2026-08-28 Lyford Road - halószoba gardrob (Rm B-02)", [
        ("lyford-b02-wardrobes/b02-szabaslista.pdf", "Szabaslista.pdf"),
        ("lyford-b02-wardrobes/b02-vagasterv.pdf", "Vagasterv.pdf"),
        ("lyford-b02-wardrobes/b02-levezetes.pdf", "Levezetes - mit miert szamoltam.pdf"),
        # Added 2026-09-20 by --hiany, which is what it is for: the G-06 job had
        # its part drawings on the list and this older one did not.
        ("lyford-b02-wardrobes/b02-part-drawings.pdf", "Alkatreszrajzok.pdf"),
        ("lyford-b02-wardrobes/00-amit-a-rajzrol-olvasok.md", "Amit a rajzrol olvasok.md"),
        ("lyford-b02-wardrobes/drawing-photos/1787919231965-AQAD0w1rG5z7iVB-.jpg",
         "Rajzok/006 - teljes lap.jpg"),
        ("lyford-b02-wardrobes/drawing-photos/1787919232131-AQAD1A1rG5z7iVB-.jpg",
         "Rajzok/006 - plan, section A, front elevation.jpg"),
        ("lyford-b02-wardrobes/drawing-photos/1787919232221-AQAD1Q1rG5z7iVB-.jpg",
         "Rajzok/006 - sections B C D E es location plan.jpg"),
        ("lyford-b02-wardrobes/drawing-photos/1787919232038-AQAD0g1rG5z7iVB-.jpg",
         "Rajzok/007 - details, teljes lap.jpg"),
        ("lyford-b02-wardrobes/drawing-photos/1787919232319-AQAD1w1rG5z7iVB-.jpg",
         "Rajzok/007 - details 1-4 kozelrol.jpg"),
        ("lyford-b02-wardrobes/drawing-photos/1787919232438-AQAD1g1rG5z7iVB-.jpg",
         "Rajzok/007 - details 4-6 kozelrol.jpg"),
    ]),
    ("2026-09-11 Lyford Road - haloszoba-dolgozo gardrob (G-06)", [
        ("lyford-g06-study-wardrobe/g06-szabaslista.pdf", "Szabaslista.pdf"),
        ("lyford-g06-study-wardrobe/g06-vagasterv.pdf", "Vagasterv.pdf"),
        ("lyford-g06-study-wardrobe/g06-levezetes.pdf", "Levezetes - mit miert szamoltam.pdf"),
        ("lyford-g06-study-wardrobe/g06-part-drawings.pdf", "Alkatreszrajzok.pdf"),
        ("lyford-g06-study-wardrobe/00-amit-a-rajzrol-olvasok.md", "Amit a rajzrol olvasok.md"),
        ("lyford-g06-study-wardrobe/drawing-photos/01-teljes-lap.jpg",
         "Rajzok/008 - teljes lap.jpg"),
        ("lyford-g06-study-wardrobe/drawing-photos/02-bal-fel-plan-sectionA-front.jpg",
         "Rajzok/008 - plan, section A, front elevation.jpg"),
        ("lyford-g06-study-wardrobe/drawing-photos/03-jobb-fel-side-sectionBC-titleblock.jpg",
         "Rajzok/008 - side elevation, sections B C es titleblock.jpg"),
    ]),
    ("Referencia - muhely", [
        ("sablon-otletek.md", "Sablon-otletek.md"),
    ]),
]


def hianyzo_pdfek():
    """PDFs under store/muhely that the JOBS list below does not name.

    WHY THIS EXISTS. drive_invoices.py was written because a hand-maintained
    list had gone stale without a word: the Szamlak folder held one invoice
    while four sat on disk. The invoices were fixed by generating that part of
    the list. The rest of JOBS is STILL hand-maintained, and on 2026-09-20 it
    had gone stale in exactly the same way: `lyford-g06-study-wardrobe/
    g06-part-drawings.pdf` was on the list, and its older twin
    `lyford-b02-wardrobes/b02-part-drawings.pdf` was not. Somebody added the
    new job and did not go back.

    This does NOT upload anything and does not decide anything. Naming a file
    on Drive is a judgement ("Szabaslista v2 - kliens valtozas utan.pdf"), and
    that is worth keeping human. What was never worth keeping human is
    NOTICING. A list that cannot report its own gaps will always drift.

    PDFs only, on purpose: they are the finished documents the owner opens on
    his phone. Intermediate JSON and raw photos are not the question here.
    """
    named = set()
    for _, files in JOBS:
        for rel, _target in files:
            named.add(os.path.normpath(rel))
    out = []
    for dirpath, _dirs, files in os.walk(M):
        for f in sorted(files):
            if not f.lower().endswith(".pdf"):
                continue
            rel = os.path.normpath(os.path.relpath(os.path.join(dirpath, f), M))
            # The invoices are generated into the list by inv.entries(), so they
            # are never "missing" -- but they are also not in `named` until that
            # call has run, which it has by the time JOBS is built.
            if rel not in named:
                out.append(rel)
    return out


def main():
    if "--hiany" in sys.argv:
        miss = hianyzo_pdfek()
        if not miss:
            print("Minden PDF szerepel a listan.")
            return 0
        # WHAT THIS DOES NOT SAY, and it was wrong here for ten minutes on
        # 2026-09-20: "not on the list" is not "not on Drive". The first
        # wording claimed the second, and the very first file it named
        # (b02-part-drawings.pdf) turned out to be up there already -- put()
        # came back with created=False. The list is a MAINTENANCE question:
        # what the sync will keep up to date from now on. Whether a copy
        # happens to sit there from an older run or a hand upload is a
        # different question, and this tool does not ask Drive at all.
        print(f"{len(miss)} PDF NINCS a szinkron-listan, tehat a szinkron NEM "
              f"tartja karban oket (fent lehetnek egy korabbi futasbol):")
        for rel in miss:
            print(f"  {rel}")
        print("\nAmelyik a gazdanak szol, vedd fel a JOBS listaba egy beszedes "
              "nevvel. Amelyik belso vagy proba, azt hagyd -- de akkor is dontes "
              "legyen, ne feledes.")
        return 1
    root, made = d.mkdir(ROOT_NAME)
    print(f"{'letrehozva' if made else 'mar letezett'}: {ROOT_NAME}\n")
    n_new = n_skip = 0
    for folder, files in JOBS:
        fid, made = d.mkdir(folder, root)
        print(f"[{'uj' if made else 'megvolt'}] {folder}")
        subs = {}
        # An invoice is identified by its NUMBER, not by the name string we happen
        # to build today. put() matches on the exact name, so a name change would
        # upload a SECOND copy of the same invoice instead of updating it -- and a
        # folder holding 10011 twice under two dates is worse than a wrong date.
        # Measured 2026-09-17: 10011 sits on Drive as "2026-08-24 ..." while the
        # PDF itself prints 25 Aug, so this is not hypothetical.
        if folder == inv.FOLDER_NAME:
            files = inv.keep_existing_names(files, fid)
        for rel, target in files:
            src = os.path.normpath(os.path.join(M, rel))
            if not os.path.exists(src):
                print(f"    HIANYZIK, kihagyva: {rel}")
                continue
            parent, name = fid, target
            if "/" in target:
                sub, name = target.split("/", 1)
                if sub not in subs:
                    subs[sub], _ = d.mkdir(sub, fid)
                parent = subs[sub]
            _, created = d.put(src, parent, name)
            n_new += created
            n_skip += (not created)
            print(f"    {'fel' if created else 'fr '} {target}")
    print(f"\n{n_new} uj fajl, {n_skip} frissitve.")
    print(f"Gyoker mappa: https://drive.google.com/drive/folders/{root}")


if __name__ == "__main__":
    sys.exit(main())
