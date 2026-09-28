#!/usr/bin/env bash
# Guards the differential backup and the image-archive hand-off.
#
# Both were built on 2026-09-22 on the owner's instruction (Telegram msg 3533,
# 3536, 3539). What makes them worth a test is that BOTH failure modes are
# silent: a differential that quietly omits a changed file still produces a
# valid archive that passes verification, and an image exclusion that fires too
# early removes a picture from the nightly backup before any image archive
# holds it. Neither shows up until a restore, which is the one moment when
# finding out is useless.
#
# Everything runs against throwaway directories -- BACKUP_DIR, BASELINE and
# IMAGE_STATE_FILE are overridable in the real scripts precisely so this test
# never touches the live backup set or the live state.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_SH="${BACKUP_SH:-${REPO_ROOT}/scripts/backup.sh}"
IMAGES_SH="${IMAGES_SH:-${REPO_ROOT}/scripts/backup-images.sh}"
DELTA_PY="${DELTA_PY:-${REPO_ROOT}/scripts/backup-delta.py}"
PASS=0; FAIL=0
ok()  { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d -t backup-diff-test.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

echo "backup.sh: differential mode and the image hand-off"
echo "===================================================="
echo

# ---------------------------------------------------------------------------
# 1. backup-delta.py: what counts as changed
# ---------------------------------------------------------------------------
mkdir -p "${TMP}/tree/sub" "${TMP}/home"
echo "alpha"  > "${TMP}/tree/a.txt"
echo "beta"   > "${TMP}/tree/sub/b.txt"
echo "gamma"  > "${TMP}/tree/sub/c.txt"
printf 'a.txt\nsub\n' > "${TMP}/repolist"
: > "${TMP}/homelist"

python3 "${DELTA_PY}" snapshot --baseline "${TMP}/base.json" \
  --repo-root "${TMP}/tree" --home "${TMP}/home" \
  --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
  --stamp test --archive test-full.tar.gz >/dev/null 2>&1

COUNT="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "${TMP}/base.json")"
# The list names one file and one DIRECTORY; the directory has to expand to its
# two files, or a whole subtree would be compared as a single entry and never
# look changed.
[[ "${COUNT}" == "3" ]] && ok "snapshot expands directories to files (3)" \
                        || bad "snapshot recorded ${COUNT} entries, expected 3"

python3 "${DELTA_PY}" filter --baseline "${TMP}/base.json" \
  --repo-root "${TMP}/tree" --home "${TMP}/home" \
  --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
  --out-repo "${TMP}/out-repo" --out-home "${TMP}/out-home" >/dev/null 2>&1
[[ ! -s "${TMP}/out-repo" ]] && ok "unchanged tree filters down to nothing" \
                             || bad "unchanged tree still lists $(wc -l < "${TMP}/out-repo") file(s)"

# A changed file and a brand-new one must BOTH survive the filter. The new file
# is the sharper case: it is absent from the baseline, so a lookup-based
# comparison that only asks "did this entry change?" would miss it entirely.
echo "alpha-modositva" > "${TMP}/tree/a.txt"
echo "delta"           > "${TMP}/tree/sub/d.txt"
python3 "${DELTA_PY}" filter --baseline "${TMP}/base.json" \
  --repo-root "${TMP}/tree" --home "${TMP}/home" \
  --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
  --out-repo "${TMP}/out-repo" --out-home "${TMP}/out-home" >/dev/null 2>&1
grep -qx 'a.txt'     "${TMP}/out-repo" && ok "a modified file survives the filter" \
                                       || bad "the modified file was filtered out"
grep -qx 'sub/d.txt' "${TMP}/out-repo" && ok "a new file survives the filter" \
                                       || bad "the new file was filtered out"
grep -qx 'sub/b.txt' "${TMP}/out-repo" && bad "an untouched file was NOT filtered out" \
                                       || ok "untouched files stay out"

# Negative control: with no baseline the filter must REFUSE, not silently
# return an empty list. An empty list would mean "nothing changed", which is
# the most dangerous possible answer to "I have no idea".
if python3 "${DELTA_PY}" filter --baseline "${TMP}/nonexistent.json" \
     --repo-root "${TMP}/tree" --home "${TMP}/home" \
     --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
     --out-repo "${TMP}/x" --out-home "${TMP}/y" >/dev/null 2>&1; then
  bad "filter accepted a missing baseline"
else
  ok "filter refuses a missing baseline (exit != 0)"
fi

# `due` must demand a FULL run when the baseline's archive is gone: a
# differential against a base nobody can extract is not a backup.
mkdir -p "${TMP}/bkempty"
python3 "${DELTA_PY}" due --baseline "${TMP}/base.json" --backup-dir "${TMP}/bkempty" >/dev/null 2>&1 \
  && ok "due says FULL when the base archive is missing" \
  || bad "due allowed a differential against a vanished base"

# ---------------------------------------------------------------------------
# 1b. INCREMENTAL since 2026-09-26: the chain advances, and a file changed
#     yesterday does not ride along again today (the owner's msg 3533/4139).
# ---------------------------------------------------------------------------
# Fresh baseline over the CURRENT tree (a.txt modified, d.txt new above).
python3 "${DELTA_PY}" snapshot --baseline "${TMP}/inc.json" \
  --repo-root "${TMP}/tree" --home "${TMP}/home" \
  --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
  --stamp t --archive inc-full.tar.gz >/dev/null 2>&1
filt() {
  python3 "${DELTA_PY}" filter --baseline "${TMP}/inc.json" \
    --repo-root "${TMP}/tree" --home "${TMP}/home" \
    --repolist "${TMP}/repolist" --homelist "${TMP}/homelist" \
    --out-repo "${TMP}/inc-out" --out-home "${TMP}/inc-outh" \
    --pending "${TMP}/pending.json" >/dev/null 2>&1
}
# Night 1: b.txt changes. mtime pushed forward so the same-second write is seen.
echo "beta-1" > "${TMP}/tree/sub/b.txt"; touch -d '+1 min' "${TMP}/tree/sub/b.txt"
filt
[[ "$(cat "${TMP}/inc-out")" == "sub/b.txt" ]] && ok "night 1 carries only the file changed that day" \
  || bad "night 1 carried: $(tr '\n' ' ' < "${TMP}/inc-out")"
python3 "${DELTA_PY}" advance --baseline "${TMP}/inc.json" --pending "${TMP}/pending.json" \
  --archive inc-1-diff.tar.gz >/dev/null 2>&1 && ok "advance accepts the pending state" \
  || bad "advance failed"
# Night 2: c.txt changes, b.txt does not. Under the old DIFFERENTIAL rule b.txt
# would ride along again -- that repetition is exactly what the owner refused.
echo "gamma-2" > "${TMP}/tree/sub/c.txt"; touch -d '+2 min' "${TMP}/tree/sub/c.txt"
filt
# Only meaningful when the filter produced today's file: an empty list from a
# failed filter would "not repeat" anything too.
if ! grep -qx 'sub/c.txt' "${TMP}/inc-out"; then
  bad "night 2 produced no usable list, the repeat check cannot run"
elif grep -qx 'sub/b.txt' "${TMP}/inc-out"; then
  bad "night 2 carried yesterday's b.txt again (differential, not incremental)"
else
  ok "night 2 does not repeat yesterday's change"
fi
grep -qx 'sub/c.txt' "${TMP}/inc-out" && ok "night 2 carries today's change" || bad "night 2 lost today's change"
python3 - "${TMP}/inc.json" <<'PY2' && ok "the chain keeps its full and lists the link" || bad "chain bookkeeping wrong"
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d["archive"] == "inc-full.tar.gz" and d["chain"] == ["inc-1-diff.tar.gz"] else 1)
PY2
# advance must refuse a missing pending file and leave the baseline alone.
cp "${TMP}/inc.json" "${TMP}/inc-before.json"
if python3 "${DELTA_PY}" advance --baseline "${TMP}/inc.json" --pending "${TMP}/nincs.json" \
     --archive x-diff.tar.gz >/dev/null 2>&1; then
  bad "advance accepted a missing pending state"
