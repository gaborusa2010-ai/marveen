#!/usr/bin/env bash
# A raked carcass: three numbers that were wrong, and the direction each was wrong in.
#
# Measured 2026-09-13 on a 1800 x 1950 x 550 three-bay practice box at 190/250:
#
#   valaszfal 1          1505.0 -> 1491.7   t*tan too LONG   (the direction that binds)
#   valaszfal 2          1054.0 -> 1040.3   same
#   tetolap ferde hossz  2260.8 -> 2215.6   45.4 too long: W/cos instead of the panel's own span
#   hatlap befoglalo     1950.0 -> 1934.0   the external height, which a rebated back never reaches
#
# And 2026-10-17, on a 29-degree loft example: the 09-13 "first principles" here
# used the same outline the code used, so both left out the panels a divider
# stands between. Every divider was t + t/cos too long (1491.7 -> 1458.0 on the
# practice box). The LIMIT check below is what catches that class: at 0.001
# degrees a raked box has to give the straight box's figures, and it did not.
#
# The expected figures here are computed FROM FIRST PRINCIPLES in this file, not
# read back out of carcass.py, so the test can disagree with the code it checks.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

echo "muhely-ferde-geometria"

mkspec() {  # $1 = construction, $2 = out
  python3 - "$1" "$2" <<'PY'
import json, sys
json.dump({"job": "ferde teszt", "w": 1800, "h": 1950, "d": 550, "t": 18,
           "back": {"type": "rebate", "t": 6, "rebate": 10},
           "construction": sys.argv[1],
           "dividers": {"count": 2},
           "slope": {"rise": 190, "run": 250, "source": "mert", "high_end": "bal"}},
          open(sys.argv[2], "w"))
PY
}

check() {  # $1 = cutlist json, $2 = construction
  python3 - "$1" "$2" <<'PY'
import json, math, sys
d = json.load(open(sys.argv[1])); construction = sys.argv[2]
parts = {p["name"]: p for p in d["parts"]}
W, H, t = 1800.0, 1950.0, 18.0
a = math.atan(190.0 / 250.0); tan, cos = math.tan(a), math.cos(a)
inner_w, inner_h = W - 2 * t, H - 2 * t
bay = (inner_w - 2 * t) / 3
out = []

def near(got, want, tol=0.6):
    return abs(float(got) - want) <= tol

# 1-2. Uprights: measured from the OUTSIDE face of the high end, so the first
#      divider sits t + bay + t/2 along, not bay + t/2. Its long point is the
#      face towards the high end, and it stands BETWEEN the bottom panel (t) and
#      the raked top (t/cos vertically; nothing for top_applied, where H is the
#      box under the worktop).
cut = t if construction == "top_applied" else t + t / cos
x = t
for i in (1, 2):
    x += bay
    want = H - x * tan - cut
    got = parts[f"valaszfal {i}."]["h"]
    # openings round DOWN to 0.5, so the tool may sit up to 0.5 under
    out.append((f"valaszfal {i}. = {want:.1f} (kapott {got})", near(got, want, 0.6)))
    x += t

# 2b. The low end's long point is its INSIDE face, W - t along.
want = H - (W - t) * tan
got = parts["oldal (alacsony veg)"]["h"]
out.append((f"alacsony oldal hosszu pont = {want:.1f} (kapott {got})", near(got, want, 0.6)))

# 3. The rake stretches the panel's OWN span.
flat = W if construction == "top_applied" else inner_w
name = "munkalap (ferde)" if construction == "top_applied" else "tetolap (ferde)"
want = flat / cos
got = parts[name]["w"]
out.append((f"{name} = {want:.1f} (kapott {got})", near(got, want, 0.6)))

# 4. A rebated back never reaches the external height.
if construction != "top_applied":
    want = inner_h + 2 * 10.0
    got = parts["hatlap (trapez)"]["h"]
    out.append((f"hatlap befoglalo = {want:.1f} (kapott {got})", near(got, want, 0.6)))
    out.append((f"hatlap NEM a kulso {H:.0f}", not near(got, H, 0.6)))

# 5. Free controls that need no arithmetic: every upright shorter than the one
#    before it, and none of them taller than the straight inner height.
hs = [parts["oldal (magas veg)"]["h"], parts["valaszfal 1."]["h"],
      parts["valaszfal 2."]["h"], parts["oldal (alacsony veg)"]["h"]]
out.append(("az allo elemek monoton fogynak a magas vegtol",
            all(hs[i] > hs[i + 1] for i in range(len(hs) - 1))))
out.append(("egyik valaszfal sem magasabb az egyenes belso magassagnal",
            max(parts["valaszfal 1."]["h"], parts["valaszfal 2."]["h"]) < inner_h))

bad = 0
for label, good in out:
    print(("  ok   " if good else "  FAIL ") + label)
    bad += 0 if good else 1
sys.exit(1 if bad else 0)
PY
}

for c in sides_full top_applied; do
  echo "  -- $c"
  mkspec "$c" "$TMP/spec.json"
  python3 scripts/muhely/carcass.py "$TMP/spec.json" --cutlist "$TMP/c.json" >/dev/null 2>&1 \
    || { no "$c: carcass.py elszallt"; continue; }
  check "$TMP/c.json" "$c" || fail=1
