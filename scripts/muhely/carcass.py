#!/usr/bin/env python3
"""
Carcass calculator: external furniture dimensions to an exact part list.

You give it the outside size of the box and how it is built. It gives back every
panel with its finished size, ready to hand to cutlist.py.

All dimensions are millimetres.

Usage
-----
  carcass.py <spec.json> [--cutlist out.json] [--json]

Spec
----
{
  "job":        "Mrs Patel komod",
  "w": 1200, "h": 800, "d": 450,     # EXTERNAL size of the carcass
  "t": 18,                            # carcass material thickness
  "back": {"type": "rebate", "t": 6, "rebate": 10},
      # "rebate": back sits in a rebate, carcass is t thick all round
      # "applied": back is screwed on the outside, full W x H
      # "none": open back
  "construction": "sides_full",
      # "sides_full": sides run floor to ceiling, top+bottom between them
      # "topbottom_full": top and bottom run the full width, sides between them
      # "top_applied": sides run full height, bottom between them, and the top
      #   is a separate worktop LAID ON the carcass at full width. This is the
      #   owner's own habit (separate boxes, separate worktop) and it is what
      #   the Kreg built-in bookshelf does. h is then the box height WITHOUT
      #   the worktop; overall height is h + t.
  "slope": {"rise": 190, "run": 250, "source": "mert"},
      # source: "mert" (measured on site) or anything else, which counts as an
      # ASSUMPTION. Leaving it out counts as an assumption too, because an
      # unstated provenance is an unverified one. See apply_slope.
  "top": {"overhang_side": 0, "overhang_front": 0},   # only for "top_applied"
  "dividers": {"count": 2, "height": 1758.9},
      # height: clear divider height. Omit for the internal height, or give
      # "full" for a divider that runs the whole external height (then the
      # top and bottom are per-bay, not one continuous panel).
  "shelves": {"count": 2, "adjustable": true, "setback": 5,
              "joint": "dado", "dado_depth": 6.35},
      # joint "dado": the shelf sits IN a groove, so it is LARGER than the
      # opening by twice the groove depth. Anything else: it fits the opening.
  "face_frame": {"stile": 31.75, "rail": 31.75},
      # A face frame narrows the VISIBLE opening, and an adjustable shelf has
      # to pass through it from the front, so that shelf is cut to the frame,
      # not to the carcass. A fixed shelf still goes to the carcass.
  "drawers": [
      {"count": 3, "front_h": 250, "runner": "side", "bottom_t": 6,
       "runner_length": 400}
      # runner_length: side runners come in fixed lengths and the box is cut
      # to the runner, not to whatever is left in front of the back. Give it
      # whenever you know it; without it the box stops
      # `drawer_box_rear_clearance` short of the back.
  ],
  "drawer_front": "overlay",          # "overlay" or "inset"
  "edging": {"t": 2, "parts": {"polc": {"w": 0, "h": 1}}}
      # The owner's rule: every banded edge takes the tape thickness off THAT
      # dimension. Per part name, say how many banded edges each dimension has
      # (0, 1 or 2). Two opposite banded edges take 2 x t off, one takes t.
}

Every clearance used is printed in the report. Check them against the runners
and hinges you actually have in the drawer, because 12.5 mm per side is the
common ball-bearing runner and not a law of nature.
"""
import json
import sys

# Real-world clearances. Override per job in the spec if your hardware differs.
DEFAULTS = {
    "runner_side_clearance": 12.5,   # per side, side-mounted ball bearing runner
    # UNDERMOUNT IS NOT A PER-SIDE CLEARANCE. The runner engages the underside of
    # the box and the locking devices grip the sides at a FIXED INSIDE width, so the
    # controlled dimension is the box's INSIDE width, not its outside. Blum TANDEM
    # publishes it as a single deduction from the cabinet opening:
    #   563H, drawer sides up to 16 mm : inside width = opening - 42 mm
    #   563F, drawer sides 19 mm       : inside width = opening - 49 mm
    # Tolerance is +0 / -1.5 mm: narrower only adds side play, wider binds.
    # Other makes differ, so override this per job if the hardware is not TANDEM.
    "undermount_inside_deduction": 42.0,
    "runner_under_clearance": 3.0,   # kept only for specs written before 2026-08-27
    "drawer_box_height_gap": 25.0,   # opening height minus box height
    # How far the box stops short of the back when no runner length is given.
    # Only a fallback: a side runner is made in fixed lengths and the box is
    # cut to the runner. The Kreg dresser uses a 16 in runner in a carcass with
    # 16.25 in of clear depth, so this guess was 10 mm out on a real plan.
    "drawer_box_rear_clearance": 10.0,
    "shelf_side_clearance": 1.0,     # per side, so the shelf does not bind
    "drawer_front_gap": 3.0,         # gap between overlay fronts
    "inset_front_gap": 2.0,          # gap all round for an inset front
    "bottom_groove_depth": 6.0,      # drawer bottom groove depth per side
}


