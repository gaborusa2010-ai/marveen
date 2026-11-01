#!/usr/bin/env python3
"""Historical replay checks used by muhely-szamla-kor.test.sh (SPEC 6.1).

For every issued invoice with a saved source (10016-10023) the weekly run must
rebuild the same invoice from the work log: the JSON fields (criterion 1), the
total and CIS on the delivered PDF (2), and the delivered PDF's text line for
line once re-rendered with the issued reference and date (3).  A one-penny unit
change has to fail all three (4).  Named exceptions, not tolerances: 10018's
site note (pinned to today's wording) and 10019's dropped one-minute overtime.
"""
import glob
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

repo = sys.argv[1]
store = os.path.join(repo, "store")
program = os.path.join(repo, "scripts", "muhely", "szamla-kor.py")
invoice_dir = os.path.join(store, "muhely", "invoices")
# Headings the agent wrote by hand on the issued invoice; the rest must come
# from the heading template, so they are NOT handed to the run as overrides.
HAND_HEADINGS = {"10019", "10020", "10021"}


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


inv = module("replay_invoice", os.path.join(repo, "scripts", "muhely", "invoice.py"))
pdftext = module("replay_pdftext", os.path.join(repo, "scripts", "muhely", "pdftext.py"))
# Re-rendering an issued invoice must not record, mark, save or upload anything.
for name in ("record_issued", "mark_jobs_invoiced", "warn_if_unlinked", "save_source",
             "archive_to_drive"):
    setattr(inv, name, lambda *a, **k: None)
business = load(os.path.join(store, "muhely-business.json"))
issued_on = {str(x["reference"]): datetime.fromtimestamp(x["at"]).date().isoformat()
             for x in load(os.path.join(store, "muhely-invoice-log.json"))
             if x.get("kind") == "invoice" and x.get("at")}
scratch = tempfile.mkdtemp()


def fields(job):
    return [[x.get(k) for k in ("desc", "qty", "unit", "cis")] for x in job["lines"]]


def rendered_text(job, ref):
    job = dict(job, reference=ref, date=issued_on[ref])
    out = os.path.join(scratch, f"{ref}-{len(os.listdir(scratch))}.pdf")
    result = inv.render("invoice", job, business, out)
    return pdftext.extract(out).splitlines(), result


def site_note_cut(lines):
    # 10018: everything up to the site note must match; the note itself is
    # checked against today's wording separately.
    return lines[:lines.index("Notes")] if "Notes" in lines else lines


