#!/usr/bin/env python3
"""
Job log: where you worked, how long, with which photos, and what to invoice.

Built Telegram-first because the carpenter has a phone in the workshop and no
laptop, but the record shape is the one a standalone app would want, so the app
can later sit on the same data instead of a rewrite.

One job accumulates: a start time, a location, photos, notes, and a stop time.
Closing it produces the hours, which feed straight into invoice.py.

Usage
-----
  jobs.py start  --client "Mrs Patel" --job "Fit chest of drawers" [--address "..."]
                 [--lat 51.37 --lon -0.09] [--rate 240] [--cis] [--at 08:00]
  jobs.py photo  <file> [--note "..."]      # EXIF gives the time; the location
                 [--hely]                  # is only taken over when asked for
  jobs.py note   "amit mondani akarsz"
  jobs.py edit   <id> --job "..." [--client "..."]   # javitas a forrasnal
  jobs.py stop   [--note "..."] [--at 17:30]
  jobs.py status                            # what is open right now
  jobs.py list   [--client X] [--open]
  jobs.py show   <id>
  jobs.py invoice <id> [--out job.json]     # feeds scripts/muhely/invoice.py

Times are stored as UTC epoch and shown in local time. A day that runs past
midnight is still one job, because the clock does not end the work.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
LEDGER = os.path.join(STORE, "muhely-jobs.json")
PHOTOS = os.path.join(STORE, "job-photos")
BUSINESS = os.path.join(STORE, "muhely-business.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load():
    if not os.path.exists(LEDGER):
        return []
    try:
        with open(LEDGER) as fh:
            return json.load(fh)
    except ValueError:
        return []


def _save(rows):
    os.makedirs(STORE, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, LEDGER)          # atomic: a crash must not truncate the log


def _open_job(rows):
    for r in reversed(rows):
        if r.get("stopped_at") is None:
            return r
    return None


def parse_when(text, base=None):
    """Accept what a person actually types: "8", "08:00", "2026-08-22 08:00".

    A bare hour means today. The carpenter tells me at 06:46 that he starts at
    8, so the log has to take a time that has not happened yet, and after a long
    day he will report a finish time that is already past. Both are normal.
    """
    if text is None:
        return time.time()
    text = str(text).strip()
    now = datetime.fromtimestamp(base or time.time()).astimezone()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).astimezone().timestamp()
        except ValueError:
            pass
    for fmt in ("%H:%M", "%H.%M", "%H"):
        try:
            t = datetime.strptime(text, fmt)
            return now.replace(hour=t.hour, minute=t.minute, second=0,
                               microsecond=0).timestamp()
        except ValueError:
            pass
    raise ValueError(f"Nem ertem ezt az idopontot: {text!r}. "
                     f"Probald igy: 8, 08:00, vagy 2026-08-22 08:00")


def _local(ts):
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")


# The owner's overtime rule, in his words (Telegram, 2026-09-09 12:45, relayed by
# Cody with the card 4596adab): "akkor 7-tol 4-ig napiber, utana minden extra ora
# az orabérben (200 font / 8 ora = 25 font/ora)". Asked whether 16:00 is a clock
# time or a worked-hours count, he answered "7-tol 4-ig fix..." (13:02).
#
# The divisor is EIGHT, and it is his number, not ours: a nine-hour day is eight
# PAID hours (two quarter breaks and half an hour for lunch). Deriving the hourly
# figure from the day rate over nine hours would underpay every overtime hour by
# about eleven per cent.
#
# THE DAY RATE BUYS NINE HOURS OF PRESENCE, AND OVERTIME IS WHAT RUNS PAST THEM.
# Not a clock time. The owner said it twice in four minutes, in the group chat on
# 2026-09-10, after we had put the wrong reading in front of him:
#
#   08:03  "Hanyszor mondjam meg a napidij az 200 font..."
#   08:07  "7-tol 4-ig az 9 ora , 8-tol 5-ig az is 9 ora = napidij 200 font..."
#
# So a 08:00-17:00 installation day is an ordinary day at 200, exactly like the
# workshop's 07:00-16:00, and only the ELEVENTH hour of a 07:00-18:00 day is paid
# extra -- which is the 250 he gave as an example on 2026-09-09.
#
# THIS FUNCTION HELD THE WRONG RULE FOR ABOUT THIRTY MINUTES ON 2026-09-10, and the
# way it got there is worth more than the fix. Forgacs asked him whether the day
# rate on an 08:00 day "runs out at four or at five" (Telegram 489). He answered
# "Hivatalosan 4-kor" (497). That is a true answer about the official end of a
# shift, and it says nothing about a day that began an hour later -- but it was
# read as "the boundary is a clock time, whatever hour the day began", and both
# this file and a fleet decision were changed on it. The question offered two
# clock times and no third option, so the answer could only be a clock time.
#
# The reading that turned out to be right was the one already built into the
# worklog-app, and the one this file used to refuse to guess at.
#
# The divisor stays EIGHT, and it is his number: nine hours of presence is eight
# PAID hours (two quarter breaks and half an hour for lunch), and 200/8 = 25.
#
# ONE REFUSAL REMAINS: a record spanning several days. The rule prices ONE day of
# presence, and a multi-day record does not say how the hours fall across them.
PAID_PRESENCE_HOURS = 9


def _overtime(r):
    """Overtime hours past a nine-hour day, or a reason not to compute.

    Returns (hours, note). `hours` is None when the figure must not be put on an
    invoice without a person deciding; `note` then says why, and is printed.
    """
    started, stopped = r.get("started_at"), r.get("stopped_at")
    if not started or not stopped:
        return None, "meg fut, nincs zarasi ido"
    days = max(1, r.get("days") or 1)
    if days > 1:
        return None, (f"{days} napos rekord: a szabaly EGY nap kilenc orajat arazza, es "
                      f"nem mondja meg, hogyan oszlanak el az orak. Szamold ki kezzel, "
                      f"es add meg a sort.")
    presence = (stopped - started) / 3600.0
    # A short day is still a whole day: the owner does not invoice part days, and
    # the overtime on one is zero rather than negative.
    return max(0.0, presence - PAID_PRESENCE_HOURS), ""


def _derived_rule_note(r):
    """Say when the overtime figure for this day is a DERIVATION, not his word.

    The owner priced ONE shape of day in his own words: a nine-hour day of
    presence, 200, and 25/hour past it (2026-09-09, 2026-09-10). Two shapes were
    never put to him, and the numbers this file prints for them are read off the
    nine-hour rule rather than quoted from him:

      - a shift that runs past midnight (a "night shift");
      - a day that starts before 07:00.

    THIS DOES NOT REFUSE, AND THAT IS DELIBERATE. `_overtime` already refuses the
    one case where the rule genuinely cannot be applied (a multi-day record).
    Here the arithmetic is sound and the figure is usable; what is missing is his
    confirmation. Refusing would stop an invoice over a question nobody has been
    asked, which is the more expensive of the two mistakes.

    IT ALSO DOES NOT ASK. The wording states the provenance of the number and
    stops there. An alert that asks a question turns into an item on a list, and
    the two questions this replaces sat on the weekend list for three weeks under
    the label "not urgent" -- while the true label was that there was NO CASE:
    measured 2026-09-12 across all 17 jobs in the ledger, zero days start before
    07:00 and zero cross midnight. So this fires for the first time on the day it
    first matters, and never before, which is the point of putting it here rather
    than on a list.
    """
    started, stopped = r.get("started_at"), r.get("stopped_at")
    if not started or not stopped:
        return ""
    s_dt, e_dt = datetime.fromtimestamp(started), datetime.fromtimestamp(stopped)
    if s_dt.date() != e_dt.date():
        return ("EJSZAKAI MUSZAK: a nap atnyulik a kovetkezore "
                f"({s_dt:%m-%d %H:%M} -> {e_dt:%m-%d %H:%M}). A tulora-szam erre "
                "LEVEZETES a kilenc oras nap-hossz szabalybol, NEM a gazda szava: "
                "ilyen napra o meg nem nyilatkozott. Hasznalhato, de ha szamlara "
                "megy, elotte kerdezd meg tole.")
    if s_dt.hour < 7:
        return (f"KORAI KEZDES: a nap {s_dt:%H:%M}-kor indult, 07:00 elott. A "
                "tulora-szam erre LEVEZETES a kilenc oras nap-hossz szabalybol, "
                "NEM a gazda szava: o a nap VEGEROL nyilatkozott, a korai "
                "kezdesrol nem. Hasznalhato, de ha szamlara megy, elotte kerdezd "
                "meg tole.")
    return ""


def _hours(r):
    """Elapsed hours, never negative.

    A job can be booked before it starts ("kezdes 8-tol" typed at 06:46), and
    until 8 o'clock the honest answer is zero worked hours, not a negative
    number that looks like a broken clock.
    """
    end = r.get("stopped_at") or time.time()
    return max(0.0, round((end - r["started_at"]) / 3600.0, 2))


def default_day_rate():
    """The day rate from the ONE place that decides it, or None.

    Added 2026-09-07. Until today a job started without --rate carried no rate at
    all, and the number reached the invoice through a person typing it: measured
    that day, FIVE of twelve jobs had rate=None and four of those were already
    invoiced. `cmd_invoice` builds a line only `if r.get("rate")`, and there is no
    fallback on that path -- so the day rate was not "taken from the file", it was
    entered by hand every time, which is a place to mistype.

    This is a lookup, not a default in the risky sense (Cody, 2026-09-07: a default
    must not invent a decision that was never made). The day rate is ONE settled
    number with its own authoritative home, and the file records the decision next
    to it: "2026-08-22, a gazda megerositette". Reading it is a citation. What the
    caller must still see is WHERE it came from, so cmd_start prints that.

    Silent on failure by design: a missing or malformed business file must not stop
    a job from being started at seven in the morning. The caller warns instead.
    """
    try:
        with open(BUSINESS) as fh:
            v = json.load(fh).get("day_rate")
        return float(v) if v else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def cmd_start(a):
    rows = _load()
    live = _open_job(rows)
    if live:
        print(f"MAR FUT egy munka: #{live['id']} {live['client']} / {live['job']}, "
              f"{_hours(live)} ora eddig.\n"
              f"Elobb zard le: jobs.py stop")
        return 1
    try:
        started = parse_when(a.at)
    except ValueError as e:
        print(e)
        return 2
    jid = max([r["id"] for r in rows], default=0) + 1
    # A fixed price is NOT a day rate: in that mode --rate is the agreed TOTAL for
    # the job. Filling it from day_rate would put 200 on a job agreed at 1200, and
    # the invoice line would even read "agreed fixed price". So the lookup runs for
    # daily and hourly only; fixed still has to be told the figure.
    rate, rate_from_file = a.rate, False
    if rate is None and a.billing in ("daily", "hourly"):
        rate = default_day_rate()
        rate_from_file = rate is not None
    row = {
        "id": jid,
        "client": a.client,
        "job": a.job,
        "address": a.address or "",
        "lat": a.lat, "lon": a.lon,
        "started_at": started,
        "stopped_at": None,
        "rate": rate,
        "cis": bool(a.cis),
        "billing": a.billing,
        "days": a.days,
        "photos": [],
        "notes": [],
        # TWO fields, because one held two meanings and every reader got it wrong.
        # `invoice_ref` is the issued invoice number and nothing else; `no_charge`
        # is a reason this job is not billable. The old `invoiced` field carried
        # either, so `if r.get("invoiced")` read "not to be invoiced, a favour" as
        # PROOF OF INVOICING -- and the status list printed that job as szamlazva.
        "invoice_ref": None,
        "no_charge": None,
    }
    rows.append(row)
    _save(rows)
    print(f"#{jid} INDUL  {a.client} / {a.job}")
    ahead = row["started_at"] - time.time()
    when = _local(row["started_at"])
    if ahead > 60:
        print(f"Kezdes: {when}  (meg {int(ahead // 60)} perc mulva, elore rogzitve)")
    else:
        print(f"Kezdes: {when}")
    if a.address:
        print(f"Hely:   {a.address}")
    if not a.address and a.lat is None:
        print("Hely nincs megadva. Kuldj egy fotot FAJLKENT, abbol kiolvasom.")
    if rate_from_file:
        print(f"Napidij: {rate:.0f} (a muhely-business.json day_rate mezojebol)")
    elif rate is None:
        print("FIGYELEM: nincs napidij ezen a munkan. A szamla sorai igy URESEK "
              "lesznek. Add meg: --rate <osszeg>")
    return 0


def cmd_photo(a):
    rows = _load()
    live = _open_job(rows)
    if not live:
        print("Nincs nyitott munka. Elobb: jobs.py start ...")
        return 1
    if not os.path.exists(a.file):
        print(f"Nincs ilyen fajl: {a.file}")
        return 1

    os.makedirs(PHOTOS, exist_ok=True)
    ext = os.path.splitext(a.file)[1] or ".jpg"
    dest = os.path.join(PHOTOS, f"job{live['id']}-{int(time.time())}{ext}")
    with open(a.file, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())        # keep our own copy: the inbox gets cleaned

    entry = {"path": dest, "note": a.note or "", "added_at": time.time()}
    try:
        from exif import read as exif_read
        ex = exif_read(a.file)
    except Exception as e:
        ex = {"available": False, "reason": str(e)}
    # EXIF says where the PICTURE was taken, which is not the same question as
    # where the JOB is. On a workshop day the photo is taken in the workshop and
    # the job's address is the customer's, 100+ km away. Until 2026-09-07 this
    # block answered the second question with the first: it silently wrote the
    # photo's coordinates into the JOB whenever the job had no lat yet, and an
    # already ADDRESSED job was not protected either -- `status` then printed a
    # London address and a Hampshire map link one under the other, with nothing
    # saying which one to believe. A wrong answer, not an error.
    #
    # So the coordinate now stops at the photo entry, and taking it over is a
    # separate, deliberate act (--hely). This is the app's existing shape:
    # joblog.js:141-142 puts the photo's location in the photo's own metadata,
    # and only setLocation (the GPS button) touches the job's. The offer below
    # keeps the convenience -- nothing is hidden, it is just no longer automatic.
    if ex.get("available"):
        entry["taken_at"] = ex.get("datetime")
        if "lat" in ex:
            entry["lat"], entry["lon"] = ex["lat"], ex["lon"]
            if getattr(a, "hely", False):
                had = live.get("lat")
                live["lat"], live["lon"] = ex["lat"], ex["lon"]
                if had is not None:
                    print(f"A munka helye FELULIRVA a fotobol: {had} -> {ex['lat']}, "
                          f"{ex['lon']} (--hely kapcsoloval kerted)")
                else:
                    print(f"A munka helye a fotobol: {ex['lat']}, {ex['lon']}")
                print(f"Terkep: {ex['maps']}")
            else:
                print(f"A fotoban GPS van: {ex['lat']}, {ex['lon']}  ({ex['maps']})")
                print("A munka helyet NEM allitottam at. Ha ez a munka helye is, "
                      "futtasd ujra a --hely kapcsoloval.")
    live["photos"].append(entry)
    _save(rows)

    print(f"#{live['id']} fotó elmentve ({len(live['photos'])}. darab)")
    if not ex.get("available"):
        print(f"Metaadat nincs: {ex.get('reason','')}")
    elif "lat" not in ex:
        print(ex.get("gps_note", ""))
    return 0


def _invoices_for(job_id):
    """Ask invoice.py's register. Never let its absence break the job log: the
    log has to keep working on a machine where no invoice was ever rendered."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import invoice
        return invoice.issued_for(job_id)
    except Exception:
        return []


