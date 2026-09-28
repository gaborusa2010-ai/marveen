#!/usr/bin/env bash
# Doors and plinth: the rule is back-calculated from a DELIVERED job, and the
# raked door had one error the straight door could not have.
#
# CASE A -- the Lyford Road TV unit, built and handed over 2026-08-24. Five
# published sizes, one free parameter (the reveal). Only gap = 3.0 reproduces
# all five, so the reveal is measured here, not chosen:
#
#   ajtolap             527.5 x 374    fogantyu deszka   531.5 x 50
#   ajto osszeallitva   426            labazat           1072.5 x 60
#
# CASE B -- 2026-09-14, on the same 1800 x 1950 x 550 practice box at 190/250:
#
#   ferde ajtolap magas el   1894.0 -> 1881.2   12.8 mm too LONG (will not shut)
#
# because a handle board of PERPENDICULAR width 50 takes 50/cos(alpha) = 62.8 mm
# off the height, not 50. The straight door is the alpha = 0 special case where
# the two are equal, which is why the straight code was right and this was not.
#
# Every expected figure is computed FROM FIRST PRINCIPLES in this file, not read
# back out of carcass.py, so the test can disagree with the code it checks.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

echo "muhely-ajto-labazat"

run() {  # $1 = spec json, $2 = out cutlist
  python3 scripts/muhely/carcass.py "$1" --cutlist "$2" >/dev/null 2>&1
}

# ---------------------------------------------------------------- CASE A
echo "  -- A: a KIADOTT Lyford Road doboz"
cat > "$TMP/a.json" <<'EOF'
{"job": "lyford doboz", "w": 1072.5, "h": 429, "d": 385, "t": 18,
 "construction": "sides_full", "back": {"type": "applied", "t": 12},
 "doors": {"count": 2, "handle_board": 50},
 "plinth": {"h": 50, "setback": 40},
 "edging": {"t": 2, "parts": {"ajtolap": {"w": 2, "h": 1}}},
 "round_to": 0.5}
EOF
run "$TMP/a.json" "$TMP/a-cut.json"
python3 - "$TMP/a-cut.json" <<'PY'
import json, math, sys
d = json.load(open(sys.argv[1]))
p = {x["name"]: x for x in d["parts"]}
W, H, n, hb, tape, grid, gap = 1072.5, 429.0, 2, 50.0, 2.0, 0.5, 3.0
assembly = H - gap                      # flush at the bottom, one reveal on top
dw_fin   = (W - (n + 1) * gap) / n      # n doors share W with n+1 reveals
leaf_fin = assembly - hb
want = {
    "ajtolap":              (math.floor((dw_fin - 2 * tape) / grid) * grid,
                             math.floor((leaf_fin - 1 * tape) / grid) * grid),
    "ajto fogantyu deszka": (math.floor(dw_fin / grid) * grid, hb),
    "labazat":              (W, 50.0 + 10.0),
}
bad = []
for name, (w, h) in want.items():
    if name not in p:
        bad.append(f"{name}: hianyzik a listarol"); continue
    if abs(p[name]["w"] - w) > 0.05 or abs(p[name]["h"] - h) > 0.05:
        bad.append(f"{name}: {p[name]['w']} x {p[name]['h']}, varva {w} x {h}")
if abs(assembly - 426.0) > 0.05:
    bad.append(f"ajto osszeallitva {assembly}, a kiadott listan 426")
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && ok "mind az ot kiadott meret eloall (ajtolap, fogantyu, labazat, 426)" \
             || no "elter a kiadott Lyford Road listatol"

# The reveal is the ONLY free number, so prove no other value fits. If 2.5 or
# 3.5 also produced 527.5 x 374, "3 mm" would be a preference, not a measurement.
python3 - <<'PY'
import math, sys
W, H, n, hb, tape, grid = 1072.5, 429.0, 2, 50.0, 2.0, 0.5
hits = []
for i in range(2, 21):
    gap = i * 0.25
    dw = (W - (n + 1) * gap) / n
    lw = math.floor((dw - 2 * tape) / grid) * grid
    lh = math.floor((H - gap - hb - tape) / grid) * grid
    if abs(lw - 527.5) < 1e-9 and abs(lh - 374.0) < 1e-9:
        hits.append(gap)
print("illeszkedo fuga-ertekek:", hits)
sys.exit(0 if hits == [3.0] else 1)
PY
[ $? -eq 0 ] && ok "a 3 mm fuga az EGYETLEN ertek, ami eloallitja a kiadott szamokat" \
             || no "tobb fuga-ertek is illeszkedik -- akkor ez nem meres"

