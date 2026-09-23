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

# MÓDOSÍTÁS: a végpont PUT-ot ÉS PATCH-et is fogad, 200-zal. (Margó mérése 2026-09-18,
# Marveen ugyanaznap szintén használta a 917-es lapon.) A referenciában eddig CSAK POST és
# GET állt, holott egy emlék FELÜLÍRHATÓ -- és a közös dashboard-tokennel BÁRMELYIK ágens
# BÁRMELYIK lapja. Ez nem rés (közös token, közös tároló), de aki nem tud róla, véletlenül
# is megteheti.
# DE EGY-EMLEK LEKERDEZES NINCS: `GET /api/memories/<id>` -> **404**. (Mérve 2026-09-18
# 20:2x, ugyanúgy, ahogy a kanbannál a `GET /api/kanban/ID`.) Vagyis egy emléket MÓDOSÍTANI
# lehet id szerint, VISSZAOLVASNI nem -- a listát kérd le és szűrj rá. Ez azért csapda,
# mert a PUT előtti "olvasd be, told hozzá, írd vissza" minta itt NEM megy: a teljes új
# tartalmat kell megírnod, különben a `content` cseréje adatot veszít.
curl -s -X PUT "http://localhost:3420/api/memories/<id>" \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"content":"A TELJES ÚJ TARTALOM"}'
#
# A `content` CSERÉL, nem told hozzá: amit nem küldesz el, az elveszik. Tehát a teljes új
# szöveget kell átadni, nem a különbséget.
#
# ÉS AMIT SOHA: ne ezen próbáld ki, hogy "fogad-e egyáltalán PUT-ot". Az a kérdés
# olvasásnak hangzik, de ÍRÁS. Margó 2026-09-18-án egy ÉLES lapon próbálta ki
# {"content":"proba"} törzzsel, és a lap tartalma öt másodpercre tényleg "proba" lett.
# Visszaállította (a feltételek álltak rá: ő okozta, ugyanabban a sessionben, és a tartalom
# megvolt nála), de a helyes alak egy ELDOBHATÓ emlék létrehozása és AZON a próba.

# keresés (válasz előtt: van-e releváns emlék?)
curl -s -H "Authorization: Bearer $T" \
  "http://localhost:3420/api/memories?agent=marveen&q=KULCSSZÓ"

# HARMADIK CSAPDA, mérve 2026-09-04: NE tedd a keresés mellé a `&category=warm`-ot.
# Ez a példa nyolc helyen állt így (mind az öt ágens CLAUDE.md-je, ez a fájl, a
# CLAUDE.md sablon és a scaffold, ami minden ÚJ ágensnek beleírja), és rossz
# eredményt ad: a kategória POST-FILTER a keresés után (src/web/routes/memories.ts),
# tehát a találatokból kidobja a `cold` és `shared` sorokat. A cold épp a TANULSÁGOK
# helye -- amit egy válasz előtt a leginkább tudni akarsz.
# Élő eset: Forgács `jobs.py` emléke `cold`. A `q=jobs.py` 2 találatot ad,
# a `q=jobs.py&category=warm` NULLÁT. Ugyanaz a kérdés, 200-as válasz, üres lista.
# Kategóriára szűrni akkor van értelme, ha NEM keresel: `?agent=X&category=warm`
# a warm listát adja. A kettőt együtt ne.

# keresés ÚGY, HOGY MŰKÖDIK ÉS LÁTSZIK A SOR GAZDÁJA (mérve 2026-09-01 éjjel)
# KÉT csapda van benne, mindkettő mérve:
# 1. ÉKEZETES KULCSSZÓT NYERSEN AZ URL-BE TENNI NEM SZABAD. A szerver 400-at ad ÜRES
#    törzzsel: "q=mérés" -> 400, "q=meres" -> 200. Egy üres törzs a legtöbb parszolóban
#    "nincs találat"-tá válik, tehát a felidézés NÉMÁN elnémul. Ezért kell a quote().
# 2. A válasz LISTA, nem {"memories": [...]} objektum. A .get('memories') itt AttributeError.
KULCS="mérés"
Q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$KULCS")
curl -s -H "Authorization: Bearer $T" "http://localhost:3420/api/memories?agent=marveen&q=$Q" | python3 -c "
import json,sys
d=json.load(sys.stdin)
rows=d.get('memories') if isinstance(d,dict) else d
me='marveen'
mine=[r for r in rows if r.get('agent_id')==me]
shared=[r for r in rows if r.get('agent_id')!=me]
print('-- A TIED (' + str(len(mine)) + ') --')
for r in mine: print('  [' + str(r.get('category')) + '] ' + str(r.get('content'))[:100])
print('-- MEGOSZTOTT, MASTOL (' + str(len(shared)) + ') --')
for r in shared: print('  [' + str(r.get('agent_id')) + '] ' + str(r.get('content'))[:100])
"
# 3. A KERESÉS MAGA VISZONT MÁR ÉKEZET-FÜGGETLEN, tehát ha egyszer átment a kérés,
#    mindegy hogyan írod. Mérve (marveen, limit=500): q=meres 61 sor, q=mérés 61 sor,
#    metszet 61; ellenorzes/ellenőrzés 48 = 48; merte/mérte 21 = 21. Kontroll, hogy a
#    szűrő tényleg szűr: q nélkül 200 sor, q=xyzzyfoobar123 -> 0 sor. Vagyis NEM kell
#    találgatni, hogy az adat ékezettel van-e eltárolva (vegyes: a saját emlékeim 34
#    százaléka ékezetes) -- a szerver mindkét irányban összeejti. A gond KIZÁRÓLAG az
#    átvitel: nyers ékezet az URL-ben nálam 400 üres törzzsel, Margónál a python urllib
#    UnicodeEncodeError-ral áll meg még küldés előtt. Ezért a quote(), és semmi más.

