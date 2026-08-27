# Marveen ágens-referencia

Ez a CLAUDE.md kísérője. Ami itt van, azt **nem kell minden megszólaláskor** a fejemben
tartani: parancs-példák, formátum-részletek, sablonok. A CLAUDE.md-ben csak a szabály és
egy mutató marad rá.

Miért: a CLAUDE.md minden egyes körben újra betöltődik, még akkor is, amikor a válaszom
annyi hogy "nincs teendő". Naponta nagyjából harminc-nyolcvan ilyen kör fut. Amit ide
átteszünk, azt csak akkor fizetjük ki, amikor tényleg kell.

Létrehozva: 2026-08-27.

---

## Dashboard API

Minden `/api/*` végpont Bearer tokennel védett. A token: `store/.dashboard-token`.

```bash
T=$(cat /home/gabor/marveen/store/.dashboard-token)
```

### Memória

```bash
# mentés
curl -s -X POST http://localhost:3420/api/memories \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"agent_id":"marveen","content":"MIT","category":"warm","keywords":"kulcsszó1, kulcsszó2"}'

# keresés (válasz előtt: van-e releváns emlék?)
curl -s -H "Authorization: Bearer $T" \
  "http://localhost:3420/api/memories?agent=marveen&q=KULCSSZÓ&category=warm"

# napi napló (append-only)
curl -s -X POST http://localhost:3420/api/daily-log \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"agent_id":"marveen","content":"## HH:MM -- Téma\nMi történt, mi lett az eredmény"}'
```

Tier-váltás (pl. lezárult hot emlék hidegbe) API-n **nincs**, csak közvetlen SQL:

```bash
python3 -c "
import sqlite3
db=sqlite3.connect('store/claudeclaw.db')
db.execute(\"UPDATE memories SET category='cold' WHERE id IN (1,2)\"); db.commit()"
```

### Kanban

```bash
curl -s -H "Authorization: Bearer $T" http://localhost:3420/api/kanban          # lista
curl -s -X POST http://localhost:3420/api/kanban \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"title":"CÍM","status":"planned","assignee":"marveen","priority":"normal"}'
curl -s -X POST http://localhost:3420/api/kanban/ID/move \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"status":"in_progress","actor":"marveen"}'
curl -s -X POST http://localhost:3420/api/kanban/ID/comments \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"author":"marveen","content":"AZ EREDMÉNY RÖVIDEN"}'
curl -s -X POST -H "Authorization: Bearer $T" http://localhost:3420/api/kanban/ID/archive
```

- Az `actor` mezőt **minden mozgatásnál** küldd el, különben nem derül ki ki mozdította.
- Egy-kártya lekérdezés (`GET /api/kanban/ID`) **nincs**. A teljes listát kérd le és szűrd.
- **Öregedés és beakadás mérésére a `last_status_at` mezőt használd, SOHA az `updated_at`-ot.**
  Az `updated_at`-ot egy puszta komment is átírja.

### Ütemezett feladatok

Fájl-alapúak: `~/.claude/scheduled-tasks/<nev>/` (SKILL.md + task-config.json). A runner
60 másodpercenként ellenőrzi, és a futó tmux sessionbe küldi a promptot. **Ne** írd
közvetlenül a `scheduled_tasks` SQLite táblát, az régi API.

```bash
# létrehozás
curl -s -X POST http://localhost:3420/api/schedules \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"name":"feladat-nev","description":"Rövid leírás","prompt":"A prompt","schedule":"0 8 * * *","agent":"marveen","type":"heartbeat"}'

# módosítás (PUT, a PATCH 404-et ad)
curl -s -X PUT http://localhost:3420/api/schedules/NEV \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"enabled":true}'
```

Cron: `perc óra nap hónap hétnapja`. `0 8 * * *` = naponta 8:00, `*/30 * * * *` = 30
percenként, `0 9 * * 1-5` = hétköznap 9:00. Node lokális időzóna, Europe/London.

### Inter-agent üzenetek

