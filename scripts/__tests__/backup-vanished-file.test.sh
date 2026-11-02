#!/usr/bin/env bash
# backup.sh must NOT fail when a listed file is deleted between the file list
# and staging (2026-11-02 21:16: a sub-agent removed a scratch worktree mid-run
# and 2140 entries failed the archive, #765). The file is skipped, named at the
# end of MANIFEST.txt, and the run exits 0. A file that still exists but cannot
# be copied keeps failing (exit 6). Synthetic repo; a PATH-shim cp deletes the
# file right before copying it, which is the race made deterministic.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../backup.sh"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1 -- got '$2', want '$3'"; fail=$((fail+1)); fi; }

mkrepo() {
  local t; t="$(mktemp -d "${TMPDIR:-/tmp}/bk-vanish.XXXXXX")"
  mkdir -p "$t/scripts/lib" "$t/store" "$t/home/.claude/skills/x" "$t/home/.claude/scheduled-tasks/y" "$t/shim"
  echo s > "$t/home/.claude/skills/x/SKILL.md"; echo s > "$t/home/.claude/scheduled-tasks/y/SKILL.md"
  echo db > "$t/store/claudeclaw.db"
  cp "$SRC" "$t/scripts/backup.sh"
  cp "$(dirname "$SRC")/lib/archive-list-has.sh" "$t/scripts/lib/"
  echo ok > "$t/store/good.txt"; echo bye > "$t/store/vanish.txt"
  git -C "$t" init -q && git -C "$t" add -A && git -C "$t" -c user.name=t -c user.email=t@t commit -qm init
  # The shim deletes store/vanish.txt the moment backup.sh tries to copy it.
  cat > "$t/shim/cp" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do case "\$a" in */store/vanish.txt) rm -f "\$a" ;; esac; done
exec $(command -v cp) "\$@"
EOF
  chmod +x "$t/shim/cp"
  echo "$t"
}
run() { ( cd "$1" && PATH="$1/shim:$PATH" HOME="$1/home" BACKUP_DIR="$1/bk" KEEP=1 bash scripts/backup.sh >"$1/out.log" 2>&1 ); echo $?; }

T="$(mkrepo)"
check "file deleted after listing -> exit 0" "$(run "$T")" "0"
check "  ... and a NOTE says so" "$(grep -c 'NOTE 1 entr' "$T/out.log")" "1"
A="$(ls "$T"/bk/claudeclaw-*.tar.gz 2>/dev/null | head -1)"
M="$(tar -xzOf "$A" MANIFEST.txt 2>/dev/null)"
check "  ... manifest lists it as gone" "$(grep -cx 'gone: repo/store/vanish.txt' <<<"$M")" "1"
check "  ... and not as a path entry" "$(grep -cx 'repo/store/vanish.txt' <<<"$M")" "0"
check "  ... the other files are archived" "$(tar -tzf "$A" | grep -cx 'repo/store/good.txt')" "1"
rm -rf "$T"

# Still present but unreadable: not a vanish, the run must keep failing.
T="$(mkrepo)"; rm "$T/shim/cp"
echo x > "$T/store/locked.txt"; chmod 000 "$T/store/locked.txt"
rc="$(run "$T")"; chmod 600 "$T/store/locked.txt"
check "unreadable existing file -> still exit 6" "$rc" "6"
rm -rf "$T"

echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
