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
| 87 | 1c6dd326 | low | done | Anorak ejszakai muszakja: nincs allando feladat ebredeskor (2026-10-17: megoldva, napi + allando eligazitas, STANDING1010) |
| 88 | e184b0e9 | low | kesz (10-17: minden script es unit a repoban vagy installerbol) | upstream-sync + a tobbi uj script bekerulese a repoba |
| 90 | 585755d4 | low | nem all fenn (10-17: 224 sor, 50 skill, 7 agens) | A skill_usage tabla gyakorlatilag ures -- nem tudjuk ki hasznal egy skillt |
| 94 | 89bb0126 | low | kesz (10-17: main 3cb51796 df42f301 80dbc575; telepitve, heti timer V 10:15, ket hang PASS) | Hang-canary: tesztmondat csere + rendszeres futtatas |
| 95 | 44f54620 | low | done (10-03) | egress-blocked.log: nincs benne agens-azonosito |
| 98 | 767acdd3 | low | kesz (10-17: inbox a mentesben, albumok ma soronkent naplozva; 767acdd3 komment) | Beragadt mellekletek az inboxban + album-sejtes |
| 101 | 9b022646 | low | nem all fenn (10-17: a szuro csak injekcio-idiomakat nez, probe 200) | Memoria-API: a biztonsagi szuro a sajat tanulsagainkat is elutasitja |
| 102 | bb1f2697 | low | kesz (10-19: 90f320ad, 10-18 ota 7 sub-agens bejovo sorai a conversation_log-ban) | Elveszett bejovo: a sweep csak a foagenst fedi -- kell-e a sub-agenseknek is? |
| 107 | 60847e57 | low | kesz (10-21: alapvonal merve, channel-keepalive-probe.log 10-01 ota 17 WARN, legkisebb koz 8221 s, nincs percenkenti ismetles; a 09-18/19-es 274+97 soros vihar ota nem volt) | Channel-watchdog timer telepitese + respawn utani eletjel-alapvonal |
| 112 | f91d8de6 | low | kesz (10-18: az esti zaras 8. pontja, scripts/napi-igeretek.py team_today, agensenkent naplo + kuldott agens-uzenet; ma ariel 0 naplo / 2 uzenet latszik) | Vak folt: a napi naplo nem latja azt, aki uzeneteken keresztul dolgozik |
| 113 | c2e6d9e9 | low | lezarva (10-17: category=hot/warm/cold adja a sajat nezetet, doksiban) | A marveen shared emlekei elnyomjak a tobbiek sajat memoria-nezetet |
| 114 | 89fd8d00 | low | kesz (10-17: 612c4307 hibatorzs+hint; a kapu-resz nem reprodukalhato) | Ekezetes q= 400-at ad URES torzzsel + egress-kapu hamis riasztasa localhost hivasra |
| 117 | 0a8bb430 | low | lezarva (10-17: a skill eszkoz nelkul igaz, kimondja) | A doc-audit.py es host-audit.py NEM letezik -- egy skill-javaslat rajuk epult |
| 121 | 8a8ec601 | low | nem all fenn (10-17: sub-agenseknel autoMemoryEnabled=false) | Kozos auto-memoria: a nevesitetlen E/1 emlek mindenki masnal hamis allitas |
| 123 | 5f04bffd | low | gazdara var (10-17 visszamerve: fennall, mar 7 agensnel + fleet-hygiene skill; flotta-szoveg, reggel gombos kerdes) | A Drive-szabaly ket mondata ellentmond egymasnak szemelyi Google-fiokon |
| 126 | 4f046b90 | low | lezarva (10-17: alullo fele audit-hookkal megszunt 09-04; tullo fele szandekos, a gate INTERPWRITE901 kommentje dokumentalja) | global-skill-write-gate: mindket iranyba teved (a parancs szoveget iteli, nem a hatasat) |
| 133 | b75cdee9 | low | nem all fenn (10-17: leiro forraskod atmegy, a valodi curl tiltva) | bash-egress-gate: hamis riasztas forraskodra, ami CSAK LEIRJA a halozati hivast |
| 138 | 7e6d6dc0 | low | enyhitve (10-17: hatas-alapu audit naploz, TREE_CHANGE sorok; megelozni nem tud) | BIZTONSAG: a global-skill-write-gate ATENGEDI a fajlbol futtatott szkriptet -- naplosor sincs rola |
| 149 | 424811d0 | low | lezarva (10-17: kontrollcsoport nelkul nem merheto, komment 208; a szabalyok egy resze azota kod-kapuban) | Trigger-audit: a skillek 'Mikor hasznald' listaja fedi-e a valos szabalyszegeseket |
| 154 | 35b01b18 | low | kesz (10-17: atnevezve .bak-ra, tsc 0 hiba) | A teszt-worktree-ben egy rossz helyre masolt fajl 4 hamis tsc-hibat ad minden futasnal |
| 156 | 5988e05b | low | KESZ 10-17 (skill-management SKILL.md 137. sor, ALLOWED_STANDING + TREE_CHANGE_GRANTED) | A skill-management skill EGY SZOT SEM ir a grant-korrol, pedig a kapuba mindenki beleutkozik |
| 163 | a7f786cf | low | kesz (10-17: szabaly docs/tudas/inter-agent-reszletek.md, token-skill patch, jegyzet 444787) | Anorak javaslatai: legyen kartyajuk, ne csak jelentesuk |
| 174 | f01a5ec0 | low | gazda: egyelore nem kezdjuk (09-06 11:44, kartya-leiras) | Nincs eval: 344 unit-teszt a kodra, nulla meres az agensek valaszaira |
| 175 | 6e101314 | low | lezarva (10-17: mar javitva, cbd99f30 09-06; quarantine-reader.md:543-559) | Menet kozben felvett karanten-domain nem er el a futo munkamenethez -- es ugyanugy hibazik, mint egy tiltott |
| 177 | 3433a075 | low | lezarva (10-18: backup-coverage store 5131/5131 fedve) | store/: a fedetlen 68 fajlbol nehany VALODI tartalom, egyetlen peldanyban (a tobbi naplo) |
| 178 | bbf225e7 | low | kesz (10-18: reports-history timer, 10 perces git-tortenet; ag fix/178-reports-history 37155066) | A mentesek KOZOTTI ablak: az ejszakai riportok most 21 oran at egyetlen peldanyban allnak |
| 179 | 26a1eba2 | low | done (10-03) | agent-night-shift.sh 23:00-kor hamisan riaszt, ha a muszak KORABBAN indult kezzel |
| 186 | aa5cb27b | low | nem all fenn (10-17: eval( tartalom HTTP 200, a szuro mar nem utasitja el) | A memoria-API biztonsagi szuroje elutasitja az 'eval(' alakot -- es a curl 0-val ter vissza ra |
| 187 | adbe868f | low | lezarva (10-18: szandekos; a kapu a sajat hatter-taskot strukturalisan felismeri, nem kerdez vissza, de naploz: 127/633 sor 10-11 ota, task= jelolessel) | A provenance-kapu a HATTERTASK-ertesitest csatorna-inputnak veszi -- egy ejszaka ot hamis riasztas |
| 188 | fa752dcb | low | lezarva (10-18: a 09-08-as dontes all; 688 sub-agens session 09-08 ota, 0 index-olvasas) | A sub-agensek skill-indexe elavul: a heartbeat csak ANNAK frissiti, aki futtatja -- es 23-bol 22 feladat marveene |
| 192 | 8a2651da | low | lezarva (10-18: nyugta-arany 7%, 83/1200 az utolso 7 napon; 09-01..07-en 36%) | A nyugtak elrejtik az erdemi uzeneteket: a mai forgalom 50-60%-a [Eredmeny] sor |
| 193 | 79f16bf2 | low | lezarva (10-18: store/ blokk inverz, backup.sh:171-202; reports-history 103 fajl a kovetkezo mentessel) | Mentes: a store/ blokk nevsoros engedelylista volt -- ket mappa ket napig kimaradt, es az elso javitas is keves lett |
| 195 | 891f35a3 | low | lezarva (10-18: BUSY-riasztas utoljara 09-20, 0 azota; worksource-szallitasnal a stuck-ag nem fut, message-router.ts 867/882) | session-stuck orszem: a hosszu batch-forduló hamis riasztast ad |
| 198 | 3999304a | low | lezarva (10-18: kivaltva; autoMemory mind a 9 sub-agensnel ki, kozos MEMORY.md-t senki nem tolti; CLAUDE.md-ket a line-cap-gate 500 sora fogja, fo 487) | Kontextus-fogyokura: CLAUDE.md-k es a kozos MEMORY.md |
| 199 | 94883820 | low | done (10-17, #578: store/agent-pages-history + 10 perces timer, branch a706abf9) | Az agens-lapok (CLAUDE.md, SOUL.md) nincsenek verziokezelve |
| 201 | 1d02f6f0 | low | kesz (10-17: scripts/wrap-fetch.mjs, eee3063f, 6/6 teszt; quarantine-reader leiras a parancsot nevezi) | A wrapUntrustedFetch-nek nincs elo hivoja -- leirt lepes vegrehajthato alak nelkul |
| 202 | 9638f59e | low | done | Orszem a kozos MEMORY.md ala: a betoltesi hatar ES a csonkulas -- premissza megszunt: a kozos MEMORY.md-t senki nem tolti be (#121), 10-17 |
| 204 | 4d9a01de | low | planned | Bridge skill (A) ag: elavult menufeliratok, a gazda verzioja kell hozza |
| 212 | f6247e86 | low | feltetelre var (10-18: invoice.py meg 7 round(), Decimal 0; csak uj, 200/240-tol eltero napidijnal kell dontes Forgaccsal) | Penz-kerekites iranya a szamlan: half-to-even vagy half-up (lappango, ma nulla hatas) |
| 215 | cb85b16d | low | kesz (10-18: scripts/szoveg-kereso.py, teszt 5/5, 80517744 a fix/215-kereso agon; bekotes egy skill-mutatoba nyitva) | Kereso-wrapper: ekezet + sortores + rekurzio, es irja ki MIT szamol |
| 218 | efa15d04 | low | kesz (10-02 ota; 10-18 visszamerve: SKILL.md 186 sor + references/buktatok.md 1572, 14/14 cimsor megvan) | A nincs-allitas-merese skill 1154 soros: szetvalasztas SKILL.md + references/ |
| 229 | ecbd713d | low | lezarva (10-18: (a) es (b) is betoltodo szoveg lett: "A MÉRET IS ÁLLÍTÁS" + "Nem becsülsz ... számot emlékezetből" 10/10 fajlban; hatasossag nem mert) | Helyes tartalom, rossz kornyezet: negy eset egy estén -- ket szabaly, nem egy |
| 231 | a57b92ba | low | done | CLAUDE.md a napindito formatumat a marveen-agent-reference.md-be utalja, de az nem all ott -- HAMIS premissza: ott all (623. sor), a grep az ekezet miatt nem talalta, 10-17 |
| 234 | 16d64fbd | low | lezarva (10-18: nem ismetlodik, 29/29 kezbesitve, median 4 mp; a 33 oras kesest a leallt session magyarazza; maradek: tui fullscreen kulcs) | Roxy: 21 perce kezbesitetlen inter-agent uzenet, es a ket ismert ok NINCS jelen |
| 235 | f5f56f21 | low | reszben lezarva (10-18: szoveg-kereso mutato a betoltodo lapokban + docs/tudas/szoveg-kereses.md; az altalanos "keress, mielott ujnak irod" reflexre nincs eszkoz, nem mert) | Egy ot napos SHARED emlek ma ketszer nem jutott el ahhoz, aki epp azt a problemat oldotta meg |
| 236 | 2c1f7a40 | low | kesz (10-17: RECEIPT920912 statusChanged a PUT-agban, futo dist-ben is; teszt 3/3 worktree-ben) | Egy lezart sor result-javitasa UJABB nyugtat kuld -- a kod maga nevezi meg a mintat, a PUT-ag nem hasznalja |
| 241 | 48b7fab7 | low | javitva, beolvasztasra var (10-17: 73de2f0e a fix/starvation-sparse-cron agon; teszt 33/33 + 30/30, tsc tiszta) | Kiehezes-orszem: egy HETI feladat sosem tudja elerni a 10-es kuszobot (es a `missed` sikeres futasnak szamit) |
| 242 | 6a6eabe8 | low | lezarva (10-18: zaras-orzo 1-6, Forgacs 1-5 marad (09-20); a 09-19-i elnyelt hiany-ag mar nem letezik, a hiany a naplo-hiany.py-ban, a 07:00-s kerdesben) | munkanaplo-zaras-orzo es munkanap-kerdes-forgacs: 1-5 cron, de a gazda szombaton is dolgozik |
| 253 | b039684a | low | done | channel-image-resize.sh nema no-op ezen a gepen (jq es sips hianyzik) -- javitva (python3 + ffmpeg), elo proba 5,4 MB -> 221 KB, 10-17 |
| 254 | cdec6ea7 | low | kesz (10-17: a 3 regi ~/.claude/hooks bejegyzes kiveve a ~/.claude/settings.json-bol, bak-20261017-2230-stale-progress; futo session nem hasznalta) | Telepitett hook-peldanyok regebbiek a repo-belieknel (telegram_progress*) |
| 255 | eb3b4a28 | low | var a gazdara (10-18: a repo-gyokeri skill-mappa is flotta-szintu, #261; iras visszavonva, tervezet store/skill-drafts/255; #261 dontese vagy nevesitett engedely kell) | Kozos skill-patch (nevesitett engedely kell): a teszt HAMIS OKBOL is atmehet |
| 261 | 86848ea4 | low | leiras javitva, var a gazdara (10-18: CLAUDE.md generalt blokk + generator, ag fix/261-skill-trap-text a5900812, worktree marveen-wt-261; a/b/c dontes a napinditoban) | Harom skill-mappa van, nem ketto: a repo-gyokeri sajat mappa mindenkibe betoltodik, de a kapu nem vedi |
| 262 | 76e9bee5 | low | javitva, beolvasztasra var (10-18: (a) mar a #241 73de2f0e-ben, a kuszob a mindenkori cronbol szamol; maradek: havi/negyedeves cron a 30 napos TTL-en tul, b89bccab ugyanazon az agon; 52 tesztfajl 654/654, tsc tiszta) | A kadencia-valtoztatas csendben leszereli a sajat starvation-riasztast |
| 263 | d5643b2a | low | kesz (10-18: (c) = #241+#262 kuszob; (b) scripts/kvota-eldobas.py elesben + reggeli-napindito SKILL.md szekcio, ag feat/263-kvota-eldobas 67942333, teszt 5/5; 30 nap alatt 2 eldobas, utolso 09-20) | Orszem-halal: 9 feladatnal elerhetetlen a starvation-kuszob, es a kvota-kapu nemán dob napokat |
| 264 | ea53b3f9 | low | 2+3 kesz (window_day 2c3fd2c2, stop-ag kiment+fuggoben); 1 var a gazdara (10-17 23:3x: napi 123%, heti 64% vs 53% eltelt = neutral, a napi dontott; fo-agens 58,7%, Anorak 3,6% allt le). Kerdes a napinditoban | Flotta-keret: a kapu a 18%-ot osztja ujra, a naptari nap ketszer allit le, es a stop-ertesites bukasa nyomtalan |
| 268 | ea0e650f | low | lezarva (10-18: 09-26 ota 0 UNGRANTED; az index csak tartalmi valtozaskor irodik, 09-18 ota valtozatlan, 54/54; maradek: leiras-valtozas standing granttal -> index-sor UNGRANTED, 30 nap alatt 0) | A kapu ket hete riaszt a sajat index-regenunkre: 10 sor 09-04 ota, 8 engedely nelkul |
| 269 | 0cbc5ea7 | low | done | Kozos MEMORY.md: eloszor MERD MEG, tortent-e valodi felulirás -- premissza megszunt, mint #202, 10-17 |
| 271 | fe92ab64 | low | lezarva (10-18: memoria-index-meret.py: 247/247 lap elerheto) | 11 memoria-lap ELERHETETLEN: bemeno wikilink kell rajuk |
| 272 | 2bf74fa8 | low | lezarva (10-18: notifySenderOfFailedHandoff 033b3ff6 ota eles, teszt 12/12; CLAUDE.md, kozos-szabalyok, Anorak postalada-skill szovege javitva) | A bukott kuldesrol a KULDO soha nem ertesul (handoff-failure) |
| 274 | 31d81bac | low | lezarva (10-18: Gmail-ablak a last_run_at-tol, after:<epoch>; 199 eldobott tick a naploban; mindket ag a fajlbol futtatva) | A kihagyott orszem-kor lyukat hagy: fix 1h ablak kontra eldobott tickek |
| 275 | 5f06ca15 | low | lezarva (10-18: 7 nap alatt 738 tuzeles, 0 ujratuzeles 120 mp-en belul, 0 lost; mind a 38 lost 09-19/09-20-i, bd93dae9/3098bbbc elotti) | A tuzeles utani orszem elveszettnek nezi a csendes fordulokat, es ujra tuzel |
| 279 | e2632440 | low | lezarva (10-18: prune-fix 2c3fd2c2, dist-ben; mind a 10 agens elo settings.json-jaban a sablon Bash-csoportja ep, gswg x2, egress x1, friss spawnok utan is) | A hook-sablon sodrodasa: a megelozes MIND A KET uton javitva es merve; nyitva a require-checked es a futo build |
| 280 | c3e12a21 | low | lezarva (10-18: elhagyas csak 60 perc FOLYAMATOS hiany utan, 71263d15/033b3ff6; 7 nap: 1733 uzenet, 8 failed, mind hianyzo session) | Het uzenetem nem ert el Anorakhoz: a 60 perces elhagyasi ablak a FOLYAMATOSAN DOLGOZO cimzettet bunteti |
| 281 | 21e17928 | low | kesz, beolvasztasra var (10-18: egress mar hordta; a skill-kapu BLOCKED/ALLOWED_BY_GRANT sora is agent=-t kap, 0567555a, fix/281-skill-gate-agent-in-log) | A biztonsagi naplok nem rogzitik, MELYIK agens sora: a darabszamra jok, a hozzarendelesre nem |
| 282 | 2c567585 | low | kesz (10-18: szabaly a fo CLAUDE.md-ben es kozos-szabalyok.md:321; plugin 0.0.7-ben edited_message 0; upstream patch nem lesz) | A gazda SZERKESZTETT Telegram-uzenete egyikunkhoz sem jut el: a pluginban nincs edited_message kezelo |
| 289 | 6c10c1c2 | low | gazda-dontesre var (10-18: ok = flotta napi keret-ore, 10-17 23:15 is; dontes #264, tg 8264) | Anorak ejszakai muszakja 27 perc utan meghal (harom megfigyelt alkalom) |
| 290 | b2443661 | low | kesz, beolvasztasra var | Reszsztring-alapu hook-szuro: withoutHook, fix/290-hook-prune 0d7021d3 |
| 294 | 82966364 | low | done | A self-pace kapu a DELETE-et is tiltja, es az uzenete csak POST-ot emlit (2026-10-17: uzenet javitva, DELETE-tiltas marad) |
| 315 | a705a9a5 | low | kesz (10-18: mar javitva 347df3d1, 09-22, GATENEVSTRIP921: a nevszabaly sajat NAME_MASK-on fut, outgoing-copy-gate.py:1305; rules _doc frissitve) | outgoing-copy-gate: a nevszabaly a nyers szovegen fut (kulso eszrevetel, visszamerve) |
| 350 | eabaa0b3 | low | kesz, beolvasztasra var (10-18: 2.1.302-ben a mertekegyseg JS .length, nem bajt, a guard jo egysegben mert; de csak a `- [` sorokat szamolta, javitva 46fee381, fix/350-index-guard-harness-count) | Index-plafon: a guard bajtot ES karaktert szamoljon, a rosszabbat mutassa |
| - | - | low | nyitott, ok merve 10-21 (kartya nincs, a #407 alatt) | email-approval-gate: `python3 $SCRIPT ... $ARG` (valtozos script-ut + barmely masik `$`-os argumentum) SZANDEKOSAN kuldesnek szamit (`_python_head_sends`, outgoing-copy-gate.py ~482, JS-iker email-send-gate.mjs), a tiltas szovege viszont "nem nyerheto ki level-szoveg/cimzett", ami felrevezet. Javitas-jelolt: a tiltas nevezze meg a mintat es a kerulot (literal script-ut). A szabaly lazitasa bypasst nyitna (`S=send.py; python3 $S $TO`). |
