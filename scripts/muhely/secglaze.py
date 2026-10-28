#!/usr/bin/env python3
"""
DIY secondary glazing: from window sizes to a cut list, a shopping list and a
printable bilingual (Hungarian + English) instruction sheet.

Free help project (#720, owner tg 9830). Not for sale. The point is that a
household in fuel poverty can measure its windows, print one page and take it
to the timber merchant and the plastics cutter.

Two ways to fit, chosen per window:

  frame     a timber batten frame screwed inside the window reveal; the clear
            sheet sits on the battens' front faces on magnetic tape.
            The size you give is the INSIDE of the reveal.
  magnetic  no timber: the sheet sits straight on the face of the existing
            window frame on magnetic tape.
            The size you give is the OUTSIDE of the frame face where the
            sheet will sit.

Every number this script uses is either SOURCED (S1..S6 below, each with the
URL it came from) or labelled BECSLES / ESTIMATE. Nothing in between. How
each source was read (the page itself, or only a search-result snippet) is in
SOURCE_READ, and that is printed on the sheet next to each source.

All dimensions are millimetres.

Usage
-----
  secglaze.py -w "Nappali=900x1200" -w "Halo=600x900x2" [--method frame|magnetic]
              [--thickness 4] [--pdf out.pdf] [--json out.json]
  secglaze.py --job job.json [--pdf out.pdf] [--json out.json]

  job.json: {"job": "Kovacs haz", "method": "frame", "thickness": 4,
             "windows": [{"name": "Nappali", "w": 900, "h": 1200, "qty": 1,
                          "method": "magnetic"}]}

Exit codes
----------
  0  every sheet and batten is planned
  2  no windows given
  3  a window size is invalid (not a number, zero, negative, too small, too
     large); nothing is planned
  4  the plan was made, but at least one sheet is larger than a stock sheet
     (listed under "NEM FER RA" / "does not fit"; split that window), or a
     batten is longer than a stock length (listed under "TUL HOSSZU LEC")
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutlist import pack, mm  # noqa: E402
try:
    from pdfout import Page, write_pdf, A4_PORTRAIT
except ImportError:      # PDF output is optional; text output must never depend on it
    Page = None

SOURCES = {
    "S1": ("Centre for Sustainable Energy, 'Secondary glazing', reviewed August 2026",
           "https://www.cse.org.uk/advice/secondary-glazing/",
           "About 10% of a typical house's heat loss is through the windows; "
           "temporary secondary glazing is a rigid clear sheet put up for the season."),
    "S2": ("South Seeds, 'DIY Secondary Glazing Step-by-Step' (2013)",
           "http://southseeds.org/wp-content/uploads/2013/07/DIY-Secondary-Glazing-How-to.pdf",
           "Measure the outside of the frame where the glazing sits, deduct 20 mm "
           "from each side, leaving at least 13 mm per side for the magnetic strips."),
    "S3": ("Omega Build, 'Fitting Tips for Magnetic Tape' (2025)",
           "https://www.omegabuild.com/wp-content/uploads/2025/07/Secondary-Glazing-Magnetic-Tape-Fitting-Info-Tips.pdf",
           "12.7 mm magnetic tape + 13 mm steel tape; rolls of 5, 15, 30 m; "
           "for 2-4 mm acrylic or plate polycarbonate; steel on the frame, magnet on the sheet."),
    "S4": ("The Plastic People, 'Secondary Glazing Measuring Guide'",
           "https://www.theplasticpeople.co.uk/content/specs/Secondary%20Glazing%20Measuring%20Guide%20%28Standard%29.pdf",
           "Allow a 15 mm clear border around the panel for fingertip adjustment."),
    "S5": ("CutLaserCut, 'Extruded clear acrylic sheet'",
           "https://cutlasercut.com/materials/extruded-clear-acrylic-sheet/",
           "Full acrylic sheet 3000 x 2000 mm; standard sizes include 2050 x 1525 mm."),
    "S6": ("HeatHack Guide Book, 'Temporary secondary glazing'",
           "https://guide.heathack.org/background-knowledge/addressing-heat-loss/windows/temporary-secondary.html",
           "Polycarbonate lasts about ten years without discolouring but scratches "
           "easily; you need somewhere to store the sheets."),
}

# How each source was read. "page": the document itself, read back by Marveen on
# 2026-10-28 (PDFs with pdftext.py, the web page via quarantine-reader; her msg 14442),
# the quoted text matched. "search": search-result snippet only. S4 is an image-only
# PDF with no extractable text, so it stays "search".
SOURCE_READ = {"S1": "search", "S2": "page", "S3": "page",
               "S4": "search", "S5": "page", "S6": "search"}
READ_LABEL = {"page": "oldalról olvasva / read from the page (Marveen, 2026-10-28)",
              "search": "keresési találat / search result only, page not opened"}

# Sourced constants.
MAGNETIC_DEDUCT = 20.0          # S2, per side
MIN_TAPE_LANDING = 13.0         # S2 / S3: the strip needs this much per side
FINGER_BORDER = 15.0            # S4, clear border around the panel
TAPE_ROLLS = (30000, 15000, 5000)   # S3, mm
STOCK_SHEET = (2050.0, 1525.0)  # S5, a standard size
MAX_SHEET = (3000.0, 2000.0)    # S5, the largest sheet
THICKNESS_OK = (2.0, 4.0)       # S3, what the tape is made for

# BECSLES / ESTIMATE: workshop practice, no source behind the number.
EST = {
    "batten": (21.0, 44.0),     # planed "2x1" batten, finished size; 21 against the reveal, 44 into the opening
    "batten_stock": 2400.0,     # common merchant length
    "fit": 2.0,                 # take off a batten's length so it goes in without forcing
    "screw_end": 50.0,          # first screw this far from each batten end
    "screw_pitch": 400.0,       # then no wider than this
    "screw": "4 x 70 mm csavar + 6 mm tipli / 4 x 70 mm screw + 6 mm wall plug",
    "waste": 0.10,              # tape and seal: 10% for corners and mistakes
    "thin_max": 800.0,          # above this longest side, 2 mm sheet flexes: use 4 mm
    "kerf": 3.2,
    "min_sheet": 100.0,         # a smaller sheet is not worth the tape
}

METHODS = ("frame", "magnetic")


class SizeError(ValueError):
    pass


def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise SizeError(f"{what}: nem szam / not a number: {v!r}")
    if not math.isfinite(x) or x <= 0:
        raise SizeError(f"{what}: nem pozitiv szam / not a positive number: {v!r}")
    return x


def half_down(x):
    """Round DOWN to 0.5 mm: a sheet that is half a millimetre big does not go in."""
    return math.floor(x * 2 + 1e-9) / 2.0


def parse_window_arg(s):
    """'Nappali=900x1200' or 'Nappali=900x1200x2' -> dict."""
    if "=" not in s:
        raise SizeError(f"{s!r}: a forma NEV=SZELESxMAGAS[xDB] / the form is NAME=WxH[xQTY]")
    name, dims = s.split("=", 1)
    parts = dims.lower().replace("*", "x").split("x")
    if len(parts) not in (2, 3):
        raise SizeError(f"{s!r}: a forma NEV=SZELESxMAGAS[xDB] / the form is NAME=WxH[xQTY]")
    win = {"name": name.strip() or "ablak", "w": parts[0], "h": parts[1]}
    if len(parts) == 3:
        win["qty"] = parts[2]
    return win


def plan_window(win, default_method, thickness):
    name = str(win.get("name") or "ablak")
    w = _num(win.get("w"), f"{name} szelesseg / width")
    h = _num(win.get("h"), f"{name} magassag / height")
    qty_raw = win.get("qty", 1)
    try:
        qty = int(str(qty_raw))
    except ValueError:
        raise SizeError(f"{name} darabszam / quantity: {qty_raw!r}")
    if qty < 1:
        raise SizeError(f"{name} darabszam / quantity: {qty_raw!r}")
    method = str(win.get("method") or default_method)
    if method not in METHODS:
        raise SizeError(f"{name}: ismeretlen modszer / unknown method {method!r}")
    if max(w, h) > MAX_SHEET[0] or min(w, h) > MAX_SHEET[1]:
        raise SizeError(f"{name}: {mm(w)} x {mm(h)} tul nagy / too large: the largest "
                        f"stock sheet is {mm(MAX_SHEET[0])} x {mm(MAX_SHEET[1])} (S5); "
                        "split the window into two panels")

    out = {"name": name, "w": w, "h": h, "qty": qty, "method": method, "battens": [],
           "warnings": []}
    if method == "magnetic":
        sw, sh = w - 2 * MAGNETIC_DEDUCT, h - 2 * MAGNETIC_DEDUCT
        out["landing_note"] = (f"a keret elülső lapján oldalanként legalább {mm(MIN_TAPE_LANDING)} mm "
                               f"sík felület kell / the frame face needs at least "
                               f"{mm(MIN_TAPE_LANDING)} mm flat per side (S2)")
    else:
        bt, bp = EST["batten"]
        landing = bp - FINGER_BORDER
        if landing < MIN_TAPE_LANDING:
            raise SizeError(f"{name}: a lec {mm(bp)} mm-es lapja keves a szalagnak / "
                            f"the batten face is too narrow for the tape")
        sw, sh = w - 2 * FINGER_BORDER, h - 2 * FINGER_BORDER
        horiz = half_down(w - EST["fit"])
        vert = half_down(h - 2 * bp - EST["fit"])
        if vert <= 0:
            raise SizeError(f"{name}: {mm(h)} mm magassag kevesebb, mint a ket lec / "
                            f"height is less than two battens")
        out["battens"] = [
            {"name": f"{name} felso+also / head+sill", "len": horiz, "qty": 2 * qty},
            {"name": f"{name} oldal / jamb", "len": vert, "qty": 2 * qty},
        ]
        out["landing_note"] = (f"a tábla {mm(landing)} mm-t fed a lécre / the sheet lands "
                               f"{mm(landing)} mm on each batten (min {mm(MIN_TAPE_LANDING)}, S2/S3)")
    sw, sh = half_down(sw), half_down(sh)
    if min(sw, sh) < EST["min_sheet"]:
        raise SizeError(f"{name}: a tabla {mm(sw)} x {mm(sh)} lenne, tul kicsi / "
                        f"the sheet would be too small")
    out["sheet"] = {"w": sw, "h": sh}
    if thickness < 4.0 and max(sw, sh) > EST["thin_max"]:
        out["warnings"].append(f"{name}: {mm(max(sw, sh))} mm-es oldal 2 mm-es lapnal hajlik, "
                               f"4 mm javasolt (BECSLES) / 2 mm sheet will flex, use 4 mm (ESTIMATE)")
    return out


def screws_for(length):
    inner = max(0.0, length - 2 * EST["screw_end"])
    return 2 + max(0, math.ceil(inner / EST["screw_pitch"]) - 1)


def rolls_for(total_mm):
    """Fewest rolls (S3 lengths) that cover total_mm; ties go to less tape bought."""
    best = None
    for a in range(0, int(total_mm // TAPE_ROLLS[0]) + 2):
        for b in range(0, 3):
            rest = total_mm - a * TAPE_ROLLS[0] - b * TAPE_ROLLS[1]
            c = max(0, math.ceil(rest / TAPE_ROLLS[2]))
            bought = a * TAPE_ROLLS[0] + b * TAPE_ROLLS[1] + c * TAPE_ROLLS[2]
            key = (bought, a + b + c)
            if best is None or key < best[0]:
                best = (key, (a, b, c))
    return dict(zip(("30 m", "15 m", "5 m"), best[1]))


def plan(job):
    windows = job.get("windows") or []
    if not windows:
        return None
    thickness = _num(job.get("thickness", 4), "vastagsag / thickness")
    if not THICKNESS_OK[0] <= thickness <= THICKNESS_OK[1]:
        raise SizeError(f"vastagsag / thickness {mm(thickness)} mm: a magneses szalag "
                        f"{mm(THICKNESS_OK[0])}-{mm(THICKNESS_OK[1])} mm-es lapra valo (S3)")
    method = str(job.get("method") or "frame")
    wins = [plan_window(w, method, thickness) for w in windows]

    sheet_parts = [{"name": w["name"], "w": w["sheet"]["w"], "h": w["sheet"]["h"],
                    "qty": w["qty"], "grain": "any"} for w in wins]
    sheets, unplaced = pack(sheet_parts, STOCK_SHEET[0], STOCK_SHEET[1], EST["kerf"])

    bt, bp = EST["batten"]
    batten_parts = [{"name": b["name"], "w": b["len"], "h": bp, "qty": b["qty"], "grain": "w"}
                    for w in wins for b in w["battens"]]
    lengths, too_long = pack(batten_parts, EST["batten_stock"], bp, EST["kerf"]) if batten_parts else ([], [])

    perim = sum(2 * (w["sheet"]["w"] + w["sheet"]["h"]) * w["qty"] for w in wins)
    tape = perim * (1 + EST["waste"])
    batten_total = sum(b["len"] * b["qty"] for w in wins for b in w["battens"])
    screws = sum(screws_for(b["len"]) * b["qty"] for w in wins for b in w["battens"])

    return {
        "job": job.get("job") or "Másodlagos üvegezés / Secondary glazing",
        "thickness": thickness,
        "windows": wins,
        "sheet_stock": {"w": STOCK_SHEET[0], "h": STOCK_SHEET[1], "count": len(sheets),
                        "unplaced": [{"name": n, "w": a, "h": b} for n, a, b in unplaced]},
        "batten_stock": {"len": EST["batten_stock"], "section": list(EST["batten"]),
                         "count": len(lengths),
                         "too_long": [{"name": n, "len": a} for n, a, _ in too_long]},
        "tape": {"each_m": round(tape / 1000.0, 2), "rolls": rolls_for(tape)},
        "seal_m": round(batten_total * (1 + EST["waste"]) / 1000.0, 2),
        "screws": screws,
        "warnings": [x for w in wins for x in w["warnings"]],
    }


def report(r):
    L = [f"{r['job']}  ({mm(r['thickness'])} mm lap / sheet)", ""]
    L.append("ABLAK / WINDOW          MODSZER   MERET           TABLA (vagasi meret)   DB")
    for w in r["windows"]:
        L.append(f"{w['name'][:22]:<22}  {w['method']:<8}  {mm(w['w']):>6} x {mm(w['h']):<6}  "
                 f"{mm(w['sheet']['w']):>7} x {mm(w['sheet']['h']):<8}       {w['qty']}")
    bat = [b for w in r["windows"] for b in w["battens"]]
    if bat:
        bt, bp = r["batten_stock"]["section"]
        L += ["", f"LECEK / BATTENS ({mm(bt)} x {mm(bp)} mm, BECSLES)"]
        for b in bat:
            L.append(f"  {b['name'][:38]:<38} {mm(b['len']):>7} mm  x{b['qty']}")
        L.append(f"  -> {r['batten_stock']['count']} db {mm(r['batten_stock']['len'])} mm-es lec / battens")
    L += ["", "ANYAGLISTA / SHOPPING LIST",
          f"  tabla / sheet {mm(r['thickness'])} mm: {sum(w['qty'] for w in r['windows'])} db meretre vagva / cut to size"
          f" (vagy {r['sheet_stock']['count']} db {mm(STOCK_SHEET[0])} x {mm(STOCK_SHEET[1])} tabla, S5)",
          f"  magneses + acel szalag / magnetic + steel tape: {r['tape']['each_m']} m mindkettobol / of each  "
          + ", ".join(f"{n} tekercs/roll x{c}" for n, c in r["tape"]["rolls"].items() if c)]
    if bat:
        L += [f"  csavar + tipli / screws + plugs: {r['screws']} db ({EST['screw']}, BECSLES)",
              f"  tomitoszalag / seal strip: {r['seal_m']} m (BECSLES)"]
    for w in r["windows"]:
        L.append(f"  * {w['name']}: {w['landing_note']}")
    for x in r["warnings"]:
        L.append(f"  ! {x}")
    for u in r["sheet_stock"]["unplaced"]:
        L.append(f"  NEM FER RA / DOES NOT FIT: {u['name']} {mm(u['w'])} x {mm(u['h'])}: oszd ket tablara / split it")
    for u in r["batten_stock"]["too_long"]:
        L.append(f"  TUL HOSSZU LEC / BATTEN TOO LONG: {u['name']} {mm(u['len'])} mm: toldani kell / needs a joint")
    return "\n".join(L)


# ---------------------------------------------------------------- instructions

STEPS = {
    "frame": [
        ("Mérd meg a káva (ablakmélyedés) belső szélességét és magasságát három helyen, és a legkisebbet add meg.",
         "Measure the inside width and height of the window reveal in three places and enter the smallest."),
        ("Nézd meg, hogy a kilincs és a nyíló szárny mögött elfér-e a léc és a tábla (21 mm léc + a tábla vastagsága).",
         "Check that the handle and the opening sash clear the batten and the sheet (21 mm batten + sheet thickness)."),
        ("Vágd le a léceket a lista szerint: a felső és az alsó teljes szélességű, az oldalsók közéjük mennek.",
         "Cut the battens to the list: head and sill run the full width, the jambs fit between them."),
        ("A léceket élükre állítva csavarozd a kávába (falazatba tipli kell), a léc és a fal közé tömítőszalag.",
         "Screw the battens into the reveal on edge (wall plugs in masonry), with seal strip between batten and wall."),
        ("A lécek elejét tisztítsd meg; az acélszalag a lécre, a mágnesszalag a tábla szélére megy.",
         "Clean the batten faces; the steel tape goes on the battens, the magnetic tape on the sheet edge (S3)."),
        ("Tedd fel a táblát. Tavasszal levehető; laposan, karcolástól védve tárold.",
         "Put the sheet up. It comes down in spring; store it flat and away from scratches (S6)."),
    ],
    "magnetic": [
        ("Mérd meg az ablakkeret külső méretét ott, ahol a tábla ülni fog.",
         "Measure the outside of the window frame where the sheet will sit (S2)."),
        ("Nézd meg, hogy a keret elején oldalanként legalább 13 mm sík felület van-e a szalagnak.",
         "Check the frame face has at least 13 mm flat per side for the tape (S2)."),
        ("A táblát a lista szerinti méretre rendeld vagy vágd: oldalanként 20 mm-rel kisebb.",
         "Order or cut the sheet to the listed size: 20 mm smaller on each side (S2)."),
        ("A keretet tisztítsd meg; az acélszalag a keretre, a mágnesszalag a tábla szélére megy.",
         "Clean the frame; the steel tape goes on the frame, the magnetic tape on the sheet edge (S3)."),
        ("Tedd fel a táblát. Tavasszal levehető; laposan, karcolástól védve tárold.",
         "Put the sheet up. It comes down in spring; store it flat and away from scratches (S6)."),
    ],
}

NOTES = [
    ("Pára: az első hetekben nézd meg a két üveg közét; ha párás, töröld ki és szellőztess (BECSLÉS, forrás nélkül).",
     "Condensation: check between the panes in the first weeks; if damp, wipe and ventilate (ESTIMATE, no source)."),
    ("Ha az ablak menekülőút (emeleti hálószoba), a táblának gyorsan levehetőnek kell lennie (BECSLÉS, forrás nélkül).",
     "If the window is a fire escape route (upstairs bedroom), the sheet must come off quickly (ESTIMATE, no source)."),
]


def _wrap(pg, s, width, size):
    words, lines, cur = str(s).split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if pg.text_width(t, size) > width and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


class _Doc:
    def __init__(self):
        self.pages, self.pg, self.y = [], None, 0
        self.W, self.H = A4_PORTRAIT
        self.new()

    def new(self):
        self.pg = Page(self.W, self.H)
        self.pages.append(self.pg)
        self.y = self.H - 50

    def para(self, s, size=9, font="F1", colour=(0, 0, 0), indent=0, gap=3):
        for ln in _wrap(self.pg, s, self.W - 90 - indent, size):
            if self.y < 50:
                self.new()
            self.pg.text(45 + indent, self.y, ln, size=size, font=font, colour=colour)
            self.y -= size + 3
        self.y -= gap

    def row(self, xs, cells, size=8.5, font="F1", rule=False):
        """One table row at fixed x offsets: proportional text cannot be aligned with spaces."""
        if self.y < 50:
            self.new()
        for x, c in zip(xs, cells):
            self.pg.text(45 + x, self.y, str(c), size=size, font=font)
        if rule:
            self.pg.line(45, self.y - 3, self.W - 45, self.y - 3, lw=0.5)
            self.y -= 4
        self.y -= size + 4


METHOD_LABEL = {"frame": "léckeret / batten frame", "magnetic": "keretre / on the frame"}


def render_pdf(r, path):
    if Page is None:
        raise RuntimeError("pdfout.py is not importable")
    d = _Doc()
    GREY, RED = (0.4, 0.4, 0.4), (0.7, 0, 0)
    d.para(r["job"], size=15, font="F2", gap=0)
    d.para(f"Másodlagos üvegezés, {mm(r['thickness'])} mm-es átlátszó lap. Ingyenes segédlet, nem eladásra. / "
           f"Secondary glazing, {mm(r['thickness'])} mm clear sheet. Free guide, not for sale.",
           size=8, colour=GREY, gap=8)

    d.para("ABLAKOK ÉS TÁBLÁK / WINDOWS AND SHEETS", size=11, font="F2", gap=2)
    xs = (0, 140, 270, 360, 470)
    d.row(xs, ("Ablak / Window", "Módszer / Method", "Méret / Size", "Tábla / Sheet", "Db / Qty"),
          font="F2", rule=True)
    for w in r["windows"]:
        d.row(xs, (w["name"][:26], METHOD_LABEL[w["method"]], f"{mm(w['w'])} x {mm(w['h'])}",
                   f"{mm(w['sheet']['w'])} x {mm(w['sheet']['h'])}", w["qty"]))
    d.para("Szélesség x magasság, mm. A tábla mérete a vágási méret. / Width x height, mm. "
           "The sheet size is the cutting size.", size=7.5, colour=GREY, gap=8)

    bat = [b for w in r["windows"] for b in w["battens"]]
    if bat:
        bt, bp = r["batten_stock"]["section"]
        d.para(f"LÉCEK / BATTENS  ({mm(bt)} x {mm(bp)} mm, BECSLÉS / ESTIMATE)", size=11, font="F2", gap=2)
        xs2 = (0, 300, 400)
        d.row(xs2, ("Darab / Piece", "Hossz / Length", "Db / Qty"), font="F2", rule=True)
        for b in bat:
            d.row(xs2, (b["name"][:48], f"{mm(b['len'])} mm", b["qty"]))
        d.para(f"Összesen {r['batten_stock']['count']} szál {mm(r['batten_stock']['len'])} mm-es léc. / "
               f"{r['batten_stock']['count']} lengths of {mm(r['batten_stock']['len'])} mm in total.",
               size=8.5, gap=8)

    d.para("ANYAGLISTA / SHOPPING LIST", size=11, font="F2", gap=2)
    n = sum(w["qty"] for w in r["windows"])
    d.para(f"Átlátszó lap / clear sheet, {mm(r['thickness'])} mm: {n} db méretre vágva / cut to size "
           f"(vagy / or {r['sheet_stock']['count']} db {mm(STOCK_SHEET[0])} x {mm(STOCK_SHEET[1])} tábla / sheet, S5)",
           size=9, gap=1)
    rolls = ", ".join(f"{c} x {k}" for k, c in r["tape"]["rolls"].items() if c)
    d.para(f"Mágnes- és acélszalag / magnetic and steel tape: mindkettőből / of each {r['tape']['each_m']} m "
           f"= {rolls} tekercs / roll (S3)", size=9, gap=1)
    if bat:
        d.para(f"Csavar és tipli / screws and plugs: {r['screws']} db, {EST['screw']} (BECSLÉS / ESTIMATE)",
               size=9, gap=1)
        d.para(f"Tömítőszalag / seal strip: {r['seal_m']} m (BECSLÉS / ESTIMATE)", size=9, gap=1)
    for w in r["windows"]:
        d.para(f"{w['name']}: {w['landing_note']}", size=8, colour=GREY, gap=0)
    for x in r["warnings"]:
        d.para(x, size=8.5, colour=RED, gap=0)
    for u in r["sheet_stock"]["unplaced"]:
        d.para(f"NEM FÉR RÁ / DOES NOT FIT: {u['name']} {mm(u['w'])} x {mm(u['h'])}, "
               "oszd két táblára / split it into two sheets", size=9, font="F2", colour=RED, gap=0)
    for u in r["batten_stock"]["too_long"]:
        d.para(f"TÚL HOSSZÚ LÉC / BATTEN TOO LONG: {u['name']} {mm(u['len'])} mm", size=9, font="F2",
               colour=RED, gap=0)
    d.y -= 10

    for method in sorted({w["method"] for w in r["windows"]}):
        title = {"frame": ("LÉCKERETES ABLAK", "BATTEN FRAME WINDOWS"),
                 "magnetic": ("MÁGNESES, KERETRE", "MAGNETIC ON THE FRAME")}[method]
        d.para(f"{title[0]} / {title[1]}", size=11, font="F2", gap=4)
        for i, (hu, en) in enumerate(STEPS[method], 1):
            d.para(f"{i}. {hu}", size=9, gap=0)
            d.para(en, size=8.5, colour=GREY, indent=12, gap=4)
        d.y -= 6
    d.para("FONTOS / IMPORTANT", size=11, font="F2", gap=4)
    for hu, en in NOTES:
        d.para(hu, size=9, gap=0)
        d.para(en, size=8.5, colour=GREY, indent=12, gap=4)
    d.y -= 6
    d.para("FORRÁSOK / SOURCES", size=11, font="F2", gap=2)
    for key, (title, url, says) in SOURCES.items():
        d.para(f"{key}  {title}", size=8.5, font="F2", gap=0)
        d.para(says, size=8, indent=12, gap=0)
        d.para(READ_LABEL[SOURCE_READ[key]], size=7, colour=GREY, indent=12, gap=0)
        d.para(url, size=7, colour=GREY, indent=12, gap=3)
    d.para("BECSLÉS / ESTIMATE (forrás nélkül / no source): léc 21 x 44 mm, 2400 mm-es szál, "
           "2 mm illesztési ráhagyás, csavar 50 mm-re a végétől és legfeljebb 400 mm-enként, "
           "10% szalag- és tömítőráhagyás, 2 mm-es lap legfeljebb 800 mm-es oldalig.", size=8)
    write_pdf(path, d.pages, title=r["job"])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-w", "--window", action="append", default=[],
                    help="NAME=WxH[xQTY], mm; repeat for more windows")
    ap.add_argument("--job", help="job JSON (see the module docstring)")
    ap.add_argument("--method", choices=METHODS)
    ap.add_argument("--thickness", type=float)
    ap.add_argument("--pdf")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    try:
        job = json.load(open(a.job)) if a.job else {}
        if not isinstance(job, dict):
            raise SizeError("a job JSON nem objektum / the job JSON is not an object")
        job.setdefault("windows", [])
        job["windows"] = list(job["windows"]) + [parse_window_arg(s) for s in a.window]
        if a.method:
            job["method"] = a.method
        if a.thickness is not None:
            job["thickness"] = a.thickness
        r = plan(job)
    except SizeError as e:
        print(f"HIBA / ERROR: {e}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as e:
        print(f"HIBA / ERROR: {e}", file=sys.stderr)
        return 2
    if r is None:
        print("HIBA / ERROR: nincs ablak megadva / no windows given", file=sys.stderr)
        return 2

    print(report(r))
    if a.json:
        json.dump(r, open(a.json, "w"), ensure_ascii=False, indent=1)
    if a.pdf:
        render_pdf(r, a.pdf)
        print(f"\nPDF: {a.pdf}")
    return 4 if r["sheet_stock"]["unplaced"] or r["batten_stock"]["too_long"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
