#!/usr/bin/env python3
"""Synthetic workshop store for the szamla-kor tests (#758).

Usage: muhely-szamla-kor-fixture.py <out-dir> [--now EPOCH]

Writes the five store files the weekly invoicing run reads, plus one issued
invoice source, into <out-dir> laid out like store/:

    muhely-jobs.json              the work log
    muhely-ugyfelek.json          the client book
    muhely-business.json          the business details (made up, no secrets)
    muhely-counters.json          the running invoice number
    muhely-invoice-log.json       the register, with the one issued invoice
    muhely/invoices/10101-*.json  that invoice's saved source

Why a fixture and not the live store. Until 2026-11-02 the test copied the
real store, so its cases depended on how many unbilled days the owner happened
to have that day: on 11-01 it was green, after the 10024/10025 run it went red
with the code unchanged (system-map test #753, items 6 and 86). The fixture has
the same shape as the live data but a fixed content, so every case means the
same thing every day.

Dates are RELATIVE to now (or --now): the missing-days check in the run looks
at the last 14 calendar days from today, not from --date, so days older than
that would silently fall out of the "missing days" question.

Ledger order matters to the cases script, which picks candidates()[0] and
candidates()[-1]:
  * the first unbilled row is the single-day client (Teszt Harmadik Ugyfel),
  * the last unbilled rows are the non-CIS three-day client (Teszt Asztalos
    Kft), so a run.toml written with cis=false for candidates()[-1] is valid,
  * between them the MIXED client (Teszt Vegyes Epito) has two non-CIS and two
    CIS days: the default run must draft it as two invoices.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta

CLIENT_A = "Teszt Asztalos Kft"        # 3 non-CIS days, last in the ledger
CLIENT_B = "Teszt Vegyes Epito"        # 2 non-CIS + 2 CIS days
CLIENT_C = "Teszt Harmadik Ugyfel"     # 1 non-CIS day, first in the ledger
RATE = 200.0
ISSUED_REF = "10101"


def weekday_before(day):
    """The same day, or the Saturday before it if it is a Sunday (the log has
    no Sunday rows and the missing-days window is Monday to Saturday)."""
    while day.weekday() == 6:
        day -= timedelta(days=1)
    return day


def row(rid, client, job, start, cis=False, invoice_ref=None, hours=9, address=""):
    return {
        "id": rid, "client": client, "job": job, "address": address,
        "lat": None, "lon": None,
        "started_at": start.timestamp(), "stopped_at": (start + timedelta(hours=hours)).timestamp(),
        "rate": RATE, "cis": cis, "billing": "daily", "days": None, "photos": [],
        "notes": [], "invoice_ref": invoice_ref, "no_charge": None,
    }


def build(now):
    today = datetime.fromtimestamp(now).astimezone().replace(hour=7, minute=0, second=0, microsecond=0)

    def start(days_ago):
        return weekday_before(today - timedelta(days=days_ago))

    rows = [
        # Issued history: billed on ISSUED_REF, older than the missing-days window.
        row(1, CLIENT_A, "Muhelymunka", start(41), invoice_ref=ISSUED_REF),
        row(2, CLIENT_A, "Muhelymunka", start(40), invoice_ref=ISSUED_REF),
        # candidates()[0]: one day, one client.
        row(3, CLIENT_C, "Szekrenyek osszerakasa helyszinen", start(12)),
        # The mixed client: two plain days, then two CIS days.
        row(4, CLIENT_B, "Furniture fitting", start(10)),
        row(5, CLIENT_B, "Furniture fitting", start(9)),
        row(6, CLIENT_B, "Furniture fitting", start(8), cis=True),
        row(7, CLIENT_B, "Furniture fitting", start(7), cis=True),
        # candidates()[-1] and the two before it: three plain workshop days.
        row(8, CLIENT_A, "Muhelymunka", start(5)),
        row(9, CLIENT_A, "Muhelymunka", start(4)),
        row(10, CLIENT_A, "Muhelymunka", start(3)),
    ]
    clients = {
        "_doc": "Teszt-ugyfelkonyv a szamla-kor teszthez (#758): a nevek es cimek kitalaltak.",
        "_forras": "scripts/__tests__/muhely-szamla-kor-fixture.py",
        "ugyfelek": {
            CLIENT_A: {"name": "Mr Test Joiner, Teszt Asztalos Kft",
                       "address": ["1 Workshop Lane", "London", "SW1A 1AA"], "_forras": "teszt"},
            CLIENT_B: {"name": "Teszt Vegyes Epito Ltd",
                       "address": ["2 Builder Street", "London", "SW1A 2BB"], "_forras": "teszt"},
            CLIENT_C: {"name": "Mrs Test Third",
                       "address": ["3 Third Road", "London", "SW1A 3CC"], "_forras": "teszt"},
        },
    }
    business = {
        "_doc": "Kitalalt vallalkozasi adatok a szamla-kor teszthez (#758).",
        "trading_name": "Test Joinery", "legal_name": "Test Joiner",
        "address": ["10 Fixture Road", "London", "SW1A 0AA"],
        "email": "test@example.invalid", "phone": "020 0000 0000",
        "utr": "0000000000", "ni_number": "QQ000000C",
        "vat_number": "", "vat_rate": 20.0,
        "bank": {"account_name": "Test Joiner", "sort_code": "00-00-00", "account_number": "00000000"},
        "payment_terms_days": 14, "quote_valid_days": 30, "footer": "",
        "quote_prefix": "Q", "invoice_prefix": "",
        "cis_rate": 20, "day_rate": RATE,
    }
    issued_at = int(start(39).timestamp())
    issued = {
        "client": {"name": clients["ugyfelek"][CLIENT_A]["name"],
                   "address": clients["ugyfelek"][CLIENT_A]["address"]},
        "job": "Day work",
        "cis": False,
        "lines": [{"desc": f"{datetime.fromtimestamp(r['started_at']).strftime('%d/%m/%y')}  Workshop work",
                   "qty": 1, "unit": RATE, "cis": False} for r in rows[:2]],
        "_notes": "", "_photos": [], "_job_ids": [1, 2],
        "reference": ISSUED_REF,
    }
    log = [{"kind": "invoice", "reference": ISSUED_REF, "job_id": 1, "job_ids": [1, 2],
            "client": issued["client"]["name"], "pdf": None, "at": issued_at}]
    counters = {f"invoice_{datetime.fromtimestamp(now).year}": int(ISSUED_REF)}
    return rows, clients, business, counters, log, issued


def write(out, now):
    rows, clients, business, counters, log, issued = build(now)
    os.makedirs(os.path.join(out, "muhely", "invoices"), exist_ok=True)

    def dump(name, data):
        with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    dump("muhely-jobs.json", rows)
    dump("muhely-ugyfelek.json", clients)
    dump("muhely-business.json", business)
    dump("muhely-counters.json", counters)
    dump("muhely-invoice-log.json", log)
    dump(os.path.join("muhely", "invoices", f"{ISSUED_REF}-teszt-asztalos-kft.json"), issued)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out")
    p.add_argument("--now", type=float, default=None, help="epoch seconds to place the days against")
    a = p.parse_args()
    write(a.out, a.now if a.now is not None else time.time())
    print(a.out)


if __name__ == "__main__":
    main()
