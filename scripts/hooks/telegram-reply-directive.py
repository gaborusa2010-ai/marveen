#!/usr/bin/env python3
"""UserPromptSubmit hook: inject a reply-tool directive whenever an inbound
Telegram TEXT message arrives.

This is the salience half of the Telegram-reply enforcement (the Stop hook
telegram-reply-guard.py is the guarantee half). It mirrors the existing
voice-reply-directive.py -- voice messages already got a hook-injected directive,
plain text messages did not. Injecting the reminder at the TOP of the turn means
the model rarely reaches the Stop-hook block at all.

Claude Code injects a UserPromptSubmit hook's stdout directly into the model
prompt (plain text, no JSON wrapper). This hook is silent for any prompt that
does not carry a Telegram channel tag, so it never disturbs non-channel turns
(e.g. scheduled heartbeats). Never blocks: any error -> silent exit(0).
"""
import sys
import os
import json
import re
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHANNEL_RX = re.compile(
    r'<channel\s+source="plugin:telegram:telegram"([^>]*)>',
    re.DOTALL,
)


def _attr(attrs, name):
    m = re.search(name + r'="([^"]*)"', attrs)
    return m.group(1) if m else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = payload.get("prompt") or ""
    m = CHANNEL_RX.search(prompt)
    if not m:
        sys.exit(0)  # not a Telegram message -> stay silent
    chat_id = _attr(m.group(1), "chat_id") or "<a bejövő chat_id>"
    # Only a topic message carries this (TELEGRAM_TOPICS_ENABLED, plugin patch);
    # without it the directive is exactly the plain one.
    thread = _attr(m.group(1), "message_thread_id")
    target = f"chat_id={chat_id}, message_thread_id={thread}" if thread else f"chat_id={chat_id}"
    sys.stdout.write(
        f"[TELEGRAM-DIREKTÍVA] Ez az üzenet a Telegram csatornáról érkezett "
        f"({target}). A válaszod KÖTELEZŐEN a "
        f"mcp__plugin_telegram_telegram__reply toolon keresztül menjen ki "
        f"({target}) -- a sima assistant-szöveg NEM jut el hozzá, csak a "
        f"tmux-ba. Ha csak nyugtázás kell (ok/köszi), akkor sem baj, de érdemi "
        f"választ MINDIG a reply toollal küldj.\n"
    )
    _keyboard_notice(payload)
    sys.exit(0)


MARKER_MAX_AGE = 7 * 86400


def _keyboard_notice(payload):
    """If this agent left a reply keyboard open (scripts/tg-valasztas.py wrote a
    marker), the first answer must remove it, whether the owner tapped a button or
    typed (owner, msg 3995 + 3999). The plugin's reply tool cannot remove a keyboard."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ledger_lib
        agent = ledger_lib.agent_id_from_payload(payload)
        path = os.path.join(os.path.dirname(ledger_lib.db_path()), f".tg-gombsor-{agent}.json")
        with open(path) as fh:
            d = json.load(fh)
        if time.time() - int(d.get("ts", 0)) > MARKER_MAX_AGE:
            return
        sys.stdout.write(
            f"[GOMBSOR NYITVA] A {d.get('message_id')}-es kérdésed gombsora még kint van. "
            f"Az ELSŐ válaszod erre az üzenetre -- akár koppintott, akár begépelte -- "
            f"NE a reply toollal menjen, hanem így (ez tünteti el a gombokat): "
            f"python3 {os.path.join(REPO, 'scripts', 'tg-valasztas.py')} --agent {agent} "
            f"--valasz \"$(cat fajl)\"\n"
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
