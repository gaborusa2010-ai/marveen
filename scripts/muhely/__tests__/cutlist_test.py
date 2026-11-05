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
    check("pdf: a lab VAGASI meretet mond", "VÁGÁSI méret" in txt and "keszmeret" not in txt, True)


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
    check("pdf: SZALIRANY oszlop", "SZÁLIRÁNY" in txt, True)
    check("pdf: ELSZALAG oszlop", "ÉLSZALAG" in txt, True)
    check("pdf: allo darab a MAGAS menten", "a MAGAS mentén" in txt, True)
    check("pdf: fekvo darab a SZELES menten", "a SZÉLES mentén" in txt, True)
    check("pdf: az elszalag erteke latszik", "2mm sz:0 m:1" in txt, True)
    check("pdf: kitoltott lista nem 'nincs kesz'", "NINCS KÉSZ" in txt, False)


def test_pdf_flags_missing_grain_and_edging():
    """A missing value prints as missing, never as a blank cell."""
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "ajto", "w": 500, "h": 700, "qty": 1}]}
    txt = _pdf_text(job)
    check("pdf: hianyzo elszalag -> a lista nincs kesz", "A LISTA NINCS KÉSZ" in txt, True)
    check("pdf: hianyzo szalirany kiirva", "NINCS MEGADVA" in txt, True)


def _curved_job(**door):
    """A curved door blank, its bending-form ribs and a fitted top (#747).

    The metric sample from the card: 800 wide opening, 100 deep arc, 18 mm,
    door front arc 853.5 finished, cut as a 900 x 760 blank.
    """
    d = {"name": "ives ajto", "w": 853.5, "h": 700, "qty": 1, "grain": "w",
         "edging": "nincs", "blank": {"w": 900, "h": 760}}
    d.update(door)
    return {"job": "t", "stock": {"w": 2440, "h": 1220},
            "parts": [d,
                      {"name": "sablon borda", "w": 803, "h": 140, "qty": 3, "grain": "w",
                       "edging": "nincs", "role": "jig"},
                      {"name": "teto", "w": 836, "h": 420, "qty": 1, "grain": "w",
                       "edging": "nincs", "fit": "install"}]}


def test_blank_is_nested_at_blank_size():
    """The sheet carries the RAW size: nesting the finished size cuts it short."""
    import json
    rc, out, _ = _run(_curved_job(), "--json")
    check("nyers: exit 0", rc, 0)
    res = json.loads(out)
    placed = [pl for m in res["materials"] for s in m["sheets"] for pl in s["parts"]]
    door = [pl for pl in placed if pl["name"].startswith("ives ajto")]
    check("nyers: a tablan '(nyers)' nevvel", [pl["name"] for pl in door], ["ives ajto (nyers)"])
    check("nyers: a tablan a nyers meret",
          sorted([door[0]["w"], door[0]["h"]]), [760.0, 900.0])
    check("nyers: json special.blank", res["special"]["blank"],
          [{"name": "ives ajto", "qty": 1, "blank": [900.0, 760.0], "finished": [853.5, 700.0]}])
    check("nyers: json special.jig", res["special"]["jig"], ["sablon borda"])
    check("nyers: json special.fit", res["special"]["fit_on_install"], ["teto"])


def test_blank_smaller_than_finished_stops():
    rc, _, _ = _run(_curved_job(blank={"w": 800, "h": 760}))
    check("nyers kisebb a kesznel: exit 3", rc, 3)


def test_bad_blank_stops():
    for bad in ({"w": 900}, {"w": "tbc", "h": 760}, "tbc", {}):
        rc, _, _ = _run(_curved_job(blank=bad))
        check(f"rossz blank {bad!r}: exit 3", rc, 3)


def test_unknown_role_and_fit_stop():
    rc, _, _ = _run(_curved_job(role="sablon"))
    check("ismeretlen role: exit 3", rc, 3)
    rc, _, _ = _run(_curved_job(fit="site"))
    check("ismeretlen fit: exit 3", rc, 3)


def test_oversized_blank_is_unplaced():
    """The finished size fits, the blank does not: that is still a missing part."""
    rc, out, _ = _run(_curved_job(blank={"w": 2600, "h": 760}))
    check("tul nagy nyers: exit 4", rc, 4)
    check("tul nagy nyers: nevvel jelezve", "ives ajto (nyers)" in out and "NEM FER RA" in out, True)


def test_special_sections_in_text_and_pdf():
    rc, out, _ = _run(_curved_job())
    check("szoveg: NYERS MERETRE VAGVA", "NYERS MÉRETRE VÁGVA" in out, True)
    check("szoveg: nyers -> kesz", "nyers 900 x 760  ->  kész 853.5 x 700" in out, True)
    check("szoveg: SABLON sor", "SABLON, nem bútor" in out and "sablon borda" in out, True)
    check("szoveg: BESZERELES UTAN SZABVA", "BESZERELES UTAN SZABVA" in out and "teto" in out, True)
    job = _curved_job(note="hajlithato MDF 2x9 mm, egymasra ragasztva")
    txt = _pdf_text(job)
    flat = " ".join(txt.split())
    check("pdf: a nyers sor VAGASI meretet mutat", "900 760" in flat, True)
    check("pdf: a kesz meret a megjegyzesben", "NYERS, kész 853.5x700" in flat, True)
    check("pdf: sablon jelolve", "SABLON, nem bútor" in flat, True)
    check("pdf: beszereles utan szabva", "beszerelés után szabva" in flat, True)
    check("pdf: hosszu megjegyzes nem vagodik le", "egymasra ragasztva" in flat, True)