def _state(r):
    """One place decides what a job's state is, so the list and the guards agree."""
    if r.get("stopped_at") is None:
        return "fut"
    if r.get("invoice_ref"):
        return f"szamlazva {r['invoice_ref']}"
    if r.get("no_charge"):
        return "nem szamlazando"
    return "kesz"


def cmd_note(a):
    """Note the open job, or a named one with --id.

    A closed job still needs notes: the gap between stopping and invoicing is
    exactly when the corrections arrive ("the customer confirmed X", "the morning
    was actually Y"), and those notes go onto the invoice. Refusing them because
    the clock has stopped pushed that information into thin air.
    """
    rows = _load()
    if a.id:
        live = next((x for x in rows if x["id"] == a.id), None)
        if not live:
            print(f"Nincs #{a.id} munka.")
            return 1
    else:
        live = _open_job(rows)
        if not live:
            print("Nincs nyitott munka. Lezart munkahoz add meg: note --id <n>")
            return 1
    live.setdefault("notes", []).append({"at": time.time(), "text": a.text})
    _save(rows)
    print(f"#{live['id']} jegyzet hozzaadva.")
    return 0


def cmd_stop(a):
    rows = _load()
    live = _open_job(rows)
    if not live:
        print("Nincs nyitott munka.")
        return 1
    try:
        live["stopped_at"] = parse_when(a.at)
    except ValueError as e:
        print(e)
        return 2
    if live["stopped_at"] < live["started_at"]:
        print(f"A befejezes ({_local(live['stopped_at'])}) korabbi mint a kezdes "
              f"({_local(live['started_at'])}). Ha atnyult ejfelen, add meg a datumot is.")
        live["stopped_at"] = None
        return 2
    if a.note:
        live["notes"].append({"at": time.time(), "text": a.note})
    _save(rows)
    h = _hours(live)
    print(f"#{live['id']} KESZ  {live['client']} / {live['job']}")
    print(f"{_local(live['started_at'])} -> {_local(live['stopped_at'])}   {h} ora")
    print(f"Fotok: {len(live['photos'])}   Jegyzetek: {len(live['notes'])}")
    if live.get("rate"):
        # The mode matters HERE too, not only in cmd_invoice. Measured 2026-08-25:
        # a 9-hour day-rate job printed "kb. 225.0" (9/8 x 200) while the invoice
        # it feeds would correctly bill one day at 200. A wrong number on the
        # closing line is the one the owner reads in the workshop, so it has to
        # agree with the invoice, not with an hourly formula that does not apply.
        if live.get("billing") == "fixed":
            # Same reason as in cmd_invoice: the closing line is what the owner
            # reads in the workshop, so it must not look like a day rate.
            print(f"Fix ar: {live['rate']} (megallapodott osszeg)"
                  f"   ({h} ora a helyszinen, az osszeget nem befolyasolja)")
        elif live.get("billing", "daily") == "daily":
            days = max(1, live.get("days") or 1)
            print(f"Napidij {live['rate']} x {days} nap = {round(days * float(live['rate']), 2)}"
                  f"   ({h} ora a helyszinen, de a napidij nem oraaranyos)")
        else:
            # The mode is called `hourly`, but the rate it multiplies is a DAY
            # rate divided by eight. Saying "oradij" here invites the reader to
            # check the number against an hourly figure, and it will look wrong
            # by a factor of eight. Measured 2026-09-02 (Margo found it through a
            # screen label, Cody confirmed it in the code): with `--rate 25` -- the
            # hourly equivalent of a 200 day -- a nine-hour day invoices at 28.20
            # instead of 225, and NOTHING flags it, because the screen and the
            # invoice agree with each other. They just both disagree with what the
            # person typing 25 meant. So the label states the unit, every time.
            print(f"Oraaranyos: {h} ora, {live['rate']} napi tetelbol "
                  f"({h}/8 nap) = kb. {round(h / 8 * live['rate'], 2)}")
    note = _derived_rule_note(live)
    if note:
        print(f"\n{note}")
    print(f"\nSzamlahoz:  python3 scripts/muhely/jobs.py invoice {live['id']}")
    return 0


