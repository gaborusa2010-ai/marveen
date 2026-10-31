#!/usr/bin/env python3
"""Short dashboard CLI.

Usage: tabla.py [--ki NAME] [--json] COMMAND ...
Commands: kartya, lista, komment, mozgat, uzenet, uzenetek, lezar,
          emlek (uj|keres|ir), naplo
Run ``tabla.py COMMAND -h`` for command-specific options.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLEET_PATH = os.path.join(ROOT, "seed-skills", "fleet-helper", "scripts", "fleet.py")
_spec = importlib.util.spec_from_file_location("tabla_fleet", FLEET_PATH)
fleet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fleet)

TZ = ZoneInfo("Europe/London")
STATUSES = ("planned", "in_progress", "waiting", "testing", "done")
PRIORITIES = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
NEW_MARK = "UJ, NINCS A LEIRASBAN"
EXCLUDED_AUTHORS = {"system", "kartya-es-ertesites"}
EXCLUDED_PREFIXES = ("[Heti átnézés]",)
MAX_TEXT = 100_000


class UsageError(Exception):
    pass


class DashboardDown(Exception):
    pass


def _call(method, path, payload=None, headers=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(fleet.base_url() + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + fleet.token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
        response_headers = dict(exc.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardDown(str(exc)) from exc
    try:
        body = json.loads(raw)
    except ValueError:
        body = raw
    return status, body, response_headers


def call(identity, method, path, payload=None):
    return _call(method, path, payload, {"X-Agent-Id": identity})


def error_text(body):
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False)


def require_ok(status, body, special=()):
    if status in special:
        return False
    if status >= 400:
        print(error_text(body)[:500], file=sys.stderr)
        raise SystemExit(1)
    return True


def identity_from_cwd():
    cwd = os.path.realpath(os.getcwd())
    parts = cwd.split(os.sep)
    for index, part in enumerate(parts[:-1]):
        if part == "agents" and index + 1 < len(parts):
            return parts[index + 1]
    if cwd == os.path.realpath(ROOT):
        return "marveen"
    raise UsageError("cannot derive identity from cwd; pass --ki NAME")


def text_arg(value, label="TEXT"):
    value = sys.stdin.read() if value == "-" else value
    if not value or not value.strip():
        raise UsageError(f"{label} must not be empty")
    if len(value) > MAX_TEXT:
        raise UsageError(f"{label} is longer than {MAX_TEXT} characters")
    return value


def json_out(body):
    print(json.dumps(body, ensure_ascii=False))


def board(identity):
    status, body, _ = call(identity, "GET", "/api/kanban?includeArchived=1")
    require_ok(status, body)
    if not isinstance(body, list):
        raise UsageError("dashboard returned an invalid kanban list")
    return body


def resolve_card(identity, ref, full=True):
    wanted = ref.strip().lstrip("#")
    if not wanted:
        raise UsageError("empty card reference")
    cards = board(identity)
    if wanted.isdigit() and len(wanted) < 8:
        hits = [card for card in cards if str(card.get("seq")) == wanted]
    else:
        hits = [card for card in cards if str(card.get("id", "")).lower().startswith(wanted.lower())]
    if not hits:
        raise UsageError(f"card not found: {ref}")
    if len(hits) > 1:
        raise UsageError(f"ambiguous card reference: {ref}")
    card = hits[0]
    if full:
        quoted = urllib.parse.quote(str(card["id"]), safe="")
        status, detailed, _ = call(identity, "GET", "/api/kanban/" + quoted)
        require_ok(status, detailed)
        if isinstance(detailed, dict):
            card = detailed
    return card


def when(timestamp, time_only=False):
    if not timestamp:
        return "-"
    try:
        moment = dt.datetime.fromtimestamp(int(timestamp), TZ)
    except (ValueError, TypeError, OSError):
        return "-"
    return moment.strftime("%H:%M" if time_only else "%Y-%m-%d %H:%M")


def counts_for_state(comment):
    if comment.get("automated"):
        return False
    if str(comment.get("author") or "").strip().lower() in EXCLUDED_AUTHORS:
        return False
    return not str(comment.get("content") or "").startswith(EXCLUDED_PREFIXES)


def cmd_kartya(args, identity):
    card = resolve_card(identity, args.ref)
    path = "/api/kanban/%s/comments" % urllib.parse.quote(str(card["id"]), safe="")
    status, comments, _ = call(identity, "GET", path)
    require_ok(status, comments)
    comments = sorted(comments or [], key=lambda item: item.get("created_at") or 0)
    if args.json:
        json_out({"card": card, "comments": comments})
        return
    desc_at = card.get("description_updated_at")
    print(f'#{card.get("seq")} ({card.get("id")}) {card.get("title")}')
    print(f'status: {card.get("status")}   assignee: {card.get("assignee") or "-"}   priority: {card.get("priority")}' +
          ("   ARCHIVALT" if card.get("archived_at") else ""))
    if card.get("status") == "waiting" or card.get("wait_kind") or card.get("wait_note"):
        print(f'varakozas: {card.get("wait_kind") or "-"}   ok: {card.get("wait_note") or "-"}   eddig: {when(card.get("wait_until"))}')
    if "state_stale" in card:
        print(f'leiras frissitve: {when(desc_at)}   state_stale: {str(card.get("state_stale")).lower()}   stale_comments: {card.get("stale_comments", 0)}')
    else:
        print(f"leiras frissitve: {when(desc_at)}   (a dashboard nem kuld state_stale mezot)")
    print("\n=== LEIRAS ===")
    print((card.get("description") or "").rstrip() or "(ures)")
    print(f"\n=== MEGJEGYZESEK ({len(comments)}) ===")
    if not comments:
        print("(nincs megjegyzes)")
    new = 0
    for comment in comments:
        newer = desc_at is not None and (comment.get("created_at") or 0) > desc_at
        mark = ""
        if newer and counts_for_state(comment):
            mark = f"  <<< {NEW_MARK}"
            new += 1
        elif newer:
            mark = "  (ertesites, nem szamit)"
        print(f'\n--- {when(comment.get("created_at"))}  {comment.get("author")}{mark}')
        print(str(comment.get("content") or "").rstrip())
    if new:
        print(f"\n{new} megjegyzes ujabb a leirasnal: a MOSTANI ALLAPOT blokk frissitendo.")


def cmd_lista(args, identity):
    cards = board(identity)
    rows = [c for c in cards if not c.get("archived_at") and c.get("status") != "done"]
    if not args.mind:
        rows = [c for c in rows if str(c.get("assignee") or "").lower() == identity.lower()]
    if args.statusz:
        rows = [c for c in rows if c.get("status") == args.statusz]
    rows.sort(key=lambda c: (PRIORITIES.get(c.get("priority"), 99), int(c.get("seq") or 0)))
    if args.json:
        json_out(rows)
    else:
        for card in rows:
            print(f'#{card.get("seq")}  {card.get("status")}  {card.get("priority")}  {card.get("title")}')


def updated_description(description, state):
    stamp = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")
    block = f"MOSTANI ALLAPOT (frissitve {stamp}): {state}"
    lines = (description or "").splitlines()
    # Only an existing state block is replaced: a description that has a "---" line
    # but no state block on top would otherwise lose everything above that line.
    if lines and lines[0].startswith("MOSTANI ALLAPOT") and "---" in lines:
        return block + "\n" + "\n".join(lines[lines.index("---"):])
    return block + "\n---\n" + (description or "")


def cmd_komment(args, identity):
    content = text_arg(args.text if args.text is not None else open(args.fajl, encoding="utf-8").read())
    state = text_arg(args.allapot, "--allapot") if args.allapot is not None else None
    card = resolve_card(identity, args.ref)
    quoted = urllib.parse.quote(str(card["id"]), safe="")
    status, body, _ = call(identity, "POST", f"/api/kanban/{quoted}/comments",
                           {"author": identity, "content": content})
    require_ok(status, body)
    if state is not None:
        description = updated_description(card.get("description") or "", state)
        put_status, put_body, _ = call(identity, "PUT", f"/api/kanban/{quoted}",
                                       {"description": description, "actor": identity})
        require_ok(put_status, put_body)
    if args.json:
        json_out(body)
    else:
        print(body.get("id") if isinstance(body, dict) else body)


def cmd_mozgat(args, identity):
    card = resolve_card(identity, args.ref, full=False)
    path = "/api/kanban/%s/move" % urllib.parse.quote(str(card["id"]), safe="")
    status, body, _ = call(identity, "POST", path, {"status": args.status, "actor": identity})
    require_ok(status, body)
    json_out(body) if args.json else print("ok")


def cmd_uzenet(args, identity):
    status, body, _ = call(identity, "GET", f"/api/messages/{args.id}")
    require_ok(status, body)
    if args.json:
        json_out(body)
        return
    print(f'from: {body.get("from_agent")}\nto: {body.get("to_agent")}\nstatus: {body.get("status")}\ntime: {when(body.get("created_at"))}')
    print(body.get("content") or "")


def cmd_uzenetek(args, identity):
    path = "/api/messages?agent=" + urllib.parse.quote(identity, safe="")
    if args.limit is not None:
        path += "&limit=" + str(args.limit)
    status, body, _ = call(identity, "GET", path)
    require_ok(status, body)
    if isinstance(body, dict):
        body = body.get("messages", [])
    rows = [m for m in body if str(m.get("to_agent") or "").lower() == identity.lower()]
    if args.nyitott:
        rows = [m for m in rows if m.get("status") not in ("done", "failed")]
    if args.json:
        json_out(rows)
        return
    for msg in rows:
        content = " ".join(str(msg.get("content") or "").split())[:120]
        print(f'{msg.get("id")}  {msg.get("from_agent")}  {msg.get("status")}  {when(msg.get("created_at"), True)}  {content}')


def cmd_lezar(args, identity):
    payload = {"status": "done"}
    if args.eredmeny is not None:
        payload["result"] = text_arg(args.eredmeny, "--eredmeny")
    if args.csendes:
        payload["notify"] = False
    status, body, _ = call(identity, "PUT", f"/api/messages/{args.id}", payload)
    if status == 409:
        print("already closed")
        return
    require_ok(status, body)
    json_out(body) if args.json else print("ok")


def cmd_emlek(args, identity):
    if args.emlek_cmd == "uj":
        content = text_arg(args.text)
        keywords = args.kulcs or ""
        status, body, _ = call(identity, "POST", "/api/memories", {
            "agent_id": identity, "content": content, "category": args.kat, "keywords": keywords})
    elif args.emlek_cmd == "keres":
        query = text_arg(args.query, "QUERY")
        path = "/api/memories?agent=%s&q=%s" % (
            urllib.parse.quote(identity, safe=""), urllib.parse.quote(query, safe=""))
        if args.szigoru:
            path += "&strict=1"
        status, body, headers = call(identity, "GET", path)
        require_ok(status, body)
        label = next((v for k, v in headers.items() if k.lower() == "x-memory-search"), None)
        if label is None:
            print("warning: X-Memory-Search header missing", file=sys.stderr)
        else:
            # stderr under --json, so stdout stays one parseable JSON document
            print(label, file=sys.stderr if args.json else sys.stdout)
            if "relaxed=true" in label.lower():
                print("warning: approximate matches (relaxed=true)", file=sys.stderr)
        if args.json:
            json_out(body)
        else:
            for row in body:
                print(f'{row.get("id")}  {row.get("category")}  {row.get("content")}')
        return
    else:
        content = text_arg(args.text)
        status, body, _ = call(identity, "PUT", f"/api/memories/{args.id}", {"content": content})
    require_ok(status, body)
    if isinstance(body, dict) and body.get("owner_mismatch"):
        print("owner_mismatch: " + error_text(body["owner_mismatch"]), file=sys.stderr)
    json_out(body) if args.json else print(body.get("id", "ok") if isinstance(body, dict) else body)


def cmd_naplo(args, identity):
    content = text_arg(args.text)
    status, body, _ = call(identity, "POST", "/api/daily-log", {"agent_id": identity, "content": content})
    require_ok(status, body)
    json_out(body) if args.json else print("ok")


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ki", help="agent identity (otherwise derived from cwd)")
    ap.add_argument("--json", action="store_true", help="print raw parsed response as JSON")
    # The same two options are accepted after the command too ("tabla.py kartya 730 --json");
    # SUPPRESS keeps a subcommand from resetting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ki", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)
    add = lambda parent, name: parent.add_parser(name, parents=[common])
    p = add(sub, "kartya"); p.add_argument("ref"); p.set_defaults(func=cmd_kartya)
    p = add(sub, "lista"); p.add_argument("--mind", action="store_true"); p.add_argument("--statusz", choices=STATUSES); p.set_defaults(func=cmd_lista)
    p = add(sub, "komment"); p.add_argument("ref"); group = p.add_mutually_exclusive_group(required=True); group.add_argument("text", nargs="?"); group.add_argument("--fajl"); p.add_argument("--allapot"); p.set_defaults(func=cmd_komment)
    p = add(sub, "mozgat"); p.add_argument("ref"); p.add_argument("status", choices=STATUSES); p.set_defaults(func=cmd_mozgat)
    p = add(sub, "uzenet"); p.add_argument("id", type=int); p.set_defaults(func=cmd_uzenet)
    p = add(sub, "uzenetek"); p.add_argument("--nyitott", action="store_true"); p.add_argument("--limit", type=int); p.set_defaults(func=cmd_uzenetek)
    p = add(sub, "lezar"); p.add_argument("id", type=int); p.add_argument("--eredmeny"); p.add_argument("--csendes", action="store_true"); p.set_defaults(func=cmd_lezar)
    p = add(sub, "emlek"); esub = p.add_subparsers(dest="emlek_cmd", required=True)
    e = add(esub, "uj"); e.add_argument("text"); e.add_argument("--kat", choices=("hot", "warm", "cold", "shared"), default="warm"); e.add_argument("--kulcs"); e.set_defaults(func=cmd_emlek)
    e = add(esub, "keres"); e.add_argument("query"); e.add_argument("--szigoru", action="store_true"); e.set_defaults(func=cmd_emlek)
    e = add(esub, "ir"); e.add_argument("id", type=int); e.add_argument("text"); e.set_defaults(func=cmd_emlek)
    p = add(sub, "naplo"); p.add_argument("text"); p.set_defaults(func=cmd_naplo)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.strip())
        return 0
    ap = parser()
    try:
        args = ap.parse_args(argv)
        identity = args.ki or identity_from_cwd()
        args.func(args, identity)
        return 0
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    except DashboardDown as exc:
        print(f"dashboard unreachable: {exc}", file=sys.stderr)
        return 3
    except (OSError, RuntimeError) as exc:
        # fleet.token() raises RuntimeError when no store/.dashboard-token is above cwd
        # (e.g. in a git worktree); set CLAW_DIR to the install root.
        print(f"usage error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
