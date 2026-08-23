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
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdfout import Page, write_pdf, A4_PORTRAIT

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
BUSINESS = os.path.join(STORE, "muhely-business.json")
COUNTERS = os.path.join(STORE, "muhely-counters.json")

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
    counters = {}
    if os.path.exists(COUNTERS):
        with open(COUNTERS) as fh:
            counters = json.load(fh)
    year = date.today().year
    key = f"{kind}_{year}"
    n = int(counters.get(key, 0)) + 1
    if bump:
        counters[key] = n
        os.makedirs(STORE, exist_ok=True)
        with open(COUNTERS, "w") as fh:
            json.dump(counters, fh, indent=2)
    prefix = (business.get(f"{kind}_prefix") or "").strip()
    if not prefix:
        return str(n)
    return f"{prefix}-{year}-{n:04d}"


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
    # UTR is normally kept off an invoice, but a CIS contractor needs it to
    # verify the subcontractor with HMRC, so it goes on when CIS applies.
    if business.get("utr") and is_invoice and (cis or job.get("show_utr")):
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
    return {"reference": ref, "subtotal": round(subtotal, 2), "vat": vat,
            "cis_rate": cis_rate, "cis_base": cis_base, "cis": cis,
            "total": total, "deposit": deposit, "balance": balance,
            "vat_registered": bool(vat_no), "pdf": out_path}


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
    r = render(kind, job, business, out)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