def cmd_status(a):
    rows = _load()
    live = _open_job(rows)
    if not live:
        print("Nincs nyitott munka.")
        return 0
    pending = live["started_at"] - time.time()
    state = "INDUL MAJD" if pending > 60 else "FUT"
    print(f"#{live['id']} {state}   {live['client']} / {live['job']}")
    if pending > 60:
        print(f"Kezdes: {_local(live['started_at'])}   "
              f"({int(pending // 60)} perc mulva)")
    else:
        print(f"Kezdes: {_local(live['started_at'])}   eltelt: {_hours(live)} ora")
    if live.get("address"):
        print(f"Hely:   {live['address']}")
    if live.get("lat") is not None:
        print(f"GPS:    https://maps.google.com/?q={live['lat']},{live['lon']}")
    print(f"Fotok:  {len(live['photos'])}   Jegyzetek: {len(live['notes'])}")
    return 0


def cmd_list(a):
    rows = _load()
    if a.client:
        rows = [r for r in rows if a.client.lower() in r["client"].lower()]
    if a.open:
        rows = [r for r in rows if r.get("stopped_at") is None]
    if not rows:
        print("Nincs talalat.")
        return 0
    print(f"{'ID':>4} {'ALLAPOT':<8} {'ORA':>6}  {'KEZDES':<17} UGYFEL / MUNKA")
    print("-" * 84)
    for r in rows:
        st = _state(r)
        print(f"{r['id']:>4} {st:<8} {_hours(r):>6}  {_local(r['started_at']):<17} "
              f"{r['client']} / {r['job'][:34]}")
    return 0


