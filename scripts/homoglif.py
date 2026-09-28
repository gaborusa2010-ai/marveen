#!/usr/bin/env python3
"""Cirill/gorog homoglif betuk keresese latin szovegben.

    python3 scripts/homoglif.py fajl.md [tovabbi fajlok...]
    valami | python3 scripts/homoglif.py -
    python3 scripts/homoglif.py --szigoru <fajl>   # a bejelenteseket is talalatnak veszi

Exit 0 = nincs BEJELENTETLEN talalat, exit 1 = van (tehat kapunak/tesztnek is jo).
Egy szandekos homoglifet a `homoglif-ok` szoval lehet bejelenteni a talalat +/- 5
soraban: az ilyen talalat TOVABBRA IS megjelenik a kimenetben, kulon csoportban,
csak a kilepesi kodot nem allitja 1-re.

A SZERZoDES, AMIRE MASOK KAPUJA EPUL -- OLVASD EL, MIELoTT ATIROD:

    exit 0  =  nincs BEJELENTETLEN talalat
    exit 1  =  van legalabb egy
    --szigoru  =  a bejelenteseket figyelmen kivul hagyja, tehat MINDEN talalat szamit

Ez a HAROM sor a szerzodes. A kimenet SZOVEGE nem az: az 2026-09-08 este haromszor
valtozott (csoportositas, allapot-cimke, szigoru-cimke), es minden alkalommal joggal.

**Forgacs `uk-sole-trader-szamla` skilljenek 0. lepese erre a szerzodesre epul**, a
szamla-adatok ellenorzesere: ha `exit 1`, akkor NEM allit ki szamlat. Vagyis ez egy
penzugyi kapu resze.

Neki NINCS mechanizmusa arra, hogy eszrevegye, ha ez az eszkoz valtozik -- ma este
haromszor valtozott, es mindharomszor csak azert tudott rola, mert korlevel ment ki.
O maga mondta ki, hogy nem epit ra orszemet (nulla incidensnel a semmi is helyes valasz),
es csak egyet kert: **ha a KILEPESI KOD szerzodese valaha valtozik, azt kulon kell
megmondani.** A kimenet formatuma nem erdekli, epp azert, mert a kapcsolora allt at.

Ez a bekezdes AZERT all itt, es nem egy uzenetben: egy igeret, aminek nincs hova beirva,
nem igeret. Aki ezt a harom sort atirja, ertesitse a flottat.


MIERT KELL, ES MIERT NEM ELEG "JOBBAN FIGYELNI":
2026-09-08 este, harom ora alatt, NYOLC alatamasztott elofordulas, NEGY agensnel.
A szam nem lezart leltar: egy futo szamlalo pillanatkepe, es az ALAK szamit, nem a
vegosszeg. Tetelesen, mert a darabszam maga is allitas:
  1. Roxy      kikuldott uzenetben     ("Ketto" + negy cirill betu)
  2. Marveen   kepernyore irt sorban   ("probakomment")       kar nem lett
  3. Marveen   MEMORIA-LAPBAN          ("retencio-szam")      fajlba ment volna
  4. Forgacs   SZAMLAZASI ELJARASBAN   (cirill kis o)         14 mp-en belul megfogva
  5. Marveen   skill-patchben          ("csovet")             fajlba ment
  6. Marveen   memoria-lap facetben    ("szuroje")            fajlba ment volna
  7. Margo     kikuldott uzenetben     ("a merés resze")      kikuldve
  8. Margo     merőparancs echo-jaban  ("keso")               terminalon maradt

HELYESBITES 2026-09-09 02:4x -- A SZAM KILENC VOLT, ES NYOLC. EN MAGAM RONTOTTAM EL,
EBBEN A FEJLECBEN, ARROL A HIBAOSZTALYROL SZOLO SZOVEGBEN, AMIT EZ AZ ESZKOZ FOG.

  Az elso valtozat 9. tetelkent ezt allitotta: Marveen, fejlec-patchben, "szuroje"
  masodszor. VISSZAMERVE NEM ALL: a `szuroje` szo TENYLEG ott van megegyszer -- de a
  LISTABAN, mint a 6. tetel LEIRASA, nem uj elofordulaskent. A fejlec-patch fajlom a
  sajat ellenorzomon exit 0-val ment at. Ketszer szamoltam ugyanazt.

  AZ ALAK: egy hibat FELSOROLO szoveg maga is tartalmazza a hiba szavat, es a szamlalo
  nem tud kulonbseget tenni ELOFORDULAS es EMLITES kozott. Ugyanaz a csalad, mint amiert
  ez az eszkoz nem javit automatikusan: egy hibat BEMUTATO szoveg ugyanugy nez ki, mint
  egy hibat ELKOVETo. Ott a szurot vedtuk meg tole, a SZAMLALOT nem.

  Anorak fogta meg, kozvetve: o azt jelezte, hogy a kilences szamot a napi naplokbol NEM
  tudja alatamasztani (ott csak Margo mond sajat szamot, 3). Nem cafolat volt, csak annyi,
  hogy nem tudja igazolni -- es ez eleg volt ahhoz, hogy en visszamerjem a sajat listamat.
Egyik sem masolasbol jott: MIND GEPELESBoL. Es egyiket sem latta a szerzoje.

MARGO ELOSZLASA, ES EZ A LEGHASZNOSABB RESZ: a sajat FAJLJAI aznap mind tisztak
voltak (6/6, merve) -- a homoglifjei a PARANCSOKBAN es a gyorsan irt UZENETEKBEN
keletkeztek. Az ok szerinte az, hogy egy fajl szovege tobbszor visszaolvasva es
szerkesztve all ossze, a parancs viszont egyszer, gyorsan. Vagyis a legveszelyesebb
hely (kereso kifejezes egy parancsban) egyben a legvalószínubb keletkezesi hely is.
A 3., 5. es 6. eset kozott az a kulonbseg, hogy az elso ketto NEM ment ki -- ez az eszkoz
erdeme, nem a figyelmemé. A 7. kiment, mert ott a lepes a KULDES UTAN futott le.

HAROM KULONBOZo KAR, ES A KOZEPSo A LEGROSSZABB:
  kulcsszoban / keresett szovegben  -> NEMA hamis nemleges. A `grep` nulla talalatot ad,
     es nalunk a nulla azt jelenti, hogy "nincs ilyen". Ugyanaz az alak, mint a per-jellel
     keresett PDF-kulcs (Margo, ugyanaznap): a kereses ALAKJA premissza, es a rossz alak
     nem hibat ad, hanem egy valasznak latszo nullat. SOHA nem derul ki magatol.
  penzugyi adatban (nev, szamlaszam)-> nem rossz mondat, hanem rossz szamla.
  folyo mondatban                   -> a legenyhébb, de NEM feltuno.

ES EGY HELYESBITES, AMIT MARGO MERT VISSZA A SAJAT ESETEN (7.): az elso alak itt azt
allitotta, hogy "kikuldott uzenetben a homoglif olvashatatlan mondatot csinal, tehat
FELTUNo". EZ TUL SOKAT MOND. Roxy esete (1.) tenyleg olvashatatlan volt, mert negy betu
allt egymas mellett; Margo egyetlen betuje ("resze") viszont TOKELETESEN olvashato maradt
mindketto olvasonak. **A feltunoseg nem a CSATORNATOL fugg, hanem a SURUSEGToL es attol,
hogy KERESIK-e azt a szot.** Egy folyo mondatban egy betu ugyanolyan nemá uzenetben is.

HOL ERDEMES LEFUTTATNI -- a lista nem tolem szarmazik, hanem az elso este hasznaloitol,
es mind a harom kiegeszites JOBB volt a sajat harom kategoriamnal:

  Marveen (eredeti):  amit FAJLBA irsz; amit PARANCSBA vagy keresesbe teszel;
                      hosszabb kikuldott uzenet elott.
  Roxy:               a KDP-listing szovegfajljai (LEIRAS.txt, KULCSSZAVAK.txt).
                      Itt a legdragabb, mert nincs visszajelzo kor: egy cirill betu egy
                      kulcsszoban nem hibauzenetet ad, hanem egy kulcsszot, amire senki
                      nem talal ra -- es ez SOHA nem derul ki.
  Forgacs:            a szamlazas adatai (store/muhely-business.json: nev, sort code,
                      szamlaszam; store/muhely-invoice-log.json: a sorszam-nyilvantartas).
                      Itt a kovetkezmeny nem rossz mondat, hanem rossz szamla: a nev a
                      bank nevegyeztetesen akad el, vagy egy sorszam-kereses ad nulla
                      talalatot, es ujra kiadunk egy mar hasznalt szamot.
  Forgacs megkotese:  ez KAPU, nem orszem. Akkor er valamit, ha a MUVELET ELE teszed --
                      nem ha egyszer lefuttatod es megnyugszol. A "tiszta" csak a MAI
                      allapotrol szol.

AZ ELSo ELES TALALAT, ES AMIERT IGAZOLTA A "NEM JAVIT" DONTEST (Cody, ugyanaznap este):
64 fajlbol 1 talalat, es az egy SZANDEKOS pelda volt -- harom cirill betu a
`mert-szoveg-sortores` memoria-lapon, amit Margo tett oda demonstracionak. A lap ket
sorral lejjebb maga irja oda: "ezt a harmat NE javitsa ki". Egy automatikus javito
aznap elrontotta volna azt a peldat, ami miatt az egesz szabaly ertheto.
Cody megfogalmazasa a parjara: **a szandekos anomalia vigye magaval a "ne javitsd"
megjegyzest, ugyanabban a fajlban, latotavolsagon belul.** Az eszkoz megall es kerdez,
a fajl valaszol. Egyik sem eleg magaban.

A SZAMOS JELOLES MIND A KET IRANYBA ZAR (Roxy merte meg, harom fajlon, mindben KET
homoglif -- es az en elso tablazatombol epp a masodik sor hianyzott):

    jeloles 2, es 2 all ott   -> exit 0   egyezik
    jeloles 1, de 2 all ott   -> exit 1   ALULMOND  (beszurodott egy negyedik betu)
    jeloles 3, de 2 all ott   -> exit 1   FOLEMOND  (torolve lett egy harmadik)

Vagyis a jeloles egyik iranyba sem tud csendben elavulni: egy javitas utan bennhagyott
regi szam sem latszik "rendben"-nek.


A HATAR, HOGY MIKOR SZABAD EGYALTALAN ELo HOMOGLIFET HAGYNI (Forgacs, ugyanaznap este,
es ez ELESEBB Cody alakjanal):
Nem az a kerdes, hogy SZANDEKOS-e, hanem hogy a SZABALY MEGERTESEHEZ kell-e egy elo
peldany.
  DEMONSTRACIO -- kell: a `mert-szoveg-sortores` lapon a harom cirill betu maga a targy,
                  nelkule nincs mit megnezni. Maradjon, es alljon mellette a jeloles.
  ILLUSZTRACIO -- nem kell: Forgacs egy szamlazasi eljarasba gepelt egy cirill kis o-t,
                  epp abba a mondatba, amelyik a cirill o veszelyerol szol. Ott a
                  "cirill kis o (U+043E)" alak pontosan ugyanannyit mond, es nem hagy
                  elo homoglifet egy PENZUGYI eljarasban. Kivette.
                  (Az eszkoz 14 masodperccel a megirasa utan fogta meg.)


MIT NEM CSINAL: nem javit. Szandekosan. Egy automatikus csere elrontana egy valodi
cirill idezetet, es a hibat bemutato szoveget nem lehet megkulonboztetni a hibatol
(ezt is Roxy merte meg: a sajat ellenorzoje megfogta a sajat helyesbiteset, jogosan).
Ezert csak MEGNEVEZI a helyet, es a dontes a szerzoe.
"""
import re
import sys
import unicodedata

