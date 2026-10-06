#!/usr/bin/env python3
"""Send the owner one weekly link when stale kanban cards need decisions.

The dashboard owns the stale-card rules. This small timer client only reads
that endpoint and sends one plain-text Telegram message, so the weekly reminder
does not need a model turn and cannot silently reimplement the filters.

Usage
-----
  kanban-heti-lista.py
  kanban-heti-lista.py --dry-run
  kanban-heti-lista.py --days 21

Exit 0 means sent or nothing to send, 1 means the dashboard could not be read,
and 2 means Telegram did not confirm the message.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(REPO, "store", ".dashboard-token")
CHAT_ID = "8547011582"


def _tg_env():
    local = os.path.join(REPO, ".claude", "channels", "telegram", ".env")
    if os.path.exists(local):
        return local
    return os.path.expanduser("~/.claude/channels/telegram/.env")


TG_ENV = _tg_env()


def dashboard_url():
    # Same precedence as cfg() in src/config.ts: a value saved on the Settings
    # page (store/config-overrides.json) wins over .env.
    try:
        override = json.load(open(os.path.join(REPO, "store", "config-overrides.json"), encoding="utf-8"))
        value = str(override.get("DASHBOARD_PUBLIC_URL") or "").strip()
        if value:
            return value.rstrip("/")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        text = open(os.path.join(REPO, ".env"), encoding="utf-8").read()
    except OSError:
        text = ""
    match = re.search(r"(?m)^\s*DASHBOARD_PUBLIC_URL\s*=\s*['\"]?([^'\"\n#]+)", text)
    return (match.group(1).strip().rstrip("/") if match else "http://localhost:3420")


def fetch_stale(days):
    token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    request = urllib.request.Request(
        f"http://localhost:3420/api/kanban/stale?days={days}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def send(text):
    """True only when Telegram itself says ok."""
    try:
        env = open(TG_ENV, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print(f"HIBA: a Telegram token nem olvasható ({exc})", file=sys.stderr)
        return False
    match = re.search(r"[0-9]+:[A-Za-z0-9_-]+", env)
    if not match:
        print("HIBA: nincs bot token a channels .env-ben", file=sys.stderr)
        return False
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "15", f"https://api.telegram.org/bot{match.group()}/sendMessage",
             "--data-urlencode", f"chat_id={CHAT_ID}", "--data-urlencode", f"text={text}",
             "--data-urlencode", "disable_web_page_preview=true"],
            capture_output=True, text=True, timeout=25,
        )
        return json.loads(result.stdout or "{}").get("ok") is True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args(argv)
    try:
        result = fetch_stale(args.days)
        count = int(result["count"])
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"HIBA: a kanban lista nem olvasható ({exc})", file=sys.stderr)
        return 1
    if count == 0:
        return 0
    link = dashboard_url() + "/?review=stale#kanban"
    message = f"Heti átnézés: {count} régóta álló kártya vár döntésre (marad / ötletbe / lezár): {link}"
    if args.dry_run:
        print(message)
        return 0
    return 0 if send(message) else 2


if __name__ == "__main__":
    raise SystemExit(main())
