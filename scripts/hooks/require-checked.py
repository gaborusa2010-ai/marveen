#!/usr/bin/env python3
"""Stop hook: blokkolja a fordulo veget, ha az utolso assistant-uzenet VEGSO
SZOVEGEBEN nincs "Checked:" blokk.

Lasd: ~/.claude/skills/ellenorzes-kimondas-elott/SKILL.md, 5. szakasz.
Telepitve 2026-09-10, a gazda engedelyevel (Telegram, message_id 2369).
EGYELORE CSAK A Fo-AGENSNEL. A "hat kulon inode" merés, amivel eloszor
indokoltam, azt bizonyitja, hogy hat kulon FAJL van -- azt NEM, hogy a keret
melyiket olvassa kinek. Azt a CLAUDE_CONFIG_DIR env-valtozo donti el (Cody
helyesbitese, msg 4091, a sajat futo sessionjeben merve:
CLAUDE_CONFIG_DIR=agents/cody/.claude-config, es nala a ~/.claude/settings.json
tenyleg nem hat). A KOVETKEZTETES all, az ALAP volt gyenge.

MIERT PYTHON ES NEM GREP. A bash-valtozat a JSONL NYERS SORARA grepelt, es az
HAMIS ZOLDET adott: egy sor tartalmazza a tool_use blokkokat is, tehat ha a
"Checked:" szo barhol elofordult egy PARANCSBAN vagy egy fajlnevben, a hook
atengedett. Cody talalta (2026-09-10, msg 4086), es Marveen visszamerte a SAJAT
ELO transzkriptjen: a nyers sorban ott allt a szo, a vegso szovegben nem --
vagyis a frissen feltett hook aznap semmit nem vedett volna.

FAIL-OPEN minden bizonytalan esetben: egy hibas hook soha ne blokkolja a munkat.
"""
import json
import os
import sys
import time

LOG_MAX = 1_000_000   # bytes; one rotation to .1 keeps at most ~2 MB on disk


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                      # olvashatatlan bemenet -> soha ne blokkolj

    # DIAGNOSZTIKA (2026-09-10 06:0x): az ELSO eles blokk hamis pozitiv volt.
    # A parser JO (a 513. sorig vagott transzkripten exit=0), de a hook a VEGSO
    # assistant-uzenet KIIRASA ELOTT futott: a naplo vegen meg egy kozbenso
    # narracios sor allt, Checked blokk nelkul -> blokk. Amig ez nincs tisztazva,
    # minden hivas payloadja naploba megy, hogy lassuk, hordoz-e a bemenet
    # barmit a vegso valaszbol.
    #
    # 2026-09-30: the question above is settled (`last_assistant_message`, see
    # below), so the full payload is no longer logged: it grew the file to 5.1 MB.
    # What the later measurements read from this log is kept (session, cwd, keys,
    # outcome); the file is rotated once to `.1` above LOG_MAX bytes.
    try:
        log_path = os.path.expanduser("~/.claude/hooks/require-checked.log")
        if os.path.isfile(log_path) and os.path.getsize(log_path) > LOG_MAX:
            os.replace(log_path, log_path + ".1")
        lam_ = payload.get("last_assistant_message")
        with open(log_path, "a", encoding="utf-8") as lg:
            lg.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "kulcsok": sorted(payload.keys()),
                "session_id": payload.get("session_id"),
                "cwd": payload.get("cwd"),
                "stop_hook_active": payload.get("stop_hook_active"),
                "lam_len": len(lam_) if isinstance(lam_, str) else None,
                "lam_checked": ("Checked:" in lam_) if isinstance(lam_, str) else None,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if payload.get("stop_hook_active") is True:
        return 0                      # hurokvedelem

    # ELSoDLEGES FORRAS: a keret ODAADJA a kesz valaszt.
    # Merve 2026-09-10 06:08 BST, az elso eles payloadbol: a Stop hook bemenete
    # tartalmaz egy `last_assistant_message` kulcsot, sima stringkent, benne a
    # fordulot zaro valasz teljes szovegevel. Ezzel a TRANSZKRIPT-VERSENYFUTAS
    # ELTUNIK: nem kell megvarni, hogy a valasz beirodjon a JSONL-be.
    # Ez javitja az elso eles blokkot (06:05), ami hamis pozitiv volt: a hook a
    # valasz kiirasa ELOTT futott, es a naplo vegen meg egy kozbenso narracios
    # sor allt, Checked blokk nelkul.
    lam = payload.get("last_assistant_message")
    if isinstance(lam, str) and lam.strip():
        return 0 if "Checked:" in lam else block()

    # TARTALEK AG: ha a keret nem adja a mezot (regebbi vagy masik build), a
    # transzkript utolso NEM URES szoveges assistant-uzenetet nezzuk. Ez az ag
    # versenyfutasra hajlamos -- lasd fent --, ezert csak tartalek.
    path = payload.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0

    text = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    blocks = [content]
                elif isinstance(content, list):
                    blocks = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                else:
                    blocks = []
                joined = "".join(blocks)
                if joined.strip():
                    text = joined     # csak a SZOVEGES assistant-uzenet szamit
    except Exception:
        return 0

    if text is None:
        return 0                      # nincs szoveges assistant-uzenet -> fail-open
    if "Checked:" in text:
        return 0
    return block()


def block() -> int:
    sys.stderr.write(
        "BLOKKOLVA az ellenorzes-kimondas-elott skill miatt: a valasz VEGSO "
        "SZOVEGEBEN nincs 'Checked:' blokk (egy parancsban vagy fajlnevben "
        "szereplo elofordulas nem szamit). Valasz elott ellenorizz minden "
        "tenyallitast forrassal szemben, es minden kerdes elott keress a "
        "memoriaban, a projekt fajljaiban es a beszelgetesben. Aztan zard a "
        "valaszt a Checked: blokkal (a formatum a skill 4. szakaszaban).\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
