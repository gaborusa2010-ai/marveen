#!/usr/bin/env python3
"""A kozos MEMORY.md merete UGY, AHOGY A BETOLTO LATJA.

MIERT LETEZIK EZ A SZKRIPT (2026-09-11): egyetlen delutan HAROM agens merte meg
ugyanezt a fajlt, es MINDHARMAN a NYERS sorszamot adtak meg (232 / 233 / 238),
mert a HTML-fejlecet is beleszamoltak. A betolto a fejlecet KISZEDI, mielott
szamolna -- tehat a valodi szam 210 volt, es a tulcsordulas tizedakkora, mint
amit jelentettunk.

A helyes szamitas OTT ALL a MEMORY.md fejlecben ES a memoria-index-ket-plafon lap
elso indexsoraban is, tehat BETOLTODO helyen. Megsem allitott meg senkit. Egy
leirt szabaly nem fog meg egy reflexet; egy parancs igen. Ezert szkript ez, nem
bekezdes.

A ket plafon (Anorak merese a harness 2.1.267 kodjabol, 2026-09-10):
  200 SOR    -- vagasnal az ELSO 200 marad, a fajl VEGE esik ki
  25 000 KARAKTER (JS .length), NEM bajt -- az ekezetek miatt a ketto elter
Mindketto a HTML-fejlec NELKUL szamol.
"""
import re, sys, os, time

def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_SLUG = project_slug(REPO)
P = os.path.expanduser(os.path.join('~/.claude/projects', PROJECT_SLUG, 'memory/MEMORY.md'))
SOR_PLAFON, KAR_PLAFON = 200, 25000

