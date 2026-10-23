#!/usr/bin/env python3
"""Per-agent, per-day shared-skill selection (owner tg 8937 + 8939, 2026-10-23).

Every agent sees three kinds of shared skill:
  core      always in the listing with its description (store/skill-kiosztas.json "core")
  pool      the agent's own allocation: listed by NAME ONLY (still invocable by name),
            and with the full description on the days the agent chose it
  the rest  off: not in the listing, not invocable

The state is written as Claude Code `skillOverrides` into the agent's
.claude/settings.local.json (main agent: the repo root one), the highest
non-locked settings scope. Measured on kiron 2026-10-23: "off" removes the skill,
"name-only" lists it without description; a skill switched from off to on
mid-session arrives at once as a delta listing, but name-only -> on only shows
its description from the next FRESH session (a resumed session keeps its listing).

Usage (agent name = dashboard name, the main agent is "marveen"):
  skill-valasztas.py lista NAME             pool with descriptions + today's choice
  skill-valasztas.py valaszt NAME s1 s2 ..  today's choice (replaces it); "-" = none
  skill-valasztas.py betolt NAME s1 ..      add for today, also outside the pool
  skill-valasztas.py alkalmaz [NAME|--mind] rewrite the settings from the state
  skill-valasztas.py reggel [--dry-run]     new day: reset choices, rewrite all,
                                            message every running agent to choose
  skill-valasztas.py allapot                counts per agent + unallocated skills
"""
import datetime
import glob
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "store", "skill-kiosztas.json")
STATE_DIR = os.path.join(ROOT, "store", "skill-valasztas")
SKILL_ROOTS = [os.path.expanduser("~/.claude/skills"), os.path.join(ROOT, ".claude", "skills")]
SCRIPT = "python3 /home/gabor/marveen/scripts/skill-valasztas.py"


def today():
    return datetime.date.today().isoformat()


def conf():
    with open(CONF) as f:
        return json.load(f)


def installed():
    """name -> description of every shared skill (global + repo root)."""
    out = {}
    for root in SKILL_ROOTS:
        for p in sorted(glob.glob(os.path.join(root, "*", "SKILL.md"))):
            name = os.path.basename(os.path.dirname(p))
            desc = ""
            with open(p, errors="replace") as f:
                lines = f.read().split("\n")
            for i, line in enumerate(lines[1:40], 1):
                if line.startswith("description:"):
                    desc = line[len("description:"):].strip().strip("\"'>|")
                    if not desc and i + 1 < len(lines):
                        desc = lines[i + 1].strip()
                    break
            out.setdefault(name, desc)
    return out


def agents(c):
    return sorted(c["agents"])


def pool(c, agent):
    out = []
    for item in c["agents"].get(agent, []):
        if item.startswith("@"):
            out += c["groups"][item[1:]]
        else:
            out.append(item)
    return sorted(set(out) - set(c["core"]))


def settings_path(agent):
    if agent == "marveen":
        return os.path.join(ROOT, ".claude", "settings.local.json")
    return os.path.join(ROOT, "agents", agent, ".claude", "settings.local.json")


def state(agent):
    p = os.path.join(STATE_DIR, agent + ".json")
    try:
        with open(p) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    if s.get("datum") != today():
        s = {"datum": today(), "valasztott": [], "betoltott": [], "valasztva": False}
    return s


