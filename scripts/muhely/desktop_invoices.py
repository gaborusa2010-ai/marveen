#!/usr/bin/env python3
"""Copy invoice PDFs to the owner's "Marveen files/Invoices" folder on his desktop.

The owner asked for it on 2026-09-20 (Telegram msg 617, Forgacs' channel): "es
csinaltam egy mappat a Marveen files-ba Invoices neven...mostantol oda is legyen
elmentve az osszes szamla amit csinaltal vagy csinalni fogsz". Note "oda IS": the
Drive copy stays, this is a SECOND home, not a move.

Why a second home at all, and why it is not redundant: Drive is where the
accountant and the tax year live; the desktop folder is where HE looks, on the
machine he actually sits at. A copy he has to go and fetch is a copy he will not
have when he wants it.

Same three properties as drive_invoices, for the same reasons:

  ONE name definition. The name comes from drive_invoices.drive_name(), so the
  desktop folder and the Drive folder hold the same filenames. Two places that
  build a name slightly differently do not produce a conflict -- they produce a
  quiet second copy of the same invoice under a second name.

  MATCH BY NUMBER, not by string. An invoice is identified by its number. The
  owner had already dropped 10016 in there by hand as "10016-palmer-furniture.pdf"
  before this code existed, and renaming or re-adding it would have given him the
  same invoice twice. Whatever name a number already has in the folder wins.

  STRUCTURAL guard on the source. Only files that live in store/muhely/invoices
  are copied. The invoice tests render real PDFs into a temp dir, and a gate that
  depends on the caller remembering an env var is the env var's gate, not the
  folder's.

Best effort by contract: the caller has already written the PDF and spent the
number, so a missing drive mount here must be reported and dropped, never raised.
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive_invoices as inv  # noqa: E402

INVOICE_DIR = inv.INVOICE_DIR

# The owner's Windows desktop as WSL sees it. Overridable so this is testable
# without writing to his real desktop.
DESKTOP_DIR = os.environ.get(
    "MUHELY_DESKTOP_INVOICES",
    "/mnt/c/Users/gabor/Desktop/Marveen files/Invoices")


def _ref(name):
    """The invoice number in a filename, whichever way it was written.

    Handles both shapes on purpose: ours ("2026-09-20 Szamla 10016 - Palmer
    Furniture.pdf") and the one the owner saves off Telegram by hand
    ("10016-palmer-furniture.pdf").
    """
    m = re.search(r"Szamla\s+(\d{4,})", name)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4,})\b", os.path.basename(name))
    return m.group(1) if m else None


def existing_by_ref(report=print):
    """{invoice number: filename} for what is ALREADY in the desktop folder."""
    out = {}
    if not os.path.isdir(DESKTOP_DIR):
        return out
    for f in os.listdir(DESKTOP_DIR):
        if not f.lower().endswith(".pdf"):
            continue
        r = _ref(f)
        if r:
            out[r] = f
    return out


def copy_one(pdf_path, report=print):
    """Put ONE invoice in the desktop folder. True if it is there afterwards."""
    if os.environ.get("MUHELY_NO_DESKTOP"):
        return False
    if os.path.dirname(os.path.abspath(pdf_path)) != INVOICE_DIR:
        if report:
            report(f"Desktop: kihagyva, mert nem a szamla-mappaban van: {pdf_path}")
        return False
    if not os.path.isdir(DESKTOP_DIR):
        if report:
            report(f"FIGYELEM: a szamla elkeszult, de a desktop-mappa nem erheto el "
                   f"({DESKTOP_DIR}). A Drive-masolat ettol fuggetlenul megvan.\n"
                   f"  Kezzel, ha a gep felallt: "
                   f"python3 scripts/muhely/desktop_invoices.py")
        return False
    target, how = inv.drive_name(pdf_path)
    ref = _ref(target)
    # The owner LOOKED at 10011's date mismatch on Drive and said keep it
    # (drive_invoices.ELFOGADOTT_NEVELTERES). Honour the same decision here, or
    # the two folders would hold the same invoice under two different dates --
    # which is exactly the one-name rule this module exists to keep.
    settled = inv.ELFOGADOTT_NEVELTERES.get(ref)
    if settled and settled != target:
        target, how = settled, "a gazda eldontott neve (Drive-val egyezoen)"
    old = existing_by_ref(report).get(ref)
    if old:
        # Already there under SOME name. Do not rename and do not add a second
        # copy: the number is what identifies it, and the name the owner chose
        # is his. Refresh the bytes only, and only if they differ.
        dest = os.path.join(DESKTOP_DIR, old)
        if os.path.getsize(dest) == os.path.getsize(pdf_path):
            if report:
                report(f"Desktop: mar ott van -> {old}")
            return True
        shutil.copyfile(pdf_path, dest)
        if report:
            report(f"Desktop: frissitve -> {old}  (a meglevo nevet tartottam meg)")
        return True
    dest = os.path.join(DESKTOP_DIR, target)
    shutil.copyfile(pdf_path, dest)
    if report:
        report(f"Desktop: masolva -> {target}  (datum forrasa: {how})")
    return True


def sync_all(report=print):
    """Every invoice on disk into the desktop folder. Re-runnable."""
    done = 0
    if not os.path.isdir(INVOICE_DIR):
        return done
    for f in sorted(os.listdir(INVOICE_DIR)):
        if f.lower().endswith(".pdf") and copy_one(os.path.join(INVOICE_DIR, f),
                                                   report):
            done += 1
    return done


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        # Without this the flag is taken as a filename and the tool answers a
        # question nobody asked ("not in the invoice folder"). A wrong answer
        # to --help is how a reader concludes the tool is broken.
        print(__doc__)
        print(f"  desktop_invoices.py                 # minden szamla oda\n"
              f"  desktop_invoices.py <szamla.pdf>    # egy darab\n"
              f"  cel: {DESKTOP_DIR}")
        sys.exit(0)
    if len(sys.argv) > 1:
        sys.exit(0 if copy_one(sys.argv[1]) else 1)
    n = sync_all()
    print(f"{n} szamla a desktop-mappaban: {DESKTOP_DIR}")
