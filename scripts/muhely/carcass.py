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
  "shelves": {"count": 2, "adjustable": true, "setback": 5},
  "drawers": [
      {"count": 3, "front_h": 250, "runner": "side", "bottom_t": 6}
  ],
  "drawer_front": "overlay"           # "overlay" or "inset"
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
    "runner_under_clearance": 3.0,   # per side, undermount runner
    "drawer_box_height_gap": 25.0,   # opening height minus box height
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

    # Shelves
    sh = spec.get("shelves") or {}
    n_shelf = int(sh.get("count", 0))
    if n_shelf:
        setback = float(sh.get("setback", 5))
        # A fixed shelf is screwed or dowelled into the carcass and IS the
        # structure, so it is cut to the opening exactly. Only an adjustable
        # shelf needs clearance to drop in without binding.
        clr = cfg["shelf_side_clearance"] if sh.get("adjustable") else 0.0
        sw = inner_w - 2 * clr
        sd = panel_depth - setback
        parts_add(parts, "polc", sw, sd, n_shelf, grain="w",
                  note=f"oldalankent {clr:.1f} mm jatek, elol {setback:.0f} mm hatrahuzva")
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

        # Drawer box
        box_w = inner_w - 2 * side_clr
        box_h = front_h - cfg["drawer_box_height_gap"]
        box_d = panel_depth - 10.0   # 10 mm short of the back so it never bottoms out
        tag = f"fiok{gi}" if len(drawer_groups) > 1 else "fiok"

        parts_add(parts, f"{tag} oldal", box_d, box_h, 2 * n, grain="w",
                  note=f"{runner} sin, oldalankent {side_clr} mm")
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
            fw = inner_w - 2 * cfg["inset_front_gap"]
            fh = front_h - 2 * cfg["inset_front_gap"]
            parts_add(parts, f"{tag} elolap", fw, fh, n, grain="w",
                      note=f"belapolt, korben {cfg['inset_front_gap']:.0f} mm fuga")
        else:
            fw = W - cfg["drawer_front_gap"]
            fh = front_h - cfg["drawer_front_gap"]
            parts_add(parts, f"{tag} elolap", fw, fh, n, grain="w",
                      note=f"rafekvo, {cfg['drawer_front_gap']:.0f} mm fuga")

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
            "kerf": spec.get("kerf", 3.2),
            "stock": spec.get("stock", {"w": 2440, "h": 1220, "label": f"{r['thickness']:.0f}mm lap"}),
            "parts": [{k: p[k] for k in ("name", "w", "h", "qty", "grain", "material")}
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
    print(f"{'ALKATRESZ':<22}{'SZELES':>9}{'MAGAS':>9}{'DB':>5}  {'ANYAG':<8}MEGJEGYZES")
    print("-" * 88)
    for p in r["parts"]:
        print(f"{p['name']:<22}{p['w']:>9.1f}{p['h']:>9.1f}{p['qty']:>5}  "
              f"{p.get('material',''):<8}{p.get('note','')}")
    print()
    for n in r["notes"]:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
