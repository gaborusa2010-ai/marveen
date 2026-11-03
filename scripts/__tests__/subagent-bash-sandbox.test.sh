#!/bin/bash
# subagent-bash-sandbox.py (#782): a sub-agent's shell gets a read-only filesystem; the main session is untouched.
set -u
H="$(cd "$(dirname "$0")/.." && pwd)/hooks/subagent-bash-sandbox.py"
fail=0; ok() { echo "ok - $1"; }; bad() { echo "not ok - $1"; fail=1; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
echo decoy > "$W/decoy"

out=$(echo '{"tool_name":"Bash","tool_input":{"command":"echo hi"},"cwd":"/","session_id":"s"}' | python3 "$H"); rc=$?
[ $rc = 0 ] && [ -z "$out" ] && ok "main session: no rewrite" || bad "main session rc=$rc out=$out"

out=$(printf '' | python3 "$H"); rc=$?
[ $rc = 0 ] && [ -z "$out" ] && ok "empty payload passes" || bad "empty payload rc=$rc"

printf '{"agent_id":"a1", broken' | python3 "$H" 2>/dev/null; rc=$?
[ $rc = 2 ] && ok "broken sub-agent payload blocked" || bad "broken sub-agent payload rc=$rc"

# 100 kB: below the kernel's 128 kB single-argument limit the wrapped `bash -c` must fit in
out=$(python3 -c 'import json;print(json.dumps({"agent_id":"a","tool_name":"Bash","tool_input":{"command":"echo " + "x"*100000},"cwd":"/"}))' | python3 "$H"); rc=$?
[ $rc = 0 ] && echo "$out" | grep -q probe-sandbox && ok "large command wrapped" || bad "large command rc=$rc"

case_cmd="echo over > $W/decoy; echo \"rc=\$?\"; echo \"it's\""
python3 -c 'import json,sys;print(json.dumps({"agent_id":"a","tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$case_cmd" "$W" \
  | python3 "$H" | python3 -c 'import json,sys;print(json.load(sys.stdin)["hookSpecificOutput"]["updatedInput"]["command"])' > "$W/run.sh"
res=$(bash "$W/run.sh" 2>&1)
echo "$res" | grep -q "Read-only file system" && [ "$(cat "$W/decoy")" = decoy ] && ok "write blocked, decoy intact" || bad "write not blocked: $res"
echo "$res" | grep -q "it's" && ok "quoting survives" || bad "quoting: $res"

out=$(echo '{"agent_id":"a","tool_name":"Bash","tool_input":{"command":"echo hi"},"cwd":"/"}' | PATH=/nonexistent /usr/bin/python3 "$H" 2>&1); rc=$?
[ $rc = 2 ] && ok "no bwrap on PATH: blocked" || bad "no bwrap rc=$rc out=$out"
exit $fail
