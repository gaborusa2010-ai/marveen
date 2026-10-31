#!/usr/bin/env python3
"""Heti szamlazasi kor: piszkozat, tulajdonosi jovahagyas, visszaolvasas.

    szamla-kor.py draft [--run run.toml] [--date EEEE-HH-NN] [--dry-run]
    szamla-kor.py approve <runs/datum mappa> <ugyfel>

Spec: store/muhely/szamlazas-program/SPEC.md (card #656). Draft writes
runs/<date>/: <client>.json, a DRAFT PDF, uzenet.txt and run.json; it spends no
number and marks nothing. Approve issues through invoice.py and reads it back.

Exit: 0 nothing to invoice / issued, 1 drafts waiting for approval, 2 bad input,
3 line text not in szovegek.toml or over 64 characters, 4 client address
missing, 5 missing work days (drafts still made), 6 a guard refused,
7 read-back of the final PDF does not match.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
PROGRAM = os.path.join(STORE, "muhely", "szamlazas-program")
CONFIG = os.path.join(PROGRAM, "config")
SEEDS = os.path.join(HERE, "szamla-kor-config")
LEDGER = os.path.join(STORE, "muhely-jobs.json")
CLIENTS = os.path.join(STORE, "muhely-ugyfelek.json")
BUSINESS = os.path.join(STORE, "muhely-business.json")
SEEN = os.path.join(STORE, "muhely-naplo-hiany-nyugtazva.json")
VERSION = 1


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


jobs = module("muhely_jobs", "jobs.py")
invoice_program = module("muhely_invoice", "invoice.py")
pdftext = module("muhely_pdftext", "pdftext.py")


@dataclass
class Stop:
    code: int
    text: str


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def norm(text):
    text = unicodedata.normalize("NFKD", str(text))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        pos = f" position {exc.pos}" if isinstance(exc, json.JSONDecodeError) else ""
        raise ValueError(f"{path}{pos}: {exc}") from exc


def load_toml(path, format_version=False):
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if format_version and data.get("format_version") != 1:
        raise ValueError(f"{path}: ismeretlen format_version: {data.get('format_version')}")
    return data


def ensure_configs(dry_run=False):
    made = []
    for name in ("szamla.toml", "szovegek.toml"):
        target = os.path.join(CONFIG, name)
        if not os.path.exists(target):
            if dry_run:
                source = os.path.join(SEEDS, name)
            else:
                os.makedirs(CONFIG, exist_ok=True)
                shutil.copyfile(os.path.join(SEEDS, name), target)
                print(f"Letrehoztam a hianyzo konfiguraciot: {target}")
                source = target
            made.append((target, source))
    return made


def config_path(name, made):
    path = os.path.join(CONFIG, name)
    for target, source in made:
        if target == path:
            return source
    return path


def collect(run, item):
    wanted = item.get("days")
    rows = run["ledger"]
    if wanted is not None:
        by_id = {r.get("id"): r for r in rows}
        bad = []
        selected = []
        for jid in wanted:
            row = by_id.get(jid)
            if not row:
                bad.append(f"#{jid} nem letezik")
            elif row.get("client") != item["client"]:
                bad.append(f"#{jid} mas ugyfele")
            elif row.get("stopped_at") is None:
                bad.append(f"#{jid} meg nyitva van")
            elif row.get("invoice_ref"):
                bad.append(f"#{jid} mar szamlazva van")
            elif row.get("no_charge"):
                bad.append(f"#{jid} nem szamlazhato")
            else:
                selected.append(row)
        if bad:
            return Stop(6, "; ".join(bad))
    else:
        selected = [r for r in rows if r.get("client") == item["client"]
                    and r.get("stopped_at") is not None and not r.get("invoice_ref")
                    and not r.get("no_charge") and bool(r.get("cis")) == item["cis"]]
    if not selected:
        return Stop(6, "nincs szamlazhato nap")
    # Before the builder: 5000 days must not cost 5000 day-builds to refuse.
    if len(selected) > int(run["config"].get("max_napok", 31)):
        return Stop(6, f"Tul sok nap egy szamlan: {len(selected)}")
    item["rows"] = selected
    item["days"] = [r["id"] for r in selected]
    return item


def build(run, item):
    rc, job, info = jobs.build_szamla(item["days"], item.get("heading"))
    if rc or not job:
        return Stop(6, "; ".join(info.get("failures") or [info["stdout"].strip()]))
    item["job"] = job
    item["builder_info"] = info
    return item


def translate(run, item):
    table = {norm(x["hu"]): x["en"] for x in run["texts"].get("text", [])}
    table.update({norm(k): v for k, v in item.get("text", {}).items()})
    markers = set(norm(x) for x in run["config"].get("magyar_jelzok", []))
    unknown = []
    questions = item.setdefault("questions", [])
    kept = []
    threshold = float(run["config"].get("tulora_min_perc", 15)) / 60.0
    for line in item["job"]["lines"]:
        desc = str(line["desc"])
        match = re.match(r"^(\d\d/\d\d/\d\d\s{2})(.*)$", desc)
        prefix, body = match.groups() if match else ("", desc)
        if body.startswith("Overtime,"):
            if float(line["qty"]) < threshold:
                questions.append(f"{prefix.strip()}: {float(line['qty']) * 60:g} perc tulora nincs szamlazva")
                continue
            kept.append(line)
            continue
        key = norm(body)
        if key in table:
            line["desc"] = prefix + table[key]
        else:
            words = set(re.findall(r"[a-z]+", key))
            english = body.isascii() and not (words & markers)
            if not english:
                unknown.append(body)
        kept.append(line)
    item["job"]["lines"] = kept
    if unknown:
        return Stop(3, "Ismeretlen magyar szoveg: " + "; ".join(sorted(set(unknown))))
    return item


def heading(run, item):
    if item.get("heading"):
        return item["heading"]
    months = []
    for row in item["rows"]:
        month = datetime.fromtimestamp(row["started_at"]).astimezone().strftime("%B %Y")
        if month not in months:
            months.append(month)
    return run["config"].get("heading_template", "Day work, {months}").format(
        months=" / ".join(months))


def check(run, item):
    if (run["business"].get("vat_number") or "").strip():
        return Stop(6, "A VAT sor a tulajdonos dontese; kitoltott vat_number mellett megalltam")
    # Ask the established recipient resolver, before its invoice-builder caller
    # falls back to a work-site address.  A site is not a customer's postal
    # address, and accepting that fallback would hide an incomplete client book.
    _name, registered_address, warning = jobs._szamla_cimzett(item["client"])
    if warning or not registered_address:
        return Stop(4, warning or f"{item['client']}: hianyzik a cim")
    item["job"]["job"] = heading(run, item)
    long_lines = [l["desc"] for l in item["job"]["lines"] if len(str(l["desc"])) > 64]
    if long_lines:
        return Stop(3, "64 karakternel hosszabb szamlasor: " + str(long_lines[0]))
    return item


def safe_name(text):
    value = re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")
    return value or "ugyfel"


def draft(run, item):
    path = os.path.join(run["run_dir"], safe_name(item["client"]) + ".json")
    pdf = os.path.splitext(path)[0] + ".pdf"
    item["json_path"], item["pdf_path"] = path, pdf
    if run["dry_run"]:
        return item
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(item["job"], fh, indent=2, ensure_ascii=False)
    invoice_program.render("invoice", item["job"], run["business"], pdf, preview=True)
    item["json_sha256"], item["pdf_sha256"] = sha(path), sha(pdf)
    return item


STEP_FUNCTIONS = {x.__name__: x for x in (collect, build, translate, check, draft)}
PRIORITY = {2: 0, 6: 1, 4: 2, 3: 3, 5: 4, 1: 5, 0: 6}


def missing_days():
    env = dict(os.environ, MUHELY_JOBS=LEDGER, MUHELY_NAPLO_HIANY_SEEN=SEEN)
    p = subprocess.run([sys.executable, os.path.join(HERE, "naplo-hiany.py"), "--ember"],
                       text=True, capture_output=True, env=env)
    return p.returncode, p.stdout.strip() or p.stderr.strip()


def draft_command(args):
    made = ensure_configs(args.dry_run)
    try:
        cfg_path = config_path("szamla.toml", made)
        txt_path = config_path("szovegek.toml", made)
        cfg = load_toml(cfg_path)
        texts = load_toml(txt_path, format_version=True)
        if cfg.get("version") != 1:
            raise ValueError(f"{cfg_path}: ismeretlen version: {cfg.get('version')}")
        unknown_steps = [x for x in cfg.get("steps", []) if x not in STEP_FUNCTIONS]
        if unknown_steps:
            raise ValueError(f"{cfg_path}: ismeretlen lepes: {unknown_steps[0]}")
        ledger = load_json(LEDGER)
        load_json(CLIENTS)
        business = load_json(BUSINESS)
        run_file = load_toml(args.run, format_version=True) if args.run else {}
    except ValueError as exc:
        print(f"Hibas bemenet: {exc}", file=sys.stderr)
        return 2
    available = [r for r in ledger if r.get("stopped_at") is not None
                 and not r.get("invoice_ref") and not r.get("no_charge")]
    still_open = [r for r in ledger if r.get("stopped_at") is None and not r.get("invoice_ref")]
    for row in still_open:
        # An open day is never offered; say so, or it silently waits a week.
        print(f"Nyitott nap, nincs a szamlan: {row.get('client')}, #{row.get('id')}", file=sys.stderr)
    if not available and not run_file.get("invoice"):
        print("Nincs szamlazando nap.")
        return 0
    overrides = run_file.get("invoice", [])
    items = []
    if overrides:
        for value in overrides:
            items.append({"client": value.get("client", ""), "cis": bool(value.get("cis", False)),
                          "days": value.get("days"), "heading": value.get("heading"),
                          "text": value.get("text", {})})
    else:
        for client, cis in sorted({(r.get("client"), bool(r.get("cis"))) for r in available}):
            items.append({"client": client, "cis": cis})
    day = args.date or date.today().isoformat()
    run_dir = os.path.join(PROGRAM, "runs", day)
    run = {"ledger": ledger, "business": business, "config": cfg, "texts": texts,
           "run_dir": run_dir, "dry_run": args.dry_run}
    if not args.dry_run:
        os.makedirs(run_dir, exist_ok=True)
    results, codes = [], []
    for item in items:
        value = item
        for name in cfg["steps"]:
            value = STEP_FUNCTIONS[name](run, value)
            if isinstance(value, Stop):
                break
        if isinstance(value, Stop):
            print(f"{item['client']}: {value.text}", file=sys.stderr)
            item.update(status="stopped", code=value.code, error=value.text)
            codes.append(value.code)
            results.append(item)
        else:
            value["status"] = "draft"
            codes.append(1)
            results.append(value)
            print(f"Piszkozat: {value['client']}, {len(value['days'])} nap, "
                  f"{sum(float(x['qty']) * float(x['unit']) for x in value['job']['lines']):,.2f} GBP")
    miss_rc, miss_text = missing_days()
    if miss_rc == 2:
        print(f"Hibas bemenet: {miss_text}", file=sys.stderr)
        return 2
    if miss_rc == 1:
        codes.append(5)
    final = min(codes or [0], key=lambda x: PRIORITY[x])
    if args.dry_run:
        return final
    messages = []
    manifest_items = []
    for item in results:
        total = cis = None
        if item.get("status") == "draft":
            total = sum(float(x["qty"]) * float(x["unit"]) for x in item["job"]["lines"])
            cis_base = sum(float(x["qty"]) * float(x["unit"]) for x in item["job"]["lines"] if x.get("cis"))
            cis = round(cis_base * float(business.get("cis_rate", 0)) / 100, 2) if item["job"].get("cis") else 0
            questions = list(item.get("questions", []))
            if miss_text:
                # The owner message describes days, never internal ledger ids.
                questions.append(re.sub(r"#\d+", "egy munkanap", miss_text))
            qtext = (" Kerdes: " + " ".join(questions)) if questions else ""
            ctext = f" CIS: {cis:,.2f} GBP." if cis else ""
            messages.append(cfg["uzenet_template"].format(client=item["client"], days=len(item["days"]),
                            total=f"{total:,.2f}", cis=ctext, questions=qtext))
        manifest_items.append({k: item.get(k) for k in ("client", "days", "status", "code",
                              "error", "json_path", "pdf_path", "json_sha256", "pdf_sha256")})
        manifest_items[-1].update(total=total, cis=cis, reference=None)
    with open(os.path.join(run_dir, "uzenet.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(messages) + ("\n" if messages else ""))
    inputs = [LEDGER, CLIENTS, BUSINESS, cfg_path, txt_path]
    if os.path.exists(SEEN):
        inputs.append(SEEN)
    inputs += [args.run] if args.run else []
    manifest = {"program_version": VERSION, "config_version": cfg["version"],
                "inputs": {os.path.abspath(p): sha(p) for p in inputs}, "exit_code": final,
                "invoices": manifest_items}
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return final


def approve_command(args):
    manifest_path = os.path.join(args.run_dir, "run.json")
    try:
        manifest = load_json(manifest_path)
        entry = next(x for x in manifest["invoices"] if x["client"] == args.client)
        if entry.get("status") != "draft":
            print(f"{args.client}: nincs jovahagyhato piszkozat (allapot: {entry.get('status')})",
                  file=sys.stderr)
            return 6
        path = entry["json_path"]
        if sha(path) != entry["json_sha256"]:
            print("a piszkozat valtozott az uzenet ota", file=sys.stderr)
            return 6
        job = load_json(path)
        business = load_json(BUSINESS)
    except (ValueError, KeyError, StopIteration) as exc:
        print(f"Hibas bemenet: {exc}", file=sys.stderr)
        return 2
    for jid in invoice_program.job_ids(job):
        prior = [x for x in invoice_program.issued_for(jid) if x.get("kind") == "invoice"]
        if prior:
            print(f"A #{jid} munkara MAR MENT SZAMLA", file=sys.stderr)
            return 6
    ref = str(job.get("reference") or invoice_program.next_number("invoice", business, bump=False))
    out_dir = os.path.join(STORE, "muhely", "invoices")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{ref}-{safe_name(args.client)}.pdf")
    render_err = io.StringIO()
    try:
        with contextlib.redirect_stderr(render_err):
            result = invoice_program.render("invoice", job, business, out)
    except (ValueError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        return 6
    rendered_messages = render_err.getvalue()
    if rendered_messages:
        print(rendered_messages, end="", file=sys.stderr)
    text = pdftext.extract(out)
    mismatches = []
    for line in job.get("lines", []):
        amount = float(line["qty"]) * float(line["unit"])
        expected = (str(line["desc"]), f"{float(line['qty']):g}",
                    invoice_program.money(float(line["unit"])), invoice_program.money(amount))
        if any(x not in text for x in expected):
            mismatches.append(" | ".join(expected))
    if f"Total\n{invoice_program.money(result['total'])}" not in text and invoice_program.money(result["total"]) not in text:
        mismatches.append(f"Total {invoice_program.money(result['total'])}")
    report = os.path.join(args.run_dir, "visszaolvasas.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("RENDBEN\n" if not mismatches else "ELTERES\n" + "\n".join(mismatches) + "\n")
    drive_failed = (bool(os.environ.get("MUHELY_NO_DRIVE")) or
                    "Drive-ra NEM sikerult" in rendered_messages or
                    "Drive-archivalas" in rendered_messages)
    entry.update(status="issued", reference=result["reference"], final_pdf=out,
                 drive="Drive masolat nem ment" if drive_failed else "rendben")
    manifest["exit_code"] = 7 if mismatches else 0
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    if drive_failed:
        print("Drive masolat nem ment")
    return 7 if mismatches else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("draft")
    p.add_argument("--run")
    p.add_argument("--date")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(function=draft_command)
    p = sub.add_parser("approve")
    p.add_argument("run_dir")
    p.add_argument("client")
    p.set_defaults(function=approve_command)
    args = parser.parse_args(argv)
    return args.function(args)


if __name__ == "__main__":
    sys.exit(main())