def parts_add(parts, name, w, h, qty, grain="any", note="", material=None):
    """material defaults to the carcass thickness, set by build() before use."""
    parts.append({"name": name, "w": round(w, 1), "h": round(h, 1),
                  "qty": int(qty), "grain": grain, "note": note,
                  "material": material or parts_add.default_material})


parts_add.default_material = "18mm"


def apply_edging(parts, edging, notes):
    """Take the tape thickness off the dimension each banded edge runs along.

    The owner's rule, and it is a rule about the CUT size, not a finish note: a
    2 mm tape on the front edge of a shelf makes the finished part 2 mm deeper,
    so the panel leaves the saw 2 mm shallower. Two opposite banded edges take
    2 x t off that dimension, one takes t, none takes nothing.

    Which edges get tape differs per part type, so it is given per part name
    (prefix match, because the generated names carry a bay or a drawer number).
    "*" applies to everything not named explicitly. None of the eighty published
    plans measured in August accounted for edging at all, so a size lifted from
    a foreign plan is 2-4 mm oversize here until this pass runs.
    """
    t = float(edging.get("t", 2))
    rules = edging.get("parts") or {}
    if not rules:
        return
    touched = 0
    for p in parts:
        # Exact name first, then longest prefix that ends on a WORD BOUNDARY.
        # A bare startswith() is not enough: "fiok elolap" is a prefix of
        # "fiok elolap-belso", so the rule for the visible front silently banded
        # the inside of the box as well, and the box came out 4 mm narrow.
        rule = rules.get(p["name"])
        if rule is None:
            for key in sorted(rules, key=len, reverse=True):
                if key == "*":
                    continue
                if p["name"].startswith(key) and p["name"][len(key):len(key) + 1] == " ":
                    rule = rules[key]
                    break
        if rule is None:
            rule = rules.get("*")
        if not rule:
            p["edging"] = "nincs"
            continue
        nw = int(rule.get("w", 0))
        nh = int(rule.get("h", 0))
        if not (nw or nh):
            p["edging"] = "nincs"
            continue
        p["w"] = round(p["w"] - nw * t, 1)
        p["h"] = round(p["h"] - nh * t, 1)
        p["edging"] = f"{t:.0f}mm sz:{nw} m:{nh}"
        touched += 1
    if touched:
        notes.append(f"ELSZALAG {t:.0f} mm levonva {touched} alkatresz meretebol. "
                     f"A listan szereplo meret a SZABASMERET, a szalaggal egyutt "
                     f"jon ki a kesz meret.")


def apply_rounding(parts, notes, grid):
    """Put every size on the saw's grid, rounding the right way per part.

    The owner's convention is 0.5 mm, because the panel saw cannot be set finer.
    Direction is not a detail: anything that goes INTO an opening rounds DOWN,
    because narrower only adds play while wider does not go in at all. The panels
    that DEFINE the outside round to nearest, since they are the reference the
    rest is measured from.

    Raked and divided work is where this bites. Three equal bays out of 1364 mm
    give 442.666..., and a shelf written as 442.7 cannot be cut; written as 443
    it is 0.3 mm too wide for its own opening.
    """
    import math

    # Parts that live inside something else. Rounding these UP is what jams a
    # shelf in its housing, so they always go down.
    into_opening = ("polc", "fiok", "valaszfal")
    down = up = 0
    for p in parts:
        constrained = p["name"].startswith(into_opening)
        for key in ("w", "h"):
            v = p[key]
            r = math.floor(v / grid) * grid if constrained else round(v / grid) * grid
            if abs(r - v) > 1e-9:
                p[key] = round(r, 2)
                if constrained:
                    down += 1
                else:
                    up += 1
    if down or up:
        notes.append(f"KEREKITES {grid} mm-re: {down} meret LEFELE (ami nyilasba megy), "
                     f"{up} a legkozelebbire (ami a kulso meretet adja). "
                     f"A panelfuresz ennel pontosabbat nem tud.")