# Miért kell a szétválasztás: a szűrő szerződése (agent_id = ? OR category = 'shared'),
# tehát mások megosztott sorai azonos súllyal állnak a sajátjaid mellett. Margó mérése
# szerint ez témafüggően elnyom: a q=meres keresésénél 25 sorból 20 volt idegen, és az
# első öt MIND másé. FIGYELEM: az alapértelmezett limit 50, némán vág (Margó mérése).

# LISTAZAS KATEGORIA SZERINT (nem keresés) -- ES A KET NEV, AMI 404-ET AD.
# Merve 2026-09-16: `GET /api/memory?tier=hot` -> HTTP 404. KET hiba egyszerre, és
# mindkettot a SAJAT lapjaink sugalljak:
#   * a vegpont MEMORIES, tobbes szamban  (`/api/memory` nem letezik)
#   * a mezo neve CATEGORY, nem `tier`    (a CLAUDE.md tablazat fejlece viszont "Tier",
#     es a hot/warm/cold/shared szavakat is tier-nek hivja -- innen a reflex)
# A mukodo alak:
curl -s -H "Authorization: Bearer $T" \
  "http://localhost:3420/api/memories?agent=marveen&category=hot"
# AMIERT EZ ITT ALL, ES NEM CSAK EGY EMLEKBEN: ezt 2026-09-10-en MAR megmertem
# (hot emlek 705, "tier= nema, a mezo category"), es 2026-09-16-an UJRA elkovettem.
# A tanulsag akkor a memoriaba ment, nem a masolhato parancs melle. Egy hot emlek
# hat nap alatt nem talalja meg azt, aki parancsot masol.
# ES EGY HARMADIK, ugyanabbol a korbol: a kanban listaja `/api/kanban`, NEM
# `/api/kanban/cards` (az is 404). Ket kulonbozo API, ket kulonbozo tobbes szam.

# napi napló (append-only)
curl -s -X POST http://localhost:3420/api/daily-log \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"agent_id":"marveen","content":"[LEZART] Téma -- mi történt, mi lett az eredmény"}'
```

**ÓRÁT NE ÍRJ A SOR ELEJÉRE.** A régi sablon `## HH:MM` prefixszel kezdődött, és az
2026-09-11-én megdőlt: a dashboard `renderLogEntries` függvénye (`web/app.js`) MINDEN
bejegyzés mellé kiírja a sor saját `created_at` mezőjét, óra:perc alakban. A kézzel írt
óra tehát nem redundáns, hanem **egy sorban áll a valódi idővel, és ellentmondhat neki**:
Margó naplójában tíz bejegyzés csúszott el, a végén 82 perccel, Forgácsnál öt sor,
a legnagyobb 38 perc -- és mind a tizenöt a KÉZZEL írt sorokból való.
Ha valamiért mégis kell óra a szövegbe, `date`-tel mérve, sose fejből.
(Mérve a FUTÓ buildben, ami 2026-09-04-i; kártya `e053398b`.)

**AMI VISZONT MARADJON: a `[LEZART]` / `[FOLYAMATBAN]` jelölés** (Forgács). Azt a
dashboard NEM írja ki, a napló pedig append-only -- nélküle egy délelőtti részeredmény
holnap végeredménynek látszik. És a visszahivatkozás TÉMÁRA menjen, ne órára:
„a fenti FOLYAMATBAN sort megdönti", nem „a 10:00-as sort megdönti" (Cody).

### Naplo-ora-prefix kereso

```bash
python3 scripts/naplo-prefix-kereso.py            # a hat CLAUDE.md + ez a doksi
python3 scripts/naplo-prefix-kereso.py <fajl>...  # csak a megadottak
python3 scripts/naplo-prefix-kereso.py --onteszt  # tiz kontroll (4 pozitiv, 6 negativ)
```

Kilepesi kod: **1, ha van talalat**, 0 ha nincs -- tehat hookba/cronba koetheto.
(2026-09-11-ig a hivo ELDOBTA a kiszamolt kodot es mindig 0-t adott. Roxy merte meg,
a POZITIV kontrollon: egy 0 talalat + exit 0 egyezes a hibas es a helyes valtozatnal
ugyanugy nez ki.)

