#!/bin/bash
# Skill Index Generator
# Generates a Level 0 index of all available skills (name + description only)
# This keeps token usage low while making all skills discoverable
#
# Usage: skill-index.sh [AGENT_DIR]
#   Without arg: generates global index at ~/.claude/skills/.skill-index.md
#   With AGENT_DIR: generates merged index (global + agent-specific) at
#                   <AGENT_DIR>/.claude/skills/.skill-index.md
#                   (backward-compatible format for no-arg callers)

GLOBAL_SKILLS_DIR="$HOME/.claude/skills"

# PWDDRIFT901 (Cody, 2026-09-01, merve). A hivo oldalon a dokumentalt alak `"$PWD"`, ami
# felteszi, hogy az agens cwd-je az agens GYOKERE. Ez a session indulasakor igaz, kozben NEM
# marad az: egy agens belep a sajat projekt-mappajaba (peldaul teszteket futtatni), es ha a
# PreCompact eppen akkor sul el, akkor a projekt-mappa lesz az AGENT_DIR. A `mkdir -p` alabb
# NEMAN letrehozza ott a .claude/skills szerkezetet, a VALODI index pedig nem frissul --
# vagyis pontosan az az allapot all elo, amit ez a javitas megszuntetni akart.
# Ugyanaz a hibaosztaly, mint a LEDGERCWD828 a ledgerben: a cwd valtozik a sessionon belul,
# ezert identitast NEM vezetunk le belole. Egy helyen normalizalunk, hat szoveg helyett.
normalize_agent_dir() {
  local d
  d="$(cd "$1" 2>/dev/null && pwd -P)" || { printf '%s' "$1"; return; }
  local install
  install="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  case "$d/" in
    "$install"/agents/*)
      # <install>/agents/<id>[/barmi...] -> <install>/agents/<id>
      local rest="${d#"$install"/agents/}"
      printf '%s' "$install/agents/${rest%%/*}"
      ;;
    "$install"/*|"$install"/)
      # a telepites gyokere alatt, de nem agens-mappaban -> a fo agens gyokere
      printf '%s' "$install"
      ;;
    *) printf '%s' "$d" ;;
  esac
}

if [ $# -ge 1 ]; then
  AGENT_DIR="$(normalize_agent_dir "$1")"
  if [ "$AGENT_DIR" != "$1" ]; then
    echo "skill-index: az utat normalizaltam: $1 -> $AGENT_DIR" >&2
  fi
  AGENT_SKILLS_DIR="$AGENT_DIR/.claude/skills"
  OUTPUT="$AGENT_SKILLS_DIR/.skill-index.md"
  MERGED=1
  mkdir -p "$AGENT_SKILLS_DIR"
else
  AGENT_DIR=""
  AGENT_SKILLS_DIR=""
  OUTPUT="$GLOBAL_SKILLS_DIR/.skill-index.md"
  MERGED=0
fi

if [ ! -d "$GLOBAL_SKILLS_DIR" ]; then
  echo "No global skills directory found at $GLOBAL_SKILLS_DIR"
  exit 0
fi

# IDEMPOTENCIA (2026-09-04, merve). A regeneralas eddig MINDIG felulirta a
# celfajlt, mert az utolso sor egy `Generálva: <ido>` bélyeget hordozott -- tehát
# minden futas megvaltoztatta a fajl tartalmat, akkor is, ha egyetlen skill sem
# valtozott.
#
# Amig ez csak egy felesleges iras volt, nem szamitott. 2026-09-04 ota szamit: a
# globalis index a VEDETT skill-mappaban all, es a PostToolUse audit minden
# valtozasarol `TREE_CHANGE_UNGRANTED` riasztast kuld. A memoria-heartbeat SKILL.md
# pedig MINDEN korben eloirja a globalis regent -- vagyis a sajat utemezett
# feladatom orankent gyartott egy hamis biztonsagi riasztast. Az audit sajat
# fejlec-kommentje pont errol ir: egy riasztas, ami farkast kialt, elobb-utobb
# elnemul, es akkor a VALODI mellett is elmegyunk.
#
# Ezert a fajl ideiglenesbe epul, es csak akkor kerul a helyere, ha a TARTALMA
# tenyleg mas. Az idobelyeg megmarad, de mostantol mast jelent, es tobbet: nem azt,
# mikor futott a szkript, hanem azt, mikor valtozott utoljara a skill-keszlet.
# Az osszevetes ezert hagyja figyelmen kivul magat a belyeget.
SKILL_INDEX_TMP="$(mktemp)"
trap 'rm -f "$SKILL_INDEX_TMP"' EXIT
OUTPUT_FINAL="$OUTPUT"
OUTPUT="$SKILL_INDEX_TMP"

echo "# Skill Index (Level 0)" > "$OUTPUT"
echo "" >> "$OUTPUT"

if [ "$MERGED" = "1" ]; then
  echo "Ez az ágensspecifikus skill index: globális (~/.claude/skills) és ágensspecifikus (.claude/skills) skilleket egyaránt tartalmaz." >> "$OUTPUT"
  echo "Ha egy skill releváns, olvasd be a teljes SKILL.md-t (Level 1)." >> "$OUTPUT"
  echo "Ha segédfájlokra is szükség van, nézd meg a scripts/ és references/ mappákat (Level 2)." >> "$OUTPUT"
  echo "" >> "$OUTPUT"
  echo "| Skill | Leírás | Scope |" >> "$OUTPUT"
  echo "|-------|--------|-------|" >> "$OUTPUT"
else
  echo "Ez az összes elérhető skill rövid indexe. Csak a nevet és leírást tartalmazza (Level 0)." >> "$OUTPUT"
  echo "Ha egy skill releváns, olvasd be a teljes SKILL.md-t (Level 1)." >> "$OUTPUT"
  echo "Ha segédfájlokra is szükség van, nézd meg a scripts/ és references/ mappákat (Level 2)." >> "$OUTPUT"
  echo "" >> "$OUTPUT"
  echo "| Skill | Leírás |" >> "$OUTPUT"
  echo "|-------|--------|" >> "$OUTPUT"
fi

SKILL_COUNT=0

index_skills_dir() {
  local dir="$1"
  local scope="$2"  # only used when MERGED=1
  for skill_dir in "$dir"/*/; do
    [ -d "$skill_dir" ] || continue
    local skill_md="$skill_dir/SKILL.md"
    [ -f "$skill_md" ] || continue

    local name
    name=$(grep -m1 "^name:" "$skill_md" 2>/dev/null | sed 's/^name: *//' | tr -d '"' | tr -d "'")
    if [ -z "$name" ]; then
      name=$(basename "$skill_dir")
    fi

    local desc
    # UTF8CUT901 (2026-09-01): `cut -c` vag BAJTOT, nem karaktert -- egy 120. bajtnal
    # kettevagott ekezetes betu ERVENYTELEN UTF-8-at hagy a fajlban. A kovetkezmenye nem
    # kozmetikai: a `file` mar "Non-ISO extended-ASCII"-nak latja, es a rendszer grep-je
    # (ugrep) BINARISKENT atugorja -- nulla talalat, exit 1, SEMMI kiiras. Ketszer hittem
    # aznap, hogy egy frissen letrehozott skill nincs benne az indexben, holott benne volt.
    # Egy indexfajl, amire nem lehet keresni, pontosan annyit er, mint egy ures index.
    # python3 mindig van (a projekt sajat szabalya), es karakterre vag.
    desc=$(grep -m1 "^description:" "$skill_md" 2>/dev/null | sed 's/^description: *//' | tr -d '"' | tr -d "'" \
             | python3 -c 'import sys; sys.stdout.write(sys.stdin.read().rstrip("\n")[:120])')
    if [ -z "$desc" ]; then
      desc="(nincs leírás)"
    fi

    if [ "$MERGED" = "1" ]; then
      echo "| \`$name\` | $desc | $scope |" >> "$OUTPUT"
    else
      echo "| \`$name\` | $desc |" >> "$OUTPUT"
    fi
    SKILL_COUNT=$((SKILL_COUNT + 1))
  done
}