def main(p=P):
    if not os.path.exists(p):
        print(f'NINCS ILYEN FAJL: {p}'); return 2
    s = open(p, encoding='utf-8').read()
    # AZ MTIME MINDIG KIIRODIK -- de ONMAGABAN NEM ELEG, es ezt Margo mutatta meg.
    # 2026-09-11-en KET TUKROS hiba tortent ugyanazon a tengelyen:
    #   Marveen: IDOBELI kulonbseget minositett MODSZERHIBANAK (Forgacs 24 207-e)
    #   Margo:   MODSZER-kulonbseget minositett IDOBELI novekedesnek (Roxy 216-a)
    # Mindketten CSAK AZ EGYIK oldalt neztuk meg, es a magyarazat pontosan arra az
    # oldalra esett. Ezert all a kiirasban mind a ketto.
    #
    # ES A ZARLAT, ami mindkettonket megcafolt: a 24 207 valodi oka EGYIK SEM volt,
    # hanem egy ESCAPE-HIBA Forgacs merokodjaban -- `'\\n'.join(sorok)` a ketkarakteres
    # "\n" SZOVEGGEL fuzott ossze, nem ujsorral. 210 sor, 209 elvalaszto, soronkent egy
    # extra karakter: 23 998 + 209 = 24 207. Reprodukalva, beture.
    # Amit ebbol meg kell tartani: EGY SZAMHOZ KET HIHETO OKOT talaltunk, es egyik sem
    # volt a valodi. Mindketto ELVITTE a figyelmet a harmadiktol -- a MERO SAJAT KODJATOL.
    # Ha ket hipotezis kozul valasztasz, nezd meg, hogy a HARMADIK nem maga a merod-e.
    mt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p)))
    print(f'  FAJL: {p}')
    print(f'  MTIME: {mt}')
    print('  KET ELTERO SZAMNAL MINDKETToT VESD OSSZE: az IDOT (ez a sor) ES a MODSZERT.')
    print('  Ha csak az egyiket nezed meg, a magyarazat arra az oldalra fog esni.')
    print()
    nyers_sor = len(s.splitlines())
    nyers_kar = len(s)
    # a betolto: markdown-lex, majd HTML-komment strip, es csak azutan szamol
    n = re.sub(r'<!--.*?-->', '', s, flags=re.S)
    # ROXY MERESE, 2026-09-11: a SOR-szamlalas eddig EGGYEL KEVESEBBET adott a
    # valosagnal, mert az URES SOROKAT eldobta. A betolto NEM dobja el oket: a
    # HTML-komment SORAIT veszi ki, a tobbit (ures sort is) szamolja.
    # KET FUGGETLEN BIZONYITEK ugyanarra a 211-re:
    #   (a) Roxy a SAJAT betoltott kontextusabol olvasta ki (194 indexsor + 1 ures
    #       sor + 16 parkolt = 211), es a harness sajat figyelmeztetese szo szerint
    #       "MEMORY.md is 211 lines"-t mondott;
    #   (b) Marveen ugyanezt reprodukalta negy szamlalo-valtozattal: csak ez a ketto
    #       adja a 211-et (210 / 213 / 211 / 211), a regi modszer 210-et.
    # MIERT SZAMIT EGY SOR: a verdikt a PEREMEN dol el. A regi modszer szerint a
    # vonal ala tett 16 sor MIND kint van; valojaban OT kozuluk MEG BETOLTODIK.
    # Aki a regi szammal szamolt felszabadult helyet, ottel tobbet szamolt.
    _l, _ki, _benne = s.splitlines(), set(), False
    for _i, _x in enumerate(_l):
        if '<!--' in _x: _benne = True
        if _benne: _ki.add(_i)
        if '-->' in _x: _benne = False
    betolto_sorok = [x for i, x in enumerate(_l) if i not in _ki]
    while betolto_sorok and not betolto_sorok[0].strip(): betolto_sorok.pop(0)
    while betolto_sorok and not betolto_sorok[-1].strip(): betolto_sorok.pop()
    torzs = [l for l in n.splitlines() if l.strip()]
    regi_modszer = len(torzs)
    sor = len(betolto_sorok)
    # A KARAKTER-SZAMOT NEM TUDJUK BETURE: a betolto pontos ures-sor- es
    # komment-kezelese nincs kimerve. Harom hihetô valtozat kozott 4 karakter a
    # szoras, de egy MASIK agens 2026-09-11-en 24 207-et mert ugyanerre a fajlra,
    # es EGYIK valtozattal sem reprodukalhato (23 998 / 24 000 / 24 002 / 24 447).
    # Ezert a NAGYOBBAT vesszuk. 2026-09-18 OTA EZ MAR NEM A VERDIKT SZAMA, csak
    # tajekoztato: a verdikt a `kar_vagott`-on all (lasd a lenti blokkot).
    valtozatok = {
        'ures sorok nelkul': len('\n'.join(torzs)),
        'ures sorokkal': len(n.strip()),
    }
    kar = max(valtozatok.values())
    # A HARNESS SORRENDJE: ELOSZOR SOR, AZTAN KARAKTER -- es a karakter-plafont a MAR
    # LEVAGOTT szovegre nezi. Anorak jelezte 2026-09-18 23:2x-kor, Marveen a FUTO
    # binarisbol (2.1.273) visszamerte, dekompilalva:
    #     let S = h ? r.split('\n').slice(0, nO).join('\n') : r;
    #     if (S.length > _z) { ... S = S.slice(0, V > 0 ? V : _z) }
    # (`nO=200`, `_z=25000`, mindketto a binarisbol; `x4e` szerint a `byteCount`
    #  valojaban `n.length`, tehat KARAKTER es nem bajt.)
    #
    # AMIT EZ ELRONTOTT: a verdikt eddig a TELJES torzsre merte a karaktert. Amikor van
    # sor-tulcsordulas -- nalunk ez ALLANDO, a vonal alatti parkolt szakasz miatt --, a
    # karakter-riasztas MINDIG hamis, mert a 200. sor utani szoveg mar ki sem kerul a
    # betoltobe. 2026-09-18 23:2x-kor 220 karakter "tulcsordulast" jelzett, kozben a
    # ténylegesen betolto elso 200 sor 1600+ karakterrel a plafon ALATT allt.
    betolto_szoveg = '\n'.join(betolto_sorok)
    vagott = '\n'.join(betolto_sorok[:SOR_PLAFON]) if sor > SOR_PLAFON else betolto_szoveg
    kar_vagott = len(vagott)
    # NEM a betolto viselkedese, hanem a GYAKORI MEROHIBA: csak az ELSO HTML-kommentet
    # szedni ki. 2026-09-11-en ketten futottak bele (Margo masodik merese 222 sor,
    # Forgacs 24 207 karakter) -- a fajlban KET komment van, a fejlec es a hatrasorolast
    # magyarazo elvalaszto. Kiirjuk, hogy felismerheto legyen, de a VERDIKTBE NEM szamit:
    # egy merohibat modellezo szam hamis biztonsagi tartaleknak latszana.
    merohiba = len('\n'.join(l for l in re.sub(r'<!--.*?-->', '', s, count=1, flags=re.S).splitlines() if l.strip()))
    print(f'  NYERS (amit a wc -l ad):      {nyers_sor:4d} sor | {nyers_kar:6d} karakter')
    print(f'  A BETOLTO SZEMEVEL:           {sor:4d} sor | {kar:6d} karakter')
    # A KULONBSEGET SZAMOLJUK, NEM ALLITJUK. Az elso alak "EGGYEL KEVESEBB"-et irt ki
    # fixen -- az a KOZOS MEMORY.md-re igaz volt es masra nem (a HANDOFF.md-n 24 a
    # kulonbseg). Egy fix szoveg, ami szamot allit, ugyanaz a csapda, mint a kitalalt
    # plafon: meresnek latszik. (Marveen, 2026-09-11, sajat hiba a javitas kozben.)
    _kul = sor - regi_modszer
    print(f'  (a regi, ures-sor nelkuli modszer: {regi_modszer} sor -- {_kul} sorral kevesebb; Roxy 09-11)')
    print(f'  a fejlec, amit eldob:         {nyers_sor-sor:4d} sor')
    print()
    print('  KARAKTER-VALTOZATOK (a betolto pontos ures-sor-kezelese nincs kimerve):')
    for nev, v in sorted(valtozatok.items(), key=lambda kv: kv[1]):
        print(f'    {v:6d}  {nev}' + ('   <- a TELJES torzs, tajekoztato' if v == kar else ''))
    print(f'    {merohiba:6d}  HA CSAK AZ ELSO KOMMENTET SZEDED KI -- ez MEROHIBA, nem a betolto')
    print()
    sor_tul, kar_tul = sor - SOR_PLAFON, kar_vagott - KAR_PLAFON
    print(f'  SOR-plafon      {SOR_PLAFON}: ' + (f'TULCSORDULAS {sor_tul} sor' if sor_tul > 0 else f'rendben, {-sor_tul} tartalek'))
    print(f'  KARAKTER-plafon {KAR_PLAFON}: ' + (f'TULCSORDULAS {kar_tul} karakter' if kar_tul > 0 else f'rendben, {-kar_tul} tartalek')
          + f'   [a TENYLEG betolto {min(sor, SOR_PLAFON)} soron merve: {kar_vagott}]')
    if sor_tul > 0 and kar > KAR_PLAFON:
        print(f'  (a TELJES torzs {kar} karakter, tehat a plafon folott -- de ez NEM vag le semmit:')
        print(f'   a harness eloszor a sort vagja, es a karakter-plafont a maradekra nezi. Annyi a')
        print(f'   hatasa, hogy a figyelmeztetes SZOVEGE "sor ES karakter" alakot vesz fel.)')
    # SZERZO-MEZO (Forgacs javaslata, 2026-09-11). A fejlec 3. szabalya keri
    # (`metadata.author`), de a fejlecet a betolto KISZEDI -- futas kozben senki
    # nem latja, es aznap 23 erintett lapbol 2-n allt csak. Egy szabaly, amit csak
    # egy nem latszo fejlec mond ki, nem all meg egy reflexet; egy futtatasban
    # megjeleno szam igen. NEM verdikt: a kilepesi kodot nem befolyasolja, mert a
    # hianyzo szerzo nem betoltesi hiba -- csak lathatova tesszuk.
    mappa = os.path.dirname(os.path.abspath(p)) or '.'
    lapok = [n for n in sorted(os.listdir(mappa))
             if n.endswith('.md') and n != os.path.basename(p)]
    nincs_szerzo = []
    for n in lapok:
        try:
            fej = open(os.path.join(mappa, n), encoding='utf-8').read(1200)
        except OSError:
            continue
        if not re.search(r'^\s*author:\s*\S', fej, re.M):
            nincs_szerzo.append(n)
    print()
    print(f'  SZERZO-MEZO: {len(lapok)-len(nincs_szerzo)}/{len(lapok)} lapon all '
          f'(`metadata.author`) -- {len(nincs_szerzo)} lapon HIANYZIK.')
    print('  Visszamenoleg NEM potoljuk: az talalgatas lenne. Az UJ lapokra kotelezo.')

    # ELERHEToSEG (Anorak jelezte 2026-09-18 23:2x, Marveen fuggetlenul visszamerte:
    # mindketten 13-at kaptunk ugyanarra a fajlallapotra). NEM VERDIKT, csak lathatova
    # teve -- ugyanaz az indok, mint a SZERZO-MEZO sornal.
    #
    # MIERT NEM ELEG AZT MERNI, HOGY "HANY LAPRA NEM MUTAT INDEXSOR": Anorak elso merese
    # 28-at adott igy, es hibas volt. Egy lap akkor is eloljon, ha egy MASIK lap
    # wikilinkjen at vezet hozza ut -- tehat a kerdes nem az indexsor, hanem a LANC.
    # A konnyen merheto mennyiseg beallt a nehezebb kerdes helyere. [[meres-a-rossz-kerdesre]]
    #
    # A KIVETELNEL EZ A SZABALY (Anorak javaslata, elfogadva): ha egy lap kikerul az
    # indexbol, a kivevo tegyen ra EGY bemeno wikilinket egy rokon, BETOLTODO laprol.
    # Az indexet ez nem noveli -- a link a lapok torzseben all.
    import collections
    _osszes = set(lapok)
    _kozv = {m for m in re.findall(r'\(([^()]+\.md)\)', '\n'.join(betolto_sorok[:SOR_PLAFON]))
             if m in _osszes}
    _elert, _q = set(_kozv), collections.deque(_kozv)
    while _q:
        _cur = _q.popleft()
        try:
            _t = open(os.path.join(mappa, _cur), encoding='utf-8').read()
        except OSError:
            continue
        for _f in ([w.strip() + '.md' for w in re.findall(r'\[\[([^\]]+)\]\]', _t)]
                   + re.findall(r'\(([^()]+\.md)\)', _t)):
            if _f in _osszes and _f not in _elert:
                _elert.add(_f); _q.append(_f)
    _arva = sorted(_osszes - _elert)
    print()
    print(f'  ELERHEToSEG: {len(_elert)}/{len(_osszes)} lap eloall a betolto {min(sor, SOR_PLAFON)} '
          f'indexsorbol, kozvetlenul vagy wikilinken at.')
    if _arva:
        print(f'  {len(_arva)} lapra SEMMILYEN uton nem lehet eljutni:')
        for _a in _arva:
            print(f'    {_a}')
        print('  Javitas: EGY bemeno [[wikilink]] egy rokon, BETOLTODO laprol. Nem indexsor.')

    if sor_tul > 0:
        print()
        print(f'  AMI NEM TOLTODIK BE (a {SOR_PLAFON}. sor utan), a fajl VEGEROL:')
        for i, l in enumerate(betolto_sorok[SOR_PLAFON:], SOR_PLAFON+1):
            print(f'    {i:4d} {l[:88]}')
    return 1 if (sor_tul > 0 or kar_tul > 0) else 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else P))