def cmd_show(a):
    rows = _load()
    r = next((x for x in rows if x["id"] == a.id), None)
    if not r:
        print(f"Nincs #{a.id} munka.")
        return 1
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0


def cmd_edit(a):
    """Correct a job's client or description AT THE SOURCE, keeping the history.

    A note is not enough. `cmd_invoice` copies `job` into the invoice unchanged
    and puts the notes in a separate field, so a description corrected only by a
    note still reaches the customer wrong unless whoever builds the PDF reads the
    notes and compares. That is a protection that works if somebody notices, and
    those are the ones that fail.

    So the field is rewritten, and the old value is appended as a note with the
    time. The record improves where it is read from, and nothing is lost.

    An INVOICED job is refused: the description on it has already gone out on a
    document, and quietly editing the record to disagree with the paper the
    customer holds is worse than a wrong description. Correct that with a note
    and a credit note, not by rewriting history.
    """
    rows = _load()
    r = next((x for x in rows if x["id"] == a.id), None)
    if not r:
        print(f"Nincs #{a.id} munka.")
        return 1
    # FILLING IN A MISSING DAY RATE IS NOT REWRITING HISTORY, and the invoiced
    # guard below must not swallow it. Measured 2026-09-10: four invoiced jobs
    # (#3, #4, #5 on 10012 and #11 on 10014) carry no `rate`, while the PDFs the
    # customer holds show 200.00 on every line. So the record does not match the
    # paper, and filling the field is what makes them agree.
    #
    # It mattered because the tools formed a CLOSED LOOP. `cmd_invoice` on such a
    # job prints "nincs munkadij ... add meg a dijat: jobs.py edit N --rate X",
    # and that command then refused itself on the invoiced guard -- with --force
    # too. The advice the tool gives has to be a command the tool accepts.
    #
    # Deliberately narrow, because the guard it steps around is a good one:
    # only --rate, only when the stored value is missing (never overwriting a
    # figure that was invoiced), only with --force, and nothing else may change
    # in the same call. A rate that is already set on an invoiced job is still
    # refused, because that WOULD contradict the paper.
    _rate_fill = (
        a.rate is not None
        and r.get("rate") in (None, 0, 0.0)
        and a.force
        and not any([a.job, a.client, a.address, a.stop])
    )
    if r.get("invoice_ref") and _rate_fill:
        r["rate"] = float(a.rate)
        r.setdefault("notes", []).append({"at": time.time(), "text":
            f"NAPIDIJ POTLAS: a rekordbol hianyzott a napidij, {float(a.rate):g} font "
            f"vezetve be. A munka mar ki van szamlazva ({r['invoice_ref']}), es a "
            f"szamla NEM valtozik: a potlas azt szunteti meg, hogy a naplo kevesebbet "
            f"mondjon, mint a vevo kezeben levo papir."})
        _save(rows)
        print(f"#{a.id} napidij potolva: {float(a.rate):g}. A {r['invoice_ref']} szamu "
              f"szamla valtozatlan, csak a rekord lett teljes.")
        return 0
    if r.get("invoice_ref"):
        print(f"#{a.id} MAR SZAMLAZVA ({r['invoice_ref']}). A leiras kiment egy "
              f"dokumentumon a vevonek, ezert nem irom at: a rekord es a papir nem "
              f"terhet el egymastol. Jegyzet es helyesbito szamla a jo ut.")
        return 1
    # invoice.py now records every reference it spends against the job it came
    # from, so this can look instead of guess. Two things it must NOT get wrong:
    #
    # Filter on kind. The register holds quotes as well, and a quote going out
    # does not lock anything: the customer has a price, not a document that says
    # what the work WAS. Reading the register without the filter would be the
    # two-meanings-in-one-field mistake again, one level up.
    #
    # And an empty register is not proof. It only knows about invoices made
    # through invoice.py from a job file carrying _job_id; a hand-written one
    # leaves no trace. So "nothing found" stays a question, not a green light --
    # it is just a much better informed question than it was an hour ago.
    issued = [e for e in _invoices_for(r["id"]) if e.get("kind") == "invoice"]
    if issued:
        ref = issued[-1].get("reference")
        r["invoice_ref"] = ref          # heal the record while we are here
        _save(rows)
        print(f"#{a.id} MAR SZAMLAZVA ({ref}), a szamla-nyilvantartas szerint. "
              f"A leiras kiment a vevonek, ezert nem irom at. A rekordba is "
              f"bevezettem a hivatkozast.")
        return 1
    if r.get("stopped_at") is not None and not a.force:
        print(f"#{a.id} LEZART munka. A szamla-nyilvantartasban NINCS rola kiadott "
              f"szamla, de az csak a jobs.py-bol keszult szamlakat ismeri: egy "
              f"kezzel irt szamla-JSON nem hagy nyomot benne. Ha biztos vagy benne "
              f"hogy meg nem szamlaztad, ismeteld meg --force kapcsoloval.")
        return 1
    changed = []
    if a.stop:
        # The finish time is the one field where a correction has to be visible:
        # it is what the day rate and the invoice are built on. Same rules as the
        # rest -- rewrite, and keep the old value in a note.
        new_stop = parse_when(a.stop)
        if new_stop != r.get("stopped_at"):
            # Human time, not epoch: these notes are copied onto the invoice.
            # _local is the formatter the rest of the file already uses.
            old_s = _local(r["stopped_at"]) if r.get("stopped_at") else "nyitva"
            changed.append(f"befejezes: {old_s} -> {_local(new_stop)}")
            r["stopped_at"] = new_stop
    if getattr(a, "rate", None) is not None:
        # The day rate belongs here for the same reason the finish time does: it is
        # what the invoice is built on, and a job that never got one produces an
        # invoice with no labour line at all. Added 2026-09-09, after job #12
        # (2026-09-07) turned up with rate=None -- started just before `start`
        # learned to read the figure from muhely-business.json. The error message
        # on that path names this flag, so the flag has to exist: it was written
        # first and did not, which is the whole reason this block is here.
        old_r = r.get("rate")
        if float(a.rate) != (float(old_r) if old_r is not None else None):
            changed.append(f"napidij: {old_r if old_r is not None else 'nincs'} -> {float(a.rate)}")
            r["rate"] = float(a.rate)
    for field, new in (("client", a.client), ("job", a.job), ("address", a.address)):
        if new is None or new == r.get(field):
            continue
        old = r.get(field, "")
        r[field] = new
        changed.append(f"{field}: \"{old}\" -> \"{new}\"")
    if not changed:
        print("Nincs mit valtoztatni.")
        return 0
    r.setdefault("notes", []).append({
        "at": time.time(),
        "text": "JAVITAS a rekordban: " + "; ".join(changed)
                + ". A regi ertek itt marad, hogy a tortenet ne vesszen el."
                + (" LEZART munkan, --force kapcsoloval, tehat valaki ALLITOTTA "
                   "hogy meg nincs szamlazva, nem a rendszer tudta."
                   if r.get("stopped_at") is not None else "")})
    _save(rows)
    print(f"#{a.id} javitva:")
    for c in changed:
        print(f"  {c}")
    print("  a regi ertek jegyzetkent rogzitve")
    return 0