**Miert szkript, es miert ne irj sajat grep-mintat ra:** 2026-09-11-en negy agens negy
jo szandeku mintat irt ugyanerre, es mind a negy mast adott -- ket alulmeres (a
literalis `HH:MM` sablon, illetve a konkret orak egyiket sem latta), es ket tulmeres
(egy horgony nelkuli minta a `--at 07:00 --billing` kapcsolot fogta meg; a mintak
UNIOJA prozaban allo idopontokat). Mind a negy "kesz" valaszt adott volna valakinek.

**Amit a szkript NEM tud:** nem kulonbozteti meg a HASZNALATOT az EMLITESTOL. Ezert
irja ki minden talalat SORAT -- a szamra ne tervezz, a sort nezd meg.

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
# HA A LEÍRÁS A VÉDETT KÖZÖS SKILL-MAPPÁRÓL SZÓL, ez az alak ELAKAD a kapun, mert a
# `-d` melletti szövegben szerepel a védett útvonal. Mérve 2026-09-16 23:4x (a
# 86848ea4 kártya létrehozásakor). A dokumentált kijárat, három lépés:
#   1. a leírást `cat > fájl <<'EOF'` alakkal írd ki  (ez ADAT, a kapu átengedi),
#   2. egy külön python FÁJL olvassa be és csomagolja JSON-ba -- az útvonal NE
#      kerüljön bele a parancssorba,
#   3. `curl ... --data-binary @a-json-fájl`.
# A POST válasza CSAK {"ok":true,"id":"..."}, tehát a mezőket VISSZAOLVASÁSSAL
# ellenőrizd, ne a válaszból (lásd lentebb a POST-vissza­jelzés buktatót).
curl -s -X POST http://localhost:3420/api/kanban/ID/move \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"status":"in_progress","actor":"marveen"}'
curl -s -X POST http://localhost:3420/api/kanban/ID/comments \
  -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d '{"author":"marveen","content":"AZ EREDMÉNY RÖVIDEN"}'
