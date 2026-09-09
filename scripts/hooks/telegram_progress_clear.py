#!/usr/bin/env python3
"""
Stop hook — two jobs, in one place so there is a single owner of the per-session
progress state at turn end (no racing Stop hooks):

  1) CLEAR: remove the "✍️ Dolgozom rajta…" placeholder(s) that the
     UserPromptSubmit hook (telegram_progress.py) posted for this session. Under
     normal operation the PostToolUse reply hook already cleared them the moment
     the agent replied, so usually there is nothing left to do.

  2) ENFORCE DELIVERY: if the turn was triggered by an inbound Telegram message
     but the agent ended the turn WITHOUT ever sending a reply to that chat
     (i.e. a placeholder is still pending), the answer only exists in the CLI /
     transcript — which the Telegram user never sees ("the agent looks frozen").
     This hook then:
       - on the first Stop: BLOCKS the stop and instructs the agent to send its
         answer via the Telegram `reply` tool (the agent re-enters and replies
         properly, with its own formatting);
       - if it STILL did not reply after that one nudge: delivers the agent's
         final answer (last assistant message from the transcript) to the chat
         as a guaranteed fallback, then removes the placeholder.
     So a Telegram turn always ends with the user getting the answer here.

Loop safety: a per-session `enforce-<sid>.marker` guarantees we block at most
once; `stop_hook_active` is also honored. Silent on stdout EXCEPT the single
decision JSON when blocking. Token/state dir resolution mirrors the plugin
(TELEGRAM_STATE_DIR else default).
"""
import sys, os, json, glob, urllib.request

MAX_FALLBACK_ATTEMPTS = 5

INSTRUCTION = (
    "KÖTELEZŐ: erre a Telegram-üzenetre még NEM küldtél választ a Telegram "
    "`reply` tool-lal (chat_id=%s). A CLI/transzkript szöveget a felhasználó a "
    "Telegramon NEM látja — onnan nézve csak befagytál. Küldd el a válaszodat "
    "MOST a `reply` tool-lal a megfelelő chat_id-vel. Ha tényleg nincs érdemi "
    "válasz, akkor is küldj egy rövid visszaigazolást."
)


def state_dir():
    d = os.environ.get("TELEGRAM_STATE_DIR")
    if d:
        return d
    return os.path.expanduser("~/.claude/channels/telegram")


