#!/usr/bin/env python3
"""Send the owner a Telegram question with tap-to-answer buttons (reply keyboard).

Why a reply keyboard and nothing else: a tapped button arrives as an ordinary
text message, which the channel plugin delivers. Inline buttons (callback_query)
are dropped by the plugin unless they are its own permission prompts, and poll
answers are never delivered. Verified 2026-09-24: msg 3934 sent, tap arrived as
msg 3936.

Usage: tg-valasztas.py [--agent NAME] "Question text" "Option 1" "Option 2" [...]
       tg-valasztas.py [--agent NAME] --valasz "Acknowledgement text"

--valasz sends a plain message that REMOVES the keyboard (remove_keyboard). Use it
for the first answer after the owner answered, tapped OR typed (msg 3999): one_time_keyboard only collapses the
keyboard, it stays reachable behind the keyboard icon and can be tapped again
(msg 3992 was a second tap on the 3987 keyboard). Owner's request, msg 3995.

Every agent sends through ITS OWN bot (owner's decision, msg 3943: "Igen, mindenkire").
The agent is --agent, else guessed from the working directory (agents/<name>/...),
else the main agent. Sub-agent token: agents/<name>/.claude/channels/telegram/.env.
Prints the sent message_id; exit 1 if Telegram did not answer ok.
"""
import json, re, sys, time, urllib.parse, urllib.request

import os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE + "/scripts/hooks")


def marker_path(agent):
    # Read by hooks/telegram-reply-directive.py: while it exists, the next inbound
    # Telegram message gets a directive to answer with --valasz.
    return f"{BASE}/store/.tg-gombsor-{agent}.json"
def clear_placeholders(agent, chat, tok):
    # This script sends outside the `reply` tool, so the PostToolUse hook
    # (telegram_progress_reply_clear.py) never fires: the "Dolgozom rajta"
    # placeholder stayed on screen and the Stop reply-guard blocked the turn
    # although the answer had gone out (2026-09-25, placeholders 4012/4014/4018).
    # Do the same cleanup here. The session id is unknown to a CLI call, so every
    # pending entry for this chat in this agent's state dir is cleared.
    sd = (f"{BASE}/agents/{agent}/.claude/channels/telegram" if agent and agent != "marveen"
          else os.environ.get("TELEGRAM_STATE_DIR") or f"{BASE}/.claude/channels/telegram")
    import glob
    for path in glob.glob(os.path.join(sd, "progress", "*.json")):
        try:
            pend = json.load(open(path))
        except Exception:
            continue
        if not isinstance(pend, list):
            continue
        keep = [p for p in pend if str(p.get("chat_id")) != str(chat)]
        for p in pend:
            if str(p.get("chat_id")) == str(chat):
                try:
                    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/deleteMessage",
                        urllib.parse.urlencode({"chat_id": p["chat_id"],
                                                "message_id": p["message_id"]}).encode(), timeout=8)
                except Exception:
                    pass
        try:
            if keep:
                json.dump(keep, open(path, "w"))
            else:
                os.remove(path)
        except Exception:
            pass

ENV = BASE + "/.env"
OWNER_CHAT = "8547011582"

def pick_agent(argv):
    if len(argv) > 2 and argv[1] == "--agent":
        return argv[2], argv[3:]
    m = re.search(r"/agents/([^/]+)", os.getcwd())
    return (m.group(1) if m else None), argv[1:]

def main():
    agent, rest = pick_agent(sys.argv)
    close = bool(rest) and rest[0] == "--valasz"
    if close:
        rest = rest[1:]
    if (close and len(rest) != 1) or (not close and len(rest) < 3):
        sys.exit(__doc__)
    text, opts = rest[0], rest[1:]
    if agent and agent != "marveen":
        env = open(f"{BASE}/agents/{agent}/.claude/channels/telegram/.env").read()
        chat = OWNER_CHAT
    else:
        env = open(ENV).read()
        chat = re.search(r"^ALLOWED_CHAT_ID=(.*)$", env, re.M).group(1).strip()
    tok = re.search(r"^TELEGRAM_BOT_TOKEN=(.*)$", env, re.M).group(1).strip()
    if close:
        kb = {"remove_keyboard": True}
    else:
        kb = {"keyboard": [[{"text": o}] for o in opts],
              "one_time_keyboard": True, "resize_keyboard": True}
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "reply_markup": json.dumps(kb, ensure_ascii=False)}).encode()
    r = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data))
    if not r.get("ok"):
        print("FAIL", r.get("description"), file=sys.stderr)
        sys.exit(1)
    mid = r["result"]["message_id"]
    clear_placeholders(agent, chat, tok)
    me = agent or main_id()
    if close:
        try:
            os.remove(marker_path(me))
        except FileNotFoundError:
            pass
    else:
        with open(marker_path(me), "w") as fh:
            json.dump({"message_id": mid, "ts": int(time.time())}, fh)
    # Log the outbound in the ledger, as the reply tool's PostToolUse hook would,
    # so the Stop reply-guard does not block after a --valasz OR after a button
    # question sent as the answer to an inbound message.
    try:
        import ledger_lib
        ledger_lib.log_outbound(me, chat, text, mid)
    except Exception as e:
        print("ledger write failed:", e, file=sys.stderr)
    print(mid)


def main_id():
    try:
        import ledger_lib
        return ledger_lib.main_agent_id()
    except Exception:
        return "marveen"

if __name__ == "__main__":
    main()