curl -s -X POST -H "Authorization: Bearer $T" http://localhost:3420/api/kanban/ID/archive
```

- **A kártya IDŐBÉLYEGEI UNIX EPOCH INTEGEREK, nem ISO stringek.** (Forgács mérése,
  2026-09-04, visszamérve.) A `created_at`, `updated_at` és `last_status_at` `int`;
  a `due_date`, `dispatched_at`, `description`, `project`, `parent_id` és
  `archived_at` lehet `None`.
  Aki `fromisoformat()`-tal próbálja, annál **minden sor egyformán bukik el**, és
  Forgács pontosan erre a csapdára hívja fel a figyelmet: az EGYENLETES hiba adatnak
  látszik, nem hibának. Nála mind a 11 waiting kártya ugyanazt a hamis kort kapta, és
  majdnem azt állította, hogy a tábla nem tárol időbélyeget.
  Helyesen: `datetime.fromtimestamp(c["updated_at"])`, vagy egyszerűen
  `(time.time() - c["updated_at"]) / 86400` a napokhoz.

- **AZ `updated_at` A MOZGATÁS IDEJE, NEM A MUNKA KORA. Egyik mező sem mondja meg,
  mióta áll egy ügy.** Ez a fenti mérés folytatása, és a következtetést érinti, nem a
  típusokat. 2026-09-04-en a waiting kártyák kora 0 és 3,7 nap között volt, amiből
  az következne, hogy beragadt kártya nincs. Csakhogy két 0,0 napos kártya
  (`200e4013`, `d048ba1e`) aznap reggel **azért** lett friss, mert ÉN tettem át őket
  waiting-be -- a tartalmuk 9,5 napja nem mozdult, és a gazda kifejezetten félretette
  őket.
  A `last_status_at` sem elég: az a státuszváltásé (egy komment csak az `updated_at`-et
  tolja, lásd `8539d215`: updated 1,9 nap, last_status 3,7 nap), és egy áthelyezés
  mindkettőt nullázza.
  **ES A KET MEZO EGYMAS ELLENTETET MONDJA -- ez a lelet elesebb fele, Forgacse.**
  Ugyanaz a 11 waiting kartya, ugyanaz a pillanat, 2026-09-04 08:40, ket mezo:

      created_at szerint:  0,0 0,4 0,4 0,4 1,9 2,0 8,9 9,5 9,5 9,7 9,7
      updated_at szerint:  0,0 0,0 0,0 0,4 0,4 0,4 1,6 1,6 1,9 1,9 3,7

  Az egyik szerint OT kartya legalabb 8,9 napja all; a masik szerint a legidosebb
  3,7 napos, es beragadt kartya nincs. (Fuggetlenul visszamerve: a szigoru "> 8,9"
  negyet ad, a "legalabb 8,9" otot -- a kep ettol nem valtozik.)
  **Forgacs megfogalmazasa, es ez a hasznalhato resz:** *"Egy hianyzo adat feltunik.
  Ket egymasnak ellentmondo adat kozul viszont az valasztodik ki, amelyik eloszor ad
  kesz valaszt, es a mero ott abbahagyja."* O maga is ott hagyta abba -- a masodik
  szamsort a scriptje amugy is kiirta, csak nem nezte meg, mit jelent.
  Tehat a kerdes elott dontsd el, MELYIK kort kerdezed: a kartya letezeset
  (`created_at`) vagy a legutobbi hozzanyulast (`updated_at`). A ketto kozul egyik sem
  a MUNKA kora.

  Meg egy technikai reszlet ugyanonnan: kor-szamitasnal a `timedelta.days` LEFELE
  CSONKOL (3,7 nap -> 3). Hasznalj `total_seconds() / 86400`-at, egy tizedessel.

  **Ha azt kérdezed, mióta áll egy ügy, a kártya időbélyegei nem válaszolnak rá.** A
  kommentek és a leírás igen. Rokon: a "waiting oszlop vak folt" -- a 48 órás
  riasztás csak ÚJ blokkolót jelez, a legrégebbi kártyák örökre némák maradnak.

- Az `actor` mezőt **minden mozgatásnál** küldd el, különben nem derül ki ki mozdította.
- **A `POST /api/kanban` VÁLASZA CSAK `{"ok":true,"id":"..."}`** -- a beállított mezőket
  NEM adja vissza. (Anorak mérése, 2026-09-09: a válaszból `parent=None assignee=None
  status=None` jött, közben MIND A NÉGY mező átment; a kód `json(res, { ok: true, id })`,
  `src/web/routes/kanban.ts:381`.) Aki a POST válaszából ellenőriz, **hamis negatívot kap**.
  Az ellenőrzés a tábla újra-lekérdezése, nem a válasz. Ez nem hiba a kódban: a végpont
  szerződése ennyi, csak könnyű többet olvasni bele.
- **A `parent_id` ÁTMEGY a sima POST-on**, nem kell hozzá a breakdown-végpont. A kezelő a
  teljes body-t továbbadja (`kanban.ts:380`), és a `createKanbanCard` ismeri a mezőt
  (`db.ts:1882`, és az INSERT oszlopai között `db.ts:1893`). Mérve 2026-09-09 két kártyán
  (65c4da11, 14cef90d, mindkettő `parent_id=53925c5f`). Alkártyát tehát így is lehet
  csinálni -- és ez a helyes alak, ha egy ügy egy MÁSIK ágens kártyájának a kommentjében
  ülne: a komment nem nyilvántartás (nincs státusza, nem lezárható, egyetlen lekérdezés
  sem látja).
- Egy-kártya lekérdezés (`GET /api/kanban/ID`) **nincs**. A teljes listát kérd le és szűrd.
- **A `GET /api/kanban` SEMMILYEN query paramétert nem fogad.** Nem csak a szűrőket: mérve
  (2026-09-02) a `?limit=`, a `?status=` és egy kitalált `?foo=1` is egyformán `400`-at ad,
  a válaszban `known: []`. A szűrés a hívó dolga, Pythonban, a teljes listán.
  Ez a kapu a JÓ irányba téved: az elgépelt szűrő itt hibát ad, nem némán több sort
  (ellentétben a `/api/memories` esetével). A `400` tehát nem üzemzavar, hanem a válasz.
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

**A `GET /api/agents` sorában az ágens neve a `name` mező, NEM `id` és nem `agent_id`.**
A futás-állapot a `running` (bool) és a `runState`. 2026-09-06 16:03 két hívást
költöttem arra, hogy ezt kiderítsem: az `a.get('id')` mindenkire `None`-t ad, és egy
f-stringben ez `TypeError: unsupported format string passed to NoneType` -- a hibaüzenet
tehát a formázásra mutat, nem a rossz mezőnévre. Egy kész sor, ami működik:

```bash
python3 -c "
import json,urllib.request
t=open('/home/gabor/marveen/store/.dashboard-token').read().strip()
r=urllib.request.Request('http://localhost:3420/api/agents',headers={'Authorization':'Bearer '+t})
for a in json.load(urllib.request.urlopen(r)):
    print(a['name'], 'running=', a['running'])
"
```

**Ez a mezőnév egy KAPU, nem részletkérdés:** üzenetet csak FUTÓ ágensnek lehet küldeni,
tehát a ping-döntés ezen a listán múlik. Ha a nevek `None`-ként jönnek vissza, a lista
használhatatlan, és a `running` szűrés csendben mindenkit kihagy.

**Kódrészletet TARTALMAZÓ üzenetet fájlból küldj, ne parancssori argumentumként.**
2026-09-01, Forgács élő esete: egy backtickekbe tett parancs-példát a shell
parancsbehelyettesítésnek olvasott és **elnyelte**. A címzetthez egy csonka mondat
érkezett (`"python3 heredoc-kal: ."`), a helper pedig `OK id=243`-at adott vissza --
tehát a küldés sikeresnek látszott, csak a tartalom nem az volt, amit írtak. Ugyanaz
a néma-siker osztály, mint a `curl exit 0`. A biztonságos alak:

```bash
cat > /tmp/uzenet.txt <<'MSG'
... a szöveg, backtickekkel, $változókkal, bármivel ...
MSG
bash scripts/agent-msg.sh marveen CELPONT - < /tmp/uzenet.txt
```


Feldolgozás után a sor nem záródik magától, és a nyitott sor ismételt ébresztést okoz.

**Ami ismételten ébreszt, az `delivered`, NEM `pending`.** A `pending` az átmeneti beszúrási
állapot: a kézbesítés másodperceken belül `delivered`-re billenti (`src/db.ts:2369` írja be,
`:2397` billenti át). Mérve 2026-09-02 12:00-kor: a teljes táblában **0 sor** állt `pending`-ben,
miközben egyedül a marveen postaládájában **303** nyitott sor volt, **mind** `delivered`
(más nyitott státusz nulla). Az itt
korábban álló, `status='pending'`-re szűrő tömeges lezárás tehát **nulla sort írt volna át,
némán, sikeres futás látszatával** -- ugyanaz az osztály, mint a `?status=unread`.

A helyes alak (abszolút úttal, mert relatívval az ágens-mappából elhasal, és a **két óránál
régebbi** sorokra szűkítve, hogy egyezzen a szerver saját csendes ágával -- `shouldNotifyDelegator`):

```bash
python3 -c "
import sqlite3,time
db=sqlite3.connect('/home/gabor/marveen/store/claudeclaw.db'); now=int(time.time())
cur=db.execute('UPDATE agent_messages SET status=?, delivered_at=COALESCE(delivered_at,?), completed_at=? WHERE to_agent=? AND status=? AND created_at < ?',
           ('done',now,now,'marveen','delivered',now-7200)); db.commit(); print('lezarva:',cur.rowcount)"
