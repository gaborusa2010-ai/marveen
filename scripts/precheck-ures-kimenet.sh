#!/bin/bash
# Generic preCheck for a watcher whose script prints only NEW items and records them as seen (#648).
#   precheck-ures-kimenet.sh <timeout-seconds> <command...>
# Empty output + exit 0 -> SKIP (the model is not woken).
# Output -> it becomes the prompt prefix, marked as ALREADY RUN: a second run would print
#           nothing, because the script has already recorded these items as seen.
# Non-zero exit or timeout -> the error text goes in as prefix, so the model sees it.
T=$1; shift
cd /home/gabor/marveen || exit 0
OUT=$(timeout "$T" "$@" 2>&1); RC=$?
if [ $RC -eq 0 ] && [ -z "$OUT" ]; then echo SKIP; exit 0; fi
if [ $RC -eq 124 ]; then
  echo "[ELOZETES FUTAS: TIMEOUT ${T}s] A figyelo szkript ($*) felbeszakadt, az allapota reszleges lehet. Futtasd le te, a feladat szerint. Amit addig kiirt:"
else
  echo "[ELOZETES FUTAS, exit $RC] A figyelo szkript ($*) MAR LEFUTOTT, NE futtasd ujra: az uj tetelek mar a seen-allapotban vannak, egy ujabb futas uresen jonne vissza. Ez a kimenete:"
fi
echo "$OUT"
exit 0
