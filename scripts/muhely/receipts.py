#!/usr/bin/env python3
"""
Receipt and expense ledger for a UK sole trader.

You photograph the till receipt or forward the online order; the agent reads it
and records it here with a category that maps straight onto the Self Assessment
form. At year end the accountant gets a CSV that already speaks their language,
which is fewer billed hours and fewer "what was this one?" emails.

Categories follow the SA103F self-employment expense boxes. Do not invent new
ones: an expense that does not fit an existing box is almost always in the wrong
place, and the accountant has to move it back.

Usage
-----
  receipts.py add --date 2026-08-21 --supplier "Wickes" --total 116.90 \
                  --category materials --job "Patel komod" --method card \
                  --image /path/to/photo.jpg [--vat 19.48] [--notes "..."]
  receipts.py list    [--year 2026/27] [--category materials] [--job X]
  receipts.py summary [--year 2026/27]
  receipts.py export  [--year 2026/27] [--out expenses.csv]
  receipts.py categories
  receipts.py check                      # data quality pass

The UK tax year runs 6 April to 5 April. "2026/27" means 6 Apr 2026 to 5 Apr 2027.
"""
import argparse
import csv
import json
import os
import sys
from datetime import date, datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
LEDGER = os.path.join(STORE, "muhely-receipts.json")

# key -> (human label, SA103F box description)
CATEGORIES = {
    "materials":   ("Anyag", "Cost of goods bought for resale or goods used"),
    "subcontract": ("Alvallalkozo", "Construction industry: payments to subcontractors"),
    "tools":       ("Szerszam, kis erteku", "Repairs and renewals of property and equipment"),
    "equipment":   ("Gep, nagy erteku", "CAPITAL: annual investment allowance, not an expense"),
    "travel":      ("Utazas, jarmu", "Car, van and travel expenses"),
    "premises":    ("Muhely berlet, rezsi, biztositas", "Rent, rates, power and insurance costs"),
    "office":      ("Telefon, iroda, papir", "Phone, fax, stationery and other office costs"),
    "advertising": ("Hirdetes", "Advertising and business entertainment costs"),
    "finance":     ("Banki koltseg, kamat", "Bank, credit card and other financial charges"),
    "professional": ("Konyvelo, jogi", "Accountancy, legal and other professional fees"),
    "insurance":   ("Szakmai biztositas", "Rent, rates, power and insurance costs"),
    "other":       ("Egyeb", "Other business expenses"),
}

# Over this, a tool is normally capital (annual investment allowance), not a
# same-year expense. Not a legal line, a practical one: flag it for the
# accountant instead of quietly expensing a table saw.
CAPITAL_THRESHOLD = 500.0


def tax_year(d):
    """UK tax year label for a date. 6 April is the boundary."""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    start = d.year if (d.month, d.day) >= (4, 6) else d.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def load():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER) as fh:
        return json.load(fh)


def save(rows):
    os.makedirs(STORE, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, LEDGER)      # atomic, so a crash cannot truncate the ledger


