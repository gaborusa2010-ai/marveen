#!/usr/bin/env python3
"""
Quotation and invoice generator for a UK sole trader.

Produces an A4 portrait PDF that satisfies what HMRC expects on a sole trader
invoice, and refuses to produce one that does not.

Not VAT registered is the default and the safe case: the document then shows no
VAT line at all. Showing "VAT 0%" or charging VAT while unregistered is an
offence, so this script will not do it unless a VAT number is configured.

Usage
-----
  invoice.py quote   <job.json> [--out FILE.pdf]
  invoice.py invoice <job.json> [--out FILE.pdf]
  invoice.py next-number [quote|invoice]     # peek at the next reference

Business details live in store/muhely-business.json (created on first run as a
template). Job file:

{
  "client":  {"name": "Mrs A Patel", "address": ["12 Elm Road", "Croydon", "CR0 1AB"]},
  "job":     "Fitted chest of drawers, oak veneer",
  "date":    "2026-08-21",
  "due_days": 14,
  "lines": [
    {"desc": "Materials: 18mm birch ply, 6mm back", "qty": 1, "unit": 116.90},
    {"desc": "Labour, 3 days @ 240", "qty": 3, "unit": 240.00},
    {"desc": "Hardware: soft close runners", "qty": 3, "unit": 18.50}
  ],
  "deposit": 300.00,
  "notes":   "Lead time 3 weeks from deposit.",
  "cis":     true          # construction work: turns on the CIS deduction
}

Mark each line with "cis": true where it is LABOUR. Materials, fuel and hired
plant stay false, or the contractor deducts 20% from money that was never yours.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdfout import Page, write_pdf, A4_PORTRAIT

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
BUSINESS = os.path.join(STORE, "muhely-business.json")
COUNTERS = os.path.join(STORE, "muhely-counters.json")
# Which job each issued reference came from. Separate file from COUNTERS on
# purpose: that one is a counter and nothing else. Putting a log beside a
# counter in one file is how a field ends up meaning two things, which is the
# bug this whole change exists to undo -- jobs.py had one `invoiced` field
# carrying both "billed" and "never to be billed", and the list read it as a
# boolean and called a free favour an invoiced job for days.
ISSUED = os.path.join(STORE, "muhely-invoice-log.json")

BUSINESS_TEMPLATE = {
    "_doc": "Sole trader adatai a szamlahoz. A csillagozott mezok KOTELEZOEK.",
    "trading_name": "",
    "legal_name": "",
    "address": [],
    "email": "",
    "phone": "",
    "utr": "",
    "vat_number": "",
    "_vat_doc": "Ures vat_number = NEM VAT regisztralt. Ilyenkor a szamlan SEMMILYEN "
                "VAT sor nem szerepelhet. Csak akkor toltsd ki, ha tenyleg regisztralt vagy.",
    "vat_rate": 20.0,
    "bank": {"account_name": "", "sort_code": "", "account_number": ""},
    "payment_terms_days": 14,
    "quote_valid_days": 30,
    "footer": "",
    "quote_prefix": "Q",
    "invoice_prefix": "INV",
}

REQUIRED = ["legal_name", "address", "email"]


def load_business():
    if not os.path.exists(BUSINESS):
        os.makedirs(STORE, exist_ok=True)
        with open(BUSINESS, "w") as fh:
            json.dump(BUSINESS_TEMPLATE, fh, indent=2, ensure_ascii=False)
        raise SystemExit(
            f"Letrehoztam a sablont: {BUSINESS}\n"
            f"Toltsd ki (legalabb: {', '.join(REQUIRED)}), aztan futtasd ujra.")
    with open(BUSINESS) as fh:
        b = json.load(fh)
    missing = [k for k in REQUIRED if not b.get(k)]
    if missing:
        raise SystemExit(
            f"HIANYZO KOTELEZO MEZO a {BUSINESS} fajlban: {', '.join(missing)}.\n"
            f"Enelkul a szamla nem felel meg a HMRC elvarasainak, ezert nem allitom ki.")
    return b


def next_number(kind, business, bump=True):
    """Continue the owner's existing sequence, do not start a parallel one.

    An invoice run that jumps from 150 to INV-2026-0001 looks to an accountant
    like a whole year of invoices went missing. With no prefix configured the
    reference is the bare running number, which is what the old invoices used.
    """
    # A szamlalo a FOLYAMATOS sorszamozas egyetlen forrasa, tehat az irasa nem
    # lehet reszleges. A regi alak `open(COUNTERS, "w")` volt: az AZONNAL csonkol,
    # es egy elszallas a csonkolas es a kiiras kozott ures vagy fel fajlt hagy --
    # onnan a kovetkezo szamla 1-tol indulna, ami HMRC-oldalrol egy eltunt ev.
    # Ezert: lock a teljes read-modify-write koré, majd temp + os.replace, ami
    # POSIX-on atomi. Cody merte fel 2026-09-06-an, az app-atvetel kapcsan, de a
    # csonkolasi ag EGYETLEN folyamatnal is fennall, nem csak parhuzamosnal.
    os.makedirs(STORE, exist_ok=True)
    lock_path = COUNTERS + ".lock"
    with open(lock_path, "a+") as lock:
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Zar nelkul is helyes marad az atomi iras; csak a parhuzamos
            # hivasok kozotti versenyt nem zarja ki. Ezt nem hallgatjuk el.
            pass
        counters = {}
        if os.path.exists(COUNTERS):
            with open(COUNTERS) as fh:
                counters = json.load(fh)
        year = date.today().year
        key = f"{kind}_{year}"
        n = int(counters.get(key, 0)) + 1
        if bump:
            counters[key] = n
            tmp = COUNTERS + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(counters, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, COUNTERS)
    prefix = (business.get(f"{kind}_prefix") or "").strip()
    if not prefix:
        return str(n)
    return f"{prefix}-{year}-{n:04d}"


def _issued_load():
    if not os.path.exists(ISSUED):
        return []
    try:
        with open(ISSUED) as fh:
            rows = json.load(fh)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        # Unreadable log means the guard downstream cannot know and will ask a
        # human instead. That is the safe direction, so do not raise here.
        return []


def record_issued(kind, ref, job, out_path):
    """Write down which job this reference was spent on.

    WHY, and it is not bookkeeping for its own sake: jobs.py refuses to edit a
    job's description once an invoice has gone out, because the paper in the
    customer's hand and the record here must not drift apart. Until now it had
    no way to know, so it asked for --force on every closed job -- a guard that
    can only ever say "I cannot tell". This is the fact it was missing.

    Called ONLY after the PDF is written and the number is spent, and after the
    collision check. Earlier would be worse than not writing at all: a failed
    render would register an invoice that does not exist, and jobs.py would then
    block a correction citing a document nobody ever sent.

    `job_id` is None for a hand-written job JSON that did not come from jobs.py.
    That is honest, and it is exactly the case where --force must stay: nothing
    here can vouch for a file we did not produce.

    A failure to write is loud but not fatal. The PDF exists and the number is
    gone; refusing at this point would help nobody. Missing the record only
    costs the downstream guard its certainty, and it degrades to asking.
    """
    ids = job_ids(job)
    rec = {
        "kind": kind,
        "reference": str(ref),
        # `job_id` stays for every reader written before multi-day invoices
        # existed; `job_ids` is the true answer. One value in two shapes is a
        # smell, but dropping the old key would silently blind jobs.py's
        # already-invoiced guard, which is the one thing this record is FOR.
        "job_id": ids[0] if ids else None,
        "job_ids": ids,
        "client": (job.get("client") or {}).get("name"),
        "pdf": out_path,
        "at": int(time.time()),
    }
    rows = _issued_load()
    # Re-rendering the same reference must not create a second entry: the
    # question the log answers is "did a document with this number go out",
    # and that is a yes or a no, not a count.
    rows = [r for r in rows
            if not (r.get("kind") == kind and str(r.get("reference")) == str(ref))]
    rows.append(rec)
    try:
        os.makedirs(STORE, exist_ok=True)
        with open(ISSUED, "w") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"FIGYELEM: a szamla elkeszult ({out_path}, {ref}), de a "
              f"nyilvantartasba nem sikerult beirni: {e}\n"
              f"  A jobs.py ezert nem fogja tudni, hogy errol a munkarol ment "
              f"szamla, es megerositest fog kerni a javitashoz.", file=sys.stderr)


def job_ids(job):
    """The work days this document covers, as a list. [] when it says none.

    A one-day invoice carries `_job_id` (jobs.py writes it); a multi-day one
    carries `_job_ids`, because an invoice for a week of work is the NORMAL
    shape here, not the exception -- five of the six invoices issued so far
    cover more than one day.

    Both keys are read so that a job file written before this existed keeps
    working, and so that a hand-written file can name its days with one line.
    """
    out = []
    for v in (job.get("_job_ids") or []):
        if v is not None and v not in out:
            out.append(v)
    one = job.get("_job_id")
    if one is not None and one not in out:
        out.append(one)
    return out


def issued_for(job_id):
    """Every reference issued for one job, newest last. [] if none.

    Reads BOTH shapes of the record. Checking only `job_id` would miss every
    day of a multi-day invoice except the first, and the caller would then be
    told that a day it already billed is free to bill again.
    """
    if job_id is None:
        return []
    return [r for r in _issued_load()
            if r.get("job_id") == job_id or job_id in (r.get("job_ids") or [])]


def money(v):
    return f"{v:,.2f}"


def render(kind, job, business, out_path):
    is_invoice = kind == "invoice"
    title = "INVOICE" if is_invoice else "QUOTATION"
    # Peek at the number, do NOT consume it yet. Anything below can still raise
    # (a malformed job file, a font/layout error), and a burnt reference leaves a
    # permanent hole in the sequence -- the one thing HMRC numbering must not do.
    # The counter is bumped only after the PDF is on disk. Measured 2026-08-23:
    # a job file with a bad line still advanced the counter from 10010 to 10011.
    explicit_ref = job.get("reference")
    ref = explicit_ref or next_number(kind, business, bump=False)

    issued = job.get("date") or date.today().isoformat()
    issued_d = datetime.strptime(issued, "%Y-%m-%d").date()
    if is_invoice:
        days = int(job.get("due_days", business.get("payment_terms_days", 14)))
        second_label, second_date = "Payment due", issued_d + timedelta(days=days)
    else:
        days = int(job.get("valid_days", business.get("quote_valid_days", 30)))
        second_label, second_date = "Valid until", issued_d + timedelta(days=days)

    lines = job.get("lines", [])
    subtotal = sum(float(l["qty"]) * float(l["unit"]) for l in lines)

    # Construction Industry Scheme. The contractor deducts at source, but ONLY
    # from labour and profit. Materials, consumables, fuel and third-party plant
    # hire are stripped out first, which is why every line carries its own flag
    # and the default is False: deducting from materials costs real money.
    cis_rate = job.get("cis_rate", business.get("cis_rate") if job.get("cis") else None)
    cis_base = cis = None
    if cis_rate:
        cis_rate = float(cis_rate)
        cis_base = sum(float(l["qty"]) * float(l["unit"])
                       for l in lines if l.get("cis"))
        cis = round(cis_base * cis_rate / 100.0, 2)

    vat_no = (business.get("vat_number") or "").strip()
    if vat_no:
        rate = float(business.get("vat_rate", 20.0))
        vat = round(subtotal * rate / 100.0, 2)
        total = round(subtotal + vat, 2)
    else:
        rate = vat = None
        total = round(subtotal, 2)
    if cis:
        total = round(total - cis, 2)

    deposit = float(job.get("deposit") or 0)
    balance = round(total - deposit, 2)

    W, H = A4_PORTRAIT
    pg = Page(W, H)
    L, R = 45.0, W - 45.0
    y = H - 55

    pg.text(L, y, business.get("trading_name") or business["legal_name"], size=16, font="F2")
    pg.text(R, y, title, size=20, font="F2", align="right")
    y -= 18
    if business.get("trading_name") and business.get("legal_name") != business.get("trading_name"):
        pg.text(L, y, business["legal_name"], size=8.5, colour=(0.35, 0.35, 0.35))
        y -= 11
    for ln in business.get("address", []):
        pg.text(L, y, ln, size=8.5, colour=(0.35, 0.35, 0.35))
        y -= 11
    for k, lab in (("email", ""), ("phone", "")):
        if business.get(k):
            pg.text(L, y, f"{lab}{business[k]}", size=8.5, colour=(0.35, 0.35, 0.35))
            y -= 11
    if vat_no:
        pg.text(L, y, f"VAT no. {vat_no}", size=8.5, colour=(0.35, 0.35, 0.35))
        y -= 11

    ry = H - 82
    pg.text(R, ry, ref, size=10, font="F2", align="right"); ry -= 13
    pg.text(R, ry, f"Date  {issued_d.strftime('%d %b %Y')}", size=9, align="right"); ry -= 12
    pg.text(R, ry, f"{second_label}  {second_date.strftime('%d %b %Y')}", size=9, align="right")

    y = min(y, ry) - 22
    pg.line(L, y, R, y, colour=(0.75, 0.75, 0.75), lw=0.8)
    y -= 20

    cl = job.get("client", {})
    pg.text(L, y, "To", size=8, font="F2", colour=(0.4, 0.4, 0.4)); y -= 13
    pg.text(L, y, cl.get("name", ""), size=10.5, font="F2"); y -= 13
    for ln in cl.get("address", []):
        pg.text(L, y, ln, size=9, colour=(0.3, 0.3, 0.3)); y -= 11

    y -= 12
    if job.get("job"):
        pg.text(L, y, job["job"], size=10, font="F2"); y -= 20

    # line item table
    pg.rect(L, y - 4, R - L, 18, fill=(0.93, 0.93, 0.92))
    pg.text(L + 6, y + 2, "Description", size=8.5, font="F2")
    pg.text(R - 190, y + 2, "Qty", size=8.5, font="F2", align="right")
    pg.text(R - 105, y + 2, "Unit", size=8.5, font="F2", align="right")
    pg.text(R - 6, y + 2, "Amount", size=8.5, font="F2", align="right")
    y -= 20

    for l in lines:
        amt = float(l["qty"]) * float(l["unit"])
        pg.text(L + 6, y, str(l["desc"])[:64], size=9)
        pg.text(R - 190, y, f"{float(l['qty']):g}", size=9, align="right")
        pg.text(R - 105, y, money(float(l["unit"])), size=9, align="right")
        pg.text(R - 6, y, money(amt), size=9, align="right")
        y -= 15
        if y < 190:
            pg.text(L + 6, y, "...", size=9)
            break

    y -= 6
    pg.line(R - 250, y, R, y, colour=(0.75, 0.75, 0.75), lw=0.6)
    y -= 16

    def total_row(label, value, bold=False, size=9.5):
        pg.text(R - 105, y, label, size=size, align="right", font="F2" if bold else "F1")
        pg.text(R - 6, y, money(value), size=size, align="right", font="F2" if bold else "F1")

    if vat_no or cis:
        total_row("Subtotal", subtotal); y -= 14
    if vat_no:
        total_row(f"VAT @ {rate:g}%", vat); y -= 14
    if cis:
        total_row(f"CIS @ {cis_rate:g}% (labour)", -cis); y -= 14
    total_row("Total", total, bold=True, size=11); y -= 16

    if deposit:
        total_row("Deposit received", -deposit); y -= 16
        total_row("Balance due", balance, bold=True, size=11); y -= 16

    y -= 10
    if is_invoice:
        bank = business.get("bank") or {}
        if bank.get("account_number"):
            pg.text(L, y, "Payment", size=8.5, font="F2", colour=(0.4, 0.4, 0.4)); y -= 12
            pg.text(L, y, f"{bank.get('account_name','')}   "
                          f"Sort code {bank.get('sort_code','')}   "
                          f"Account {bank.get('account_number','')}", size=9); y -= 13
            pg.text(L, y, f"Please quote {ref} as the payment reference.", size=8.5,
                    colour=(0.35, 0.35, 0.35)); y -= 16

    if job.get("notes"):
        pg.text(L, y, "Notes", size=8.5, font="F2", colour=(0.4, 0.4, 0.4)); y -= 12
        for ln in str(job["notes"]).split("\n"):
            pg.text(L, y, ln[:95], size=8.5, colour=(0.3, 0.3, 0.3)); y -= 11

    foot = business.get("footer") or ""
    if not vat_no:
        foot = (foot + "  " if foot else "") + "Not VAT registered."
    pg.text(L, 42, foot.strip(), size=7.5, colour=(0.5, 0.5, 0.5))
    # UTR and NI go on every invoice by default: the old Express Invoice
    # sheets carried them, Palmer expects them, and a CIS contractor needs the
    # UTR to verify the subcontractor with HMRC. Pass "show_utr": false to
    # leave them off a one-off invoice.
    if business.get("utr") and is_invoice and job.get("show_utr", True):
        extra = f"UTR {business['utr']}"
        if business.get("ni_number"):
            extra += f"   NI {business['ni_number']}"
        pg.text(R, 42, extra, size=7.5, colour=(0.5, 0.5, 0.5), align="right")

    write_pdf(out_path, [pg], title=f"{title} {ref}")
    # PDF exists: only now is the reference actually spent.
    if not explicit_ref:
        consumed = next_number(kind, business)
        if consumed != ref:
            # Another process took the number between the peek and the write.
            # Say so loudly rather than shipping a PDF whose printed reference
            # disagrees with the ledger.
            raise SystemExit(
                f"sorszam utkozes: a PDF-en {ref} all, de a szamlalo {consumed}-t "
                f"adta ki. A {out_path} fajlt NE hasznald, futtasd ujra.")
    # Past the collision check, so the number on the PDF is the number the
    # ledger agrees was spent. Only now is there something true to write down.
    record_issued(kind, ref, job, out_path)
    # The work log has to learn the number too, or jobs.py will keep offering
    # these days up as unbilled. See mark_jobs_invoiced for what that cost.
    _say = lambda m: print(m, file=sys.stderr)
    mark_jobs_invoiced(kind, ref, job, report=_say)
    warn_if_unlinked(kind, ref, job, report=_say)
    save_source(job, ref, out_path)
    archive_to_drive(kind, out_path)
    return {"reference": ref, "subtotal": round(subtotal, 2), "vat": vat,
            "cis_rate": cis_rate, "cis_base": cis_base, "cis": cis,
            "total": total, "deposit": deposit, "balance": balance,
            "vat_registered": bool(vat_no), "pdf": out_path}


JOBS_LEDGER = os.path.join(STORE, "muhely-jobs.json")


def mark_jobs_invoiced(kind, ref, job, report=None):
    """Write the issued number back onto every work day this invoice covers.

    WHY THIS EXISTS, measured 2026-09-20. Two files answer "is this day billed"
    and only one of them was being updated. The register (muhely-invoice-log)
    learned the answer at issue time; the work log (muhely-jobs.json,
    `invoice_ref`) did not, and `jobs.py list` -- which is what the Sunday
    invoicing sentry reads -- shows the work log. So five days that had just
    gone out on invoice 10016 still read `kesz` afterwards, and the NEXT Sunday
    the sentry would have offered them up to be invoiced a second time. The
    same trap the skill's own Buktatok section warns about, one file over.

    It had not bitten yet only because the one-day path is rare here: a
    hand-written multi-day job file names no days at all, so nothing was ever
    written back and nothing ever looked wrong.

    SOFT, like the Drive copy and for the same reason: the PDF exists and the
    number is spent by the time this runs. A failure here is reported and
    dropped -- and it degrades to exactly the state we were in before, which
    `jobs.py` still guards with its own "LEZART munka" refusal.
    """
    if kind != "invoice":
        return []
    ids = job_ids(job)
    if not ids:
        return []
    try:
        with open(JOBS_LEDGER, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError) as e:
        if report:
            report(f"FIGYELEM: a szamla elkeszult, de a munkanaplot nem tudtam "
                   f"megjelolni ({e}). Kezzel: jobs.py szamlazva {ref} "
                   f"{' '.join(str(i) for i in ids)}")
        return []
    touched, conflict = [], []
    for r in rows:
        if r.get("id") in ids:
            old = r.get("invoice_ref")
            if old and str(old) != str(ref):
                # Do NOT overwrite. A day already carrying a DIFFERENT number
                # means two invoices claim the same work, and that is a question
                # for a person, not something to tidy away.
                conflict.append((r["id"], old))
                continue
            if not old:
                r["invoice_ref"] = str(ref)
                touched.append(r["id"])
    if conflict and report:
        for jid, old in conflict:
            report(f"FIGYELEM: a #{jid} munkanapon MAR a {old} szamu szamla all, "
                   f"most a {ref} is ra hivatkozna. NEM irtam at.")
    if not touched:
        return []
    try:
        tmp = JOBS_LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, JOBS_LEDGER)
    except OSError as e:
        if report:
            report(f"FIGYELEM: a szamla elkeszult, de a munkanaplo irasa elbukott "
                   f"({e}). Kezzel: jobs.py szamlazva {ref} "
                   f"{' '.join(str(i) for i in ids)}")
        return []
    if report:
        report(f"Munkanaplo: a {ref} bevezetve ide -> "
               f"{', '.join('#' + str(i) for i in touched)}")
    return touched


def warn_if_unlinked(kind, ref, job, report=None):
    """Say so when an invoice names no work day at all.

    Not a refusal: a hand-written invoice for something that never went through
    the work log is legitimate. But silence here is expensive -- the days stay
    `kesz`, and the Sunday sentry offers them again as unbilled. One sentence
    and the exact command costs nothing and closes the loop.
    """
    if kind != "invoice" or job_ids(job) or not report:
        return
    report(f"FIGYELEM: a {ref} szamu szamla EGYETLEN munkanaphoz sincs kotve "
           f"(nincs _job_id vagy _job_ids a job fajlban).\n"
           f"  Amig nincs, a `jobs.py list` ezeket a napokat SZAMLAZANDOKENT "
           f"mutatja, es a vasarnapi orszem ujra felhozza oket.\n"
           f"  Kezzel: python3 scripts/muhely/jobs.py szamlazva {ref} <nap-id-k>")


def archive_to_drive(kind, out_path):
    """Put a finished INVOICE on the owner's Drive, in the Szamlak folder.

    WHY it runs here and not in a weekly job: on 2026-09-17 the owner asked why
    his invoices were not on his Drive. Measured that morning -- Szamlak held
    10011 and nothing else, while 10012, 10013, 10014 and 10015 had been sitting
    on disk since 08-30. Nothing had failed. Issuing an invoice simply uploaded
    nothing, and the sync script carried a hand-written list that named exactly
    one file. The gap was not an error anyone could see; it was work nobody did.

    Invoices only. A quote is a draft and the owner asked for the folder to hold
    what went out under a number.

    LAST, and soft. The PDF is written and the number is spent by the time this
    runs, so a network failure must be reported and dropped, never raised: the
    invoice is the deliverable, the copy is a convenience. drive-sync-muhely.py
    picks up anything missed -- that second path is what makes this one safe to
    let fail. MUHELY_NO_DRIVE=1 turns it off for tests and offline runs.
    """
    if kind != "invoice":
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import drive_invoices
        drive_invoices.upload_one(out_path,
                                  report=lambda m: print(m, file=sys.stderr))
    except Exception as e:
        print(f"FIGYELEM: a szamla elkeszult ({out_path}), de a Drive-archivalas "
              f"el sem indult: {e}\n"
              f"  Kezzel: python3 scripts/muhely/drive-sync-muhely.py", file=sys.stderr)
    # SECOND home, on the owner's own desktop. His words, 2026-09-20, Telegram
    # msg 617: "mostantol oda is legyen elmentve az osszes szamla". Separate
    # try/except on purpose -- a Drive outage must not cost him the local copy,
    # and a missing drive mount must not cost him the Drive copy.
    try:
        import desktop_invoices
        desktop_invoices.copy_one(out_path,
                                  report=lambda m: print(m, file=sys.stderr))
    except Exception as e:
        print(f"FIGYELEM: a szamla elkeszult ({out_path}), de a desktop-masolat "
              f"el sem indult: {e}\n"
              f"  Kezzel: python3 scripts/muhely/desktop_invoices.py",
              file=sys.stderr)


def save_source(job, ref, out_path):
    """Keep the job file that produced this PDF, next to the PDF.

    WHY, and it is a closed loop rather than a convenience: on 2026-09-07 the
    owner asked for two corrections to one invoice, and neither time was there a
    source to correct. The lines had to be read back out of the PDF that had
    already been issued -- so the input to the fix was our own output. That
    catches a mistake made DURING the fix, and misses one that was already in
    what we read. The result would be an official invoice, under the same
    number, carrying a number that merely looks right; and a wrong figure on a
    sent invoice is the most expensive kind of mistake on this side of the work.

    Written AFTER record_issued for the same reason that one runs last: nothing
    is recorded about a document until the document exists. And it must never
    interfere with what came before it -- the numbering is written to survive a
    crash mid-write, so a failure here is REPORTED and then dropped. The PDF is
    on disk and the number is spent; refusing at this point would help nobody.

    The reference is stored explicitly, so re-rendering this file reproduces the
    same invoice number instead of consuming a new one.

    Not backfilled for documents issued before this existed: a source
    reconstructed from a finished PDF is not a source, it is a reading of one,
    and it would sit in the folder looking exactly like the real thing.
    """
    saved = dict(job)
    saved["reference"] = str(ref)
    path = os.path.splitext(out_path)[0] + ".json"
    try:
        with open(path, "w") as fh:
            json.dump(saved, fh, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as e:
        # Loud, because a silent miss here is invisible until the day someone
        # needs the file. Not fatal, because the invoice itself is fine.
        print(f"FIGYELEM: a szamla forrasa nem mentodott el ({path}): {e}",
              file=sys.stderr)
        return None
    return path


def main(argv):
    if len(argv) < 2 or argv[1] not in ("quote", "invoice", "next-number"):
        print(__doc__)
        return 2
    business = load_business()
    if argv[1] == "next-number":
        kind = argv[2] if len(argv) > 2 else "invoice"
        print(next_number(kind, business, bump=False))
        return 0
    kind = argv[1]
    with open(argv[2]) as fh:
        job = json.load(fh)
    out = argv[argv.index("--out") + 1] if "--out" in argv else f"{kind}.pdf"

    # A SECOND INVOICE FOR THE SAME JOB SPENDS A NEW NUMBER, and until 2026-09-10
    # nothing here noticed. Measured that day in a sandbox copy: job #11, already
    # invoiced as 10014, rendered again without a word and moved the counter from
    # 10015 to 10016. Two invoices, same day of work, different numbers, and the
    # customer holding both would be right to ask which one to pay.
    #
    # The fact was already on file -- `issued_for` has answered this question since
    # the register was added, and jobs.py `edit` has been asking it all along. It
    # was simply never asked at the one moment that spends a number.
    #
    # A WARNING, NOT A BAN, and the difference matters: a corrective invoice is a
    # real thing, and so is a job wrongly marked. The rule is that a second number
    # must be a decision somebody made, not a side effect of running the same
    # command twice. Quotes are excluded on purpose: a customer may be re-quoted
    # any number of times, and a quote spends nothing that says what the work WAS.
    if kind == "invoice" and "--force" not in argv:
        # EVERY day this file names, not just the first. A multi-day invoice
        # that re-used one already-billed day would otherwise walk straight
        # through the guard, which is the case this guard exists for.
        prior, hit = [], None
        for jid in job_ids(job):
            p = [e for e in issued_for(jid) if e.get("kind") == "invoice"]
            if p:
                prior, hit = p, jid
                break
        if prior:
            refs = ", ".join(str(e.get("reference")) for e in prior)
            print(f"A #{hit} munkara MAR MENT SZAMLA: {refs}\n"
                  f"  {prior[-1].get('pdf')}\n"
                  f"Egy masodik szamla UJ SORSZAMOT koltene ugyanarra a munkara, es a "
                  f"vevo ket papirt kapna ugyanarrol a naprol.\n"
                  f"  Ha a MEGLEVo szamlat akarod ujra, nyisd meg a fenti PDF-et: "
                  f"ahhoz nem kell uj szamot kiadni.\n"
                  f"  Ha tenyleg UJ (helyesbito) szamla kell, ismeteld meg "
                  f"--force kapcsoloval.", file=sys.stderr)
            return 3

    r = render(kind, job, business, out)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
