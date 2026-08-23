#!/usr/bin/env python3
"""
Claude usage watch: self-calibrating quota warning for a subscription plan.

The authoritative usage endpoint needs an OAuth token this install does not
have, so a real percentage is not available. What IS available is the local
transcript volume per rolling window. On its own that number means nothing.
It becomes useful once we know where the ceiling actually is, so this records
every window and learns the ceiling from the one event that reveals it: the
moment the limit is actually hit.

Until a limit has been observed, it reports the trend and says plainly that it
cannot give a percentage. It does not invent one. A fabricated "you are at 80%"
is worse than no number, because it gets trusted.

Usage
-----
  usage-watch.py snapshot            # record the current window, warn if near
  usage-watch.py hit [--note "..."]  # a limit WAS hit now: calibrate from this
  usage-watch.py report              # what we know so far
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "usage-watch.json")
PROJECTS = os.path.expanduser("~/.claude/projects")

WINDOW_HOURS = 5          # the plan's rolling window
WARN_AT = 0.75            # fraction of the learned ceiling that triggers a warning


def _window_tokens(hours=WINDOW_HOURS):
    """Total tokens seen in transcripts within the last `hours`.

    Counts input, output and cache fields because the plan meters them all,
    and a count that silently drops cache reads understates a heavy session.
    """
    cutoff = time.time() - hours * 3600
    total = 0
    files = 0
    for root, _dirs, names in os.walk(PROJECTS):
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            p = os.path.join(root, n)
            try:
                if os.path.getmtime(p) < cutoff:
                    continue
                files += 1
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        ts = rec.get("timestamp")
                        if ts:
                            try:
                                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if t.timestamp() < cutoff:
                                    continue
                            except ValueError:
                                pass
                        u = ((rec.get("message") or {}).get("usage")
                             or rec.get("usage") or {})
                        for k in ("input_tokens", "output_tokens",
                                  "cache_creation_input_tokens",
                                  "cache_read_input_tokens"):
                            total += int(u.get(k) or 0)
            except OSError:
                continue
    return total, files


def _load():
    if os.path.exists(STATE):
        try:
            with open(STATE) as fh:
                return json.load(fh)
        except ValueError:
            pass
    return {"observations": [], "limit_hits": [], "ceiling": None, "plan": "Pro"}


def _save(d):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, STATE)


def _extension_percent():
    """Fresh reading from the Chrome extension, or None.

    Only a recent reading is usable: a percentage from this morning says
    nothing about the window we are in now.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from chrome_usage_bridge import read_latest
    except ImportError:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "cub", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "chrome-usage-bridge.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            read_latest = mod.read_latest
        except Exception:
            return None
    try:
        r = read_latest()
    except Exception:
        return None
    if not r or r.get("age_seconds", 1e9) > 1800:
        return None
    sess = (r.get("limits") or {}).get("session")
    if not sess:
        return None
    r["percent"] = sess["percent"]           # the 5 hour window is the one that bites
    r["resets_at"] = sess["resets_at"]
    r["weekly_percent"] = ((r.get("limits") or {}).get("weekly") or {}).get("percent")
    return r


CALIB_MIN_PERCENT = 10      # below this, tokens/pct divides by near-nothing
MAX_CALIB_SPREAD = 1.5      # above this the ratio is not a constant, so drop it


