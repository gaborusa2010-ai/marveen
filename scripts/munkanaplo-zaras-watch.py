#!/usr/bin/env python3
"""
Tell the owner, with no model turn, that a work-log entry is still open.

Why this exists at all
----------------------
The model-driven sentry (`munkanaplo-zaras-orzo`) already asks this question at
16:05 and 18:30. It failed on two consecutive evenings, and both failures were
measured on 2026-09-16:

  2026-09-14 16:05:13  the runner logged "Scheduled task fired", then at
                       16:30:12 "Stuck-input restart deferred -- parked input
                       still recoverable or possibly a human draft". The prompt
                       sat unprocessed; at 17:10:24 the machine shut down.
  2026-09-15 18:30:12  fired again, but from 17:44 until 04:45 the next morning
                       every model call was refused ("monthly spend limit"), so
                       no turn ever happened.

Both nights the scheduler log said "fired" and `schedule-last-run.json` recorded
a time. Neither records whether the WORK ran. Job #18 stayed open for 48.5 hours
and 2026-09-15 never made it into the ledger at all -- about 400 pounds that
Forgacs caught by hand on the morning of 09-16.

What this does NOT fix
----------------------
The Monday failure. The machine was off from 17:10, and nothing running on that
machine can speak while it is off. What `Persistent=true` on the timer buys is
the NEXT boot: on 09-15 the user manager came back at 17:44, and a catch-up run
would have seen a ledger entry open since Sunday morning. That is the half this
covers; a machine that stays off all evening still needs the owner.

It also does not replace the 16:05 round. That one goes to Forgacs first and
needs judgement (he may know the owner is still working). This is the net
underneath, for the hours when the skill itself says the question goes straight
to the owner.

Repeat protection
-----------------
State in store/munkanaplo-zaras-seen.json: which job id was last reported and
when. A second message about the same open job waits MIN_REPEAT_MIN minutes, so
a 15-minute timer does not turn one open log into a stream.

The state is committed ONLY after Telegram confirms the send. curl exits 0 even
when the API refuses, so the answer is parsed, not the exit code -- the same
trap the repo's inter-agent helper exists to avoid.

Usage
-----
  munkanaplo-zaras-watch.py            # what is open, as JSON, no send
  munkanaplo-zaras-watch.py --notify   # send it and remember
  munkanaplo-zaras-watch.py --min-hours 6

Exit 0 when there is nothing to say; that is the normal state, not a failure.
Exit 2 only when something actually broke.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "store", "muhely-jobs.json")
STATE = os.path.join(REPO, "store", "munkanaplo-zaras-seen.json")


def _tg_env():
    """Bot token file: install-scoped first (#915), legacy HOME as fallback.

    Measured 2026-09-16: ~/.claude/channels was empty while the real token sat
    in <install>/.claude/channels/telegram/.env, so this module could not send
    anything. The shell senders (scripts/limit-monitor.sh) already tried both.
    """
    local = os.path.join(REPO, ".claude", "channels", "telegram", ".env")
    if os.path.exists(local):
        return local
    return os.path.expanduser("~/.claude/channels/telegram/.env")


TG_ENV = _tg_env()
CHAT_ID = "8547011582"

# Below this the log is probably a normal working day still in progress. The
# owner's measured pattern is 07:00-16:00, so at 18:30 an open log is already
# past nine hours; 8 keeps one margin hour without waking him mid-afternoon.
DEFAULT_MIN_HOURS = 8.0

# One open job, one message per this many minutes.
MIN_REPEAT_MIN = 100


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, STATE)


def open_job():
    """The open ledger entry, or None. A missing ledger is not an error here."""
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"HIBA: a munkanaplo nem olvashato ({e})", file=sys.stderr)
        return "error"
    if not isinstance(rows, list):
        print("HIBA: a munkanaplo nem lista", file=sys.stderr)
        return "error"
    for r in rows:
        if isinstance(r, dict) and r.get("started_at") and not r.get("stopped_at"):
            return r
    return None


def message(job, hours):
    started = datetime.datetime.fromtimestamp(job["started_at"])
    return (
        f"A muhely-naplodban a #{job['id']} munka meg fut, "
        f"{started.strftime('%m-%d %H:%M')} ota, {hours:.1f} ora.\n"
        f"{job.get('client') or '?'} / {(job.get('job') or '')[:70]}\n\n"
        "Ha az appban mar lezartad, az ide nem jut el: akkor csak a befejezes "
        "idejet ird meg, es atvezetem. Ha meg dolgozol, hagyjuk nyitva.\n\n"
        "Ezt a sort egy timer kuldi, modell nelkul. Az idopontot nem stampelem "
        "elore, mert az penzugyi adat."
    )


def send(text):
    """True only when Telegram itself says ok.

    EZ A HIVAS NEM HAGY NYOMOT A `conversation_log`-BAN, es ezt tudni kell, mielott
    valaki a kezbesitest onnan probalja visszamerni. Kozvetlenul a Telegram Bot
    API-nak megy, tehat megkeruli a channels-plugin retegét, ami a conversation_log-ot
    irja. Merve 2026-09-20: az itt kikuldott kerdes zaro mondata
    ("Ha azon a napon nem dolgozott...") a tabla 1954 soraban, 2026-08-21 ota
    NULLASZOR fordul elo -- nem csak egy napon, hanem EGYSZER SEM.

    Amiert ez elsult: Forgacs 2026-09-19-re azt merte, hogy a hianyzo-munkanap jelzes
    "nem ert celba", mert a conversation_log-ban nincs rola sor. A meres pontos volt,
    csak nem arra a kerdesre valaszolt: ezen a csatornan SOHA nincs sor. Amit a
    kezbesitesrol tudni lehet, az a `store/munkanaplo-zaras-seen.json`: a `save_state`
    CSAK sikeres `send()` utan fut, es a `send()` csak akkor ad True-t, ha a Telegram
    `ok: true`-t valaszolt. Tehat egy beirt `hiany_date` azt jelenti, hogy a Telegram
    ATVETTE az uzenetet a 8547011582-es chatre.

    Amit EZ SEM bizonyit: hogy a gazda elolvasta. Arra a Bot API nem ad meroeszkozt.
    """
    try:
        env = open(TG_ENV, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"HIBA: a Telegram token nem olvashato ({e})", file=sys.stderr)
        return False
    m = re.search(r"[0-9]+:[A-Za-z0-9_-]+", env)
    if not m:
        print("HIBA: nincs bot token a channels .env-ben", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "15",
             f"https://api.telegram.org/bot{m.group()}/sendMessage",
             "--data-urlencode", f"chat_id={CHAT_ID}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "disable_web_page_preview=true"],
            capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"HIBA: a curl nem futott: {e}", file=sys.stderr)
        return False
    try:
        ok = json.loads(r.stdout or "{}").get("ok") is True
    except ValueError:
        ok = False
    if not ok:
        print(f"HIBA: a Telegram elutasitotta: {(r.stdout or '')[:200]}", file=sys.stderr)
    return ok


MISSING = os.path.join(REPO, "scripts", "muhely", "naplo-hiany.py")


def missing_days():
    """Forgacs' deterministic missing-day check, or None when it has nothing.

    Two different questions, and only one of them is visible: `jobs.py status`
    prints an OPEN record, but a day with NO record at all shows up on no
    screen. That is how 2026-09-15 went missing while both sentries watched the
    open-record question and stayed correctly silent.

    Kept as a subprocess call rather than reimplemented: one owner, one set of
    tests (scripts/muhely/__tests__/naplo_hiany_test.sh). A failure here must
    never take down the open-log warning, so every error path returns None.
    """
    if not os.path.exists(MISSING):
        return None
    # Hand it the SAME ledger this module is reading. In production that is the
    # one file either way; in a test it is what keeps the child process off the
    # live data -- a child that reads its own default would quietly answer a
    # different question than the parent.
    env = dict(os.environ)
    env["MUHELY_JOBS"] = LEDGER
    # MUHELY_NAPLO_HIANY_SEEN is deliberately NOT set: the acknowledgement file
    # is shared with Forgacs, so a day he closes stays closed here too. A test
    # that hands over a scratch ledger must hand over the seen file as well,
    # or its probe would write into the live acknowledgements.
    try:
        r = subprocess.run([sys.executable, MISSING, "--ember"],
                           capture_output=True, text=True, timeout=30, cwd=REPO, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    # No --napok either: the window is the tool's own default (14 days as of
    # 2026-09-16, two invoicing weeks). Pinning it here would freeze this caller
    # at today's value the moment Forgacs widens it -- the same mistake in a
    # different shape as filtering on the output text.
    # --ember is a CONTRACT, not a text filter. The first version of this
    # stripped the tool's trailing shell-command line by matching its wording,
    # which would have gone silently permissive the moment Forgacs reworded it.
    # A flag keeps holding; a string match only looks like it does.
    if r.returncode != 1 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                    help="send it through the Bot API instead of only printing")
    ap.add_argument("--min-hours", type=float, default=DEFAULT_MIN_HOURS)
    ap.add_argument("--repeat-min", type=int, default=MIN_REPEAT_MIN)
    ap.add_argument("--no-hiany", action="store_true",
                    help="skip the missing-day question")
    a = ap.parse_args(argv)

    now = datetime.datetime.now()
    st = load_state()
    parts = []
    new_state = dict(st)
    rc = 0

    # (1) open record, the question both old sentries already asked
    job = open_job()
    if job == "error":
        rc = 2
    elif job is not None:
        hours = (now.timestamp() - job["started_at"]) / 3600.0
        fresh = (st.get("job_id") == job["id"]
                 and (now.timestamp() - st.get("sent_at", 0)) < a.repeat_min * 60)
        if hours >= a.min_hours and not fresh:
            parts.append(message(job, hours))
            new_state["job_id"] = job["id"]
            new_state["sent_at"] = now.timestamp()
            new_state["sent_at_local"] = now.strftime("%Y-%m-%d %H:%M:%S")

    # (2) missing day -- once a day at most, because it is a standing question
    # and not an event: the same day would otherwise come up every evening.
    #
    # A CSILLAPITO A KERDES TARTALMAT IS NEZI, NEM CSAK A NAPOT (2026-09-20).
    # Forgacs vette eszre: a `hiany_date` a FUTAS napjat tarolja, nem azt, MELYIK
    # napra szolt a kerdes. Igy ha ugyanazon a napon egy MASIK nap is hianyzani
    # kezd (pl. a gazda dolgozott ma, es este meg nincs rola sor), a csillapito
    # ugyanannak a kerdesnek latja, es a masodik, UJ kerdes nem megy ki -- pedig
    # az mar nem ismetles. A datum onmagaban nem tudja megkulonboztetni oket.
    #
    # A megoldas nem datum-parszolas a szovegbol: az a `--ember` kimenet alakjara
    # kotne minket, es a fenti kommentek epp ez ellen szolnak. Helyette a kikuldott
    # kerdes sha256-ja all a csillapitoban. Azonos kerdes ugyanazon a napon -> csend;
    # MAS kerdes ugyanazon a napon -> megy, mert az mas kerdes.
    if not a.no_hiany:
        hiany = missing_days() if (st.get("hiany_date") != now.strftime("%Y-%m-%d")
                                   or st.get("hiany_sha")) else None
        if hiany:
            sha = hashlib.sha256(hiany.encode("utf-8")).hexdigest()
            mar_kerdeztuk = (st.get("hiany_date") == now.strftime("%Y-%m-%d")
                             and st.get("hiany_sha") == sha)
            if not mar_kerdeztuk:
                parts.append(hiany + "\n\nHa azon a napon nem dolgozott, csak ird meg, "
                             "es lezarom, tobbe nem kerdezem. Ha dolgozott, mondd meg a "
                             "kezdest es a vegzest, es potolom a naploban.")
                new_state["hiany_date"] = now.strftime("%Y-%m-%d")
                new_state["hiany_sha"] = sha

    if not parts:
        return rc

    text = "\n\n".join(parts)
    if not a.notify:
        print(json.dumps({"text": text, "reszek": len(parts)}, ensure_ascii=False))
        return rc

    if not send(text):
        # Nothing committed on failure: both questions are still open.
        return 2
    save_state(new_state)
    return rc


if __name__ == "__main__":
    sys.exit(main())
