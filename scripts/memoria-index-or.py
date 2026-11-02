#!/usr/bin/env python3
"""Daily sentinel for the shared memory index (card 5f372007 / #763).

WHAT IT DOES: measures MEMORY.md the way the harness loads it (via
memory-index-guard.py, same numbers as its report mode) and, when the loaded size
is above the guard's TARGET, posts ONE inter-agent message with the numbers and
the guard's candidate list. It never writes the index: archiving stays a human
decision (owner, tg 3070, 2026-09-19: chose fewer new pages and pairing lines,
not bulk archiving). The `--apply` of the guard is the tool; this is the alarm.

WHY A SENTINEL AND NOT THE OLD GATE: scripts/memory-index-gate.sh is a
schedule-runner preCheck that measures BYTES of the whole file (header included)
against 24400, and card #349 (0eda731e, 2026-10-17) decided not to wire it because
its premise (a shared index) is false for the MAIN agent. The premise IS true for
five sub-agents (anorak, cody, forgacs, margo, roxy: .claude-config/projects ->
~/.claude/projects), so SOMETHING must watch the file; this does, with the
harness-count numbers, outside the LLM turn.

LEVELS (loaded lines / chars):
  rendben  <= 160 / 20000  (guard TARGET)      -> no message
  FIGYELEM  > 160 / 20000                       -> message
  SURGOS    > 180 / 23000                       -> message
  CSONKUL   > 200 / 25000  (harness LIMIT)      -> message, the tail is ALREADY lost
  HIBA      index missing, unreadable, not UTF-8,
            empty or without a single entry     -> message, exit 2 (a quiet sentinel
                                                   on a missing file is the failure
                                                   this exists to prevent)

ABSENCE: the installer wires OnFailure= to `--report-failure`, which posts one
message when a run dies without having posted (traceback, exit 3); a HIBA run that
already posted is recognised from the fresh state file and not repeated.

REPEATS: the same level is reported once per 7 days (the index sits above target
for days while someone decides); a HIGHER level is reported at once; HIBA every
run. `--force` posts regardless. The state file carries last_level / last_message_at.

Exit: 0 measured (message sent or not needed), 2 index missing/empty (message
sent), 3 the message could not be posted (stderr says why). `--dry-run` prints the
payload instead of posting. Overrides for tests: MEMORY_INDEX_DIR, DASHBOARD_URL,
DASHBOARD_TOKEN_FILE, MEMORIA_INDEX_OR_STATE.

Installed by scripts/install-memoria-index-or.sh as a systemd user timer (05:40).
"""
import argparse, importlib.util, json, os, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
URGENT_LINES, URGENT_CHARS = 180, 23000
MAX_LISTED = 15
REPEAT_DAYS = 7
ORDER = {"rendben": 0, "FIGYELEM": 1, "SURGOS": 2, "CSONKUL": 3, "HIBA": 9}


def read_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def already_reported(prev, level, now):
    """True when the same (or a lower) level went out less than REPEAT_DAYS ago."""
    if level == "HIBA" or not prev.get("last_message_at"):
        return False
    if ORDER.get(level, 0) > ORDER.get(prev.get("last_level"), 0):
        return False
    return now - prev["last_message_at"] < REPEAT_DAYS * 86400