else
  cmp -s "${TMP}/inc.json" "${TMP}/inc-before.json" && ok "advance refuses a missing pending state, baseline untouched" \
    || bad "advance refused but changed the baseline"
fi
# due: full + link present -> incremental (exit 1); link missing -> FULL (exit 0).
mkdir -p "${TMP}/bkchain"; : > "${TMP}/bkchain/inc-full.tar.gz"; : > "${TMP}/bkchain/inc-1-diff.tar.gz"
python3 "${DELTA_PY}" due --baseline "${TMP}/inc.json" --backup-dir "${TMP}/bkchain" >/dev/null 2>&1 \
  && bad "due demanded FULL with an intact chain" || ok "intact chain -> incremental"
rm "${TMP}/bkchain/inc-1-diff.tar.gz"
python3 "${DELTA_PY}" due --baseline "${TMP}/inc.json" --backup-dir "${TMP}/bkchain" >/dev/null 2>&1 \
  && ok "a missing link -> FULL" || bad "due allowed an incremental on a broken chain"

# ---------------------------------------------------------------------------
# 2. backup-images.sh covered: only provably archived files
# ---------------------------------------------------------------------------
IMG_ONE="$(bash "${IMAGES_SH}" list 2>/dev/null | head -1)"
if [[ -z "${IMG_ONE}" ]]; then
  ok "no images on this host -- covered/exclusion checks skipped"
