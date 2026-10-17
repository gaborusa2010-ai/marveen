# Belso technikai lista (osszevonva 2026-09-30)

A gazda Telegram msg 5038 (2026-09-30 18:02 BST): "Neked még mindig nagyon sok kártyád van!"
Ezert ez a 80 belso technikai kartya (flotta-hibak, kapuk, hookok, orszemek, doksi-res) egyenkent ARCHIVALVA lett,
es EGY gyujtokartya kepviseli a tablan. Visszahozas: POST /api/kanban/<hex>/unarchive. A kartyak kommentjei megmaradtak.

| seq | hex | prio | statusz | cim |
|---|---|---|---|---|
| 93 | 988542bc | normal | gazda-dontesre var (10-17: destructive-gate.py kesz, mindenhol KI; 1421-es lista 9. pont) | Bash-iras es git push: PreToolUse hook, mert a tiltolista nem eleg |
| 299 | 755110ea | normal | waiting | A WSL-reteg haromszor allt le mentes kozben ot napon belul (09-17, 09-21, 09-22) |
| 305 | 759434e9 | normal | kesz (10-17, 5607c489) | A flotta-kapu EGYSZER allit le egy agenst naponta: aki utana ujraindul, aznap mar immunis |
| 316 | 0bd247b6 | normal | kesz (10-17: hook marad tartaleknak, docs/memory-system.md kimondja, hogy ma nem fut) | A PreCompact hook soha nem sul el: az or elobb ujraindit (merve, pozitiv kontrollal) |
| 322 | 0cf47cd9 | normal | gazda-dontesre var (10-17: skill-engedely kell; 1421-es lista 10. pont) | Az agens-inditas-atadas skill hallgat a kartyacim szo szerinti atvetelerol (a #321 kod-javitasanak dokumentacios fele) |
| 323 | c776ed4d | normal | nem all fenn (10-17: a panel maga a claude, kilepeskor a session megszunik) | LAPPANGO RES: elo tmux session + halott claude = a sub-agens sosem indul ujra |
| 325 | 64a234de | normal | kesz (10-17: fejlec javitva, a fo-agens a .channels-config lapot tolti be) | A MEMORY.md HAT kulon fajl, nem kozos inode, es a fejlece az ellenkezojet allitja |
| 334 | 788a2a05 | normal | nem all fenn (10-17: a gombsor rendszeresen megjelenik, pl. msg 8050, 8053) | Gombos kerdes (tg-valasztas.py) gombsora nem jelent meg a gazdanal |
| 349 | 0eda731e | normal | lezarva (10-17: nem kotjuk be, a premisszaja itt nem all; upstream kod, marad) | memory-index-gate.sh: nincs eles hivoja -- bekotni vagy kivezetni |
| 353 | 7045306c | normal | kesz (10-17: binaris c1e83c0, 15 katalogus betoltve, kontroll OK) | Bumblebee binaris frissitese: az upstream katalogus schema 0.2.0, a v0.1.1 csak 0.1.0-t olvas |
| 87 | 1c6dd326 | low | planned | Anorak ejszakai muszakja: nincs allando feladat ebredeskor |
| 88 | e184b0e9 | low | kesz (10-17: minden script es unit a repoban vagy installerbol) | upstream-sync + a tobbi uj script bekerulese a repoba |
| 90 | 585755d4 | low | nem all fenn (10-17: 224 sor, 50 skill, 7 agens) | A skill_usage tabla gyakorlatilag ures -- nem tudjuk ki hasznal egy skillt |
| 94 | 89bb0126 | low | planned | Hang-canary: tesztmondat csere + rendszeres futtatas |
| 95 | 44f54620 | low | done (10-03) | egress-blocked.log: nincs benne agens-azonosito |
| 98 | 767acdd3 | low | planned | Beragadt mellekletek az inboxban + album-sejtes |
| 101 | 9b022646 | low | nem all fenn (10-17: a szuro csak injekcio-idiomakat nez, probe 200) | Memoria-API: a biztonsagi szuro a sajat tanulsagainkat is elutasitja |
| 102 | bb1f2697 | low | planned | Elveszett bejovo: a sweep csak a foagenst fedi -- kell-e a sub-agenseknek is? |
| 107 | 60847e57 | low | reszben (10-17: timer fut; a respawn utani alapvonal nem mert) | Channel-watchdog timer telepitese + respawn utani eletjel-alapvonal |
| 112 | f91d8de6 | low | planned | Vak folt: a napi naplo nem latja azt, aki uzeneteken keresztul dolgozik |
| 113 | c2e6d9e9 | low | lezarva (10-17: category=hot/warm/cold adja a sajat nezetet, doksiban) | A marveen shared emlekei elnyomjak a tobbiek sajat memoria-nezetet |
| 114 | 89fd8d00 | low | kesz (10-17: 612c4307 hibatorzs+hint; a kapu-resz nem reprodukalhato) | Ekezetes q= 400-at ad URES torzzsel + egress-kapu hamis riasztasa localhost hivasra |
| 117 | 0a8bb430 | low | lezarva (10-17: a skill eszkoz nelkul igaz, kimondja) | A doc-audit.py es host-audit.py NEM letezik -- egy skill-javaslat rajuk epult |
| 121 | 8a8ec601 | low | nem all fenn (10-17: sub-agenseknel autoMemoryEnabled=false) | Kozos auto-memoria: a nevesitetlen E/1 emlek mindenki masnal hamis allitas |
| 123 | 5f04bffd | low | planned | A Drive-szabaly ket mondata ellentmond egymasnak szemelyi Google-fiokon |
| 126 | 4f046b90 | low | planned | global-skill-write-gate: mindket iranyba teved (a parancs szoveget iteli, nem a hatasat) |
| 133 | b75cdee9 | low | planned | bash-egress-gate: hamis riasztas forraskodra, ami CSAK LEIRJA a halozati hivast |
| 138 | 7e6d6dc0 | low | planned | BIZTONSAG: a global-skill-write-gate ATENGEDI a fajlbol futtatott szkriptet -- naplosor sincs rola |
| 149 | 424811d0 | low | planned | Trigger-audit: a skillek 'Mikor hasznald' listaja fedi-e a valos szabalyszegeseket |
| 154 | 35b01b18 | low | kesz (10-17: atnevezve .bak-ra, tsc 0 hiba) | A teszt-worktree-ben egy rossz helyre masolt fajl 4 hamis tsc-hibat ad minden futasnal |
| 156 | 5988e05b | low | planned | A skill-management skill EGY SZOT SEM ir a grant-korrol, pedig a kapuba mindenki beleutkozik |
| 163 | a7f786cf | low | planned | Anorak javaslatai: legyen kartyajuk, ne csak jelentesuk |
| 174 | f01a5ec0 | low | planned | Nincs eval: 344 unit-teszt a kodra, nulla meres az agensek valaszaira |
| 175 | 6e101314 | low | planned | Menet kozben felvett karanten-domain nem er el a futo munkamenethez -- es ugyanugy hibazik, mint egy tiltott |
| 177 | 3433a075 | low | planned | store/: a fedetlen 68 fajlbol nehany VALODI tartalom, egyetlen peldanyban (a tobbi naplo) |
| 178 | bbf225e7 | low | planned | A mentesek KOZOTTI ablak: az ejszakai riportok most 21 oran at egyetlen peldanyban allnak |
| 179 | 26a1eba2 | low | done (10-03) | agent-night-shift.sh 23:00-kor hamisan riaszt, ha a muszak KORABBAN indult kezzel |
| 186 | aa5cb27b | low | planned | A memoria-API biztonsagi szuroje elutasitja az 'eval(' alakot -- es a curl 0-val ter vissza ra |
| 187 | adbe868f | low | planned | A provenance-kapu a HATTERTASK-ertesitest csatorna-inputnak veszi -- egy ejszaka ot hamis riasztas |
| 188 | fa752dcb | low | planned | A sub-agensek skill-indexe elavul: a heartbeat csak ANNAK frissiti, aki futtatja -- es 23-bol 22 feladat marveene |
| 192 | 8a2651da | low | planned | A nyugtak elrejtik az erdemi uzeneteket: a mai forgalom 50-60%-a [Eredmeny] sor |
| 193 | 79f16bf2 | low | planned | Mentes: a store/ blokk nevsoros engedelylista volt -- ket mappa ket napig kimaradt, es az elso javitas is keves lett |
| 195 | 891f35a3 | low | planned | session-stuck orszem: a hosszu batch-forduló hamis riasztast ad |
| 198 | 3999304a | low | planned | Kontextus-fogyokura: CLAUDE.md-k es a kozos MEMORY.md |
| 199 | 94883820 | low | planned | Az agens-lapok (CLAUDE.md, SOUL.md) nincsenek verziokezelve |
| 201 | 1d02f6f0 | low | planned | A wrapUntrustedFetch-nek nincs elo hivoja -- leirt lepes vegrehajthato alak nelkul |
| 202 | 9638f59e | low | planned | Orszem a kozos MEMORY.md ala: a betoltesi hatar ES a csonkulas |
| 204 | 4d9a01de | low | planned | Bridge skill (A) ag: elavult menufeliratok, a gazda verzioja kell hozza |
| 212 | f6247e86 | low | planned | Penz-kerekites iranya a szamlan: half-to-even vagy half-up (lappango, ma nulla hatas) |
| 215 | cb85b16d | low | planned | Kereso-wrapper: ekezet + sortores + rekurzio, es irja ki MIT szamol |
| 218 | efa15d04 | low | planned | A nincs-allitas-merese skill 1154 soros: szetvalasztas SKILL.md + references/ |
| 229 | ecbd713d | low | planned | Helyes tartalom, rossz kornyezet: negy eset egy estén -- ket szabaly, nem egy |
| 231 | a57b92ba | low | planned | CLAUDE.md a napindito formatumat a marveen-agent-reference.md-be utalja, de az nem all ott |
| 234 | 16d64fbd | low | planned | Roxy: 21 perce kezbesitetlen inter-agent uzenet, es a ket ismert ok NINCS jelen |
| 235 | f5f56f21 | low | planned | Egy ot napos SHARED emlek ma ketszer nem jutott el ahhoz, aki epp azt a problemat oldotta meg |
| 236 | 2c1f7a40 | low | planned | Egy lezart sor result-javitasa UJABB nyugtat kuld -- a kod maga nevezi meg a mintat, a PUT-ag nem hasznalja |
| 241 | 48b7fab7 | low | planned | Kiehezes-orszem: egy HETI feladat sosem tudja elerni a 10-es kuszobot (es a `missed` sikeres futasnak szamit) |
| 242 | 6a6eabe8 | low | planned | munkanaplo-zaras-orzo es munkanap-kerdes-forgacs: 1-5 cron, de a gazda szombaton is dolgozik |
| 253 | b039684a | low | planned | channel-image-resize.sh nema no-op ezen a gepen (jq es sips hianyzik) |
| 254 | cdec6ea7 | low | planned | Telepitett hook-peldanyok regebbiek a repo-belieknel (telegram_progress*) |
| 255 | eb3b4a28 | low | planned | Kozos skill-patch (nevesitett engedely kell): a teszt HAMIS OKBOL is atmehet |
| 261 | 86848ea4 | low | planned | Harom skill-mappa van, nem ketto: a repo-gyokeri sajat mappa mindenkibe betoltodik, de a kapu nem vedi |
| 262 | 76e9bee5 | low | planned | A kadencia-valtoztatas csendben leszereli a sajat starvation-riasztast |
| 263 | d5643b2a | low | planned | Orszem-halal: 9 feladatnal elerhetetlen a starvation-kuszob, es a kvota-kapu nemán dob napokat |
| 264 | ea53b3f9 | low | planned | Flotta-keret: a kapu a 18%-ot osztja ujra, a naptari nap ketszer allit le, es a stop-ertesites bukasa nyomtalan |
| 268 | ea0e650f | low | planned | A kapu ket hete riaszt a sajat index-regenunkre: 10 sor 09-04 ota, 8 engedely nelkul |
| 269 | 0cbc5ea7 | low | planned | Kozos MEMORY.md: eloszor MERD MEG, tortent-e valodi felulirás |
| 271 | fe92ab64 | low | planned | 11 memoria-lap ELERHETETLEN: bemeno wikilink kell rajuk |
| 272 | 2bf74fa8 | low | planned | A bukott kuldesrol a KULDO soha nem ertesul (handoff-failure) |
| 274 | 31d81bac | low | planned | A kihagyott orszem-kor lyukat hagy: fix 1h ablak kontra eldobott tickek |
| 275 | 5f06ca15 | low | planned | A tuzeles utani orszem elveszettnek nezi a csendes fordulokat, es ujra tuzel |
| 279 | e2632440 | low | planned | A hook-sablon sodrodasa: a megelozes MIND A KET uton javitva es merve; nyitva a require-checked es a futo build |
| 280 | c3e12a21 | low | planned | Het uzenetem nem ert el Anorakhoz: a 60 perces elhagyasi ablak a FOLYAMATOSAN DOLGOZO cimzettet bunteti |
| 281 | 21e17928 | low | planned | A biztonsagi naplok nem rogzitik, MELYIK agens sora: a darabszamra jok, a hozzarendelesre nem |
| 282 | 2c567585 | low | planned | A gazda SZERKESZTETT Telegram-uzenete egyikunkhoz sem jut el: a pluginban nincs edited_message kezelo |
| 289 | 6c10c1c2 | low | planned | Anorak ejszakai muszakja 27 perc utan meghal (harom megfigyelt alkalom) |
| 290 | b2443661 | low | planned | Reszsztring-alapu hook-szuro: ma stilus-kockazat, nem elo hiba (egy utkozes volt, javitva) |
| 294 | 82966364 | low | planned | A self-pace kapu a DELETE-et is tiltja, es az uzenete csak POST-ot emlit |
| 315 | a705a9a5 | low | planned | outgoing-copy-gate: a nevszabaly a nyers szovegen fut (kulso eszrevetel, visszamerve) |
| 350 | eabaa0b3 | low | planned | Index-plafon: a guard bajtot ES karaktert szamoljon, a rosszabbat mutassa |
