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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive_up as d  # noqa: E402

ROOT_NAME = "Muhely - Fejes Istvan Gabor"
M = os.path.join(HERE, "..", "..", "store", "muhely")

# (folder, [(source path, name on Drive)]), subfolder given as "sub/" prefix
JOBS = [
    # Invoices live TOGETHER, not one per job folder: the owner asked for it and
    # he is right, because an invoice is looked up by number and by tax year, not
    # by which job it came off. Dated names so the folder sorts by itself.
    ("Szamlak", [
        ("invoices/10011-picagne.pdf", "2026-08-24 Szamla 10011 - Picagne.pdf"),
    ]),
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


def main():
    root, made = d.mkdir(ROOT_NAME)
    print(f"{'letrehozva' if made else 'mar letezett'}: {ROOT_NAME}\n")
    n_new = n_skip = 0
    for folder, files in JOBS:
        fid, made = d.mkdir(folder, root)
        print(f"[{'uj' if made else 'megvolt'}] {folder}")
        subs = {}
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