def log(sd, msg):
    try:
        os.makedirs(os.path.join(sd, "progress"), exist_ok=True)
        with open(os.path.join(sd, "progress", "debug.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def token(sd):
    try:
        for line in open(os.path.join(sd, ".env"), encoding="utf-8"):
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


class TelegramApiError(Exception):
    """The Bot API answered, and the answer was a refusal."""


def api(tok, method, payload):
    # Base is overridable (TELEGRAM_API_BASE) so tests can point it at a local
    # stub; defaults to the real Bot API, so production is unchanged.
    #
    # FALLBACKVAK901: urlopen only raises on an HTTP-level error, and Telegram
    # refuses with HTTP 200 + {"ok":false} (chat not found, bot blocked, message
    # too long, chat not allowlisted). Returning that body as if it were a
    # success is the python spelling of the `curl exit 0` trap this project's
    # own CLAUDE.md names -- and here it silently ate the owner's final answer.
    # A refusal must raise, so the one caller that can retry gets to know.
    base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    url = f"{base}/bot{tok}/{method}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        body = json.loads(r.read().decode())
    if not isinstance(body, dict) or not body.get("ok"):
        desc = ""
        if isinstance(body, dict):
            desc = str(body.get("description") or body)
        raise TelegramApiError(f"{method} rejected: {desc}"[:300])
    return body


def last_assistant_text(transcript_path):
    """Return the last non-empty assistant text message from the JSONL transcript
    (the agent's final user-facing answer). Empty string if none / unreadable."""
    text = ""
    if not transcript_path:
        return text
    try:
        for line in open(transcript_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            msg = ev.get("message") or {}
            role = msg.get("role") or ev.get("role")
            if ev.get("type") == "assistant" or role == "assistant":
                content = msg.get("content", ev.get("content"))
                parts = []
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                elif isinstance(content, str):
                    parts.append(content)
                t = "\n".join(p for p in parts if p).strip()
                if t:
                    text = t  # keep the LAST non-empty one
    except Exception:
        pass
    return text


def main():
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:
        ev = {}
    sid = ev.get("session_id") or "default"
    transcript = ev.get("transcript_path")
    stop_active = bool(ev.get("stop_hook_active"))
    sd = state_dir()
    pdir = os.path.join(sd, "progress")
    guard = os.path.join(pdir, f"enforce-{sid}.marker")

    # Clean up this session's dedup markers (created by telegram_progress.py).
    for m in glob.glob(os.path.join(pdir, f"seen-{sid}-*.marker")):
        try:
            os.remove(m)
        except Exception:
            pass

    path = os.path.join(pdir, f"{sid}.json")
    try:
        pend = json.load(open(path))
    except Exception:
        pend = None

    if not pend:
        # Nothing pending: either not a Telegram turn, or the reply was already
        # sent (PostToolUse cleared it). Drop any stale enforce marker and exit.
        try:
            os.remove(guard)
        except Exception:
            pass
        return

    # A Telegram turn whose reply was NOT sent for the listed chat(s).
    blocked_before = os.path.exists(guard)
    if not stop_active and not blocked_before:
        # First Stop with an un-replied Telegram turn -> nudge the agent to reply
        # properly via its own tool. Keep the placeholder so the re-entry path
        # (PostToolUse) clears it when the reply finally goes out.
        try:
            open(guard, "w").close()
        except Exception:
            pass
        chats = ", ".join(sorted({str(p.get("chat_id")) for p in pend}))
        log(sd, f"[enforce] blocking stop, no reply sent sid={sid} chats={chats}")
        print(json.dumps({"decision": "block", "reason": INSTRUCTION % chats}))
        return

    # Already nudged once (or loop guard tripped) and STILL no reply -> guaranteed
    # fallback: deliver the agent's final answer to the chat, then clear.
    answer = last_assistant_text(transcript)
    tok = token(sd)
    sent = failed = 0
    if tok:
        for p in pend:
            cid, mid = p.get("chat_id"), p.get("message_id")
            delivered_here = True
            if answer:
                try:
                    api(tok, "sendMessage", {"chat_id": cid, "text": answer[:4000]})
                    sent += 1
                except Exception as e:
                    delivered_here = False
                    failed += 1
                    log(sd, f"[enforce] fallback send FAILED chat={cid}: {e}")
            # The placeholder is only noise once the real answer landed. If the
            # send failed, leave it up: it is the only thing the user can see.
            # Per chat, not per batch -- with two chats the first success must
            # not strip the second chat's placeholder.
            if delivered_here:
                try:
                    api(tok, "deleteMessage", {"chat_id": cid, "message_id": mid})
                except Exception as e:
                    log(sd, f"[stop] delete failed: {e}")
    elif answer:
        failed = len(pend)
        log(sd, "[enforce] fallback send FAILED: no bot token")

    # FALLBACKVAK901: the state files used to be removed unconditionally, so a
    # refused send destroyed the only record that anything was owed -- the
    # answer was gone for good and the log still said delivered. Keep the state
    # when delivery failed so the watchdog's orphan sweep retries it, but keep
    # it BOUNDED: a permanently refusing chat (bot blocked) must not wedge the
    # session forever. The guard file is kept alongside so the retry goes
    # straight back to this branch instead of nudging the agent again.
    attempts_path = os.path.join(pdir, f"fallback-attempts-{sid}")
    if failed:
        try:
            attempts = int(open(attempts_path).read().strip())
        except Exception:
            attempts = 0
        attempts += 1
        if attempts < MAX_FALLBACK_ATTEMPTS:
            try:
                with open(attempts_path, "w") as f:
                    f.write(str(attempts))
            except Exception:
                pass
            log(sd, f"[enforce] fallback-delivered={sent} FAILED={failed} "
                    f"state KEPT for retry (attempt {attempts}/"
                    f"{MAX_FALLBACK_ATTEMPTS}) sid={sid}")
            return
        log(sd, f"[enforce] fallback-delivered={sent} FAILED={failed} "
                f"GIVING UP after {attempts} attempts -- THE ANSWER IS LOST "
                f"sid={sid}")
    for f in (path, guard, attempts_path):
        try:
            os.remove(f)
        except Exception:
            pass
    log(sd, f"[enforce] fallback-delivered={sent} failed={failed} "
            f"cleared {len(pend)} placeholder(s) sid={sid}")


if __name__ == "__main__":
    main()