def test_plain_job_has_no_special_sections():
    """Negative control: an ordinary job prints none of the new sections."""
    import json
    job = {"job": "t", "stock": {"w": 2440, "h": 1220},
           "parts": [{"name": "oldal", "w": 560, "h": 720, "qty": 2}]}
    rc, out, _ = _run(job)
    check("sima: exit 0", rc, 0)
    check("sima: nincs kulon szakasz",
          any(k in out for k in ("NYERS", "SABLON", "BESZERELES", "FELTETELEZES")), False)
    _, out, _ = _run(job, "--json")
    res = json.loads(out)
    check("sima: special ures", res["special"], {"blank": [], "jig": [], "fit_on_install": []})
    check("sima: nincs feltetelezes", res["assumptions"], [])


def _row_ys(parts):
    """Every y the cut-list pages write at, footer (y=28) left out."""
    import cutlist
    seen = []

    class Rec(cutlist.Page):
        def text(self, x, y, s, *a, **k):
            if y != 28:
                seen.append((y, str(s)))
            return super().text(x, y, s, *a, **k)

    real, cutlist.Page = cutlist.Page, Rec
    try:
        pages = cutlist._parts_pages("t", parts, *cutlist.A4_LANDSCAPE)
    finally:
        cutlist.Page = real
    return pages, seen


def test_wrapped_note_breaks_page_before_footer():
    """A wrapped note's last line stays above the footer (#747 review, Marveen).

    The page break used to look at the row's first line only; with 60 parts
    carrying a long note the last lines landed at y=-19, off the paper.
    Whether a given list hits the bottom depends on where the rows happen to
    fall, so the test shifts them with 0-5 one-line rows in front and tries
    2-, 3- and 4-line notes: a single layout passed on the old code by luck.
    """
    words = "beszereles utan szabva a helyszinen falhoz igazitva ellenorizd meretet".split()
    lows, counts = [], []
    for n_lines in (2, 3, 4):
        note = " ".join(words[i % len(words)] for i in range(7 * n_lines))
        for lead in range(6):
            ps = [{"name": f"r{i}", "w": 300, "h": 400, "qty": 1, "grain": "h",
                   "edging": "nincs"} for i in range(lead)]
            ps += [{"name": f"p{i}", "w": 300, "h": 400, "qty": 1, "grain": "h",
                    "edging": "nincs", "note": note} for i in range(60)]
            pages, seen = _row_ys(ps)
            lows.append(min(y for y, _ in seen))
            counts.append(sum(1 for _, s in seen if s.startswith("p") and s[1:].isdigit()))
    check("hosszu megjegyzes: semmi nem fut 50 ala (18 elrendezes)", min(lows) >= 50, True)
    check("hosszu megjegyzes: mind a 60 sor kiirva", set(counts), {60})
    short = [{"name": f"p{i}", "w": 300, "h": 400, "qty": 1, "grain": "h", "edging": "nincs"}
             for i in range(30)]
    check("rovid sorok: egy oldal marad (kontroll)", len(_row_ys(short)[0]), 1)


def test_empty_or_broken_job_stops():
    """No parts is not a plan, and a broken file is not a crash (#747)."""
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc, out, _ = _run({"job": "t", "stock": {"w": 2440, "h": 1220}, "parts": []})
    check("ures parts: exit 3", rc, 3)
    rc, _, _ = _run({"job": "t", "parts": [{"name": "a", "w": 100, "h": 100}]})
    check("nincs stock: exit 3", rc, 3)
    with tempfile.TemporaryDirectory() as d:
        for body, why in (("", "ures fajl"), ("{not json", "hibas JSON")):
            path = os.path.join(d, "job.json")
            with open(path, "w") as fh:
                fh.write(body)
            r = subprocess.run([sys.executable, os.path.join(here, "cutlist.py"), path],
                               capture_output=True, text=True)
            check(f"{why}: exit 3, nincs traceback", (r.returncode, "Traceback" in r.stderr), (3, False))


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
    print("cutlist.py ives, sablon, beszereles utan (#747)")
    test_blank_is_nested_at_blank_size()
    test_blank_smaller_than_finished_stops()
    test_bad_blank_stops()
    test_unknown_role_and_fit_stop()
    test_oversized_blank_is_unplaced()
    test_special_sections_in_text_and_pdf()
    test_plain_job_has_no_special_sections()
    test_wrapped_note_breaks_page_before_footer()
    test_empty_or_broken_job_stops()
    print()
    if FAILED:
        print(f"BUKOTT: {len(FAILED)} -- {', '.join(FAILED)}")
        sys.exit(1)
    print("Mind atment.")
