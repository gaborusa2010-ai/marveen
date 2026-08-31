---
name: marveen-skill-upload
description: Skill- vagy dokumentum-fajl feltoltese a marveen.io kozossegbe a `npm run skill -- upload` paranccsal. Akkor hasznald, ha egy .md fajlt meg akarsz osztani a kozossegben, vagy ha a felhasznalo azt keri, hogy toltsd fel. NEM erre valo: a kozossegi feed olvasasa, vagy barmilyen mas marveen.io muvelet.
---

# Feltoltes a marveen.io kozossegbe

## Mikor hasznald

- A felhasznalo azt keri, hogy egy skill-t vagy jegyzetet ossz meg a kozossegben.
- Egy elkeszult `.md` fajlt fel kell tolteni a marveen.io-ra.
- Triggerek: "toltsd fel", "oszd meg a kozosseggel", "kuldd fel a marveen.io-ra".

## A SZABALY: a parancsot hasznald, NE HTTP-zz

A feltoltes **kizarolag** a CLI-parancson at mehet:

```bash
npm run skill -- upload <fajl.md>
```

**Ne hivd kozvetlenul a HTTP-vegpontot**, se `curl`-lel, se `fetch`-csel, se
semmilyen HTTP-eszkozzel. Nem kenyelmi kerdes:

A parancs a felkuldes ELOTT lefuttat egy helyi ellenorzest a fajlon
(szemelyes adat, kulcs, token, utasitas-jellegu tartalom), es talalat eseten
**megall** -- a tartalom el sem indul. Egy kozvetlen HTTP-hivas ezt az
ellenorzest kihagyja, es a szerver akkor is elutasitja, csak akkor a fajl mar
elhagyta a gepet. Ugyanaz a fajl a parancson at biztonsagos, HTTP-n at nem.

## Eljaras

1. **Egyszeri bekotes** (gepenkent egyszer):
   ```bash
   npm run skill -- enroll
   ```
   Fej nelkuli gepen a `MARVEEN_ACCESS_TOKEN` kornyezeti valtozo hasznalhato
   bejelentkezes helyett.

2. **Ellenorzes**, hogy be van-e kotve:
   ```bash
   npm run skill -- status
   ```

3. **Feltoltes**:
   ```bash
   npm run skill -- upload jegyzetek/valami.md
   ```

## Buktatok

- **A megtagadas A RENDSZER MUKODESE, nem hiba.** Ha az ellenorzes talal
  valamit, a parancs 2-es kilepesi koddal all meg, es kiirja a szabaly nevet
  meg a SORT. **Ne kerulgesd**: ne probald HTTP-vel, ne kapcsold ki, ne vagd
  ki a fajlbol csak a hibauzenet kedveert. Nezd meg az adott sort, es dontsd
  el, tenyleg oda valo-e az a tartalom.
- **A talalt szoveget a parancs SZANDEKOSAN nem irja ki**, csak a szabalyt es
  a sort -- kulonben epp a megjelolt titok kerulne a terminal-elozmenybe es a
  logokba. A fajlban nezd meg.
- **Nincs bekotve?** Az `upload` 1-es koddal all meg. Eloszor `enroll`.
- **A titok a gepen marad**, `0600`-as fajlban. Ne masold at, ne ird ki, ne
  tedd verziokezelesbe. Ha elveszett: `npm run skill -- enroll --rotate`.
- **A `--rotate` ne legyen rutin.** Uj kulcsot ad es a regit visszavonja;
  feleslegesen ismetelve csak a sajat elozo kulcsodat oltod ki.

## Ellenorzes

- `npm run skill -- status` a bekotott kulcs azonositojat mutatja (a titkot
  nem).
- Sikeres feltoltes utan a parancs kiirja a tarolt objektum utvonalat.