```

A `print` nem dísz: enélkül a nulla és a háromszáz pontosan egyformán néz ki. A fenti alak
egy **másolaton** futott le (`cp` a scratchpadre), és 286 sort írt át; az éles fájl 303-on
maradt. A parancs tehát megy, csak nem futott élesben.

**És szándékosan nem futott.** 2026-09-01 este a flotta megállapodott, hogy **visszamenőleg
senki nem takarít** a postaládájában, mert a régi sorok a nap bizonyítékai. Ez a parancs
ahhoz kell, hogy egy TUDATOS döntés végrehajtható legyen, nem ahhoz, hogy magától
rendrakj. Nagy adagot darabolva.

Ez a közvetlen DB-írás **nem küld nyugtát** senkinek. Friss (két óránál újabb) sort ezért ne
így zárj le, hanem a `PUT /api/messages/<id>`-vel -- ott a küldő megkapja az eredményt.

### HÁROM LEZÁRÁSI ÚT VAN, ÉS MINDHÁROMNAK MÁS A HELYES ALAPÉRTELMEZÉSE

Ezt 2026-09-17-én Roxy kérte leírni, és jó oka van: a három út ugyanazt a mezőt állítja,
de nem ugyanarra az esetre való. Aki "egységesíteni" akarja őket, épp azt a különbséget
tünteti el, amiért így állnak.

| út | nyugta | mikor ez a helyes |
|---|---|---|
| közvetlen DB-írás (fent) | **nincs** | tudatos, nagy adagos takarítás; a küldőt nem érdekli |
| `PUT /api/messages/<id>` | **van**, ez az alapértelmezés | a lezárás MAGA az értesítés: nem küldtél külön üzenetet |
| `bash scripts/agent-msg.sh --close` | **nincs** (`notify: false`) | ugyanez a hívás küldi a tartalmat ÉS zárja a sort |

**A helper ágán ez nem megítélés kérdése, hanem a kód kényszeríti:** a `--close` mellett is
kötelező mind a három pozicionális argumentum (`${1:?from required}`, `${2:?to required}`,
`${3:?content required}`, a 95. sor). Tehát close-only hívás ezen az úton **nem létezik** --
aki zár, az ugyanabban a hívásban küldött is. Élesen próbálva a helper `from required`-del
áll meg. (Roxy mérése, 2026-09-17; Marveen visszamérte a kódból és éles próbával.)

**A nyers `PUT` ágon ezért marad ÉRTESÍTŐ az alapértelmezés**, és ez nem szerencse: ott a
lezárás tényleg lehet az egyetlen jelzés. Ha mégis külön üzenetben már válaszoltál, oda
kézzel kell odaírni:

```bash
-d '{"status":"done","result":"...","notify":false}'
```

**MIÉRT ÁLL EZ ITT ÉS NEM CSAK A CLAUDE.md-BEN.** A `notify` szóra ebben a fájlban
2026-09-17-ig **nulla** találat volt (`grep -c notify`, kis-nagybetű érzékeny; érzéketlenül egy, a `shouldNotifyDelegator` függvénynév miatt), miközben a másolható
lezárási parancs ITT áll. Ez a
flotta saját, nevesített hibája: a helyes tudás dokumentálva van, de a másolható PARANCS és
az ALAPÉRTELMEZÉS a rosszat viszi. Mérve ugyanaznap: Marveen egyetlen nap alatt **15**
duplikált nyugtát termelt, mind a helper `--close` ágán; Roxy postájának 46%-a (6/13) ilyen
volt.

Listázás (`GET /api/messages`), mérve 2026-09-02:

- Ismeretlen paraméter és ismeretlen `status`-érték egyaránt **400**, felsorolt `known` listával.
  Ez a kapu jó irányba téved, nem némán szélesít.
- **A `limit` 200-nál elvágódik**: `?limit=1000` is 200 sort ad (`messages.ts`,
  `Math.min(..., 200)`). Egy lista hossza tehát NEM darabszám.
  **2026-09-04 óta a válasz megmondja, ha vágott** (kártya c1fa4c60), fejlécekben, mert a
  törzs sima tömb marad és minden hívó úgy indexeli:

      X-Result-Truncated: true
      X-Result-Limit: 200
      X-Next-Before: 1702      <- ezzel kérd a következő lapot

  A `before` **ÜZENET-AZONOSÍTÓ, nem epoch** -- ez a sor korábban `before=<epoch>`-ot írt,
  és az soha nem volt igaz. És eddig a FLOTTA-SZINTŰ úton el volt fogadva, de nem hatott:
  `?limit=200` és `?limit=200&before=1600` bitre ugyanazt adta. Csak a `?agent=`-es ág
  lapozott. Most mind a kettő. Élesben visszamérve 2026-09-04 14:29-kor: `limit=200` ->
  min id 1702, `limit=200&before=1702` -> 1502..1701, tehát tényleg lép.

  Miért nem elég a plafont ismerni: a vágás **mindig a nap ELEJÉT** dobja el (a rendezés
  `created_at DESC`). Anorak 2026-09-03-án így vesztett el 218 sorból 18-at, 8,3%-ot, mind
  08:12 előttről -- és az eredmény teljes napnak látszott. Ha SZÁMOT akarsz, lapozz végig
  az `X-Next-Before`-ral, vagy kérd le ágensenként és uniózd.
- A `?status=pending` az egyetlen ág, ami megkerüli a `limit`-et -- és mivel a `pending`
  átmeneti, ez a gyakorlatban mindig üres listát ad. **A 200-as, üres válasz itt nem azt
  jelenti, hogy nincs postád.**

**A MASOLHATO PARANCS, ami tenyleg megmondja, mi var rad** (merve 2026-09-13 21:4x).
Ez azert all ITT, a figyelmeztetes ALATT: a fenti bekezdes 2026-09-02 ota helyesen
mondja, hogy a `?status=pending` mindig ures -- es 09-13-an EGY NAP ALATT KETTEN
futottunk bele ujra (Roxy es Marveen), mert a szabaly le volt irva, de a helyette
hasznalando parancs nem. Aki dolgozik, parancsot masol, nem bekezdest olvas.

```bash
python3 - <<'PY'
import json,urllib.request
AGENS="marveen"          # <- a sajat neved
tok=open('/home/gabor/marveen/store/.dashboard-token').read().strip()
r=urllib.request.Request(f"http://localhost:3420/api/messages?agent={AGENS}&limit=200",
                         headers={"Authorization":"Bearer "+tok})