else
  python3 - "${TMP}/imgstate.json" "${REPO_ROOT}" "${IMG_ONE}" <<'PY'
import json, os, sys
state, root, rel = sys.argv[1:4]
st = os.lstat(os.path.join(root, rel))
json.dump({"fingerprint": "t", "archive": "t.tar.gz", "covered": {
    rel: [int(st.st_mtime), int(st.st_size)],
    rel + ".nemletezik": [1, 1],          # gone from disk -> must not be reported
    "__eltero__": [int(st.st_mtime), int(st.st_size)],
}}, open(state, "w"))
PY
  # A deliberately wrong size for a file that DOES exist: the covered list must
  # drop it, because that version of it is not what the archive holds.
  python3 - "${TMP}/imgstate.json" "${REPO_ROOT}" "${IMG_ONE}" <<'PY'
import json, os, sys
state, root, rel = sys.argv[1:4]
d = json.load(open(state))
st = os.lstat(os.path.join(root, rel))
del d["covered"]["__eltero__"]
d["covered"][rel] = [int(st.st_mtime), int(st.st_size)]
json.dump(d, open(state, "w"))
PY
  COV="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/imgstate.json" bash "${IMAGES_SH}" covered | wc -l | tr -d ' ')"
  [[ "${COV}" == "1" ]] && ok "covered reports only the file that matches on disk (1)" \
                        || bad "covered reported ${COV} file(s), expected 1"

  # Now break the recorded size for that same file: it must fall off the list.
  python3 - "${TMP}/imgstate.json" "${IMG_ONE}" <<'PY'
import json, sys
state, rel = sys.argv[1:3]
d = json.load(open(state))
d["covered"][rel][1] += 1
json.dump(d, open(state, "w"))
PY
  COV="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/imgstate.json" bash "${IMAGES_SH}" covered | wc -l | tr -d ' ')"
  [[ "${COV}" == "0" ]] && ok "covered drops a file whose size no longer matches" \
                        || bad "covered still reported ${COV} file(s) after the size changed"

  # And the property the whole design rests on: with NO state file at all,
  # covered prints nothing, so backup.sh subtracts nothing and the nightly
  # archive keeps carrying every picture.
  COV="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/nincs.json" bash "${IMAGES_SH}" covered | wc -l | tr -d ' ')"
  [[ "${COV}" == "0" ]] && ok "no image state -> covered is empty -> nothing is excluded" \
                        || bad "covered reported ${COV} file(s) with no state file"
fi

# ---------------------------------------------------------------------------
# 2b. backup-images.sh run: a change is not enough, the UNCOVERED volume decides
# ---------------------------------------------------------------------------
# Added 2026-09-23 after the owner stopped a 3.2 GB upload that 206 new avatar
# files had triggered. Both directions are checked, because a threshold that
# never fires and one that always fires look identical from the outside.
if [[ -z "${IMG_ONE}" ]]; then
  ok "no images on this host -- rebuild-threshold checks skipped"