def save_state(agent, s):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, agent + ".json")
    with open(p + ".tmp", "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def overrides(c, agent, s, names):
    core, mine = set(c["core"]), set(pool(c, agent))
    full = set(s["valasztott"]) | set(s["betoltott"])
    out = {}
    for n in sorted(names):
        if n in core or n in full:
            out[n] = "on"
        elif n in mine:
            out[n] = "name-only"
        else:
            out[n] = "off"
    return out


def apply(c, agent, names):
    p = settings_path(agent)
    if not os.path.isdir(os.path.dirname(os.path.dirname(p))):
        return "NINCS MAPPA: " + os.path.dirname(os.path.dirname(p))
    try:
        with open(p) as f:
            d = json.load(f)
    except OSError:
        d = {}
    ov = overrides(c, agent, state(agent), names)
    d["skillOverrides"] = ov
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(p + ".tmp", p)
    cnt = {k: sum(1 for v in ov.values() if v == k) for k in ("on", "name-only", "off")}
    return "teljes %(on)d, csak nev %(name-only)d, ki %(off)d" % cnt


def check_names(wanted, names):
    bad = [w for w in wanted if w not in names]
    if bad:
        sys.exit("FAIL: nincs ilyen kozos skill: " + ", ".join(bad))


def morning_text(agent):
    return (
        "[SKILL-VALASZTAS] Uj nap. Gondold at, min fogsz ma dolgozni (hot memoria, kartyaid, "
        "jegyzeteid, nyitott uzeneteid), es valaszd ki hozza a skilleket a SAJAT keretedbol.\n"
        "1. Lista: " + SCRIPT + " lista " + agent + "\n"
        "2. Valasztas: " + SCRIPT + " valaszt " + agent + " skill1 skill2 ...  (ha ma egyik sem kell: valaszt " + agent + " -)\n"
        "Napkozben, ha kell egy uj: " + SCRIPT + " betolt " + agent + " skill\n"
        "Az alap-skillek mindig megvannak. A keretedben levo skill neve most is a listadban all, "
        "es a Skill toollal barmikor hivhato; a leirasa a kovetkezo friss sessiontol latszik."
    )


def main(args):
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    c = conf()
    names = installed()

    if cmd == "lista":
        agent = rest[0]
        s = state(agent)
        print("%s, %s. Mai valasztas: %s" % (agent, today(),
              ", ".join(s["valasztott"] + s["betoltott"]) or ("nincs (valasztott: semmi)" if s["valasztva"] else "MEG NEM VALASZTOTTAL")))
        print("\nAlap (mindig teljes): " + ", ".join(sorted(n for n in c["core"] if n in names)))
        print("\nA kereted (valaszthato):")
        for n in pool(c, agent):
            if n in names:
                print("  %-36s %s" % (n, names[n][:150]))
        return 0

    if cmd in ("valaszt", "betolt"):
        agent, wanted = rest[0], [w for w in rest[1:] if w != "-"]
        if agent not in c["agents"]:
            sys.exit("FAIL: ismeretlen ugynok: " + agent)
        check_names(wanted, names)
        s = state(agent)
        if cmd == "valaszt":
            mine = set(pool(c, agent)) | set(c["core"])
            outside = [w for w in wanted if w not in mine]
            if outside:
                sys.exit("FAIL: nincs a keretedben: %s. Ha megis kell ma: betolt %s %s"
                         % (", ".join(outside), agent, " ".join(outside)))
            s["valasztott"] = sorted(set(wanted))
        else:
            s["betoltott"] = sorted(set(s["betoltott"]) | set(wanted))
        s["valasztva"] = True
        save_state(agent, s)
        print("OK %s: %s" % (agent, apply(c, agent, names)))
        if cmd == "betolt":
            print("Ami eddig ki volt kapcsolva, az most azonnal megjelenik a listadban; "
                  "ami csak nevvel allt ott, az a Skill toollal most is hivhato.")
        return 0

    if cmd == "alkalmaz":
        targets = agents(c) if (not rest or rest[0] == "--mind") else rest
        for a in targets:
            print(a, apply(c, a, names))
        return 0

    if cmd == "reggel":
        dry = "--dry-run" in rest
        running = set()
        try:
            req = urllib.request.Request("http://localhost:3420/api/agents")
            with open(os.path.join(ROOT, "store", ".dashboard-token")) as f:
                req.add_header("Authorization", "Bearer " + f.read().strip())
            with urllib.request.urlopen(req, timeout=20) as r:
                running = {a.get("name") for a in json.loads(r.read().decode()) if a.get("running")}
        except Exception as e:  # the reset still matters without the nudge
            print("FIGYELEM: /api/agents nem valaszolt (%s), uzenet nem megy ki" % e)
        running.add("marveen")
        for a in agents(c):
            if not dry:
                save_state(a, state(a))  # date rolled -> empty choice
                res = apply(c, a, names)
            else:
                res = "(dry-run)"
            if a not in running:
                print(a, res, "| NEM FUT: az elso inditasakor valaszt (kozos-szabalyok)")
                continue
            if dry:
                print("--- " + a + "\n" + morning_text(a))
                continue
            r = subprocess.run(["bash", os.path.join(ROOT, "scripts", "agent-msg.sh"), "marveen", a, "-"],
                               input=morning_text(a), text=True, capture_output=True)
            print(a, res, "|", (r.stdout or r.stderr).strip())
        return 0

    if cmd == "allapot":
        allocated = set(c["core"])
        for a in agents(c):
            allocated |= set(pool(c, a))
            s = state(a)
            ov = overrides(c, a, s, names)
            print("%-9s teljes %3d  csak nev %3d  ki %3d  mai valasztas: %s" % (
                a, sum(v == "on" for v in ov.values()), sum(v == "name-only" for v in ov.values()),
                sum(v == "off" for v in ov.values()),
                ", ".join(s["valasztott"] + s["betoltott"]) or ("-" if s["valasztva"] else "nincs meg")))
        orphan = sorted(set(names) - allocated)
        unknown = sorted(allocated - set(names))
        print("\nSenkihez nincs kiosztva (mindenkinel ki): " + (", ".join(orphan) or "-"))
        print("Kiosztva, de nincs telepitve: " + (", ".join(unknown) or "-"))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