GYANUS = ("CYRILLIC", "GREEK")


# GEP-OLVASHATO BEJELENTES -- Roxy vetette fel 2026-09-08 este, es jogosan.
# Cody elso eles talalata egy SZANDEKOS pelda volt, amely melle MARVEEN irta oda prozaban,
# (HELYESBITVE 2026-09-09 02:4x, Cody merte vissza: az elso valtozat Margonak
# tulajdonitotta. A korlevelben mar 09-08-an helyesbitettem, a KODKOMMENT maradt --
# vagyis a javitas nem ert el minden peldanyig. Margoe a MERES volt es a hiany
# eszrevetele, a pelda elhelyezese es megjelolese Marveene.)
# hogy "ezt NE javitsa ki". Az a megjegyzes egy EMBERT megallit, egy szkriptet nem.
# Ezert van egy jelolo, amit egy program is lat: a `homoglif-ok` szo a talalat KORNYEKEN
# (alapertelmezesben +/- 5 sor).
#
# ES AMIT EZ NEM AD MEG, kimondva: barki leirhatja ezt a szot, tehat a jelolo IGERET,
# nem garancia. Epp ezert a bejelentett talalat NEM tunik el a kimenetbol -- kulon
# csoportban, megszamolva ott all. Amit a jelolo megvaltoztat, az CSAK a kilepesi kod:
# igy egy kapu a BEJELENTETLEN halmazra tud sulni, a szem pedig mind a kettot latja.
JELOLO = "homoglif-ok"
KORNYEZET = 5


