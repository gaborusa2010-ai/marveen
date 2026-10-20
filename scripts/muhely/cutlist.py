#!/usr/bin/env python3
"""
Cut list optimiser for sheet goods (plywood, MDF, chipboard, melamine).

Takes a part list and a sheet size, packs the parts onto as few sheets as
possible with a guillotine (straight-through) cutting pattern, and reports the
sheets needed, the waste, and an optional SVG of each layout.

Guillotine is deliberate: a table saw and a panel saw can only make cuts that
run the full width of the offcut. A tighter nesting that a CNC could do is
useless to someone cutting by hand.

All dimensions are millimetres.

Usage
-----
  cutlist.py <job.json> [--svg out_prefix] [--pdf out.pdf] [--json]

Exit codes
----------
  0  every part is on a sheet
  2  no job file given
  3  a part has no usable size; nothing was nested
  4  the plan was made, but at least one part does NOT fit on its sheet.
     The report, the JSON and the PDF are all still written, with the part
     listed under "NEM FER RA A TABLARA" / "unplaced".

Input JSON
----------
{
  "job":      "Mrs Patel komod",
  "kerf":     3.2,              # saw blade width, default 3.2
  "trim":     0,                # trim off each factory edge, default 0
  "stock":    {"w": 2440, "h": 1220, "price": 48.50, "label": "18mm birch ply"},
              # or, when the job uses more than one thickness:
              # [{"material":"18mm","w":2440,"h":1220,"price":48.50,"label":"18mm birch ply"},
              #  {"material":"6mm", "w":2440,"h":1220,"price":19.90,"label":"6mm ply hatlap"}]
  "parts": [
    {"name": "oldal",  "w": 800, "h": 450, "qty": 2, "grain": "w", "material": "18mm"},
    {"name": "polc",   "w": 1164, "h": 430, "qty": 3, "grain": "w"}
  ]
}

"grain" controls rotation:
  "w"   part width runs along the sheet width (2440 direction). No rotation.
  "h"   part width runs along the sheet height. Rotated 90 degrees, then fixed.
  "any" or omitted: the packer may rotate to save material.

Set grain on anything with a visible face. Rotating a veneered panel 90 degrees
gives you a stripe running the wrong way, and it is only obvious after finishing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from pdfout import Page, write_pdf, A4_LANDSCAPE
except ImportError:      # PDF output is optional; text output must never depend on it
    Page = None

DEFAULT_KERF = 3.2


class Placement:
    __slots__ = ("name", "x", "y", "w", "h", "rotated")

    def __init__(self, name, x, y, w, h, rotated):
        self.name = name
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.rotated = rotated

    def as_dict(self):
        return {"name": self.name, "x": round(self.x, 1), "y": round(self.y, 1),
                "w": round(self.w, 3), "h": round(self.h, 3), "rotated": self.rotated}


class Sheet:
    """One physical sheet, packed with a guillotine free-rectangle scheme."""

    def __init__(self, w, h, kerf):
        self.w = w
        self.h = h
        self.kerf = kerf
        self.free = [(0.0, 0.0, float(w), float(h))]
        self.placed = []

    def _best_fit(self, pw, ph, may_rotate):
        """Find the free rect that leaves the least wasted area. Returns
        (index, width, height, rotated) or None."""
        best = None
        for i, (fx, fy, fw, fh) in enumerate(self.free):
            for w, h, rot in ((pw, ph, False), (ph, pw, True)):
                if rot and not may_rotate:
                    continue
                if rot and abs(pw - ph) < 1e-9:
                    continue  # square, rotation is a no-op
                if w <= fw + 1e-9 and h <= fh + 1e-9:
                    leftover = fw * fh - w * h
                    if best is None or leftover < best[0]:
                        best = (leftover, i, w, h, rot)
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def place(self, name, pw, ph, may_rotate):
        hit = self._best_fit(pw, ph, may_rotate)
        if hit is None:
            return False
        idx, w, h, rot = hit
        fx, fy, fw, fh = self.free.pop(idx)
        self.placed.append(Placement(name, fx, fy, w, h, rot))

        # Guillotine split. Shorter-axis split (SAS): cut along the shorter
        # remaining dimension, which tends to leave one large usable offcut
        # instead of two thin useless ones.
        k = self.kerf
        rem_w = fw - w - k
        rem_h = fh - h - k
        if rem_w < 0:
            rem_w = 0.0
        if rem_h < 0:
            rem_h = 0.0

        if fw - w < fh - h:
            # split horizontally: right piece is short, bottom piece spans full width
            right = (fx + w + k, fy, rem_w, h)
            bottom = (fx, fy + h + k, fw, rem_h)
        else:
            # split vertically: right piece spans full height
            right = (fx + w + k, fy, rem_w, fh)
            bottom = (fx, fy + h + k, w, rem_h)

        for r in (right, bottom):
            if r[2] > 1e-6 and r[3] > 1e-6:
                self.free.append(r)
        return True

    def used_area(self):
        return sum(p.w * p.h for p in self.placed)

    def waste_pct(self):
        total = self.w * self.h
        return 0.0 if total <= 0 else 100.0 * (total - self.used_area()) / total


def pack(parts, stock_w, stock_h, kerf):
    """First-fit decreasing over an expanding list of sheets."""
    queue = []
    for p in parts:
        grain = str(p.get("grain", "any")).lower()
        w = float(p["w"])
        h = float(p["h"])
        if grain == "h":
            w, h = h, w
        may_rotate = grain in ("any", "", "none")
        for n in range(int(p.get("qty", 1))):
            queue.append((p["name"], w, h, may_rotate))

    # Largest first. Big awkward panels decide the layout; offcuts absorb the rest.
    queue.sort(key=lambda t: (max(t[1], t[2]), t[1] * t[2]), reverse=True)

    sheets = []
    unplaced = []
    for name, w, h, may_rotate in queue:
        fits_alone = (w <= stock_w and h <= stock_h) or (
            may_rotate and h <= stock_w and w <= stock_h)
        if not fits_alone:
            unplaced.append((name, w, h))
            continue
        for sh in sheets:
            if sh.place(name, w, h, may_rotate):
                break
        else:
            sh = Sheet(stock_w, stock_h, kerf)
            sh.place(name, w, h, may_rotate)
            sheets.append(sh)
    return sheets, unplaced


SVG_COLOURS = ["#cfe3f7", "#d6f0d8", "#fbe6c8", "#efd9f2", "#d9eef0",
               "#f7d9d9", "#e6e6c8", "#dcdcf0"]


def mm(v):
    """A part size for print, exactly as given: 523.5 stays 523.5, 564.0 is 564.

    Never round a size for display. carcass.py already rounds to the 0.5 mm the
    panel saw can do, and that half millimetre is the whole point. The old
    ':.0f' printed 523.5 as 524 and 2115.5 as 2116 (Python rounds a half to the
    EVEN neighbour), so the saw got a number 0.5 mm off in either direction.
    """
    return format(float(v), ".3f").rstrip("0").rstrip(".")


def to_svg(sheet, index, label, scale=0.25):
    w = sheet.w * scale
    h = sheet.h * scale
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h + 28:.0f}" '
        f'viewBox="0 0 {w:.0f} {h + 28:.0f}">',
        f'<rect x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="#f4f4f2" stroke="#333" stroke-width="1"/>',
    ]
    names = []
    for p in sheet.placed:
        if p.name not in names:
            names.append(p.name)
    for p in sheet.placed:
        c = SVG_COLOURS[names.index(p.name) % len(SVG_COLOURS)]
        x, y = p.x * scale, p.y * scale
        pw, ph = p.w * scale, p.h * scale
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw:.1f}" height="{ph:.1f}" '
                   f'fill="{c}" stroke="#222" stroke-width="0.7"/>')
        if pw > 46 and ph > 20:
            cx, cy = x + pw / 2, y + ph / 2
            out.append(f'<text x="{cx:.1f}" y="{cy - 3:.1f}" font-family="sans-serif" '
                       f'font-size="9" text-anchor="middle" fill="#111">{p.name}</text>')
            out.append(f'<text x="{cx:.1f}" y="{cy + 8:.1f}" font-family="sans-serif" '
                       f'font-size="8" text-anchor="middle" fill="#444">'
                       f'{mm(p.w)}x{mm(p.h)}{" R" if p.rotated else ""}</text>')
    out.append(f'<text x="4" y="{h + 18:.0f}" font-family="sans-serif" font-size="11" fill="#111">'
               f'{label} | tabla {index}: {mm(sheet.w)}x{mm(sheet.h)} mm, '
               f'hulladek {sheet.waste_pct():.1f}%</text>')
    out.append("</svg>")
    return "\n".join(out)


# --- printable output -------------------------------------------------------

PDF_COLOURS = [(0.81, 0.89, 0.97), (0.84, 0.94, 0.85), (0.98, 0.90, 0.78),
               (0.94, 0.85, 0.95), (0.85, 0.93, 0.94), (0.97, 0.85, 0.85),
               (0.90, 0.90, 0.78), (0.86, 0.86, 0.94)]


GRAIN_LABEL = {"w": "a SZELES menten", "h": "a MAGAS menten",
               "any": "mindegy", "none": "mindegy"}


def _parts_pages(job_name, parts, W, H):
    """The cut list itself: one row per part, with GRAIN and EDGING columns.

    The owner's rule (agents/forgacs/CLAUDE.md, workshop conventions): a cut
    list for the shop carries a SZALIRANY and an ELSZALAG column, and without
    them the list is not finished -- it is the reason he trusts the list over
    the drawing. Until 2026-10-20 (#611) the PDF had neither: the sheet pages
    print name and size only, so grain and banding lived on the screen and in
    the JSON, never on the paper that goes to the saw.

    A missing value is printed as missing, in red, never as a blank: a blank
    cell reads as "nothing to do", and for edging that is a SIZE decision.
    """
    RED = (0.7, 0, 0)
    no_edging = not any("edging" in p for p in parts)
    cols = [(40, "ALKATRESZ"), (210, "SZELES"), (270, "MAGAS"), (330, "DB"),
            (360, "ANYAG"), (420, "SZALIRANY"), (530, "ELSZALAG"), (640, "MEGJEGYZES")]
    pages, pg, y = [], None, 0
    for i, p in enumerate(parts):
        if pg is None or y < 50:
            pg = Page(W, H)
            pages.append(pg)
            y = H - 42
            pg.text(40, y, f"{job_name or 'Szabaslista'}: SZABASLISTA", size=14, font="F2")
            y -= 18
            if no_edging:
                pg.text(40, y, "A LISTA NINCS KESZ: egyetlen soron sincs elszalag megadva. "
                               "Az elszalag elenkent megy, es minden szalagos el 2 mm-t "
                               "vesz le abbol a meretbol.", size=9, font="F2", colour=RED)
                y -= 14
            y -= 4
            for x, head in cols:
                pg.text(x, y, head, size=8, font="F2")
            y -= 4
            pg.line(40, y, W - 40, y, lw=0.5)
            y -= 11
        # Empty counts as missing, the same as listcheck.py's NINCS SZALIRANY.
        grain = str(p["grain"]).lower() if p.get("grain") else None
        g_txt = GRAIN_LABEL.get(grain, grain) if grain is not None else "NINCS MEGADVA"
        edging = p.get("edging")
        e_txt = edging if edging else "NINCS MEGADVA"
        pg.text(40, y, str(p.get("name", "?"))[:30], size=8)
        pg.text(210, y, mm(float(p["w"])), size=8)
        pg.text(270, y, mm(float(p["h"])), size=8)
        pg.text(330, y, str(p.get("qty", 1)), size=8)
        pg.text(360, y, str(p.get("material", ""))[:10], size=8)
        pg.text(420, y, g_txt, size=8, colour=RED if grain is None else (0, 0, 0))
        pg.text(530, y, e_txt[:18], size=8, colour=RED if not edging else (0, 0, 0))
        pg.text(640, y, str(p.get("note", ""))[:38], size=7, colour=(0.35, 0.35, 0.35))
        y -= 11
    if pg is not None:
        pg.text(40, 28, "SZALIRANY: merre fusson az erezet az alkatreszen. A meretek mm-ben, "
                        "VAGASI meret (a carcass.py-bol jovo listan az elszalag mar levonva).",
                size=7.5, colour=(0.45, 0.45, 0.45))
    return pages


def render_pdf(result, path, parts=None):
    """A summary page, the cut list with grain and edging, then one page per sheet."""
    if Page is None:
        raise RuntimeError("pdfout.py not importable, PDF output unavailable")
    W, H = A4_LANDSCAPE
    pages = []

    # --- summary page ---
    pg = Page(W, H)
    y = H - 50
    pg.text(40, y, result["job"] or "Szabaslista", size=18, font="F2")
    y -= 26
    pg.text(40, y, f"Furreszres {result['kerf']} mm", size=10, colour=(0.3, 0.3, 0.3))
    y -= 26
    for e in result["materials"]:
        pg.text(40, y, f"{e['label']}", size=12, font="F2")
        cost = f"   koltseg {e['cost']:.2f}" if e["stock"]["price"] else ""
        pg.text(300, y, f"{e['sheets_needed']} db tabla "
                        f"({mm(e['stock']['w'])} x {mm(e['stock']['h'])} mm)"
                        f"   hulladek {e['waste_pct']}%{cost}", size=10)
        y -= 18
    if result.get("total_cost"):
        y -= 8
        pg.text(40, y, f"ANYAGKOLTSEG OSSZESEN: {result['total_cost']:.2f}",
                size=12, font="F2")
        y -= 22
    if result.get("unplaced"):
        y -= 8
        pg.text(40, y, "NEM FER RA A TABLARA:", size=11, font="F2", colour=(0.7, 0, 0))
        y -= 15
        for u in result["unplaced"]:
            pg.text(52, y, f"[{u['material']}] {u['name']}  {mm(u['w'])} x {mm(u['h'])}",
                    size=9, colour=(0.7, 0, 0))
            y -= 12
    pages.append(pg)

    # --- the cut list: grain and edging per part ---
    if parts:
        pages.extend(_parts_pages(result.get("job", ""), parts, W, H))

    # --- one page per sheet ---
    for e in result["materials"]:
        names = []
        for sh in e["sheets"]:
            for pl in sh["parts"]:
                if pl["name"] not in names:
                    names.append(pl["name"])
        sw, sh_mm = e["stock"]["w"], e["stock"]["h"]
        for sheet in e["sheets"]:
            pg = Page(W, H)
            pg.text(40, H - 42, f"{result['job'] or 'Szabaslista'}", size=14, font="F2")
            pg.text(40, H - 60,
                    f"{e['label']}   |   {sheet['index']}. tabla / {e['sheets_needed']}"
                    f"   |   {mm(sw)} x {mm(sh_mm)} mm   |   hulladek {sheet['waste_pct']}%",
                    size=10, colour=(0.25, 0.25, 0.25))

            draw_w, draw_h = 470.0, 380.0
            scale = min(draw_w / sw, draw_h / sh_mm)
            ox, oy_top = 40.0, H - 80.0
            bw, bh = sw * scale, sh_mm * scale
            oy = oy_top - bh
            pg.rect(ox, oy, bw, bh, fill=(0.96, 0.96, 0.95), stroke=(0.2, 0.2, 0.2), lw=1.0)

            for pl in sheet["parts"]:
                c = PDF_COLOURS[names.index(pl["name"]) % len(PDF_COLOURS)]
                x = ox + pl["x"] * scale
                # sheet coords run top-down, PDF runs bottom-up
                yy = oy_top - (pl["y"] + pl["h"]) * scale
                pw, ph = pl["w"] * scale, pl["h"] * scale
                pg.rect(x, yy, pw, ph, fill=c, stroke=(0.15, 0.15, 0.15), lw=0.5)
                if pw > 42 and ph > 18:
                    pg.text(x + pw / 2, yy + ph / 2 + 1, pl["name"], size=6.5, align="center")
                    pg.text(x + pw / 2, yy + ph / 2 - 7,
                            "%sx%s%s" % (mm(pl["w"]), mm(pl["h"]), " R" if pl["rotated"] else ""),
                            size=6, colour=(0.35, 0.35, 0.35), align="center")

            # parts table down the right hand side
            tx = ox + bw + 22
            ty = oy_top - 6
            pg.text(tx, ty, "ALKATRESZ", size=8, font="F2")
            pg.text(tx + 120, ty, "MERET", size=8, font="F2")
            ty -= 12
            for pl in sorted(sheet["parts"], key=lambda q: (q["y"], q["x"])):
                if ty < 50:
                    pg.text(tx, ty, "...", size=8)
                    break
                r = " R" if pl["rotated"] else ""
                pg.text(tx, ty, pl["name"][:22], size=7.5)
                pg.text(tx + 120, ty, "%s x %s%s" % (mm(pl["w"]), mm(pl["h"]), r), size=7.5)
                ty -= 10.5

            pg.text(40, 28, "R = forgatva. A meretek mm-ben, VAGASI meret (a carcass.py-bol "
                            "jovo listan az elszalag mar levonva). "
                            "Vagas elott ellenorizd a tabla tenyleges meretet.",
                    size=7.5, colour=(0.45, 0.45, 0.45))
            pages.append(pg)

    return write_pdf(path, pages, title=result.get("job", "Szabaslista"))


def _stock_table(job, parts):
    """Normalise `stock` into {material: {...}}.

    Accepts a single dict (applies to every part), or a list of dicts each
    carrying a `material` key. A part whose material has no stock entry falls
    back to the first entry, and that substitution is reported, never silent.

    STOCKLABEL901 (2026-09-01): the single-dict branch WAS silent, and that is
    the whole bug. One stock entry spread over SEVERAL materials copied the
    size, the price and the LABEL to each of them, so a 6 mm back panel printed
    under an "18mm lap" heading -- which is exactly how a back gets cut from the
    wrong board. The spread is a guess about which board the caller meant, not a
    fact, so it now drops the label (the heading then names the material itself)
    and reports itself like any other substitution. A genuinely single-material
    job is unaffected: nothing is dropped and nothing is reported.
    """
    raw = job["stock"]
    if isinstance(raw, dict) and "material" not in raw:
        mats = sorted({p.get("material", "default") for p in parts})
        if len(mats) < 2:
            return {m: dict(raw) for m in mats}, []
        table = {}
        for m in mats:
            e = dict(raw)
            e.pop("label", None)
            table[m] = e
        return table, mats
    entries = raw if isinstance(raw, list) else [raw]
    table = {}
    for e in entries:
        table[e.get("material", "default")] = dict(e)
    missing = []
    fallback = entries[0]
    for m in sorted({p.get("material", "default") for p in parts}):
        if m not in table:
            table[m] = dict(fallback)
            missing.append(m)
    return table, missing


def main(argv):
    # `--help` used to fall through to open(argv[1]) and die with a traceback
    # naming "--help" as a missing file. The usage text was always here; only
    # the flag that everyone tries first was not wired to it.
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2 if len(argv) < 2 else 0
    path = argv[1]
    svg_prefix = None
    as_json = "--json" in argv
    if "--svg" in argv:
        svg_prefix = argv[argv.index("--svg") + 1]

    with open(path) as fh:
        job = json.load(fh)

    kerf = float(job.get("kerf", DEFAULT_KERF))
    trim = float(job.get("trim", 0))
    all_parts = job["parts"]

    # A part with no usable size cannot be nested, and the nester must say so
    # BY NAME instead of dying inside pack() at a bare float(). Measured
    # 2026-09-12: a row with `w: "tbc"` produced a traceback and exit 1 -- the
    # same code this tool uses for a real failure, so a caller reading only the
    # exit code cannot tell a crash from an answer.
    #
    # It STOPS, it does not skip. A silently dropped row is the worse failure:
    # the workshop would get a plan that looks complete and is short a part
    # (that has happened here once already, eleven parts, 2026-09-04).
    bad = []
    for p in all_parts:
        for key in ("w", "h"):
            v = p.get(key)
            try:
                if float(v if v is not None else 0) <= 0:
                    bad.append(f'{p.get("name", "?")}: {key}={v!r} nulla vagy negativ')
            except (TypeError, ValueError):
                bad.append(f'{p.get("name", "?")}: {key}={v!r} nem szam')
    if bad:
        print("NEM NESTELHETo SOR, a vagasterv nem keszult el:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        print("Dontsd el a meretet, vagy vedd ki a sort. "
              "A listcheck.py ugyanezt nevszerint jelzi.", file=sys.stderr)
        return 3

    stock_table, substituted = _stock_table(job, all_parts)

    groups = {}
    for p in all_parts:
        groups.setdefault(p.get("material", "default"), []).append(p)

    result = {"job": job.get("job", ""), "kerf": kerf, "materials": [],
              "total_cost": 0.0, "unplaced": []}
    svg_n = 0

    for material in sorted(groups):
        st = stock_table[material]
        sw = float(st["w"]) - 2 * trim
        sh = float(st["h"]) - 2 * trim
        price = float(st.get("price", 0) or 0)
        label = st.get("label", material)

        sheets, unplaced = pack(groups[material], sw, sh, kerf)
        total_area = sum(s.w * s.h for s in sheets)
        used_area = sum(s.used_area() for s in sheets)

        entry = {
            "material": material,
            "label": label,
            "stock": {"w": sw, "h": sh, "price": price},
            "sheets_needed": len(sheets),
            "cost": round(len(sheets) * price, 2),
            "waste_pct": round(0.0 if total_area == 0 else
                               100.0 * (total_area - used_area) / total_area, 1),
            "sheets": [],
        }
        for s in sheets:
            svg_n += 1
            sd = {"index": len(entry["sheets"]) + 1,
                  "waste_pct": round(s.waste_pct(), 1),
                  "parts": [pl.as_dict() for pl in s.placed]}
            if svg_prefix:
                fn = f"{svg_prefix}-{material}-{sd['index']}.svg"
                with open(fn, "w") as fh:
                    fh.write(to_svg(s, sd["index"], f"{job.get('job', '')} / {label}"))
                sd["svg"] = fn
            entry["sheets"].append(sd)
        result["materials"].append(entry)
        result["total_cost"] += entry["cost"]
        result["unplaced"].extend(
            {"material": material, "name": n, "w": w, "h": h} for n, w, h in unplaced)

    result["total_cost"] = round(result["total_cost"], 2)
    result["stock_substituted"] = substituted

    if "--pdf" in argv:
        out = argv[argv.index("--pdf") + 1]
        render_pdf(result, out, parts=all_parts)
        result["pdf"] = out
        if not as_json:
            print(f"Nyomtathato szabaslista: {out}")
            print()

    # A plan with a part left off is not a finished plan. Until 2026-09-30 this
    # still exited 0 (Cody measured it, msg 7021): the warning was on the
    # screen, but a caller chaining on `&&` or reading only the exit code saw
    # success. Same failure shape as the eleven parts dropped on 2026-09-04.
    rc = 4 if result["unplaced"] else 0

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return rc

    print(f"MUNKA: {result['job']}")
    print(f"Furreszres: {kerf} mm")
    if substituted:
        print(f"FIGYELEM: ezekhez az anyagokhoz nincs sajat tabla megadva, a MERET "
              f"es az AR egyetlen tablabol jon: {', '.join(substituted)}. "
              f"Ellenorizd a tablameretet es az arat, mielott anyagkoltseget mondasz.")
    print()
    for e in result["materials"]:
        print(f"=== {e['label']} ({e['material']}) ===")
        print(f"Tabla {mm(e['stock']['w'])} x {mm(e['stock']['h'])} mm, "
              f"szukseges: {e['sheets_needed']} db", end="")
        if e["stock"]["price"]:
            print(f", koltseg {e['cost']:.2f}")
        else:
            print()
        print(f"Hulladek: {e['waste_pct']}%")
        for s in e["sheets"]:
            print(f"  --- {s['index']}. tabla (hulladek {s['waste_pct']}%) ---")
            for pl in sorted(s["parts"], key=lambda q: (q["y"], q["x"])):
                r = " [forgatva]" if pl["rotated"] else ""
                print(f"    {pl['name']:<20} {mm(pl['w']):>7} x {mm(pl['h']):<7} "
                      f"@ x={pl['x']:.0f} y={pl['y']:.0f}{r}")
        print()
    if result["total_cost"]:
        print(f"ANYAGKOLTSEG OSSZESEN: {result['total_cost']:.2f}")
    if result["unplaced"]:
        print()
        print("NEM FER RA A TABLARA (daraboljuk vagy nagyobb tabla kell):")
        for u in result["unplaced"]:
            print(f"  [{u['material']}] {u['name']}  {mm(u['w'])} x {mm(u['h'])}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