# ---------------------------------------------------------------- CASE B
echo "  -- B: ferde ajto"
cat > "$TMP/b.json" <<'EOF'
{"job": "ferde ajto", "w": 1800, "h": 1950, "d": 550, "t": 18,
 "construction": "sides_full", "back": {"type": "rebate", "t": 6, "rebate": 10},
 "slope": {"rise": 190, "run": 250, "source": "mert", "high_end": "bal"},
 "dividers": {"count": 2},
 "doors": {"count": 3, "handle_board": 50, "top": "raked", "hinge_side": "low"},
 "edging": {"t": 2, "parts": {"ajtolap": {"w": 2, "h": 1}}},
 "round_to": 0.5}
EOF
run "$TMP/b.json" "$TMP/b-cut.json"
python3 - "$TMP/b-cut.json" <<'PY'
import json, math, sys
d = json.load(open(sys.argv[1]))
p = {x["name"]: x for x in d["parts"]}
W, H, hb, n, gap, tape, grid = 1800.0, 1950.0, 50.0, 3, 3.0, 2.0, 0.5
a = math.atan(190.0 / 250.0); tan, cos = math.tan(a), math.cos(a)
dw = (W - (n + 1) * gap) / n
drop = gap / cos            # a reveal ALONG the rake is deeper measured vertically
hb_v = hb / cos             # and so is the handle board
bad = []
x = gap
for k in range(1, n + 1):
    h_hi = H - x * tan - drop - hb_v
    want_w = math.floor((dw - 2 * tape) / grid) * grid
    want_h = math.floor((h_hi - 1 * tape) / grid) * grid
    got = p.get(f"ajtolap {k}.")
    if not got:
        bad.append(f"ajtolap {k}. hianyzik"); x += dw + gap; continue
    if abs(got["w"] - want_w) > 0.05 or abs(got["h"] - want_h) > 0.05:
        bad.append(f"ajtolap {k}.: {got['w']} x {got['h']}, varva {want_w} x {want_h}")
    # the solid strip is a LENGTH x WIDTH part, not a bounding rectangle
    hbp = p.get(f"ajto fogantyu deszka {k}.")
    want_len = math.floor((dw / cos) / grid) * grid
    if not hbp:
        bad.append(f"fogantyu deszka {k}. hianyzik")
    elif abs(hbp["w"] - want_len) > 0.05 or abs(hbp["h"] - hb) > 0.05:
        bad.append(f"fogantyu {k}.: {hbp['w']} x {hbp['h']}, varva {want_len} x {hb}")
    x += dw + gap
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && ok "harom ferde ajtolap es harom tomor fogantyu deszka jo" \
             || no "a ferde ajto merete elter az elso elvektol"

# The bug that was here: hb subtracted VERTICALLY. It survives a straight door
# and only shows on a rake, so the straight case cannot guard it.
python3 - "$TMP/b-cut.json" <<'PY'
import json, math, sys
d = json.load(open(sys.argv[1]))
got = {x["name"]: x for x in d["parts"]}["ajtolap 1."]["h"]
a = math.atan(190.0 / 250.0)
wrong = got + (50.0 / math.cos(a) - 50.0)
print(f"  most {got} mm; a fuggoleges levonassal {wrong:.1f} mm lett volna")
sys.exit(0 if abs(wrong - got) > 10 else 1)
PY
[ $? -eq 0 ] && ok "a regi, fuggoleges levonas merhetoen mas szamot adott" \
             || no "a ket olvasat nem ter el -- akkor ez a teszt nem meri a hibat"

# ---------------------------------------------------------------- hinge
echo "  -- C: a pant-ellenorzes tud-e NEMET mondani"
cat > "$TMP/c.json" <<'EOF'
{"job": "hegyes veg", "w": 2400, "h": 1950, "d": 550, "t": 18,
 "construction": "sides_full", "back": {"type": "rebate", "t": 6, "rebate": 10},
 "slope": {"rise": 190, "run": 250, "source": "mert"},
 "doors": {"count": 4, "hinge_side": "low"}}
EOF
python3 scripts/muhely/carcass.py "$TMP/c.json" 2>&1 | grep -q "ket pantcseszehez viszont" \
  && ok "keskeny hegyes ajtora KIIRJA, hogy nem fer ra ket pant" \
  || no "a pant-ellenorzes nem tud nemet mondani"