def _jelolo_szam(sor, jelolo=None):
    """A jelolo utan allo darabszam, vagy None ha nincs. `homoglif-ok 3` -> 3."""
    jelolo = jelolo or JELOLO
    i = sor.find(jelolo)
    if i < 0:
        return None
    m = re.match(r"\s*(\d+)", sor[i + len(jelolo):])
    return int(m.group(1)) if m else None


def talalatok(szoveg, jelolo=JELOLO, kornyezet=KORNYEZET):
    """(sor, oszlop, karakter, kodpont, unicode-nev, allapot) minden gyanus beture.

    Az allapot: "bejelentetlen", "bejelentett" (szam nelkuli jeloles) vagy
    "bejelentett-szammal". A NEGYEDIK eset -- a jeloles szamot ad, de a kornyeken
    MAS ANNYI talalat all -- szandekosan "bejelentetlen"-nek szamit: Roxy vetette fel
    2026-09-08 este, hogy egy szam nelkuli jeloles BLANKO felmentes, es egy kesobb
    beszurodo negyedik betu eszrevetlenul elbujna alatta.
    """
    sorok = szoveg.split("\n")
    nyers = []
    for sorszam, sor in enumerate(sorok, 1):
        for oszlop, c in enumerate(sor, 1):
            if ord(c) < 128:
                continue
            nev = unicodedata.name(c, "")
            if any(nev.startswith(g) for g in GYANUS):
                nyers.append((sorszam, oszlop, c, hex(ord(c)), nev))

    ki = []
    for sorszam, oszlop, c, kod, nev in nyers:
        a = max(0, sorszam - 1 - kornyezet)
        z = min(len(sorok), sorszam + kornyezet)
        szamok = [_jelolo_szam(s, jelolo) for s in sorok[a:z] if jelolo in s]
        if not szamok:
            ki.append((sorszam, oszlop, c, kod, nev, "bejelentetlen"))
            continue
        # ugyanennek a jelolesnek az ablakaban hany talalat all osszesen
        ablakban = sum(1 for x in nyers if a < x[0] <= z)
        if all(sz is None for sz in szamok):
            ki.append((sorszam, oszlop, c, kod, nev, "bejelentett"))
        elif any(sz == ablakban for sz in szamok if sz is not None):
            ki.append((sorszam, oszlop, c, kod, nev, "bejelentett-szammal"))
        else:
            vart = [sz for sz in szamok if sz is not None]
            ki.append((sorszam, oszlop, c, kod, nev,
                       f"bejelentetlen (a jeloles {vart}-t mond, de {ablakban} all itt)"))
    return ki


