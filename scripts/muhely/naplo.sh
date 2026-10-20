#!/usr/bin/env bash
# Append one entry to the daily log, with the time STAMPED HERE, not typed by hand.
#
# Why this exists: on 2026-09-08 all ten of my daily-log entries carried a made-up
# clock, drifting monotonically to +154 minutes. The lesson was written down, with
# the exact fix (NOW=$(date +%H:%M) in the same command). On 2026-09-09 I did it
# again -- nine entries, drift up to +119 minutes -- because the mechanism lived in
# a memory page and the actual command was still typed out by hand each time.
#
# So the fix is not another sentence. The time cannot be passed in: this script
# reads the clock at the moment it posts, and REFUSES text that already starts with
# one. A log is history; a chronology that drifts an hour states a false causal order.
#
# Usage:  naplo.sh "[LEZART] Tema. Mi tortent, mi lett az eredmenye."
#         naplo.sh --agent margo "[FOLYAMATBAN] ..."
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
AGENT=forgacs
if [ "${1:-}" = "--agent" ]; then AGENT="$2"; shift 2; fi

# -f FILE reads the entry from a file, and that is the SAFE path, not a convenience.
# Text passed on the command line goes through bash first: a backtick, a $(...) or a
# $VAR is EXECUTED before this script starts, and nothing here can catch it -- the
# damage is done before argv exists. Measured 2026-09-09 on this very script: a log
# line containing `job` in backticks lost the word entirely ("mind a negy a  MEZoT
# irja at"), and the shell printed "job: command not found" where it was easy to miss.
#
# The project CLAUDE.md says the same about inter-agent messages, with the same
# reasoning, and the same fix: the text must not be born on the command line.
TEXT=""
if [ "${1:-}" = "-f" ]; then
  [ -r "${2:-}" ] || { echo "nem olvashato: ${2:-}" >&2; exit 2; }
  TEXT=$(cat "$2")
else
  TEXT="${1:-}"
fi

if [ -z "$TEXT" ]; then
  echo "hasznalat: naplo.sh [--agent NEV] \"[LEZART|FOLYAMATBAN] Tema. Mi tortent.\"" >&2
  exit 2
fi

# A hand-typed clock is exactly what this script exists to prevent. Catch it
# whether or not the "## " prefix is there.
if printf '%s' "$TEXT" | grep -qE '^(##[[:space:]]*)?[0-9]{1,2}[:.][0-9]{2}'; then
  echo "ELUTASITVA: a szoveg IDOVEL kezdodik. Az idot ez a szkript teszi ra, mert" >&2
  echo "  kezzel irva elcsuszik (2026-09-08: +154 perc, 2026-09-09: +119 perc)." >&2
  echo "  Ird a szoveget idopont nelkul, allapot-cimkevel: \"[LEZART] Tema. ...\"" >&2
  exit 1
fi

# The status label is the other half of the rule: an append-only log makes a partial
# result look like a final one unless the line says which it is.
if ! printf '%s' "$TEXT" | grep -qE '^\[(LEZART|FOLYAMATBAN|HELYESBITES)\]'; then
  echo "FIGYELEM: nincs allapot-cimke a sor elejen ([LEZART] / [FOLYAMATBAN] /" >&2
  echo "  [HELYESBITES]). Egy naplo append-only, tehat a reszeredmeny vegeredmenynek" >&2
  echo "  latszik nelkule. Folytatom, de gondold at." >&2
fi

NOW=$(date +%H:%M)
OUT=$(TEXT="$TEXT" NOW="$NOW" AGENT="$AGENT" REPO="$REPO" python3 - <<'PY'
import json, os, urllib.request
repo = os.environ["REPO"]
tok = open(os.path.join(repo, "store", ".dashboard-token")).read().strip()
body = {"agent_id": os.environ["AGENT"],
        "content": f'## {os.environ["NOW"]} -- {os.environ["TEXT"]}'}
req = urllib.request.Request(
    "http://localhost:3420/api/daily-log",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
print(urllib.request.urlopen(req).read().decode()[:120])
PY
) || { echo "A POST elhalt: $OUT" >&2; exit 1; }

echo "naplozva $NOW (a szkript orajarol) | valasz: $OUT"