def load_guard():
    spec = importlib.util.spec_from_file_location("memory_index_guard", os.path.join(HERE, "memory-index-guard.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def measure(mdir, g):
    """Returns (level, text, plan) where level is one of the LEVELS above."""
    idx = os.path.join(mdir, "MEMORY.md")
    try:
        if os.path.getsize(idx) == 0:
            return "HIBA", f"az index URES: {idx}", None
        p = g.plan(mdir)
    except OSError as e:
        return "HIBA", f"az index nem olvashato: {idx}: {e.strerror or e}", None
    except ValueError as e:   # UnicodeDecodeError: bytes that are not UTF-8
        return "HIBA", f"az index nem olvashato: {idx}: {e}", None
    n_l, n_c = p["lines"], p["chars"]
    if n_l == 0 or not p["entries"]:
        # Bytes without a single `- [` entry: a wiped or header-only index. This is
        # the loss the sentinel exists for, not a healthy small index.
        return "HIBA", f"az indexben NINCS egyetlen bejegyzes sem ({n_l} sor / {n_c} kar): {idx}", p
    if n_l > g.LIMIT_LINES or n_c > g.LIMIT_CHARS:
        level = "CSONKUL"
    elif n_l > URGENT_LINES or n_c > URGENT_CHARS:
        level = "SURGOS"
    elif n_l > g.TARGET_LINES or n_c > g.TARGET_CHARS:
        level = "FIGYELEM"
    else:
        level = "rendben"
    text = (f"{n_l} sor / {n_c} kar (plafon {g.LIMIT_LINES}/{g.LIMIT_CHARS}, "
            f"cel {g.TARGET_LINES}/{g.TARGET_CHARS})")
    if level == "CSONKUL":
        text += "; a betolto MAR VAG, a fajl vege nem toltodik be"
    if level != "rendben":
        files = [f for _, f in p["move"]]
        if files:
            shown = ", ".join(files[:MAX_LISTED])
            more = f" +{len(files) - MAX_LISTED} tovabbi" if len(files) > MAX_LISTED else ""
            text += (f". A guard {len(files)} sort mozgatna a cel ala ({shown}{more}). "
                     f"Jelentes: python3 scripts/memory-index-guard.py; az --apply gazdai dontes (tg 3070).")
        else:
            text += ". NINCS archivalhato jelolt (csak user/feedback maradt): emberi dontes kell, kartya acb30fbe."
    return level, text, p


def post(url, token_file, sender, to, content, timeout=30):
    token = open(token_file, encoding="utf-8").read().strip()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/messages", method="POST",
        data=json.dumps({"from": sender, "to": to, "content": content}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("id")


def write_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except OSError as e:
        print(f"state nem irhato: {path}: {e}", file=sys.stderr)


FRESH_SECONDS = 600


def report_failure(state_path, url, token_file, sender, to, dry_run):
    """The absence alarm. systemd runs this when the sentinel service failed (non-zero
    exit, traceback, missing python). If the failed run itself already posted
    (HIBA exits 2 after posting), the state file is fresh and carries message_id:
    nothing more to say. Otherwise one message goes out. Always exit 0: a failing
    failure-handler would only loop."""
    prev = read_state(state_path)
    now = int(time.time())
    age = now - int(prev.get("measured_at") or 0)
    if age < FRESH_SECONDS and prev.get("message_id"):
        print(f"a bukott futas mar jelzett (id={prev['message_id']}, {age} mp-e), nincs tovabbi uzenet")
        return 0
    why = prev.get("post_error") or ("a futas nem irt state-et" if age >= FRESH_SECONDS else prev.get("level", "?"))
    content = (f"[MEMORIA-INDEX] HIBA: az orszem futasa elhalt (systemd OnFailure, {why}). "
               f"Nezd: journalctl --user -u marveen-memoria-index-or; state: {state_path}")
    if dry_run:
        print(json.dumps({"from": sender, "to": to, "content": content}, ensure_ascii=False))
        return 0
    try:
        mid = post(url, token_file, sender, to, content)
        print(f"hiany-uzenet elkuldve, id={mid}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"a hiany-uzenet sem ment el ({url}): {e}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the payload, post nothing")
    ap.add_argument("--force", action="store_true", help="post even if the same level went out this week")
    ap.add_argument("--from", dest="sender", default="marveen")
    ap.add_argument("--to", default="marveen")
    ap.add_argument("--report-failure", action="store_true",
                    help="systemd OnFailure= hook: say that the sentinel run died, unless it already posted")
    a = ap.parse_args(argv)
    g = load_guard()
    mdir = os.environ.get("MEMORY_INDEX_DIR") or g.M
    url = os.environ.get("DASHBOARD_URL", "http://localhost:3420")
    token_file = os.environ.get("DASHBOARD_TOKEN_FILE", os.path.join(REPO, "store", ".dashboard-token"))
    state_path = os.environ.get("MEMORIA_INDEX_OR_STATE", os.path.join(REPO, "store", "memoria-index-or-state.json"))
    if a.report_failure:
        return report_failure(state_path, url, token_file, a.sender, a.to, a.dry_run)

    now = int(time.time())
    prev = read_state(state_path)
    level, text, p = measure(mdir, g)
    state = {"measured_at": now, "level": level, "index": os.path.join(mdir, "MEMORY.md")}
    for k in ("last_level", "last_message_at", "last_message_id"):
        if k in prev:
            state[k] = prev[k]
    if p:
        state.update(lines=p["lines"], chars=p["chars"], candidates=len(p["move"]))
    print(f"{level}: {text}")
    rc = 2 if level == "HIBA" else 0
    if level == "rendben":
        # The quiet week ends here: a later rise is news again, not a repeat.
        for k in ("last_level", "last_message_at", "last_message_id"):
            state.pop(k, None)
        write_state(state_path, state)
        return rc
    if not a.force and already_reported(prev, level, now):
        age_h = (now - prev["last_message_at"]) // 3600
        print(f"mar jelezve: {prev.get('last_level')} szint {age_h} oraja (id={prev.get('last_message_id')}), "
              f"{REPEAT_DAYS} napig nem ismetlem; --force kuld ujra")
        write_state(state_path, state)
        return rc

    content = f"[MEMORIA-INDEX] {level}: {text}"
    if a.dry_run:
        print(json.dumps({"from": a.sender, "to": a.to, "content": content}, ensure_ascii=False))
        state["dry_run"] = True
        write_state(state_path, state)
        return rc
    try:
        mid = post(url, token_file, a.sender, a.to, content)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"az uzenet nem ment el ({url}): {e}", file=sys.stderr)
        state["post_error"] = str(e)
        write_state(state_path, state)
        return 3
    if not mid:
        print("az uzenet nem ment el: a valaszban nincs id", file=sys.stderr)
        state["post_error"] = "no id in response"
        write_state(state_path, state)
        return 3
    print(f"uzenet elkuldve, id={mid}")
    state.update(message_id=mid, last_level=level, last_message_at=now, last_message_id=mid)
    write_state(state_path, state)
    return rc


if __name__ == "__main__":
    sys.exit(main())
