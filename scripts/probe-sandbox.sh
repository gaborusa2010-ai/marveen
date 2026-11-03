#!/bin/bash
# Run a probe command with the whole filesystem READ-ONLY (#780: on 2026-11-03 03:24 a review
# helper's "real bash" probe ran `rm -rf ~/marveen`). Only a fresh throwaway dir is writable; it is
# also HOME, TMPDIR and the cwd. No network (--unshare-net). The dir is removed afterwards unless
# PROBE_KEEP=1; its path is printed on stderr either way.
#
# Usage: bash scripts/probe-sandbox.sh <command...>
#   e.g. bash scripts/probe-sandbox.sh bash -c "$CASE"
#        bash scripts/probe-sandbox.sh python3 -I /path/to/runner.py cases.json
# Files the probe needs: read them by absolute path (readable), write results under $HOME.
# Exit code: the command's own. 125: sandbox could not start (do NOT fall back to a real shell).
#
# Options (env), used by scripts/hooks/subagent-bash-sandbox.py:
#   PROBE_CWD=<dir>     run there instead of the throwaway dir (it stays read-only unless in PROBE_RW)
#   PROBE_REAL_HOME=1   keep the real HOME (read-only) so per-user tools and packages still load
#   PROBE_RW=<a:b:...>  extra existing dirs bound writable (missing ones are skipped)
#   PROBE_QUIET=1       do not print the writable dir
set -u
[ $# -ge 1 ] || { echo "usage: probe-sandbox.sh <command...>" >&2; exit 2; }
command -v bwrap >/dev/null || { echo "probe-sandbox: bwrap missing, refusing to run unsandboxed" >&2; exit 125; }
BOX="$(mktemp -d "${TMPDIR:-/tmp}/probe-sandbox.XXXXXX")" || exit 125
[ "${PROBE_KEEP:-0}" = 1 ] || trap 'rm -rf "$BOX"' EXIT
[ "${PROBE_QUIET:-0}" = 1 ] || echo "probe-sandbox: writable dir $BOX" >&2
RW=()
IFS=: read -r -a _rw <<< "${PROBE_RW:-}"
for d in "${_rw[@]}"; do [ -n "$d" ] && [ -d "$d" ] && RW+=(--bind "$d" "$d"); done
H="$BOX"; [ "${PROBE_REAL_HOME:-0}" = 1 ] && H="$HOME"
bwrap --ro-bind / / --dev /dev --proc /proc \
  --bind "$BOX" "$BOX" "${RW[@]}" --chdir "${PROBE_CWD:-$BOX}" \
  --setenv HOME "$H" --setenv TMPDIR "$BOX" \
  --unshare-net --unshare-pid --die-with-parent --new-session \
  "$@"
exit $?
