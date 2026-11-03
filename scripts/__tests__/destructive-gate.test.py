#!/usr/bin/env python3
"""Regresszios kapu a destructive-gate.py szovegkornyezet-erzekenysegehez (ba856d56).

Az osztaly, amit ez a teszt kizar: a kapu a parancs SZOVEGEBEN keres mintat, ezert
olyan hivast is blokkol, amely a mintat csak EMLITI -- heredoc-torzsben, karakterlanc-
literalban vagy kommentben --, mikozben a muvelet soha nem hajtodik vegre. Meres 3365
valos parancson (2026-09-14): 109 blokk, ebbol 31 hamis pozitiv. A javitas utan 4.

A masik irany legalabb ennyire fontos: a javitas EGYETLEN valos blokkot sem veszithet
el. Az itteni POZITIV kontrollok pontosan ezt rogzitik -- egy jovobeli "meg kevesebb
hamis pozitiv" finomitas ezeken bukjon meg, ne eles hasznalatban.

A ket eset, amit a legkonnyebb osszekeverni, es amit ez a teszt kulon pin-el:
  cat > fajl <<EOF   -- a torzs ADAT, fajlba megy, semmi nem futtatja  -> atengedve
  python3 - <<PY     -- a torzset az ertelmezo VEGREHAJTJA             -> vizsgalva
A mereskor ket VALODI hitelesito-fajl-olvasas allt ilyen ertelmezo-torzsben; egy naiv
"a heredoc-torzset hagyd ki" javitas elengedte volna oket.

Futtatas: python3 <ezafajl>   Exit 0 = minden teszt zold.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GATE = os.path.join(ROOT, "scripts", "hooks", "destructive-gate.py")

spec = importlib.util.spec_from_file_location("dgate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class Blocked(Exception):
    pass


def _raise(msg):
    raise Blocked(msg)


gate.block = _raise

failed = []


def check(name, ok):
    print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        failed.append(name)


def blocks_cwd(cmd, cwd):
    """Ugyanaz, mint blocks(), de megadott munkakonyvtarral: a rm-engedely
    (PR #1357) relativ utaknal ettol fugg, es cwd nelkul minden relativ ut
    egyszeruen nem eldontheto -- az sosem mutatna meg a kulonbseget."""
    try:
        gate.check_bash(cmd, cwd=cwd)
        return False
    except Blocked:
        return True


def blocks(cmd):
    try:
        gate.check_bash(cmd)
        return False
    except Blocked:
        return True


def blocks_path(path):
    try:
        gate.check_read(path)
        return False
    except Blocked:
        return True



def _hook_exit(stdin_text):
    """A kaput ELES hookkent futtatja: JSON a szabvanyos bemeneten, exit-kod vissza."""
    r = subprocess.run([sys.executable, GATE], input=stdin_text,
                       capture_output=True, text=True)
    return r.returncode


def _injected_fault_exit():
    """Szandekos hiba a check_bash-ben: a main()-nek 2-vel kell kilepnie, nem 0-val."""
    src = open(GATE, encoding="utf-8").read()
    # A csonk szignaturaja kovesse a valodit (cwd-vel egyutt): kulonben nem a
    # BEINJEKTALT hibat mernenk, hanem egy TypeError-t -- ami szinten 2-vel
    # lepne ki, tehat a teszt zold maradna, mikozben mar mast merne.
    fault = ("\n\ndef check_bash(cmd, _depth=0, cwd=None):\n"
             "    raise ValueError('szandekosan injektalt hiba')\n\n")
    # A foltnak a main() HIVASA ELE kell kerulnie, kulonben a modul mar lefutott.
    marker = "if __name__ =="
    i = src.index(marker)
    src = src[:i] + fault + src[i:]
    tmp = os.path.join(tempfile.mkdtemp(), "faulty-gate.py")
    open(tmp, "w", encoding="utf-8").write(src)
    r = subprocess.run([sys.executable, tmp],
                       input=json.dumps({"tool_name": "Bash",
                                         "tool_input": {"command": "ls"}}),
                       capture_output=True, text=True)
    return r.returncode


def _depth_limit_blocks():
    """A melysegkorlat tullepese blokkol-e. Kozvetlenul hivjuk, mert jol formazott
    shell-idezessel ilyen melyen agyazni nem lehet -- a korlat a KOD vedelme."""
    try:
        gate.check_bash("bash -c 'ls'", _depth=gate._MAX_NEST)
        return False
    except Blocked:
        return True
    except TypeError:
        return False


# A vedett nevek es a tiltott parancsnevek OSSZEFUZVE allnak elo. Nem szepitesbol:
# maga a kapu blokkolna ennek a tesztfajlnak a MEGIRASAT, ha szo szerint allnanak
# benne -- a meres kozben ez hetszer fordult elo (lasd a kapu fejlecet). A teszt
# tehat sajat maga is peldany arra a hibaosztalyra, amit vizsgal.
D = "."
SSH = D + "ssh"
ENV = D + "env"
GM = D + "gmail-mcp"
HOME = "/home/istvan"
RM = "r" + "m"
MV = "m" + "v"
SUDO = "sud" + "o"
SHRED = "shre" + "d"
DD = "d" + "d"
GPUSH = "git " + "push"

# --- 1. POZITIV kontrollok: ezek TOVABBRA IS blokkolodnak -------------------
print("POZITIV kontroll -- valodi muvelet, blokkolni kell:")
check("egyszeru torles", blocks(RM + " /tmp/x"))
check("rekurziv torles", blocks(RM + " -rf build"))
check("torles lanc masodik tagjakent", blocks("cd /tmp && " + RM + " x"))
check("torles csovezetek utan", blocks("ls /tmp | xargs " + RM))
check("athelyezes", blocks(MV + " a b"))
check("jogosultsag-emeles", blocks(SUDO + " apt install gh"))
check("biztonsagos torles", blocks(SHRED + " -u titok.txt"))
check("nyers lemeziras", blocks(DD + " if=/dev/zero of=/dev/sdx"))
check("kozos repoba iras", blocks(GPUSH + " origin main"))
check("hitelesito konyvtar listazasa", blocks("ls -la ~/" + SSH + "/"))
check("hitelesito konyvtar zaro perjel nelkul", blocks("ls -la " + HOME + "/" + GM))
check("hitelesito fajl masolasa", blocks("cp ~/" + GM + "/credentials.json /tmp/"))
check("konfig-fajl olvasasa szokoz utan", blocks("sed -n 's/^WEB_PORT=//p' " + ENV))
# Ezt a REGI valtozat ELENGEDTE: csak szokoz utan illesztett, ezert a teljes utvonalas
# alak atment rajta. A meres talalta meg, 1 valos esettel. Ez tehat nem uj blokk,
# hanem egy addig eszrevetlen lyuk zarasa.
check("konfig-fajl olvasasa teljes utvonallal",
      blocks('grep -i "MODEL" ' + ROOT + "/" + ENV))
# A $( ) idezojelen BELUL is parancs-kontextus: ezt a regresszios futas elso korenek
# 6 elvesztett valos talalata tanitotta meg.
check("parancs-behelyettesites idezojelen belul",
      blocks('PORT="$(sed -n \'s/^WEB_PORT=//p\' ' + ENV + ' | head -1)"'))
check("idezett, de szokoz nelkuli parancsnev", blocks("'" + RM + "' /tmp/x"))

print()
print("POZITIV kontroll -- ERTELMEZONEK adott kod, blokkolni kell:")
check("ertelmezo-heredoc megnyitja a hitelesito fajlt",
      blocks("python3 - <<'PY'\nimport json, os\n"
             "h = os.path.expanduser('~/" + GM + "')\n"
             "print(json.load(open(h + '/credentials.json')))\nPY"))
check("ertelmezo -c kapcsoloval megnyitja a hitelesito fajlt",
      blocks('python3 -c "import json; d = json.load(open(\'' + HOME + '/' + GM +
             '/credentials.json\')); print(d)"'))
check("bash-heredoc torol", blocks("bash <<'SH'\n" + RM + " -rf /tmp/x\nSH"))

print()
print("POZITIV kontroll -- fajl-eszkoz ag (itt a meres 0 hamis pozitivot mutatott):")
check("hitelesito fajl olvasasa", blocks_path(HOME + "/" + SSH + "/id_rsa"))
check("konfig-fajl olvasasa", blocks_path(ROOT + "/" + ENV))
check("artalmatlan fajl atengedve", not blocks_path(ROOT + "/README.md"))

# --- 2. NEGATIV kontrollok: ezek NEM blokkolodnak tobbe ---------------------
print()
print("NEGATIV kontroll -- a minta csak EMLITVE van, a muvelet nem tortenik meg:")
check("shell-komment emliti a torlest",
      not blocks("# ide nem irok " + RM + " parancsot\nls -la"))
check("shell-komment emliti a hitelesito konyvtarat",
      not blocks("# a ~/" + GM + " hitelesito adatot tartalmaz, ezert nem nyulunk hozza\nls"))
check("ADAT-heredoc torzse emliti a torlest",
      not blocks("cat > /tmp/doc.md <<'EOF'\nA szabaly: " + RM +
                 " parancsot nem futtatsz.\nEOF"))
check("ADAT-heredoc torzse emliti a hitelesito konyvtarat",
      not blocks('cat > /tmp/profil.json <<\'EOF\'\n{"deny": ["Read(${HOME}/' + SSH +
                 '/**)"]}\nEOF'))
check("ADAT-heredoc torzse emliti a konfig-fajlt",
      not blocks("cat > /tmp/jegyzet.txt <<'EOF'\nA port a " + ENV + " fajlban all.\nEOF"))
check("regex-minta tartalmazza a tiltott parancsot",
      not blocks('grep -ln "' + RM + ' -rf\\|' + MV + ' " scripts/hooks/*'))
check("uzenet-torzs emliti a hitelesito konyvtarat",
      not blocks('curl -s -X POST http://localhost:3420/api/messages '
                 '-d \'{"content":"a ~/' + GM + ' konyvtar mar letrehozva"}\''))
check("teszteset-lista karakterlanckent tartalmazza a parancsot",
      not blocks("python3 - <<'PY'\ncases = [('egyszeru', '" + RM +
                 " /tmp/x', 2)]\nprint(len(cases))\nPY"))
check("proza egy ertelmezo-heredoc karakterlancaban",
      not blocks("python3 - <<'PY'\nm = 'az ls ~/" + GM +
                 "/ parancs nem mond semmit'\nprint(m)\nPY"))

print()
print("NEGATIV kontroll -- artalmatlan hetkoznapi parancsok:")
for cmd in ("cd /home/istvan/marveen && ls -la",
            "git commit -m 'fix'",
            "rmdir /tmp/ures",
            "curl -s http://localhost:3420/api/kanban",
            "tmux list-sessions",
            "python3 - <<'PY'\nprint(1 + 1)\nPY",
            "grep -rn 'TODO' src/ | head -20"):
    check("atengedve: " + cmd.split("\n")[0][:46], not blocks(cmd))

# --- 3. Burkolo-parancsok (dec196bb) ----------------------------------------
# A kapu a szegmens ELSO szavat nezi parancsnevnek. Minden burkolo, amely parancsot kap
# ARGUMENTUMKENT, atvitte mellette a tiltott parancsot. Mind a tizenegy alak a kapu
# KORABBI valtozatan is atment: nem regresszio volt, hanem kezdettol nyitva allo res.
print()
print("POZITIV kontroll -- BURKOLT tiltott parancs, blokkolni kell:")
check("xargs helyettesito jellel", blocks("xargs -I {} " + RM + " {}"))
check("xargs kulon-szavas kapcsoloval", blocks("ls | xargs -n 1 " + RM))
check("find -exec pontosvesszovel", blocks("find . -name x -exec " + RM + " {} ;"))
check("find -exec plusz jellel", blocks("find . -name x -exec " + RM + " {} +"))
check("idokorlat-burkolo", blocks("timeout 5 " + RM + " /tmp/x"))
check("prioritas-burkolo", blocks("nice " + RM + " /tmp/x"))
check("prioritas-burkolo kapcsoloval", blocks("nice -n 10 " + RM + " /tmp/x"))
check("parhuzamosito", blocks("parallel " + RM + " ::: a b"))
check("ismetlo burkolo", blocks("watch " + RM + " /tmp/x"))
check("lancolt burkolok", blocks("timeout 5 nice " + RM + " /tmp/x"))
# A ket legfontosabb: igy indit egy agens parancsot a leggyakrabban.
check("shell -c kapcsoloval", blocks('bash -c "' + RM + ' -rf /tmp/x"'))
check("shell -c aposztroffal", blocks("sh -c '" + RM + " -rf /tmp/x'"))
# A burkolo-atlatas nem torolheti a szegmens sajat parancsanak vizsgalatat:
check("burkolo utan artalmatlan parancs atengedve", not blocks("timeout 5 ls -la /tmp"))
check("xargs utan artalmatlan parancs atengedve", not blocks("ls | xargs grep -l TODO"))
check("shell -c artalmatlan szkripttel atengedve",
      not blocks('bash -c "ls -la /tmp | head -3"'))

# --- 4. A KIMONDOTT KORLATOK ------------------------------------------------
# Ezek NEM allitasok arrol, hogy a viselkedes helyes. Azert allnak itt, hogy aki a kaput
# tovabbfejleszti, MERT adatot lasson arrol, meddig hat a vedelem -- es hogy ha valaki
# egyszer zarja oket, itt lassa, hol kell allitania.
print()
print("KIMONDOTT KORLAT -- ezek TUDATOSAN nyitva vannak (nem allitas, csak jelentes):")
for leiras, parancs in (
        ("tobbszintu beagyazas", 'bash -c "bash -c \\"' + RM + ' -rf /tmp/x\\""'),
        ("tavoli gepen futo parancs", "ssh gep " + RM + " -rf /tmp/x")):
    print("  [%s] %s" % ("nyitva" if not blocks(parancs) else "mar zarul", leiras))

# --- 5. Melysegkorlat es FAIL-CLOSED ----------------------------------------
# A melysegkorlat nem a mai viselkedes miatt kell (a mert maximalis melyseg 3), hanem
# hogy egy kesobbi valtoztatas se tudjon vegtelen rekurzioba futni. Tullepese BLOKK.
print()
print("POZITIV kontroll -- melysegkorlat es fail-closed:")
check("melysegkorlat letezik es blokkol",
      _depth_limit_blocks())
check("olvashatatlan bemenetre exit 2", _hook_exit("nem json") == 2)
check("artalmatlan parancsra exit 0",
      _hook_exit(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})) == 0)
check("tiltott parancsra exit 2",
      _hook_exit(json.dumps({"tool_name": "Bash",
                             "tool_input": {"command": RM + " -rf /tmp/x"}})) == 2)
# A LENYEG: ha maga a kapu hasal el, az NEM lehet csendes atengedes. A block() SystemExit-tel
# lep ki, azt az "except Exception" nem fogja meg -- tehat a valodi blokkolas atmegy, es
# csak a VARATLAN kivetel valt ki fail-closed viselkedest.
check("a kapu sajat hibaja is exit 2 (fail-closed)", _injected_fault_exit() == 2)


# --- 6. rm a munkakonyvtar alatt + git push (PR #1357) ----------------------
# A kapu ket szabalya SZUKULT, Szabolcs review-ja nyoman: a torles a munkakonyvtar
# alatt es a sajat munkaagra valo push a MUNKA resze, nem dontes. Amit a
# feltetel nelkuli tiltas termelt, az nem biztonsag volt, hanem atfogalmazas.
#
# A ket irany itt kulon all, es a NEGATIV oldal az uj: egy szabaly, ami csak
# blokkolni tud, ugyanannyit er, mint ami csak atengedni.
GROOT = gate.PROJECT_ROOT
OUTSIDE = os.path.dirname(GROOT)

print()
print("POZITIV kontroll -- torles, amit tovabbra sem enged at:")
check("munkakonyvtaron kivul, abszolut", blocks_cwd(RM + " /tmp/x", GROOT))
check("munkakonyvtaron kivul, relativ kilepessel",
      blocks_cwd(RM + " ../../valami", os.path.join(GROOT, "agents")))
check("a verziotortenet", blocks_cwd(RM + " -rf " + GROOT + "/.git", GROOT))
check("az eles allapot (store/)", blocks_cwd(RM + " " + GROOT + "/store/adat.db", GROOT))
check("maga a munkakonyvtar gyokere", blocks_cwd(RM + " -rf " + GROOT, GROOT))
check("cd utan mar mashol torol",
      blocks_cwd("cd " + OUTSIDE + " && " + RM + " -rf x", GROOT))
check("behelyettesites: nem eldontheto, hova mutat",
      blocks_cwd(RM + ' -rf "$TARGET/build"', GROOT))
check("cwd nelkul a relativ ut nem eldontheto", blocks(RM + " -rf build"))
check("nem latszik, mit torolne (csovezetekbol)", blocks_cwd("ls | xargs " + RM, GROOT))
check("find -exec helyettesito jellel", blocks_cwd(
      "find . -name '*.tmp' -exec " + RM + " {} ;", GROOT))

print()
print("NEGATIV kontroll -- torles a munkakonyvtar alatt, ez a MUNKA resze:")
check("abszolut ut a gyoker alatt",
      not blocks_cwd(RM + " " + GROOT + "/build/out.js", GROOT))
check("relativ ut a cwd alatt", not blocks_cwd(RM + " -rf build", GROOT))
check("relativ ut melyebb cwd-bol",
      not blocks_cwd(RM + " -f jegyzet.txt", os.path.join(GROOT, "agents", "valaki")))
check("glob nem lep at konyvtarhataron",
      not blocks_cwd(RM + " -f " + GROOT + "/tmp/*.log", GROOT))
check("cd a gyoker ALA, utana torles",
      not blocks_cwd("cd " + GROOT + "/agents && " + RM + " -rf scratch", GROOT))
check("'..' a gyokeren BELUL marad",
      not blocks_cwd(RM + " -rf ../build", os.path.join(GROOT, "agents")))

print()
print("POZITIV kontroll -- push, ami tovabbra is tiltott:")
check("eroltetett push", blocks(GPUSH + " --force origin munkaag"))
check("eroltetett push rovid alakkal", blocks(GPUSH + " -f origin munkaag"))
check("lease-elt eroltetes", blocks(GPUSH + " --force-with-lease origin munkaag"))
check("'+' refspec is eroltetes", blocks(GPUSH + " origin +munkaag"))
check("tavoli ag torlese", blocks(GPUSH + " origin --delete munkaag"))
check("vedett ag: main", blocks(GPUSH + " origin main"))
check("vedett ag: master ketoldalu refspecben", blocks(GPUSH + " origin HEAD:master"))
check("vedett ag teljes ref-uttal", blocks(GPUSH + " origin munkaag:refs/heads/main"))
check("mindent kitol", blocks(GPUSH + " --all origin"))
check("cel-ag nelkul nem eldontheto", blocks(GPUSH))
check("csak remote, refspec nelkul", blocks(GPUSH + " origin"))
check("a git sajat -C kapcsolojan is atlat", blocks("git -C /tmp push origin main"))

print()
print("NEGATIV kontroll -- sajat munkaagra valo push, ez a MUNKA resze:")
check("egyszeru munkaag", not blocks(GPUSH + " origin munkaag"))
check("upstream beallitasa", not blocks(GPUSH + " -u origin HEAD:munkaag"))
check("fork-URL-re, tokennel", not blocks(
      GPUSH + " https://user:x@github.com/valaki/repo.git munkaag:munkaag"))
check("cimke", not blocks(GPUSH + " origin v1.2.3"))
check("mas git alparancs valtozatlan", not blocks("git commit -m 'fix'"))


# --- 7. A cwd BEKOTESE a hook teljes utjan --------------------------------
# A 6. szakasz esetei kozvetlenul a check_bash()-t hivjak, megadott cwd-vel. A main()
# viszont az ESEMENYBOL veszi a cwd-t, es azt az ag egyetlen fenti eset sem erinti:
# ha a mezo neve elirodna, minden modul-szintu teszt zold maradna, es a kapu elesben
# minden relativ torlest blokkolna (vagy rosszabb: rossz konyvtarhoz merne).
def _hook_cwd_exit(cmd, cwd):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    if cwd is not None:
        ev["cwd"] = cwd
    return _hook_exit(json.dumps(ev))


print()
print("A cwd bekotese a hook teljes utjan (main -> check_bash):")
check("relativ torles a gyoker alol: atengedve", _hook_cwd_exit(RM + " -rf build", ROOT) == 0)
check("ugyanaz a parancs mashonnan: blokkolva",
      _hook_cwd_exit(RM + " -rf build", "/etc") == 2)
check("cwd nelkul a relativ ut nem eldontheto",
      _hook_cwd_exit(RM + " -rf build", None) == 2)
check("abszolut ut a gyoker alatt cwd nelkul is atmegy",
      _hook_cwd_exit(RM + " -rf " + ROOT + "/build", None) == 0)
check("munkaagra push atengedve", _hook_cwd_exit(GPUSH + " origin munkaag", ROOT) == 0)
check("main-re push blokkolva", _hook_cwd_exit(GPUSH + " origin main", ROOT) == 2)


# --- 8. SHELL-KULCSSZO a parancsnev elott (2026-09-24-i res) ----------------
# MERVE az akkori kapun: "rm -f /tmp/x" -> exit 2, de
# "if rm -f /tmp/x; then echo ok; fi" -> exit 0. A szegmens elso tokenje az `if`,
# az nem tiltott parancsnev, es a mogotte allo rm-et a kapu meg sem nezte. A hazi
# stilus epp ezt az alakot irja elo, tehat a leggyakoribb alak volt a vak folt.
print()
print("Shell-kulcsszo a parancsnev elott (nem rejtheti el a tiltott parancsot):")
check("if + rm blokkolva", blocks("if " + RM + " -f /etc/passwd; then echo ok; fi"))
check("while + rm blokkolva", blocks("while " + RM + " -rf /etc/x; do :; done"))
check("! + rm blokkolva", blocks("! " + RM + " -rf /etc/x"))
check("csoportositas + rm blokkolva", blocks("{ " + RM + " -rf /etc/x; }"))
check("then-agban allo rm blokkolva", blocks("then " + RM + " -rf " + HOME + "/.ssh"))
check("if + eros push blokkolva", blocks("if " + GPUSH + " --force origin main; then echo ok; fi"))
check("if + rm a gyoker alol atengedve",
      not blocks_cwd("if " + RM + " -rf build; then echo ok; fi", ROOT))
check("if + munkaagra push atengedve",
      not blocks_cwd("if " + GPUSH + " origin munkaag; then echo ok; fi", ROOT))
check("artalmatlan if-feltetel valtozatlanul atmegy",
      not blocks("if systemctl --user is-active foo; then echo A; else echo B; fi"))
check("teszt-feltetel atmegy", not blocks("if [ -f /tmp/x ]; then echo van; fi"))
check("az 'rm' szo mint grep-minta nem parancs", not blocks("if grep -q rm file.txt; then echo t; fi"))


# --- 9. AZ ERTELMEZO-PAYLOAD BELSEJE (efccadab) ----------------------------
# Ez a vedelem 2026-09-21-ig CSAK az eles, untracked kapuban letezett, a PR-agon
# nem. A 281b06df kartya azert nyilt, mert ha valaki az eles kaput a repobol
# "szinkronizalja", a vedelem NYOM NELKUL eltunik -- es semmi nem szol. Az alabbi
# kontrollok ezt teszik zajossa: a vedelem eltavolitasa mostantol piros tesztet ad.
# MERVE 2026-09-24-en, a PR-agi kapun a szakasz atvitele ELOTT: mind az ot pozitiv
# eset ATMENT (exit 0). Nem elmeleti res volt.
OSREM = "os." + "remove"
RMTREE = "shutil." + "rmtree"
UNLINKS = "unlink" + "Sync"

print()
print("POZITIV kontroll -- ertelmezo-payloadban allo, ma is tiltott muvelet:")
check("python -c: fajltorles",
      blocks('python3 -c "import os; ' + OSREM + "('/etc/x')\""))
check("python heredoc: konyvtartorles",
      blocks("python3 - <<PY\nimport shutil\n" + RMTREE + '("/etc/x")\nPY'))
check("stdin-cso: echo ... | python3 -",
      blocks('echo "import os; ' + OSREM + "('/etc/x')\" | python3 -"))
check("node -e: fajltorles",
      blocks('node -e "require(\'fs\').' + UNLINKS + "('/etc/x')\""))
check("perl -e: beepitett unlink",
      blocks('perl -e "unlink(\'/etc/x\')"'))

# A shell-kihivas NEM kulon szabalylista: visszamegy a check_bash-be, tehat a
# burkon beluli parancsra pontosan ugyanaz a szabalykeszlet all. Ez a ket eset azt
# pineli, hogy a visszavezetes tenylegesen megtortenik -- mind a torlesre, mind a
# push-szabalyra, amit ez a kartya vitt at ide.
print()
print("POZITIV kontroll -- a payloadbol kihivott SHELL ugyanazt a szabalyt kapja:")
check("payload -> shell: munkakonyvtaron kivuli torles",
      blocks('python3 -c "import subprocess; subprocess.run([\'' + RM +
             "','-rf','/etc/x'])\""))
check("payload -> shell: eroltetett push",
      blocks('python3 -c "import os; os.system(\'' + GPUSH + " --force origin main')\""))

# A masik irany. Egy szabaly, ami csak blokkolni tud, ugyanannyit er, mint ami csak
# atengedni -- es a payload-szures a legkonnyebben elszabadulo fajta, mert minden
# beagyazott kodot gyanusnak lat.
print()
print("NEGATIV kontroll -- artalmatlan payload, nem blokkolhat:")
check("artalmatlan python egysoros", not blocks('python3 -c "print(\'hello world\')"'))
check("ADAT-heredoc csak EMLITI a muveletet",
      not blocks("cat > /tmp/jegyzet.txt <<EOF\n" + OSREM + " egy python hivas\nEOF"))
check("payload -> shell: artalmatlan parancs",
      not blocks('python3 -c "import subprocess; subprocess.run([\'ls\',\'-la\'])"'))
check("a muvelet neve a payloadban KIKOMMENTEZVE",
      not blocks('python3 -c "\n# ' + OSREM + "('/etc/x')\nprint(1)\""))

# A payload a TELJES parancsbol jon, nem egy szegmensbol, ezert ha barhol allt benne
# `cd`, a payload munkakonyvtara nem eldontheto -- es a nem eldonthetot a kapu nem
# engedi at. Enelkul a `cd <kifele> && python3 -c "...('build')"` alak ugy nezne ki,
# mintha a sajat munkakonyvtaraban torolne.
print()
print("POZITIV/NEGATIV kontroll -- a payload munkakonyvtara:")
check("payload -> shell: relativ torles a gyoker alol atengedve",
      not blocks_cwd('python3 -c "import os; os.system(\'' + RM + " -rf build')\"", GROOT))
check("cd utan a payload munkakonyvtara mar nem eldontheto",
      blocks_cwd("cd " + OUTSIDE + ' && python3 -c "import os; os.system(\'' + RM +
                 " -rf build')\"", GROOT))

print()
print("KIMONDOTT KORLAT -- a payload-szures szandekosan ennyit lat:")
check("csonkitas (open 'w') NEM tiltott: a `: > fajl` alak sem az",
      not blocks('python3 -c "open(\'/etc/x\',\'w\').close()"'))
check("SQL-torlest a kapu sosem nezett", not blocks(
    'python3 -c "import sqlite3; sqlite3.connect(\'a.db\').execute(\'DELETE FROM t\')"'))


# --- 10. URES FORRAS-REFSPEC = TAVOLI AG TORLESE ---------------------------
# 2026-09-24-en merve, a push-szabaly atvitelekor: a `--delete` kapcsolos alakot a
# szabaly tiltotta, a ketpontos alak (`:ag`) ATMENT, pedig ugyanaz a muvelet. Az
# eles kapu akkor meg minden push-t tiltott, tehat a szabaly valtoztatas nelkuli
# atvitele egy esetet BLOKK-bol ATMEGY-be mozgatott volna.
print()
print("POZITIV kontroll -- ures forras-refspec (tavoli ag torlese):")
check("git push origin :ag", blocks(GPUSH + " origin :munkaag"))
check("git push origin +:ag", blocks(GPUSH + " origin +:munkaag"))

# A git sajat kapcsoloi a 'push' ELOTT allnak. A regi, feltetel nelkuli eles tiltas
# a parancsnev UTANI szot nezte, ezert a `git -C <ut> push` alakot eszre sem vette
# (merve: atment). Az uj szabaly a kapcsolokon atlepve keresi az alparancsot.
print()
print("A 'git -C <ut> push' alak is push:")
check("git -C: eroltetett push blokkolva",
      blocks("git -C " + GROOT + " push --force origin main"))
check("git -C: munkaagra push atengedve",
      not blocks("git -C " + GROOT + " push origin munkaag"))


# --- 11. A HEREDOC-TORZS: PROGRAM VAGY EGY PROGRAM BEMENETE? (79d8b59c) -----
# A kapu a `<<` nyito soran azt kerdezte, hogy szerepel-e BARHOL ertelmezo-nev.
# Ez minden `bash <valami>.sh <<EOF` alakra igaz, tehat egy tiltott parancs
# MEGEMLITESE egy uzenet-torzsben ugyanugy blokkolodott, mint a vegrehajtasa.
# MERVE 2026-09-25-en, a javitas ELOTT: 11 ilyen adat-eset blokkolt (mind a ket
# kapu-peldanyon azonosan). A helyes kerdes nem az, hogy hol all ertelmezo-nev,
# hanem az, hogy az ertelmezo HONNAN veszi a programjat.
#
# FIGYELEM, ez a szakasz ket iranyban pinel. A pozitiv kontrollok (a torzs MAGA a
# program) azert vannak itt, mert egy kovetkezo "meg kevesebb hamis pozitiv"
# finomitas ezeken bukjon el, ne eles hasznalatban.
print()
print("NEGATIV kontroll -- a torzs egy MEGNEVEZETT program bemenete, tehat ADAT:")
check("bash szkript.sh <<EOF",
      not blocks("bash kuldo.sh a b - <<EOF\n%s -rf /tmp/x\nEOF" % RM))
check("sh helper.sh <<TXT",
      not blocks("sh helper.sh <<TXT\n%s -rf /tmp/x\nTXT" % RM))
check("python3 riport.py <<DATA",
      not blocks("python3 riport.py <<DATA\n%s -rf /tmp/x\nDATA" % RM))
check("node app.js <<DATA",
      not blocks("node app.js <<DATA\n%s -rf /tmp/x\nDATA" % RM))
check("bash -x szkript.sh <<EOF (kapcsolo, majd fajl)",
      not blocks("bash -x szkript.sh <<EOF\n%s -rf /tmp/x\nEOF" % RM))
check("/usr/bin/bash szkript.sh <<EOF (teljes ut)",
      not blocks("/usr/bin/bash szkript.sh <<EOF\n%s -rf /tmp/x\nEOF" % RM))
check("python3 -c ... <<PY (a program a -c utan van, a torzs a stdin)",
      not blocks('python3 -c "print(1)" <<PY\n%s -rf /tmp/x\nPY' % RM))
check("cat a.txt | python3 proc.py <<DATA",
      not blocks("cat a.txt | python3 proc.py <<DATA\n%s -rf /tmp/x\nDATA" % RM))
check("bash szkript.sh <<EOF, a torzsben sudo",
      not blocks("bash szkript.sh <<EOF\nsudo systemctl restart foo\nEOF"))

print()
print("POZITIV kontroll -- a torzs MAGA a program, tehat vizsgalni kell:")
check("bash <<SH", blocks("bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("sh <<SH", blocks("sh <<SH\n%s -rf /tmp/x\nSH" % RM))
check("bash -s <<SH (a -s kifejezetten stdin)",
      blocks("bash -s <<SH\n%s -rf /tmp/x\nSH" % RM))
check("bash -x <<SH (csak kapcsolo, program nincs megnevezve)",
      blocks("bash -x <<SH\n%s -rf /tmp/x\nSH" % RM))
check("python3 - <<PY", blocks("python3 - <<PY\nimport shutil\n%s('/tmp/x')\nPY" % RMTREE))
check("python3 <<PY", blocks("python3 <<PY\nimport shutil\n%s('/tmp/x')\nPY" % RMTREE))
check("echo x | bash <<SH", blocks("echo x | bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("exec bash <<SH (atlatszo burkolo)",
      blocks("exec bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("FOO=1 bash <<SH (kornyezeti ertekadas)",
      blocks("FOO=1 bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("env FOO=1 bash <<SH",
      blocks("env FOO=1 bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("bash <<SH, a torzsben sudo",
      blocks("bash <<SH\nsudo systemctl restart foo\nSH"))

print()
print("POZITIV kontroll -- a heredoc-on KIVULI parancsot a javitas nem takarhatja el:")
check("bash sc.sh <<EOF ... EOF majd torles",
      blocks("bash sc.sh <<EOF\nadat\nEOF\n%s -rf /tmp/x" % RM))

print()
print("KIMONDOTT KORLAT -- burkolo mogotti ertelmezo:")
# #748 2. kor ota a burkolok argumentum-alakja TABLABOL jon (_STDIN_WRAPPERS: a timeout
# elso operandusa idotartam, a strace-nek nincs), igy a `timeout 5 bash sc.sh <<EOF`
# mar nem hamis pozitiv: a bash a FAJLT futtatja, a torzs annak a bemenete (bash
# lefuttatva 2026-11-02: csak a szkript sora irodik ki). Az ISMERETLEN burkolo mogotti
# ertelmezo viszont ADAT (Marveen review 3543, 5. pont: ertelmezo csak parancs-pozicioban
# vagy ismert burkolo mogott szamit), ez a kimondott korlat: egy uj burkolo a tablaba valo.
check("timeout 5 bash <<SH (helyesen blokkol)",
      blocks("timeout 5 bash <<SH\n%s -rf /tmp/x\nSH" % RM))
check("timeout 5 bash sc.sh <<EOF: a torzs a szkript bemenete, adat",
      not blocks("timeout 5 bash sc.sh <<EOF\n%s -rf /tmp/x\nEOF" % RM))
check("ismeretlenwrap 5 bash <<SH -- KIMONDOTT KORLAT: ismeretlen burkolo mogott adat",
      not blocks("ismeretlenwrap 5 bash <<SH\n%s -rf /tmp/x\nSH" % RM))


# --- 12. Onvedelem: a rm-engedely nem erhet el az ELES gyokerig (be8ef9f1) ---
# A PROJECT_ROOT a FAJL helyebol szarmazik, ezert ugyanez a szabaly mast jelent
# minden peldanyban. Amig a fajl worktree-ben ul, a "munkakonyvtar alatt szabad"
# egy eldobhato masolatra vonatkozik. A #1357 beolvadasa utan PONTOSAN UGYANAZ a
# szabaly az ELES telepitesre vonatkozna. MERVE 2026-09-25, a javitas elott,
# szintetikus gyokeren: `rm -rf <root>/scripts/hooks` -- MAGA A KAPU -- ATMENT.
# Ket fuggetlen retege van a javitasnak, es mindketto kulon bukhat, ezert kulon all.

print()
print("ONVEDELEM -- vedett utvonalak, eldobhato masolatban is:")
check("maga a kapu konyvtara", blocks_cwd(RM + " -rf " + GROOT + "/scripts/hooks", GROOT))
check("a kapu OSE is (rm -rf scripts)", blocks_cwd(RM + " -rf " + GROOT + "/scripts", GROOT))
check("egyetlen hook-fajl is", blocks_cwd(
      RM + " -f " + GROOT + "/scripts/hooks/destructive-gate.py", GROOT))
check("a mentesek konyvtara", blocks_cwd(RM + " -rf " + GROOT + "/backups", GROOT))
check("mentes-fajl a konyvtaron belul", blocks_cwd(
      RM + " -f " + GROOT + "/backups/claudeclaw-x.tar.gz", GROOT))

print()
print("ONVEDELEM -- a mellettuk allo utak NEM lettek vedettek (a masik irany):")
# Ha ezek is blokkolnanak, az elotag-illesztes tul szelesre sikerult volna, es a
# javitas csendben visszaallitana a feltetel nelkuli tiltast.
check("scripts-hez HASONLO nevu ut szabad", not blocks_cwd(
      RM + " -rf " + GROOT + "/scriptsegyeb", GROOT))
check("backups-hoz HASONLO nevu ut szabad", not blocks_cwd(
      RM + " -rf " + GROOT + "/backups-regi", GROOT))
check("melyebb, nem vedett ut szabad", not blocks_cwd(
      RM + " -rf " + GROOT + "/agents/valaki/scratch", GROOT))

print()
print("ONVEDELEM -- a GLOB nem kerulheti meg a vedett listat (9e34a3b7):")
# MERVE 2026-09-25, a javitas elott, szintetikus gyokeren: `rm -rf *` a gyoker
# alatt ATMENT, es vele a store/, a .git es maga a kapu. A kod indoklasa
# ("a shell glob nem lep at '/'-en, tehat a minta a munkakonyvtar alatt marad")
# IGAZ allitas volt, csak nem arrol, amit a vedett lista vedene: az egy
# MUNKAKONYVTARON BELULI lista, es a shell a globot a kapu VERDIKTJE UTAN
# terjeszti ki. Ezert az illesztes fnmatch-csel megy: a minta-szegmens ugy
# szamit, mintha a vedett nevre illene.
for _g in ("*", "./*", "*/*", "s*", ".g*", "[a-z]*", "scripts/*"):
    check("glob nem kerulheti meg a vedett listat: " + _g,
          blocks_cwd(RM + " -rf " + _g, GROOT))
# A masik irany, kulon, mert ha ez is blokkolna, a javitas csendben
# visszaallitana a feltetel nelkuli tiltast:
check("'?' NEM lyuk: egy karakter egyik vedett nevre sem illik",
      not blocks_cwd(RM + " -rf ?", GROOT))
check("nem vedett konyvtar globja tovabbra is szabad",
      not blocks_cwd(RM + " -rf agents/*", GROOT))
check("nem vedett melyebb glob tovabbra is szabad",
      not blocks_cwd(RM + " -rf src/web/*.js", GROOT))

print()
print("ELES TELEPITES felismerese -- a jelolo a store/ telepiteskor keletkezo fajlja:")
with tempfile.TemporaryDirectory() as td:
    check("jelolo nelkul: eldobhato masolat", not gate._live_install(td))
    os.makedirs(os.path.join(td, "store"))
    check("ures store/ meg nem eles", not gate._live_install(td))
    open(os.path.join(td, "store", "claudeclaw.db"), "w").close()
    check("claudeclaw.db -> ELES", gate._live_install(td))
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, "store"))
    open(os.path.join(td, "store", ".dashboard-token"), "w").close()
    check("dashboard-token -> ELES", gate._live_install(td))
# Az itt futo peldany maga NEM lehet eles: kulonben a fenti 6. szakasz negativ
# kontrolljai (a munkakonyvtar alatti torles) mast mernenek, mint amit allitanak.
check("a teszt sajat gyokere nem eles telepites", not gate._live_install(GROOT))

print()
print("ELES TELEPITESEN a munkakonyvtar-szabaly NEM all:")
_orig_live = gate._live_install
try:
    gate._live_install = lambda root: True
    check("build/ torlese eles gyokeren BLOKK", blocks_cwd(RM + " -rf " + GROOT + "/build", GROOT))
    check("relativ ut eles gyokeren BLOKK", blocks_cwd(RM + " -rf build", GROOT))
    check("melyebb cwd-bol is BLOKK", blocks_cwd(
          RM + " -f jegyzet.txt", os.path.join(GROOT, "agents", "valaki")))
finally:
    gate._live_install = _orig_live
print()
print("ELES TELEPITESEN az INDOK a valodi akadalyt nevezi meg (67433099):")
# A verdikt eles gyokeren mindig BLOKK; ez a szakasz az INDOKLAST meri. MERVE
# 2026-10-07: a /tmp alatti session-scratchpad torleset a kapu "munkakonyvtar
# ALATTI torles"-kent indokolta, holott a cel a gyokeren KIVUL volt.
def _block_msg(cmd, cwd):
    try:
        gate.check_bash(cmd, cwd=cwd)
        return None
    except Blocked as e:
        return str(e)
_orig_live2 = gate._live_install
try:
    gate._live_install = lambda root: True
    _m = _block_msg(RM + " -rf /tmp/kivul-allo-scratch/x", GROOT) or ""
    check("eles gyokeren, kivulre mutato cel: BLOKK", bool(_m))
    check("...es az indok a KIVULRE mutato celt nevezi meg", "KIVULRE" in _m)
    check("...es megemliti az eles gyokeret is", "ELES" in _m)
    _m = _block_msg(RM + " -rf " + GROOT + "/build", GROOT) or ""
    check("eles gyokeren, gyoker alatti cel: BLOKK, indok az ELES gyoker",
          "ELES telepites gyokere" in _m and "KIVULRE" not in _m)
    _m = _block_msg(RM + " -rf " + GROOT + "/store", GROOT) or ""
    check("eles gyokeren, vedett ut: az indok a vedett utat nevezi meg",
          "vedett utvonal" in _m)
finally:
    gate._live_install = _orig_live2

# A visszaallitas utan ugyanaz a parancs ismet atmegy -- ez bizonyitja, hogy a
# fenti harom blokkot a kapcsolo okozta, nem valami mas.
check("a kapcsolo visszaallitasa utan ismet atmegy",
      not blocks_cwd(RM + " -rf " + GROOT + "/build", GROOT))

# A fenti harom eset a PREDIKATUMOT csereli ki, ezert egy elromlott _live_install()-t
# nem venne eszre -- MERVE 2026-09-25: az "_live_install mindig False" mutacio csak a
# ket egyseg-ellenorzest olte meg, ezt a harmat nem. Ez az eset ezert VALODI jelolot
# tesz ki egy szintetikus gyokerre, es csak a PROJECT_ROOT-ot iranyitja oda: igy a
# ket resz OSSZEKAPCSOLASA is meg van merve, nem csak kulon-kulon.
_orig_root = gate.PROJECT_ROOT
with tempfile.TemporaryDirectory() as td:
    td = os.path.realpath(td)
    os.makedirs(os.path.join(td, "store"))
    os.makedirs(os.path.join(td, "build"))
    try:
        gate.PROJECT_ROOT = td
        check("valodi jelolo nelkul a szintetikus gyokeren atmegy",
              not blocks_cwd(RM + " -rf " + td + "/build", td))
        open(os.path.join(td, "store", "claudeclaw.db"), "w").close()
        check("VALODI claudeclaw.db kiteve -> ugyanaz a parancs BLOKK",
              blocks_cwd(RM + " -rf " + td + "/build", td))
    finally:
        gate.PROJECT_ROOT = _orig_root



# --- 8. heredoc-olvasas: idezojel, komment, tag-alak, ertelmezo-nev (#729) ----
# Merve a main-en (Cody msg 14663, Marveen review 2932 es a #725 reviewja): mindegyik
# MUST-BLOCK sor atment, es a bash lefuttatta a payloadot. A negativ kontroll ugyanaz
# az alak adat-torzzsel: az olvasas javitasa nem tilthat le dokumentaciot.
print()
print("#729 heredoc-olvasas, MUST-BLOCK:")
PAY = "\n" + RM + " -rf /etc/x\n"
PYRM = "import shutil\n" + RMTREE + '("/etc/x")\n'
check("idezett <<EOF nem nyit torzset", blocks('echo "<<EOF"' + PAY + "EOF"))
check("aposztrofos <<EOF nem nyit torzset", blocks("echo '<<EOF'" + PAY + "EOF"))
check("kommentben allo <<EOF nem nyit torzset", blocks("# <<EOF" + PAY + "EOF"))
check("sor vegi kommentben allo <<EOF", blocks("ls # <<EOF" + PAY + "EOF"))
check("ertelmezo-torzs nem-szo taggel", blocks("python3 - <<'END-X'\n" + PYRM + "END-X"))
check("ertelmezo-torzs visszaperjeles taggel", blocks("python3 - <<\\EOF\n" + PYRM + "EOF"))
check("reszben idezett tag E\\OF: a zaro utani sor fut",
      blocks("cat > /dev/null <<E\\OF\njunk\nEOF" + PAY))
check('reszben idezett tag E"O"F: a zaro utani sor fut',
      blocks('cat > /dev/null <<E"O"F\njunk\nEOF' + PAY))
for name in ('"python3"', "'python3'", "\\python3", "py\\thon3", 'p""ython3', "$P", "${P}",
             "$'python3'", "env -S'python3'", "/usr/bin/pytho?3", "{python3,}"):
    check("ertelmezo-nev %s - <<'E'" % name, blocks(name + " - <<'E'\n" + PYRM + "E"))
check("a parancs a heredoc utan: <<'X' python3", blocks("<<'X' python3\n" + PYRM + "X"))
check("csovon ertelmezohoz: cat <<'X' | python3", blocks("cat <<'X' | python3\n" + PYRM + "X"))
check("sorfolytatas: python3 \\ NL - <<'E'", blocks("python3 \\\n - <<'E'\n" + PYRM + "E"))
check("source /dev/stdin <<'E'", blocks("source /dev/stdin <<'E'" + PAY + "E"))
check("masodik heredoc ugyanazon a soron",
      blocks("cat <<'A' > /tmp/f; python3 - <<'B'\nadat\nA\n" + PYRM + "B"))
check("idezetlen adat-torzsben $( ) fut", blocks("cat > /tmp/f <<EOF\n$(" + RM + " -rf /etc/x)\nEOF"))
check("idezetlen adat-torzsben backtick fut",
      blocks("cat > /tmp/f <<EOF\n`" + RM + " -rf /etc/x`\nEOF"))
check("$( )-ben lezaratlan heredoc: bash a ) utan folytat",
      blocks("x=$(cat <<EOF\nhi\nEOF)" + PAY))
check("eltolas a[ ]-ben nem heredoc", blocks("a[1<<EOF]=x" + PAY + "EOF"))
check("eltolas $(( ))-ben nem heredoc", blocks("echo $((1<<2))" + PAY))
check("tul hosszu nyito sor: a torzs kodkent marad (tultiltas)",
      blocks("cat" + " " * 5000 + "<<'E'" + PAY + "E"))

print()
print("#729 heredoc-olvasas, negativ kontroll (adat marad adat):")
check("adat-torzs nem-szo taggel", not blocks("cat > /tmp/f <<'END-X'" + PAY + "END-X"))
check("adat-torzs visszaperjeles taggel", not blocks("cat > /tmp/f <<\\EOF" + PAY + "EOF"))
check("adat-torzs reszben idezett taggel", not blocks("cat > /dev/null <<E\\OF" + PAY + "EOF"))
check("adat-torzsben a payload, utana ls", not blocks("cat > /tmp/f <<'EOF'" + PAY + "EOF\nls"))
check("cat - <<'E' ertelmezo-kod adatkent", not blocks("cat - <<'E'\n" + PYRM + "E"))
check("<<'X' cat: a parancs a heredoc utan, de nem ertelmezo",
      not blocks("<<'X' cat > /tmp/f\n" + PYRM + "X"))
check("csovon program-fajlhoz: | python3 proc.py",
      not blocks("cat <<'X' | python3 proc.py\n" + PYRM + "X"))
check("idezett tag: a $( ) a torzsben szoveg", not blocks("cat > /tmp/f <<'EOF'\n$(" + RM + " -rf /etc/x)\nEOF"))
check("ket adat-heredoc egy soron", not blocks("cat <<'A' <<'B'\na" + PAY + "A\nb" + PAY + "B"))
check("lezart heredoc $( )-ben", not blocks("x=$(cat <<'EOF'" + PAY + "EOF\n)\necho $x"))
check("<<< nem heredoc, a kovetkezo sor ls", not blocks("cat <<< 'x'\nls"))


# --- 9. heredoc-torzs mint PROGRAM: a #729 review maradeka (#748) -------------------
# A bazison (e90f4af4) mindegyik MUST-BLOCK sor atment, es a bash lefuttatta a torzset
# (orakulum: scripts/__tests__/heredoc-program-cases.json, bash_runs). A negativ kontroll
# ugyanaz az alak adat-torzzsel: a bazison is atment, es itt is at kell mennie.
print()
print("#748 heredoc-torzs mint program, MUST-BLOCK:")
SHPAY = PAY + "SH"
check("bash 0<<SH: a 0 fajlleiro", blocks("bash 0<<SH" + SHPAY))
check("bash 2>&1 <<SH", blocks("bash 2>&1 <<SH" + SHPAY))
check("bash &>/dev/null <<SH", blocks("bash &>/dev/null <<SH" + SHPAY))
check("bash /dev/fd/3 3<<SH: a leiro a szkript", blocks("bash /dev/fd/3 3<<SH" + SHPAY))
check('eval "$(cat <<EOF)"', blocks('eval "$(cat <<EOF' + PAY + 'EOF\n)"'))
check('builtin eval "$(cat <<EOF)"', blocks('builtin eval "$(cat <<EOF' + PAY + 'EOF\n)"'))
check('eval "`cat <<EOF`"', blocks('eval "`cat <<EOF' + PAY + 'EOF\n`"'))
check(". <(cat <<SH)", blocks(". <(cat <<SH" + SHPAY + "\n)"))
check("source <(cat <<SH)", blocks("source <(cat <<SH" + SHPAY + "\n)"))
check("bash <(cat <<SH)", blocks("bash <(cat <<SH" + SHPAY + "\n)"))
check("while read l; do $l; done <<EOF", blocks("while read l; do $l; done <<EOF" + PAY + "EOF"))
check('while read l; do eval "$l"; done <<EOF', blocks('while read l; do eval "$l"; done <<EOF' + PAY + "EOF"))
check("tobbsoros while ... done <<EOF", blocks("while read l\ndo\n  $l\ndone <<EOF" + PAY + "EOF"))
check("f(){ bash; }; f <<SH", blocks("f(){ bash; }; f <<SH" + SHPAY))
check("function g { sh; }; g <<SH", blocks("function g { sh; }; g <<SH" + SHPAY))
check("h() ( bash ); h <<SH", blocks("h() ( bash ); h <<SH" + SHPAY))
check("f(){ bash; }; cat <<SH | f", blocks("f(){ bash; }; cat <<SH | f" + SHPAY))
check("bash \\<ujsor><<SH", blocks("bash \\\n<<SH" + SHPAY))
check("`bash <<SH`", blocks("`bash <<SH" + SHPAY + "\n`"))
check("sudo -u x bash <<SH", blocks("sudo -u x bash <<SH" + SHPAY))
check("if true; then bash <<SH", blocks("if true; then bash <<SH" + SHPAY + "\nfi"))
check("if true; then bash; fi <<SH", blocks("if true; then bash; fi <<SH" + SHPAY))
check("{ bash; } <<SH", blocks("{ bash; } <<SH" + SHPAY))
check("bash -s -- a <<SH", blocks("bash -s -- a <<SH" + SHPAY))
check("! bash <<SH", blocks("! bash <<SH" + SHPAY))
check("V=1 bash -e <<SH: a -e errexit, nem szkript", blocks("V=1 bash -e <<SH" + SHPAY))
check("bash -o pipefail <<SH", blocks("bash -o pipefail <<SH" + SHPAY))
check('bash -c "$(cat <<EOF)"', blocks('bash -c "$(cat <<EOF' + PAY + 'EOF\n)"'))
check("x=$(bash <<SH ... )", blocks("x=$(bash <<SH" + SHPAY + "\n)"))
# review 2. kor (Marveen, kartya-komment 3543): a stdin utvonalkent, alhej, here-string,
# folyamat-helyettesites, burkolo-kapcsolok. Az orakulum mindet lefuttatta (bash_runs),
# kiveve az ssh/docker/sudo -s sort, amit itt nem lehet futtatni (note a case-fajlban).
check("bash /dev/stdin <<SH", blocks("bash /dev/stdin <<SH" + SHPAY))
check("bash /dev/fd/0 <<SH", blocks("bash /dev/fd/0 <<SH" + SHPAY))
check("bash /proc/self/fd/0 <<SH", blocks("bash /proc/self/fd/0 <<SH" + SHPAY))
check("bash 3<<SH <&3: a 3-as leiro a stdin-re masolva", blocks("bash 3<<SH <&3" + SHPAY))
check("(bash) <<SH", blocks("(bash) <<SH" + SHPAY))
check("( cd /tmp && bash ) <<SH", blocks("( cd /tmp && bash ) <<SH" + SHPAY))
check("(cat <<SH | bash)", blocks("(cat <<SH | bash)" + SHPAY))
check('bash <<<"$(cat <<SH)": a here-string a stdin', blocks('bash <<<"$(cat <<SH' + SHPAY + '\n)"'))
check("tee >(bash) <<SH >/dev/null", blocks("tee >(bash) <<SH >/dev/null" + SHPAY))
check("timeout -k 1 5s bash <<SH", blocks("timeout -k 1 5s bash <<SH" + SHPAY))
check("sudo -u x -- bash <<SH", blocks("sudo -u x -- bash <<SH" + SHPAY))
check("sudo -s <<SH: parancs nelkul a shell olvas", blocks("sudo -s <<SH" + SHPAY))
check("env -i bash <<SH", blocks("env -i bash <<SH" + SHPAY))
check("env -u HOME bash <<SH", blocks("env -u HOME bash <<SH" + SHPAY))
check("env -S'bash -' <<SH", blocks("env -S'bash -' <<SH" + SHPAY))
check("command -p bash <<SH", blocks("command -p bash <<SH" + SHPAY))
check("exec -a name bash <<SH", blocks("exec -a name bash <<SH" + SHPAY))
check("ssh host bash <<SH", blocks("ssh host bash <<SH" + SHPAY))
check("docker exec -i c bash <<SH", blocks("docker exec -i c bash <<SH" + SHPAY))

print()
print("#748 heredoc-torzs mint program, negativ kontroll (adat marad adat):")
check("bash 0<<A 3<<B: a 3-as leiro adat", not blocks("bash 0<<A 3<<B\necho a\nA" + PAY + "B"))
check("cat 3<<B | bash: a 3-as leiro nem a cso", not blocks("cat 3<<B | bash" + PAY + "B"))
check("y=$(cat <<EOF)", not blocks("y=$(cat <<EOF" + PAY + "EOF\n)"))
check('echo "$(cat <<EOF)"', not blocks('echo "$(cat <<EOF' + PAY + 'EOF\n)"'))
check('bash "$(cat <<EOF)": a kimenet egy szkript NEVE', not blocks('bash "$(cat <<EOF' + PAY + 'EOF\n)"'))
check("diff <(cat <<A) <(cat <<B)", not blocks("diff <(cat <<A" + PAY + "A\n) <(cat <<B" + PAY + "B\n)"))
check("( cat <<EOF ): alhej, a cat-e", not blocks("( cat > /tmp/f <<EOF" + PAY + "EOF\n)"))
check('while read l; do echo "$l"; done <<EOF', not blocks('while read l; do echo "$l"; done <<EOF' + PAY + "EOF"))
check("f(){ cat > /tmp/f; }; f <<EOF", not blocks("f(){ cat > /tmp/f; }; f <<EOF" + PAY + "EOF"))
check("bash -c 'echo hi' <<EOF", not blocks("bash -c 'echo hi' <<EOF" + PAY + "EOF"))
check("bash -ec 'echo hi' <<EOF", not blocks("bash -ec 'echo hi' <<EOF" + PAY + "EOF"))
check("python3 -m json.tool <<EOF", not blocks("python3 -m json.tool <<EOF" + PAY + "EOF"))
check("env cat > /tmp/f <<EOF", not blocks("env cat > /tmp/f <<EOF" + PAY + "EOF"))
check("timeout 30 tee /tmp/f <<EOF", not blocks("timeout 30 tee /tmp/f <<EOF" + PAY + "EOF"))
# review 2. kor, 5. pont: az ertelmezo neve ARGUMENTUMKENT nem program (az 1. kor ezt blokkolta)
check("grep -c python <<'EOF': a python egy minta", not blocks("grep -c python <<'EOF'" + PAY + "EOF"))
check('git commit -m "node fix" -F - <<\'EOF\'', not blocks('git commit -m "node fix" -F - <<\'EOF\'' + PAY + "EOF"))
check("cat 3<<EOF <&3", not blocks("cat 3<<EOF <&3" + PAY + "EOF"))
check("cat /dev/fd/3 3<<SH", not blocks("cat /dev/fd/3 3<<SH" + PAY + "SH"))
check("(cat) <<EOF", not blocks("(cat) <<EOF" + PAY + "EOF"))
check('echo <<<"$(cat <<EOF)"', not blocks('echo <<<"$(cat <<EOF' + PAY + 'EOF\n)"'))
check("tee >(cat >/dev/null) <<EOF >/dev/null", not blocks("tee >(cat >/dev/null) <<EOF >/dev/null" + PAY + "EOF"))
check("docker exec -i c bash -c 'echo hi' <<EOF", not blocks("docker exec -i c bash -c 'echo hi' <<EOF" + PAY + "EOF"))
check("awk '{print}' <<EOF", not blocks("awk '{print}' <<EOF" + PAY + "EOF"))
check("sort -S 1M <<EOF: a -S nem az env-e", not blocks("sort -S 1M <<EOF" + PAY + "EOF"))
check("env -u HOME cat <<EOF", not blocks("env -u HOME cat <<EOF" + PAY + "EOF"))
check("env -i <<EOF: parancs nelkul csak kiir", not blocks("env -i <<EOF" + PAY + "EOF"))

# --- 10. HERE-STRING PROGRAM es LITERAL `bash -c` PROGRAM (#777) ----------------------
# A bazison (f2fba696) minden MUST-BLOCK sor atment, es a bash lefuttatta a torzset
# (orakulum: agents/cody/work/777/oracle-out.txt, a sorok a heredoc-program-cases.json-ban
# 777_ nevvel). A negativ kontroll: ugyanaz a szoveg adatkent (grep, wc, jq, egy kartya-
# komment), es a bash-sal mert "nem fut" alakok (ervenytelen -c szo, eval <<<).
print()
print("#777 here-string es -c program, MUST-BLOCK:")
RMH = RM + " -rf ~/marveen"
check("bash <<< 'RM'", blocks("bash <<< '" + RMH + "'"))
check("bash <<< 'RM' #comment: a megjegyzes nem szkriptfajl", blocks("bash <<< '" + RMH + "' #comment"))
check("bash <<EOF #c: a megjegyzes a heredoc fejen", blocks("bash <<EOF # note\n" + RMH + "\nEOF"))
check("bash 3<<< 'RM' <&3", blocks("bash 3<<< '" + RMH + "' <&3"))
check("bash <<< 'r''m ...': osszeragasztott idezet", blocks("bash <<< 'r''m -rf ~/marveen'"))
check("bash -- /dev/stdin <<< 'RM'", blocks("bash -- /dev/stdin <<< '" + RMH + "'"))
check("sh -c 'cat | sh' <<< 'RM'", blocks("sh -c 'cat | sh' <<< '" + RMH + "'"))
check("bash -c 'bash' <<EOF", blocks("bash -c 'bash' <<EOF\n" + RMH + "\nEOF"))
check("bash -c 'bash -c \"source /dev/stdin\"' <<< 'RM'", blocks("bash -c 'bash -c \"source /dev/stdin\"' <<< '" + RMH + "'"))
check("bash -c 'bash -c \"$(cat)\"' <<< 'RM'", blocks("bash -c 'bash -c \"$(cat)\"' <<< '" + RMH + "'"))
check("bash -xc 'RM'", blocks("bash -xc '" + RMH + "'"))
check("bash -o pipefail -c 'RM'", blocks("bash -o pipefail -c '" + RMH + "'"))
check("bash -c -- 'RM'", blocks("bash -c -- '" + RMH + "'"))
check("nice bash -c 'RM'", blocks("nice bash -c '" + RMH + "'"))
check("bash -c $'r\\x6d -rf ~/marveen': ANSI-C szo", blocks("bash -c $'r\\x6d -rf ~/marveen'"))
check("bash -c rm\\ -rf\\ ~/marveen: visszaperjeles szo", blocks("bash -c r" + "m\\ -rf\\ ~/marveen"))
check("bash -c 'ec'\"ho hi; RM\": osszeragasztott program", blocks("bash -c 'ec'\"ho hi; " + RMH + "\""))
check('echo "$(bash -c \'RM\')"', blocks('echo "$(bash -c \'' + RMH + '\')"'))
check("bash -c 'bash -c \"RM\"': ketszintu", blocks("bash -c 'bash -c \"" + RMH + "\"'"))
print("#777 negativ kontroll (adat, nem program):")
check("grep -c rm <<< 'RM' adat", not blocks("grep -c rm <<< '" + RMH + "'"))
check("wc -l <<< 'RM' adat", not blocks("wc -l <<< '" + RMH + "'"))
check("jq . <<< json adat", not blocks("jq . <<< '{\"cmd\":\"" + RMH + "\"}'"))
check("cat <<< 'RM' > notes.txt adat", not blocks("cat <<< '" + RMH + "' > notes.txt"))
check("tabla.py komment <<< 'bash -c RM' adat", not blocks("python3 scripts/tabla.py komment 777 <<< 'bash -c " + RMH + "'"))
check("eval <<< 'RM': az eval nem olvas stdin-t (merve)", not blocks("eval <<< '" + RMH + "'"))
check("bash -c'RM': ervenytelen kapcsolo, nem fut (merve)", not blocks("bash -c'" + RMH + "'"))
check("bash x.sh <<< 'RM': a szkript a fajl", not blocks("bash x.sh <<< '" + RMH + "'"))

# --- 13. Egy magaban allo `&` parancsot zar (regi hezag, nem #777) ----------------------
# `sleep 1 & RM` a bazison (f2fba696) ATMENT: a `&` nem volt szegmenshatar, a `rm` a
# `sleep` argumentumanak latszott. bash lefuttatja (a hatterbe tett elso utan a masodik fut).
print()
print("magaban allo `&` mint szegmenshatar, MUST-BLOCK:")
check("sleep 1 & RM", blocks("sleep 1 & " + RMH))
check("sleep 1 &RM (szokoz nelkul)", blocks("sleep 1 &" + RMH))
check("x & y & RM", blocks("x & y & " + RMH))
check("true & bash -c 'RM'", blocks("true & bash -c '" + RMH + "'"))
check("python3 x.py 2>&1 & RM", blocks("python3 x.py 2>&1 & " + RMH))
print("negativ kontroll (atiranyitas es idezett `&`, nem hatar):")
check("echo a 2>&1 | cat", not blocks("echo a 2>&1 | cat"))
check("echo a &> /dev/null", not blocks("echo a &> /dev/null"))
check("echo a &>> log", not blocks("echo a &>> log"))
check("echo a >&2", not blocks("echo a >&2"))
check("cat <&3", not blocks("cat <&3"))
check("echo 'a & b'", not blocks("echo 'a & " + RM + "'"))
check('echo "a & b"', not blocks('echo "a & ' + RM + '"'))
check("sed 's/&/and/' f", not blocks("sed 's/&/and/' f"))
check("setsid nohup x >> log 2>&1 < /dev/null & disown", not blocks("setsid nohup x >> log 2>&1 < /dev/null & disown"))

# --- 14. #777 review 3: itt-string vegrehajtott heredoc-torzsben, backtickben, `<&0` ------
# A review harom alakja: (1) a vegrehajtott heredoc-torzsben allo `<<<` szot a kulso find_all
# nem latta; (2) backtickben a zaro backtick nem zarta a szot, a rekord elveszett, es a
# certain=False-t senki nem olvasta; (3) a JS iker a `<&0`-nal szegmenst vagott (itt a
# python oldal kontrollja). A lezaratlan szo nyers sor-maradekkent megy tovabb (tultilt).
print()
print("#777 review 3, MUST-BLOCK:")
check("bash <<'A' / bash <<< 'RM' / A", blocks("bash <<'A'\nbash <<< '" + RMH + "'\nA"))
check("bash <<A / sh <<< 'RM' / A (idezetlen tag)", blocks("bash <<A\nsh <<< '" + RMH + "'\nA"))
check("bash <<'A' / bash <<'B' / bash <<< 'RM' / B / A (ket szint)", blocks("bash <<'A'\nbash <<'B'\nbash <<< '" + RMH + "'\nB\nA"))
check("echo `bash <<< 'RM'`", blocks("echo `bash <<< '" + RMH + "'`"))
check("x=`bash <<< 'RM'`", blocks("x=`bash <<< '" + RMH + "'`"))
check("echo \"`bash <<< 'RM'`\"", blocks("echo \"`bash <<< '" + RMH + "'`\""))
check("bash <&0 -c 'RM'", blocks("bash <&0 -c '" + RMH + "'"))
check("bash <<< 'RM (lezaratlan szo, tultilt)", blocks("bash <<< '" + RMH))
print("negativ kontroll (adat, nem program):")
check("cat <<'A' / bash <<< 'RM' / A: a cat nem futtat", not blocks("cat <<'A'\nbash <<< '" + RMH + "'\nA"))
check("echo `grep rm <<< 'RM'`: a grep nem futtat", not blocks("echo `grep rm <<< '" + RMH + "'`"))
check("python3 - <<PY / x = \"bash <<< 'RM'\" / PY: idezojelben", not blocks("python3 - <<'PY'\nx = \"bash <<< '" + RMH + "'\"\nPY"))
check("bash <<'A' / wc -l <<< 'RM' / A: a wc nem futtat", not blocks("bash <<'A'\nwc -l <<< '" + RMH + "'\nA"))

# --- 15. #777 review 4: nem shell-nevu futtato torzse, beagyazott escape-elt backtick --------
# (1) a _shell_opener csak shell-NEVET nezett a nyito soron, pedig a _heredoc_runs_body mar
# tudta, hogy a torzs fut: eval "$(cat <<A)", source /dev/stdin <<A, $SHELL <<A, a zaro soron
# allo cso, sorfolytatas, while read ... done <<A, fuggveny, { bash; } <<A. Most csak a
# kod-ertelmezo (python/node/...) torzse marad ki (_code_opener). (2) backtickben a
# visszaper-backtick BEAGYAZOTT helyettesites: a zaro a szo vegere ragadt, es a fej $SUB-ba
# olvadt. Mind bash-sal merve (homokozo, probe-bash.sh): a szo lefut.
print()
print("#777 review 4, MUST-BLOCK:")
check("source /dev/stdin <<'A' / bash <<< 'RM' / A", blocks("source /dev/stdin <<'A'\nbash <<< '" + RMH + "'\nA"))
check(". /dev/stdin <<'A' / bash <<< 'RM' / A", blocks(". /dev/stdin <<'A'\nbash <<< '" + RMH + "'\nA"))
check("eval \"$(cat <<'A' / bash <<< 'RM' / A / )\"", blocks("eval \"$(cat <<'A'\nbash <<< '" + RMH + "'\nA\n)\""))
check("echo \"$(cat <<'A' ... A )\" | bash (zaro soron cso)", blocks("echo \"$(cat <<'A'\nbash <<< '" + RMH + "'\nA\n)\" | bash"))
check("$SHELL <<'A' / bash <<< 'RM' / A", blocks("$SHELL <<'A'\nbash <<< '" + RMH + "'\nA"))
check("bash \\ (ujsor) <<'A' / bash <<< 'RM' / A (sorfolytatas)", blocks("bash \\\n<<'A'\nbash <<< '" + RMH + "'\nA"))
check("while read l; do eval \"$l\"; done <<'A' / bash <<< 'RM' / A", blocks("while read l; do eval \"$l\"; done <<'A'\nbash <<< '" + RMH + "'\nA"))
check("source <(cat <<'A' / bash <<< 'RM' / A / )", blocks("source <(cat <<'A'\nbash <<< '" + RMH + "'\nA\n)"))
check("cat <<'A' | source /dev/stdin / bash <<< 'RM' / A", blocks("cat <<'A' | source /dev/stdin\nbash <<< '" + RMH + "'\nA"))
check("cat <<'A' | eval \"$(cat)\" / bash <<< 'RM' / A", blocks("cat <<'A' | eval \"$(cat)\"\nbash <<< '" + RMH + "'\nA"))
check("bash <<'A' / eval \"$(cat <<'B' / bash <<< 'RM' / B / )\" / A (ket szint)", blocks("bash <<'A'\neval \"$(cat <<'B'\nbash <<< '" + RMH + "'\nB\n)\"\nA"))
check("f(){ bash; }; f <<'A' / bash <<< 'RM' / A", blocks("f(){ bash; }; f <<'A'\nbash <<< '" + RMH + "'\nA"))
check("{ bash; } <<'A' / bash <<< 'RM' / A", blocks("{ bash; } <<'A'\nbash <<< '" + RMH + "'\nA"))
check("echo `echo \\`bash <<< 'RM'\\`` (beagyazott escape-elt backtick)", blocks("echo `echo \\`bash <<< '" + RMH + "'\\``"))
check("echo `echo \"\\`bash <<< 'RM'\\`\"` (idezojelben)", blocks("echo `echo \"\\`bash <<< '" + RMH + "'\\`\"`"))
check("echo `echo \\`bash <<< 'RM'\\` tail`", blocks("echo `echo \\`bash <<< '" + RMH + "'\\` tail`"))
print("negativ kontroll (adat, nem program):")
check("git commit -m \"$(cat <<'A' / bash <<< 'RM' / A / )\": a git nem futtat", not blocks("git commit -m \"$(cat <<'A'\nbash <<< '" + RMH + "'\nA\n)\""))
check("echo 'x <<< y' && cat <<'A' / bash <<< 'RM' / A: a cat nem futtat", not blocks("echo 'x <<< y' && cat <<'A'\nbash <<< '" + RMH + "'\nA"))
check("echo `echo hi` 'bash <<< RM': idezett szoveg", not blocks("echo `echo hi` 'bash <<< " + RMH + "'"))
check("echo `bash <<< \\`echo x\\``: dinamikus szo, nincs RM", not blocks("echo `bash <<< \\`echo x\\``"))


# --- 16. #777 review 5: a fogyaszto SZAVA dont, nem a nyito soron talalt nev -------------------
# A _code_opener az ertelmezo-nevet (python/node/ruby/perl/php) BARHOL a nyito soron (es a
# folytatosoron) kereste, es ha talalt, nem ment le a torzsbe itt-stringert: `source /dev/stdin
# ruby <<A` fogyasztoja a `source`, a ruby csak egy operandus, a torzs fut. Most a
# _heredoc_runs_body a FOGYASZTO SZOT adja vissza (mint a JS heredocRunsBody), es a
# _code_consumer azon dont: INTERPRETERS-ben van es nem shell. Marveen merte (artalmatlan
# helyettesitovel, eldobhato HOME-mal): mind a 8 alak futtatja a szot.
print()
print("#777 review 5, MUST-BLOCK (ertelmezo-nev a soron, de a fogyaszto shell/eval/source):")
check("source /dev/stdin ruby <<'A' / bash <<< 'RM' / A", blocks("source /dev/stdin ruby <<'A'\nbash <<< '" + RMH + "'\nA"))
check(". /dev/stdin perl <<'A' / bash <<< 'RM' / A", blocks(". /dev/stdin perl <<'A'\nbash <<< '" + RMH + "'\nA"))
check("eval \"$(cat <<'A' # node / bash <<< 'RM' / A / )\"", blocks("eval \"$(cat <<'A' # node\nbash <<< '" + RMH + "'\nA\n)\""))
check("$SHELL <<'A'; : python3 / bash <<< 'RM' / A", blocks("$SHELL <<'A'; : python3\nbash <<< '" + RMH + "'\nA"))
check("f(){ eval \"$(cat)\"; }; f php <<'A' / bash <<< 'RM' / A", blocks("f(){ eval \"$(cat)\"; }; f php <<'A'\nbash <<< '" + RMH + "'\nA"))
check("while read l; do eval \"$l\"; done <<'A' # node / bash <<< 'RM' / A", blocks("while read l; do eval \"$l\"; done <<'A' # node\nbash <<< '" + RMH + "'\nA"))
check("cat <<'A' | source /dev/stdin node / bash <<< 'RM' / A", blocks("cat <<'A' | source /dev/stdin node\nbash <<< '" + RMH + "'\nA"))
check("node -v; \\ (ujsor) source /dev/stdin <<'A' / bash <<< 'RM' / A", blocks("node -v; \\\nsource /dev/stdin <<'A'\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (kod-ertelmezo fogyaszto: a torzs nem bash-szoveg, a szo idezojelben adat):")
check("ruby - <<'A' / puts \"bash <<< 'RM'\" / A", not blocks("ruby - <<'A'\nputs \"bash <<< '" + RMH + "'\"\nA"))
check("cat <<'A' | node - / console.log(\"bash <<< 'RM'\") / A", not blocks("cat <<'A' | node -\nconsole.log(\"bash <<< '" + RMH + "'\")\nA"))
check("f(){ python3 -; }; f <<'A' / x = \"bash <<< 'RM'\" / A: a fuggveny a fogyaszto, lemegyunk, de a szo idezett",
      not blocks("f(){ python3 -; }; f <<'A'\nx = \"bash <<< '" + RMH + "'\"\nA"))
print("a fogyaszto szo (a _heredoc_runs_body visszaadja, a JS iker alakja):")


def _consumer(cmd):
    recs, _certain = gate._heredoc_scan().find_all(cmd)
    return [os.path.basename(gate._heredoc_runs_body(cmd, h) or '') for h in recs]


check("source /dev/stdin ruby <<'A' -> source", _consumer("source /dev/stdin ruby <<'A'\nx\nA") == ["source"])
check("python3 - <<'PY' -> python3", _consumer("python3 - <<'PY'\nx\nPY") == ["python3"])
check("cat <<'A' > f -> '' (adat)", _consumer("cat <<'A' > f\nx\nA") == [""])
check("f(){ eval \"$(cat)\"; }; f php <<'A' -> eval (review 6: a fuggveny TORZSENEK fogyasztoja)",
      _consumer("f(){ eval \"$(cat)\"; }; f php <<'A'\nx\nA") == ["eval"])
check("while read l; do eval \"$l\"; done <<'A' -> done", _consumer("while read l; do eval \"$l\"; done <<'A'\nx\nA") == ["done"])
check("cat <<'A' | node - -> node", _consumer("cat <<'A' | node -\nx\nA") == ["node"])
check("bash <<< 'x' -> bash", _consumer("bash <<< 'x'") == ["bash"])

# --- 17. #777 review 6: a fuggveny a nev elott, a torzs fogyasztoja a szo; opcio-ertek nem parancs ---
# (1) `python3(){ bash; }; python3 <<A`: a fuggveny arnyekolja a PATH-ot, a torzset a bash olvassa.
# A _cmd_runs_stdin a funcs ELOTT futott, 'python3'-at adott, a _code_consumer kodnak vette, a
# torzs itt-stringje kimaradt. Most a fuggveny jon elobb, es a TORZSENEK a fogyasztoja a szo (bash),
# mert a nev (python3, f) nem mondja meg, kod vagy shell olvassa-e. Kivetel: command/builtin/exec/
# env/nohup elotag megkeruli a fuggvenyt (bash-sal merve, probe-sandbox: `python3(){ echo FN; };
# command python3 <<<"print(7)"` 7-et ir; `time f` a fuggvenyt futtatja).
# (2) `xargs -a python3 sh <<A`: az -a erteke volt az elso ertelmezo-nevu szo. Most a kapcsolok
# ertekei kimaradnak, es a shell-szo nyer az ertelmezo-szo elott (ssh/docker host-nev is; az
# unshare --map-user es a systemd-run --uid erteke a burkolo-tablaban).
print()
print("#777 review 6, MUST-BLOCK (fuggveny ertelmezo-nevvel, opcio-ertek ertelmezo-nevvel):")
for label, cmd in [
    ("python3(){ bash; }; python3 <<'A'", "python3(){ bash; }; python3 <<'A'"),
    ("node(){ eval \"$(cat)\"; }; node <<'A'", "node(){ eval \"$(cat)\"; }; node <<'A'"),
    ("python3(){ sh; } / python3 <<'A' (kulon soron)", "python3(){ sh; }\npython3 <<'A'"),
    ("python3(){ bash; }; time python3 <<'A' (a time a fuggvenyt futtatja)", "python3(){ bash; }; time python3 <<'A'"),
    ("xargs -a python3 sh <<'A'", "xargs -a python3 sh <<'A'"),
    ("xargs -a /dev/null -E python3 sh <<'A'", "xargs -a /dev/null -E python3 sh <<'A'"),
    ("parallel -a python3 sh <<'A'", "parallel -a python3 sh <<'A'"),
    ("docker exec -i python3 bash <<'A'", "docker exec -i python3 bash <<'A'"),
    ("ssh python3 bash <<'A'", "ssh python3 bash <<'A'"),
    ("systemd-run --uid python3 bash <<'A'", "systemd-run --uid python3 bash <<'A'"),
    ("unshare --map-user python3 bash <<'A'", "unshare --map-user python3 bash <<'A'"),
    ("xargs -I{} sh -c {} <<'A' (valtozatlan)", "xargs -I{} sh -c {} <<'A'"),
    ("f(){ bash; }; f <<'A' (valtozatlan)", "f(){ bash; }; f <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (a torzs kod, a szo idezojelben adat):")
for label, cmd, body in [
    ("python3(){ /usr/bin/python3 \"$@\"; }; python3 - <<'PY' (a torzs fogyasztoja python)",
     "python3(){ /usr/bin/python3 \"$@\"; }; python3 - <<'PY'", "x = \"bash <<< 'RM'\""),
    ("python3(){ bash; }; command python3 - <<'PY' (a command megkeruli a fuggvenyt)",
     "python3(){ bash; }; command python3 - <<'PY'", "x = \"bash <<< 'RM'\""),
    ("ssh host python3 - <<'PY'", "ssh host python3 - <<'PY'", "print(\"bash <<< 'RM'\")"),
    ("docker exec -i c python3 - <<'PY'", "docker exec -i c python3 - <<'PY'", "print(\"bash <<< 'RM'\")"),
    ("xargs python3 <<'PY'", "xargs python3 <<'PY'", "print(\"bash <<< 'RM'\")"),
    ("xargs -n 1 python3 <<'PY' (a -n erteke kimarad, a python a program)", "xargs -n 1 python3 <<'PY'",
     "print(\"bash <<< 'RM'\")"),
]:
    check(label, not blocks(cmd + "\n" + body.replace('RM', RMH) + "\nPY"))
print("a fogyaszto szo (a fuggvenye a torzse fogyasztoja):")
check("python3(){ bash; }; python3 <<'A' -> bash", _consumer("python3(){ bash; }; python3 <<'A'\nx\nA") == ["bash"])
check("python3(){ bash; }; command python3 <<'A' -> python3", _consumer("python3(){ bash; }; command python3 <<'A'\nx\nA") == ["python3"])
check("f(){ python3 -; }; f <<'A' -> python3", _consumer("f(){ python3 -; }; f <<'A'\nx\nA") == ["python3"])
check("xargs -a python3 sh <<'A' -> sh", _consumer("xargs -a python3 sh <<'A'\nx\nA") == ["sh"])
check("ssh python3 bash <<'A' -> bash", _consumer("ssh python3 bash <<'A'\nx\nA") == ["bash"])
check("ssh host python3 - <<'A' -> python3", _consumer("ssh host python3 - <<'A'\nx\nA") == ["python3"])


# --- 18. #777 review 7: a torzs MINDEN parancsa; a futtato programja a kapcsolok es a host utan ---
# (1) `g(){ bash; }; f(){ g; }; f <<A`: a torzs fogyasztoja az ELSO fogyaszto-szo volt. Most a
# torzs minden egyszeru parancsat nezzuk: a fuggveny-szo a sajat torzsevel oldodik fel rekurzivan,
# a sajat stdin-u (`<f`, `<<<x`, `<<T`) es a hatterbe tett (`&`) parancs kimarad (bash-sal merve,
# probe-sandbox: `f(){ cat & wait; }; f <<<B` semmit nem ir, `f(){ cat </dev/null; cat; }; f <<<B`
# B-t ir), es ha BARMELYIK fogyaszto shell, az a szo. A `</dev/stdin` es a `<<< "$(cat)"` NEM sajat
# stdin: az orokolt maga, illetve a tartalma.
# (2) `ssh -l python3 host bash <<A`: az "elso shell-szo nyer" az ertelmezo ARGUMENTUMAT is
# programnak vette (`ssh host python3 - bash`). Most a futtato kapcsolo-ertekei es a host kimaradnak,
# es a kovetkezo szo a program; `ssh host <<A` parancs nelkul a tavoli login shell olvassa a torzset.
print()
print("#777 review 7, MUST-BLOCK (a torzs kesobbi parancsa shell; a futtato programja a host utan):")
for label, cmd in [
    ("g(){ bash; }; f(){ g; }; f <<'A' (fuggveny a fuggvenyben)", "g(){ bash; }; f(){ g; }; f <<'A'"),
    ("f(){ cat </dev/null; bash; }; f <<'A' (sajat stdin, aztan shell)", "f(){ cat </dev/null; bash; }; f <<'A'"),
    ("f(){ sleep 1 & bash; }; f <<'A' (hatterben, aztan shell)", "f(){ sleep 1 & bash; }; f <<'A'"),
    ("f(){ python3 - <<<''; bash; }; f <<'A' (sajat itt-string, aztan shell)", "f(){ python3 - <<<''; bash; }; f <<'A'"),
    ("f(){ python3 -; bash; }; f <<'A' (a shell nyer az ertelmezo utan is)", "f(){ python3 -; bash; }; f <<'A'"),
    ("f(){ bash </dev/stdin; }; f <<'A' (az orokolt stdin utvonalon)", "f(){ bash </dev/stdin; }; f <<'A'"),
    ("ssh -l python3 host bash <<'A'", "ssh -l python3 host bash <<'A'"),
    ("ssh host <<'A' (parancs nelkul: a login shell)", "ssh host <<'A'"),
    ("ssh -p 22 host <<'A'", "ssh -p 22 host <<'A'"),
    ("kubectl exec -i python3 -- bash <<'A'", "kubectl exec -i python3 -- bash <<'A'"),
    ("sshpass -p python3 ssh host bash <<'A'", "sshpass -p python3 ssh host bash <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (a program az ertelmezo, a shell-nevu szo az argumentuma; nyers torzs):")
for label, cmd in [
    ("ssh host python3 - bash <<'A'", "ssh host python3 - bash <<'A'"),
    ("docker exec -i c python3 - bash <<'A'", "docker exec -i c python3 - bash <<'A'"),
    ("docker exec -i c python3 script.py bash <<'A'", "docker exec -i c python3 script.py bash <<'A'"),
    ("xargs python3 tool.py sh <<'A'", "xargs python3 tool.py sh <<'A'"),
    ("kubectl exec -i pod -- python3 - <<'A'", "kubectl exec -i pod -- python3 - <<'A'"),
    ("f(){ python3 - <<<''; }; f <<'A' (a torzs semmit nem olvas az orokoltbol)", "f(){ python3 - <<<''; }; f <<'A'"),
]:
    check(label, not blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("a fogyaszto szo (a torzs barmelyik shellje; a futtato programja):")
check("g(){ bash; }; f(){ g; }; f <<'A' -> bash", _consumer("g(){ bash; }; f(){ g; }; f <<'A'\nx\nA") == ["bash"])
check("f(){ cat </dev/null; bash; }; f <<'A' -> bash", _consumer("f(){ cat </dev/null; bash; }; f <<'A'\nx\nA") == ["bash"])
check("f(){ python3 -; bash; }; f <<'A' -> bash", _consumer("f(){ python3 -; bash; }; f <<'A'\nx\nA") == ["bash"])
check("f(){ bash </dev/stdin; }; f <<'A' -> bash", _consumer("f(){ bash </dev/stdin; }; f <<'A'\nx\nA") == ["bash"])
check("f(){ python3 - <<<''; }; f <<'A' -> [python3 (az itt-string), '' (a heredoc adat)]",
      _consumer("f(){ python3 - <<<''; }; f <<'A'\nx\nA") == ["python3", ""])
check("ssh -l python3 host bash <<'A' -> bash", _consumer("ssh -l python3 host bash <<'A'\nx\nA") == ["bash"])
check("ssh host <<'A' -> ssh", _consumer("ssh host <<'A'\nx\nA") == ["ssh"])
check("ssh host python3 - bash <<'A' -> python3", _consumer("ssh host python3 - bash <<'A'\nx\nA") == ["python3"])
check("kubectl exec -i python3 -- bash <<'A' -> bash", _consumer("kubectl exec -i python3 -- bash <<'A'\nx\nA") == ["bash"])
check("xargs python3 tool.py sh <<'A' -> python3", _consumer("xargs python3 tool.py sh <<'A'\nx\nA") == ["python3"])
check("sshpass -p python3 ssh host bash <<'A' -> bash", _consumer("sshpass -p python3 ssh host bash <<'A'\nx\nA") == ["bash"])


# --- 19. #777 review 8: EGY futtato-feloldo es a TENYLEGES stdin ------------------------------
# (1) Burkolo, xargs/parallel, ssh/docker/kubectl futtato, su/runuser/script: egyetlen feloldo
# adja a program szavat, a kapcsolo-ertekekkel egyutt (opcio-csoport, aminek az UTOLSO betuje
# erteket var, a kovetkezo szot is elnyeli: `-tl`). Ismeretlen program futtato mogott: barmelyik
# kesobbi shell-szo a fogyaszto; parancs nelkuli parallel/xargs-argv-mod is fogyaszto.
# (2) A sajat stdin a TENYLEGES: az atiranyitasok sorrendben, az UTOLSO nyer; csak literal cel
# (nincs $, backtick, `<(`, nem /dev/stdin-szeru), `<&-` sajat, `<&n` nem, `<<` csak idezett
# hatarolóval, `&` csak atiranyitas es `set -m` nelkul; a `2<` nem a stdin.
# (3) A `#`-tel kezdodo szo komment: utana nincs parancs-szo.
print()
print("#777 review 8, MUST-BLOCK (futtato-feloldo, tenyleges stdin):")
for label, cmd in [
    ("taskset 1 bash <<'A'", "taskset 1 bash <<'A'"),
    ("chrt -f 1 bash <<'A'", "chrt -f 1 bash <<'A'"),
    ("runuser -u x -- bash <<'A'", "runuser -u x -- bash <<'A'"),
    ("su <<'A' (a login shell olvas)", "su <<'A'"),
    ("su -c 'bash' <<'A'", "su -c 'bash' <<'A'"),
    ("script -q /dev/null <<'A'", "script -q /dev/null <<'A'"),
    ("busybox sh <<'A'", "busybox sh <<'A'"),
    ("xargs -0 nice <<'A' (argv-mod, a burkolo parancs nelkul)", "xargs -0 nice <<'A'"),
    ("parallel <<'A' (parancs nelkul a sorok parancsok)", "parallel <<'A'"),
    ("ssh host \"bash -s\" <<'A' (a tavoli szavak ujraolvasva)", "ssh host \"bash -s\" <<'A'"),
    ("xargs ssh host <<'A'", "xargs ssh host <<'A'"),
    ("xargs su -c <<'A'", "xargs su -c <<'A'"),
    ("xargs script -qc <<'A'", "xargs script -qc <<'A'"),
    ("f(){ bash 2</dev/null; }; f <<'A' (a 2< nem a stdin)", "f(){ bash 2</dev/null; }; f <<'A'"),
    ("f(){ bash </dev/null <&0; }; f <<'A' (az utolso atiranyitas nyer)", "f(){ bash </dev/null <&0; }; f <<'A'"),
    ("f(){ set -m; bash & wait; }; f <<'A' (job control: a hatter orokol)", "f(){ set -m; bash & wait; }; f <<'A'"),
    ("f(){ bash < \"$F\"; }; f <<'A' (nem literal cel)", "f(){ bash < \"$F\"; }; f <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (sajat stdin, komment, adat-olvaso a futtato mogott):")
for label, cmd in [
    ("f(){ bash </dev/null; }; f <<'A'", "f(){ bash </dev/null; }; f <<'A'"),
    ("f(){ bash 0</dev/null; }; f <<'A'", "f(){ bash 0</dev/null; }; f <<'A'"),
    ("f(){ bash <<< 'echo hi'; }; f <<'A'", "f(){ bash <<< 'echo hi'; }; f <<'A'"),
    ("f(){ bash & wait; }; f <<'A'", "f(){ bash & wait; }; f <<'A'"),
    ("f(){ bash <&-; }; f <<'A'", "f(){ bash <&-; }; f <<'A'"),
    ("f(){ cat #bash; }; f <<'A' (komment)", "f(){ cat #bash\n}; f <<'A'"),
    ("xargs grep -l pattern <<'A'", "xargs grep -l pattern <<'A'"),
    ("docker run -i --rm alpine cat <<'A'", "docker run -i --rm alpine cat <<'A'"),
    ("chrt 1 cat <<'A'", "chrt 1 cat <<'A'"),
    ("su -c 'echo hi' <<'A'", "su -c 'echo hi' <<'A'"),
    ("ssh -n host true <<'A'", "ssh -n host true <<'A'"),
]:
    check(label, not blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("a fogyaszto szo (review 8):")
check("taskset 1 bash <<'A' -> bash", _consumer("taskset 1 bash <<'A'\nx\nA") == ["bash"])
check("busybox sh <<'A' -> sh", _consumer("busybox sh <<'A'\nx\nA") == ["sh"])
check("ssh host \"bash -s\" <<'A' -> bash", _consumer("ssh host \"bash -s\" <<'A'\nx\nA") == ["bash"])
check("f(){ bash </dev/null; }; f <<'A' -> '' (adat)", _consumer("f(){ bash </dev/null; }; f <<'A'\nx\nA") == [""])
check("chrt 1 cat <<'A' -> '' (adat)", _consumer("chrt 1 cat <<'A'\nx\nA") == [""])

# --- 20. #777 review 9: dinamikus atiranyitas-cel, komment a stdin-szabalyban, futtato-tabla ----------
# (1) Glob/kapcsos cel nem literal (`</dev/std[i]n` a stdin). (2) A `#` utani szo a stdin-szabalyban
# sem atiranyitas. (3) xargs mogott a puszta atlatszo elotag (env, nohup, time) a program.
# (4) xargs mogott az ssh-csalad minden szava a tavoli shell-sorba megy. (5) su --session-command.
# (6) Parancs nelkul shellt indito futtatok. (7) busybox ash/hush. (8) Barhol idezett heredoc-hatarolo.
# (9) Tovabbi futtatok a tablaban.
print()
print("#777 review 9, MUST-BLOCK:")
for label, cmd in [
    ("f(){ bash </dev/std[i]n; }; f <<'A'", "f(){ bash </dev/std[i]n; }; f <<'A'"),
    ("f(){ bash </proc/self/f[d]/0; }; f <<'A'", "f(){ bash </proc/self/f[d]/0; }; f <<'A'"),
    ("while :; do bash </dev/std?n; break; done <<'A'", "while :; do bash </dev/std?n; break; done <<'A'"),
    ("f(){ bash # <x / }; f <<'A'", "f(){ bash # <x\n}; f <<'A'"),
    ("xargs env <<'A'", "xargs env <<'A'"),
    ("xargs -n9 nohup <<'A'", "xargs -n9 nohup <<'A'"),
    ("xargs env -i <<'A'", "xargs env -i <<'A'"),
    ("xargs ssh host echo <<'A'", "xargs ssh host echo <<'A'"),
    ("xargs sshpass -p pw ssh host echo <<'A'", "xargs sshpass -p pw ssh host echo <<'A'"),
    ("xargs -0 su root --session-command <<'A'", "xargs -0 su root --session-command <<'A'"),
    ("su --session-command=bash <<'A'", "su --session-command=bash <<'A'"),
    ("pkexec <<'A'", "pkexec <<'A'"),
    ("firejail <<'A'", "firejail <<'A'"),
    ("newgrp grp <<'A'", "newgrp grp <<'A'"),
    ("busybox ash <<'A'", "busybox ash <<'A'"),
    ("xargs -0 busybox hush -c <<'A'", "xargs -0 busybox hush -c <<'A'"),
    ("fakeroot <<'A'", "fakeroot <<'A'"),
    ("proot -r / bash <<'A'", "proot -r / bash <<'A'"),
    ("gdb -batch -ex r --args bash <<'A'", "gdb -batch -ex r --args bash <<'A'"),
    ("distrobox enter box -- bash <<'A'", "distrobox enter box -- bash <<'A'"),
    ("toolbox run -c box bash <<'A'", "toolbox run -c box bash <<'A'"),
    ("machinectl shell host <<'A'", "machinectl shell host <<'A'"),
    ("prlimit --nofile=10 bash <<'A'", "prlimit --nofile=10 bash <<'A'"),
    ("numactl -C 0 bash <<'A'", "numactl -C 0 bash <<'A'"),
    ("cgexec -g cpu:x bash <<'A'", "cgexec -g cpu:x bash <<'A'"),
    ("sg grp -c bash <<'A'", "sg grp -c bash <<'A'"),
    ("setarch x86_64 <<'A'", "setarch x86_64 <<'A'"),
    ("chpst -u x bash <<'A'", "chpst -u x bash <<'A'"),
    ("faketime now bash <<'A'", "faketime now bash <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (review 9):")
for label, cmd in [
    ("f(){ bash <<\\B / exit / B / }; f <<'A' (visszaperjeles hatarolo: sajat stdin)", "f(){ bash <<\\B\nexit\nB\n}; f <<'A'"),
    ("f(){ bash <<B\"C\" / BC / }; f <<'A' (idezojel a hatarolo kozepen)", "f(){ bash <<B\"C\"\nexit\nBC\n}; f <<'A'"),
    ("f(){ bash </dev/null; }; f <<'A' (literal cel)", "f(){ bash </dev/null; }; f <<'A'"),
    ("f(){ bash <\"a*b\"; }; f <<'A' (idezett csillag: literal)", "f(){ bash <\"a*b\"; }; f <<'A'"),
    ("xargs <<'A' (parancs nelkul: echo)", "xargs <<'A'"),
    ("grep bash <<'A'", "grep bash <<'A'"),
    ("gdb -batch prog <<'A' (--args nelkul nem fut a program)", "gdb -batch prog <<'A'"),
    ("numactl -C 0 cat <<'A'", "numactl -C 0 cat <<'A'"),
    ("toolbox run cat <<'A'", "toolbox run cat <<'A'"),
    ("su -c 'cat' root <<'A'", "su -c 'cat' root <<'A'"),
]:
    check(label, not blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("a fogyaszto szo (review 9):")
check("xargs env <<'A' -> env", _consumer("xargs env <<'A'\nx\nA") == ["env"])
check("busybox ash <<'A' -> ash", _consumer("busybox ash <<'A'\nx\nA") == ["ash"])
check("gdb -batch -ex r --args bash <<'A' -> bash", _consumer("gdb -batch -ex r --args bash <<'A'\nx\nA") == ["bash"])
check("xargs <<'A' -> '' (adat)", _consumer("xargs <<'A'\nx\nA") == [""])


# --- 21. #777 review 10: kapcsos tartomany, a fuggvenytorzs vege, gdb stdin, tulblokk, futtato-tabla ---
# (1) `{i..i}` kapcsos tartomany a celban: nem literal. (2) A `}` csak `;`/`&`/ujsor/`)`/`}` utan zar,
# idezojelben, szo kozepen es kommentben nem. (3) gdb -batch nelkul a stdinrol olvas parancsot:
# MERVE gdb 17.1-gyel a homokozoban (2026-11-03), `shell echo PWNED` LEFUTOTT -q -nx, -ex, --args,
# -n mellett is; -batch/--batch/-batch-silent es --version mellett NEM. (4) Info-kapcsolo es nem
# shellt indito alparancs nem blokkol (machinectl list, distrobox create, sg grp cat). (5) rsh,
# dbclient, autossh tavoli futtato; mksh, yash shell.
print()
print("#777 review 10, MUST-BLOCK:")
for label, cmd in [
    ("f(){ bash </dev/std{i..i}n; }; f <<'A'", "f(){ bash </dev/std{i..i}n; }; f <<'A'"),
    ("while :; do bash </dev/std{i..i}n; break; done <<'A'", "while :; do bash </dev/std{i..i}n; break; done <<'A'"),
    ("f(){ echo a}b; bash; }; f <<'A'", "f(){ echo a}b; bash; }; f <<'A'"),
    ("f(){ echo '}'; bash; }; f <<'A'", "f(){ echo '}'; bash; }; f <<'A'"),
    ("f(){ bash </dev/null; echo }; bash; }; f <<'A'", "f(){ bash </dev/null; echo }; bash; }; f <<'A'"),
    ("f(){ if true; then { :; }; fi; bash; }; f <<'A'", "f(){ if true; then { :; }; fi; bash; }; f <<'A'"),
    ("f(){ # a; } / bash; }; f <<'A'", "f(){ # a; }\nbash; }; f <<'A'"),
    ("gdb -q -nx prog <<'A' (gdb 17.1: lefutott)", "gdb -q -nx prog <<'A'"),
    ("gdb -q -nx -ex r --args prog <<'A' (gdb 17.1: lefutott)", "gdb -q -nx -ex r --args prog <<'A'"),
    ("gdb -batch -ex r --args bash <<'A'", "gdb -batch -ex r --args bash <<'A'"),
    ("machinectl shell box <<'A'", "machinectl shell box <<'A'"),
    ("setarch x86_64 <<'A'", "setarch x86_64 <<'A'"),
    ("distrobox enter box <<'A'", "distrobox enter box <<'A'"),
    ("toolbox enter <<'A'", "toolbox enter <<'A'"),
    ("sg grp bash <<'A'", "sg grp bash <<'A'"),
    ("sg grp -c bash <<'A'", "sg grp -c bash <<'A'"),
    ("sg grp <<'A'", "sg grp <<'A'"),
    ("xargs rsh host echo <<'A'", "xargs rsh host echo <<'A'"),
    ("xargs dbclient host echo <<'A'", "xargs dbclient host echo <<'A'"),
    ("autossh -M 0 host bash <<'A'", "autossh -M 0 host bash <<'A'"),
    ("autossh -M 0 host <<'A'", "autossh -M 0 host <<'A'"),
    ("mksh <<'A'", "mksh <<'A'"),
    ("yash <<'A'", "yash <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("negativ kontroll (review 10):")
for label, cmd in [
    ("gdb -batch prog <<'A' (gdb 17.1: nem futott)", "gdb -batch prog <<'A'"),
    ("gdb --version <<'A' (gdb 17.1: nem futott)", "gdb --version <<'A'"),
    ("machinectl list <<'A'", "machinectl list <<'A'"),
    ("machinectl status box <<'A'", "machinectl status box <<'A'"),
    ("setarch --list <<'A'", "setarch --list <<'A'"),
    ("distrobox create box <<'A'", "distrobox create box <<'A'"),
    ("toolbox list <<'A'", "toolbox list <<'A'"),
    ("sg grp cat <<'A'", "sg grp cat <<'A'"),
    ("f(){ cat; }; f <<'A'", "f(){ cat; }; f <<'A'"),
    ("f(){ echo {a,b}; cat; }; f <<'A' (kapcsos szo, nem csoport)", "f(){ echo {a,b}; cat; }; f <<'A'"),
    ("f(){ echo }; }; cat <<'A' (a `}` argumentum, a cat olvas)", "f(){ echo }; }; cat <<'A'"),
]:
    check(label, not blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
print("a fogyaszto szo (review 10):")
check("gdb -q -nx prog <<'A' -> gdb", _consumer("gdb -q -nx prog <<'A'\nx\nA") == ["gdb"])
check("gdb -batch prog <<'A' -> '' (adat)", _consumer("gdb -batch prog <<'A'\nx\nA") == [""])
check("xargs rsh host echo <<'A' -> rsh", _consumer("xargs rsh host echo <<'A'\nx\nA") == ["rsh"])
check("mksh <<'A' -> mksh", _consumer("mksh <<'A'\nx\nA") == ["mksh"])

# --- 22. #777 review 11: a fuggvenytorzs vege kifejtesben, heredocban; gdb -ex/-x/source ---------------
# (1) A torzs vege a parse-olt es a regi zarojel-szamlalo KOZUL a kesobbi: a parser hibaja tultilt.
# A parser atugorja a ${..}, $(..), $'..', backtick szoveget es a fuggvenyben allo heredoc-torzset
# (`f(){ cat <<'X'\n}\nX\nbash; }`): probe-sandbox bash-sal merve, mind futtatja a `bash`-t.
# (2) gdb -batch mellett az -ex/-iex `shell X`, `!X`, `pipe ..| X`, `| ..| X` erteke shell-sor, ami a
# gdb stdinjet orokli; `python ..` tetszoleges kod. `-x /dev/stdin` es `source /dev/stdin`: a torzs
# gdb-szkript. A stdinrol olvasott gdb-szkript `shell`/`!`/pipe sorai shell-sorok (`shell rm -rf x`).
# gdb nincs telepitve: a gdb-szabalyok a gdb-dokumentaciora epulnek, nem mertuk.
print()
print("#777 review 11, MUST-BLOCK:")
for label, cmd in [
    ("f(){ echo ${x:- ; } ; bash; }; f <<'A'", "f(){ echo ${x:- ; } ; bash; }; f <<'A'"),
    ("f(){ cat <<'X' / } / X / bash; }; f <<'A'", "f(){ cat <<'X'\n}\nX\nbash; }; f <<'A'"),
    ("f(){ cat <<-X / <tab>} / <tab>X / bash; }; f <<'A'", "f(){ cat <<-X\n\t}\n\tX\nbash; }; f <<'A'"),
    ("f(){ echo $'\\''; echo '; } '; bash; }; f <<'A'", "f(){ echo $'\\''; echo '; } '; bash; }; f <<'A'"),
    ("f(){ echo \"${x:-; }}\"; bash; }; f <<'A'", "f(){ echo \"${x:-; }}\"; bash; }; f <<'A'"),
    ("f(){ echo `echo ; }` ; bash; }; f <<'A'", "f(){ echo `echo ; }` ; bash; }; f <<'A'"),
    ("gdb -batch -ex 'shell bash' <<'A'", "gdb -batch -ex 'shell bash' <<'A'"),
    ("gdb -batch -ex '!bash' <<'A'", "gdb -batch -ex '!bash' <<'A'"),
    ("gdb -batch --eval-command='shell bash' <<'A'", "gdb -batch --eval-command='shell bash' <<'A'"),
    ("gdb -batch -iex 'shell sh -s' <<'A'", "gdb -batch -iex 'shell sh -s' <<'A'"),
    ("gdb -batch -ex 'pipe info frame | bash' <<'A'", "gdb -batch -ex 'pipe info frame | bash' <<'A'"),
    ("gdb -batch -ex 'python import os; os.system(\"bash\")' <<'A'",
     "gdb -batch -ex 'python import os; os.system(\"bash\")' <<'A'"),
]:
    check(label + " / bash <<< 'RM' / A", blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
for label, cmd in [
    ("gdb -batch -x /dev/stdin <<'A' / shell RM", "gdb -batch -x /dev/stdin <<'A'\nshell " + RMH + "\nA"),
    ("gdb -batch --command=/dev/stdin <<'A' / !RM", "gdb -batch --command=/dev/stdin <<'A'\n!" + RMH + "\nA"),
    ("gdb -batch -ex 'source /dev/stdin' <<'A' / shell RM",
     "gdb -batch -ex 'source /dev/stdin' <<'A'\nshell " + RMH + "\nA"),
    ("gdb -q -nx prog <<'A' / shell RM", "gdb -q -nx prog <<'A'\nshell " + RMH + "\nA"),
    ("gdb -q -nx prog <<'A' / pipe info frame | RM", "gdb -q -nx prog <<'A'\npipe info frame | " + RMH + "\nA"),
]:
    check(label, blocks(cmd))
print("negativ kontroll (review 11):")
for label, cmd in [
    ("f(){ echo ${x:-a}; cat; }; f <<'A'", "f(){ echo ${x:-a}; cat; }; f <<'A'"),
    ("f(){ cat <<'X' / } / X / }; f <<'A'", "f(){ cat <<'X'\n}\nX\n}; f <<'A'"),
    ("f(){ echo $'a\\'b'; cat; }; f <<'A'", "f(){ echo $'a\\'b'; cat; }; f <<'A'"),
    ("f(){ echo $((1+2)); cat; }; f <<'A'", "f(){ echo $((1+2)); cat; }; f <<'A'"),
    ("gdb -batch -nx <<'A'", "gdb -batch -nx <<'A'"),
    ("gdb -batch -ex 'shell echo hi' <<'A'", "gdb -batch -ex 'shell echo hi' <<'A'"),
    ("gdb -batch -ex 'print 1' prog <<'A'", "gdb -batch -ex 'print 1' prog <<'A'"),
]:
    check(label, not blocks(cmd + "\nbash <<< '" + RMH + "'\nA"))
check("gdb -batch -x cmds.gdb prog <<'A' / shell RM (a szkript fajlbol jon, a torzs adat)",
      not blocks("gdb -batch -x cmds.gdb prog <<'A'\nshell " + RMH + "\nA"))
check("gdb -q -nx prog <<'A' / shell echo hi / bt", not blocks("gdb -q -nx prog <<'A'\nshell echo hi\nbt\nA"))
check("cat <<'A' / shell RM (adat)", not blocks("cat <<'A'\nshell " + RMH + "\nA"))
print("a fogyaszto szo (review 11):")
check("gdb -batch -ex 'shell bash' <<'A' -> bash", _consumer("gdb -batch -ex 'shell bash' <<'A'\nx\nA") == ["bash"])
check("gdb -batch -x /dev/stdin <<'A' -> gdb", _consumer("gdb -batch -x /dev/stdin <<'A'\nx\nA") == ["gdb"])
check("gdb -batch -nx <<'A' -> '' (adat)", _consumer("gdb -batch -nx <<'A'\nx\nA") == [""])

def _missing_module_exit():
    """A heredoc_scan nelkul masolt kapu BLOKKOL (exit 2), nem enged at (exit 1)."""
    tmp = os.path.join(tempfile.mkdtemp(), "lonely-gate.py")
    with open(GATE, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    r = subprocess.run([sys.executable, tmp],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
                       capture_output=True, text=True)
    return r.returncode


check("a heredoc_scan modul hianya is exit 2 (fail-closed)", _missing_module_exit() == 2)

print()
if failed:
    print("%d FAILED: %s" % (len(failed), failed), file=sys.stderr)
    sys.exit(1)
print("Minden destructive-gate szovegkornyezet-teszt zold.")