index_skills_dir "$GLOBAL_SKILLS_DIR" "global"

# HAROM skill-mappa letezik, nem ketto, es a harmadik 2026-09-09-ig kimaradt innen:
#   a GLOBALIS (a HOME alatt, lasd GLOBAL_SKILLS_DIR) -- a teljes flottae
#   a PROJEKT-szintu <install>/.claude/skills -- a harness ezt is betolti MINDEN
#     agensnek, mert az <install> a sub-agensek munkakonyvtaranak a szuloje
#   az AGENS-sajat <install>/agents/<id>/.claude/skills
# Cody merte 2026-09-09 14:1x: a frissen ujragenerált indexeben a projekt-szintu mappa
# tizenot skillje kozul EGY SEM szerepelt, koztuk ket olyan, amit aznap patcheltunk.
# A harness listaja latta oket, a MIENK nem -- tehat aki az indexet grepeli (mi, minden
# nap), egy tizenot elemu vak foltot hordozott. A fo-agensnel ez nem latszott, mert nala
# az AGENT_SKILLS_DIR EPPEN a projekt-szintu mappa; ezert szurjuk ki az egyezest lent.
if [ "$MERGED" = "1" ]; then
  PROJECT_SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/.claude/skills"
  if [ -d "$PROJECT_SKILLS_DIR" ] \
     && [ "$PROJECT_SKILLS_DIR" != "$AGENT_SKILLS_DIR" ] \
     && [ "$PROJECT_SKILLS_DIR" != "$GLOBAL_SKILLS_DIR" ]; then
    index_skills_dir "$PROJECT_SKILLS_DIR" "project"
  fi
fi

if [ "$MERGED" = "1" ] && [ -d "$AGENT_SKILLS_DIR" ]; then
  index_skills_dir "$AGENT_SKILLS_DIR" "agent"
fi

echo "" >> "$OUTPUT"
echo "_${SKILL_COUNT} skill indexelve. Generálva: $(date '+%Y-%m-%d %H:%M')_" >> "$OUTPUT"

# A belyeg nelkuli alak a dontes alapja. `Generálva:` utan mindent levagunk, tehat
# a darabszam (`_29 skill indexelve.`) SZAMIT -- az a keszlet resze --, az ido nem.
strip_stamp() { sed 's/\(Generálva:\).*/\1/' "$1"; }

if [ -f "$OUTPUT_FINAL" ] && strip_stamp "$OUTPUT" | cmp -s - <(strip_stamp "$OUTPUT_FINAL"); then
  echo "Skill index unchanged: $OUTPUT_FINAL ($SKILL_COUNT skills)"
else
  cat "$OUTPUT" > "$OUTPUT_FINAL"
  echo "Skill index generated: $OUTPUT_FINAL ($SKILL_COUNT skills)"
fi