done

# THE LIMIT. A raked box at 0.001 degrees is a straight box, so every upright
# has to come out as the straight branch gives it. This is the check that does
# not share a model with the code: it compares the code with ITSELF.
echo "  -- hatareset: 0.001 fok = egyenes doboz"
for c in sides_full top_applied topbottom_full; do
  if python3 - "$c" "$TMP" <<'PY'
import json, subprocess, sys
c, tmp = sys.argv[1], sys.argv[2]
base = {"job": "hatar", "w": 1800, "h": 1950, "d": 550, "t": 18,
        "back": {"type": "applied", "t": 8}, "construction": c,
        "dividers": {"count": 2}}
got = {}
for tag, extra in (("egyenes", {}), ("ferde", {"slope": {"angle": 0.001, "source": "mert"}})):
    json.dump({**base, **extra}, open(f"{tmp}/{tag}.json", "w"))
    subprocess.run(["python3", "scripts/muhely/carcass.py", f"{tmp}/{tag}.json",
                    "--cutlist", f"{tmp}/{tag}-c.json"], capture_output=True, check=True)
    got[tag] = json.load(open(f"{tmp}/{tag}-c.json"))["parts"]
straight = {p["name"]: p["h"] for p in got["egyenes"]}
bad = []
for p in got["ferde"]:
    n = p["name"]
    key = "valaszfal" if n.startswith("valaszfal") else "oldal" if n.startswith("oldal") else None
    if key and abs(p["h"] - straight[key]) > 0.2:
        bad.append(f"{n}: {p['h']} != {straight[key]}")
print("    " + ("; ".join(bad) if bad else "egyezik"))
sys.exit(1 if bad else 0)
PY
  then ok "$c: a ferde ag a hataresetben az egyenest adja"
  else no "$c: a ferde ag a hataresetben NEM az egyenest adja"
  fi
done

# DRAWERS IN A RAKED BAY. The old check measured a stack against the carcass
# height, which is the tallest figure in the box and therefore the flattering
# one: three 250 mm fronts came back as "1200 mm free" while the low bay is
# 596 mm at its far edge. Both answers have to be reachable, or the check is
# decoration.
echo "  -- fiok a ferde rekeszben"
python3 - "$TMP/fiok.json" <<'PY'
import json, sys
json.dump({"job": "fiok ferden", "w": 1800, "h": 1950, "d": 550, "t": 18,
           "back": {"type": "rebate", "t": 6, "rebate": 10},
           "construction": "sides_full", "dividers": {"count": 2},
           "drawers": [{"count": 3, "front_h": 250, "runner": "side",
                        "bottom_t": 6, "runner_length": 450}],
           "drawer_front": "overlay",
           "slope": {"rise": 190, "run": 250, "source": "mert"}},
          open(sys.argv[1], "w"))
PY
out=$(python3 scripts/muhely/carcass.py "$TMP/fiok.json" 2>&1)
echo "$out" | grep -q "Nem fer bele" \
  && ok "megnevezetlen rekesz -> a legrosszabb esettel szamol es megbukik" \
  || no "megnevezetlen rekesz -> nem fogta meg, hogy nem fer bele"
echo "$out" | grep -q "1200 mm szabadon marad" \
  && no "meg mindig a kulso magassaghoz mer (1950)" \
  || ok "nem a kulso magassaghoz mer"

# The same stack DOES fit in the high bay, so the check must pass there. Without
# this the first assertion would also pass on a checker that always says no.
python3 - "$TMP/fiok.json" "$TMP/fiok1.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["drawers"][0]["bay"] = 1
json.dump(d, open(sys.argv[2], "w"))
PY
out=$(python3 scripts/muhely/carcass.py "$TMP/fiok1.json" 2>&1)
echo "$out" | grep -q "Nem fer bele" \
  && no "a magas rekeszben is nemet mond -- a checker mindig nemet mond" \
  || ok "bay 1 -> ugyanaz a keszlet belefer a magas rekeszbe"

# NEGATIVE CONTROL: the checker must be able to say no. Feed it a list built the
# old way (dividers t*tan too long) and it has to fail. Without this the whole
# file could be passing because it asserts nothing.
echo "  -- negativ kontroll"
mkspec sides_full "$TMP/spec.json"
python3 scripts/muhely/carcass.py "$TMP/spec.json" --cutlist "$TMP/c.json" >/dev/null 2>&1
python3 - "$TMP/c.json" "$TMP/rossz.json" <<'PY'
import json, math, sys
d = json.load(open(sys.argv[1]))
bump = 18.0 * math.tan(math.atan(190.0 / 250.0))   # the exact old error
for p in d["parts"]:
    if p["name"].startswith("valaszfal"):
        p["h"] = round(p["h"] + bump, 1)
json.dump(d, open(sys.argv[2], "w"))
PY
if check "$TMP/rossz.json" sides_full >/dev/null 2>&1; then
  no "a negativ kontroll ATMENT -- a teszt nem tud nemet mondani"
else
  ok "a regi hibaval megbukik, tehat tenyleg meri"
fi

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  BUKOTT"
exit $fail
