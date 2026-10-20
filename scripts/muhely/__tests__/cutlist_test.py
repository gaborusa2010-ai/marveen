#!/usr/bin/env python3
"""Regression tests for cutlist.py stock handling.

Written 2026-09-01 during the fleet-wide audit, because the bug it pins down
(STOCKLABEL901) was a SILENT one: a single stock entry spread over several
materials copied the label too, so a 6 mm back panel printed under an
"18mm lap" heading. Nothing failed, nothing warned, and the workshop would have
read the heading. A fix without a test is how that comes back.

Run: python3 scripts/muhely/__tests__/cutlist_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutlist import _stock_table  # noqa: E402

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}\n        vart: {want!r}\n        kapott: {got!r}")
        FAILED.append(name)


def parts(*materials):
    return [{"name": f"p{i}", "w": 100, "h": 100, "qty": 1, "material": m}
            for i, m in enumerate(materials)]


def test_single_material_keeps_label():
    """One material, one stock entry: nothing is dropped and nothing is warned."""
    job = {"stock": {"w": 2440, "h": 1220, "label": "18mm lap", "price": 48.5}}
    table, substituted = _stock_table(job, parts("18mm", "18mm"))
    check("egy anyag: megmarad a label", table["18mm"].get("label"), "18mm lap")
    check("egy anyag: nincs figyelmeztetes", substituted, [])


def test_spread_drops_label():
    """Several materials off ONE stock entry: the label is a guess, so it goes.

    This is the actual bug. The heading then names the material itself, which
    cannot mislead: a 6 mm part can no longer print under an 18 mm heading.
    """
    job = {"stock": {"w": 2440, "h": 1220, "label": "18mm lap", "price": 48.5}}
    table, substituted = _stock_table(job, parts("18mm", "6mm"))
    check("szort: 18mm-nel nincs label", table["18mm"].get("label"), None)
    check("szort: 6mm-nel nincs label", table["6mm"].get("label"), None)
    check("szort: mindket anyag jelentve", sorted(substituted), ["18mm", "6mm"])


def test_spread_keeps_size_and_price():
    """The size and price still carry over -- dropping them would silently
    produce a zero cost, which is a worse lie than a copied one. They are
    reported instead, which is what `substituted` is for."""
    job = {"stock": {"w": 2440, "h": 1220, "label": "18mm lap", "price": 48.5}}
    table, _ = _stock_table(job, parts("18mm", "6mm"))
    check("szort: meret atjon", (table["6mm"]["w"], table["6mm"]["h"]), (2440, 1220))
    check("szort: ar atjon", table["6mm"]["price"], 48.5)


def test_explicit_list_per_material():
    """A proper per-material list keeps every label and warns about nothing."""
    job = {"stock": [
        {"material": "18mm", "w": 2440, "h": 1220, "label": "18mm nyir retegelt"},
        {"material": "6mm", "w": 2440, "h": 1220, "label": "6mm hatlap"},
    ]}
    table, substituted = _stock_table(job, parts("18mm", "6mm"))
    check("lista: 18mm label", table["18mm"]["label"], "18mm nyir retegelt")
    check("lista: 6mm label", table["6mm"]["label"], "6mm hatlap")
    check("lista: nincs figyelmeztetes", substituted, [])


def test_material_missing_from_list_is_reported():
    """A material with no entry of its own falls back -- and says so."""
    job = {"stock": [
        {"material": "18mm", "w": 2440, "h": 1220, "label": "18mm nyir retegelt"},
    ]}
    table, substituted = _stock_table(job, parts("18mm", "6mm"))
    check("hianyzo anyag: van tabla", table["6mm"]["w"], 2440)
    check("hianyzo anyag: jelentve", substituted, ["6mm"])


def _run(job, *extra):
    """Run cutlist.py as a caller would, on a throwaway job file."""
    import json
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "job.json")
        with open(path, "w") as fh:
            json.dump(job, fh)
        args = [a.replace("{d}", d) for a in extra]
        r = subprocess.run([sys.executable, os.path.join(here, "cutlist.py"), path, *args],
                           capture_output=True, text=True)
        pdf_written = any(a.endswith(".pdf") and os.path.exists(a) for a in args)
        return r.returncode, r.stdout, pdf_written


def test_unplaced_part_is_not_success():
    """A part that fits no sheet must not exit 0 (#408, Cody, msg 7021).

    The report still has to come out: the workshop needs to see WHICH part
    is missing, so the exit code signals it and the output keeps it.
    """
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "ok", "w": 500, "h": 400, "qty": 1},
                     {"name": "oriasi", "w": 3000, "h": 600, "qty": 1}]}
    rc, out, _ = _run(job)
    check("nem fer: szoveges mod exit 4", rc, 4)
    check("nem fer: a darab neve kiirva", "oriasi" in out and "NEM FER RA" in out, True)
    rc, out, _ = _run(job, "--json")
    check("nem fer: json mod exit 4", rc, 4)
    check("nem fer: json unplaced", '"oriasi"' in out, True)
    rc, _, pdf = _run(job, "--pdf", "{d}/out.pdf")
    check("nem fer: pdf mod exit 4", rc, 4)
    check("nem fer: a pdf ettol meg elkeszul", pdf, True)


def test_everything_fits_is_zero():
    """Negative control: the same run without the oversized part stays 0."""
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "ok", "w": 500, "h": 400, "qty": 2}]}
    rc, _, _ = _run(job)
    check("minden fer: szoveges mod exit 0", rc, 0)
    rc, _, _ = _run(job, "--json")
    check("minden fer: json mod exit 0", rc, 0)


def test_half_mm_survives_print():
    """A 0.5 mm size must reach the page as written (2026-10-17, Forgacs).

    The text report and the PDF printed sizes with ':.0f'. Python rounds a half
    to the EVEN neighbour, so 523.5 came out as 524 and 2115.5 as 2116 (0.5 too
    big), 1856.5 as 1856 (0.5 too small). The delivered Lyford Road nesting PDF
    (2026-08-27) shows "524x374" and "500x346" for the 523.5 door panel and the
    499.5 shelf. The input JSON was right; only the print was wrong.
    """
    import json
    import subprocess
    import tempfile
    from cutlist import mm
    check("mm: 523.5", mm(523.5), "523.5")
    check("mm: 2115.5", mm(2115.5), "2115.5")
    check("mm: 564.0 egesz marad", mm(564.0), "564")
    check("mm: 499.75 nem kerekit", mm(499.75), "499.75")

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "ajto", "w": 523.5, "h": 374, "qty": 1},
                     {"name": "oldal", "w": 569, "h": 1856.5, "qty": 1},
                     {"name": "nagyajto", "w": 590, "h": 2115.5, "qty": 1}]}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "job.json")
        with open(path, "w") as fh:
            json.dump(job, fh)
        pdf = os.path.join(d, "out.pdf")
        r = subprocess.run([sys.executable, os.path.join(here, "cutlist.py"), path, "--pdf", pdf],
                           capture_output=True, text=True)
        txt = subprocess.run([sys.executable, os.path.join(here, "pdftext.py"), pdf],
                             capture_output=True, text=True).stdout
    for want in ("523.5", "1856.5", "2115.5"):
        check(f"szoveg: {want} betu szerint", want in r.stdout, True)
        check(f"pdf: {want} betu szerint", want in txt, True)
    check("szoveg: nincs 524/1856 /2116", any(b in r.stdout for b in ("524 ", "x 1856 ", "2116")), False)
    check("pdf: a lab VAGASI meretet mond", "VAGASI meret" in txt and "keszmeret" not in txt, True)


def _pdf_text(job):
    import json
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "job.json")
        with open(path, "w") as fh:
            json.dump(job, fh)
        pdf = os.path.join(d, "out.pdf")
        subprocess.run([sys.executable, os.path.join(here, "cutlist.py"), path, "--pdf", pdf],
                       capture_output=True, text=True)
        return subprocess.run([sys.executable, os.path.join(here, "pdftext.py"), pdf],
                              capture_output=True, text=True).stdout


def test_pdf_has_grain_and_edging_columns():
    """The shop PDF carries SZALIRANY and ELSZALAG per part (#611, 2026-10-20).

    The owner's convention: without those two columns the cut list is not
    finished. The sheet pages only ever printed name and size.
    """
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "oldal", "w": 450, "h": 800, "qty": 2, "grain": "h",
                      "edging": "nincs"},
                     {"name": "polc", "w": 1162, "h": 443, "qty": 2, "grain": "w",
                      "edging": "2mm sz:0 m:1"}]}
    txt = _pdf_text(job)
    check("pdf: SZALIRANY oszlop", "SZALIRANY" in txt, True)
    check("pdf: ELSZALAG oszlop", "ELSZALAG" in txt, True)
    check("pdf: allo darab a MAGAS menten", "a MAGAS menten" in txt, True)
    check("pdf: fekvo darab a SZELES menten", "a SZELES menten" in txt, True)
    check("pdf: az elszalag erteke latszik", "2mm sz:0 m:1" in txt, True)
    check("pdf: kitoltott lista nem 'nincs kesz'", "NINCS KESZ" in txt, False)


def test_pdf_flags_missing_grain_and_edging():
    """A missing value prints as missing, never as a blank cell."""
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "ajto", "w": 500, "h": 700, "qty": 1}]}
    txt = _pdf_text(job)
    check("pdf: hianyzo elszalag -> a lista nincs kesz", "A LISTA NINCS KESZ" in txt, True)
    check("pdf: hianyzo szalirany kiirva", "NINCS MEGADVA" in txt, True)


if __name__ == "__main__":
    print("cutlist.py stock-kezeles")
    test_single_material_keeps_label()
    test_spread_drops_label()
    test_spread_keeps_size_and_price()
    test_explicit_list_per_material()
    test_material_missing_from_list_is_reported()
    print("cutlist.py kilepesi kod")
    test_unplaced_part_is_not_success()
    test_everything_fits_is_zero()
    print("cutlist.py meret-kiiras")
    test_half_mm_survives_print()
    print("cutlist.py szabaslista oldal")
    test_pdf_has_grain_and_edging_columns()
    test_pdf_flags_missing_grain_and_edging()
    print()
    if FAILED:
        print(f"BUKOTT: {len(FAILED)} -- {', '.join(FAILED)}")
        sys.exit(1)
    print("Mind atment.")