d=json.load(urllib.request.urlopen(r))
if isinstance(d, dict) and 'error' in d:      # <- Roxy javaslata, 2026-09-13
    raise SystemExit(f"API HIBA: {d}")        #    a valasz `hint` mezoje is kiirodik
rows = d if isinstance(d, list) else d.get('messages', d.get('data', []))
nyitott=[x for x in rows if x.get('to_agent')==AGENS and x.get('status')!='done']
print(len(nyitott),'nyitott sor')
for x in nyitott:
    print(' ', x['id'], x.get('status'), x.get('from_agent'), (x.get('content') or '')[:70].replace('\n',' '))
PY
```

Harom mert reszlet, amiert epp igy:

- **`?agent=<nev>` MUKODIK, `?to=<nev>` NEM.** A `to` ismeretlen parameter -> HTTP 400.
  Mérve mind a kettore ugyanabban a percben.
- **Az `agent=` MIND A KET IRANYT adja** (amit kaptal ES amit kuldtel), ezert kell a
  `to_agent` szures kliens-oldalon.
- **A nyitott sorok kozott ott lesznek a `[Eredmeny]` nyugtak is.** Azokat NE zard le
  (lasd a fo CLAUDE.md-t) -- tehat a "nyitott" szam nem azonos a "teendo" szammal.

**A NULLA SOR HAROM KULONBOZO OKBOL JOHET, ES MIND A HAROM UGYANUGY NEZ KI.**
Merve 2026-09-13 este; a harmadikat Roxy talalta a sajat parancsan, miutan en az
elsovel magyaraztam a hibajat -- tehat meg az OK-ot is el lehet teveszteni utolag.

1. **Ervenyes, de sosem illeszkedo parameter-ertek** -- `?status=pending`. HTTP 200,
   ures tomb, semmi jelzes. (Lasd a fenti bekezdest.)
2. **Rossz MEZONEV a sajat, kliens-oldali szuresedben.** A visszaadott sorok kulcsai,
   merve:

       completed_at, content, created_at, delivered_at, from_agent, id, origin_note,
       parent_span_id, result, span_id, status, to_agent, trace_id

   **`to` es `from` NEM LETEZIK** -- a mezo `to_agent` es `from_agent`. Aki `r.get("to")`-ra
   szur, MINDEN sort kidob, es "0 nyitottat" kap egy tokeletes valaszbol.
3. **HTTP 400, amit a sajat egysorosod NYEL LE.** Ez a legrosszabb, mert a szerver
   PONTOSAN megmondja, mi a baj:

       {"error": "unknown query parameter", "unknown": ["to"],
        "known": ["agent", "status", "limit", "before"],
        "hint": "the mailbox filter is \"agent\"; \"agent_id\" and \"to\" are not read"}

   De a szokasos `curl -s ... | python3 -c "... d.get('messages', [])"` mintaban ez az
   objektum nem tartalmaz `messages` kulcsot, tehat a fallback ures listat ad, es a
   szkript kiirja: `0`. **A hibauzenet ott volt, es a sajat parancsunk dobta el.**
   Ha nullat kapsz, ELOSZOR a nyers valaszt ird ki, ne a szurest kezdd javitani.

   **AZ ORKODO KET SOR, ES A FENTI BLOKKBAN MAR BENNE IS ALL** (Roxy javasolta, 09-13):
   a `d.get('messages', ...)` fallback CSENDBEN nyeli le a hibat, ezert a kicsomagolas
   ELE kell egy `if isinstance(d, dict) and 'error' in d: raise SystemExit(...)`.
   Ez a harom okbol az 1.-et es a 3.-at azonnal MEGNEVEZI; a 2.-at (rossz mezonev a
   sajat szuresedben) nem fogja meg, arra a kulcs-lista kiirasa valo.
   **Negativ kontrollal merve, mindket iranyban:** a rossz `?to=roxy` alakon
   `API HIBA: {...}` + exit 1, a jo `?agent=roxy` alakon `sorok: 5` + exit 0.
   Egy orszem, ami csak igent tud mondani, nem orszem.

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
P=$(cat .claude/channels/telegram/bot.pid); echo "[$P]"; ps -p "$P" -o pid,etime --no-headers
```

