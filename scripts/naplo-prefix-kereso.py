#!/usr/bin/env python3
"""Naplo-ora-prefix keresese a flotta betoltodo lapjain.

MIERT EGY SZKRIPT, ES NEM EGY GREP-MINTA A FEJEDBEN:
2026-09-11-en negy agens negy jo szandeku mintat irt ugyanerre a kerdesre, es
MINDEGYIK mast adott -- ket alulmeres es egy tulmeres:

  Cody elso mintaja  `HH:MM`              a literalis sablont latta, a KONKRET orakat nem
  Marveen mintaja    `(## )?NN:NN( --| -)` a konkret orakat latta, a SABLONT nem
                                          ES egy parancssori kapcsolot (`--at 07:00 --billing`)
                                          naplo-prefixnek nezett  (Forgacs merte)
  az unio                                 3 valodi peldany Anorak lapjan

Mind a harom minta "kesz" valaszt adott volna valakinek. Ezert nem pontosabb
SZABALY kell, hanem EGY implementacio, amit hivunk. (Forgacs alakja, ugyanaznap,
a 210/211/212-es sor-szamlalas utan.)

A HORGONY a `##`, ezert egy parancssori kapcsolo nem illeszkedik.

AMIT NEM TUD, es ezt tudni kell rola: nem kulonbozteti meg az ALLITAST az
ARROL SZOLO IDEZETTOL. Egy dokumentacios mondat, ami idezi a regi sablont
`## HH:MM -- Tema` alakban, TALALATNAK szamit. Ezert a kimenet a SORT is kiirja:
az olvas dontse el. (Ugyanez a hatar sult el a kartya-leiras ellenorzesenel.)

A VAKSAG NEM VELETLEN, HANEM EGY KONVENCIORA TAMASZKODIK (Margo, 2026-09-11).
Eloszor "szerencsenek" neveztem, hogy egy magyarazo mondat nem lesz talalat.
Margo pontositott, es igaza van: a horgony azert nem illeszkedik, mert a
sablonrol SZOLO mondat backtickbe teszi a kod-reszletet -- ez markdown-konvencio,
nem veletlen. A ket eset rendszeresen eltér:

    HASZNALAT (elo sablon)  a sor elejen all, backtick NELKUL -- mert futni fog
    EMLITES (rola szolo)    backtickben all -- mert kod-idezet

A kulonbseg gyakorlati: egy SZERENCSE-alapu nullat nem lehet megbizhatonak
nevezni, egy KONVENCIO-alapu nullat viszont lehet -- amig a konvencio all, es
ezt meg lehet nezni. De garancia ez sem: aki backtick nelkul ir le egy sablont
egy magyarazo mondatban (rossz markdown, de elofordul), talalatot fog kapni.
Ezert all a kimenetben minden talalat SORA is.
"""
import re, sys, os, glob

MINTA = re.compile(r'(?:^|"|\\n)\s*##\s*(?:\d{2}:\d{2}|HH:MM)\s*--')

# Glob, not a fixed list: a fixed list missed kiron, merlin and prospero
# (Anorak's fleet retro, 2026-10-12) -- every new agent must be covered.
ALAP = (
    ['CLAUDE.md']
    + sorted(glob.glob('agents/*/CLAUDE.md'))
    + ['docs/marveen-agent-reference.md']
)

