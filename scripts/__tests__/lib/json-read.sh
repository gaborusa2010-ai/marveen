# shellcheck shell=bash
# JSON readers for the shell suites, python3 only (no jq).
#
# jq is NOT installed on every host this repo runs on -- the owner's own box
# has none -- and a suite that exits "jq is required" never measures the thing
# it was written for. The gates themselves already read JSON without jq
# (MEMIDXJQ924); the suites now do the same.
#
# jqv FILE PATH
#   Prints the value at PATH the way `jq -r PATH FILE` would for the subset
#   the suites use: `.a`, `.a.b`, `.a[0].b`, optionally followed by `| length`.
#   A string prints raw; null/true/false print as the jq literals; numbers print
#   as JSON numbers; arrays/objects print as JSON. A missing key or an index
#   past the end is null, like jq. An unreadable or invalid file prints nothing
#   (jq would fail, and the suites discard its stderr).
jqv() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null
import json, re, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        v = json.load(fh)
except Exception:
    sys.exit(1)
path = sys.argv[2].strip()
want_len = False
m = re.fullmatch(r"(.*?)\s*\|\s*length", path)
if m:
    path, want_len = m.group(1).strip(), True
if path != "." and not re.fullmatch(r"(\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+", path):
    sys.exit(3)                       # unsupported path: never guess
for key, idx in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path):
    if v is None:
        break
    if key:
        v = v.get(key) if isinstance(v, dict) else sys.exit(5)
    else:
        i = int(idx)
        v = (v[i] if i < len(v) else None) if isinstance(v, list) else sys.exit(5)
if want_len:
    v = 0 if v is None else len(v) if isinstance(v, (str, list, dict)) else abs(v)
if isinstance(v, str):
    print(v)
else:
    print(json.dumps(v, ensure_ascii=False))
PY
}

# json_valid FILE -- exit 0 iff FILE parses as JSON (`jq -e . FILE` minus the
# falsy-value quirk; the state files are always objects).
json_valid() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$1" >/dev/null 2>&1
}

# json_fields_num_or_null FILE FIELD... -- exit 0 iff FILE is a JSON object
# that HAS every FIELD, and each one is a number or null (never a string,
# never a bool, never absent).
json_fields_num_or_null() {
  python3 - "$@" <<'PY' >/dev/null 2>&1
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ok = isinstance(d, dict) and all(
    k in d and (d[k] is None or (isinstance(d[k], (int, float)) and not isinstance(d[k], bool)))
    for k in sys.argv[2:])
sys.exit(0 if ok else 1)
PY
}

# json_eval FILE EXPR -- exit 0 iff FILE parses as JSON and the Python
# expression EXPR is truthy, with the parsed document bound to `d` and
# `is_num(x)` meaning "a JSON number" (jq `type == "number"`: a bool is NOT a
# number, unlike Python's isinstance). For the one-off shape checks that
# `jq -e '...'` used to do; an unreadable file or a raising EXPR is a failure,
# never a pass.
json_eval() {
  python3 - "$1" "$2" <<'PY' >/dev/null 2>&1
import json, sys
def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)
d = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if eval(sys.argv[2], {"d": d, "is_num": is_num}) else 1)
PY
}