python3 scripts/muhely/carcass.py "$TMP/b.json" 2>&1 | grep -q "ket pantcseszehez viszont" \
  && no "a normal ajtora IS riaszt -- hamis pozitiv" \
  || ok "a normal ajtora nem riaszt"

# ---------------------------------------------------------------- rounding
echo "  -- D: az ajto LEFELE kerekul"
python3 - <<'PY'
import sys
sys.path.insert(0, "scripts/muhely")
import importlib.util
spec = importlib.util.spec_from_file_location("c", "scripts/muhely/carcass.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
parts = [{"name": "ajtolap", "w": 531.75, "h": 100.0},
         {"name": "tetolap", "w": 531.75, "h": 100.0}]
m.apply_rounding(parts, [], 0.5)
print("  ajtolap:", parts[0]["w"], " tetolap:", parts[1]["w"])
# 531.75 down is 531.5; to nearest it is 532.0 and the pair closes the reveal
sys.exit(0 if abs(parts[0]["w"] - 531.5) < 1e-9 and abs(parts[1]["w"] - 532.0) < 1e-9 else 1)
PY
[ $? -eq 0 ] && ok "531.75 -> 531.5 ajton, 532.0 kulso panelen" \
             || no "az ajto rossz iranyba kerekul"

# ---------------------------------------------------------------- E
# COMPLETENESS. Whatever the variant, the pieces of one door have to ADD UP to
# the door. The square branch failed exactly this: it took the handle board off
# the leaf and then emitted no board, so the door was 50 mm short of its own
# opening and every individual size still looked right.
echo "  -- E: az ajto darabjai kiadjak-e a teljes ajtot"
for VAR in raked level square; do
  python3 - "$VAR" "$TMP/e-$VAR.json" <<'EOF'
import json, sys
v = sys.argv[1]
d = {"job": f"teljesseg {v}", "w": 1800, "h": 1950, "d": 550, "t": 18,
     "construction": "sides_full", "back": {"type": "rebate", "t": 6, "rebate": 10},
     "slope": {"rise": 190, "run": 250, "source": "mert", "high_end": "bal"},
     "dividers": {"count": 2},
     "doors": {"count": 3, "handle_board": 50, "hinge_side": "high",
               "top": "square" if v == "square" else "raked"}}
if v == "level":
    d["doors"]["handle_top"] = "level"
json.dump(d, open(sys.argv[2], "w"))
EOF
  python3 scripts/muhely/carcass.py "$TMP/e-$VAR.json" --cutlist "$TMP/e-$VAR-cut.json" >/dev/null 2>&1
  python3 - "$TMP/e-$VAR-cut.json" "$VAR" <<'EOF'
import json, math, sys
d = json.load(open(sys.argv[1])); v = sys.argv[2]
p = [x for x in d["parts"] if x["name"].startswith("ajto")]
W, H, hb, n, gap = 1800.0, 1950.0, 50.0, 3, 3.0
a = math.atan(190.0 / 250.0); tan, cos = math.tan(a), math.cos(a)
dw = (W - (n + 1) * gap) / n
bad = []
x = gap
for k in range(1, n + 1):
    h_hi = H - x * tan - gap / cos          # full door height at its TALL edge
    h_lo = H - (x + dw) * tan - gap / cos
    got = {q["name"]: q for q in p if q["name"].endswith(f" {k}.")}
    if v == "raked":
        # trapezoid leaf (bounding = tall edge) + strip of vertical extent hb/cos
        total = got[f"ajtolap {k}."]["h"] + hb / cos
    elif v == "level":
        total = got[f"ajtolap {k}."]["h"] + hb + got[f"ajto felso betet {k}."]["h"]
    else:
        total = got[f"ajtolap {k}."]["h"] + hb + got[f"ajto vakolo {k}."]["h"]
    if abs(total - h_hi) > 0.15:
        bad.append(f"{v} {k}.: a darabok {total:.1f} mm-t adnak, az ajto {h_hi:.1f} mm")
    if hb and not any("fogantyu" in q for q in got):
        bad.append(f"{v} {k}.: NINCS fogantyu deszka a listan, pedig kertunk")
    x += dw + gap
print("\n".join(bad))
sys.exit(1 if bad else 0)
EOF
  [ $? -eq 0 ] && ok "$VAR: a darabok kiadjak a teljes ajtot, es a deszka rajta van"                || no "$VAR: a darabok NEM adjak ki az ajtot"
done

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  BUKOTT"
exit $fail
