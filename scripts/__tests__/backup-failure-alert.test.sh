#!/usr/bin/env bash
# A FAILED backup must not stay in logs/backup.log, which nobody reads: on exit 6
# backup.sh queues a [MENTES] alert to the install's main agent through
# scripts/agent-msg.sh. Until this test (card #578, 2026-10-24) only the exit
# code was covered, and the alert had never fired in production, so nothing
# showed whether the send itself still worked. A stub agent-msg.sh records its
# argv instead of sending: no network, no real queue, no message to anyone.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../backup.sh"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1 -- got '$2', want '$3'"; fail=$((fail+1)); fi; }

# $1 = the stub's exit code. Each call appends its three args, one per line.
mkrepo() {
  local t; t="$(mktemp -d "${TMPDIR:-/tmp}/bk-alert.XXXXXX")"
  mkdir -p "$t/scripts/lib" "$t/store" "$t/home/.claude/skills/x" "$t/home/.claude/scheduled-tasks/y"
  echo s > "$t/home/.claude/skills/x/SKILL.md"; echo s > "$t/home/.claude/scheduled-tasks/y/SKILL.md"
  echo db > "$t/store/claudeclaw.db"; echo ok > "$t/store/good.txt"
  cp "$SRC" "$t/scripts/backup.sh"
  cp "$(dirname "$SRC")/lib/archive-list-has.sh" "$t/scripts/lib/"
  cat > "$t/scripts/agent-msg.sh" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$1" "\$2" "\$3" >> "$t/stub-calls"
exit ${1:-0}
STUB
  chmod +x "$t/scripts/agent-msg.sh"
  git -C "$t" init -q && git -C "$t" add -A && git -C "$t" -c user.name=t -c user.email=t@t commit -qm init
  echo "$t"
}
run() { ( cd "$1" && HOME="$1/home" BACKUP_DIR="$1/bk" KEEP=1 bash scripts/backup.sh >"$1/out.log" 2>&1 ); echo $?; }
break_staging() { echo x > "$1/store/unreadable.txt"; chmod 000 "$1/store/unreadable.txt"; }
calls() { if [ -f "$1/stub-calls" ]; then echo $(( $(wc -l < "$1/stub-calls") / 3 )); else echo 0; fi; }
arg() { sed -n "${2}p" "$1/stub-calls"; }

# Negative control: a good run sends nothing.
T="$(mkrepo 0)"; check "clean run exits 0" "$(run "$T")" "0"
check "  ... and queues no alert" "$(calls "$T")" "0"
rm -rf "$T"

T="$(mkrepo 0)"; break_staging "$T"; rc="$(run "$T")"; chmod 600 "$T/store/unreadable.txt"
check "failed run exits 6" "$rc" "6"
check "  ... queues exactly one alert" "$(calls "$T")" "1"
check "  ... from the default main agent" "$(arg "$T" 1)" "marveen"
check "  ... to the default main agent" "$(arg "$T" 2)" "marveen"
check "  ... text is the [MENTES] failure notice" "$(arg "$T" 3 | grep -c '^\[MENTES\] A napi mentes ellenorzese ELBUKOTT')" "1"
rm -rf "$T"

# The recipient comes from .env the way the app reads it: last definition wins,
# quotes stripped. A wrong id makes the alert go nowhere (card a8a92d55).
T="$(mkrepo 0)"; printf 'MAIN_AGENT_ID=old-main\nMAIN_AGENT_ID="probe-main"\n' > "$T/.env"
break_staging "$T"; rc="$(run "$T")"; chmod 600 "$T/store/unreadable.txt"
check "MAIN_AGENT_ID from .env: exits 6" "$rc" "6"
check "  ... alert goes to the last, unquoted definition" "$(arg "$T" 2)" "probe-main"
rm -rf "$T"

# The send runs as `bash agent-msg.sh`, so the exec bit is not needed -- and it
# is not there: commit 6f7e65d1 (2026-09-16) stored agent-msg.sh as 100644, and
# an `-x` guard then skipped the alert without a word on every failed run.
T="$(mkrepo 0)"; chmod -x "$T/scripts/agent-msg.sh"
break_staging "$T"; rc="$(run "$T")"; chmod 600 "$T/store/unreadable.txt"
check "agent-msg.sh without exec bit: exits 6" "$rc" "6"
check "  ... and still queues the alert" "$(calls "$T")" "1"
rm -rf "$T"

# A messaging failure must neither change the exit code nor stay silent.
T="$(mkrepo 1)"; break_staging "$T"; rc="$(run "$T")"; chmod 600 "$T/store/unreadable.txt"
check "alert send fails: still exits 6" "$rc" "6"
check "  ... and says the alert was not queued" "$(grep -c 'could not queue the failure alert for marveen' "$T/out.log")" "1"
rm -rf "$T"

T="$(mkrepo 0)"; rm "$T/scripts/agent-msg.sh"; break_staging "$T"; rc="$(run "$T")"; chmod 600 "$T/store/unreadable.txt"
check "no agent-msg.sh at all: still exits 6" "$rc" "6"
check "  ... and says the alert was not queued" "$(grep -c 'no scripts/agent-msg.sh' "$T/out.log")" "1"
rm -rf "$T"

echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