def _recalibrate(d):
    """Ceiling from (tokens, percent) pairs; the limit hits still win.

    A hit is ground truth. The extension pairs are an estimate, so they are
    only used when nothing better exists, and the answer is the median, which
    one bad pair cannot drag around.
    """
    hits = [h["tokens"] for h in d.get("limit_hits", []) if h["tokens"] > 0]
    if hits:
        d["ceiling"] = min(hits)
        d["ceiling_source"] = "limit_hit"
        return
    pairs = [p for p in d.get("calibration", [])
             if p.get("percent", 0) >= CALIB_MIN_PERCENT and p.get("tokens", 0) > 0]
    if len(pairs) >= 3:
        est = sorted(p["tokens"] / (p["percent"] / 100.0) for p in pairs)
        spread = est[-1] / est[0] if est[0] else 999.0
        # Measured 2026-08-22: implied ceilings ran from 92M to 269M on the same
        # account within hours, a spread of 2.9x. The local counter includes
        # cache reads, which the plan does not meter proportionally, so this
        # ratio is not a constant and no median over it is meaningful. Rather
        # than publish a number with a 3x error bar, refuse to publish one.
        if spread > MAX_CALIB_SPREAD:
            d["ceiling"] = None
            d["ceiling_source"] = (f"unusable: a {len(pairs)} meresbol szamolt plafon "
                                   f"{spread:.1f}x szorast mutat")
        else:
            d["ceiling"] = int(est[len(est) // 2])
            d["ceiling_source"] = f"extension_estimate ({len(pairs)} meres)"


def cmd_snapshot(argv):
    d = _load()
    tokens, files = _window_tokens()
    now = int(time.time())
    d["observations"].append({"at": now, "tokens": tokens, "files": files})
    d["observations"] = d["observations"][-500:]

    ext = _extension_percent()
    if ext:
        d.setdefault("calibration", []).append(
            {"at": now, "tokens": tokens, "percent": ext["percent"]})
        d["calibration"] = d["calibration"][-200:]
    _recalibrate(d)

    ceiling = d.get("ceiling")
    est_pct = tokens / float(ceiling) if ceiling else None

    # A fresh measurement always beats a derived estimate. The local token count
    # includes cache reads, which the plan does not meter the same way, so the
    # ratio drifts: measured 16% against an estimated 45% on the same window.
    # Reporting the estimate next to the measurement as though both were facts
    # is how a number nobody should trust gets trusted.
    measured = ext["percent"] / 100.0 if ext else None
    pct = measured if measured is not None else est_pct
    status = "WARN" if (pct is not None and pct >= WARN_AT) else "ok"

    divergent = (measured is not None and est_pct is not None
                 and abs(measured - est_pct) > 0.20)
    if divergent:
        d["calibration_warning"] = {
            "at": now, "measured": round(measured, 3), "estimated": round(est_pct, 3)}
    _save(d)

    iso = datetime.fromtimestamp(now, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    print(f"{iso}   {WINDOW_HOURS}h ablak: {tokens:,} token ({files} aktiv transcript)")
    if ext:
        wk = f", heti {ext['weekly_percent']}%" if ext.get("weekly_percent") is not None else ""
        left = ext.get("resets_at", 0) - time.time()
        lh, lm = int(max(0, left) // 3600), int((max(0, left) % 3600) // 60)
        print(f"Chrome extension (friss, {ext['age_seconds'] // 60} perce): "
              f"5 oras {ext['percent']}%{wk}, reset {lh}h {lm}m mulva")
        if ext["percent"] >= int(WARN_AT * 100):
            print(f"ALERT: az extension szerint {ext['percent']}% -on vagy. "
                  f"Ez MERT ertek, nem becsles.")
    if measured is not None:
        if status == "WARN":
            print(f"ALERT: {measured*100:.0f}% a mert ertek, a {WARN_AT*100:.0f}% "
                  f"felett vagy. Nagy feladatot inkabb halassz.")
        if divergent:
            print(f"A helyi becslesem {est_pct*100:.0f}% lenne, a mert ertek "
                  f"{measured*100:.0f}%. A becsles MEGBIZHATATLAN (a helyi szamlalo "
                  f"a cache-olt tokeneket is beleveszi), ezert a mert erteket "
                  f"hasznalom. A becsles csak akkor jut szohoz, ha a bovitmeny "
                  f"adata regi vagy hianyzik.")
    elif ceiling:
        print(f"Nincs friss mert ertek. Becsult plafon: {ceiling:,} token "
              f"({d.get('ceiling_source','?')})  ->  kb. {est_pct*100:.0f}%")
        print("Ez BECSLES, nem meres. Nyisd meg a Chrome-ot a pontos szamert.")
        if status == "WARN":
            print(f"ALERT: a becsles {WARN_AT*100:.0f}% felett van.")
    else:
        src = d.get("ceiling_source", "")
        print("Nincs friss mert ertek, es szazalekot sem tudok mondani.")
        if src.startswith("unusable"):
            print(f"  {src}. A helyi token-szamlalo nem aranyos a csomag meresevel, "
                  f"ezert nem szamolok belole szazalekot. Nyisd meg a Chrome-ot a "
                  f"bovitmennyel, es azonnal lesz pontos szam.")
        else:
            recent = [o["tokens"] for o in d["observations"][-10:]]
            if len(recent) > 1:
                print(f"  Utolso {len(recent)} helyi meres: "
                      f"{min(recent):,} es {max(recent):,} token kozott. Ez csak trend, "
                      f"nem szazalek.")
            print("  Pontos szamhoz nyisd meg a Chrome-ot a bovitmennyel, vagy ha "
                  "limitbe futsz, szolj: usage-watch.py hit")
    return 0


def cmd_hit(argv):
    """A limit was actually hit. This observation is what calibrates everything."""
    d = _load()
    tokens, _files = _window_tokens()
    note = ""
    if "--note" in argv:
        note = argv[argv.index("--note") + 1]
    now = int(time.time())
    d["limit_hits"].append({"at": now, "tokens": tokens, "note": note})

    # The lowest observed hit is the safest ceiling: the limit was already
    # reached there, so anything above it is certainly over.
    _recalibrate(d)
    _save(d)
    print(f"Rogzitve. A limit {tokens:,} tokennel lett elerve.")
    print(f"Tanult plafon most: {d['ceiling']:,} token "
          f"({len(d['limit_hits'])} megfigyelesbol).")
    return 0


def cmd_report(argv):
    d = _load()
    obs = d.get("observations", [])
    hits = d.get("limit_hits", [])
    print(f"Elofizetes: {d.get('plan')}")
    print(f"Meresek: {len(obs)}   Limit-esemenyek: {len(hits)}")
    if d.get("ceiling"):
        print(f"Tanult plafon: {d['ceiling']:,} token / {WINDOW_HOURS}h "
              f"({d.get('ceiling_source','?')})")
    cal = d.get("calibration", [])
    if cal:
        print(f"Kalibralo parok az extensionbol: {len(cal)}")
    else:
        print("Tanult plafon: meg nincs (nem futottal limitbe, vagy nem jeleztuk)")
    if obs:
        vals = [o["tokens"] for o in obs]
        print(f"Ablak-forgalom: min {min(vals):,}  atlag {sum(vals)//len(vals):,}  "
              f"max {max(vals):,}")
    for h in hits[-5:]:
        iso = datetime.fromtimestamp(h["at"], timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"  limit {iso}: {h['tokens']:,} token  {h.get('note','')}")
    return 0


def main(argv):
    cmds = {"snapshot": cmd_snapshot, "hit": cmd_hit, "report": cmd_report}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    return cmds[argv[1]](argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