Ha a `bot.pid` kiürült: `scripts/channel-watchdog.sh`. A channels service `systemctl
restart`-ja **tilos**, a közös tmux szervert vinné.

---

## Kép-olvasás idegen oldalról

Terv-oldalak a szabáslistát gyakran képben tartják. A képet **nem szűrjük**, mert
a megítéléshez el kellene olvasni, és az olvasás maga a kitettség. Helyette a
KIMENET szűk: az olvasó csak egy rögzített űrlapot tölthet ki, szabad szöveget nem
adhat vissza. Három lépés, sorrendben:

```bash
# 1. letoltes: a kaput MEGKERDEZI (nem masolja), atiranyitast NEM kovet,
#    a formatumot a fajl sajat bajtjaibol allapitja meg. SVG-t nem fogad el.
python3 scripts/fetch-image.py "https://oldal.tld/plan.jpg" --out store/muhely/kepek

# 2. olvasas: kulon sub-agens, egyetlen eszkozzel (Read). A valasza CSAK az urlap.
#    Agent({ subagent_type: "quarantine-image-reader",
#            prompt: `READ {"path":"...","nonce":"..."}` })

# 3. alak-ellenorzes GEPPEL, mielott barmi tovabbmegy:
python3 scripts/muhely/imgform.py valasz.json --nonce "$NONCE" --clean jovahagyott.json
```

- A 3. lépés kimenete (`--clean`) megy tovább, **soha nem** az olvasó nyers válasza.
- A `--nonce` **nem formalitás**: enélkül semmi nem köti a választ a te kérésedhez.
  Ha kihagyod, a script kiírja, hogy nem ellenőrizte. (2026-08-28: a reader
  kötelezővé tette a nonce-t, az imgform ismeretlen mezőként dobta ki, és a lánc
  egyetlen valódi képet sem engedett át. Egy éles futtatás derítette ki.)
- `unreadable` kötelező mező. A veszély nem a rossz olvasás, hanem a **kitalálás**:
  ha nincs hova tenni a bizonytalanságot, tippelni fog, és a tipp mérésnek látszik.
- **Két tömb, nem egy jelölő.** `rows` = nyomtatott szabáslista-sor (név, darab,
  két méret, mind kötelező). `measurements` = rajzi méret (egy szám, opcionális
  címke). Egy rajzon nincs darabszám és nincs második méret, tehát ha rows-ba
  kényszerítjük, a reader kitalálja őket vagy eldobja a sort. 2026-08-28-án
  eldobta: olvasható lapról nulla eredmény jött. A rajzi méretből **javaslat**
  lehet, végleges szabáslista nem.
- Mérés ugyanaznap: a rajzról leolvasott számok HELYESEK voltak, és nem a kész
  alkatrész méretei (polc 506 -> 499,5; fix polc 1054 -> 1052; ajtó 430 -> 374).
  A szabáslista a rajzból **számolható**, de a levezetés (ráhagyás, vasalat,
  élzáró) nélkül nem az. A félreolvasott méret számnak látszik, nem hibának.
- **A lánc határa: legjobb esetben is azt adja vissza, ami A LAPON van, nem azt
  ami IGAZ.** Élő eset 2026-08-28: a reader tökéletesen olvasta a Lyford Road
  elevationt, de az a lap nem lett javítva, tehát a rajta lévő ajtóméret elavult.
  Ezt semmilyen olvasó nem látja, és revíziószám sem dönti el (az a P3 tartalmilag
  azonos volt a P2-vel). A rajz állítás egy időpontról, nem tény.
