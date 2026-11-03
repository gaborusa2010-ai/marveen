#!/bin/bash
# subagent-write-scope.py (#782): a sub-agent's file tools write only to scratch or a side worktree.
set -u
H="$(cd "$(dirname "$0")/.." && pwd)/hooks/subagent-write-scope.py"
MAIN="$(git -C "$(dirname "$H")" worktree list --porcelain | sed -n '1s/^worktree //p')"
fail=0; ok() { echo "ok - $1"; }; bad() { echo "not ok - $1"; fail=1; }
run() { python3 -c 'import json,sys;a=sys.argv;d={"tool_name":a[1],"tool_input":{"file_path":a[2]},"cwd":"/home/x","session_id":"sid1"}
if a[3]=="1": d["agent_id"]="a1"
print(json.dumps(d))' "$1" "$2" "$3" | python3 "$H" 2>/dev/null; echo $?; }
SCR="/tmp/claude-$(id -u)/-home-x/sid1"

[ "$(run Write "$MAIN/x.txt" 0)" = 0 ] && ok "main session untouched" || bad "main session"
[ "$(run Write "$MAIN/x.txt" 1)" = 2 ] && ok "sub-agent: main checkout blocked" || bad "main checkout"
[ "$(run Edit "$MAIN/../$(basename "$MAIN")/x" 1)" = 2 ] && ok "sub-agent: ../ trick blocked" || bad "dotdot"
[ "$(run Write "$SCR/scratchpad/r.md" 1)" = 0 ] && ok "sub-agent: scratch allowed" || bad "scratch"
[ "$(run Write "${SCR}x/r.md" 1)" = 2 ] && ok "sub-agent: scratch-prefix lookalike blocked" || bad "prefix"
[ "$(run Write "relative.txt" 1)" = 2 ] && ok "sub-agent: relative path blocked" || bad "relative"
[ "$(run Write "/etc/passwd" 1)" = 2 ] && ok "sub-agent: outside path blocked" || bad "outside"
side="$(git -C "$MAIN" worktree list --porcelain | sed -n 's/^worktree //p' | sed -n '2p')"
if [ -n "$side" ]; then [ "$(run Write "$side/f.txt" 1)" = 0 ] && ok "sub-agent: side worktree allowed" || bad "side worktree"; fi
out=$(printf '{"agent_id":"a", broken' | python3 "$H" 2>/dev/null; echo $?); [ "$out" = 2 ] && ok "broken payload blocked" || bad "broken"
out=$(printf '' | python3 "$H"; echo $?); [ "$out" = 0 ] && ok "empty payload passes" || bad "empty"
exit $fail
