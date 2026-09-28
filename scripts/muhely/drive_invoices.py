#!/usr/bin/env python3
"""Naming and upload of invoice PDFs to the owner's Drive "Szamlak" folder.

ONE definition of the Drive name, on purpose. drive_up.put() matches an existing
file by its EXACT name, so two places that build the name slightly differently do
not produce a conflict -- they produce a SECOND copy of the same invoice under a
second name, quietly. Both the sync script and invoice.py import from here.

Why this file exists at all: on 2026-09-17 the owner asked why his invoices were
not on his Drive. Measured: the Szamlak folder held 10011 and nothing else, while
10012, 10013, 10014 and 10015 sat on disk. Nothing was broken -- the sync script
simply carried a hand-written list that named one invoice, and issuing an invoice
uploaded nothing at all. A list that has to be edited by hand to stay true will
go stale, and nothing reports it.
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_MUHELY = os.path.normpath(os.path.join(HERE, "..", "..", "store", "muhely"))
INVOICE_DIR = os.path.join(STORE_MUHELY, "invoices")

ROOT_NAME = "Muhely - Fejes Istvan Gabor"
FOLDER_NAME = "Szamlak"


def invoice_date(pdf_path):
    """(YYYY-MM-DD, how) -- the date PRINTED ON the invoice, and where it came from.

    Three different dates live on one invoice and they do NOT agree:
      - the "Date" line on the page (what the client sees, what HMRC reads),
      - the generation timestamp in store/muhely-invoice-log.json,
      - the file mtime.
    Measured 2026-09-17: 10013 prints 06 Sep 2026 but was generated 09-07 07:22,
    and 10011 prints 25 Aug while its Drive copy is named 2026-08-24. The printed
    date is the only one worth putting in the name, and every fallback says out
    loud that it IS a fallback -- a guessed date that looks measured is the trap.
    """
    side = os.path.splitext(pdf_path)[0] + ".json"
    if os.path.exists(side):
        try:
            v = json.load(open(side)).get("date")
            if v:
                return v, "sidecar"
        except Exception:
            pass
    try:
        sys.path.insert(0, HERE)
        import pdftext
        m = re.search(r"Date\s+(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})",
                      pdftext.extract(pdf_path))
        if m:
            d = datetime.datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y")
            return d.strftime("%Y-%m-%d"), "pdf"
    except Exception:
        pass
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(pdf_path))
    return mt.strftime("%Y-%m-%d"), "mtime (NEM a nyomtatott datum)"


def client_label(stem):
    """10012-palmer-furniture -> Palmer Furniture (the owner's own naming)."""
    parts = stem.split("-", 1)
    slug = parts[1] if len(parts) > 1 else stem
    return " ".join(w.capitalize() for w in slug.split("-"))


def drive_name(pdf_path):
    """(name on Drive, how the date was obtained)."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    ref = stem.split("-", 1)[0]
    date, how = invoice_date(pdf_path)
    return f"{date} Szamla {ref} - {client_label(stem)}.pdf", how


def entries(report=print):
    """[(path relative to store/muhely, name on Drive)] for every invoice on disk."""
    out = []
    if not os.path.isdir(INVOICE_DIR):
        return out
    for f in sorted(os.listdir(INVOICE_DIR)):
        if not f.endswith(".pdf"):
            continue
        name, how = drive_name(os.path.join(INVOICE_DIR, f))
        if how.startswith("mtime") and report:
            report(f"    FIGYELEM {f}: a datum {how}")
        out.append((f"invoices/{f}", name))
    return out


# Nev-elteresek, amiket a gazda MEGNEZETT es ugy dontott, maradjanak. Enelkul a
# szinkron minden futasnal felhozna ugyanazt az eldontott kerdest: egy pontos,
# ismetlodo es MAR MEGVALASZOLT riasztas ugyanannyit er, mint a hallgatas, csak
# zajosabb. A sor NEM azt mondja, hogy a nev helyes -- azt, hogy o valasztotta.
ELFOGADOTT_NEVELTERES = {
    # 10011: fent "2026-08-24", a PDF-en "25 Aug 2026". A gazda dontese,
    # 2026-09-17 07:30 BST, Telegram msg 597: "Maradhat."
    "10011": "2026-08-24 Szamla 10011 - Picagne.pdf",
}


def ref_of(name):
    m = re.search(r"Szamla\s+(\d+)", name)
    return m.group(1) if m else None


def keep_existing_names(files, folder_id, report=print):
    """Reuse the name an invoice ALREADY has on Drive, so put() updates it.

    An invoice is identified by its NUMBER, not by the name string we happen to
    build today. Without this, changing the naming rule silently doubles the
    folder instead of renaming anything -- and a Szamlak folder holding 10011
    twice under two dates is worse than one wrong date.
    """
    import drive_up as d
    try:
        r = d.g.call(d.API, params={"q": f"'{folder_id}' in parents and trashed = false",
                                    "fields": "files(id,name)", "pageSize": 200})
        by_ref = {ref_of(f["name"]): f["name"] for f in r.get("files", [])
                  if ref_of(f["name"])}
    except Exception as e:
        if report:
            report(f"    FIGYELEM: a Szamlak mappat nem tudtam listazni ({e}); "
                   f"nev szerinti egyeztetes marad")
        return files
    out = []
    for rel, target in files:
        ref = ref_of(target)
        old = by_ref.get(ref)
        if old and old != target and ELFOGADOTT_NEVELTERES.get(ref) == old:
            out.append((rel, old))          # eldontott, nem kerdes tobbe
            continue
        if old and old != target:
            if report:
                report(f"    NEVELTERES {ref_of(target)}: fent '{old}', a nyomtatott "
                       f"datum szerint '{target}'. A MEGLEVO nevet tartom meg "
                       f"(nem duplikalok); az atnevezes a gazda dontese.")
            target = old
        out.append((rel, target))
    return out


def upload_one(pdf_path, report=print):
    """Put ONE invoice on Drive. Returns True on success.

    Best effort by contract: the caller has already written the PDF and spent the
    number, so a network failure here must not fail the invoice. It is reported
    and dropped, and the next drive-sync-muhely.py run picks the file up anyway --
    that second path is why this one is allowed to be soft.
    """
    if os.environ.get("MUHELY_NO_DRIVE"):
        return False
    # STRUCTURAL guard, not an env var anyone has to remember. The invoice tests
    # render real PDFs into a mktemp dir (store/__tests__/... -> $TMP/a.pdf), and
    # without this a test run would push "a.pdf" into the owner's Szamlak folder
    # under a made-up number. A gate that depends on the caller setting a flag is
    # the flag's gate, not the folder's.
    if os.path.dirname(os.path.abspath(pdf_path)) != INVOICE_DIR:
        if report:
            report(f"Drive: kihagyva, mert nem a szamla-mappaban van: {pdf_path}")
        return False
    sys.path.insert(0, HERE)
    try:
        import drive_up as d
        root, _ = d.mkdir(ROOT_NAME)
        fid, _ = d.mkdir(FOLDER_NAME, root)
        name, how = drive_name(pdf_path)
        name = keep_existing_names([(pdf_path, name)], fid, report)[0][1]
        _, created = d.put(pdf_path, fid, name)
        if report:
            report(f"Drive: {'feltoltve' if created else 'frissitve'} -> "
                   f"{FOLDER_NAME}/{name}  (datum forrasa: {how})")
        return True
    except Exception as e:
        if report:
            report(f"FIGYELEM: a szamla elkeszult, de a Drive-ra NEM sikerult "
                   f"feltolteni: {e}\n"
                   f"  Kezzel: python3 scripts/muhely/drive-sync-muhely.py")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(0 if upload_one(sys.argv[1]) else 1)
    for rel, name in entries():
        print(f"{rel}  ->  {name}")