base_ledger = load(os.path.join(store, "muhely-jobs.json"))
failures = []
checked = 0
for ref in [str(x) for x in range(10016, 10024)]:
    matches = glob.glob(os.path.join(invoice_dir, ref + "-*.json"))
    if not matches:
        failures.append(f"{ref}: source hianyzik")
        continue
    issued_path = matches[0]
    issued = load(issued_path)
    ids = issued.get("_job_ids") or []
    if not ids:
        failures.append(f"{ref}: _job_ids hianyzik")
        continue
    ledger = json.loads(json.dumps(base_ledger))
    for row in ledger:
        if row.get("id") in ids:
            row["invoice_ref"] = ""
    with open(os.path.join(store, "muhely-jobs.json"), "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, ensure_ascii=False)
    run_file = os.path.join(repo, f"run-{ref}.toml")
    client = next(row["client"] for row in ledger if row.get("id") == ids[0])
    with open(run_file, "w", encoding="utf-8") as fh:
        fh.write("format_version = 1\n[[invoice]]\n")
        fh.write("client = " + json.dumps(client) + "\n")
        fh.write("cis = " + ("true" if issued.get("cis") else "false") + "\n")
        fh.write("days = [" + ", ".join(map(str, ids)) + "]\n")
        if ref in HAND_HEADINGS:
            fh.write("heading = " + json.dumps(issued.get("job", "")) + "\n")
    day = f"2026-12-{int(ref) - 10000:02d}"
    env = dict(os.environ, MUHELY_NO_DRIVE="1", MUHELY_NO_DESKTOP="1")
    p = subprocess.run([sys.executable, program, "draft", "--run", run_file, "--date", day],
                       text=True, capture_output=True, env=env)
    run_dir = os.path.join(store, "muhely", "szamlazas-program", "runs", day)
    manifest_path = os.path.join(run_dir, "run.json")
    # The run also drafts the work log's other open invoices (run.toml only
    # replaces the one it names), so the exit code is theirs too: judge the
    # replayed invoice by its own entry.
    if not os.path.exists(manifest_path):
        failures.append(f"{ref}: draft exit {p.returncode}, nincs run.json: {p.stderr.strip()}")
        continue
    entry = next((x for x in load(manifest_path)["invoices"] if x.get("days") == ids), None)
    if entry is None:
        failures.append(f"{ref}: a {ids} napok nincsenek a run.json-ban: {p.stderr.strip()}")
        continue
    if entry["status"] != "draft":
        failures.append(f"{ref}: {entry.get('error')}")
        continue
    got = load(entry["json_path"])

    # Criterion 1: the JSON fields.
    if got.get("client") != issued.get("client"):
        failures.append(f"{ref}: cimzett elter")
    if got.get("job") != issued.get("job"):
        failures.append(f"{ref}: fejlec elter: {got.get('job')!r} != {issued.get('job')!r}")
    if fields(got) != fields(issued):
        failures.append(f"{ref}: sorok elternek: {fields(got)} != {fields(issued)}")
    if ref == "10018":
        if "from 23 September to 24 September 2026." not in got.get("notes", ""):
            failures.append("10018: az uj helyszin-szoveg elter")
    elif got.get("notes") != issued.get("notes"):
        failures.append(f"{ref}: site note elter")
    if ref == "10019":
        message = open(os.path.join(run_dir, "uzenet.txt"), encoding="utf-8").read()
        if len(got["lines"]) != 1 or "1 perc tulora" not in message:
            failures.append("10019: a kis tulora nincs nev szerint kezelve")

    # Criteria 2 and 3: the delivered PDF.
    delivered = pdftext.extract(os.path.splitext(issued_path)[0] + ".pdf").splitlines()
    text, result = rendered_text(got, ref)
    if inv.money(result["total"]) not in delivered:
        failures.append(f"{ref}: vegosszeg {inv.money(result['total'])} nincs a kiadott PDF-en")
    if result["cis"] and inv.money(result["cis"]) not in " ".join(delivered):
        failures.append(f"{ref}: CIS {inv.money(result['cis'])} nincs a kiadott PDF-en")
    want = site_note_cut(delivered) if ref == "10018" else delivered
    if ref == "10020":
        # Issued 2026-10-04 with "CIS @ 20% (labour)"; the owner had the suffix
        # removed a week later (Telegram msg 913, see invoice.py's CIS row).
        if "CIS @ 20% (labour)" not in want:
            failures.append("10020: a regi CIS-felirat nincs a kiadott PDF-en")
        want = ["CIS @ 20%" if x == "CIS @ 20% (labour)" else x for x in want]
    have = site_note_cut(text) if ref == "10018" else text
    if have != want:
        diff = [f"{a!r} != {b!r}" for a, b in zip(have, want) if a != b][:3]
        failures.append(f"{ref}: PDF szoveg elter ({len(have)} vs {len(want)} sor): {diff}")

    # Criterion 4: a one-penny unit change fails 1 and 3, and moves 2.
    mutant = json.loads(json.dumps(got))
    mutant["lines"][0]["unit"] = float(mutant["lines"][0]["unit"]) + .01
    mutant_text, mutant_result = rendered_text(mutant, ref)
    if fields(mutant) == fields(issued):
        failures.append(f"{ref}: negativ kontroll (JSON) nem bukott el")
    if (site_note_cut(mutant_text) if ref == "10018" else mutant_text) == want:
        failures.append(f"{ref}: negativ kontroll (PDF szoveg) nem bukott el")
    if inv.money(mutant_result["total"]) in delivered:
        failures.append(f"{ref}: negativ kontroll (vegosszeg) nem bukott el")
    checked += 1

with open(os.path.join(store, "muhely-jobs.json"), "w", encoding="utf-8") as fh:
    json.dump(base_ledger, fh, indent=2, ensure_ascii=False)
if failures or checked != 8:
    print("\n".join(failures) or f"csak {checked} szamla ellenorizve a 8-bol")
    raise SystemExit(1)
print(f"{checked} szamla: JSON, vegosszeg/CIS es PDF-szoveg egyezik, negativ kontroll bukik")