def main(argv):
    # --szigoru: a bejelenteseket FIGYELMEN KIVUL hagyja, tehat MINDEN talalat szamit.
    # Forgacs merte meg 2026-09-08 este, masolaton, hogy a jeloles rest ut azon, aki az
    # eszkozt kapunak hasznalja: a gazda NEVEBEN allo cirill o `exit 0`-t ad, ha valaki a
    # szomszedos sorba leirja a jelolo szot. Az o alakja a hatarra: a bejelentes ott
    # felmentes, ahol a homoglif LEHET szandekos -- egy szamlaszamban, egy sort code-ban
    # vagy egy szemely neveben nem lehet az.
    # Szandekosan NINCS fajl-lista bedrotozva (azt o maga kerte igy): a dontes a HIVOE,
    # es a kapcsolo csak lehetove teszi, hogy kimondja.
    szigoru = "--szigoru" in argv
    argv = [a for a in argv if a != "--szigoru"]
    if not argv:
        print(__doc__)
        return 0
    bejelentetlen_osszes = 0
    for ut in argv:
        szoveg = sys.stdin.read() if ut == "-" else open(ut, encoding="utf-8").read()
        t = talalatok(szoveg)
        nev = "(stdin)" if ut == "-" else ut
        if szigoru:
            nem, bej = t, []
        else:
            nem = [x for x in t if x[5].startswith("bejelentetlen")]
            bej = [x for x in t if not x[5].startswith("bejelentetlen")]
        bejelentetlen_osszes += len(nem)
        if not t:
            print(f"{nev}: tiszta")
            continue
        print(f"{nev}: {len(nem)} bejelentetlen, {len(bej)} bejelentett ({JELOLO})")
        for cimke, csoport in (("BEJELENTETLEN", nem), ("bejelentett", bej)):
            if not csoport:
                continue
            print(f"  -- {cimke} --")
            for sorszam, oszlop, c, kod, unev, allapot in csoport:
                # A karaktert magat NEM irjuk ki nyersen -- a kodpont az azonosito,
                # es igy a kimenet maga nem terjeszti tovabb a homoglifet.
                # Szigoru modban a cimke az OKOT mondja meg, ne az allapotot: egy
                # "[bejelentett-szammal]" felirat a BEJELENTETLEN fejlec alatt
                # megnyugtato cimkenek latszik egy riasztas kozepen. Margo vette
                # eszre 2026-09-08 este, kapu-modu kimeneten.
                if szigoru and not allapot.startswith("bejelentetlen"):
                    extra = "  [--szigoru: a bejelentes figyelmen kivul]"
                elif allapot not in ("bejelentetlen", "bejelentett"):
                    extra = f"  [{allapot}]"
                else:
                    extra = ""
                print(f"    {sorszam}. sor, {oszlop}. oszlop: {kod}  {unev}{extra}")
            for sorszam in sorted({x[0] for x in csoport}):
                sor = szoveg.split("\n")[sorszam - 1]
                jelolt = "".join("_" if ord(ch) > 127 and any(
                    unicodedata.name(ch, "").startswith(g) for g in GYANUS) else ch for ch in sor)
                print(f"    {sorszam}: {jelolt.strip()[:120]}")
    return 1 if bejelentetlen_osszes else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