else
  # A state whose fingerprint cannot match, so the early "unchanged" exit is
  # never the reason for what we observe below.
  mk_state() {  # $1 = state path, $2 = "all" | "none"
    python3 - "$1" "$2" "${REPO_ROOT}" "${IMAGES_SH}" <<'PY'
import json, os, subprocess, sys
state, mode, root, images_sh = sys.argv[1:5]
covered = {}
if mode == "all":
    out = subprocess.run(["bash", images_sh, "list"], cwd=root,
                         capture_output=True, text=True).stdout
    for rel in out.splitlines():
        if not rel:
            continue
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            continue
        covered[rel] = [int(st.st_mtime), int(st.st_size)]
json.dump({"fingerprint": "sosem-egyezik", "archive": "t.tar.gz",
           "covered": covered}, open(state, "w"))
PY
  }

  mk_state "${TMP}/img-all.json" all
  OUT="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/img-all.json" \
         IMAGE_REBUILD_MB=500 bash "${IMAGES_SH}" status 2>&1)"
  grep -q "NEM epitek ujra" <<<"${OUT}" \
    && ok "everything already covered -> under the threshold -> no rebuild" \
    || bad "rebuilt although nothing is uncovered: ${OUT}"

  mk_state "${TMP}/img-none.json" none
  OUT="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/img-none.json" \
         IMAGE_REBUILD_MB=500 bash "${IMAGES_SH}" status 2>&1)"
  grep -q "NEM epitek ujra" <<<"${OUT}" \
    && bad "threshold blocked a rebuild while NOTHING is covered: ${OUT}" \
    || ok "nothing covered -> over the threshold -> rebuild proceeds"

  # --force must ignore the threshold, or a real emergency rebuild is impossible.
  OUT="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/img-all.json" \
         IMAGE_REBUILD_MB=500 bash "${IMAGES_SH}" status --force 2>&1)"
  grep -q "NEM epitek ujra" <<<"${OUT}" \
    && bad "--force was stopped by the threshold: ${OUT}" \
    || ok "--force overrides the threshold"

  # 2c. Since 2026-09-26 a covered base means a SUPPLEMENT, not a rebuild: the
  # owner stopped a full 3.7 GB re-upload twice (09-23, 09-25). With the
  # default threshold (0), everything covered must still be a no-op, and one
  # uncovered file must go out as a supplement, not as the whole archive.
  OUT="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/img-all.json" \
         bash "${IMAGES_SH}" status 2>&1)"
  grep -q "NEM epitek ujra" <<<"${OUT}" \
    && ok "default threshold, everything covered -> nothing to ship" \
    || bad "shipped something although nothing is uncovered: ${OUT}"

  python3 - "${TMP}/img-all.json" "${TMP}/img-one.json" <<'PY2'
import json, sys
d = json.load(open(sys.argv[1]))
k = sorted(d["covered"])[0]
del d["covered"][k]
json.dump(d, open(sys.argv[2], "w"))
PY2
  OUT="$(cd "${REPO_ROOT}" && IMAGE_STATE_FILE="${TMP}/img-one.json" \
         bash "${IMAGES_SH}" status 2>&1)"
  grep -q "ebbol 1 fajl" <<<"${OUT}" && grep -q "kiegeszito csomagban" <<<"${OUT}" \
    && ok "one uncovered file on a covered base -> a one-file supplement" \
    || bad "expected a one-file supplement: ${OUT}"
fi

# ---------------------------------------------------------------------------
# 3. Retention must not orphan a differential
# ---------------------------------------------------------------------------
PRUNE="$(awk "/^python3 - \"\\\$\{BACKUP_DIR\}\" \"\\\$\{KEEP_FULL\}\" <<'PRUNE'/{f=1;next} /^PRUNE\$/{f=0} f" "${BACKUP_SH}")"
if [[ -z "${PRUNE}" ]]; then
  bad "could not extract the retention block from backup.sh (did it move?)"
else
  ok "extracted the retention block from backup.sh itself"
  mkdir -p "${TMP}/bk"
  for d in 01 02 03; do
    : > "${TMP}/bk/claudeclaw-202609${d}-010000-full.tar.gz"
    : > "${TMP}/bk/claudeclaw-202609${d}-010000-full.tar.gz.gpg"
    : > "${TMP}/bk/claudeclaw-202609${d}-020000-diff.tar.gz"
  done
  : > "${TMP}/bk/claudeclaw-20260830-010000.tar.gz"     # legacy, no suffix
  printf '%s\n' "${PRUNE}" | python3 - "${TMP}/bk" 2 >/dev/null 2>&1
  [[ -f "${TMP}/bk/claudeclaw-20260903-010000-full.tar.gz" ]] \
    && ok "newest full kept" || bad "newest full was pruned"
  [[ -f "${TMP}/bk/claudeclaw-20260902-010000-full.tar.gz" ]] \
    && ok "second-newest full kept (KEEP_FULL=2)" || bad "second full was pruned"
  [[ -f "${TMP}/bk/claudeclaw-20260902-020000-diff.tar.gz" ]] \
    && ok "a diff belonging to a kept full survives" || bad "a diff was orphaned from its kept base"
  [[ -f "${TMP}/bk/claudeclaw-20260901-010000-full.tar.gz" ]] \
    && bad "the third-oldest full was not pruned" || ok "the full beyond KEEP_FULL is pruned"
  [[ -f "${TMP}/bk/claudeclaw-20260901-010000-full.tar.gz.gpg" ]] \
    && bad "the .gpg twin outlived its .tar.gz" || ok "the .gpg twin is pruned with its archive"
  [[ -f "${TMP}/bk/claudeclaw-20260901-020000-diff.tar.gz" ]] \
    && bad "a diff older than the oldest kept full survived" || ok "diffs below the floor are pruned"
fi

echo
echo "PASS: ${PASS}  FAIL: ${FAIL}"
[[ "${FAIL}" -eq 0 ]] || exit 1