```bash
bash scripts/agent-msg.sh marveen CELPONT "Feladat leírása."     # -> OK id=<n> vagy FAIL
echo "hosszú szöveg" | bash scripts/agent-msg.sh marveen CELPONT -
curl -s -H "Authorization: Bearer $T" http://localhost:3420/api/agents   # kik érhetők el
```

Feldolgozás után a sor nem záródik magától, és a nyitott sor ismételt ébresztést okoz:

```bash
python3 -c "
import sqlite3,time
db=sqlite3.connect('store/claudeclaw.db'); now=int(time.time())
db.execute('UPDATE agent_messages SET status=?, delivered_at=COALESCE(delivered_at,?), completed_at=? WHERE to_agent=? AND status=?',
           ('done',now,now,'marveen','pending')); db.commit()"
```

### Ágensek indítása és leállítása

```bash
curl -s -X POST -H "Authorization: Bearer $T" http://localhost:3420/api/agents/NEV/restart
curl -s -X POST -H "Authorization: Bearer $T" http://localhost:3420/api/agents/NEV/stop
curl -s -X POST -H "Authorization: Bearer $T" http://localhost:3420/api/agents/NEV/start
```

Egy sub-ágens **nem tudja magát újraindítani**, a kapcsoló a fő ágensnél van.
Újraindítás UTÁN ellenőrizd a fő ágens Telegram pollerét, rögtön és pár perccel később is,
mert a reap késleltetve is elsülhet:

```bash
P=$(cat ~/.claude/channels/telegram/bot.pid); echo "[$P]"; ps -p "$P" -o pid,etime --no-headers
```

Ha a `bot.pid` kiürült: `scripts/channel-watchdog.sh`. A channels service `systemctl
restart`-ja **tilos**, a közös tmux szervert vinné.

---

## Reggeli napindító formátum

MarkdownV2, a Telegram csatornára.

- Félkövér: `*szöveg*` (EGY csillag)
- Escapelendő: `( ) . - + = ! { } [ ] | ~ > #`
- Markdown fejléc **nincs**, a Telegram nem támogatja. Emoji + félkövér a szekciócím.
- Ha egy kategóriában nincs esemény, a szekció maradjon ki teljesen.

Tartalom: email (12 óra, spam és promó nélkül), naptár (mai nap), AI hírek (tegnapi
dátummal keresve). A `DREAM.md` öt szekciója a napindító ELEJÉRE kerül, ha a fájl létezik.

---

## Skill-sablon

Új skillhez a `skill-factory`, meglévő módosításához a `skill-management` skill való.
A váz, ha kézzel írod:

```markdown
---
name: skill-nev
description: Mikor használd, mit csinál. Legyél konkrét a triggerelésben.
---
# Skill neve

## Mikor használd
[Konkrét triggerek és kontextusok]

## Eljárás
1. [Első lépés]

## Buktatók
- [Ismert probléma és megoldása]

## Ellenőrzés
- [Hogyan validáld az eredményt]
```

Tartsd 500 sor alatt, a nagyobb anyag megy a `references/` almappába. A skillek három
szinten töltődnek: név+leírás mindig, teljes tartalom ha releváns, segédfájlok ha
kifejezetten kellenek.

Index-frissítés szerkesztés után, **mindkét szinten**:

```bash
bash scripts/skill-index.sh                     # globális
bash scripts/skill-index.sh /home/gabor/marveen # ágensspecifikus
```

---

## Kódváltoztatás ezen a gépen

- **A tesztkészlet szándékosan NEM fut éles telepítésen** (`assert-not-live-install`),
  mert a `store/` tartalmát írná felül. Ne kerüld meg.
- Marad: `npx tsc --noEmit`, `node --check web/app.js`, és éles ellenőrzés.
- **Mondd meg a gazdának és írd bele a commitba, hogy teszt nem futott.**
- Szerverkód után: `npm run build`, majd
  `systemctl --user restart marveen-dashboard`, majd curl-lel várd meg a 200-at.
  Ez biztonságos, és NEM azonos a channels újraindításával.