- A letöltés beleszámít a napi forrás-keretbe (`ALLOWED_IMAGE` sor a naplóban).
- Teszt: `bash scripts/__tests__/image-pipeline.test.sh` (éles telepítésen is fut).

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

Index-frissítés szerkesztés után. **A SAJÁT mappádra, LITERÁLIS úttal**:

```bash
bash /home/gabor/marveen/scripts/skill-index.sh /home/gabor/marveen/agents/<sajat-neved>
```

A főágens a telepítés gyökerét adja meg (`/home/gabor/marveen`).

**NE `$PWD`-t használj.** Kézenfekvőnek tűnik, és egy fél óráig ez is állt itt, de a
munkakönyvtár a session közben elcsúszik: egy ügynök belép a saját projekt-mappájába, vagy
`cd`-zik a repo gyökerébe, mert onnan futnak a szkriptek. Akkor `$PWD` MÁS ágens indexét
írja, sikeresnek látszó kimenettel. Ez ugyanaz a hibaosztály, amit a ledger kódja
LEDGERCWD828 néven már névvel hordoz: a cwd változik a sessionön belül, ezért identitást
NEM vezetünk le belőle. (Cody és Forgács mérte ki, 2026-09-01.)

A régi példa itt `/home/gabor/marveen`-t adott ÁGENS-MAPPAKÉNT: az a FŐÁGENS mappája, tehát
egy sub-ágens, aki azt másolta, nem a sajátját frissítette, hanem a főágensét.

A szkript emellett **felfelé normalizálja** a kapott utat: ha az a telepítésen belülre esik,
`<install>/agents/<id>`-ig vágja vissza. Ez véd az elgépelt alkönyvtár ellen, de NEM véd az
ellen, ha a repo gyökeréből futtatod: onnan a főágens indexe íródik, mert az út önmagában
nem árulja el, ki vagy.

**Argumentum NÉLKÜL a KÖZÖS indexet írja** (`~/.claude/skills/.skill-index.md`), ami a
teljes flottára hat és jóváhagyás-köteles (`skill_patch`). Ne fusson így reflexből:

```bash
bash /home/gabor/marveen/scripts/skill-index.sh   # KÖZÖS index -- csak jóváhagyással
```

Két buktató, mindkettő mért (2026-09-01):
- A generátor korábban **bájtra** vágta a leírásokat 120-nál. Egy ékezetes betű közepén
  elvágva a fájl érvénytelen UTF-8 lett, és a gép keresője (ugrep) ilyenkor binárisnak
  ítéli és **némán átugorja**: nulla találat, exit 1, semmi kiírás. Javítva, de ha egy
  keresés gyanúsan üres: `file <fájl>`, és ha nem UTF-8 text, akkor `grep -a`.
- Öt ügynökből háromnál az ágens-szintű index **nem is létezett**, mert a skill írása után
  senki nem futtatta a generátort. A tünet ugyanaz, mint a sérült fájlé: nulla találat.
  Ezért a skill-index frissítése a skill-írás RÉSZE, nem külön lépés.

---

## Kódváltoztatás ezen a gépen

- **A tesztkészlet szándékosan NEM fut éles telepítésen** (`assert-not-live-install`),
  mert a `store/` tartalmát írná felül. Ne kerüld meg.
- Marad: `npx tsc --noEmit`, `node --check web/app.js`, és éles ellenőrzés.
- **Mondd meg a gazdának és írd bele a commitba, hogy teszt nem futott.**
- Szerverkód után: `npm run build`, majd
  `systemctl --user restart marveen-dashboard`, majd curl-lel várd meg a 200-at.
  Ez biztonságos, és NEM azonos a channels újraindításával.

### Git: EGY munkakönyvtár van, a főágens és a sub-ágensek közösen használják

**Az ág itt nem izolál, csak címkéz.** Eldönti, milyen név alá kerülnek a commitok,
de a fájlokat ugyanabban a mappában írja mindenki. Ezért a "ne commitolj a mainre"
szabály nálunk **nem védelem, hanem csapda**: ott ahol mindenkinek saját checkoutja
van, az ág megvédi a másikat, itt viszont csak annyit ér el, hogy a main csendben
lemarad, miközben mindenki azt hiszi, óvatos volt.

- **Közös checkoutban a mainre commitolunk.** Aki ágat NYIT, az szól mindenkinek.
- Élő eset 2026-08-28: egy ágnyitás után négy commit ment az ágra abban a hitben,
  hogy a mainre megy. A main közben egy ismert hibás változaton állt.
- **Ág-mutatót léptetni ÍGY kell**, ha más ágens is dolgozik a mappában:

  ```bash
  git log --oneline main..HEAD && git log --oneline HEAD..main   # elobb merd le
  git merge-base --is-ancestor main HEAD && git branch -f main HEAD   # ff, mutato
  ```

  A `git branch -f` a **mutatót** lépteti és a munkakönyvtárhoz hozzá sem nyúl.
  A `checkout` viszont kirántja a fájlokat a másik ágens keze alól: azt **ő végezze**,
  amikor tiszta ponton van.
