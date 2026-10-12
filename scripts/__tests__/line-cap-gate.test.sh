#!/usr/bin/env bash
# line-cap-gate.py: 500 allowed, 501 denied, shrinking an oversized file allowed,
# generated CLAUDE.md blocks not counted, Bash growth reported once.
set -u
G="$(cd "$(dirname "$0")/.." && pwd)/hooks/line-cap-gate.py"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok(){ echo "ok   $1"; }; bad(){ echo "FAIL $1"; fail=1; }
mkdir -p "$T/skills/x"; F="$T/skills/x/SKILL.md"
case_(){ # name expect(allow|deny) json
  out=$(printf '%s' "$3" | python3 "$G")
  if [[ "$2" == deny ]]; then [[ "$out" == *'"deny"'* ]] && ok "$1" || bad "$1"
  else [[ -z "$out" ]] && ok "$1" || bad "$1: $out"; fi
}
mk(){ python3 -c 'import json,sys;print(json.dumps({"hook_event_name":"PreToolUse","tool_name":sys.argv[1],"tool_input":json.loads(sys.argv[2])}))' "$@"; }
seq 1 499 > "$F"
case_ "edit to 500 allowed" allow "$(mk Edit "{\"file_path\":\"$F\",\"old_string\":\"499\\n\",\"new_string\":\"499\\na\\n\"}")"
case_ "edit to 501 denied" deny "$(mk Edit "{\"file_path\":\"$F\",\"old_string\":\"499\\n\",\"new_string\":\"499\\na\\nb\\n\"}")"
seq 1 600 > "$F"
case_ "shrinking oversized allowed" allow "$(mk Edit "{\"file_path\":\"$F\",\"old_string\":\"600\\n\",\"new_string\":\"\"}")"
case_ "growing oversized denied" deny "$(mk Edit "{\"file_path\":\"$F\",\"old_string\":\"600\\n\",\"new_string\":\"600\\nz\\n\"}")"
case_ "other files ignored" allow "$(mk Write "{\"file_path\":\"$T/notes.md\",\"content\":\"$(printf 'x\\n%.0s' $(seq 700))\"}")"
C="$T/CLAUDE.md"
python3 - "$C" <<'PY'
import sys
gen="<!-- BEGIN GENERATED: g (auto) -->\n"+"g\n"*300+"<!-- END GENERATED: g -->\n"
open(sys.argv[1],"w").write("h\n"*450+gen)
PY
case_ "CLAUDE.md generated block not counted" allow "$(mk Edit "{\"file_path\":\"$C\",\"old_string\":\"h\\n\",\"new_string\":\"h\\nnew\\n\"}")"
mkdir -p "$T/home/.claude/skills/y"; seq 1 510 > "$T/home/.claude/skills/y/SKILL.md"
P="{\"hook_event_name\":\"PostToolUse\",\"tool_name\":\"Bash\",\"cwd\":\"$T/cwd-linecap-test\"}"
printf '%s' "$P" | HOME="$T/home" python3 "$G" >/dev/null
echo x >> "$T/home/.claude/skills/y/SKILL.md"
out=$(printf '%s' "$P" | HOME="$T/home" python3 "$G"); [[ "$out" == *'"block"'* ]] && ok "bash growth reported" || bad "bash growth reported"
out=$(printf '%s' "$P" | HOME="$T/home" python3 "$G"); [[ -z "$out" ]] && ok "reported only once" || bad "reported only once"
rm -f "$(dirname "$G")/../../store/line-cap/snapshot-"*cwd-linecap-test.json
exit $fail