def cmd_invoice(a):
    rows = _load()
    r = next((x for x in rows if x["id"] == a.id), None)
    if not r:
        print(f"Nincs #{a.id} munka.")
        return 1
    if r.get("stopped_at") is None:
        print(f"#{a.id} meg fut. Elobb: jobs.py stop")
        return 1
    # A JOB MARKED no_charge IS NOT A JOB WITH A MISSING RATE, and until 2026-09-10
    # this could not tell them apart. Measured that day on the one real case, job #1
    # (2026-08-22, the favour the owner decided not to bill): the rate guard below
    # fired with "nincs munkadij ... add meg a dijat: jobs.py edit 1 --rate <osszeg>"
    # and exit 3 -- advice that contradicts the record it just read. The decision not
    # to charge is IN the file; asking for a figure is asking to undo it.
    #
    # It is the same shape as the closed loop fixed earlier the same day, one field
    # over: a tool that answers from one field while another field already settled
    # the question. So the check goes HERE, ahead of everything that reads `rate`.
    #
    # Margo raised it (via Marveen, msg 4206) from the other side: the worklog-app
    # treats rate 0 as a real zero fee on purpose, this file treats it as missing.
    # Both readings stay as they are -- what was wrong was that `no_charge`, which
    # is neither of those, fell through to the second one.
    if r.get("no_charge"):
        print(f"#{a.id} NEM SZAMLAZANDO, es ezert nem keszitek szamlat.\n"
              f"  A dontes a rekordban all: {r['no_charge']}\n"
              f"  Ha megis szamlazni kell, eloszor a dontest kell visszavonni, "
              f"nem dijat megadni.")
        return 0
    hours = _hours(r)
    lines = []
    overtime_unresolved = False
    if r.get("billing") == "fixed":
        # Fixed price: ONE line, and the description must say so. The amount could
        # be produced without this mode (rate=500, days=1 gives 500), and it would
        # even be correct -- but the line would read "Labour, 1 day" at a 500/day
        # rate, which is not what was agreed. This document goes to the client, so
        # a right total under a wrong description is still a wrong invoice.
        # Added 2026-09-02: the owner said he sometimes agrees a fixed price
        # ("nem a szokvanyos napi berben dolgozom vagy fix arban egyezunk meg").
        # NOTE: in this mode `rate` is the agreed TOTAL, not a per-day figure.
        if r.get("rate"):
            lines.append({"desc": f"Labour, agreed fixed price"
                                  f" ({hours} hours on site)",
                          "qty": 1, "unit": float(r["rate"]),
                          "cis": bool(r.get("cis"))})
        days = round(hours / 8.0, 2)
    elif r.get("billing") == "daily":
        # Day rate: a day is a day. 9.58 hours is one day's work, not 1.2 days,
        # and nobody invoices a contractor for 1.2 days. Breaks are irrelevant
        # here too, which is why the billing mode has to be known before the
        # lunch question is even worth asking.
        days = max(1, r.get("days") or 1)
        if r.get("rate"):
            lines.append({"desc": f"Labour, {days} day{'s' if days > 1 else ''}"
                                  f" ({hours} hours on site)",
                          "qty": days, "unit": float(r["rate"]),
                          "cis": bool(r.get("cis"))})
            # Overtime is a SECOND line, not a bigger day. The client should see
            # what he is paying for, and the day rate stays the day rate.
            derived = _derived_rule_note(r)
            if derived:
                print(f"FIGYELEM: {derived}", file=sys.stderr)
            ot, why = _overtime(r)
            if ot is None:
                # The refusal has to reach the EXIT CODE, not only stderr. Until
                # 2026-09-09 this printed and returned 0, so a job with unbilled
                # overtime looked like a finished invoice to anything checking $?.
                #
                # That was tolerable while the only 08:00 start in the ledger was
                # job #1 (no_charge). It stopped being tolerable the moment the
                # owner said the 08:00 start is the RULE for installation days
                # ("nem mindig tudunk 7-kor kezdeni... olyankor 8-kor kezdunk",
                # 2026-09-09). From here on this fires on real, invoiced days:
                # an 08:00-19:00 day would go out at 200 with three hours missing.
                overtime_unresolved = True
                print(f"TULORA: NEM SZAMOLOM EL MAGAMTOL. {why}", file=sys.stderr)
            elif ot > 0:
                # qty stays the EXACT quotient; only the description is rounded.
                # The file's own note above explains why: invoice.py multiplies
                # qty * unit, so a value rounded here loses money in the total and
                # in the CIS base with it.
                unit = float(r["rate"]) / 8.0
                shown = round(ot, 2)
                lines.append({"desc": f"Overtime, {shown:g} hour{'' if shown == 1 else 's'}"
                                      f" beyond the {PAID_PRESENCE_HOURS}-hour day"
                                      f" at {unit:g}/hour",
                              "qty": ot, "unit": unit,
                              "cis": bool(r.get("cis"))})
    else:
        # A KEREKITETT NAPSZAM CSAK SZOVEG, A SZAMLA A PONTOS TORTTEL SZAMOL.
        # Merve 2026-09-09 (Cody talalta, Marveen visszamerte): 9 ora / 200-as
        # napi tetelnel a `stop` sora 225.0-t irt ki, a szamla viszont 224.0-t
        # keszitett. Az ok nem a keplet, hanem a SORREND: a `round(9/8, 2)` a
        # felet a PAROS fele viszi (1.125 -> 1.12), es az invoice.py mindenhol
        # `qty * unit`-ot szamol (invoice.py:239, :249, :317), tehat a levagott
        # ezred egy egesz fontot vitt el -- a CIS-alapbol is.
        # A 225 a helyes: 9/8 nap * 200 font. Ezert a `qty` a PONTOS hanyados,
        # es a kerekitett ertek csak a leirasban all, ahol ember olvassa.
        # Amiert ez fontos a par font ellenere: a `stop` zaro sora az, amit a
        # gazda a muhelyben elolvas, tehat a szamlaval kell egyeznie. A fajl
        # sajat kommentje (338-343) EPP errol a hibaosztalyrol szol.
        # A LEIRAS ES A MENNYISEG-OSZLOP UGYANAZT A SZAMOT MUTASSA. Az invoice.py
        # a qty-t `:g` formaval nyomtatja (invoice.py:319), tehat 1.125-ot ir ki;
        # ha a leirasban 1.12 allna, a kesz lapon ket kulonbozo napszam nezne
        # egymassal szembe ugyanabban a sorban. Ezert itt is `:g`.
        qty = hours / 8.0
        if r.get("rate"):
            lines.append({"desc": f"Labour, {hours} hours ({qty:g} days)",
                          "qty": qty, "unit": float(r["rate"]),
                          "cis": bool(r.get("cis"))})
    job = {
        "client": {"name": r["client"], "address": [r["address"]] if r.get("address") else []},
        "job": r["job"],
        "cis": bool(r.get("cis")),
        "lines": lines,
        # INTERNAL notes, and the underscore is the point. invoice.py PRINTS
        # job["notes"] on the customer's PDF under a "Notes" heading
        # (invoice.py:358-361), and the notes this ledger collects are not for a
        # customer: they are provenance ("a befejezes ideje a gazda szava,
        # Telegram ..."), corrections ("napidij: nincs -> 200.0"), and open
        # questions. Measured 2026-09-09: job #13 carries 277 characters of
        # exactly that, and it would have gone out on the Palmer invoice on the
        # first run through this path. The two issued invoices escaped only
        # because they were built from hand-written job files with no notes at all.
        #
        # A real customer note is still possible: put `notes` into the job file
        # by hand before running invoice.py. That is now a DECISION, not a leak.
        "_notes": "\n".join(n["text"] for n in r.get("notes", [])),
        "_job_id": r["id"],
        "_photos": [p["path"] for p in r.get("photos", [])],
    }
    out = a.out or f"job{r['id']}.json"
    with open(out, "w") as fh:
        json.dump(job, fh, indent=2, ensure_ascii=False)
    print(f"Kiirva: {out}")
    print(f"Munkaido {hours} ora ({days} nap).")
    print(f"Anyagot es egyeb tetelt kezzel adj hozza, aztan:")
    print(f"  python3 scripts/muhely/invoice.py invoice {out} --out szamla.pdf")
    if not lines:
        # A job with no labour line is not a usable invoice input, and until
        # 2026-09-09 this said so and then returned 0. The sentence stops a person
        # who reads the terminal; it stops nothing else. Measured that day on job
        # #12 (2026-09-07, started before `start` learned to read the day rate from
        # muhely-business.json): the file was written, the warning printed, and the
        # exit code was 0 -- so any step that checks `$?` instead of reading Hungarian
        # would carry an invoice with zero labour on it straight to the client.
        # The owner's money is on this path, so the failure has to be in the channel
        # a caller can actually test.
        print("FIGYELEM: nincs munkadij a munkan (--rate hianyzott inditaskor), "
              "ezert a szamla sorok URESEK. Ird bele kezzel, vagy add meg a dijat:",
              file=sys.stderr)
        print(f"  python3 scripts/muhely/jobs.py edit {r['id']} --rate <osszeg>",
              file=sys.stderr)
        return 3
    if overtime_unresolved:
        # Distinct from 3: there IS a labour line, the invoice is merely incomplete.
        # The file is written either way, so the figures are there to look at.
        print("A szamla EGYEBKENT kesz, de a tulora-sor hianyzik belole. Dontsd el a "
              "ket olvasas kozott, es ird bele kezzel, mielott kimegy.", file=sys.stderr)
        return 4
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start")
    p.add_argument("--client", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--address")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    # The unit lives HERE, next to where the number is typed. Cody and Margo
    # 2026-09-02: the --billing text explains it, but somebody reading only
    # this line in --help sees nothing -- and that is the person entering the
    # figure. The same rule the screen labels follow.
    p.add_argument("--rate", type=float,
                   help="NAPI ertek (oraaranyosnal is); fix arnal a MEGALLAPODOTT TELJES OSSZEG. Lasd --billing")
    p.add_argument("--cis", action="store_true")
    p.add_argument("--at", help="kezdes idopontja, pl. 8 vagy 08:00")
    p.add_argument("--billing", choices=("daily", "hourly", "fixed"), default="daily",
                   help="napidijas (alap), oraaranyos, vagy fix ar. MINDHAROM modban a "
                        "--rate NAPI ertek, KIVEVE a fix arat, ahol a MEGALLAPODOTT "
                        "TELJES OSSZEG. Az 'hourly' NEM oradij: a napi tetelt aranyositja "
                        "orara (ora/8), tehat 200-at irj be, ne 25-ot")
    p.add_argument("--days", type=int, help="hany napot szamlazunk (napidijasnal)")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("photo"); p.add_argument("file"); p.add_argument("--note")
    p.add_argument("--hely", action="store_true",
                   help="a foto GPS-koordinatajat vedd at a MUNKA helyenek is. "
                        "Alapbol nem: a kep helye nem a munka helye")
    p.set_defaults(fn=cmd_photo)
    p = sub.add_parser("note"); p.add_argument("text")
    p.add_argument("--id", type=int, help="lezart munkahoz is")
    p.set_defaults(fn=cmd_note)
    p = sub.add_parser("edit"); p.add_argument("id", type=int)
    p.add_argument("--job"); p.add_argument("--client")
    p.add_argument("--address", help="a munkavegzes helye, ha utolag derult ki")
    p.add_argument("--stop", help="a befejezes idopontjanak javitasa, pl. 16:00")
    p.add_argument("--rate", type=float,
                   help="a napidij potlasa vagy javitasa, ha inditaskor lemaradt")
    p.add_argument("--force", action="store_true",
                   help="lezart munkanal is, ha biztos hogy meg nincs szamlazva")
    p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("stop"); p.add_argument("--note")
    p.add_argument("--at", help="befejezes idopontja, pl. 17:30")
    p.set_defaults(fn=cmd_stop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("list"); p.add_argument("--client")
    p.add_argument("--open", action="store_true"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("id", type=int); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("invoice"); p.add_argument("id", type=int)
    p.add_argument("--out"); p.set_defaults(fn=cmd_invoice)

    a = ap.parse_args(argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
