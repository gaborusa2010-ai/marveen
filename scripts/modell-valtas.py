#!/usr/bin/env python3
"""Switch a fleet agent to another model -- safely, and without losing work.

WHY IT EXISTS
-------------
The owner asked for it on 2026-09-20 (Telegram msg 3256) as a "just in case"
tool: not because he wants to switch today, but so that switching is one command
when he does. Before this, a switch meant hand-editing agent-config.json and
remembering the restart order.

THE THREE TRAPS IT EXISTS TO CLOSE
----------------------------------
1. **A WRONG MODEL ID FAILS SILENTLY.** agent-process.ts:1549 spells it out: the
   interactive TUI validates `--model` against known Anthropic models and
   silently falls back to the built-in default for an unrecognized value, which
   then errors against the custom ANTHROPIC_BASE_URL. So a typo does not give
   you "no such model" -- it gives you an agent that looks started and is not on
   the model you asked for. This script validates the id SHAPE against the four
   provider families the code actually implements (resolveProviderEnv) and
   refuses anything it cannot place.

2. **THE OLD CONVERSATION MUST NOT BE CARRIED OVER.** The launcher adds
   `--continue` when a prior session exists, which is right for a same-model
   restart and wrong for a model switch: the free/cheap models have a far
   smaller context window than the 1M the agents run on now, so replaying the
   old transcript into them cannot work. The work must travel in HANDOFF.md, the
   cards and the memories -- the same route the context-guard already uses every
   day. This script therefore REFUSES to switch a running agent unless it can
   see a fresh HANDOFF.md, and says so.

3. **A SWITCH YOU CANNOT UNDO.** Every run backs up agent-config.json first and
   `--vissza` restores it, so a bad switch costs one command, not an archaeology
   session.

WHAT IT DOES NOT DO
-------------------
It does not ask the agent to write the handoff. Doing that means an inter-agent
message and an open-ended wait, and a half-waited handoff is worse than none --
you would restart on top of a file that is still being written. The operator
asks for the handoff, sees it land, then runs this.

Usage:
  modell-valtas.py --lista                      # current models + the known tiers
  modell-valtas.py cody claude-opus-5[1m]
  modell-valtas.py cody openrouter-auto:tier0   # free tier
  modell-valtas.py cody --vissza                # restore the previous config
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "http://localhost:3420"
TOKEN_FILE = os.path.join(REPO, "store", ".dashboard-token")
# Egy friss atado lap ennyi percnel nem lehet regebbi, kulonben nem ehhez a
# valtashoz keszult. 30 perc: a kontextus-or sajat atadasai is ezen belul futnak le.
HANDOFF_FRISS_PERC = 30

# A negy csalad, amit a resolveProviderEnv (src/web/agent-process.ts) tenylegesen
# implemental. Ami egyikbe sem fer bele, azt NEM engedjuk at: lasd az 1. csapdat.
TIEREK = {"tier0", "tier1", "tier2", "tier3", "tier4"}


def token():
    with open(TOKEN_FILE) as fh:
        return fh.read().strip()


def api(ut, adat=None, metodus=None):
    kereses = urllib.request.Request(
        API + ut, method=metodus or ("POST" if adat is not None else "GET"),
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"},
        data=json.dumps(adat).encode() if adat is not None else None)
    with urllib.request.urlopen(kereses, timeout=30) as v:
        nyers = v.read().decode()
    try:
        return v.status, json.loads(nyers)
    except ValueError:
        return v.status, nyers


def agens_dir(nev):
    return os.path.join(REPO, "agents", nev)


def config_ut(nev):
    return os.path.join(agens_dir(nev), "agent-config.json")


def modell_csalad(m):
    """Melyik szolgaltatora megy, vagy None ha egyikre sem illik."""
    if m.startswith("openrouter-auto:"):
        return "openrouter-auto" if m.split(":", 1)[1] in TIEREK else None
    if m.startswith("claude-"):
        return "claude"
    if m.startswith("deepseek-"):
        return "deepseek"
    if m.startswith("minimax-"):
        return "minimax"
    if "/" in m:
        return "openrouter"
    # Ollama-cimke: nev:tag, per jel nelkul. Szandekosan SZUK: egy elgepelt
    # claude-nev (pl. "claude5-opus") igy NEM csuszik at ollamanak.
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*", m):
        return "ollama"
    return None


def fut_e(nev):
    try:
        ki = subprocess.run(["pgrep", "-af", "claude"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None  # nem tudom megmondani -- ez NEM ugyanaz, mint a "nem fut"
    for sor in ki.splitlines():
        pid = sor.split()[0]
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cwd == agens_dir(nev) or cwd.startswith(agens_dir(nev) + os.sep):
            return True
    return False


def handoff_kora_perc(nev):
    p = os.path.join(agens_dir(nev), "HANDOFF.md")
    if not os.path.exists(p):
        return None
    return (time.time() - os.path.getmtime(p)) / 60


def lista():
    print(f"{'agens':<10}{'jelenlegi modell':<34}{'fut?':<8}{'HANDOFF.md'}")
    for nev in sorted(os.listdir(os.path.join(REPO, "agents"))):
        if not os.path.isfile(config_ut(nev)):
            continue
        with open(config_ut(nev)) as fh:
            m = json.load(fh).get("model") or "(nincs)"
        f = fut_e(nev)
        kor = handoff_kora_perc(nev)
        allap = {True: "fut", False: "all", None: "?"}[f]
        print(f"{nev:<10}{m:<34}{allap:<8}"
              f"{'nincs' if kor is None else f'{kor:.0f} perce'}")
    print("\nOpenRouter savok (src/web/openrouter-models.ts, beepitett alapertek):")
    print("  tier0  INGYENES   meta-llama/llama-3.3-70b-instruct:free | qwen/qwen3-coder:free")
    print("  tier1  igaslo     deepseek/deepseek-chat-v3.1 | google/gemini-2.5-flash")
    print("  tier2  kod        qwen/qwen3-coder | mistralai/codestral-2508")
    print("  tier3  gondolkodo anthropic/claude-sonnet-5 | google/gemini-3.1-pro")
    print("  tier4  latas      google/gemini-2.5-flash | qwen/qwen3-vl-30b-a3b-instruct")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("agens", nargs="?")
    ap.add_argument("modell", nargs="?")
    ap.add_argument("--lista", action="store_true")
    ap.add_argument("--vissza", action="store_true", help="az elozo konfig visszaallitasa")
    ap.add_argument("--most", action="store_true",
                    help="futo agenst is valt, atado lap nelkul (MUNKA VESZHET EL)")
    a = ap.parse_args(argv)

    if a.lista or not a.agens:
        return lista()
    if not os.path.isfile(config_ut(a.agens)):
        print(f"HIBA: nincs ilyen agens: {a.agens}", file=sys.stderr)
        return 2

    if a.vissza:
        mentesek = sorted(f for f in os.listdir(agens_dir(a.agens))
                          if f.startswith("agent-config.json.bak-modellvaltas-"))
        if not mentesek:
            print("HIBA: nincs mentes, amibol vissza lehetne allni", file=sys.stderr)
            return 2
        forras = os.path.join(agens_dir(a.agens), mentesek[-1])
        with open(forras) as fh:
            regi = json.load(fh).get("model")
        os.replace(forras, config_ut(a.agens))
        print(f"visszaallitva: {a.agens} -> {regi}  ({mentesek[-1]})")
        print("Az ujrainditast kulon kell elvegezni: POST /api/agents/<nev>/stop majd /start")
        return 0

    if not a.modell:
        print("HIBA: add meg a modellt (vagy hasznald a --lista kapcsolot)", file=sys.stderr)
        return 2
    csalad = modell_csalad(a.modell)
    if csalad is None:
        print(f"HIBA: a '{a.modell}' egyik ismert szolgaltato-alakra sem illik.\n"
              "  claude-*  |  deepseek-*  |  minimax-*  |  provider/model (OpenRouter)\n"
              "  |  openrouter-auto:tier0..tier4  |  ollama-cimke (nev:tag)\n"
              "Ezt NEM engedem at: egy fel nem ismert nevre a Claude Code CSENDBEN "
              "visszaesik az alapertelmezettre (agent-process.ts:1549), tehat a hiba "
              "csak kesobb, mashol derulne ki.", file=sys.stderr)
        return 2

    with open(config_ut(a.agens)) as fh:
        cfg = json.load(fh)
    regi = cfg.get("model")
    if regi == a.modell:
        print(f"{a.agens} mar ezen van: {regi}. Nincs teendo.")
        return 0

    fut = fut_e(a.agens)
    kor = handoff_kora_perc(a.agens)
    if fut and not a.most:
        if kor is None or kor > HANDOFF_FRISS_PERC:
            print(f"MEGALLOK: {a.agens} FUT, es "
                  + ("nincs HANDOFF.md." if kor is None else f"a HANDOFF.md {kor:.0f} perce iródott.")
                  + f"\n  Egy modellvaltas ujrainditas, es a beszelgetes NEM vihet at (a masik\n"
                    "  modell kontextusa kisebb). A munka az atado lapon, a kartyakon es az\n"
                    "  emlekekben utazik.\n"
                    "  Kerd meg az agenst, hogy irjon HANDOFF.md-t, es ha megvan, futtasd ujra.\n"
                    "  Ha tudod, hogy nincs folyamatban levo munka: --most", file=sys.stderr)
            return 3
        print(f"HANDOFF.md megvan ({kor:.0f} perce), mehet a valtas.")
    elif fut is None:
        print("FIGYELEM: nem tudtam megmerni, fut-e az agens (a pgrep nem valaszolt).")

    mentes = config_ut(a.agens) + time.strftime(".bak-modellvaltas-%Y%m%d-%H%M%S")
    with open(mentes, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)

    allapot, valasz = api(f"/api/agents/{a.agens}", {"model": a.modell}, "PUT")
    if allapot != 200:
        print(f"HIBA: a modell beallitasa nem sikerult ({allapot}): {valasz}", file=sys.stderr)
        return 2
    # VISSZAOLVASAS: a 200 azt mondja, hogy a kerest elfogadtak, nem azt, hogy a
    # fajlban is ez all. Ugyanaz a szabaly, mint az inter-agent kuldes id-jenel.
    with open(config_ut(a.agens)) as fh:
        tenyleg = json.load(fh).get("model")
    if tenyleg != a.modell:
        print(f"HIBA: a konfigban {tenyleg!r} all, nem {a.modell!r}. Nem inditom ujra.",
              file=sys.stderr)
        return 2
    print(f"modell: {regi}  ->  {tenyleg}   (csalad: {csalad}, mentes: {os.path.basename(mentes)})")

    if fut:
        api(f"/api/agents/{a.agens}/stop", {})
        time.sleep(3)
        allapot, valasz = api(f"/api/agents/{a.agens}/start", {})
        print(f"ujrainditva: HTTP {allapot}")
    else:
        print("Az agens nem futott, ezert nem inditottam el. Inditsd, amikor kell.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
