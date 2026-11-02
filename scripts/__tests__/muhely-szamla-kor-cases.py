#!/usr/bin/env python3
"""Bad-input table and approval path of szamla-kor.py (SPEC 6.2 and section 4).

Usage: muhely-szamla-kor-cases.py <worktree> <store-src> <tmpdir>
<store-src> is normally the synthetic fixture written by muhely-szamla-kor-fixture.py
(#758); the cases pick candidates()[0] and candidates()[-1], see the fixture
for the ledger order they rely on.
Every case gets its own sandbox copy of the scripts and of the store files the
run reads; the store source itself is only read.  Prints one ok/FAIL line per
case and exits 1 if any case failed.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

WORKTREE, STORE_SRC, TMP = sys.argv[1:4]
failed = []


def ok(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        failed.append(name)


def sandbox(name):
    root = os.path.join(TMP, "case-" + name)
    shutil.copytree(os.path.join(WORKTREE, "scripts", "muhely"), os.path.join(root, "scripts", "muhely"))
    os.makedirs(os.path.join(root, "store", "muhely"))
    for path in glob.glob(os.path.join(STORE_SRC, "muhely-*.json")):
        shutil.copy2(path, os.path.join(root, "store"))
    shutil.copytree(os.path.join(STORE_SRC, "muhely", "invoices"),
                    os.path.join(root, "store", "muhely", "invoices"))
    return root


def read(root, name):
    with open(os.path.join(root, "store", name), encoding="utf-8") as fh:
        return json.load(fh)


def write(root, name, data):
    with open(os.path.join(root, "store", name), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def run(root, *args, env=None):
    e = dict(os.environ, MUHELY_NO_DRIVE="1", MUHELY_NO_DESKTOP="1")
    e.update(env or {})
    for key, value in list(e.items()):
        if value is None:
            del e[key]
    p = subprocess.run([sys.executable, os.path.join(root, "scripts", "muhely", "szamla-kor.py"), *args],
                       text=True, capture_output=True, env=e, cwd=root)
    return p.returncode, p.stdout, p.stderr


def run_toml(root, client, days, cis=False):
    path = os.path.join(root, "run.toml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"format_version = 1\n[[invoice]]\nclient = {json.dumps(client)}\n"
                 f"cis = {'true' if cis else 'false'}\ndays = {json.dumps(days)}\n")
    return path


def candidates(ledger):
    return [r for r in ledger if r.get("stopped_at") is not None
            and not r.get("invoice_ref") and not r.get("no_charge")]


def rundir(root, day):
    return os.path.join(root, "store", "muhely", "szamlazas-program", "runs", day)


# --- empty ledger: nothing to invoice ----------------------------------------
root = sandbox("empty")
write(root, "muhely-jobs.json", [])
# Every day of the missing-days window acknowledged: only then is "nothing to
# invoice" the whole answer (an unacknowledged empty week is asked, see below).
write(root, "muhely-naplo-hiany-nyugtazva.json",
      {time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400)): {"ok": "test"} for i in range(16)})
rc, out, err = run(root, "draft", "--date", "2026-11-01")
ok("ures naplo: exit 0, nincs mit szamlazni", rc == 0 and "Nincs szamlazando nap" in out
   and not os.path.isdir(rundir(root, "2026-11-01")), f"rc={rc} {out}{err}")

# --- open day: never offered, listed; named in run.toml -> 6 ------------------
root = sandbox("open")
ledger = read(root, "muhely-jobs.json")
day = candidates(ledger)[-1]
day["stopped_at"] = None
write(root, "muhely-jobs.json", ledger)
rc, out, err = run(root, "draft", "--dry-run", "--date", "2026-11-01")
ok("nyitott nap: nincs a piszkozatban, listazva", f"#{day['id']}" in err and "Nyitott nap" in err,
   f"rc={rc} {err}")
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, day["client"], [day["id"]]))
ok("nyitott nap a run.toml-ban: exit 6", rc == 6 and "meg nyitva" in err, f"rc={rc} {err}")

# --- run.toml naming another client's day -> 6 --------------------------------
root = sandbox("other-client")
ledger = read(root, "muhely-jobs.json")
day = candidates(ledger)[-1]
other = next(c for c in read(root, "muhely-ugyfelek.json")["ugyfelek"] if c != day["client"])
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, other, [day["id"]]))
ok("mas ugyfel napja a run.toml-ban: exit 6", rc == 6 and "mas ugyfele" in err, f"rc={rc} {err}")

# --- client missing from the client book -> 4, named --------------------------
root = sandbox("no-client")
clients = read(root, "muhely-ugyfelek.json")
gone = candidates(read(root, "muhely-jobs.json"))[0]["client"]
del clients["ugyfelek"][gone]
write(root, "muhely-ugyfelek.json", clients)
rc, out, err = run(root, "draft", "--dry-run")
ok("hianyzo ugyfel: exit 4, a nevevel", rc == 4 and gone in err, f"rc={rc} {err}")

# --- vat_number filled in -> stop, not a silent 20% ---------------------------
root = sandbox("vat")
business = read(root, "muhely-business.json")
business["vat_number"] = "GB123456789"
write(root, "muhely-business.json", business)
rc, out, err = run(root, "draft", "--dry-run")
ok("kitoltott vat_number: megall (6)", rc == 6 and "VAT" in err and "Piszkozat" not in out,
   f"rc={rc} {out}{err}")

# --- 5000 closed days: one invoice per client, over 31 refused -----------------
root = sandbox("big")
ledger = read(root, "muhely-jobs.json")
clients = read(root, "muhely-ugyfelek.json")
template = dict(candidates(ledger)[0], invoice_ref=None, cis=False, no_charge=None, notes="")
start = template["started_at"] - 400 * 86400
rows, next_id = [], max(r["id"] for r in ledger) + 1
for c in range(161):
    name = f"Teszt Ugyfel {c:03d}"
    clients["ugyfelek"][name] = {"name": name, "address": ["1 Test Street", "London"], "_forras": "teszt"}
    for d in range(32 if c == 0 else 20):
        stamp = start + (len(rows)) * 3600
        rows.append(dict(template, id=next_id, client=name, started_at=stamp, stopped_at=stamp + 9 * 3600,
                         job="Furniture fitting"))
        next_id += 1
write(root, "muhely-jobs.json", [r for r in ledger if r.get("invoice_ref")] + rows)
write(root, "muhely-ugyfelek.json", clients)
began = time.time()
rc, out, err = run(root, "draft", "--dry-run")
drafts = out.count("Piszkozat:")
ok(f"3232 nap, 161 ugyfel: 160 piszkozat, a 32 napos visszautasitva ({time.time() - began:.0f} s)",
   rc == 6 and drafts == 160 and "Teszt Ugyfel 000: Tul sok nap egy szamlan: 32" in err,
   f"rc={rc} drafts={drafts} {err[-300:]}")

# --- accents: the table lookup normalises, the PDF keeps the English -----------
root = sandbox("accent")
ledger = read(root, "muhely-jobs.json")
day = candidates(ledger)[-1]
day["job"] = "Műhelymunka"
write(root, "muhely-jobs.json", ledger)
rc, out, err = run(root, "draft", "--date", "2026-11-01",
                   "--run", run_toml(root, day["client"], [day["id"]]))
manifest = json.load(open(os.path.join(rundir(root, "2026-11-01"), "run.json"), encoding="utf-8"))
# The overridden invoice is appended after the other clients' defaults, so
# it is not invoices[0] once the store has more than one client.
entry = next(x for x in manifest["invoices"] if x["client"] == day["client"] and not x["key"].endswith("--cis"))
desc = json.load(open(entry["json_path"], encoding="utf-8"))["lines"][0]["desc"] if entry.get("json_path") else ""
pdf = subprocess.run([sys.executable, os.path.join(root, "scripts", "muhely", "pdftext.py"), entry.get("pdf_path") or "/nonexistent"],
                     text=True, capture_output=True).stdout
ok("ekezetes naplo-szoveg: forditva, a PDF angol", rc in (1, 5) and desc.endswith("  Workshop work")
   and "Workshop work" in pdf and "Műhely" not in pdf and "DRAFT" in pdf, f"rc={rc} {desc!r} {err}")

# --- unknown Hungarian text -> 3, listed; a long English line -> 3 -------------
root = sandbox("unknown")
ledger = read(root, "muhely-jobs.json")
day = candidates(ledger)[-1]
day["job"] = "Konyhaszekrény szerelés"
write(root, "muhely-jobs.json", ledger)
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, day["client"], [day["id"]]))
ok("ismeretlen magyar szoveg: exit 3, a szoveggel", rc == 3 and "Konyhaszekrény szerelés" in err,
   f"rc={rc} {err}")
long_text = "Fitting of the kitchen wall units and the tall larder units on site"
day["job"] = long_text
write(root, "muhely-jobs.json", ledger)
# Allowed as English, so only its length can stop it (not the unknown-text check).
texts_path = os.path.join(root, "scripts", "muhely", "szamla-kor-config", "szovegek.toml")
texts = open(texts_path, encoding="utf-8").read()
with open(texts_path, "w", encoding="utf-8") as fh:
    fh.write(texts.replace("angol = [\n", f"angol = [\n  {json.dumps(long_text)},\n", 1))
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, day["client"], [day["id"]]))
ok("64-nel hosszabb sor: exit 3, a sorral, nem vagva", rc == 3 and long_text in err
   and "64 karakternel" in err, f"rc={rc} {err}")

# --- unaccented Hungarian not in the table stops (review 3110, 2.) ------------
root = sandbox("plain-hungarian")
ledger = read(root, "muhely-jobs.json")
day = candidates(ledger)[0]
day["job"] = "Ajtok beszerelese"
write(root, "muhely-jobs.json", ledger)
rc, out, err = run(root, "draft", "--dry-run")
ok("ekezet nelkuli magyar, nincs a tablaban: exit 3", rc == 3 and "Ajtok beszerelese" in err
   and f"Piszkozat: {day['client']}," not in out, f"rc={rc} {out}{err}")

# --- one client, CIS and non-CIS days: two drafts, two files (review 3110, 1.) --
root = sandbox("mixed-cis")
ledger = read(root, "muhely-jobs.json")
client = candidates(ledger)[-1]["client"]
mine = [r for r in candidates(ledger) if r["client"] == client]
for row in mine[-2:]:
    row["cis"] = True
write(root, "muhely-jobs.json", ledger)
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entries = [x for x in json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))["invoices"]
           if x["client"] == client]
paths = {x.get("json_path") for x in entries}
message = open(os.path.join(rd, "uzenet.txt"), encoding="utf-8").read()
ok("kevert CIS: ket piszkozat, ket fajl, CIS-nel a fizetendo is",
   len(entries) == 2 and len(paths) == 2 and all(x["status"] == "draft" for x in entries)
   and all(os.path.exists(x) for x in paths) and "fizetendo" in message, f"rc={rc} {entries} {err}")
rc, out, err = run(root, "approve", rd, client)
ok("kevert CIS: a puszta ugyfelnev ketertelmu, exit 6", rc == 6 and "tobb piszkozat" in err, f"rc={rc} {err}")
cis_entry = next(x for x in entries if x["key"].endswith("-cis"))
rc, out, err = run(root, "approve", rd, cis_entry["key"])
issued = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["key"] == cis_entry["key"])
readback = open(os.path.join(rd, "visszaolvasas.txt"), encoding="utf-8").read()
ok("kevert CIS: a CIS-piszkozat kulccsal kiallithato, RENDBEN",
   rc == 0 and issued["status"] == "issued" and readback == "RENDBEN\n", f"rc={rc} {readback!r} {err}")

# --- business file changed between draft and tap (review 3110, 3.) -------------
for name, change in (("vat-late", {"vat_number": "GB123456789"}), ("cis-rate-late", {"cis_rate": 30})):
    root = sandbox(name)
    ledger = read(root, "muhely-jobs.json")
    for row in candidates(ledger):
        row["cis"] = True
    write(root, "muhely-jobs.json", ledger)
    rc, out, err = run(root, "draft", "--date", "2026-11-01")
    rd = rundir(root, "2026-11-01")
    entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
    business = read(root, "muhely-business.json")
    business.update(change)
    write(root, "muhely-business.json", business)
    counter = read(root, "muhely-counters.json")
    rc, out, err = run(root, "approve", rd, entry["key"])
    ok(f"{name}: jovahagyas exit 6, nem fogy sorszam",
       rc == 6 and read(root, "muhely-counters.json") == counter
       and not glob.glob(os.path.join(root, "store", "muhely", "invoices", f"{counter[next(iter(counter))] + 1}-*")),
       f"rc={rc} {err}")

# --- nothing to invoice but an unlogged week: asked, not silent (review 3110, 7.)
root = sandbox("unlogged-week")
ledger = read(root, "muhely-jobs.json")
cutoff = time.time() - 15 * 86400
write(root, "muhely-jobs.json", [r for r in ledger if r.get("invoice_ref") and (r.get("started_at") or 0) < cutoff])
seen = os.path.join(root, "store", "muhely-naplo-hiany-nyugtazva.json")
if os.path.exists(seen):
    os.remove(seen)
rc, out, err = run(root, "draft", "--date", "2026-11-01")
message = open(os.path.join(rundir(root, "2026-11-01"), "uzenet.txt"), encoding="utf-8").read() \
    if os.path.isdir(rundir(root, "2026-11-01")) else ""
ok("nincs szamlazando nap, de naplozatlan a het: exit 5 es kerdes", rc == 5 and message.startswith("Kerdes:"),
   f"rc={rc} {out}{err}")

# --- run.toml replaces only the invoice it names (review 3110, 8.) ---------------
root = sandbox("partial-run")
ledger = read(root, "muhely-jobs.json")
cands = candidates(ledger)
named = cands[-1]
others = sorted({r["client"] for r in cands if r["client"] != named["client"]})
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, named["client"], [named["id"]]))
ok("run.toml csak a nevezett szamlat irja felul, a tobbi marad",
   bool(others) and all(f"Piszkozat: {c}," in out for c in others) and "felulirva" in out
   and f"Piszkozat: {named['client']}, 1 nap" in out, f"rc={rc} {out}{err}")

# --- missing days: asked together with the invoice, drafts still made ----------
root = sandbox("missing")
ledger = read(root, "muhely-jobs.json")
keep = candidates(ledger)
cutoff = time.time() - 15 * 86400
write(root, "muhely-jobs.json", [r for r in ledger if r in keep or (r.get("started_at") or 0) < cutoff])
seen = os.path.join(root, "store", "muhely-naplo-hiany-nyugtazva.json")
if os.path.exists(seen):
    os.remove(seen)
rc, out, err = run(root, "draft", "--date", "2026-11-01")
message = open(os.path.join(rundir(root, "2026-11-01"), "uzenet.txt"), encoding="utf-8").read()
made = [x for x in json.load(open(os.path.join(rundir(root, "2026-11-01"), "run.json")))["invoices"]
        if x["status"] == "draft" and os.path.exists(x["pdf_path"])]
ok("hianyzo munkanap: exit 5, kerdes az uzenetben, piszkozat kesz",
   rc == 5 and "Kerdes" in message and len(made) >= 1 and "#" not in message, f"rc={rc} {message}{err}")
paragraphs = message.strip().split("\n\n")
asked = [x for x in message.splitlines() if "aznap?" in x]
ok("hianyzo munkanap: a kerdes egyszer, kulon bekezdesben (#734)",
   len(made) >= 2 and message.count("Kerdes:") == 1
   and len(asked) == len(set(asked)) > 0 and len(paragraphs) == len(made) + 1 and paragraphs[-1].startswith("Kerdes:")
   and all("Kerdes" not in p for p in paragraphs[:-1]), f"{message!r}")

# --- approval: number spent once, days marked, read-back, no second time -------
root = sandbox("approve")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
manifest = json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))
entry = next(x for x in manifest["invoices"] if x["status"] == "draft")
counter_before = read(root, "muhely-counters.json")
log_before = len(read(root, "muhely-invoice-log.json"))
rc, out, err = run(root, "approve", rd, entry["key"])
counter_after = read(root, "muhely-counters.json")
ref = str(json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))["invoices"][
    manifest["invoices"].index(entry)].get("reference"))
marked = [r for r in read(root, "muhely-jobs.json") if r["id"] in entry["days"]]
readback = open(os.path.join(rd, "visszaolvasas.txt"), encoding="utf-8").read()
key = next(iter(counter_before))
ok("jovahagyas: exit 0, egy sorszam, napok jelolve, visszaolvasas RENDBEN",
   rc == 0 and counter_after[key] == counter_before[key] + 1 and ref == str(counter_after[key])
   and all(str(r.get("invoice_ref")) == ref for r in marked) and readback == "RENDBEN\n"
   and len(read(root, "muhely-invoice-log.json")) == log_before + 1
   and glob.glob(os.path.join(root, "store", "muhely", "invoices", f"{ref}-*.pdf")),
   f"rc={rc} ref={ref} {counter_before}->{counter_after} {readback!r} {err}")
rc, out, err = run(root, "approve", rd, entry["key"])
ok("masodik jovahagyas: exit 6, nincs uj sorszam",
   rc == 6 and read(root, "muhely-counters.json") == counter_after, f"rc={rc} {err}")
current = json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))
current["invoices"].append({"client": "Megallitott Ugyfel", "status": "stopped", "code": 3,
                            "json_path": None, "json_sha256": None})
with open(os.path.join(rd, "run.json"), "w", encoding="utf-8") as fh:
    json.dump(current, fh)
rc, out, err = run(root, "approve", rd, "Megallitott Ugyfel")
ok("megallitott tetel jovahagyasa: exit 6, nem traceback", rc == 6 and "Traceback" not in err,
   f"rc={rc} {err}")

# --- counter moved by someone else between draft and approval ------------------
root = sandbox("counter")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
counter = read(root, "muhely-counters.json")
key = next(iter(counter))
counter[key] += 5
write(root, "muhely-counters.json", counter)
rc, out, err = run(root, "approve", rd, entry["key"])
issued = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["client"] == entry["client"])
ok("szamlalo kozben odebb lepett: a kovetkezo szabad szamot kapja",
   rc == 0 and str(issued.get("reference")) == str(counter[key] + 1), f"rc={rc} {issued} {err}")

# --- Drive unreachable: the invoice stands, the run says so --------------------
root = sandbox("drive")
with open(os.path.join(root, "scripts", "muhely", "drive_up.py"), "w", encoding="utf-8") as fh:
    fh.write("def mkdir(name, parent=None):\n"
             "    raise OSError('[Errno -2] Name or service not known: www.googleapis.invalid')\n")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
rc, out, err = run(root, "approve", rd, entry["key"], env={"MUHELY_NO_DRIVE": None})
issued = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["client"] == entry["client"])
ok("Drive elerhetetlen: a szamla all, a futas jelzi",
   rc == 0 and "Drive masolat nem ment" in out and issued.get("drive") == "Drive masolat nem ment"
   and os.path.exists(issued.get("final_pdf", "")) and "NEM sikerult" in err, f"rc={rc} {out}{err}")

# --- read-back mismatch -> 7 ----------------------------------------------------
root = sandbox("readback")
with open(os.path.join(root, "scripts", "muhely", "pdftext.py"), "a", encoding="utf-8") as fh:
    fh.write("\n_real_extract = extract\n"
             "def extract(path):\n"
             "    return _real_extract(path).replace('200.00', '200.10')\n")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
rc, out, err = run(root, "approve", rd, entry["key"])
readback = open(os.path.join(rd, "visszaolvasas.txt"), encoding="utf-8").read()
ok("visszaolvasasi elteres: exit 7, ELTERES a riportban", rc == 7 and readback.startswith("ELTERES"),
   f"rc={rc} {readback!r} {err}")

# --- read-back: only the Total row differs -> 7 (review 3110, 4.) --------------
root = sandbox("readback-total")
with open(os.path.join(root, "scripts", "muhely", "pdftext.py"), "a", encoding="utf-8") as fh:
    fh.write("\n_real_extract = extract\n"
             "def extract(path):\n"
             "    rows = _real_extract(path).split('\\n')\n"
             "    i = rows.index('Total')\n"
             "    rows[i + 1] += '9'\n"
             "    return '\\n'.join(rows)\n")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
rc, out, err = run(root, "approve", rd, entry["key"])
readback = open(os.path.join(rd, "visszaolvasas.txt"), encoding="utf-8").read()
ok("visszaolvasas: csak a Total sor ter el, exit 7", rc == 7 and readback.startswith("ELTERES")
   and "Total" in readback, f"rc={rc} {readback!r} {err}")

# --- #734: one page only ------------------------------------------------------
def client_days(root, name, count, cis=False):
    """A client of its own with `count` closed, unbilled days; returns their ids."""
    ledger = read(root, "muhely-jobs.json")
    clients = read(root, "muhely-ugyfelek.json")
    clients["ugyfelek"][name] = {"name": name, "address": ["1 Test Street", "London"], "_forras": "teszt"}
    template = dict(candidates(ledger)[0], invoice_ref=None, cis=cis, no_charge=None, notes="")
    start = template["started_at"] - 200 * 86400
    next_id = max(r["id"] for r in ledger) + 1
    ids = []
    for d in range(count):
        stamp = start + d * 86400
        ledger.append(dict(template, id=next_id + d, client=name, started_at=stamp,
                           stopped_at=stamp + 9 * 3600, job="Furniture fitting"))
        ids.append(next_id + d)
    write(root, "muhely-jobs.json", ledger)
    write(root, "muhely-ugyfelek.json", clients)
    return ids


def counter(root):
    return read(root, "muhely-counters.json")


root = sandbox("page")
ids = client_days(root, "Oldal Teszt", 32)
fits = None
for n in range(20, 32):
    rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, "Oldal Teszt", ids[:n]))
    if rc == 6 and "nem fer egy oldalra" in err:
        break
    fits = n if rc in (1, 5) else fits
ok("egy oldal: van legnagyobb befero sorszam, felette exit 6 dry-runban is",
   fits is not None and n == fits + 1 and f"nem fer egy oldalra: {n} sor, ebbol {fits} fer ki" in err, f"fits={fits} n={n} rc={rc} {err}")
before = counter(root)
rc, out, err = run(root, "draft", "--date", "2026-11-01", "--run", run_toml(root, "Oldal Teszt", ids[:fits + 1]))
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["client"] == "Oldal Teszt")
ok("egy oldal: a tul hosszu szamla piszkozata megall, nincs fajl, nem fogy sorszam",
   rc == 6 and entry["status"] == "stopped" and not entry.get("json_path") and counter(root) == before,
   f"rc={rc} {entry} {err}")
shutil.rmtree(rd)
rc, out, err = run(root, "draft", "--date", "2026-11-01", "--run", run_toml(root, "Oldal Teszt", ids[:fits]))
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["client"] == "Oldal Teszt")
rc, out, err = run(root, "approve", rd, entry["key"])
readback = open(os.path.join(rd, "visszaolvasas.txt"), encoding="utf-8").read()
ok(f"egy oldal: a meg befero {fits} soros szamla kiallithato, visszaolvasas RENDBEN",
   rc == 0 and readback == "RENDBEN\n", f"rc={rc} {readback!r} {err}")
job = json.load(open(entry["json_path"], encoding="utf-8"))
job["lines"].append(dict(job["lines"][-1]))
job.pop("reference", None)
job_path = os.path.join(root, "long-job.json")
write_path = open(job_path, "w", encoding="utf-8")
json.dump(job, write_path, ensure_ascii=False)
write_path.close()
before = counter(root)
pdfs = set(os.listdir(os.path.join(root, "store", "muhely", "invoices")))
p = subprocess.run([sys.executable, os.path.join(root, "scripts", "muhely", "invoice.py"), "invoice", job_path,
                    "--force"], text=True, capture_output=True, cwd=root,
                   env=dict(os.environ, MUHELY_NO_DRIVE="1", MUHELY_NO_DESKTOP="1"))
ok("invoice.py: tul hosszu szamla exit 2, nincs PDF, nem fogy sorszam",
   p.returncode == 2 and "nem fer egy oldalra" in p.stderr and counter(root) == before
   and set(os.listdir(os.path.join(root, "store", "muhely", "invoices"))) == pdfs,
   f"rc={p.returncode} {p.stdout[-200:]} {p.stderr[-300:]}")

# --- #734: run.toml CIS flag differs from the day's ------------------------------
root = sandbox("cis-flag")
ids = client_days(root, "Cis Teszt", 2, cis=True)
rc, out, err = run(root, "draft", "--dry-run", "--run", run_toml(root, "Cis Teszt", ids, cis=False))
ok("run.toml cis=false CIS-napokkal: exit 6, nincs masodik piszkozat",
   rc == 6 and "CIS-jelzoje elter" in err and "Piszkozat: Cis Teszt," not in out, f"rc={rc} {out}{err}")

# --- #734: a client named "X CIS" next to X's CIS draft ---------------------------
root = sandbox("key-collision")
client_days(root, "Kulcs Teszt", 1, cis=True)
client_days(root, "Kulcs Teszt CIS", 1)
rc, out, err = run(root, "draft", "--date", "2026-11-01")
entries = [x for x in json.load(open(os.path.join(rundir(root, "2026-11-01"), "run.json")))["invoices"]
           if x["client"].startswith("Kulcs Teszt")]
ok("kulcs-utkozes: 'Kulcs Teszt CIS' es 'Kulcs Teszt' CIS-piszkozata kulon kulcs, kulon fajl",
   len(entries) == 2 and len({x["key"] for x in entries}) == 2 and len({x["json_path"] for x in entries}) == 2
   and all(x["status"] == "draft" for x in entries), f"rc={rc} {entries} {err}")

# --- #734: business file changed (not money) between draft and tap ---------------
root = sandbox("business-late")
rc, out, err = run(root, "draft", "--date", "2026-11-01")
rd = rundir(root, "2026-11-01")
entry = next(x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"] if x["status"] == "draft")
business = read(root, "muhely-business.json")
business["payment_terms_days"] = int(business.get("payment_terms_days") or 14) + 1
write(root, "muhely-business.json", business)
rc, out, err = run(root, "approve", rd, entry["key"])
ok("business.json valtozott (nem penz): kiallitva, de jelezve",
   rc == 0 and "muhely-business.json valtozott" in err, f"rc={rc} {err}")

sys.exit(1 if failed else 0)