def apply_slope(parts, notes, *, slope, W, H, t, bays, div_t, panel_depth):
    """Rake the top of the carcass to follow a staircase.

    Give the stair as rise/run, as an angle, or as the finished low-end height,
    and every upright gets its own length. The three numbers that go wrong on a
    sloped job, all of them written onto the list here:

    1. The RAKED EDGE IS LONGER THAN THE WIDTH: W / cos(alpha), not W. On a
       600 mm box at 37 degrees that is 753 mm, so a list saying 600 is 153 out.
    2. EVERY UPRIGHT IS A DIFFERENT LENGTH. A divider 900 mm along the run is
       900 * tan(alpha) shorter than the high end, and there is no single
       "divider height" to write down.
    3. THE CUT IS AN ANGLED CUT, so the length is ambiguous unless the list says
       which point it is measured at. These are LONG-POINT lengths.

    The site is never square: plaster, the stringer and a sloping floor all
    conspire. Measure in two or three places, work to the smallest, and send
    anything that meets the wall or the floor out oversize.
    """
    import math

    run = float(slope.get("run", 0))
    rise = float(slope.get("rise", 0))
    if slope.get("angle") is not None:
        alpha = math.radians(float(slope["angle"]))
        how = f"megadott szog {math.degrees(alpha):.1f} fok"
    elif rise and run:
        alpha = math.atan(rise / run)
        how = f"emelkedes {rise:.0f} / futas {run:.0f}"
    elif slope.get("h_low") is not None:
        drop = H - float(slope["h_low"])
        alpha = math.atan(drop / W)
        how = f"magas veg {H:.0f}, alacsony veg {float(slope['h_low']):.0f}"
    else:
        raise ValueError("A slope blokkhoz rise+run, angle vagy h_low kell.")

    tan_a = math.tan(alpha)
    drop_total = W * tan_a
    h_low = H - drop_total
    if h_low <= 0:
        raise ValueError(f"A doles {drop_total:.0f} mm-t esik {W:.0f} mm alatt, "
                         f"a {H:.0f} mm-es magassag ezt nem birja el.")
    # Geometrically fine and practically useless: the low end tapers to a sliver
    # nobody can build or use. A real under-stairs run stops short of the point
    # and the last bay is closed off, so say it instead of emitting a 32 mm panel
    # with a straight face.
    if h_low < 250:
        notes.append(f"FIGYELEM: az alacsony veg csak {h_low:.0f} mm. Ez a szamitas "
                     f"szerint helyes, de hasznalhatatlanul keskeny. A lepcso alatti "
                     f"munka rendszerint NEM fut ki a hegyes vegig: rovidebb futast "
                     f"adj meg, vagy zard le az utolso rekeszt.")
    high_end = slope.get("high_end", "bal")

    notes.append(f"FERDE TETO. {how}, ez {math.degrees(alpha):.1f} fok. "
                 f"A {W:.0f} mm-es futason {drop_total:.1f} mm-t esik: a magas veg "
                 f"{H:.0f}, az alacsony {h_low:.1f} mm. A magas veg: {high_end}.")

    # WHERE THE ANGLE CAME FROM TRAVELS WITH THE ANGLE.
    #
    # A quote is written before anyone can measure, so the stair figure on it is
    # an assumption, and the quote says so. Then the job is won, somebody builds
    # the final cut list from the same spec file, and the assumption arrives at
    # the saw as a bare number: the label stayed on the quote and did not travel.
    # On a raked job that is not one part out, it is EVERY upright out.
    #
    # So the mark is attached to the parts themselves, not to a covering note,
    # and it is the DEFAULT: an unstated provenance is an unverified one.
    measured = str(slope.get("source", "")).lower() in ("mert", "measured")
    if not measured:
        notes.append("FIGYELEM: a lepcso szoge FELTETELEZETT, nem mert. Ez a lista "
                     "AJANLATHOZ jo, vegleges szabashoz NEM. Merd le a helyszinen a "
                     "rise es a run erteket, ird be a slope.source mezobe hogy 'mert', "
                     "es futtasd ujra. A listcheck addig nem engedi at.")

    # Uprights: the two ends, then each divider at its own distance along the run.
    x = 0.0
    positions = []
    for i, bay in enumerate(bays):
        x += bay
        if i < len(bays) - 1:
            positions.append(x + div_t / 2)
            x += div_t

    for p in list(parts):
        if p["name"] == "oldal":
            parts.remove(p)
            parts.append({**p, "name": "oldal (magas veg)", "h": round(H, 1), "qty": 1,
                          "note": f"{high_end} oldal, teljes magassag, hosszu pont"})
            parts.append({**p, "name": "oldal (alacsony veg)", "h": round(h_low, 1), "qty": 1,
                          "note": f"szemkozti oldal, {drop_total:.1f} mm-rel rovidebb, hosszu pont"})
        elif p["name"] == "valaszfal":
            parts.remove(p)
            for i, xp in enumerate(positions, 1):
                hi = H - xp * tan_a
                parts.append({**p, "name": f"valaszfal {i}.", "h": round(hi, 1), "qty": 1,
                              "note": f"{xp:.0f} mm-re a magas vegtol, ezert "
                                      f"{xp * tan_a:.1f} mm-rel rovidebb, hosszu pont"})
        elif p["name"] in ("tetolap", "munkalap"):
            raked = W / math.cos(alpha)
            parts.remove(p)
            parts.append({**p, "name": f"{p['name']} (ferde)", "w": round(raked, 1),
                          "note": f"a FERDE el hossza {raked:.1f} mm, nem {W:.0f}. "
                                  f"Ket vege {math.degrees(alpha):.1f} fokra vagva"})
        elif p["name"] == "hatlap":
            parts.remove(p)
            parts.append({**p, "name": "hatlap (trapez)", "h": round(H, 1),
                          "note": f"TRAPEZ: {H:.0f} mm a magas vegen, {h_low:.1f} mm "
                                  f"az alacsonyon, a ferde el {math.degrees(alpha):.1f} fok. "
                                  f"A megadott meret a befoglalo teglalap"})

    notes.append("A ferde vagasok HOSSZU PONTON mertek. A helyszinen merj ket-harom "
                 "ponton, a legkisebbel szamolj, es ami falhoz vagy padlohoz er, "
                 "az tulmeretre vagva menjen ki.")
    if not measured:
        for p in parts:
            if p["name"].startswith(("oldal", "valaszfal", "tetolap", "munkalap", "hatlap")):
                p["note"] = (p.get("note", "") + "  [FELTETELEZETT SZOG]").strip()
    return {"angle_deg": round(math.degrees(alpha), 2), "h_low": round(h_low, 1),
            "raked_edge": round(W / math.cos(alpha), 1),
            "source": "mert" if measured else "feltetelezett"}