def cmd_add(a):
    if a.category not in CATEGORIES:
        print(f"Ismeretlen kategoria: {a.category}\nErvenyes: {', '.join(sorted(CATEGORIES))}")
        return 2
    try:
        d = datetime.strptime(a.date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Rossz datum formatum: {a.date}. Varhato: EEEE-HH-NN")
        return 2
    if d > date.today():
        print(f"FIGYELEM: a datum a jovoben van ({a.date}). Elgepeles?")

    rows = load()
    rid = max([r["id"] for r in rows], default=0) + 1
    row = {
        "id": rid,
        "date": a.date,
        "tax_year": tax_year(d),
        "supplier": a.supplier,
        "total": round(float(a.total), 2),
        "vat": round(float(a.vat), 2) if a.vat is not None else None,
        "category": a.category,
        "job": a.job or "",
        "method": a.method or "",
        "image": a.image or "",
        "notes": a.notes or "",
        "capital_flag": False,
    }
    if a.category in ("tools", "equipment") and row["total"] >= CAPITAL_THRESHOLD:
        row["capital_flag"] = True

    # A duplicate is far more likely than a coincidence: same supplier, same day,
    # same amount usually means the receipt got photographed twice.
    dupes = [r for r in rows if r["date"] == row["date"]
             and r["supplier"].lower() == row["supplier"].lower()
             and abs(r["total"] - row["total"]) < 0.005]
    rows.append(row)
    save(rows)

    print(json.dumps(row, indent=2, ensure_ascii=False))
    if dupes:
        print(f"\nFIGYELEM: {len(dupes)} korabbi tetel ugyanezzel a bolttal, nappal es "
              f"osszeggel (id: {', '.join(str(r['id']) for r in dupes)}). "
              f"Ket kulon vasarlas volt, vagy ketszer rogzitettuk?")
    if row["capital_flag"]:
        print(f"\nFIGYELEM: {row['total']:.2f} font szerszamra. A {CAPITAL_THRESHOLD:.0f} font "
              f"feletti eszkoz altalaban ertekcsokkenesi tetel (annual investment allowance), "
              f"nem egyosszegu koltseg. A konyvelo dontse el, de megjeloltem.")
    return 0


def _filter(rows, a):
    out = rows
    if getattr(a, "year", None):
        out = [r for r in out if r["tax_year"] == a.year]
    if getattr(a, "category", None):
        out = [r for r in out if r["category"] == a.category]
    if getattr(a, "job", None):
        out = [r for r in out if a.job.lower() in (r.get("job") or "").lower()]
    return sorted(out, key=lambda r: (r["date"], r["id"]))


def cmd_list(a):
    rows = _filter(load(), a)
    if not rows:
        print("Nincs talalat.")
        return 0
    print(f"{'ID':>4} {'DATUM':<11} {'BOLT':<22} {'OSSZEG':>9} {'KATEGORIA':<13} MUNKA")
    print("-" * 88)
    for r in rows:
        flag = " *" if r.get("capital_flag") else ""
        print(f"{r['id']:>4} {r['date']:<11} {r['supplier'][:21]:<22} "
              f"{r['total']:>9.2f} {r['category']:<13} {r.get('job','')}{flag}")
    print("-" * 88)
    print(f"{len(rows)} tetel, osszesen {sum(r['total'] for r in rows):.2f}")
    return 0


def cmd_summary(a):
    rows = _filter(load(), a)
    if not rows:
        print("Nincs adat.")
        return 0
    by = {}
    for r in rows:
        by.setdefault(r["category"], []).append(r)
    year = a.year or "minden ev"
    print(f"KOLTSEG OSSZESITO  ({year})")
    print("-" * 78)
    total = 0.0
    for cat in sorted(by, key=lambda c: -sum(r["total"] for r in by[c])):
        s = sum(r["total"] for r in by[cat])
        total += s
        label = CATEGORIES.get(cat, (cat, ""))[0]
        print(f"{label:<34} {s:>10.2f}   ({len(by[cat])} tetel)")
    print("-" * 78)
    print(f"{'OSSZESEN':<34} {total:>10.2f}")
    cap = [r for r in rows if r.get("capital_flag")]
    if cap:
        print()
        print(f"Ertekcsokkenesre jelolve ({len(cap)} tetel, {sum(r['total'] for r in cap):.2f}), "
              f"a konyvelo dontse el:")
        for r in cap:
            print(f"  {r['date']}  {r['supplier']}  {r['total']:.2f}  {r.get('notes','')}")
    missing = [r for r in rows if not r.get("image")]
    if missing:
        print()
        print(f"FIGYELEM: {len(missing)} tetelhez nincs elmentett bizonylat-kep. "
              f"A HMRC 5 evig kerheti oket.")
    return 0


def cmd_export(a):
    rows = _filter(load(), a)
    out = a.out or f"expenses-{(a.year or 'all').replace('/', '-')}.csv"
    cols = ["date", "supplier", "total", "vat", "category", "sa103f_box",
            "job", "method", "capital_flag", "notes", "image"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in cols}
            # A capital-flagged tool must not export under an expense box, or the
            # accountant reads the box and expenses a table saw in one year.
            if r.get("capital_flag"):
                row["sa103f_box"] = ("CAPITAL: annual investment allowance, "
                                     "not a same-year expense")
            else:
                row["sa103f_box"] = CATEGORIES.get(r["category"], ("", ""))[1]
            w.writerow(row)
    print(f"Kiirva: {out}  ({len(rows)} tetel, osszesen {sum(r['total'] for r in rows):.2f})")
    return 0


def cmd_categories(a):
    print(f"{'KULCS':<14}{'MAGYARUL':<34}SA103F BOX")
    print("-" * 100)
    for k in sorted(CATEGORIES):
        label, box = CATEGORIES[k]
        print(f"{k:<14}{label:<34}{box}")
    print()
    print(f"A {CAPITAL_THRESHOLD:.0f} font feletti szerszam es gep automatikusan "
          f"ertekcsokkenesre jelolodik.")
    return 0


def cmd_check(a):
    rows = load()
    if not rows:
        print("Ures a fokonyv.")
        return 0
    problems = []
    seen = {}
    for r in rows:
        key = (r["date"], r["supplier"].lower(), round(r["total"], 2))
        seen.setdefault(key, []).append(r["id"])
        if not r.get("image"):
            problems.append(f"id {r['id']}: nincs bizonylat-kep")
        if not r.get("supplier"):
            problems.append(f"id {r['id']}: nincs bolt megadva")
        if r["total"] <= 0:
            problems.append(f"id {r['id']}: nem pozitiv osszeg ({r['total']})")
        if r.get("vat") is not None and r["vat"] > r["total"]:
            problems.append(f"id {r['id']}: a VAT nagyobb mint a vegosszeg")
    for key, ids in seen.items():
        if len(ids) > 1:
            problems.append(f"lehetseges duplikatum: id {', '.join(map(str, ids))} "
                            f"({key[0]}, {key[1]}, {key[2]:.2f})")
    if not problems:
        print(f"{len(rows)} tetel, nem talaltam problemat.")
        return 0
    print(f"{len(rows)} tetel, {len(problems)} eszrevetel:")
    for p in problems:
        print(f"  {p}")
    return 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--date", required=True)
    p.add_argument("--supplier", required=True)
    p.add_argument("--total", required=True, type=float)
    p.add_argument("--category", required=True)
    p.add_argument("--vat", type=float)
    p.add_argument("--job")
    p.add_argument("--method")
    p.add_argument("--image")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_add)

    for name, fn in (("list", cmd_list), ("summary", cmd_summary), ("export", cmd_export)):
        p = sub.add_parser(name)
        p.add_argument("--year")
        p.add_argument("--category")
        p.add_argument("--job")
        if name == "export":
            p.add_argument("--out")
        p.set_defaults(fn=fn)

    sub.add_parser("categories").set_defaults(fn=cmd_categories)
    sub.add_parser("check").set_defaults(fn=cmd_check)

    a = ap.parse_args(argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