# ------------------------------------------------------------------------------
# ONTESZT. `python3 scripts/naplo-prefix-kereso.py --onteszt`
#
# MIERT ALL A KONTROLL A SZKRIPTBEN, ES NEM EGY UZENETBEN:
# Roxy harmadik aga, 2026-09-11. A ket vak minta melle jott egy harmadik alak:
# aki "biztos ami biztos" alapon UNIOZZA a mintakat, csendben SZELESIT. O a lezaro
# `( --| -)` csoportot opcionalissa tette, es a minta ezzel BARMILYEN `NN:NN`-re
# illeszkedett: harom PROZABAN allo idopontot jelentett talalatnak a SAJAT, mar
# javitott fajljan. Majdnem "megis van benne"-t jelentett rola; a negativ kontroll
# mentette meg -- azt akarta latni, hogy a mintai egyaltalan talalnak-e valamit.
#
# A CSALAD TEHAT HAROM AGU, es mindharom "kesz"-nek VAGY "van meg"-nek latszik:
#   1. ket jo szandeku minta VAKON hagyja egymas talalatait (alulmeres)
#   2. az UNIOJUK csendben szelesit (tulmeres)
#   3. es egy horgony nelkuli minta parancssori kapcsolot fog meg (tulmeres,
#      Forgacs merte: `--at 07:00 --billing`)
# Ezert a lezaro `--` KOTELEZO ebben a mintaban, es ezert van horgony. Aki
# szelesiteni akar rajta, FUTTASSA LE EZT ELOTTE ES UTANA.
KONTROLL = [
    (True,  '    ## 10:00 -- [FOLYAMATBAN] Lyford Road szabaslista ellenorzes.'),
    (True,  '    ## 11:15 -- [LEZART] Lyford Road ujraszamolva, a 10:00-as sor MEGDOLT.'),
    (True,  '''-d '{"agent_id":"anorak","content":"## HH:MM -- Tema Mi tortent"}' '''),
    (True,  '''  -d '{"agent_id":"marveen","content":"## HH:MM -- Tema\\nMi tortent"}' '''),
    (False, 'python3 scripts/muhely/jobs.py start --job "Y" --at 07:00 --billing daily'),
    (False, '    "MERVE 2026-09-04 08:40-kor, pillanatnyi allapot: ..."   <- avulhat'),
    (False, 'A gazda kerte, Telegramon, 2026-09-10 05:40 BST (msg 2359-2361):'),
    (False, '... mtime-ja 2026-09-11 03:00:17, tehat ejjel kerult be ...'),
    (False, '''  -d '{"content":"[LEZART] Tema -- mi tortent"}' '''),
    (False, 'A regi sablon `## HH:MM` prefixszel kezdodott, es az 2026-09-11-en megdolt'),
]

def onteszt():
    jo = rossz = 0
    for vart, szoveg in KONTROLL:
        van = bool(MINTA.search(szoveg))
        ok = (van == vart)
        jo += ok; rossz += not ok
        print(('  OK  ' if ok else '  BUKO'),
              f'vart={"talalat" if vart else "nincs  "} kapott={"talalat" if van else "nincs  "}',
              szoveg.strip()[:62])
    print(f'\n  {jo} helyes, {rossz} bukott, {len(KONTROLL)} kontrollbol')
    print('  A pozitivak ES a negativak is kellenek: egy minta, ami mindenre igent mond,')
    print('  ugyanugy atmenne csak a pozitivakon.')
    return 1 if rossz else 0

def main(gyoker, fajlok):
    ossz = 0
    for rel in fajlok:
        p = os.path.join(gyoker, rel)
        # A LETEZEST KULON TESZTELJUK. A `grep -c` NULLA talalatnal is 1-es kodot ad,
        # tehat a "nincs benne" es a "nincs fajl" ugyanugy nez ki (Cody merte, 09-11).
        if not os.path.isfile(p):
            print(f'  {rel:38s} NINCS ILYEN FAJL')
            continue
        try:
            sorok = open(p, encoding='utf-8').read().splitlines()
        except OSError as e:
            print(f'  {rel:38s} NEM OLVASHATO: {e}')
            continue
        tal = [(i, s) for i, s in enumerate(sorok, 1) if MINTA.search(s)]
        ossz += len(tal)
        print(f'  {rel:38s} {len(sorok):5d} sor | {len(tal)} talalat')
        for i, s in tal:
            print(f'      {i:5d}: {s.strip()[:96]}')
    print()
    print(f'  OSSZESEN: {ossz} talalat {len(fajlok)} fajlban.')
    print('  Minden talalatot NEZZ MEG: egy idezet a regi sablonrol ugyanugy illeszkedik,')
    print('  mint egy elo sablon. A szkript a sort adja, a dontest nem.')
    return 1 if ossz else 0

if __name__ == '__main__':
    if '--onteszt' in sys.argv:
        sys.exit(onteszt())
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gy = os.environ.get('MARVEEN_ROOT', repo)
    # A VISSZATERESI ERTEK KIJUT. Roxy merte meg 2026-09-11-en: a `main` kiszamolta a
    # kodot (`return 1 if ossz else 0`), a hivo viszont eldobta, es a `sys.exit(0)`
    # MINDIG nullat adott. Nem "nincs kilepesi kod" volt, hanem egy KISZAMOLT valasz,
    # ami nem jutott el a hivohoz -- ket sorral azutan, hogy eloallt.
    # Es a hibat a POZITIV kontroll mutatta meg (Anorak fajlja, ahol tudjuk hogy 3
    # talalat van), nem egy tiszta fajl nullaja: egy "0 talalat -> exit 0" egyezes
    # ugyanigy nezett volna ki a hibas es a helyes valtozatnal is.
    sys.exit(main(gy, sys.argv[1:] or ALAP))