def build(spec):
    cfg = dict(DEFAULTS)
    cfg.update(spec.get("clearances", {}))

    W = float(spec["w"])
    H = float(spec["h"])
    D = float(spec["d"])
    t = float(spec["t"])
    construction = spec.get("construction", "sides_full")
    back = spec.get("back", {"type": "rebate", "t": 6, "rebate": 10})
    back_type = back.get("type", "rebate")
    back_t = float(back.get("t", 6))
    rebate = float(back.get("rebate", 10))

    parts = []
    notes = []
    parts_add.default_material = f"{t:.0f}mm"

    # Depth available for panels in front of the back
    if back_type == "rebate":
        # A rebated back sits INSIDE the carcass depth: the rebate is machined
        # into the side, top and bottom, so those panels stay full depth. Only
        # the clear internal depth loses the back thickness. Subtracting it from
        # every panel (as this did until 2026-08-26) made each one back_t too
        # shallow, which the Kreg media console, dowel bookcase and pegboard
        # cabinet cut lists all contradict.
        panel_depth = D
        notes.append(f"Hatlap {back_t} mm, falcba ul: a panelek TELJES melysegek, "
                     f"a belso szabad melyseg {D - back_t:.0f} mm.")
    elif back_type == "applied":
        panel_depth = D - back_t
        notes.append(f"Hatlap {back_t} mm, kivulrol felcsavarozva, a korpusz melysege {panel_depth:.0f} mm.")
    else:
        panel_depth = D
        notes.append("Nyitott hatu korpusz, nincs hatlap.")

    if construction == "sides_full":
        parts_add(parts, "oldal", panel_depth, H, 2, grain="h",
                  note="teljes magassag, erezet fuggoleges")
        parts_add(parts, "tetolap", W - 2 * t, panel_depth, 1, grain="w")
        parts_add(parts, "aljlap", W - 2 * t, panel_depth, 1, grain="w")
        inner_w = W - 2 * t
        inner_h = H - 2 * t
        notes.append("Oldalak futnak vegig, a teto es az alj koze bekotve.")
    elif construction == "top_applied":
        # Sides run the full box height, the bottom is let in between them, and
        # the worktop is a separate panel laid on top. The box height h does NOT
        # include the worktop, so the finished piece stands t taller. Reading a
        # plan this way is what made the Kreg built-in bookshelf's 72 in top and
        # 70.5 in bottom stop looking like a contradiction: one lies on the
        # sides, the other sits between them.
        top = spec.get("top") or {}
        oh_s = float(top.get("overhang_side", 0))
        oh_f = float(top.get("overhang_front", 0))
        parts_add(parts, "oldal", panel_depth, H, 2, grain="h",
                  note="teljes magassag, erezet fuggoleges")
        parts_add(parts, "aljlap", W - 2 * t, panel_depth, 1, grain="w",
                  note="az oldalak koze bekotve")
        parts_add(parts, "munkalap", W + 2 * oh_s, panel_depth + oh_f, 1, grain="w",
                  note="rafekvo, kulon darab")
        inner_w = W - 2 * t
        inner_h = H - t          # only the bottom eats into the inside
        notes.append(f"Oldalak futnak vegig, az alj koze bekotve, a munkalap "
                     f"RAFEKSZIK. A kesz butor magassaga {H + t:.0f} mm, "
                     f"nem {H:.0f}.")
        if oh_s or oh_f:
            notes.append(f"Munkalap tullogas: oldalt {oh_s:.0f} mm, elol {oh_f:.0f} mm.")
    else:
        parts_add(parts, "tetolap", W, panel_depth, 1, grain="w")
        parts_add(parts, "aljlap", W, panel_depth, 1, grain="w")
        parts_add(parts, "oldal", panel_depth, H - 2 * t, 2, grain="h")
        inner_w = W - 2 * t
        inner_h = H - 2 * t
        notes.append("Teto es alj fut vegig, az oldalak koze bekotve.")

    # Back panel
    if back_type == "rebate":
        bw = inner_w + 2 * rebate
        bh = inner_h + 2 * rebate
        parts_add(parts, "hatlap", bw, bh, 1, grain="any",
                  note=f"{rebate:.0f} mm falcba ul korben",
                  material=f"{back_t:.0f}mm")
    elif back_type == "applied":
        parts_add(parts, "hatlap", W, H, 1, grain="any", note="kivulrol felcsavarozva",
                  material=f"{back_t:.0f}mm")

    # Vertical dividers. Six of the twenty published plans measured on 2026-08-26
    # split the carcass, and so does the Lyford Road TV unit, so the bay width --
    # not the internal width -- is what a shelf, a drawer or a door is cut to.
    #
    # "dividers": {"count": 2}                 -> equal bays
    # "dividers": {"count": 1, "bays": [880, 430]}  -> explicit CLEAR bay widths
    #
    # An explicit list is checked against the internal width and refused if it
    # does not add up, because a bay list that silently disagrees with the
    # carcass is exactly the kind of error that reaches the saw.
    dv = spec.get("dividers") or {}
    n_div = int(dv.get("count", 0))
    div_t = float(dv.get("t", t))
    bays = [inner_w]
    if n_div:
        # A divider is not always as tall as the inside of the box. The Kreg
        # built-in bookshelf splits only the upper section, and the sliding door
        # cabinet runs its dividers the FULL external height because that plan
        # has no continuous top and bottom at all: each bay gets its own. Both
        # cut lists disagreed with a divider assumed to be inner_h, so say it.
        div_h_spec = dv.get("height", "inner")
        if div_h_spec == "full":
            div_h = H
            div_note = "teljes kulso magassag, rekeszenkent kulon teto es alj"
        elif div_h_spec == "inner":
            div_h = inner_h
            div_note = "allo elem, erezet fuggoleges"
        else:
            div_h = float(div_h_spec)
            div_note = f"reszmagassagu, {div_h:.0f} mm a {inner_h:.0f} mm belsobol"
            if div_h > inner_h + 0.5:
                raise ValueError(f"A valaszfal {div_h:.1f} mm magas, a belso "
                                 f"magassag viszont {inner_h:.1f} mm.")
        parts_add(parts, "valaszfal", panel_depth, div_h, n_div, grain="h",
                  note=div_note, material=f"{div_t:.0f}mm")
        if div_h_spec != "inner":
            notes.append(f"Valaszfal magassaga {div_h:.1f} mm ({div_note}).")
        explicit = dv.get("bays")
        if explicit:
            bays = [float(b) for b in explicit]
            if len(bays) != n_div + 1:
                raise ValueError(f"{n_div} valaszfal {n_div + 1} rekeszt ad, "
                                 f"a bays lista viszont {len(bays)} elemu.")
            total = sum(bays) + n_div * div_t
            if abs(total - inner_w) > 0.5:
                raise ValueError(f"A bays lista {total:.1f} mm-t ad ki a valaszfalakkal "
                                 f"egyutt, a belso szelesseg viszont {inner_w:.1f} mm.")
            notes.append(f"{n_div} valaszfal, megadott rekeszek: "
                         + ", ".join(f"{b:.1f}" for b in bays) + " mm.")
        else:
            bay_w = (inner_w - n_div * div_t) / (n_div + 1)
            bays = [bay_w] * (n_div + 1)
            notes.append(f"{n_div} valaszfal, {n_div + 1} egyenlo rekesz, "
                         f"egyenkent {bay_w:.1f} mm tiszta nyilas.")

    # Shelves
    # A face frame is screwed to the front of the carcass and its stiles stand
    # proud of the side panels, so the VISIBLE opening is narrower than the
    # carcass. An adjustable shelf has to go in through that opening, so it is
    # cut to the frame. A fixed shelf goes in before the frame does and stays
    # at the carcass size. Measured on a published plan: carcass inside 22.5 in,
    # 1.25 in stiles, visible opening 21.5, shelf 21.375.
    ff = spec.get("face_frame") or {}
    ff_stile = float(ff.get("stile", 0))
    ff_rail = float(ff.get("rail", ff_stile))
    ff_intrusion = max(0.0, ff_stile - t) if ff_stile else 0.0
    frame_opening_w = inner_w - 2 * ff_intrusion
    if ff_stile:
        frame_h = H + (t if construction == "top_applied" else 0)
        parts_add(parts, "keret allo", ff_stile, frame_h, 2, grain="h",
                  note="homlokkeret, fuggoleges")
        parts_add(parts, "keret fekvo", W - 2 * ff_stile, ff_rail, 2, grain="w",
                  note="homlokkeret, vizszintes")
        notes.append(f"Homlokkeret {ff_stile:.1f} mm allo, {ff_rail:.1f} mm fekvo. "
                     f"A latszo nyilas {frame_opening_w:.1f} mm, a korpusz belso "
                     f"merete {inner_w:.1f} mm, a kulonbseg oldalankent "
                     f"{ff_intrusion:.1f} mm.")

    sh = spec.get("shelves") or {}
    n_shelf = int(sh.get("count", 0))
    if n_shelf:
        setback = float(sh.get("setback", 5))
        # A fixed shelf is screwed or dowelled into the carcass and IS the
        # structure, so it is cut to the opening exactly. Only an adjustable
        # shelf needs clearance to drop in without binding.
        clr = cfg["shelf_side_clearance"] if sh.get("adjustable") else 0.0
        # A shelf housed in a groove is the one case where the part is BIGGER
        # than the opening: it has to reach into the groove on both sides. The
        # mudroom locker cut list is 0.5 in wider than its own clear opening for
        # exactly this reason, which reads as an error until you see the dado.
        dado = float(sh.get("dado_depth", 0)) if sh.get("joint") == "dado" else 0.0
        if dado:
            clr = -dado
            notes.append(f"A polc HORONYBA ul, oldalankent {dado:.1f} mm melyen, "
                         f"ezert oldalankent ennyivel NAGYOBB a nyilasnal.")
        sd = panel_depth - setback
        # An adjustable shelf goes in through the face frame; a fixed one does
        # not. With no frame the two are the same number.
        through_front = bool(sh.get("adjustable")) and not dado
        shelf_ref_deduct = 2 * ff_intrusion if (ff_stile and through_front) else 0.0
        if ff_stile and through_front:
            notes.append("Az ALLITHATO polc a homlokkeret latszo nyilasahoz "
                         "meretezve, mert elolrol kell betolni.")
        if n_div:
            # One shelf row per bay: count is PER BAY, because a divided carcass
            # is really n+1 boxes sharing two ends.
            for bi, bw_ in enumerate(bays, 1):
                opening = bw_ - shelf_ref_deduct
                parts_add(parts, f"polc {bi}. rekesz", opening - 2 * clr, sd, n_shelf, grain="w",
                          note=f"{opening:.1f} mm nyilas, oldalankent {clr:.1f} mm jatek, "
                               f"elol {setback:.0f} mm hatrahuzva")
            notes.append(f"A polc a REKESZ szelessegere megy, nem a belso szelessegre. "
                         f"{n_shelf} db rekeszenkent, osszesen {n_shelf * len(bays)} db.")
        else:
            opening = inner_w - shelf_ref_deduct
            parts_add(parts, "polc", opening - 2 * clr, sd, n_shelf, grain="w",
                      note=f"{opening:.1f} mm nyilas, oldalankent {clr:.1f} mm jatek, "
                           f"elol {setback:.0f} mm hatrahuzva")
        kind = "allithato, furt sorral" if sh.get("adjustable") else "fix, bekotve"
        notes.append(f"{n_shelf} db polc ({kind}).")

    # Drawers
    drawer_groups = spec.get("drawers") or []
    front_style = spec.get("drawer_front", "overlay")
    for gi, dg in enumerate(drawer_groups, 1):
        n = int(dg.get("count", 1))
        front_h = float(dg["front_h"])
        runner = dg.get("runner", "side")
        bottom_t = float(dg.get("bottom_t", 6))
        side_clr = cfg["runner_side_clearance"] if runner == "side" else cfg["runner_under_clearance"]

        # Drawer box. In a divided carcass the drawer runs in a BAY, so the
        # opening is the bay width, not the full internal width.
        opening_w = min(bays) if n_div else inner_w
        if runner == "under":
            # Size the INSIDE first, then derive the outside. See the note on
            # undermount_inside_deduction above.
            ded = float(dg.get("inside_deduction", cfg["undermount_inside_deduction"]))
            inside_w = opening_w - ded
            box_w = inside_w + 2 * t
            notes.append(f"Alulszerelt sin: a BELSO szelesseg a vezerelt meret. "
                         f"{opening_w:.1f} nyilas minusz {ded:.0f} = {inside_w:.1f} belso, "
                         f"{t:.0f} mm oldallal a doboz kulso merete {box_w:.1f}. "
                         f"Turés plusz 0 minusz 1,5: szukebb szabad, bovebb szorul.")
            if abs(t - 19.0) < 0.6 and abs(ded - 42.0) < 0.1:
                notes.append("FIGYELEM: 19 mm-es fiokoldalhoz a TANDEM 49 mm levonast ir, "
                             "nem 42-t. Allitsd at az inside_deduction erteket, vagy "
                             "hasznalj vekonyabb oldalt.")
        else:
            box_w = opening_w - 2 * side_clr
        box_h = front_h - cfg["drawer_box_height_gap"]
        # The runner, not the carcass, sets the box depth. Side runners are made
        # in fixed lengths and the box is cut to the runner; only when the length
        # is unknown does it fall back to stopping short of the back.
        runner_len = dg.get("runner_length")
        if runner_len:
            box_d = float(runner_len)
            if box_d > panel_depth + 0.5:
                notes.append(f"FIGYELEM: a {box_d:.0f} mm-es sin nem fer be a "
                             f"{panel_depth:.0f} mm-es korpuszba.")
        else:
            box_d = panel_depth - cfg["drawer_box_rear_clearance"]
        tag = f"fiok{gi}" if len(drawer_groups) > 1 else "fiok"

        side_note = (f"{runner} sin, a belso szelesseg vezerelt"
                     if runner == "under" else
                     f"{runner} sin, oldalankent {side_clr} mm")
        parts_add(parts, f"{tag} oldal", box_d, box_h, 2 * n, grain="w", note=side_note)
        parts_add(parts, f"{tag} elolap-belso", box_w - 2 * t, box_h, n, grain="w")
        parts_add(parts, f"{tag} hatlap", box_w - 2 * t, box_h, n, grain="w")
        bw = box_w - 2 * t + 2 * cfg["bottom_groove_depth"]
        bd = box_d - 2 * t + 2 * cfg["bottom_groove_depth"]
        parts.append({"name": f"{tag} fenek", "w": round(bw, 1), "h": round(bd, 1),
                      "qty": n, "grain": "any",
                      "note": f"{bottom_t:.0f} mm, horonyba, {cfg['bottom_groove_depth']:.0f} mm melyen",
                      "material": f"{bottom_t:.0f}mm"})

        # Visible front
        if front_style == "inset":
            fw = opening_w - 2 * cfg["inset_front_gap"]
            fh = front_h - 2 * cfg["inset_front_gap"]
            parts_add(parts, f"{tag} elolap", fw, fh, n, grain="w",
                      note=f"belapolt, korben {cfg['inset_front_gap']:.0f} mm fuga")
        else:
            # An overlay front covers the carcass, so it is measured against the
            # OUTSIDE. In a divided carcass one front covers one bay plus half of
            # each neighbouring upright, not the whole piece; taking W here made
            # a three-bay front three times too wide. With a face frame the front
            # laps the frame opening instead, by the overlay the hinges give.
            if ff_stile:
                lap = float(ff.get("overlay", cfg["runner_side_clearance"]))
                fw = frame_opening_w + 2 * lap
                fnote = f"rafekvo a homlokkeretre, oldalankent {lap:.1f} mm ratet"
            elif n_div:
                fw = opening_w + div_t - cfg["drawer_front_gap"]
                fnote = (f"rafekvo, egy REKESZT fed ({opening_w:.1f} mm nyilas plusz "
                         f"egy valaszfalnyi osztas), "
                         f"{cfg['drawer_front_gap']:.0f} mm fuga")
                # Say out loud what is left over at the two ends. The bay pitch
                # covers the interior cleanly, but the outer edge lands on the
                # side panel, and that leftover is a decision, not a rounding
                # error: it is the reveal the owner will see on the finished job.
                # Say it as a PITCH, not as a panel size: the panel size still
                # has the edge banding to come off it, and a note quoting a
                # number the table then contradicts is worse than no note.
                pitch = fw + cfg["drawer_front_gap"]
                leftover = W - len(bays) * pitch
                notes.append(f"Rafekvo elolapok osztott korpuszon: {len(bays)} rekesz "
                             f"{pitch:.1f} mm-es osztassal a {W:.0f} mm-bol, a ket "
                             f"szelen osszesen {leftover:.1f} mm marad szabadon. "
                             f"A tablazatban SZABASMERET all, elszalag mar levonva. "
                             f"Ha ez nem a kivant fuga, add meg az elolapot kezzel.")
            else:
                fw = W - cfg["drawer_front_gap"]
                fnote = f"rafekvo, {cfg['drawer_front_gap']:.0f} mm fuga"
            fh = front_h - cfg["drawer_front_gap"]
            parts_add(parts, f"{tag} elolap", fw, fh, n, grain="w", note=fnote)

        notes.append(f"{n} db fiok, {front_h:.0f} mm elolap, {runner} sin. "
                     f"Fiokdoboz {box_w:.0f} x {box_h:.0f} x {box_d:.0f} mm.")
        if box_h <= 0:
            notes.append(f"FIGYELEM: a {tag} doboz magassaga nem pozitiv. "
                         f"A {front_h:.0f} mm elolap tul alacsony ehhez a sinhez.")

    # Sanity check on the drawer stack. Overlay fronts cover the OUTSIDE of the
    # carcass, so they are measured against H. Inset fronts sit inside the
    # opening, so they are measured against the internal height. Checking an
    # overlay stack against inner_h reports a false failure on a correct design.
    total_h = sum(float(d["front_h"]) * int(d.get("count", 1)) for d in drawer_groups)
    if drawer_groups:
        if front_style == "inset":
            limit, what = inner_h, "belso magassag"
        else:
            limit, what = H, "kulso magassag"
        if total_h > limit + 1:
            notes.append(f"FIGYELEM: a fiokelolapok osszmagassaga {total_h:.0f} mm, "
                         f"a {what} viszont csak {limit:.0f} mm. Nem fer bele.")
        elif limit - total_h > 15:
            notes.append(f"A fiokelolapok osszesen {total_h:.0f} mm-t fednek a "
                         f"{limit:.0f} mm-bol, {limit - total_h:.0f} mm szabadon marad. "
                         f"Szandekos? Ha vegig fiokos a front, novelt elolap-magassag kell.")

    # The rake goes on BEFORE the edge banding, because banding comes off the
    # finished size and the rake is what decides that size.
    slope_info = None
    if spec.get("slope"):
        slope_info = apply_slope(parts, notes, slope=spec["slope"], W=W, H=H, t=t,
                                 bays=bays, div_t=div_t, panel_depth=panel_depth)

    edging = spec.get("edging")
    if edging:
        apply_edging(parts, edging, notes)

    # Last, because it has to land on whatever the rake and the banding left.
    grid = spec.get("round_to")
    if grid:
        apply_rounding(parts, notes, float(grid))

    return {
        "job": spec.get("job", ""),
        "external": {"w": W, "h": H, "d": D},
        "internal": {"w": round(inner_w, 1), "h": round(inner_h, 1), "d": round(panel_depth, 1)},
        "thickness": t,
        "construction": construction,
        "back": back_type,
        "clearances": cfg,
        "parts": parts,
        "notes": notes,
        "slope": slope_info,
    }


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1]) as fh:
        spec = json.load(fh)
    r = build(spec)

    if "--cutlist" in argv:
        out = argv[argv.index("--cutlist") + 1]
        job = {
            "job": r["job"],
            # ALWAYS written, including when there is no rake at all. If the key
            # were omitted on a straight job, a MISSING key would mean both "no
            # rake here" and "raked, but the provenance was lost" -- and a reader
            # cannot tell those apart, so it has to stay silent on both. Emitting
            # it every time buys the distinction: absent now means the file was
            # written by something that did not know, which is worth a word.
            "slope_source": r["slope"]["source"] if r.get("slope") else "nincs ferde",
            "kerf": spec.get("kerf", 3.2),
            "stock": spec.get("stock", {"w": 2440, "h": 1220, "label": f"{r['thickness']:.0f}mm lap"}),
            # `note` travels too. The per-part stamp is worth nothing if it only
            # survives on the printed page: the JSON is what the next tool reads,
            # and a row that has forgotten why it is odd looks merely odd.
            "parts": [{k: p[k] for k in ("name", "w", "h", "qty", "grain", "material")}
                      | ({"edging": p["edging"]} if "edging" in p else {})
                      | ({"note": p["note"]} if p.get("note") else {})
                      for p in r["parts"]],
        }
        with open(out, "w") as fh:
            json.dump(job, fh, indent=2, ensure_ascii=False)
        print(f"Szabaslista bemenet kiirva: {out}")

    if "--json" in argv:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    print(f"MUNKA: {r['job']}")
    print(f"Kulso meret:  {r['external']['w']:.0f} x {r['external']['h']:.0f} x {r['external']['d']:.0f} mm")
    print(f"Belso meret:  {r['internal']['w']:.0f} x {r['internal']['h']:.0f} x {r['internal']['d']:.0f} mm")
    print(f"Anyagvastagsag: {r['thickness']:.0f} mm, kotes: {r['construction']}, hatlap: {r['back']}")
    print()
    has_edging = any("edging" in p for p in r["parts"])
    el = f"{'ELSZALAG':<16}" if has_edging else ""
    print(f"{'ALKATRESZ':<22}{'SZELES':>9}{'MAGAS':>9}{'DB':>5}  {'ANYAG':<8}{el}MEGJEGYZES")
    print("-" * (88 + (16 if has_edging else 0)))
    for p in r["parts"]:
        el = f"{p.get('edging', ''):<16}" if has_edging else ""
        print(f"{p['name']:<22}{p['w']:>9.1f}{p['h']:>9.1f}{p['qty']:>5}  "
              f"{p.get('material',''):<8}{el}{p.get('note','')}")
    print()
    for n in r["notes"]:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
